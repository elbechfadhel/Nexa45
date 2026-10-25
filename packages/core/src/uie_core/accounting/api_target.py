"""Préparation de pièces comptables pour une cible PAR API (une requête par pièce) : APERÇU seulement.

    n'importe quelle source ──► Accounting CDM ──► écritures gross_by_account_and_tax ──► binding cible
        (`records: per_document`, éléments = écritures) résolu contre le Registry ──► requête validée
        ──► READY / WARNING / BLOCKED + état D11 PAR INSTANCE CIBLE ──► aperçu

Générique des deux côtés : la source n'est connue que par l'Accounting CDM, la cible par son binding, le
contrat du Registry et le contrat d'envoi (`uie_core.delivery`). Aucun nom de système ici.

Valeurs fournies par UIE (jamais devinées) :
- contact de la pièce = lien PARTNER existant (envoi ou rapprochement humain) vers la MÊME cible ;
- valeur de chaque élément (ex. catégorie) = correspondance EXPLICITE compte source → option de la cible,
  enregistrée dans `export_value_mappings` ; l'option est contrôlée contre la liste LUE dans la cible
  (existence, sorte attendue selon le rôle du partenaire).

`prepare_documents` est PURE (ni base, ni réseau). L'aperçu ne fait que LIRE : la source (CDM), la cible
(options, GET seulement), l'historique ; il n'envoie ni n'enregistre rien.

ENVOI (phase 2b) : confié tel quel au service générique `uie_core.delivery` (sélection explicite, clé
d'envoi, recherche de doublons, élément commité avant l'appel, UNKNOWN jamais retenté, lien + D11 par
instance cible) ; ce module ne lui fournit que les requêtes préparées et la portée."""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Callable
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session
from uie_cdm import AccountingDocument, DocumentStatus, TaxComputation

from uie_core.accounting.bookings import Booking, gross_bookings
from uie_core.accounting.service import AccountingPeriod, AccountingReadItem, AccountingReadService
from uie_core.delivery.contract import (
    RecordSender,
    RecordSenderError,
    RecordSenderResolver,
    SendError,
    TargetChoice,
)
from uie_core.delivery.records import TargetSendItem, TargetSendRun
from uie_core.delivery.service import DeliveryCandidate, DeliveryScope, DeliveryService
from uie_core.exports.documents import DocumentContent, DocumentExportLedger, DocumentExportState
from uie_core.exports.records import ExportValueMappingRecord
from uie_core.identity.context import AdminContext, Context, TenantContext, ensure_context
from uie_core.mapping import (
    IssueLevel,
    LookupKind,
    LookupSpec,
    PlainValueFormat,
    RecordFileWriterError,
    RecordRequestBuilder,
    RecordRequestBuilderRegistry,
    RecordStatus,
    ResolvedBinding,
    TargetBinding,
    TargetBindingError,
    TargetBindingRegistry,
    TargetIssue,
    map_record,
    resolve_binding,
)
from uie_core.registry import models as m
from uie_core.registry.service import NotFound, RegistryError, RegistryService

SOURCE_MODEL = "accounting"
DOMAIN = "accounting"
PARTNER_DOMAIN = "partner"  # liens créés par l'envoi des partenaires (même source, même cible)
DELIVERY = "api"
ENCODING = "utf-8"
ZERO = Decimal("0")
CENT = Decimal("0.01")


class AccountingApiTargetError(RegistryError):
    """Préparation impossible (→ 400)."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PreparedItem(_Model):
    booking: int  # rang de l'écriture dans la pièce
    account_ref: str
    account_code: str
    account_name: str | None
    tax_name: str | None
    values: dict[str, Any]  # champ du contrat → valeur préparée
    issues: list[TargetIssue]
    # faits GÉNÉRIQUES de l'écriture (CDM), pour l'affichage : jamais un nom de champ de la cible
    gross_amount: Decimal = ZERO
    tax_amount: Decimal = ZERO
    tax_rate: Decimal | None = None
    choice_id: str | None = None
    choice_label: str | None = None


class PreparedApiDocument(_Model):
    source_ref: str
    number: str | None
    document_type: str | None
    partner_name: str | None
    status: RecordStatus
    issues: list[TargetIssue]
    values: dict[str, Any]  # champs de la pièce
    items: list[PreparedItem]
    request: dict[str, Any] | None  # None : bloquée
    content: DocumentContent
    # faits GÉNÉRIQUES de la pièce (CDM) pour l'affichage
    document_date: date | None = None
    due_date: date | None = None
    currency: str | None = None
    total_amount: Decimal | None = None
    tax_amount: Decimal | None = None
    contact_linked: bool = False


class AccountNeed(_Model):
    """Compte source utilisé par les pièces de l'aperçu, avec la correspondance décidée (ou non)."""

    account_ref: str
    code: str
    name: str | None
    role: str  # rôle du partenaire des pièces qui l'utilisent (sorte d'option attendue)
    choice_id: str | None
    choice_label: str | None
    problem: str | None


class PreviewApiDocument(_Model):
    prepared: PreparedApiDocument
    export_state: DocumentExportState
    changed_fields: list[str] = []
    outcome_unknown: bool = False  # un envoi précédent a une issue inconnue non résolue

    @property
    def skip_reason(self) -> str | None:
        """Pourquoi cette pièce ne peut PAS être envoyée (None : envoyable). Règle stricte du domaine :
        seulement une pièce prête, sans avertissement, jamais envoyée. Une pièce modifiée depuis son envoi
        demande une autorisation spécifique (pas encore offerte) : jamais renvoyée d'ici."""
        if self.prepared.status == RecordStatus.BLOCKED:
            return "BLOCKED"
        if self.outcome_unknown:
            return "OUTCOME_UNKNOWN"
        if self.export_state == DocumentExportState.ALREADY_EXPORTED:
            return "UNCHANGED"
        if self.export_state == DocumentExportState.CHANGED_SINCE_EXPORT:
            return "CHANGED_NEEDS_APPROVAL"
        if self.prepared.status != RecordStatus.READY:
            return "WARNING_NOT_SENT"
        return None

    @property
    def sendable(self) -> bool:
        return self.skip_reason is None


class AccountingApiPreview(_Model):
    source_name: str
    target_name: str
    target_system: str
    target: str  # nom du binding
    contract: str
    company: str | None
    period: str
    options_checked: bool  # la liste des options de la cible a été lue (sinon : non vérifiées)
    accounts: list[AccountNeed]
    records: list[PreviewApiDocument]
    truncated: bool

    def count(self, status: RecordStatus) -> int:
        return sum(1 for r in self.records if r.prepared.status == status)


class ApiSendResult(_Model):
    run: Any  # TargetSendRun
    items: list[Any]  # TargetSendItem
    numbers: dict[str, str | None]  # référence source → numéro de la pièce (affichage)


class TargetOptionOut(_Model):
    instance_id: uuid.UUID
    name: str
    system: str
    target: str
    connected: bool


# --------------------------------------------------------------------------- #
# Préparation PURE
# --------------------------------------------------------------------------- #
def _document_view(doc: AccountingDocument) -> dict[str, Any]:
    return {
        "kind": doc.document_type.value,
        "role": doc.document_type.partner_role.value,
        "document_type": doc.document_type.value,
        "number": doc.number,
        "reference": doc.reference,
        "document_date": doc.document_date,
        "accounting_date": doc.accounting_date,
        "due_date": doc.due_date,
        "total_amount": doc.total_amount,
        "tax_amount": doc.tax_amount,
        "description": doc.description,
        "currency": doc.currency,
        # note de provenance : numéro de la pièce dans SA source, puis sa description (rien n'est perdu
        # quand le numéro envoyé à la cible est une référence externe)
        "source_note": " · ".join(x for x in (doc.number, doc.description) if x) or None,
    }


def _booking_view(b: Booking) -> dict[str, Any]:
    rate = b.tax.rate if b.tax is not None else ZERO
    return {"amount": b.amount, "tax_amount": abs(b.tax_amount), "tax_rate": rate}


def _issue(message: str, level: IssueLevel = IssueLevel.BLOCKING) -> TargetIssue:
    return TargetIssue(level=level, message=message)


def _source_reasons(item: AccountingReadItem) -> list[str]:
    if item.document is None or item.issues:
        return [i.message for i in item.issues] or ["The document could not be read"]
    if item.status == DocumentStatus.DRAFT:
        return ["Draft documents are not sent"]
    if item.status == DocumentStatus.CANCELLED:
        return ["Cancelled documents are not sent"]
    return []


def _items_binding(resolved: ResolvedBinding) -> ResolvedBinding:
    """Même contrat, seulement les champs des éléments (ni règles, ni données écartées de la pièce)."""
    b = resolved.binding
    assert b.items is not None
    copy = b.model_copy(update={"fields": b.items.fields, "rules": [], "omitted": [], "repeat": []})
    return dataclasses.replace(resolved, binding=copy)


def _document_binding(resolved: ResolvedBinding) -> ResolvedBinding:
    return dataclasses.replace(resolved, binding=resolved.binding.model_copy(update={"items": None}))


def prepare_documents(
    items: list[AccountingReadItem],
    resolved: ResolvedBinding,
    builder: RecordRequestBuilder,
    *,
    contacts: dict[str, str],
    choices: dict[str, str],
    options: dict[str, TargetChoice] | None,
) -> list[PreparedApiDocument]:
    """Accounting CDM → requêtes de la cible, une par pièce. PURE.

    `contacts` : référence source du partenaire → identifiant cible (liens PARTNER) ; `choices` : référence
    source du compte → option choisie ; `options` : options LUES dans la cible (None : non vérifiables)."""
    b = resolved.binding
    if b.items is None or b.booking is None:
        raise AccountingApiTargetError(f"Le binding {b.name} ne prépare pas de pièces")
    lookup = b.lookup(LookupKind.ACCOUNT)
    fmt = PlainValueFormat()
    doc_resolved, item_resolved = _document_binding(resolved), _items_binding(resolved)
    fields = {f.technical_name: f for f in resolved.fields}
    display = b.target.display_name
    out = []
    for read in items:
        doc = read.document
        reasons = _source_reasons(read)
        if doc is None or reasons:
            out.append(
                PreparedApiDocument(
                    source_ref=doc.source_ref if doc else f"#{read.number or '?'}",
                    number=read.number,
                    document_type=read.document_type.value if read.document_type else None,
                    partner_name=doc.partner.name if doc else None,
                    status=RecordStatus.BLOCKED,
                    issues=[_issue(r) for r in reasons],
                    values={},
                    items=[],
                    request=None,
                    content=DocumentContent(source_ref=doc.source_ref if doc else "?", units=[]),
                    document_date=(doc.document_date or doc.accounting_date) if doc else None,
                    due_date=doc.due_date if doc else None,
                    currency=doc.currency if doc else None,
                    total_amount=doc.total_amount if doc else None,
                    tax_amount=doc.tax_amount if doc else None,
                    contact_linked=bool(doc and doc.partner.source_ref in contacts),
                )
            )
            continue
        issues: list[TargetIssue] = []
        if b.currencies and doc.currency not in b.currencies:
            issues.append(
                _issue(f"{display} accepts only {', '.join(b.currencies)} (document in {doc.currency})")
            )
        bookings = gross_bookings(doc)
        issues += [_issue(i.message) for i in bookings.issues]
        if any(bk.counter_account is None for bk in bookings.bookings):
            # source sans plan comptable (CDM 1.1) : pas encore de correspondance catégorie → cible par API
            issues.append(_issue(f"Lines without a ledger account cannot be sent to {display} yet"))
        rec = map_record(
            _document_view(doc),
            doc_resolved,
            fmt,
            ENCODING,
            source_ref=doc.source_ref,
            target_values={"contact_id": contacts.get(doc.partner.source_ref)},
        )
        issues += rec.issues
        role = doc.document_type.partner_role.value
        prepared_items = []
        for bk in bookings.bookings:
            if bk.counter_account is None:
                continue  # signalé au niveau de la pièce : jamais envoyé
            item_issues = _tax_issues(bk)
            choice = choices.get(bk.counter_account.source_ref)
            if lookup is not None:
                item_issues += _choice_issues(lookup, bk, choice, options, role, display)
            irec = map_record(
                _booking_view(bk),
                item_resolved,
                fmt,
                ENCODING,
                source_ref=doc.source_ref,
                target_values={"category_id": choice},
            )
            # sans choix, « catégorie manquante » est déjà dit par `_choice_issues` : pas de doublon
            item_issues += [i for i in irec.issues if choice or i.field != lookup_field(b)]
            prepared_items.append(
                PreparedItem(
                    booking=bk.index,
                    account_ref=bk.counter_account.source_ref,
                    account_code=bk.counter_account.code,
                    account_name=bk.counter_account.name,
                    tax_name=bk.tax.name if bk.tax else None,
                    values=irec.values,
                    issues=item_issues,
                    gross_amount=bk.amount,
                    tax_amount=abs(bk.tax_amount),
                    tax_rate=bk.tax.rate if bk.tax else ZERO,
                    choice_id=choice,
                    choice_label=(options or {}).get(choice or "").label
                    if (options or {}).get(choice or "")
                    else None,
                )
            )
        if bookings.bookings and not bookings.issues:
            issues += _total_issues(doc, bookings.bookings)
        all_issues = issues + [i for it in prepared_items for i in it.issues]
        status = _status(all_issues)
        request = None
        if status != RecordStatus.BLOCKED:
            request = builder.build(doc_resolved.fields, rec.values)
            request[b.items.path] = [
                builder.build(item_resolved.fields, it.values)[b.items.path][0] for it in prepared_items
            ]
        units = [{k: fmt.format_cell(fields[k], v) for k, v in sorted(rec.values.items())}]
        units += [
            {k: fmt.format_cell(fields[k], v) for k, v in sorted(it.values.items())} for it in prepared_items
        ]
        out.append(
            PreparedApiDocument(
                source_ref=doc.source_ref,
                number=doc.number,
                document_type=doc.document_type.value,
                partner_name=doc.partner.name,
                status=status,
                issues=issues,
                values=rec.values,
                items=prepared_items,
                request=request,
                content=DocumentContent(source_ref=doc.source_ref, number=doc.number, units=units),
                document_date=doc.document_date or doc.accounting_date,
                due_date=doc.due_date,
                currency=doc.currency,
                total_amount=doc.total_amount,
                tax_amount=doc.tax_amount,
                contact_linked=doc.partner.source_ref in contacts,
            )
        )
    return out


def lookup_field(b: TargetBinding) -> str | None:
    lk = b.lookup(LookupKind.ACCOUNT)
    return lk.field if lk else None


def _tax_issues(bk: Booking) -> list[TargetIssue]:
    if bk.tax is None:
        return [_issue(f"No tax on account {bk.counter_account.code}: sent with 0 %", IssueLevel.WARNING)]
    if bk.tax.computation not in (None, TaxComputation.PERCENT) or bk.tax.rate is None:
        return [_issue(f"Tax {bk.tax.name} is not a percentage: not supported")]
    if bk.tax.price_included:
        return [_issue(f"Tax {bk.tax.name} is included in the price: not supported yet")]
    return _rounding_issues(bk)


def _rounding_issues(bk: Booking) -> list[TargetIssue]:
    """Taxe de l'élément = taux appliqué au montant TTC, à l'arrondi près. La source arrondit souvent par
    LIGNE : un écart d'un centime par ligne regroupée est toléré (signalé), au-delà la pièce est bloquée.
    Rien n'est jamais recalculé ni corrigé : la taxe envoyée est celle de la source."""
    assert bk.tax is not None and bk.tax.rate is not None
    gross, tax = abs(bk.gross_amount), abs(bk.tax_amount)
    expected = (gross * bk.tax.rate / (100 + bk.tax.rate)).quantize(CENT, rounding=ROUND_HALF_UP)
    gap = abs(expected - tax)
    if gap == 0:
        return []
    account = bk.counter_account.code
    if gap <= CENT * len(bk.lines):
        return [
            _issue(
                f"Tax of account {account} differs by {gap} from {bk.tax.rate} % of the amount "
                "(rounding per line in the source): sent as in the source",
                IssueLevel.WARNING,
            )
        ]
    return [_issue(f"Tax of account {account} does not match its rate {bk.tax.rate} % (gap {gap})")]


def _total_issues(doc: AccountingDocument, bookings: list[Booking]) -> list[TargetIssue]:
    """Totaux de la pièce = somme de ses éléments (sinon la cible les refuserait ou les recalculerait)."""
    gross = sum((b.amount for b in bookings), ZERO)
    tax = sum((abs(b.tax_amount) for b in bookings), ZERO)
    out = []
    if gross != doc.total_amount:
        out.append(_issue(f"The items add up to {gross}, the document total is {doc.total_amount}"))
    if tax != doc.tax_amount:
        out.append(_issue(f"The item taxes add up to {tax}, the document tax is {doc.tax_amount}"))
    return out


def _choice_issues(
    lookup: LookupSpec,
    bk: Booking,
    choice: str | None,
    options: dict[str, TargetChoice] | None,
    role: str,
    display: str,
) -> list[TargetIssue]:
    account = f"{bk.counter_account.code} {bk.counter_account.name or ''}".strip()
    if not choice:
        return [_issue(f"Choose the {lookup.label} for account {account}")]
    if options is None:
        return []  # options non lisibles : signalé une fois pour l'aperçu
    option = options.get(choice)
    if option is None:
        return [_issue(f"The {lookup.label} chosen for account {account} no longer exists in {display}")]
    expected = lookup.option_kind_by_role.get(role)
    if expected and option.kind and option.kind != expected:
        return [
            _issue(
                f"The {lookup.label} chosen for account {account} is of kind « {option.kind} »; "
                f"this document needs « {expected} »"
            )
        ]
    return []


def _status(issues: list[TargetIssue]) -> RecordStatus:
    if any(i.level == IssueLevel.BLOCKING for i in issues):
        return RecordStatus.BLOCKED
    return RecordStatus.WARNING if issues else RecordStatus.READY


# --------------------------------------------------------------------------- #
# Service (lecture seule)
# --------------------------------------------------------------------------- #
def _system(inst: m.Instance) -> tuple[m.System, m.SystemProduct, m.SystemVersion]:
    ver = inst.interface.version
    return ver.product.system, ver.product, ver


class AccountingApiTargetService:
    def __init__(
        self,
        session: Session,
        ctx: Context,
        reader: AccountingReadService,
        senders: RecordSenderResolver | None = None,
        commit: Callable[[], None] | None = None,
    ) -> None:
        self.s = session
        self.ctx = ensure_context(ctx)
        if reader.ctx != self.ctx:
            raise ValueError("Les services de préparation doivent partager le même contexte")
        self.reader = reader
        self.instances = reader.instances
        self.registry = RegistryService(session, self.ctx)
        self.ledger = DocumentExportLedger(session)
        self.senders = senders or RecordSenderResolver()
        self.user = self.ctx.user_id if isinstance(self.ctx, TenantContext | AdminContext) else None
        self.delivery = DeliveryService(session, user_id=self.user, commit=commit)

    # ------------------------------------------------------------------ #
    def _binding(self, inst: m.Instance) -> TargetBinding | None:
        system, product, _ = _system(inst)
        try:
            binding = TargetBindingRegistry.get(SOURCE_MODEL, system.code, product.code)
        except TargetBindingError:
            return None
        return binding if binding.delivery == DELIVERY else None

    def targets(self, source_id: uuid.UUID) -> list[TargetOptionOut]:
        source = self.instances.get(source_id)
        out = []
        for inst in self.instances.list():
            if inst.id == source.id or inst.organization_id != source.organization_id:
                continue
            binding = self._binding(inst)
            if binding is None:
                continue
            conn = inst.connections[0] if inst.connections else None
            out.append(
                TargetOptionOut(
                    instance_id=inst.id,
                    name=inst.name,
                    system=_system(inst)[0].name,
                    target=binding.name,
                    connected=bool(conn and conn.last_test_status == "OK"),
                )
            )
        return out

    def _pair(
        self, source_id: uuid.UUID, target_id: uuid.UUID
    ) -> tuple[m.Instance, m.Instance, ResolvedBinding, RecordRequestBuilder]:
        source = self.instances.get(source_id)  # contrôles d'appartenance AVANT toute lecture
        target = self.instances.get(target_id)
        if source.id == target.id:
            raise AccountingApiTargetError("Choose two different systems")
        if source.organization_id != target.organization_id:
            raise NotFound(f"Instance {target_id} introuvable")
        binding = self._binding(target)
        if binding is None:
            raise NotFound(f"Le système {target.name} ne reçoit pas de pièces comptables")
        system, _, version = _system(target)
        try:
            resolved = resolve_binding(self.registry, binding, version.version)
            builder = RecordRequestBuilderRegistry.get(system.code, target.interface.code)
        except (TargetBindingError, RecordFileWriterError) as exc:
            raise AccountingApiTargetError(str(exc)) from None
        return source, target, resolved, builder

    def _scope(self, source: m.Instance, target: m.Instance, b: TargetBinding, domain: str) -> DeliveryScope:
        return DeliveryScope(
            organization_id=source.organization_id,
            source_instance_id=source.id,
            target_instance_id=target.id,
            domain=domain,
            target_system=b.target.system,
            target_product=b.target.product,
            resource=b.target.entity,
        )

    def _sender_factory(self, target: m.Instance) -> Callable[[], RecordSender]:
        system, _, _ = _system(target)
        interface = target.interface.code
        if not self.senders.supports(system.code, interface):
            raise AccountingApiTargetError(f"{system.name} cannot be read here")

        def factory() -> RecordSender:
            _, ctx = self.instances.connection_context(target.id)  # secret déchiffré au dernier moment
            try:
                return self.senders.create(system.code, interface, ctx)
            except (RecordSenderError, SendError) as exc:
                raise AccountingApiTargetError(str(exc)) from None

        return factory

    def options(self, source_id: uuid.UUID, target_id: uuid.UUID) -> list[TargetChoice]:
        """Options de la correspondance des comptes, LUES dans la cible (GET seulement)."""
        _, target, resolved, _ = self._pair(source_id, target_id)
        lookup = resolved.binding.lookup(LookupKind.ACCOUNT)
        if lookup is None or not lookup.options_resource:
            return []
        with self._sender_factory(target)() as sender:
            try:
                return sender.options(lookup.options_resource)
            except SendError as exc:
                raise AccountingApiTargetError(f"The options could not be read ({exc.kind.value})") from None

    def _mappings(self, source: m.Instance, b: TargetBinding) -> dict[str, ExportValueMappingRecord]:
        rows = self.s.scalars(
            select(ExportValueMappingRecord).where(
                ExportValueMappingRecord.organization_id == source.organization_id,
                ExportValueMappingRecord.target_system == b.target.system,
                ExportValueMappingRecord.target_product == b.target.product,
                ExportValueMappingRecord.instance_id == source.id,
                ExportValueMappingRecord.kind == LookupKind.ACCOUNT.value,
            )
        )
        return {r.source_ref: r for r in rows}

    def decide(
        self,
        source_id: uuid.UUID,
        target_id: uuid.UUID,
        account_ref: str,
        choice_id: str,
        *,
        code: str | None = None,
        name: str | None = None,
    ) -> ExportValueMappingRecord:
        """Correspondance EXPLICITE compte source → option de la cible, vérifiée contre la liste lue."""
        source, _, resolved, _ = self._pair(source_id, target_id)
        known = {o.id: o for o in self.options(source_id, target_id)}
        if choice_id not in known:
            raise AccountingApiTargetError("This value does not exist in the target system")
        b = resolved.binding
        row = self._mappings(source, b).get(account_ref)
        if row is None:
            row = ExportValueMappingRecord(
                organization_id=source.organization_id,
                target_system=b.target.system,
                target_product=b.target.product,
                instance_id=source.id,
                kind=LookupKind.ACCOUNT.value,
                source_ref=account_ref,
            )
            self.s.add(row)
        row.target_value = choice_id
        row.origin = "MANUAL"
        row.suggestion_origin = None
        row.source_code = (code or "")[:120] or None
        row.source_name = (name or "")[:200] or None
        row.decided_by = self.user
        self.s.flush()
        return row

    def clear(self, source_id: uuid.UUID, target_id: uuid.UUID, account_ref: str) -> bool:
        source, _, resolved, _ = self._pair(source_id, target_id)
        row = self._mappings(source, resolved.binding).get(account_ref)
        if row is None:
            return False
        self.s.delete(row)
        self.s.flush()
        return True

    def preview(
        self, source_id: uuid.UUID, target_id: uuid.UUID, company: str, period: AccountingPeriod
    ) -> AccountingApiPreview:
        """Aperçu SANS EFFET : lit les pièces de la source, les options de la cible (GET) et l'historique."""
        source, target, resolved, builder = self._pair(source_id, target_id)
        b = resolved.binding
        result = self.reader.read(source.id, company, period)
        partner_links = self.delivery.links(self._scope(source, target, b, PARTNER_DOMAIN))
        contacts = {ref: link.target_id for ref, link in partner_links.items()}
        mappings = self._mappings(source, b)
        choices = {ref: r.target_value for ref, r in mappings.items() if r.target_value}
        options: dict[str, TargetChoice] | None
        try:
            options = {o.id: o for o in self.options(source_id, target_id)}
        except AccountingApiTargetError:
            options = None
        prepared = prepare_documents(
            result.items, resolved, builder, contacts=contacts, choices=choices, options=options
        )
        scope = self._scope(source, target, b, DOMAIN).documents
        sendable = [p for p in prepared if p.content.units]
        classes = self.ledger.classify(scope, [p.content for p in sendable])
        unknown = self.delivery.open_unknown(self._scope(source, target, b, DOMAIN))
        labels = {f.technical_name: f.label or f.technical_name for f in resolved.fields}
        records = [
            PreviewApiDocument(
                outcome_unknown=p.source_ref in unknown,
                prepared=p,
                export_state=classes[p.source_ref].state
                if p.source_ref in classes
                else DocumentExportState.NEW,
                changed_fields=sorted(
                    {labels.get(c.field, c.field) for c in classes[p.source_ref].changes}
                    if p.source_ref in classes
                    else set()
                ),
            )
            for p in prepared
        ]
        return AccountingApiPreview(
            source_name=source.name,
            target_name=target.name,
            target_system=_system(target)[0].name,
            target=b.name,
            contract=resolved.snapshot_label,
            company=result.company.name,
            period=result.period,
            options_checked=options is not None,
            accounts=_accounts(result.items, mappings, options, b),
            records=records,
            truncated=result.truncated,
        )

    # ------------------------------------------------------------------ #
    # Envoi (phase 2b) : moteur générique `uie_core.delivery`, sélection et confirmation explicites
    # ------------------------------------------------------------------ #
    def send(
        self,
        source_id: uuid.UUID,
        target_id: uuid.UUID,
        company: str,
        period: AccountingPeriod,
        *,
        refs: list[str],
        request_key: str,
        confirm: bool,
    ) -> ApiSendResult:
        """Envoie les pièces CHOISIES de la période (jamais une pièce bloquée ni déjà envoyée inchangée).
        Confirmation explicite exigée ; même `request_key` = même envoi, jamais rejoué."""
        if not confirm:
            raise AccountingApiTargetError("Confirm the send first")
        source, target, resolved, _ = self._pair(source_id, target_id)
        b = resolved.binding
        scope = self._scope(source, target, b, DOMAIN)
        existing = self.delivery.find_run(scope, request_key)
        if existing is not None and existing.source_instance_id == source.id and existing.domain == DOMAIN:
            # rejeu ou double clic : le MÊME envoi, sans relire la source ni appeler la cible
            return ApiSendResult(run=existing, items=self.delivery.items(existing), numbers={})
        preview = self.preview(source_id, target_id, company, period)
        candidates = [
            DeliveryCandidate(
                source_ref=r.prepared.source_ref,
                blocked=r.prepared.status == RecordStatus.BLOCKED,
                request=r.prepared.request,
                content=r.prepared.content,
                skip_reason=r.skip_reason,
            )
            for r in preview.records
            if r.prepared.content.units
        ]
        run = self.delivery.send(
            scope,
            candidates,
            refs=refs,
            request_key=request_key,
            create_new=set(),
            target_label=f"{b.name} ({resolved.snapshot_label})",
            sender_factory=self._sender_factory(target),
        )
        numbers = {r.prepared.source_ref: r.prepared.number for r in preview.records}
        return ApiSendResult(run=run, items=self.delivery.items(run), numbers=numbers)

    def run(self, source_id: uuid.UUID, target_id: uuid.UUID, run_id: uuid.UUID) -> ApiSendResult:
        source, target, resolved, _ = self._pair(source_id, target_id)
        run = self.delivery.run(self._scope(source, target, resolved.binding, DOMAIN), run_id)
        return ApiSendResult(run=run, items=self.delivery.items(run), numbers={})

    def resolve_unknown(
        self,
        source_id: uuid.UUID,
        target_id: uuid.UUID,
        company: str,
        period: AccountingPeriod,
        item_id: uuid.UUID,
        *,
        created: bool,
    ) -> ApiSendResult:
        """Un humain a vérifié dans la cible une issue INCONNUE : « pas créée » → la pièce redevient
        envoyable ; « créée » → la cible est interrogée (lecture) et la pièce n'est liée que si UN SEUL
        enregistrement porte son identité (sinon refus : jamais deviné). Aucune écriture dans la cible."""
        source, target, resolved, _ = self._pair(source_id, target_id)
        scope = self._scope(source, target, resolved.binding, DOMAIN)
        factory = self._sender_factory(target)
        remote_id = None
        if created:
            item = self.s.get(TargetSendItem, item_id)
            if item is None:
                raise NotFound(f"Élément {item_id} introuvable")
            preview = self.preview(source_id, target_id, company, period)
            rec = next((r for r in preview.records if r.prepared.source_ref == item.source_ref), None)
            body = rec.prepared.request if rec else None
            if body is None:
                raise AccountingApiTargetError("This document can no longer be prepared: check it first")
            with factory() as sender:
                try:
                    found = sender.find_candidates(scope.resource, body)
                except SendError as exc:
                    raise AccountingApiTargetError(
                        f"The target system could not be searched ({exc.kind.value})"
                    ) from None
            if len(found) != 1:
                raise AccountingApiTargetError(
                    f"{len(found)} matching record(s) found in the target system: it cannot be confirmed"
                )
            remote_id = found[0].id
        item = self.delivery.resolve_unknown(
            scope, item_id, created=created, target_id=remote_id, sender_factory=factory
        )
        run = self.delivery.run(scope, item.run_id)
        return ApiSendResult(run=run, items=self.delivery.items(run), numbers={})

    def runs(self, source_id: uuid.UUID, target_id: uuid.UUID) -> list[TargetSendRun]:
        source, target, resolved, _ = self._pair(source_id, target_id)
        return self.delivery.runs(self._scope(source, target, resolved.binding, DOMAIN))


def _accounts(
    items: list[AccountingReadItem],
    mappings: dict[str, ExportValueMappingRecord],
    options: dict[str, TargetChoice] | None,
    b: TargetBinding,
) -> list[AccountNeed]:
    """Comptes de contrepartie des pièces lues (une ligne par compte), avec leur correspondance."""
    lookup = b.lookup(LookupKind.ACCOUNT)
    seen: dict[str, AccountNeed] = {}
    for read in items:
        doc = read.document
        if doc is None:
            continue
        role = doc.document_type.partner_role.value
        for bk in gross_bookings(doc).bookings:
            acc = bk.counter_account
            if acc is None or acc.source_ref in seen:
                continue
            row = mappings.get(acc.source_ref)
            choice = row.target_value if row else None
            option = (options or {}).get(choice or "")
            problem = None
            if lookup is not None and choice:
                found = _choice_issues(lookup, bk, choice, options, role, b.target.display_name)
                problem = found[0].message if found else None
            seen[acc.source_ref] = AccountNeed(
                account_ref=acc.source_ref,
                code=acc.code,
                name=acc.name,
                role=role,
                choice_id=choice,
                choice_label=option.label if option else None,
                problem=problem,
            )
    return sorted(seen.values(), key=lambda a: a.code)
