"""Parcours du mode Simple vers une cible FORMAT DE FICHIER (voir `formats`) : démarrage par le fichier,
aperçu et téléchargement. Appelé par `MigrationWizardService` ; les services d'export (profils, numéros de
compte, historique par pièce) sont ceux des exports existants, avec une source FICHIER (aucune instance)."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from uie_cdm import DocumentStatus

from uie_core.accounting.service import AccountingReadItem, AccountingReadResult, CompanyRef
from uie_core.accounting.target import AccountingTargetNotReady, ExportInvalid, PeriodSpan
from uie_core.domains.model import DomainKey
from uie_core.exports.records import ExportAccountAssignmentRecord
from uie_core.exports.service import DEFAULT_CLIENT, ExportError, ExportNotReady
from uie_core.identity.context import TenantContext
from uie_core.mapping import IssueLevel, RecordStatus
from uie_core.partners import PartnerReadResult
from uie_core.wizard.cdm_accounting import FILE_REF, category, documents_from_rows
from uie_core.wizard.cdm_partners import partners_from_rows
from uie_core.wizard.formats import (
    MAPPING,
    SETTING,
    FormatTarget,
    blocked_reasons,
    data_type_of_file,
    from_value_question,
    mapping_decisions,
    mapping_questions,
    role_of,
    setting_values,
    settings_questions,
    structure,
)
from uie_core.wizard.mapping import ColumnStatus, MappingDecision, PreparedFile, prepare
from uie_core.wizard.model import (
    ChoiceSource,
    TargetProposal,
    WizardAnswer,
    WizardDataTypeUnknown,
    WizardInputError,
    WizardUnavailable,
)
from uie_core.wizard.quick import (
    MAX_BLOCKING,
    QuickQuestion,
    QuickStart,
    data_type_mismatch,
    infer_data_type,
)
from uie_core.wizard.text import count, say
from uie_core.wizard.values import ExcludedRow, ValueIssue, ValueQuestion, rows_of

if TYPE_CHECKING:
    from uie_core.wizard.service import MigrationWizardService

DATA_TYPE_TOPIC = "data_type"


class FormatFlow:
    def __init__(self, svc: MigrationWizardService, fmt: FormatTarget, client: str = DEFAULT_CLIENT) -> None:
        self.svc = svc
        self.fmt = fmt
        # dossier (mandant) de l'organisation : réglages, numéros de compte et historique propres ; un seul
        # pour l'instant (« default »), la structure en admet plusieurs
        self.client = client

    # ------------------------------------------------------------------ #
    def _org(self) -> Any:
        ctx = self.svc.ctx
        if not isinstance(ctx, TenantContext):
            raise WizardUnavailable("This target needs an organization (supplier portal)")
        return ctx.organization_id

    def _exports(self) -> Any:
        if self.svc.exports is None:
            raise WizardUnavailable("File formats are not available here")
        return self.svc.exports

    def _saved(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """Réglages déjà enregistrés de l'organisation (ce format, format partagé) ; {} hors d'un tenant."""
        if not isinstance(self.svc.ctx, TenantContext) or self.svc.exports is None:
            return {}, {}
        profiles = self.svc.exports.profiles
        b = self.fmt.binding
        own = profiles.get(b.target.system, b.target.product, client_key=self.client)
        shared = None
        if b.inherit_profile:
            shared = profiles.get(
                b.inherit_profile.target.system, b.inherit_profile.target.product, client_key=self.client
            )
        return dict(own.values if own else {}), dict(shared.values if shared else {})

    # ------------------------------------------------------------------ #
    def quick_start(
        self,
        answers: list[WizardAnswer],
        label: str,
        data: bytes,
        file_name: str,
        df: Any,
        target: Any,
        chosen: str | None = None,
    ) -> QuickStart:
        headers = [str(c).strip() for c in df.columns]
        options = list(self.fmt.table.data_types)
        if chosen is not None and chosen not in options:
            raise WizardDataTypeUnknown(f"{self.fmt.label} takes: " + ", ".join(options), options)
        # fichier d'un autre type (clients vers un journal de factures) : dit, jamais forcé
        mismatch = None if chosen else data_type_mismatch(headers, options, self.fmt.label)
        if mismatch:
            raise WizardDataTypeUnknown(mismatch, options)
        # la cible ne reçoit qu'un type de données : choisi d'office ; sinon colonnes du tableau, puis mots
        data_type = (
            chosen
            or (options[0] if len(options) == 1 else None)
            or data_type_of_file(self.fmt, headers)
            or infer_data_type(headers, options)
        )
        if data_type is None:
            raise WizardDataTypeUnknown(
                f"We could not tell what kind of data your file holds: choose what {self.fmt.label} should "
                "receive",
                options,
            )
        answers = [
            *answers,
            WizardAnswer(
                question_id=DATA_TYPE_TOPIC,
                topic=DATA_TYPE_TOPIC,
                question="What kind of data does your file hold?",
                value=data_type.casefold().replace(" ", "_"),
                label=data_type,
                source=ChoiceSource.FILE,
            ),
        ]
        p, mapping = self.svc.map_file(answers, structure(self.fmt, data_type), data, file_name)
        decisions = [
            MappingDecision(position=c.position, target=c.target)
            for c in mapping.columns
            if c.target and c.status in (ColumnStatus.MATCHED, ColumnStatus.AI_MATCH, ColumnStatus.PREVIOUS)
        ]
        saved, saved_shared = self._saved()
        settings = settings_questions(
            self.fmt, saved, saved_shared, self._data_year(p, data, file_name, decisions)
        )
        choices = {q.decision: q.default for q in settings if q.decision and q.default}
        # décisions du moteur de valeurs et du constructeur du CDM (sur les réponses présélectionnées)
        state = self._state(answers, p, data, file_name, decisions, choices)
        decided = [from_value_question(q) for q in state["questions"]]
        choices |= {q.decision: q.default for q in decided if q.decision and q.default}
        mappings = self._mapping_questions(state, choices)
        choices |= {q.decision: q.default for q in mappings if q.decision and q.default}
        every = [*settings, *decided, *mappings]
        asked = [q for q in every if q.default is None]
        applied = [q for q in every if q.default is not None]
        return QuickStart(
            target_label=label,
            target=target,
            data_type=data_type,
            data_type_from="user" if chosen else "target" if len(options) == 1 else "rules",
            structure_from="registry",
            answers=answers,
            proposal=p,
            mapping=mapping,
            decisions=decisions,
            choices=choices,
            questions=asked[:MAX_BLOCKING],
            applied=applied,
            fills=[],
            more_questions=max(0, len(asked) - MAX_BLOCKING),
        )

    def _data_year(
        self, p: TargetProposal, data: bytes, file_name: str, decisions: list[MappingDecision]
    ) -> int:
        """Année de l'exercice présélectionnée : celle de la plupart des dates du fichier, sinon l'année en
        cours."""
        summary, rows, _ = prepare(p, data, file_name, decisions)
        dates = [f.name for f in p.fields if f.type == "date"]
        years = [
            r[summary.headers.index(n)][:4]
            for r in rows
            for n in dates
            if n in summary.headers and r[summary.headers.index(n)][:4].isdigit()
        ]
        if not years:
            return date.today().year
        return int(max(set(years), key=years.count))

    def _mapping_questions(self, state: dict[str, Any], choices: dict[str, str]) -> list[QuickQuestion]:
        if self.fmt.domain != DomainKey.ACCOUNTING:
            return []
        built = state["built"]
        cats, rates = built.categories, built.rates
        chart = choices.get(SETTING + "chart_of_accounts") or self._saved()[1].get("chart_of_accounts")
        return mapping_questions(self.fmt, cats, rates, chart, choices)

    # ------------------------------------------------------------------ #
    def _state(
        self,
        answers: list[WizardAnswer],
        p: TargetProposal,
        data: bytes,
        file_name: str,
        decisions: list[MappingDecision],
        choices: dict[str, str],
    ) -> dict[str, Any]:
        """Moteur de valeurs puis constructeur du CDM (rien n'est lu dans un système, rien n'est écrit)."""
        summary, rows, pairs = prepare(p, data, file_name, decisions, choices=choices)
        columns = {target: header for header, target in pairs}
        out = {e.row - 1 for e in summary.excluded_rows}
        table_rows = [
            {} if i in out else {h: r[j] for j, h in enumerate(summary.headers) if r[j]}
            for i, r in enumerate(rows)
        ]
        data_type = next(
            (a.label for a in answers if a.topic == DATA_TYPE_TOPIC), self.fmt.table.data_types[0]
        )
        own, _ = setting_values(self.fmt, choices)
        if self.fmt.domain == DomainKey.PARTNER:
            built: Any = partners_from_rows(self.fmt.table, table_rows, columns, role_of(data_type), choices)
        else:
            currency = own.get("currency") or summary.currency or "EUR"
            built = documents_from_rows(
                self.fmt.table, table_rows, columns, currency, choices, partner_vat=self._partner_vat()
            )
        return {
            "summary": summary,
            "built": built,
            "columns": columns,
            "questions": [*summary.value_questions, *built.questions],
        }

    def prepare(
        self,
        answers: list[WizardAnswer],
        p: TargetProposal,
        data: bytes,
        file_name: str,
        decisions: list[MappingDecision],
        choices: dict[str, str],
        generate: bool,
    ) -> tuple[PreparedFile, bytes, str]:
        org = self._org()
        exports = self._exports()
        state = self._state(answers, p, data, file_name, decisions, choices)
        summary: PreparedFile = state["summary"]
        built = state["built"]
        own, shared = setting_values(self.fmt, choices)
        t = self.fmt.binding.target
        issues: list[ValueIssue] = [*summary.value_issues, *built.issues]
        excluded: list[ExcludedRow] = [*summary.excluded_rows, *built.skipped]
        notices: list[str] = []
        content, filename = b"", ""
        status = "prepared"
        warnings: dict[str, list[int]] = defaultdict(list)
        if self.fmt.domain == DomainKey.PARTNER:
            result = PartnerReadResult(
                plan_checksum="file", partners=built.partners, read=len(built.partners)
            )
            preview = exports.partners.file_preview(t.system, t.product, result, own, org, self.client)
            for r in preview.records:
                row = built.row_of.get(r.source_ref, 0)
                if r.status == RecordStatus.BLOCKED:
                    excluded.append(
                        ExcludedRow(
                            row=row,
                            reason=f"{r.name}: {blocked_reasons(r.issues)}: not written.",
                            kind="target",
                        )
                    )
                for i in r.issues:
                    if i.level == IssueLevel.WARNING:
                        warnings[i.message].append(row - 1)
            written = sum(1 for r in preview.records if r.status != RecordStatus.BLOCKED)
            new = sum(1 for r in preview.records if r.account_new and r.status != RecordStatus.BLOCKED)
            offset = sum(1 for r in preview.records if r.account_number and r.status != RecordStatus.BLOCKED)
            notices.append(
                say("{n} [account|accounts] for your", written)
                + f" {self._noun(answers, written)}"
                + (f", {count(new, 'new account number')} reserved at download" if new else "")
                + "."
            )
            del offset
            if generate:
                try:
                    gen = exports.partners.file_generate(
                        t.system, t.product, result, own, org, self.client, vat=built.vat
                    )
                except (ExportNotReady, ExportError) as exc:
                    raise WizardInputError(str(exc)) from None
                content, filename = gen.content, gen.filename
        else:
            result, span = self._documents(built, own, shared, choices)
            decided = mapping_decisions(choices, {})
            try:
                prep, plan, content, gen = exports.accounting.file_export(
                    t.system,
                    t.product,
                    result,
                    own,
                    shared,
                    decided,
                    org,
                    span,
                    version=self.fmt.version,
                    generate=generate,
                    client_key=self.client,
                )
            except (AccountingTargetNotReady, ExportInvalid) as exc:
                # réglages incomplets, ou AUCUNE pièce écrivable (toutes bloquées : clients sans compte...) :
                # l'analyse aboutit quand même, chaque ligne avec son motif ; seul le fichier est refusé
                if generate:
                    raise WizardInputError(str(exc)) from None
                prep, _ = exports.accounting.file_preparation(
                    t.system, t.product, result, own, shared, decided, org, self.fmt.version, self.client
                )
                plan, content, gen = None, b"", None
                notices.append(
                    str(exc)
                    if isinstance(exc, AccountingTargetNotReady)
                    else "No invoice can be written to the file: every invoice is blocked (see each row)."
                )
                status = "to_complete"
            by_ref = {d.source_ref: d for d in prep.documents}
            for d in prep.documents:
                row = built.row_of.get(d.source_ref, 0)
                every = [*d.issues, *(i for bk in d.bookings for i in bk.issues)]
                if d.target_status == RecordStatus.BLOCKED:
                    excluded.append(
                        ExcludedRow(
                            row=row,
                            reason=f"Invoice {d.number}: {blocked_reasons(every)}: not written.",
                            kind="target",
                        )
                    )
                for i in every:
                    if i.level == IssueLevel.WARNING:
                        warnings[i.message].append(row - 1)
            if plan is not None:
                for x in plan.excluded:
                    if x.export_state is not None and x.document_ref in by_ref:
                        excluded.append(
                            ExcludedRow(
                                row=built.row_of.get(x.document_ref, 0),
                                reason=f"Invoice {x.number}: " + "; ".join(x.reasons) + ".",
                                kind="already_exported",
                            )
                        )
                written = plan.exported_documents
                notices.append(
                    say("{n} [invoice|invoices] in the file", written)
                    + f" ({count(plan.row_count, 'booking')}, period {span.text})."
                )
            else:
                written = sum(
                    1 for d in prep.documents if d.target_status in (RecordStatus.READY, RecordStatus.WARNING)
                )
            if gen is not None:
                content, filename = gen.content, gen.filename
        for message, idx in warnings.items():
            issues.append(
                ValueIssue(
                    field="",
                    column="",
                    kind="target_warning",
                    count=len(idx),
                    examples=[],
                    message=message,
                    rows=rows_of(idx),
                    severity="to_check",
                )
            )
        pending = [k for k, v in choices.items() if k.startswith((SETTING, MAPPING)) and not v]
        if pending:
            status = "to_complete"
        questions: list[ValueQuestion] = [*summary.value_questions, *built.questions]
        out = summary.model_copy(
            update={
                "rows": written,
                "value_issues": issues,
                "value_questions": questions,
                "value_transforms": [*summary.value_transforms, *getattr(built, "transforms", [])],
                "excluded_rows": sorted(excluded, key=lambda e: (e.row, e.kind)),
                "status": status,
                "missing_required": [],
                "delivery": "format",
                "notices": notices,
                "created_records": [],
            }
        )
        return out, content, filename

    def _partner_vat(self) -> dict[str, tuple[str | None, str | None]]:
        """Numéro du client → état de son numéro de TVA, retenu à l'export de la liste des clients (comptes du
        format partagé, même dossier, source fichier) ; {} : clients pas encore exportés depuis un fichier."""
        b = self.fmt.binding
        if (
            b.inherit_profile is None
            or self.svc.exports is None
            or not isinstance(self.svc.ctx, TenantContext)
        ):
            return {}
        t = b.inherit_profile.target
        profile = self.svc.exports.profiles.get(t.system, t.product, client_key=self.client)
        if profile is None:
            return {}
        rows = self.svc.s.scalars(
            select(ExportAccountAssignmentRecord).where(
                ExportAccountAssignmentRecord.profile_id == profile.id,
                ExportAccountAssignmentRecord.instance_id.is_(None),
                ExportAccountAssignmentRecord.source_ref.like(f"{FILE_REF}%"),
            )
        )
        return {r.source_ref.removeprefix(FILE_REF): (r.vat_status, r.vat_country) for r in rows}

    def _noun(self, answers: list[WizardAnswer], n: int) -> str:
        data_type = next((a.label for a in answers if a.topic == DATA_TYPE_TOPIC), "records").lower()
        one = {
            "customers": "customer",
            "suppliers": "supplier",
            "customers & suppliers": "customer or supplier",
        }
        return one.get(data_type, data_type) if n == 1 else data_type

    def _documents(
        self, built: Any, own: dict[str, Any], shared: dict[str, Any], choices: dict[str, str]
    ) -> tuple[AccountingReadResult, PeriodSpan]:
        """Pièces construites → résultat de lecture ; période = mois couverts par les pièces de l'exercice."""
        fy_raw = shared.get("fiscal_year_start") or self._saved()[1].get("fiscal_year_start")
        days = [d.accounting_date for d in built.documents]
        try:
            start = date.fromisoformat(str(fy_raw)) if fy_raw else None
        except ValueError:
            start = None
        if start is not None:
            inside = [d for d in days if start <= d < date(start.year + 1, start.month, start.day)]
            days = inside or days
        span = PeriodSpan.covering(days or [date.today()])
        items = [
            AccountingReadItem(
                number=d.number, status=DocumentStatus.POSTED, document_type=d.document_type, document=d
            )
            for d in built.documents
        ]
        del choices, own
        result = AccountingReadResult(
            period=str(span),
            company=CompanyRef(source_ref="file", name=None),
            plan_checksum="file",
            items=items,
        )
        return result, span


__all__ = ["FormatFlow", "category"]
