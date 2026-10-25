"""AccountingReadService : lit les pièces comptables d'UNE société d'un système source pour UNE période (mois
calendaire de la date comptable) et les rend en Accounting CDM. Lecture seule, à la demande, rien n'est
stocké.

    système source ──► CatalogSource (conditions génériques : société =, date comptable entre, type parmi)
                  ──► plan ACCOUNTING (semantic bindings) + value_maps du pack ──► AccountingDocument

Aucun nom de système : champs par semantic key, valeurs source traduites par les `value_maps` du pack (une
valeur absente de la traduction est NON PRISE EN CHARGE : signalée, jamais devinée). Seuls les types de
pièce traduits sont demandés à la source (les autres, ex. écritures diverses, sont hors du domaine).
Brouillons et pièces annulées sont rendus (pour « Not exported ») mais jamais exportables.

Source à SCHÉMA FIXE (binding de source du domaine ACCOUNTING) : ni découverte ni analyse ; lecture par
un `RecordSource` filtré sur la période, conversion `uie_core.accounting.fixed` (en-tête seulement si la
source n'a pas de comptes ; champs du CDM impossibles à remplir listés dans `gaps`). Une seule société.

Constructions non prises en charge en V1, DÉTECTÉES et conservées (la pièce reste visible, non exportable) :
ligne d'un type non traduit, plusieurs taxes sur une ligne, taxe non « pourcentage », taxe incluse dans le
prix, pièce déséquilibrée, partenaire ou devise absents. Aucun numéro de compte ni code de taxe d'une cible
ici."""

from __future__ import annotations

import calendar
import os
import re
import uuid
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from uie_cdm import (
    AccountingDocument,
    DocumentLine,
    DocumentStatus,
    DocumentType,
    LedgerAccountRef,
    LineKind,
    PartnerRef,
    TaxComputation,
    TaxRef,
    TaxScope,
)

from uie_core.accounting import fixed
from uie_core.accounting.plan import AccountingReadPlan, build_accounting_plan
from uie_core.catalog.plan import BoundField, EntityPlan
from uie_core.catalog.reader import (
    CatalogReaderResolver,
    CatalogSource,
    Condition,
    FieldRequest,
    Ref,
    SourceRecord,
)
from uie_core.domains.enrichment import SemanticEnrichmentService
from uie_core.domains.model import DomainKey
from uie_core.domains.pack import ValueMap
from uie_core.domains.semantics import DecisionSource, MeaningStatus
from uie_core.identity.context import Context, ensure_context
from uie_core.instances import InstanceService
from uie_core.registry import models as m
from uie_core.registry.service import NotFound, RegistryError
from uie_core.sources import (
    RecordPeriod,
    RecordSource,
    RecordSourceError,
    RecordSourceResolver,
    SourceBinding,
    SourceBindingError,
    SourceBindingRegistry,
    SourceEntity,
    value_at,
)

BATCH = 200
DEFAULT_MAX_DOCUMENTS = 10_000
USABLE = (MeaningStatus.KNOWN.value, MeaningStatus.CONFIRMED.value)
_PERIOD = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


LABELS_MAX = 5_000  # libellés lus par référence (ex. catégories d'un compte)
SINGLE_COMPANY = "single"  # source à schéma fixe : un compte de la source = une société


def accounting_read_max() -> int:
    return int(os.environ.get("UIE_ACCOUNTING_READ_MAX", DEFAULT_MAX_DOCUMENTS))


class AccountingError(RegistryError):
    """Lecture comptable impossible (→ 400)."""


class AccountingNotReady(AccountingError):
    """Analyse ACCOUNTING absente ou incomplète (→ 409)."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AccountingPeriod(_Model):
    """Période comptable V1 : un mois calendaire de la date comptable."""

    year: int
    month: int

    @model_validator(mode="after")
    def _valid(self) -> AccountingPeriod:
        if not (2000 <= self.year <= 2099 and 1 <= self.month <= 12):
            raise ValueError("period must be a month between 2000-01 and 2099-12")
        return self

    @classmethod
    def parse(cls, value: str) -> AccountingPeriod:
        found = _PERIOD.match(value.strip())
        if not found:
            raise AccountingError(f"Invalid accounting period « {value} »: expected YYYY-MM")
        return cls(year=int(found.group(1)), month=int(found.group(2)))

    @property
    def first_day(self) -> date:
        return date(self.year, self.month, 1)

    @property
    def last_day(self) -> date:
        return date(self.year, self.month, calendar.monthrange(self.year, self.month)[1])

    def __str__(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"


class ReadIssue(_Model):
    code: str  # ex. UNSUPPORTED_LINE_KIND, MULTIPLE_TAXES, UNBALANCED
    message: str  # phrase simple (anglais), sans nom technique


class AccountingReadItem(_Model):
    number: str | None
    status: DocumentStatus | None
    document_type: DocumentType | None
    document: AccountingDocument | None  # None : pièce non représentable (voir issues)
    issues: list[ReadIssue] = []

    @property
    def exportable(self) -> bool:
        """Candidate à un export : comptabilisée, représentée, sans construction non prise en charge."""
        return self.document is not None and self.status == DocumentStatus.POSTED and not self.issues


class CompanyRef(_Model):
    source_ref: str
    name: str | None


class AccountingReadResult(_Model):
    period: str
    company: CompanyRef
    plan_checksum: str
    items: list[AccountingReadItem]
    truncated: bool = False
    # champs du CDM que la source ne permet pas de remplir sans inventer (source à schéma fixe)
    gaps: list[ReadIssue] = []

    def by_status(self) -> dict[str, int]:
        return dict(Counter(i.status.value if i.status else "UNKNOWN" for i in self.items))

    def by_type(self) -> dict[str, int]:
        return dict(Counter(i.document_type.value if i.document_type else "UNKNOWN" for i in self.items))


class AccountingReadService:
    def __init__(
        self,
        session: Session,
        ctx: Context,
        instances: InstanceService,
        enrichment: SemanticEnrichmentService,
        readers: CatalogReaderResolver | None = None,
        max_documents: int | None = None,
        sources: RecordSourceResolver | None = None,
    ) -> None:
        self.s = session
        self.ctx = ensure_context(ctx)
        if instances.ctx != self.ctx or enrichment.ctx != self.ctx:
            raise ValueError("Les services de la lecture comptable doivent partager le même contexte")
        if enrichment.domain_key != DomainKey.ACCOUNTING:
            raise ValueError("AccountingReadService attend l'enrichissement du domaine ACCOUNTING")
        self.instances = instances
        self.enrichment = enrichment
        self.domains = enrichment.domains
        self.readers = readers or CatalogReaderResolver()
        self.sources = sources or RecordSourceResolver()
        self.max_documents = max_documents or accounting_read_max()

    # ------------------------------------------------------------------ #
    # Source à schéma fixe
    # ------------------------------------------------------------------ #
    @staticmethod
    def source_binding(inst: m.Instance) -> SourceBinding | None:
        ver = inst.interface.version
        return SourceBindingRegistry.find(
            DomainKey.ACCOUNTING.value, ver.product.system.code, ver.version, inst.interface.code
        )

    def _checked(self, inst: m.Instance, binding: SourceBinding) -> SourceEntity:
        snap = self.s.scalar(
            select(m.SchemaSnapshot)
            .where(m.SchemaSnapshot.interface_id == inst.interface_id)
            .order_by(m.SchemaSnapshot.created_at.desc())
            .limit(1)
        )
        if snap is None:
            raise AccountingError("The contract of this system is not in the Registry")
        try:
            binding.check_contract(
                {e.technical_name: {f.technical_name for f in e.fields} for e in snap.entities}
            )
            return fixed.document_spec(binding)
        except SourceBindingError as exc:
            raise AccountingError(str(exc)) from None

    def _read_fixed(
        self, inst: m.Instance, binding: SourceBinding, company: str, period: AccountingPeriod
    ) -> AccountingReadResult:
        if company != SINGLE_COMPANY:
            raise AccountingError("Unknown company for this system")
        spec = self._checked(inst, binding)
        _, ctx = self.instances.connection_context(inst.id)
        window = (
            RecordPeriod(path=spec.period, start=period.first_day, end=period.last_day)
            if spec.period
            else None
        )
        ver = inst.interface.version
        try:
            with self.sources.open(ver.product.system.code, inst.interface.code, ctx) as src:
                batch = src.records(
                    spec.resource, limit=self.max_documents, period=window, detail=spec.detail
                )
                labels, unlabeled = _labels(src, binding)
        except RecordSourceError as exc:
            raise AccountingError(f"The documents could not be read: {exc}") from None
        items: list[AccountingReadItem] = []
        for raw in batch.records:
            rid, _, values = spec.evaluate(raw)
            if rid is None:
                continue
            lines = spec.items.evaluate(raw) if spec.items else []
            number, status, doc_type, document, problems = fixed.convert(rid, values, lines, labels)
            if document and not (period.first_day <= document.accounting_date <= period.last_day):
                continue  # filtre de la source contrôlé : jamais une pièce hors période
            items.append(
                AccountingReadItem(
                    number=number,
                    status=status,
                    document_type=doc_type,
                    document=document,
                    issues=_unique([ReadIssue(code=c, message=msg) for c, msg in problems]),
                )
            )
        return AccountingReadResult(
            period=str(period),
            company=CompanyRef(source_ref=SINGLE_COMPANY, name=inst.name),
            plan_checksum=binding.checksum,
            items=items,
            truncated=batch.truncated,
            gaps=[ReadIssue(code=field, message=reason) for field, reason in fixed.cdm_gaps(binding)]
            + [
                ReadIssue(code=f"{key}.name", message="The names could not be read: identifiers are shown")
                for key in unlabeled
            ],
        )

    # ------------------------------------------------------------------ #
    def read_plan(self, instance_id: uuid.UUID) -> AccountingReadPlan:
        inst = self.domains.instance(instance_id)  # contrôle d'appartenance
        stored = self.domains.get_instance_domain(inst.id)
        rows = self.s.scalars(
            select(m.FieldSemanticBinding).where(
                m.FieldSemanticBinding.instance_id == inst.id,
                m.FieldSemanticBinding.domain_key == DomainKey.ACCOUNTING.value,
                m.FieldSemanticBinding.meaning_status.in_(USABLE),
                m.FieldSemanticBinding.semantic_key.is_not(None),
                m.FieldSemanticBinding.pending.is_(False),
            )
        )
        bound = [
            BoundField(
                entity=r.entity_technical_name,
                field=r.field_technical_name,
                key=r.semantic_key or "",
                human=r.source == DecisionSource.HUMAN.value,
                pack=r.source == DecisionSource.STANDARD.value,
            )
            for r in rows
        ]
        snap = stored.snapshot
        return build_accounting_plan(
            snap.entities, [*snap.fields, *snap.extensions], bound, domain_snapshot_id=stored.id
        )

    def ensure_ready(self, instance_id: uuid.UUID) -> m.Instance:
        inst = self.instances.get(instance_id)  # contrôle d'appartenance AVANT tout secret
        if self.source_binding(inst) is not None:
            return inst  # schéma fixe : rien à analyser
        try:
            summary = self.enrichment.summary(inst.id)
        except NotFound:
            raise AccountingNotReady(
                "Accounting data not analyzed yet: run the accounting analysis first"
            ) from None
        if not summary.complete:
            raise AccountingNotReady(
                f"Accounting analysis not complete: {summary.pending} question(s) to answer"
            )
        return inst

    def _source(self, inst: m.Instance) -> CatalogSource:
        _, ctx = self.instances.connection_context(inst.id)
        ver = inst.interface.version
        return self.readers.resolve(ver.product.system.code, ver.version, inst.interface.code).open(ctx)

    def companies(self, instance_id: uuid.UUID) -> list[CompanyRef]:
        """Sociétés de la source (cible de la clé `company` des pièces) : à choisir explicitement."""
        inst = self.ensure_ready(instance_id)
        if self.source_binding(inst) is not None:
            return [CompanyRef(source_ref=SINGLE_COMPANY, name=inst.name)]  # un compte = une société
        plan = self.read_plan(inst.id)
        company = plan.document.get("company")
        target = company.target if company else None
        if not target:
            raise AccountingError("The company of the documents cannot be identified")
        src = self._source(inst)
        return [CompanyRef(source_ref=r.id, name=r.label) for r in src.read_page(target, [], limit=BATCH)]

    def read(self, instance_id: uuid.UUID, company: str, period: AccountingPeriod) -> AccountingReadResult:
        inst = self.ensure_ready(instance_id)
        binding = self.source_binding(inst)
        if binding is not None:
            return self._read_fixed(inst, binding, company, period)
        plan = self.read_plan(inst.id)
        maps = _value_maps(self.domains.pack_for_instance(inst).value_maps(), plan)
        return read_documents(self._source(inst), plan, maps, company, period, self.max_documents)


# --------------------------------------------------------------------------- #
# Lecture + conversion (pures sur une CatalogSource)
# --------------------------------------------------------------------------- #
ENUM_KEYS = {
    "document_type": DocumentType,
    "document_status": DocumentStatus,
    "line_kind": LineKind,
    "tax_computation": TaxComputation,
    "tax_usage": TaxScope,
}


def _labels(src: RecordSource, binding: SourceBinding) -> tuple[dict[str, dict[str, str]], list[str]]:
    """Libellés des références déclarées par le binding (ex. noms des catégories), lus UNE fois. Illisibles :
    les identifiants restent affichés (jamais un nom deviné) et la clé est signalée."""
    labels: dict[str, dict[str, str]] = {}
    unlabeled: list[str] = []
    for key, ls in binding.label_sources().items():
        try:
            batch = src.records(ls.resource, limit=LABELS_MAX)
        except RecordSourceError:
            unlabeled.append(key)
            continue
        labels[key] = {
            str(rid): str(name)
            for r in batch.records
            if (rid := value_at(r, ls.id)) is not None and (name := value_at(r, ls.name))
        }
    return labels, unlabeled


def _value_maps(maps: list[ValueMap], plan: AccountingReadPlan) -> dict[str, ValueMap]:
    """Traduction de valeurs de chaque clé énumérée, pour le champ que le plan a retenu (clé → ValueMap)."""
    out: dict[str, ValueMap] = {}
    by_path = {vm.path: vm for vm in maps}
    for ep in (plan.document, plan.line, plan.tax):
        for key, planned in ep.fields.items():
            if key in ENUM_KEYS:
                vm = by_path.get(f"{ep.entity}.{planned.field}")
                if vm is None or vm.key != key:
                    raise AccountingError(f"No value translation declared for {key}")
                out[key] = vm
    return out


def read_documents(
    src: CatalogSource,
    plan: AccountingReadPlan,
    maps: dict[str, ValueMap],
    company: str,
    period: AccountingPeriod,
    max_documents: int,
) -> AccountingReadResult:
    d = plan.document
    company_field, date_field, type_field = (
        d.fields["company"],
        d.fields["accounting_date"],
        d.fields["document_type"],
    )
    where = [
        Condition(field=company_field.field, op="eq", values=[company], data_type="reference"),
        Condition(
            field=date_field.field,
            op="between",
            values=[period.first_day.isoformat(), period.last_day.isoformat()],
            data_type="date",
        ),
        # seuls les types traduits par le pack : le reste (écritures diverses…) est hors du domaine
        Condition(field=type_field.field, op="in", values=sorted(maps["document_type"].values)),
    ]
    total = src.count(d.entity, where)
    wanted = min(total, max_documents)
    docs: list[SourceRecord] = []
    for offset in range(0, wanted, BATCH):
        docs += src.read_page(
            d.entity, _requests(d), where=where, offset=offset, limit=min(BATCH, wanted - offset)
        )

    lines_by_doc = _lines(src, plan.line, [r.id for r in docs])
    all_lines = [ln for lines in lines_by_doc.values() for ln in lines]
    accounts = _related(src, plan.account, {_ref(ln, plan.line, "ledger_account") for ln in all_lines})
    tax_ids = {t for ln in all_lines for t in _ids(ln, plan.line, "line_taxes")}
    tax_ids |= {_ref(ln, plan.line, "line_tax") for ln in all_lines} - {None}
    taxes = _related(src, plan.tax, tax_ids)
    currencies = _related(src, plan.currency, {_ref(r, d, "currency") for r in docs})

    company_name = next(
        (v.label for r in docs if isinstance(v := r.values.get(company_field.field), Ref)), None
    )
    items = [_item(r, plan, maps, lines_by_doc.get(r.id, []), accounts, taxes, currencies) for r in docs]
    return AccountingReadResult(
        period=str(period),
        company=CompanyRef(source_ref=company, name=company_name),
        plan_checksum=plan.checksum,
        items=items,
        truncated=total > max_documents,
    )


def _item(
    rec: SourceRecord,
    plan: AccountingReadPlan,
    maps: dict[str, ValueMap],
    lines: list[SourceRecord],
    accounts: dict[str, SourceRecord],
    taxes: dict[str, SourceRecord],
    currencies: dict[str, SourceRecord],
) -> AccountingReadItem:
    d, issues = plan.document, []
    number = _text(_get(rec, d, "document_number"))
    doc_type = _enum(maps, "document_type", _get(rec, d, "document_type"))
    status = _enum(maps, "document_status", _get(rec, d, "document_status"))
    if status is None:
        issues.append(ReadIssue(code="UNSUPPORTED_STATUS", message="The document status is not supported"))

    partner = _get(rec, d, "partner")
    currency_rec = currencies.get(_ref(rec, d, "currency") or "")
    currency = (
        (_text(_get(currency_rec, plan.currency, "currency_code")) or "").upper() if currency_rec else ""
    )
    if not isinstance(partner, Ref):
        issues.append(ReadIssue(code="MISSING_PARTNER", message="The document has no customer or supplier"))
    if not re.fullmatch(r"[A-Z]{3}", currency):
        issues.append(
            ReadIssue(code="MISSING_CURRENCY", message="The currency of the document is not recognized")
        )

    tax_refs = {tid: _tax(t, plan.tax, maps) for tid, t in taxes.items()}
    cdm_lines: list[DocumentLine] = []
    for ln in lines:
        line = _line(ln, plan, maps, accounts, tax_refs, issues)
        if line is not None:
            cdm_lines.append(line)
    for tax in {t.source_ref: t for line in cdm_lines for t in line.taxes}.values():
        if tax.computation is None:
            issues.append(
                ReadIssue(
                    code="UNSUPPORTED_TAX", message=f"Tax « {tax.name} » uses an unsupported computation"
                )
            )
        elif tax.computation != TaxComputation.PERCENT:
            issues.append(
                ReadIssue(code="TAX_NOT_PERCENT", message=f"Tax « {tax.name} » is not a percentage tax")
            )
        if tax.price_included:
            issues.append(
                ReadIssue(code="PRICE_INCLUDED_TAX", message=f"Tax « {tax.name} » is included in the price")
            )

    document = None
    if (
        doc_type is not None
        and status is not None
        and isinstance(partner, Ref)
        and not any(i.code == "MISSING_CURRENCY" for i in issues)
    ):
        try:
            document = AccountingDocument(
                source_ref=rec.id,
                document_type=doc_type,
                status=status,
                number=number if status == DocumentStatus.POSTED or (number and number != "/") else None,
                reference=_text(_get(rec, d, "document_reference")),
                accounting_date=_date(_get(rec, d, "accounting_date")),
                document_date=_opt_date(_get(rec, d, "document_date")),
                due_date=_opt_date(_get(rec, d, "due_date")),
                currency=currency,
                # même identité source que le Partner CDM (référence du partenaire commercial)
                partner=PartnerRef(
                    source_ref=partner.id, role=doc_type.partner_role, name=partner.label or partner.id
                ),
                total_amount=abs(_decimal(_get(rec, d, "total_amount"))),
                tax_amount=abs(_decimal(_get(rec, d, "tax_amount"))),
                lines=cdm_lines,
                description=_text(_get(rec, d, "description")),
            )
        except (ValidationError, ValueError) as exc:
            issues.append(
                ReadIssue(code="INVALID_DOCUMENT", message=f"The document cannot be represented ({exc})")
            )
            document = None
    if document is not None and document.balance() != 0:
        issues.append(
            ReadIssue(code="UNBALANCED", message="Debit and credit of the read lines do not balance")
        )
    return AccountingReadItem(
        number=number, status=status, document_type=doc_type, document=document, issues=_unique(issues)
    )


def _line(
    ln: SourceRecord,
    plan: AccountingReadPlan,
    maps: dict[str, ValueMap],
    accounts: dict[str, SourceRecord],
    tax_refs: dict[str, TaxRef],
    issues: list[ReadIssue],
) -> DocumentLine | None:
    lp = plan.line
    raw_kind = _get(ln, lp, "line_kind")
    vm = maps["line_kind"]
    if raw_kind is not None and str(raw_kind) in vm.ignore:
        return None  # ligne de mise en page, sans montant
    kind = _enum(maps, "line_kind", raw_kind)
    amount = _decimal(_get(ln, lp, "line_amount_currency"), default=_decimal(_get(ln, lp, "line_amount")))
    if kind is None:
        # jamais écartée en silence : la pièce porte l'anomalie (son équilibre le montre aussi)
        issues.append(
            ReadIssue(
                code="UNSUPPORTED_LINE_KIND", message=f"A line of type « {raw_kind} » is not supported yet"
            )
        )
        return None
    account_rec = accounts.get(_ref(ln, lp, "ledger_account") or "")
    code = _text(_get(account_rec, plan.account, "account_code")) if account_rec else None
    if account_rec is None or code is None:
        issues.append(ReadIssue(code="MISSING_ACCOUNT", message="A line has no ledger account"))
        return None
    if kind == LineKind.ITEM:
        refs = [tax_refs[t] for t in _ids(ln, lp, "line_taxes") if t in tax_refs]
    else:
        tax_id = _ref(ln, lp, "line_tax")
        refs = [tax_refs[tax_id]] if tax_id in tax_refs else []
    if kind == LineKind.ITEM and len(refs) > 1:
        issues.append(ReadIssue(code="MULTIPLE_TAXES", message="A line has several taxes"))
    gross = None
    if kind == LineKind.ITEM:
        raw_gross = _get(ln, lp, "line_gross_amount")
        if raw_gross is not None:
            gross = abs(_decimal(raw_gross)) * (-1 if amount < 0 else 1)  # même signe que le montant
    return DocumentLine(
        kind=kind,
        account=LedgerAccountRef(
            source_ref=account_rec.id, code=code, name=_text(_get(account_rec, plan.account, "account_name"))
        ),
        amount=amount,
        gross_amount=gross,
        taxes=refs,
        description=_text(_get(ln, lp, "line_description")),
    )


def _tax(rec: SourceRecord, tp: EntityPlan, maps: dict[str, ValueMap]) -> TaxRef:
    raw_computation = _get(rec, tp, "tax_computation")
    computation = _enum(maps, "tax_computation", raw_computation)
    included = _get(rec, tp, "tax_price_included") is True
    return TaxRef(
        source_ref=rec.id,
        name=_text(_get(rec, tp, "tax_name")) or rec.id,
        computation=computation,
        rate=_decimal(_get(rec, tp, "tax_rate"), default=None),
        scope=_enum(maps, "tax_usage", _get(rec, tp, "tax_usage")),
        price_included=included,
    )


# --------------------------------------------------------------------------- #
def _requests(ep: EntityPlan) -> list[FieldRequest]:
    return [FieldRequest(name=f.field, data_type=f.data_type) for f in ep.requested()]


def _lines(src: CatalogSource, lp: EntityPlan, doc_ids: list[str]) -> dict[str, list[SourceRecord]]:
    link = lp.fields["document"].field
    out: dict[str, list[SourceRecord]] = {}
    for start in range(0, len(doc_ids), BATCH):
        chunk = doc_ids[start : start + BATCH]
        offset = 0
        while True:
            page = src.read_page(
                lp.entity,
                _requests(lp),
                where=[Condition(field=link, values=chunk)],
                offset=offset,
                limit=BATCH,
            )
            for rec in page:
                ref = rec.values.get(link)
                if isinstance(ref, Ref):
                    out.setdefault(ref.id, []).append(rec)
            if len(page) < BATCH:
                break
            offset += BATCH
    return out


def _related(src: CatalogSource, ep: EntityPlan, ids: set[str | None]) -> dict[str, SourceRecord]:
    wanted = sorted(i for i in ids if i)
    out: dict[str, SourceRecord] = {}
    for start in range(0, len(wanted), BATCH):
        for r in src.read(ep.entity, wanted[start : start + BATCH], _requests(ep)):
            out[r.id] = r
    return out


def _enum(maps: dict[str, ValueMap], key: str, raw: Any) -> Any:
    vm = maps.get(key)
    if vm is None or raw is None:
        return None
    value = vm.values.get(str(raw))
    return ENUM_KEYS[key](value) if value else None


def _get(rec: SourceRecord | None, ep: EntityPlan, key: str) -> Any:
    if rec is None:
        return None
    f = ep.get(key)
    return rec.values.get(f.field) if f else None


def _ref(rec: SourceRecord, ep: EntityPlan, key: str) -> str | None:
    v = _get(rec, ep, key)
    return v.id if isinstance(v, Ref) else None


def _ids(rec: SourceRecord, ep: EntityPlan, key: str) -> list[str]:
    v = _get(rec, ep, key)
    return [str(i) for i in v] if isinstance(v, list) else []


def _text(v: Any) -> str | None:
    if v is None or isinstance(v, bool):
        return None
    s = str(v).strip()
    return s or None


def _decimal(v: Any, default: Any = Decimal("0")) -> Any:
    if v is None or isinstance(v, bool):
        return default
    try:
        return Decimal(str(v))
    except InvalidOperation:
        return default


def _date(v: Any) -> date:
    return date.fromisoformat(str(v)[:10])


def _opt_date(v: Any) -> date | None:
    return _date(v) if v else None


def _unique(issues: list[ReadIssue]) -> list[ReadIssue]:
    seen: dict[tuple[str, str], ReadIssue] = {}
    for i in issues:
        seen.setdefault((i.code, i.message), i)
    return list(seen.values())
