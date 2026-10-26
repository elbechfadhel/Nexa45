"""Préparation d'un format cible pour les pièces comptables (ACCOUNTING → binding cible) : APERÇU validé, sans
fichier.

    AccountingReadService (Accounting CDM, une société, une période)
        ──► écritures dérivées (modèle générique du binding, `uie_core.accounting.bookings`)
        ──► comptes du tiers (numéros attribués par l'export d'un autre format, lecture seule)
        ──► correspondances de valeurs (compte, taxe) : décisions confirmées + suggestions explicables
        ──► enregistrements validés par le Registry de la version choisie (READY / WARNING / BLOCKED)

Générique : le format, ses positions, ses correspondances et ses réglages viennent du binding (domain pack) et
du Registry ; aucun nom de système ni de colonne ici. Le CDM n'est jamais modifié. Une suggestion n'est jamais
appliquée : seule une décision confirmée (persistée) est utilisée. Rien n'est écrit dans le système source,
aucun fichier n'est produit, rien n'est envoyé.
"""

from __future__ import annotations

import calendar
import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from uie_cdm import (
    AccountingDocument,
    DocumentStatus,
    LedgerAccountRef,
    LineKind,
    PartnerRole,
    PostingCategoryRef,
    TaxRef,
)

from uie_core.accounting.bookings import Booking, Side, gross_bookings
from uie_core.accounting.duplicates import CrossSourceGuard
from uie_core.accounting.service import AccountingPeriod, AccountingReadResult, AccountingReadService
from uie_core.db.base import utcnow
from uie_core.exports.documents import (
    DocumentClassification,
    DocumentContent,
    DocumentExportLedger,
    DocumentExportState,
    DocumentScope,
    ExportHistoryError,
    RecordOrigin,
)
from uie_core.exports.records import (
    ExportAccountAssignmentRecord,
    ExportProfileRecord,
    ExportRunRecord,
    ExportValueMappingRecord,
)
from uie_core.exports.service import (
    DEFAULT_CLIENT,
    ExportProfileService,
    GeneratedFile,
    ProfileInvalid,
    validate_profile,
)
from uie_core.identity.context import AdminContext, Context, TenantContext, ensure_context
from uie_core.mapping import (
    BookingModel,
    IssueLevel,
    LookupKind,
    LookupSpec,
    RecordFileWriter,
    RecordFileWriterRegistry,
    RecordStatus,
    ResolvedBinding,
    TargetBinding,
    TargetBindingError,
    TargetBindingRegistry,
    TargetIssue,
    check_value,
    header_values,
    map_record,
    resolve_binding,
)
from uie_core.mapping.suggestions import CategoryAccountRegistry
from uie_core.registry import SnapshotRef
from uie_core.registry.service import NotFound, RegistryError, RegistryService

SOURCE_MODEL = "accounting"


class AccountingTargetError(RegistryError):
    """Préparation impossible : version non prise en charge, correspondance inconnue (→ 400)."""


class AccountingTargetNotReady(AccountingTargetError):
    """Réglages du dossier cible absents ou incomplets (→ 409)."""


class MappingInvalid(AccountingTargetError):
    """Valeur cible refusée par le Registry ou les réglages (→ 422)."""


class ExportInvalid(AccountingTargetError):
    """Le fichier ne peut pas être produit : une valeur prête est refusée à la sérialisation, l'en-tête est
    invalide, ou aucune pièce n'est exportable (→ 422). Rien n'est omis en silence."""


class ExportChanged(AccountingTargetError):
    """Les données ont changé depuis l'aperçu du fichier (→ 409) : refaire l'aperçu."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------- #
# Modèles de l'aperçu
# --------------------------------------------------------------------------- #
class MappingStatus(StrEnum):
    CONFIRMED = "CONFIRMED"  # décision persistée, valide pour les réglages actuels
    SUGGESTED = "SUGGESTED"  # proposition déterministe, jamais appliquée sans confirmation
    MISSING = "MISSING"  # ni décision ni suggestion
    INVALID = "INVALID"  # décision persistée devenue invalide (réglages, Registry)


class MappingOrigin(StrEnum):
    MANUAL = "MANUAL"  # valeur saisie
    SUGGESTION_ACCEPTED = "SUGGESTION_ACCEPTED"  # suggestion confirmée
    IDENTICAL_CODE = "IDENTICAL_CODE"  # suggestion : même numéro que le compte source


class ValueMapping(_Model):
    kind: LookupKind
    source_ref: str  # identifiant opaque de la source
    source_code: str  # code du compte / nom de la taxe dans la source
    source_name: str | None
    status: MappingStatus
    target_value: str | None = None  # décision confirmée (None + no_value : « aucune valeur »)
    no_value: bool = False
    suggestion: str | None = None
    origin: MappingOrigin | None = None
    reason: str  # phrase simple (anglais) : pourquoi ce statut
    documents: int = 0  # pièces de la période concernées

    @property
    def key(self) -> str:
        return f"{self.kind.value}:{self.source_ref}"


class SourceLine(_Model):
    position: int  # rang de la ligne dans la pièce (0..n-1)
    kind: LineKind
    description: str | None
    account_code: str
    amount: Decimal  # signé (CDM)
    gross_amount: Decimal | None


class TargetCell(_Model):
    field: str  # position du format
    label: str | None  # libellé du Registry
    value: str  # valeur telle que l'écrivain du format l'écrirait


class PreparedBooking(_Model):
    index: int
    amount: Decimal  # positif
    partner_side: Side
    net_amount: Decimal
    gross_amount: Decimal
    counter_account: LedgerAccountRef | None  # None : écriture d'une catégorie (source sans comptes)
    tax: TaxRef | None
    partner_account: str | None
    counter_target: str | None
    tax_target: str | None
    tax_no_value: bool  # taxe décidée « aucune valeur »
    source_lines: list[SourceLine]
    counter_category: PostingCategoryRef | None = None  # catégorie de la source (CDM 1.1)
    tax_rate: Decimal | None = None  # taux de la source quand elle n'a pas de taxes
    values: dict[str, Any]  # position → valeur validée (avant sérialisation) : entrée du fichier
    cells: list[TargetCell]
    status: RecordStatus
    issues: list[TargetIssue]


class PreparedDocument(_Model):
    source_ref: str
    number: str | None
    reference: str | None
    document_type: str | None
    source_status: str | None
    eligible: bool  # éligibilité SOURCE (phase 3), inchangée
    target_status: RecordStatus | None  # None : non préparée (brouillon, annulée)
    not_prepared_reason: str | None = None
    partner_name: str | None = None
    partner_role: str | None = None
    accounting_date: date | None = None
    document_date: date | None = None
    currency: str | None = None
    total_amount: Decimal | None = None
    balance: Decimal | None = None
    issues: list[TargetIssue] = []
    bookings: list[PreparedBooking] = []


class SettingValue(_Model):
    name: str
    label: str
    value: Any
    inherited: bool  # lu dans le profil du format partagé (lecture seule ici)


class TargetPreparation(_Model):
    target: str  # libellé du format
    system: str
    product: str
    format_version: str
    snapshot_label: str
    binding_version: str
    period: str
    company_name: str | None
    settings: list[SettingValue]
    mappings: list[ValueMapping]
    documents: list[PreparedDocument]
    truncated: bool

    def count(self, status: RecordStatus) -> int:
        return sum(1 for d in self.documents if d.target_status == status)

    @property
    def eligible(self) -> int:
        return sum(1 for d in self.documents if d.eligible)

    @property
    def not_prepared(self) -> int:
        return sum(1 for d in self.documents if d.target_status is None)


class TargetOption(_Model):
    system: str
    product: str
    name: str
    versions: list[str]  # versions du binding présentes dans le Registry (la plus récente d'abord)


class TargetSettings(_Model):
    system: str
    product: str
    shared_with: str | None  # format dont les réglages partagés sont lus
    values: list[SettingValue]
    params: list[dict[str, Any]]  # paramètres propres (nom, libellé, aide, format, obligatoire)
    missing: list[str]  # libellés des réglages manquants (partagés ou propres)
    missing_for_file: list[str] = []  # réglages exigés seulement pour créer le fichier

    @property
    def ready(self) -> bool:
        return not self.missing


class ExportRow(_Model):
    """Une ligne du fichier = une écriture préparée d'une pièce exportée (provenance conservée)."""

    row: int  # rang dans le fichier (1..n)
    document_ref: str  # référence opaque de la pièce source
    document_number: str | None
    partner_name: str | None
    booking_index: int
    status: RecordStatus  # statut de la pièce : READY ou WARNING (jamais BLOCKED)
    export_state: DocumentExportState = DocumentExportState.NEW  # historique d'export de la pièce
    source_lines: list[int]  # positions des lignes de la pièce à l'origine de l'écriture
    cells: list[TargetCell]


class ExportFieldChange(_Model):
    booking: int  # rang de l'écriture dans la pièce
    field: str  # position du format
    label: str | None  # libellé du Registry
    change: str  # CHANGED | ADDED | REMOVED


class ExportDocument(_Model):
    """Pièce exportable selon son statut cible, avec son état d'historique d'export (deux axes distincts)."""

    document_ref: str
    number: str | None
    partner_name: str | None
    target_status: RecordStatus  # READY | WARNING (phase 4)
    state: DocumentExportState  # NEW | ALREADY_EXPORTED | CHANGED_SINCE_EXPORT
    included: bool  # écrite dans CE fichier
    released: bool = False  # modifiée, nouvel export autorisé explicitement
    duplicate: bool = False  # déjà exportée, réécrite sur demande explicite
    bookings: int = 0
    previous_exported_at: datetime | None = None
    previous_origin: str | None = None
    previous_bookings: int | None = None
    changes: list[ExportFieldChange] = []
    legacy_exported_at: datetime | None = None  # présente dans un fichier généré avant le suivi
    legacy_run_id: uuid.UUID | None = None


class ExcludedDocument(_Model):
    document_ref: str
    number: str | None
    reference: str | None
    document_type: str | None
    source_status: str | None
    target_status: RecordStatus | None  # None : non préparée
    reasons: list[str]
    # exclue par l'historique d'export (déjà exportée, modifiée non autorisée) ; None : exclue par son statut
    export_state: DocumentExportState | None = None


class ExportFilePlan(_Model):
    """Ce que le fichier contient EXACTEMENT : mêmes lignes et même en-tête que le téléchargement (seuls
    l'horodatage de création de l'en-tête et le nom du fichier changent d'un appel à l'autre)."""

    target: str
    system: str
    product: str
    format_version: str
    snapshot_label: str
    binding_version: str
    period: str
    company_name: str | None
    encoding: str
    source_documents: int
    # période écrite dans l'en-tête, en clair (« 08/2026 to 09/2026 ») : mois des pièces ÉCRITES
    exported_documents: int
    excluded_documents: int
    warning_documents: int  # exportées avec avertissement
    blocked_documents: int
    not_prepared_documents: int
    row_count: int
    column_count: int
    header: list[TargetCell]  # valeurs d'en-tête renseignées, telles qu'écrites
    volatile_header_fields: list[str]  # positions d'en-tête qui changent à chaque génération
    rows: list[ExportRow]
    excluded: list[ExcludedDocument]
    content_digest: str  # empreinte du contenu hors métadonnées volatiles
    size: int
    previous_exports: int  # fichiers déjà générés pour ce système et cette période
    last_export_at: datetime | None
    # historique d'export par pièce (idempotence) : état de chaque pièce exportable et compteurs
    documents: list[ExportDocument] = []
    period_text: str = ""
    new_documents: int = 0
    already_exported_documents: int = 0
    changed_documents: int = 0
    released_documents: int = 0  # modifiées dont le nouvel export est autorisé (écrites)
    duplicate_documents: int = 0  # déjà exportées, réécrites sur demande explicite
    include_exported: bool = False


# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class _Target:
    binding: TargetBinding
    resolved: ResolvedBinding
    writer: RecordFileWriter


def _name(target: _Target) -> str:
    """Nom du système cible dans les messages : donnée du binding, jamais écrit dans le Core."""
    return target.binding.target.display_name


def _one_year(start: date) -> date:
    try:
        return start.replace(year=start.year + 1) - timedelta(days=1)
    except ValueError:  # 29 février
        return start.replace(year=start.year + 1, day=28)


class AccountingTargetService:
    def __init__(self, session: Session, ctx: Context, reader: AccountingReadService) -> None:
        self.s = session
        self.ctx = ensure_context(ctx)
        if reader.ctx != self.ctx:
            raise ValueError("Les services de la préparation doivent partager le même contexte")
        self.reader = reader
        self.registry = RegistryService(session, self.ctx)
        self.profiles = ExportProfileService(session, self.ctx)
        self.ledger = DocumentExportLedger(session)

    # ------------------------------------------------------------------ #
    # Formats et réglages
    # ------------------------------------------------------------------ #
    def targets(self) -> list[TargetOption]:
        out = []
        for b in TargetBindingRegistry.for_model(SOURCE_MODEL):
            t = b.target
            present = []
            for v in sorted(t.versions, key=lambda v: (int(v), v) if v.isdigit() else (0, v), reverse=True):
                ref = SnapshotRef(system=t.system, product=t.product, version=v, interface=t.interface)
                try:
                    self.registry.resolve_snapshot(ref)
                except NotFound:
                    continue
                present.append(v)
            out.append(TargetOption(system=t.system, product=t.product, name=b.name, versions=present))
        return out

    def _target(self, system: str, product: str, version: str | None = None) -> _Target:
        try:
            binding = TargetBindingRegistry.find(system, product)
        except TargetBindingError as exc:
            raise NotFound(str(exc)) from None
        if binding.source.model != SOURCE_MODEL:
            raise NotFound(f"{system}/{product} n'est pas une cible comptable")
        if version is not None and version not in binding.target.versions:
            raise AccountingTargetError(
                f"Version {version} of {binding.name} is not supported (supported: "
                f"{', '.join(binding.target.versions)})"
            )
        try:
            resolved = resolve_binding(self.registry, binding, version)
            writer = RecordFileWriterRegistry.get(binding.target.system, binding.target.interface)
        except TargetBindingError as exc:
            raise NotFound(str(exc)) from None
        return _Target(binding, resolved, writer)

    def _profiles(
        self, target: _Target, organization_id: uuid.UUID, client_key: str = DEFAULT_CLIENT
    ) -> tuple[ExportProfileRecord | None, ExportProfileRecord | None]:
        b = target.binding
        own = self.profiles.get(b.target.system, b.target.product, organization_id, client_key)
        shared = None
        if b.inherit_profile:
            t = b.inherit_profile.target
            shared = self.profiles.get(t.system, t.product, organization_id, client_key)
        return own, shared

    def settings(self, instance_id: uuid.UUID, system: str, product: str) -> TargetSettings:
        inst = self.reader.instances.get(instance_id)  # contrôle d'appartenance
        target = self._target(system, product)
        return self._settings(target, *self._profiles(target, inst.organization_id))

    def _settings(
        self, target: _Target, own: ExportProfileRecord | None, shared: ExportProfileRecord | None
    ) -> TargetSettings:
        b = target.binding
        values: list[SettingValue] = []
        missing: list[str] = []
        missing_for_file: list[str] = []
        shared_with = None
        if b.inherit_profile:
            t = b.inherit_profile.target
            shared_binding = TargetBindingRegistry.find(t.system, t.product)
            shared_with = shared_binding.name
            labels = {p.name: p.label for p in shared_binding.profile}
            for name in b.inherit_profile.params:
                value = (shared.values if shared else {}).get(name)
                values.append(
                    SettingValue(name=name, label=labels.get(name, name), value=value, inherited=True)
                )
                required = next((p.required for p in shared_binding.profile if p.name == name), False)
                if required and value in (None, ""):
                    missing.append(labels.get(name, name))
        params = []
        for p in b.profile:
            value = (own.values if own else {}).get(p.name, p.default)
            values.append(SettingValue(name=p.name, label=p.label, value=value, inherited=False))
            params.append(
                {
                    "name": p.name,
                    "label": p.label,
                    "help": p.help,
                    "format_help": p.format_help,
                    "required": p.required,
                    "required_for_file": p.required_for_file,
                    "choices": list(p.choices),
                }
            )
            if p.required and value in (None, ""):
                missing.append(p.label)
            elif p.required_for_file and value in (None, ""):
                missing_for_file.append(p.label)
        if own is not None:
            try:
                validate_profile(target.resolved, target.writer, own.values)
            except ProfileInvalid as exc:
                missing.extend(exc.errors.values())
        return TargetSettings(
            system=b.target.system,
            product=b.target.product,
            shared_with=shared_with,
            values=values,
            params=params,
            missing=missing,
            missing_for_file=missing_for_file,
        )

    def save_settings(
        self, instance_id: uuid.UUID, system: str, product: str, values: dict[str, Any]
    ) -> TargetSettings:
        inst = self.reader.instances.get(instance_id)
        target = self._target(system, product)
        own, _ = self._profiles(target, inst.organization_id)
        merged = dict(own.values if own else {}) | values  # un réglage saisi ne retire pas les autres
        self.profiles.save(system, product, merged, inst.organization_id)
        return self._settings(target, *self._profiles(target, inst.organization_id))

    # ------------------------------------------------------------------ #
    # Correspondances (décisions explicites)
    # ------------------------------------------------------------------ #
    def decide(
        self,
        instance_id: uuid.UUID,
        system: str,
        product: str,
        kind: LookupKind,
        source_ref: str,
        *,
        value: str | None,
        no_value: bool = False,
        accepted_suggestion: MappingOrigin | None = None,
        source_code: str | None = None,
        source_name: str | None = None,
    ) -> ExportValueMappingRecord:
        """Confirme la valeur cible d'un compte / d'une taxe source (ou « aucune valeur » si le format
        l'admet). La valeur est contrôlée par le Registry (motif, longueur) et les réglages (longueur
        exacte)."""
        inst = self.reader.instances.get(instance_id)
        target = self._target(system, product)
        lookup = target.binding.lookup(kind)
        if lookup is None:
            raise AccountingTargetError(f"{target.binding.name} has no {kind.value.lower()} mapping")
        own, shared = self._profiles(target, inst.organization_id)
        if no_value:
            if not lookup.allow_none:
                raise MappingInvalid(f"{lookup.label}: a value is required")
            clean = None
        else:
            clean = (value or "").strip()
            if reason := self._invalid(target, lookup, clean, shared):
                raise MappingInvalid(f"{lookup.label} {clean or '(empty)'}: {reason}")
        b = target.binding.target
        row = self.s.scalar(
            select(ExportValueMappingRecord).where(
                ExportValueMappingRecord.organization_id == inst.organization_id,
                ExportValueMappingRecord.target_system == b.system,
                ExportValueMappingRecord.target_product == b.product,
                ExportValueMappingRecord.instance_id == inst.id,
                ExportValueMappingRecord.kind == kind.value,
                ExportValueMappingRecord.source_ref == source_ref,
            )
        )
        if row is None:
            row = ExportValueMappingRecord(
                organization_id=inst.organization_id,
                target_system=b.system,
                target_product=b.product,
                instance_id=inst.id,
                kind=kind.value,
                source_ref=source_ref,
            )
            self.s.add(row)
        row.target_value = clean
        row.origin = (
            MappingOrigin.SUGGESTION_ACCEPTED.value if accepted_suggestion else MappingOrigin.MANUAL.value
        )
        row.suggestion_origin = accepted_suggestion.value if accepted_suggestion else None
        row.source_code = (source_code or "")[:120] or None  # libellé affiché seulement
        row.source_name = (source_name or "")[:200] or None
        row.decided_by = self.ctx.user_id if isinstance(self.ctx, TenantContext | AdminContext) else None
        row.updated_at = utcnow()
        self.s.flush()
        return row

    def clear(
        self, instance_id: uuid.UUID, system: str, product: str, kind: LookupKind, source_ref: str
    ) -> bool:
        inst = self.reader.instances.get(instance_id)
        b = self._target(system, product).binding.target
        row = self.s.scalar(
            select(ExportValueMappingRecord).where(
                ExportValueMappingRecord.organization_id == inst.organization_id,
                ExportValueMappingRecord.target_system == b.system,
                ExportValueMappingRecord.target_product == b.product,
                ExportValueMappingRecord.instance_id == inst.id,
                ExportValueMappingRecord.kind == kind.value,
                ExportValueMappingRecord.source_ref == source_ref,
            )
        )
        if row is None:
            return False
        self.s.delete(row)
        self.s.flush()
        return True

    def _invalid(
        self, target: _Target, lookup: LookupSpec, value: str, shared: ExportProfileRecord | None
    ) -> str | None:
        if not value:
            return "is empty"
        fd = target.resolved.field(lookup.field)
        if lookup.length_from:
            length = (shared.values if shared else {}).get(lookup.length_from)
            if length is None:
                return f"cannot be checked: the account length of the {_name(target)} client is not set"
            if len(value) != int(length):
                return f"must have {int(length)} digits"
        return check_value(fd, value, target.writer, target.binding.encodings[0])

    # ------------------------------------------------------------------ #
    # Aperçu
    # ------------------------------------------------------------------ #
    def preview(
        self,
        instance_id: uuid.UUID,
        company: str,
        period: AccountingPeriod,
        system: str,
        product: str,
        version: str | None = None,
    ) -> TargetPreparation:
        inst = self.reader.instances.get(instance_id)  # contrôle d'appartenance AVANT toute lecture
        target = self._target(system, product, version)
        own, shared = self._profiles(target, inst.organization_id)
        settings = self._settings(target, own, shared)
        if not settings.ready:
            raise AccountingTargetNotReady(
                f"{_name(target)} settings are incomplete: " + "; ".join(settings.missing)
            )
        result = self.reader.read(inst.id, company, period)
        prep = _prepare(self.s, target, inst.id, inst.organization_id, own, shared, settings, result)
        return self._cross_source(inst, target, period, prep, result)

    # ------------------------------------------------------------------ #
    # Fichier (phase 5) : sérialisation des écritures DÉJÀ préparées, aperçu = téléchargement
    # ------------------------------------------------------------------ #
    def file_preview(
        self,
        instance_id: uuid.UUID,
        company: str,
        period: AccountingPeriod,
        system: str,
        product: str,
        version: str | None = None,
        *,
        include_exported: bool = False,
    ) -> ExportFilePlan:
        """Contenu exact du fichier (lecture seule : rien n'est enregistré, aucun numéro attribué, aucune
        pièce marquée exportée). `include_exported` : montre ce que donnerait un doublon volontaire."""
        return self._file(
            instance_id, company, period, system, product, version, utcnow(), include_exported
        ).plan

    def generate(
        self,
        instance_id: uuid.UUID,
        company: str,
        period: AccountingPeriod,
        system: str,
        product: str,
        version: str | None = None,
        *,
        expected_digest: str | None = None,
        include_exported: bool = False,
        confirm_duplicates: bool = False,
    ) -> GeneratedFile:
        """Produit le fichier et journalise la génération ET chaque pièce écrite, dans la même transaction.
        `expected_digest` (empreinte de l'aperçu) : si le contenu a changé depuis, le fichier n'est PAS
        produit. Par défaut seules les pièces NOUVELLES (ou modifiées et autorisées) sont écrites ; réécrire
        des pièces déjà exportées exige `include_exported` ET `confirm_duplicates` (doublon volontaire)."""
        if include_exported and not confirm_duplicates:
            raise AccountingTargetError(
                "Exporting already exported documents again creates duplicate bookings: confirm it explicitly"
            )
        now = utcnow()
        f = self._file(instance_id, company, period, system, product, version, now, include_exported)
        plan = f.plan
        if plan.exported_documents == 0:
            raise ExportInvalid(
                "No new document to export for this period: every exportable document was already exported"
                if plan.already_exported_documents or plan.changed_documents
                else "No document can be exported for this period"
            )
        if expected_digest and expected_digest != plan.content_digest:
            raise ExportChanged("The data changed since the file preview: preview the file again")
        user = self.ctx.user_id if isinstance(self.ctx, TenantContext | AdminContext) else None
        run = ExportRunRecord(
            organization_id=f.scope.organization_id,
            profile_id=f.profile.id,
            instance_id=f.scope.instance_id,
            created_by=user,
            target_label=plan.target,
            target_snapshot_id=f.snapshot_id,
            binding_version=plan.binding_version,
            read_plan_checksum=f.plan_checksum,
            encoding=plan.encoding,
            records=plan.row_count,
            warnings=plan.warning_documents,
            blocked=plan.blocked_documents,
            new_accounts=0,
            file_sha256=hashlib.sha256(f.content).hexdigest(),
            file_size=len(f.content),
            period=plan.period,
            details={
                "company_ref": f.company_ref,
                "company": plan.company_name,
                "format_version": plan.format_version,
                "source_documents": plan.source_documents,
                "exported_documents": plan.exported_documents,
                "excluded_documents": plan.excluded_documents,
                "not_prepared_documents": plan.not_prepared_documents,
                "rows": plan.row_count,
                "content_digest": plan.content_digest,
                # historique d'export par pièce : ce que ce fichier a écrit, sauté ou laissé bloqué
                "new_documents": sum(
                    1 for d in plan.documents if d.included and not d.released and not d.duplicate
                ),
                "already_exported_skipped": plan.already_exported_documents - plan.duplicate_documents,
                "changed_blocked": plan.changed_documents - plan.released_documents,
                "released_documents": plan.released_documents,
                "duplicate_documents": plan.duplicate_documents,
                "duplicate_override": bool(plan.duplicate_documents),
                # provenance légère : pièce → rangs de ses lignes dans le fichier (aucun montant)
                "documents": _row_index(plan.rows),
            },
        )
        self.s.add(run)
        self.s.flush()
        written = []
        for d in plan.documents:
            if not d.included:
                continue
            c = f.classes[d.document_ref]
            origin = (
                RecordOrigin.RELEASED
                if d.released
                else RecordOrigin.RE_EXPORTED
                if d.duplicate
                else RecordOrigin.EXPORTED
            )
            written.append((f.contents[d.document_ref], origin, c.previous_record_id))
        # même transaction que le journal : pas de fichier « exporté » sans ses pièces, ni l'inverse
        self.ledger.record(f.scope, run, written, scope_ref=f.company_ref, decided_by=user)
        b = self._target(system, product, version).binding.target
        slug = re.sub(r"[^A-Za-z0-9]+", "-", plan.company_name or "").strip("-")
        name = "_".join(
            x for x in (b.system.upper(), b.product, slug, plan.period, now.strftime("%Y%m%d_%H%M%S")) if x
        )
        writer = RecordFileWriterRegistry.get(b.system, b.interface)
        return GeneratedFile(
            run=run, content=f.content, filename=f"{name}.{writer.extension}", media_type=writer.media_type
        )

    # ------------------------------------------------------------------ #
    # Source FICHIER (pièces construites depuis le fichier d'un client, aucune instance)
    # ------------------------------------------------------------------ #
    def file_preparation(  # noqa: PLR0913
        self,
        system: str,
        product: str,
        result: AccountingReadResult,
        values: dict[str, Any],
        shared_values: dict[str, Any],
        decided: dict[tuple[str, str], Any],
        organization_id: uuid.UUID,
        version: str | None = None,
        client_key: str = DEFAULT_CLIENT,
    ) -> tuple[TargetPreparation, TargetSettings]:
        """Préparation de pièces venues d'un FICHIER : réglages des profils de l'organisation complétés par
        les réponses en cours (`values` : ce format ; `shared_values` : format partagé), décisions de
        correspondance passées par l'appelant. Rien n'est enregistré."""
        target = self._target(system, product, version)
        own, shared = self._transient(target, organization_id, values, shared_values, client_key)
        settings = self._settings(target, own, shared)  # type: ignore[arg-type]
        prep = _prepare(
            self.s,
            target,
            None,
            organization_id,
            own,
            shared,
            settings,
            result,
            decided,  # type: ignore[arg-type]
        )
        return prep, settings

    def file_export(  # noqa: PLR0913
        self,
        system: str,
        product: str,
        result: AccountingReadResult,
        values: dict[str, Any],
        shared_values: dict[str, Any],
        decided: dict[tuple[str, str], Any],
        organization_id: uuid.UUID,
        span: PeriodSpan,
        *,
        version: str | None = None,
        generate: bool = False,
        client_key: str = DEFAULT_CLIENT,
    ) -> tuple[TargetPreparation, ExportFilePlan, bytes, GeneratedFile | None]:
        """Fichier de pièces venues d'un FICHIER. Historique par pièce (D11) dans la portée « source
        fichier » de l'organisation : une pièce déjà exportée n'est jamais réécrite. `generate` : réglages
        enregistrés dans les profils, génération journalisée avec chaque pièce écrite (même transaction)."""
        target = self._target(system, product, version)
        if generate:
            b = target.binding
            try:
                if b.inherit_profile and shared_values:
                    t = b.inherit_profile.target
                    old = self.profiles.get(t.system, t.product, organization_id, client_key)
                    merged = dict(old.values if old else {}) | shared_values
                    self.profiles.save(t.system, t.product, merged, organization_id, client_key)
                old = self.profiles.get(b.target.system, b.target.product, organization_id, client_key)
                self.profiles.save(
                    b.target.system,
                    b.target.product,
                    dict(old.values if old else {}) | values,
                    organization_id,
                    client_key,
                )
            except ProfileInvalid as exc:
                raise AccountingTargetNotReady(
                    f"{_name(target)} settings are incomplete: " + "; ".join(exc.errors.values())
                ) from None
            own, shared = self._profiles(target, organization_id, client_key)
        else:
            own, shared = self._transient(target, organization_id, values, shared_values, client_key)  # type: ignore[assignment]
        settings = self._settings(target, own, shared)
        if not settings.ready:
            raise AccountingTargetNotReady(
                f"{_name(target)} settings are incomplete: " + "; ".join(settings.missing)
            )
        if generate and settings.missing_for_file:
            raise AccountingTargetNotReady(
                "Required to create the file: " + "; ".join(settings.missing_for_file)
            )
        prep = _prepare(self.s, target, None, organization_id, own, shared, settings, result, decided)
        t = target.binding.target
        scope = DocumentScope(
            organization_id=organization_id,
            instance_id=None,
            domain=SOURCE_MODEL,
            target_system=t.system,
            target_product=t.product,
            client_key=client_key,  # un mandant ne voit jamais l'historique d'un autre
        )
        contents = {
            d.source_ref: _content(target, d) for d in prep.documents if d.target_status in EXPORTABLE
        }
        classes = self.ledger.classify(scope, list(contents.values()), {})
        now = utcnow()
        plan, content = _serialize(target, prep, settings, span, now, classes=classes)  # type: ignore[arg-type]
        # période de l'en-tête (Datum von / bis) = mois des pièces réellement ÉCRITES : un mois dont toutes
        # les pièces sont bloquées ou hors de l'exercice n'y figure pas
        written = {d.document_ref for d in plan.documents if d.included}
        days = [d.accounting_date for d in prep.documents if d.source_ref in written and d.accounting_date]
        narrowed = PeriodSpan.covering(days) if days else span
        if (narrowed.first_day, narrowed.last_day) != (span.first_day, span.last_day):
            span = narrowed
            plan, content = _serialize(target, prep, settings, span, now, classes=classes)  # type: ignore[arg-type]
        if not generate:
            return prep, plan, content, None
        if plan.exported_documents == 0:
            raise ExportInvalid(
                "No new document to export: every exportable document was already exported"
                if plan.already_exported_documents or plan.changed_documents
                else "No document can be exported"
            )
        user = self.ctx.user_id if isinstance(self.ctx, TenantContext | AdminContext) else None
        assert own is not None
        run = ExportRunRecord(
            organization_id=organization_id,
            profile_id=own.id,
            instance_id=None,
            created_by=user,
            target_label=plan.target,
            target_snapshot_id=target.resolved.snapshot_id,
            binding_version=plan.binding_version,
            read_plan_checksum=result.plan_checksum,
            encoding=plan.encoding,
            records=plan.row_count,
            warnings=plan.warning_documents,
            blocked=plan.blocked_documents,
            new_accounts=0,
            file_sha256=hashlib.sha256(content).hexdigest(),
            file_size=len(content),
            period=str(span),
            details={
                "source": "file",
                "period_from": span.first_day.isoformat(),
                "period_to": span.last_day.isoformat(),
                "format_version": plan.format_version,
                "source_documents": plan.source_documents,
                "exported_documents": plan.exported_documents,
                "already_exported_skipped": plan.already_exported_documents,
                "changed_blocked": plan.changed_documents,
                "rows": plan.row_count,
                "content_digest": plan.content_digest,
                "documents": _row_index(plan.rows),
            },
        )
        self.s.add(run)
        self.s.flush()
        written = [
            (contents[d.document_ref], RecordOrigin.EXPORTED, classes[d.document_ref].previous_record_id)
            for d in plan.documents
            if d.included
        ]
        self.ledger.record(scope, run, written, scope_ref=FILE_SCOPE, decided_by=user)
        writer = RecordFileWriterRegistry.get(t.system, t.interface)
        name = "_".join((t.system.upper(), t.product, span.slug, now.strftime("%Y%m%d_%H%M%S")))
        return (
            prep,
            plan,
            content,
            GeneratedFile(
                run=run, content=content, filename=f"{name}.{writer.extension}", media_type=writer.media_type
            ),
        )

    def _transient(
        self,
        target: _Target,
        organization_id: uuid.UUID,
        values: dict[str, Any],
        shared_values: dict[str, Any],
        client_key: str = DEFAULT_CLIENT,
    ) -> tuple[SimpleNamespace, SimpleNamespace | None]:
        """Profils du dossier (mandant) complétés par les réponses en cours, SANS les enregistrer."""
        own, shared = self._profiles(target, organization_id, client_key)
        t = SimpleNamespace(id=own.id if own else None, values=dict(own.values if own else {}) | values)
        if target.binding.inherit_profile is None:
            return t, None
        s = SimpleNamespace(
            id=shared.id if shared else None, values=dict(shared.values if shared else {}) | shared_values
        )
        return t, s

    def release_document(
        self,
        instance_id: uuid.UUID,
        company: str,
        period: AccountingPeriod,
        system: str,
        product: str,
        source_ref: str,
        *,
        version: str | None = None,
        note: str | None = None,
        revoke: bool = False,
    ) -> bool:
        """Décision humaine explicite : autorise (ou retire l'autorisation de) le nouvel export d'une pièce
        MODIFIÉE depuis son export, pour son contenu actuel seulement. L'historique n'est jamais modifié."""
        f = self._file(instance_id, company, period, system, product, version, utcnow(), False)
        content = f.contents.get(source_ref)
        if content is None:
            raise NotFound("Document not found among the exportable documents of this period")
        if revoke:
            return self.ledger.revoke(f.scope, content)
        user = self.ctx.user_id if isinstance(self.ctx, TenantContext | AdminContext) else None
        try:
            self.ledger.release(f.scope, content, decided_by=user, note=note)
        except ExportHistoryError as exc:
            raise AccountingTargetError(str(exc)) from None
        return True

    def adopt_legacy_run(self, instance_id: uuid.UUID, system: str, product: str, run_id: uuid.UUID) -> int:
        """Adopte comme « déjà exportées » les pièces d'un fichier généré AVANT le suivi par pièce — seulement
        si l'empreinte de contenu de ce fichier prouve que les pièces sont identiques aujourd'hui (mêmes
        pièces, mêmes lignes, mêmes réglages). Sinon refus : aucune empreinte n'est fabriquée."""
        inst = self.reader.instances.get(instance_id)
        target = self._target(system, product)
        own, shared = self._profiles(target, inst.organization_id)
        run = self.s.get(ExportRunRecord, run_id)
        if run is None or own is None or run.profile_id != own.id or run.instance_id != inst.id:
            raise NotFound("Generated file not found")
        d = run.details or {}
        refs = [x.get("ref") for x in d.get("documents") or []]
        if not (run.period and d.get("company_ref") and d.get("content_digest") and refs and all(refs)):
            raise AccountingTargetError("This file has no document list: it cannot be adopted")
        if self.ledger.recorded_runs([run.id]):
            raise AccountingTargetError("This file is already tracked document by document")
        settings = self._settings(target, own, shared)
        if not settings.ready or settings.missing_for_file:
            raise AccountingTargetNotReady(f"{_name(target)} settings are incomplete")
        period = AccountingPeriod.parse(run.period)
        result = self.reader.read(inst.id, d["company_ref"], period)
        prep = _prepare(self.s, target, inst.id, inst.organization_id, own, shared, settings, result)
        candidates = {x.source_ref: x for x in prep.documents if x.target_status in EXPORTABLE}
        mismatch = "The current documents are not identical to that file: it cannot be adopted"
        if not set(refs) <= set(candidates):
            raise AccountingTargetError(mismatch)
        plan, _ = _serialize(target, prep, settings, period, utcnow(), only_refs=set(refs))
        if plan.content_digest != d["content_digest"]:
            raise AccountingTargetError(mismatch)
        scope = self._scope(inst, target)
        user = self.ctx.user_id if isinstance(self.ctx, TenantContext | AdminContext) else None
        latest = self.ledger.latest(scope, refs)
        todo = [
            (_content(target, candidates[r]), RecordOrigin.ADOPTED, None) for r in refs if r not in latest
        ]
        self.ledger.record(scope, run, todo, scope_ref=d["company_ref"], decided_by=user)
        return len(todo)

    def runs(self, instance_id: uuid.UUID, system: str, product: str) -> list[tuple[ExportRunRecord, bool]]:
        """Fichiers générés pour ce système et ce format (30 derniers), sans leur contenu. Le booléen dit si
        le fichier est suivi pièce par pièce (False : généré avant le suivi)."""
        inst = self.reader.instances.get(instance_id)
        target = self._target(system, product)
        own, _ = self._profiles(target, inst.organization_id)
        if own is None:
            return []
        rows = list(
            self.s.scalars(
                select(ExportRunRecord)
                .where(ExportRunRecord.profile_id == own.id, ExportRunRecord.instance_id == inst.id)
                .order_by(ExportRunRecord.created_at.desc())
                .limit(30)
            )
        )
        tracked = self.ledger.recorded_runs([r.id for r in rows])
        return [(r, r.id in tracked) for r in rows]

    def _cross_source(
        self,
        inst: Any,
        target: _Target,
        period: AccountingPeriod,
        prep: TargetPreparation,
        result: AccountingReadResult,
    ) -> TargetPreparation:
        """Même pièce venue d'une AUTRE source du même format (filiation, comparaison métier forte, fichier
        d'avant le suivi) : problèmes AJOUTÉS à la préparation (jamais retirés) ; le registre par pièce, les
        historiques et les écritures préparées restent inchangés."""

        def read(other: uuid.UUID, company: str, when: object) -> list[tuple[str, AccountingDocument]]:
            items = self.reader.read(other, company, when).items  # type: ignore[arg-type]
            return [(i.document.source_ref, i.document) for i in items if i.document is not None]

        t = target.binding.target
        extra = CrossSourceGuard(self.s, read).check(
            organization_id=inst.organization_id,
            instance_id=inst.id,
            domain=SOURCE_MODEL,
            target_system=t.system,
            target_product=t.product,
            period=period,
            documents={
                i.document.source_ref: i.document
                for i in result.items
                if i.document is not None and i.status == DocumentStatus.POSTED
            },
        )
        if not extra:
            return prep
        documents = []
        for d in prep.documents:
            more = extra.get(d.source_ref)
            if not more or d.target_status is None:  # brouillons / annulées : jamais préparées
                documents.append(d)
                continue
            issues = [*d.issues, *more]
            every = issues + [i for bk in d.bookings for i in bk.issues]
            documents.append(d.model_copy(update={"issues": issues, "target_status": _status(every)}))
        return prep.model_copy(update={"documents": documents})

    def _scope(self, inst: Any, target: _Target) -> DocumentScope:
        t = target.binding.target
        return DocumentScope(
            organization_id=inst.organization_id,
            instance_id=inst.id,
            domain=SOURCE_MODEL,
            target_system=t.system,
            target_product=t.product,
        )

    def _legacy(
        self, own: ExportProfileRecord, instance_id: uuid.UUID, period: AccountingPeriod
    ) -> dict[str, tuple[uuid.UUID, datetime]]:
        """Pièces citées par un fichier de cette période généré AVANT le suivi par pièce (simple indice)."""
        runs = list(
            self.s.scalars(
                select(ExportRunRecord)
                .where(
                    ExportRunRecord.profile_id == own.id,
                    ExportRunRecord.instance_id == instance_id,
                    ExportRunRecord.period == str(period),
                )
                .order_by(ExportRunRecord.created_at)
            )
        )
        tracked = self.ledger.recorded_runs([r.id for r in runs])
        out: dict[str, tuple[uuid.UUID, datetime]] = {}
        for r in runs:
            if r.id in tracked:
                continue
            for x in (r.details or {}).get("documents") or []:
                if x.get("ref"):
                    out[x["ref"]] = (r.id, r.created_at)
        return out

    def _file(
        self,
        instance_id: uuid.UUID,
        company: str,
        period: AccountingPeriod,
        system: str,
        product: str,
        version: str | None,
        now: datetime,
        include_exported: bool,
    ) -> _File:
        inst = self.reader.instances.get(instance_id)  # contrôle d'appartenance AVANT toute lecture
        target = self._target(system, product, version)
        own, shared = self._profiles(target, inst.organization_id)
        settings = self._settings(target, own, shared)
        if not settings.ready or own is None:
            raise AccountingTargetNotReady(
                f"{_name(target)} settings are incomplete: " + "; ".join(settings.missing)
            )
        if settings.missing_for_file:
            raise AccountingTargetNotReady(
                "Required to create the file: " + "; ".join(settings.missing_for_file)
            )
        result = self.reader.read(inst.id, company, period)
        prep = _prepare(self.s, target, inst.id, inst.organization_id, own, shared, settings, result)
        prep = self._cross_source(inst, target, period, prep, result)  # doublons entre sources : bloqués
        # historique d'export, AVANT la sélection finale : état de chaque pièce exportable
        scope = self._scope(inst, target)
        contents = {
            d.source_ref: _content(target, d) for d in prep.documents if d.target_status in EXPORTABLE
        }
        classes = self.ledger.classify(scope, list(contents.values()), self._legacy(own, inst.id, period))
        plan, content = _serialize(
            target, prep, settings, period, now, classes=classes, include_exported=include_exported
        )
        previous = self.s.execute(
            select(func.count(ExportRunRecord.id), func.max(ExportRunRecord.created_at)).where(
                ExportRunRecord.profile_id == own.id,
                ExportRunRecord.instance_id == inst.id,
                ExportRunRecord.period == str(period),
            )
        ).one()
        plan = plan.model_copy(update={"previous_exports": previous[0] or 0, "last_export_at": previous[1]})
        return _File(
            plan=plan,
            content=content,
            profile=own,
            snapshot_id=target.resolved.snapshot_id,
            plan_checksum=result.plan_checksum,
            company_ref=result.company.source_ref,
            scope=scope,
            contents=contents,
            classes=classes,
        )


FILE_SCOPE = "file"  # portée d'historique des pièces venues d'un fichier (aucune instance)


@dataclass(frozen=True)
class PeriodSpan:
    """Période d'un fichier source : du premier jour du premier mois au dernier jour du dernier mois (en-tête
    « Datum von / bis ») ; même interface qu'une période mensuelle pour l'en-tête et le journal."""

    first_day: date
    last_day: date

    @property
    def year(self) -> int:
        return self.first_day.year

    @property
    def month(self) -> int:
        return self.first_day.month

    @property
    def label(self) -> str:
        a, b = self.first_day, self.last_day
        if (a.year, a.month) == (b.year, b.month):
            return f"{a.month:02d}/{a.year:04d}"
        return f"{a.month:02d}/{a.year:04d} bis {b.month:02d}/{b.year:04d}"

    @property
    def text(self) -> str:
        """Même période pour l'écran (anglais) ; `label` est écrit dans le fichier (langue du format)."""
        return self.label.replace(" bis ", " to ")

    @property
    def slug(self) -> str:
        a, b = self.first_day, self.last_day
        return f"{a:%Y-%m}" if (a.year, a.month) == (b.year, b.month) else f"{a:%Y-%m}_{b:%Y-%m}"

    def __str__(self) -> str:  # colonne du journal (AAAA-MM) : premier mois ; la plage est dans les détails
        return f"{self.first_day:%Y-%m}"

    @classmethod
    def covering(cls, days: list[date]) -> PeriodSpan:
        first, last = min(days), max(days)
        end = date(last.year, last.month, calendar.monthrange(last.year, last.month)[1])
        return cls(date(first.year, first.month, 1), end)


@dataclass(frozen=True)
class _File:
    plan: ExportFilePlan
    content: bytes
    profile: ExportProfileRecord
    snapshot_id: uuid.UUID
    plan_checksum: str
    company_ref: str
    scope: DocumentScope
    contents: dict[str, DocumentContent]
    classes: dict[str, DocumentClassification]


# --------------------------------------------------------------------------- #
# Sérialisation : écritures préparées → fichier. Aucune décision comptable ici.
# --------------------------------------------------------------------------- #
# statuts cibles dont les écritures sont écrites : READY, et WARNING (techniquement valide : tout champ
# obligatoire est validé ; un avertissement signale un champ facultatif omis ou un point d'attention)
EXPORTABLE = frozenset({RecordStatus.READY, RecordStatus.WARNING})


def _content(target: _Target, d: PreparedDocument) -> DocumentContent:
    """Contenu cible canonique d'une pièce pour son empreinte d'export : chaque écriture préparée, champ du
    format → texte EXACT que l'écrivain écrirait (montant, sens, comptes, clé de taxe, date, référence,
    texte). Rien de volatil : ni horodatage, ni nom de fichier, ni rang dans le fichier, ni identifiant de
    génération."""
    return DocumentContent(
        source_ref=d.source_ref,
        number=d.number,
        units=[
            {
                fd.technical_name: target.writer.format_cell(fd, bk.values[fd.technical_name])
                for fd in target.resolved.fields
                if fd.technical_name in bk.values
            }
            for bk in d.bookings
        ],
    )


def _when(at: datetime | None) -> str:
    return at.strftime("%Y-%m-%d") if at else "an earlier date"


def _serialize(
    target: _Target,
    prep: TargetPreparation,
    settings: TargetSettings,
    period: AccountingPeriod,
    now: datetime,
    *,
    classes: dict[str, DocumentClassification] | None = None,
    include_exported: bool = False,
    only_refs: set[str] | None = None,
) -> tuple[ExportFilePlan, bytes]:
    """Écritures préparées → fichier. `classes` (historique d'export par pièce) restreint la sélection : NEW
    écrite ; ALREADY_EXPORTED seulement sur demande explicite (`include_exported`) ; CHANGED_SINCE_EXPORT
    seulement si autorisée. Sans `classes`, toutes les pièces exportables sont écrites (`only_refs` :
    seulement celles-là)."""
    b, resolved, writer = target.binding, target.resolved, target.writer
    values = {v.name: v.value for v in settings.values}
    encoding = values.get("encoding") or b.encodings[0]
    if encoding not in b.encodings:
        raise ExportInvalid(f"File encoding {encoding} is not supported by {b.name}")

    candidates = sorted(
        (
            d
            for d in prep.documents
            if d.target_status in EXPORTABLE and (only_refs is None or d.source_ref in only_refs)
        ),
        key=lambda d: (d.accounting_date or date.min, d.number or "", d.source_ref),
    )
    excluded = [
        ExcludedDocument(
            document_ref=d.source_ref,
            number=d.number,
            reference=d.reference,
            document_type=d.document_type,
            source_status=d.source_status,
            target_status=d.target_status,
            reasons=_reasons(d),
        )
        for d in prep.documents
        if d.target_status not in EXPORTABLE
    ]
    if not candidates:
        raise ExportInvalid(
            "No document can be exported for this period: every document is blocked or not prepared"
        )

    # sélection finale par l'historique d'export (axe distinct du statut cible)
    documents: list[ExportDocument] = []
    exported: list[PreparedDocument] = []
    for d in candidates:
        c = classes.get(d.source_ref) if classes is not None else None
        state = c.state if c else DocumentExportState.NEW
        released = bool(c and c.released)
        duplicate = state == DocumentExportState.ALREADY_EXPORTED and include_exported
        included = state == DocumentExportState.NEW or released or duplicate
        documents.append(
            ExportDocument(
                document_ref=d.source_ref,
                number=d.number,
                partner_name=d.partner_name,
                target_status=d.target_status or RecordStatus.READY,
                state=state,
                included=included,
                released=released,
                duplicate=duplicate,
                bookings=len(d.bookings),
                previous_exported_at=c.previous_exported_at if c else None,
                previous_origin=c.previous_origin if c else None,
                previous_bookings=c.previous_units if c else None,
                changes=[
                    ExportFieldChange(
                        booking=x.unit,
                        field=x.field,
                        label=next((f.label for f in resolved.fields if f.technical_name == x.field), None),
                        change=x.change,
                    )
                    for x in (c.changes if c else [])
                ],
                legacy_exported_at=c.legacy_exported_at if c else None,
                legacy_run_id=c.legacy_run_id if c else None,
            )
        )
        if included:
            exported.append(d)
        else:
            reason = (
                f"Already exported on {_when(c.previous_exported_at if c else None)}: not exported again"
                if state == DocumentExportState.ALREADY_EXPORTED
                else f"Exported on {_when(c.previous_exported_at if c else None)} and changed since: "
                "allow a new export first"
            )
            excluded.append(
                ExcludedDocument(
                    document_ref=d.source_ref,
                    number=d.number,
                    reference=d.reference,
                    document_type=d.document_type,
                    source_status=d.source_status,
                    target_status=d.target_status,
                    reasons=[reason],
                    export_state=state,
                )
            )
    states = {x.document_ref: x.state for x in documents}

    # contrôle final de CHAQUE valeur contre le Registry (avec l'encodage du fichier) : une valeur refusée
    # arrête tout — jamais de ligne omise en silence
    rows: list[ExportRow] = []
    row_values: list[dict[str, Any]] = []
    problems: list[str] = []
    for d in exported:
        for bk in d.bookings:
            where = f"{d.number or d.source_ref}, booking {bk.index}"
            for fd in resolved.fields:
                value = bk.values.get(fd.technical_name)
                if value is None:
                    if fd.required:
                        problems.append(f"{where}: {fd.label} is required and has no value")
                    continue
                if reason := check_value(fd, value, writer, encoding):
                    problems.append(f"{where}: {fd.label} {reason}")
            row_values.append(bk.values)
            rows.append(
                ExportRow(
                    row=len(rows) + 1,
                    document_ref=d.source_ref,
                    document_number=d.number,
                    partner_name=d.partner_name,
                    booking_index=bk.index,
                    # statut de la PIÈCE (un tout) : avertissement de la pièce visible sur ses lignes
                    status=d.target_status or bk.status,
                    export_state=states[d.source_ref],
                    source_lines=[sl.position for sl in bk.source_lines],
                    cells=_cells(target, resolved.fields, bk.values),
                )
            )

    # en-tête : réglages (propres et partagés), contexte de l'export, métadonnées du snapshot
    specs: dict[str, Any] = {}
    if b.inherit_profile:
        t = b.inherit_profile.target
        specs = {p.name: p for p in TargetBindingRegistry.find(t.system, t.product).profile}
    context = {
        "period": str(period),
        "period_start": period.first_day,
        "period_end": period.last_day,
        "period_year": f"{period.year:04d}",
        "period_month": f"{period.month:02d}",
        "period_label": getattr(period, "label", f"{period.month:02d}/{period.year:04d}"),
    }
    header = header_values(resolved, values, now.replace(tzinfo=None), context=context, params=specs)
    stable = header_values(resolved, values, None, context=context, params=specs)
    for pos, value in header.items():
        fd = resolved.header_field(pos)
        if reason := check_value(fd, value, writer, encoding):
            problems.append(f"File header, {fd.label}: {reason}")
    if problems:
        raise ExportInvalid("The file cannot be created: " + "; ".join(problems[:10]))

    # aucune pièce à écrire (tout est déjà exporté ou bloqué par l'historique) : pas de fichier
    content = (
        writer.render(resolved.header_fields, header, resolved.fields, row_values, encoding) if rows else b""
    )
    digest = hashlib.sha256(
        writer.render(resolved.header_fields, stable, resolved.fields, row_values, encoding)
    ).hexdigest()
    plan = ExportFilePlan(
        target=prep.target,
        system=prep.system,
        product=prep.product,
        format_version=prep.format_version,
        snapshot_label=prep.snapshot_label,
        binding_version=prep.binding_version,
        period=prep.period,
        period_text=str(getattr(period, "text", period)),
        company_name=prep.company_name,
        encoding=encoding,
        source_documents=len(prep.documents),
        exported_documents=len(exported),
        excluded_documents=len(excluded),
        warning_documents=sum(1 for d in exported if d.target_status == RecordStatus.WARNING),
        blocked_documents=prep.count(RecordStatus.BLOCKED),
        not_prepared_documents=prep.not_prepared,
        row_count=len(rows),
        column_count=len(resolved.fields),
        header=_cells(target, resolved.header_fields, header),
        volatile_header_fields=sorted(set(header) - set(stable)),
        rows=rows,
        excluded=excluded,
        content_digest=digest,
        size=len(content),
        previous_exports=0,
        last_export_at=None,
        documents=documents,
        new_documents=sum(1 for x in documents if x.state == DocumentExportState.NEW),
        already_exported_documents=sum(
            1 for x in documents if x.state == DocumentExportState.ALREADY_EXPORTED
        ),
        changed_documents=sum(1 for x in documents if x.state == DocumentExportState.CHANGED_SINCE_EXPORT),
        released_documents=sum(1 for x in documents if x.released),
        duplicate_documents=sum(1 for x in documents if x.duplicate),
        include_exported=include_exported,
    )
    return plan, content


def _cells(target: _Target, fields: list[Any], values: dict[str, Any]) -> list[TargetCell]:
    return [
        TargetCell(
            field=fd.technical_name,
            label=fd.label,
            value=target.writer.format_cell(fd, values[fd.technical_name]),
        )
        for fd in fields
        if fd.technical_name in values
    ]


def _reasons(d: PreparedDocument) -> list[str]:
    if d.target_status is None:
        return [d.not_prepared_reason or "Not prepared"]
    out: list[str] = []
    for i in [*d.issues, *(i for bk in d.bookings for i in bk.issues)]:
        if i.level == IssueLevel.BLOCKING and i.message not in out:
            out.append(i.message)
    return out or ["Blocked"]


def _row_index(rows: list[ExportRow]) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        entry = out.setdefault(
            r.document_ref, {"ref": r.document_ref, "number": r.document_number, "rows": []}
        )
        entry["rows"].append(r.row)
    return list(out.values())


# --------------------------------------------------------------------------- #
def _prepare(  # noqa: PLR0913
    s: Session,
    target: _Target,
    instance_id: uuid.UUID | None,
    organization_id: uuid.UUID,
    own: ExportProfileRecord | None,
    shared: ExportProfileRecord | None,
    settings: TargetSettings,
    result: AccountingReadResult,
    decided: dict[tuple[str, str], Any] | None = None,
) -> TargetPreparation:
    """`instance_id` None : source FICHIER (aucune instance) ; `decided` : décisions de correspondance passées
    par l'appelant (sorte, référence source) → décision (`target_value`, `origin`), au lieu des décisions
    enregistrées."""
    b = target.binding
    if b.booking is None or b.booking.model != BookingModel.GROSS_BY_ACCOUNT_AND_TAX:
        raise AccountingTargetError(f"{b.name}: booking model not supported")
    values = {v.name: v.value for v in settings.values}
    encoding = b.encodings[0]

    # comptes du tiers : numéros attribués par l'export du format partagé (lecture seule)
    partner_numbers: dict[tuple[str, str], str] = {}
    if b.partner_account and shared is not None:
        for row in s.scalars(
            select(ExportAccountAssignmentRecord).where(
                ExportAccountAssignmentRecord.profile_id == shared.id,
                ExportAccountAssignmentRecord.instance_id == instance_id,
            )
        ):
            partner_numbers[(row.source_ref, row.role)] = row.account_number
    decisions = (
        decided
        if decided is not None
        else {
            (r.kind, r.source_ref): r
            for r in s.scalars(
                select(ExportValueMappingRecord).where(
                    ExportValueMappingRecord.organization_id == organization_id,
                    ExportValueMappingRecord.target_system == b.target.system,
                    ExportValueMappingRecord.target_product == b.target.product,
                    ExportValueMappingRecord.instance_id == instance_id,
                )
            )
        }
    )

    # écritures de chaque pièce comptabilisée (brouillons / annulées : jamais préparées)
    derived = {}
    for item in result.items:
        if item.document is not None and item.status == DocumentStatus.POSTED:
            derived[item.document.source_ref] = gross_bookings(item.document)

    # correspondances nécessaires sur la période (comptes de contrepartie, taxes des écritures)
    used: dict[tuple[LookupKind, str], tuple[str, str | None, set[str]]] = {}
    roles = {i.document.source_ref: i.document.partner.role for i in result.items if i.document is not None}
    for ref, bs in derived.items():
        for bk in bs.bookings:
            kind, src, code0, name0 = _counter_key(bk)
            code, name, docs = used.get((kind, src), (code0, name0, set()))
            used[(kind, src)] = (code, name, docs | {ref})
            tax_key = _tax_key(bk, roles[ref])
            if tax_key is not None:
                code, name, docs = used.get(tax_key[:2], (tax_key[2], None, set()))
                used[tax_key[:2]] = (code, name, docs | {ref})
    mappings = {
        key: _mapping(
            target, key[0], key[1], code, name, len(docs), decisions.get((key[0].value, key[1])), values
        )
        for key, (code, name, docs) in used.items()
        if b.lookup(key[0]) is not None
    }

    documents = []
    for item in result.items:
        doc = item.document
        if doc is None or item.status != DocumentStatus.POSTED:
            reason = {
                DocumentStatus.DRAFT: "Draft documents are not prepared: post them in your system first",
                DocumentStatus.CANCELLED: "Cancelled documents are never prepared",
            }.get(item.status, "This document could not be read completely")
            documents.append(
                PreparedDocument(
                    source_ref=doc.source_ref if doc else (item.number or ""),
                    number=item.number if item.number and item.number != "/" else None,
                    reference=doc.reference if doc else None,
                    document_type=item.document_type.value if item.document_type else None,
                    source_status=item.status.value if item.status else None,
                    eligible=False,
                    target_status=None,
                    not_prepared_reason=reason,
                    partner_name=doc.partner.name if doc else None,
                    partner_role=doc.partner.role.value if doc else None,
                    accounting_date=doc.accounting_date if doc else None,
                    currency=doc.currency if doc else None,
                    total_amount=doc.total_amount if doc else None,
                    balance=doc.balance() if doc else None,
                )
            )
            continue
        documents.append(
            _document(target, item, doc, derived[doc.source_ref], mappings, partner_numbers, values, encoding)
        )
    return TargetPreparation(
        target=f"{b.name} · version {target.resolved.format_version}",
        system=b.target.system,
        product=b.target.product,
        format_version=target.resolved.format_version,
        snapshot_label=target.resolved.snapshot_label,
        binding_version=b.binding_version,
        period=result.period,
        company_name=result.company.name,
        settings=settings.values,
        mappings=sorted(mappings.values(), key=lambda m: (m.kind.value, m.source_code)),
        documents=documents,
        truncated=result.truncated,
    )


def _mapping(
    target: _Target,
    kind: LookupKind,
    source_ref: str,
    code: str,
    name: str | None,
    documents: int,
    decision: ExportValueMappingRecord | None,
    values: dict[str, Any],
) -> ValueMapping:
    lookup = target.binding.lookup(kind)
    assert lookup is not None
    base = {
        "kind": kind,
        "source_ref": source_ref,
        "source_code": code,
        "source_name": name,
        "documents": documents,
    }
    fd = target.resolved.field(lookup.field)
    length = values.get(lookup.length_from) if lookup.length_from else None

    def problem(value: str) -> str | None:
        if length is not None and len(value) != int(length):
            return f"must have {int(length)} digits"
        return check_value(fd, value, target.writer, target.binding.encodings[0])

    if decision is not None:
        origin = MappingOrigin(decision.origin)
        if decision.target_value is None:
            if lookup.allow_none:
                return ValueMapping(
                    **base,
                    status=MappingStatus.CONFIRMED,
                    no_value=True,
                    origin=origin,
                    reason=f"Confirmed by you: {lookup.none_label or 'no value'}",
                )
            return ValueMapping(
                **base, status=MappingStatus.INVALID, origin=origin, reason="A value is required"
            )
        if reason := problem(decision.target_value):
            return ValueMapping(
                **base,
                status=MappingStatus.INVALID,
                target_value=decision.target_value,
                origin=origin,
                reason=f"The chosen value {reason} with the current {_name(target)} settings",
            )
        suffix = (
            " (you accepted the suggestion: same number)"
            if origin == MappingOrigin.SUGGESTION_ACCEPTED
            else ""
        )
        return ValueMapping(
            **base,
            status=MappingStatus.CONFIRMED,
            target_value=decision.target_value,
            origin=origin,
            reason=f"Confirmed by you{suffix}",
        )
    if kind == LookupKind.CATEGORY:  # jamais de suggestion : une catégorie n'a pas de numéro de compte
        return ValueMapping(
            **base,
            status=MappingStatus.MISSING,
            reason=f"No automatic mapping for a source category: enter the {_name(target)} "
            f"{lookup.label} yourself",
        )
    if kind == LookupKind.ACCOUNT:
        if code.isdigit() and problem(code) is None:
            return ValueMapping(
                **base,
                status=MappingStatus.SUGGESTED,
                suggestion=code,
                origin=MappingOrigin.IDENTICAL_CODE,
                reason=(
                    "Same number as the source account, and it has the length of your "
                    f"{_name(target)} {lookup.label}s "
                    f"({int(length)} digits). Confirm it only if both use the same chart of accounts."
                    if length is not None
                    else "Same number as the source account. Confirm it only if both use the same chart."
                ),
            )
        return ValueMapping(
            **base,
            status=MappingStatus.MISSING,
            reason=(
                f"No suggestion: the source account number does not fit your {_name(target)} {lookup.label}s"
                + (f" ({int(length)} digits)" if length is not None else "")
            ),
        )
    return ValueMapping(
        **base,
        status=MappingStatus.MISSING,
        reason=(
            f"Tax keys depend on the configuration of your {_name(target)} client: "
            "enter the key given by your tax advisor"
            + (
                f", or choose “{lookup.none_label}” if the G/L account calculates the tax itself"
                if lookup.allow_none
                else ""
            )
        ),
    )


def _document(
    target: _Target,
    item: Any,
    doc: AccountingDocument,
    bs: Any,
    mappings: dict[tuple[LookupKind, str], ValueMapping],
    partner_numbers: dict[tuple[str, str], str],
    values: dict[str, Any],
    encoding: str,
) -> PreparedDocument:
    b = target.binding
    doc_issues: list[TargetIssue] = [
        TargetIssue(level=IssueLevel.BLOCKING, message=f"In your system: {i.message}") for i in item.issues
    ]
    doc_issues += [TargetIssue(level=IssueLevel.BLOCKING, message=i.message) for i in bs.issues]

    # contrôles de la pièce entière (réglages du dossier cible)
    currency = values.get("currency")
    if currency and doc.currency != currency:
        doc_issues.append(
            TargetIssue(
                level=IssueLevel.BLOCKING,
                message=f"The document currency {doc.currency} is not the currency of the "
                f"{_name(target)} client "
                f"({currency})",
            )
        )
    fy = values.get("fiscal_year_start")
    if fy:
        start = date.fromisoformat(str(fy))
        if not start <= doc.accounting_date <= _one_year(start):
            doc_issues.append(
                TargetIssue(
                    level=IssueLevel.BLOCKING,
                    message=f"The accounting date is outside the fiscal year starting {start.isoformat()}",
                )
            )
    if doc.document_date and (doc.document_date.year, doc.document_date.month) != (
        doc.accounting_date.year,
        doc.accounting_date.month,
    ):
        doc_issues.append(
            TargetIssue(
                level=IssueLevel.WARNING,
                message="The document date is in another month than the accounting date",
            )
        )

    role = doc.partner.role.value
    partner_account = partner_numbers.get((doc.partner.source_ref, role))
    partner_issue = None
    if b.partner_account:
        fd = target.resolved.field(b.partner_account.field)
        who = "Customer" if role == "CUSTOMER" else "Supplier"
        if partner_account is None:
            shared = TargetBindingRegistry.find(
                b.partner_account.target.system, b.partner_account.target.product
            )
            partner_issue = TargetIssue(
                level=IssueLevel.BLOCKING,
                field=b.partner_account.field,
                label=fd.label,
                message=f"{who} {doc.partner.name} has no {_name(target)} account yet: "
                "export your customers & suppliers "
                f"({shared.name}) first",
            )
        else:
            length = values.get(b.partner_account.length_from)
            if length is not None and len(partner_account) != int(length) + b.partner_account.plus:
                partner_issue = TargetIssue(
                    level=IssueLevel.BLOCKING,
                    field=b.partner_account.field,
                    label=fd.label,
                    message=f"The {_name(target)} account {partner_account} of {doc.partner.name} "
                    "does not have "
                    f"{int(length) + b.partner_account.plus} digits",
                )

    text = ", ".join(p for p in (doc.partner.name, doc.number) if p)
    # comptes AUTOMATIQUES du plan comptable du dossier (données) : la cible calcule la taxe, aucune clé
    scope = "SALE" if doc.partner.role == PartnerRole.CUSTOMER else "PURCHASE"
    chart = values.get(b.chart_param) if b.chart_param else None
    table = CategoryAccountRegistry.find(
        b.target.system, b.target.product, str(chart) if chart else None, scope
    )
    automatic = set(table.automatic_accounts) if table else set()
    bookings = []
    for bk in bs.bookings:
        issues: list[TargetIssue] = [partner_issue] if partner_issue else []
        kind, src, _, _ = _counter_key(bk)
        counter, issue = _resolve(target, mappings, kind, src)
        if issue:
            issues.append(issue)
        tax_value, tax_none = None, False
        tax_key = _tax_key(bk, doc.partner.role)
        if tax_key is not None and counter in automatic:
            tax_none = True  # compte automatique : jamais de clé de taxe
        elif tax_key is not None:
            mapping = mappings.get(tax_key[:2])
            tax_value, issue = _resolve(target, mappings, LookupKind.TAX, tax_key[1])
            tax_none = bool(mapping and mapping.status == MappingStatus.CONFIRMED and mapping.no_value)
            if issue:
                issues.append(issue)
        view = {
            "amount": bk.amount,
            "partner_side": bk.partner_side.value,
            "currency": doc.currency,
            "accounting_date": doc.accounting_date,
            "number": doc.number,
            "booking_text": text,
        }
        rec = map_record(
            view,
            target.resolved,
            target.writer,
            encoding,
            source_ref=doc.source_ref,
            target_values={
                "partner_account": partner_account if partner_issue is None else None,
                "counter_account": counter,
                "tax_key": tax_value,
            },
        )
        mine = {i.field for i in issues if i.field}
        issues += [i for i in rec.issues if i.field not in mine]
        cells = [
            TargetCell(
                field=fd.technical_name,
                label=fd.label,
                value=target.writer.format_cell(fd, rec.values[fd.technical_name]),
            )
            for fd in target.resolved.fields
            if fd.technical_name in rec.values
        ]
        bookings.append(
            PreparedBooking(
                index=bk.index,
                amount=bk.amount,
                partner_side=bk.partner_side,
                net_amount=bk.net_amount,
                gross_amount=bk.gross_amount,
                counter_account=bk.counter_account,
                tax=bk.tax,
                partner_account=partner_account,
                counter_target=counter,
                tax_target=tax_value,
                tax_no_value=tax_none,
                counter_category=bk.counter_category,
                tax_rate=bk.tax_rate,
                source_lines=[
                    SourceLine(
                        position=i,
                        kind=doc.lines[i].kind,
                        description=doc.lines[i].description,
                        account_code=doc.lines[i].account.code if doc.lines[i].account else "",
                        amount=doc.lines[i].amount,
                        gross_amount=doc.lines[i].gross_amount,
                    )
                    for i in bk.lines
                ],
                values=rec.values,
                cells=cells,
                status=_status(issues),
                issues=issues,
            )
        )
    every = doc_issues + [i for bk in bookings for i in bk.issues]
    return PreparedDocument(
        source_ref=doc.source_ref,
        number=doc.number,
        reference=doc.reference,
        document_type=doc.document_type.value,
        source_status=doc.status.value,
        eligible=item.exportable,
        target_status=_status(every),  # une pièce est un tout : une écriture bloquée bloque la pièce
        partner_name=doc.partner.name,
        partner_role=role,
        accounting_date=doc.accounting_date,
        document_date=doc.document_date,
        currency=doc.currency,
        total_amount=doc.total_amount,
        balance=doc.balance(),
        issues=doc_issues,
        bookings=bookings,
    )


def _counter_key(bk: Booking) -> tuple[LookupKind, str, str, str | None]:
    """Correspondance de la contrepartie d'une écriture : compte de la source, sinon sa CATÉGORIE (source sans
    plan comptable, CDM 1.1). (sorte, référence, libellé, nom)."""
    if bk.counter_account is not None:
        acc = bk.counter_account
        return LookupKind.ACCOUNT, acc.source_ref, acc.code, acc.name
    assert bk.counter_category is not None
    cat = bk.counter_category
    return LookupKind.CATEGORY, cat.source_ref, cat.name or cat.source_ref, None


def _tax_key(bk: Booking, role: PartnerRole) -> tuple[LookupKind, str, str] | None:
    """Taxe d'une écriture à faire correspondre : taxe de la source, sinon son TAUX et son sens (vente /
    achat : clés différentes chez une cible) — une décision de la cible, jamais une taxe inventée."""
    if bk.tax is not None:
        return LookupKind.TAX, bk.tax.source_ref, bk.tax.name
    if bk.tax_rate is None:
        return None
    scope = "SALE" if role == PartnerRole.CUSTOMER else "PURCHASE"
    rate = bk.tax_rate.normalize()
    label = "sales" if scope == "SALE" else "purchases"
    return LookupKind.TAX, f"rate:{scope}:{rate:f}", f"{rate:f} % ({label})"


def _resolve(
    target: _Target, mappings: dict[tuple[LookupKind, str], ValueMapping], kind: LookupKind, source_ref: str
) -> tuple[str | None, TargetIssue | None]:
    """Valeur cible CONFIRMÉE d'un compte / d'une taxe, ou le problème qui bloque l'écriture."""
    lookup = target.binding.lookup(kind)
    if lookup is None:
        return None, None
    fd = target.resolved.field(lookup.field)
    m = mappings.get((kind, source_ref))
    label = "Category" if kind == LookupKind.CATEGORY else lookup.label
    what = f"{label} {m.source_code}" if m else label
    if m is not None and m.source_name:
        what += f" {m.source_name}"
    if m is None or m.status == MappingStatus.MISSING:
        message = f"{what} has no {_name(target)} mapping yet"
    elif m.status == MappingStatus.SUGGESTED:
        message = f"{what}: the suggested {_name(target)} value {m.suggestion} is not confirmed yet"
    elif m.status == MappingStatus.INVALID:
        message = f"{what}: {m.reason}"
    else:
        return (None if m.no_value else m.target_value), None
    return None, TargetIssue(level=IssueLevel.BLOCKING, field=lookup.field, label=fd.label, message=message)


def _status(issues: list[TargetIssue]) -> RecordStatus:
    levels = {i.level for i in issues}
    if IssueLevel.BLOCKING in levels:
        return RecordStatus.BLOCKED
    return RecordStatus.WARNING if levels else RecordStatus.READY
