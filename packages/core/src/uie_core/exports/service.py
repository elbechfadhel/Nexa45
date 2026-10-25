"""Export de partenaires vers un format de fichier cible (PARTNER → ex. Debitoren/Kreditoren EXTF).

    PartnerReadService (CDM, lecture à la demande) ──► binding cible résolu contre le Registry
        ──► comptes attribués (profil, plages) ──► enregistrements validés ──► aperçu | fichier + journal

Générique : la cible est un binding enregistré (`TargetBindingRegistry`, modèle « partner ») ; ni nom de
système ni colonne ici. Aperçu = LECTURE SEULE (aucun numéro réservé, rien d'écrit) ; génération = numéros
réservés pour les enregistrements écrits, journal d'audit (compteurs, empreinte), jamais le contenu. Rien
n'est envoyé au système cible : le fichier est rendu à l'utilisateur.

Doublons entre sources (étape 1, sans migration) : un partenaire qui recevrait un NOUVEAU numéro alors que le
même partenaire (même identité FORTE : n° de TVA normalisé, jamais le nom) a déjà un numéro pour le même rôle
dans ce profil, attribué depuis une AUTRE source, est BLOQUÉ (aperçu et fichier) ; les autres sources du
profil sont relues à la demande. Aucun numéro existant n'est modifié."""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import date
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session
from uie_cdm import Partner, PartnerRole

from uie_core.db.base import utcnow
from uie_core.exports.records import ExportAccountAssignmentRecord, ExportProfileRecord, ExportRunRecord
from uie_core.identity.context import (
    AdminContext,
    Context,
    TenantContext,
    ensure_context,
    tenant_organization,
)
from uie_core.mapping import (
    IssueLevel,
    ParamType,
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
    profile_header_value,
    resolve_binding,
)
from uie_core.partners import PartnerReadResult, PartnerReadService
from uie_core.registry import models as m
from uie_core.registry.service import NotFound, RegistryError, RegistryService

SOURCE_MODEL = "partner"
DEFAULT_CLIENT = "default"  # dossier (mandant) unique d'une organisation qui n'en a qu'un"


class ExportError(RegistryError):
    """Export impossible (→ 400)."""


class ExportNotReady(ExportError):
    """Profil absent ou incomplet, analyse PARTNER incomplète (→ 409)."""


class ProfileInvalid(ExportError):
    """Valeurs du profil invalides (→ 422), une phrase par paramètre."""

    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------- #
# Profil
# --------------------------------------------------------------------------- #
def _org(ctx: Context, organization_id: uuid.UUID | None) -> uuid.UUID:
    org = tenant_organization(ctx)
    if org is not None:
        return org  # contexte fournisseur : toujours SON organisation, jamais un id du client
    if organization_id is None:
        raise ExportError("organization_id requis hors contexte fournisseur")
    return organization_id


def _range(value: Any) -> tuple[int, int] | None:
    if isinstance(value, dict) and {"start", "end"} <= set(value):
        try:
            return int(value["start"]), int(value["end"])
        except (TypeError, ValueError):
            return None
    return None


def validate_profile(
    resolved: ResolvedBinding, writer: RecordFileWriter, values: dict[str, Any]
) -> dict[str, Any]:
    """Valeurs propres du profil, ou `ProfileInvalid`. Contrôles : type, obligatoire, choix, motif du
    Registry de la position d'en-tête où la valeur est écrite, longueur des plages, recouvrement."""
    b = resolved.binding
    clean: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for p in b.profile:
        raw = values.get(p.name, p.default)
        if raw is None or raw == "" or (p.type == ParamType.RANGE and not raw):
            if p.required:
                errors[p.name] = f"{p.label} is required"
            continue
        try:
            if p.type == ParamType.INTEGER:
                if not re.fullmatch(r"\d+", str(raw).strip()):
                    raise ValueError
                value: Any = int(str(raw).strip())
            elif p.type == ParamType.DATE:
                value = (raw if isinstance(raw, date) else date.fromisoformat(str(raw))).isoformat()
            elif p.type == ParamType.RANGE:
                rng = _range(raw)
                if rng is None or rng[0] > rng[1]:
                    raise ValueError
                value = {"start": rng[0], "end": rng[1]}
            elif p.type == ParamType.BOOLEAN:
                if not isinstance(raw, bool):
                    raise ValueError
                value = raw
            elif p.type == ParamType.CHOICE:
                if raw not in p.choices:
                    raise ValueError
                value = raw
            else:
                value = str(raw).strip()
        except ValueError:
            errors[p.name] = f"{p.label} is not valid"
            continue
        if p.header_field:
            fd = resolved.header_field(p.header_field)
            reason = check_value(fd, profile_header_value(p, value), writer, "utf-8")
            if reason:
                # format déclaré par le binding (phrase simple) plutôt que la raison générique
                errors[p.name] = f"{p.label}: {p.format_help}" if p.format_help else f"{p.label} {reason}"
                continue
        clean[p.name] = value
    acc = b.account
    if acc and acc.length_from in clean:
        length = int(clean[acc.length_from]) + acc.plus
        spans = []
        for name in acc.ranges.values():
            rng = _range(clean.get(name))
            if rng is None:
                continue
            if any(len(str(n)) != length for n in rng):
                label = next(p.label for p in b.profile if p.name == name)
                errors[name] = f"{label}: account numbers must have {length} digits"
            spans.append((rng, name))
        for i, (a, name_a) in enumerate(spans):
            for b_rng, _ in spans[i + 1 :]:
                if a[0] <= b_rng[1] and b_rng[0] <= a[1]:
                    errors.setdefault(name_a, "The account ranges overlap")
    if errors:
        raise ProfileInvalid(errors)
    return clean


@dataclass(frozen=True)
class ExportTarget:
    binding: TargetBinding
    resolved: ResolvedBinding
    writer: RecordFileWriter

    @property
    def label(self) -> str:
        t = self.resolved
        return f"{self.binding.name} · version {t.format_version}"


class ExportProfileService:
    def __init__(self, session: Session, ctx: Context) -> None:
        self.s = session
        self.ctx = ensure_context(ctx)
        self.registry = RegistryService(session, self.ctx)

    def targets(self) -> list[TargetBinding]:
        return TargetBindingRegistry.for_model(SOURCE_MODEL)

    def target(self, system: str, product: str) -> ExportTarget:
        try:
            # un format = un binding, quel que soit le modèle source (partner, accounting…)
            binding = TargetBindingRegistry.find(system, product)
            resolved = resolve_binding(self.registry, binding)
            writer = RecordFileWriterRegistry.get(binding.target.system, binding.target.interface)
        except TargetBindingError as exc:
            raise NotFound(str(exc)) from None
        return ExportTarget(binding, resolved, writer)

    def get(
        self,
        system: str,
        product: str,
        organization_id: uuid.UUID | None = None,
        client_key: str = DEFAULT_CLIENT,
    ) -> ExportProfileRecord | None:
        """Réglages de ce format pour ce dossier (mandant) de l'organisation."""
        org = _org(self.ctx, organization_id)
        return self.s.scalar(
            select(ExportProfileRecord).where(
                ExportProfileRecord.organization_id == org,
                ExportProfileRecord.target_system == system,
                ExportProfileRecord.target_product == product,
                ExportProfileRecord.client_key == client_key,
            )
        )

    def save(
        self,
        system: str,
        product: str,
        values: dict[str, Any],
        organization_id: uuid.UUID | None = None,
        client_key: str = DEFAULT_CLIENT,
    ) -> ExportProfileRecord:
        target = self.target(system, product)
        clean = validate_profile(target.resolved, target.writer, values)
        row = self.get(system, product, organization_id, client_key)
        if row is None:
            row = ExportProfileRecord(
                organization_id=_org(self.ctx, organization_id),
                source_model=target.binding.source.model,
                target_system=system,
                target_product=product,
                client_key=client_key,
            )
            self.s.add(row)
        row.values, row.updated_at = clean, utcnow()
        self.s.flush()
        return row


# --------------------------------------------------------------------------- #
# Comptes
# --------------------------------------------------------------------------- #
@dataclass
class AccountPlan:
    numbers: dict[tuple[str, str], str]  # (source_ref, rôle) → numéro
    new: set[tuple[str, str]]  # attribués par cet aperçu / cette génération
    origin: dict[tuple[str, str], str]  # ASSIGNED | REUSED
    exhausted: set[str]  # rôles dont la plage est pleine


def strong_identity(partner: Partner) -> str | None:
    """Identité FORTE d'un partenaire pour reconnaître le même partenaire venu d'une autre source : n° de TVA
    normalisé (lettres et chiffres, majuscules). Jamais le nom. None : pas d'identité forte."""
    vat = re.sub(r"[^0-9A-Z]", "", (partner.tax.vat_id or "").upper())
    return vat if len(vat) >= 4 else None


def plan_accounts(
    s: Session,
    profile: ExportProfileRecord,
    binding: TargetBinding,
    instance_id: uuid.UUID | None,
    needs: list[tuple[Partner, PartnerRole]],
) -> AccountPlan:
    """Numéro de chaque (partenaire, rôle) : existant (jamais changé), sinon reprise du numéro de la source
    si le profil l'autorise et qu'il convient, sinon décalage (« 10000 + numéro de la source ») si le profil
    en donne un et qu'il tient dans la plage, sinon le premier libre de la plage. Jamais un numéro déjà
    attribué dans ce profil (y compris à un système supprimé). Ne réserve rien : c'est la génération qui
    enregistre les numéros des lignes écrites. `instance_id` None : source fichier."""
    acc = binding.account
    rows = list(
        s.scalars(
            select(ExportAccountAssignmentRecord).where(
                ExportAccountAssignmentRecord.profile_id == profile.id
            )
        )
    )
    used = {r.account_number for r in rows}
    mine = {(r.source_ref, r.role): r.account_number for r in rows if r.instance_id == instance_id}
    plan = AccountPlan(numbers={}, new=set(), origin={}, exhausted=set())
    if acc is None:
        return plan
    values = profile.values
    length = int(values[acc.length_from]) + acc.plus
    reuse = bool(values.get(acc.reuse_param)) if acc.reuse_param else False
    offset = values.get(acc.offset_param) if acc.offset_param else None
    cursor: dict[str, int] = {}
    for partner, role in sorted(needs, key=lambda n: (_sort_ref(n[0].source_ref), n[1].value)):
        key = (partner.source_ref, role.value)
        if key in mine:
            plan.numbers[key] = mine[key]
            continue
        rng = _range(values.get(acc.ranges.get(role.value, "")))
        if rng is None:
            continue
        source = partner.mapping_view(role).get(acc.reuse_from.get(role.value, ""))
        candidate = source if reuse else None
        shifted = (
            str(int(offset) + int(source))
            if offset not in (None, "") and source and re.fullmatch(r"\d+", str(source))
            else None
        )
        if (
            candidate
            and re.fullmatch(r"\d+", str(candidate))
            and len(str(candidate)) == length
            and rng[0] <= int(candidate) <= rng[1]
            and str(candidate) not in used
        ):
            number, origin = str(candidate), "REUSED"
        elif shifted and len(shifted) == length and rng[0] <= int(shifted) <= rng[1] and shifted not in used:
            number, origin = shifted, "OFFSET"
        else:
            n = cursor.get(role.value, rng[0])
            while n <= rng[1] and str(n) in used:
                n += 1
            if n > rng[1]:
                plan.exhausted.add(role.value)
                continue
            cursor[role.value] = n + 1
            number, origin = str(n), "ASSIGNED"
        used.add(number)
        plan.numbers[key], plan.origin[key] = number, origin
        plan.new.add(key)
    return plan


def _sort_ref(ref: str) -> tuple[int, str]:
    return (int(ref), ref) if ref.isdigit() else (1 << 62, ref)


# --------------------------------------------------------------------------- #
# Aperçu et génération
# --------------------------------------------------------------------------- #
class PreviewRecord(_Model):
    source_ref: str
    name: str
    role: str
    account_number: str | None
    account_new: bool
    status: RecordStatus
    issues: list[TargetIssue]
    values: dict[str, Any]


class NotExported(_Model):
    source_ref: str
    name: str | None
    reason: str


class ExportPreview(_Model):
    target: str
    format_version: str
    encoding: str
    partners_read: int
    records: list[PreviewRecord]
    not_exported: list[NotExported]
    issues_by_ref: dict[str, list[str]]  # problèmes de lecture de la source, par partenaire
    truncated: bool

    def count(self, status: RecordStatus) -> int:
        return sum(1 for r in self.records if r.status == status)


@dataclass(frozen=True)
class GeneratedFile:
    run: ExportRunRecord
    content: bytes
    filename: str
    media_type: str


class PartnerExportService:
    def __init__(self, session: Session, ctx: Context, partners: PartnerReadService) -> None:
        self.s = session
        self.ctx = ensure_context(ctx)
        if partners.ctx != self.ctx:
            raise ValueError("Les services de l'export doivent partager le même contexte")
        self.partners = partners
        self.profiles = ExportProfileService(session, self.ctx)

    def _prepare(
        self, system: str, product: str, instance_id: uuid.UUID
    ) -> tuple[ExportTarget, ExportProfileRecord, PartnerReadResult, uuid.UUID]:
        inst = self.partners.instances.get(instance_id)  # contrôle d'appartenance AVANT toute lecture
        target = self.profiles.target(system, product)
        profile = self.profiles.get(system, product, inst.organization_id)
        if profile is None:
            raise ExportNotReady("Export settings are missing: fill in the export profile first")
        try:
            validate_profile(target.resolved, target.writer, profile.values)
        except ProfileInvalid as exc:
            raise ExportNotReady(f"Export settings are incomplete: {exc}") from None
        result = self.partners.read(inst.id)
        return target, profile, result, inst.id

    def preview(self, system: str, product: str, instance_id: uuid.UUID) -> ExportPreview:
        target, profile, result, iid = self._prepare(system, product, instance_id)
        return self._build(target, profile, result, iid)[0]

    def generate(self, system: str, product: str, instance_id: uuid.UUID) -> GeneratedFile:
        target, profile, result, iid = self._prepare(system, product, instance_id)
        return self._write(target, profile, result, iid)

    # ------------------------------------------------------------------ #
    # Source FICHIER (partenaires construits depuis le fichier d'un client, aucune instance)
    # ------------------------------------------------------------------ #
    def file_preview(
        self,
        system: str,
        product: str,
        result: PartnerReadResult,
        values: dict[str, Any],
        organization_id: uuid.UUID | None = None,
        client_key: str = DEFAULT_CLIENT,
    ) -> ExportPreview:
        """Aperçu pour des partenaires venus d'un FICHIER : réglages du dossier (mandant) de l'organisation
        complétés par `values` (réponses en cours, jamais enregistrées ici) ; rien n'est réservé ni écrit."""
        target = self.profiles.target(system, product)
        profile = self.profiles.get(system, product, organization_id, client_key)
        merged = dict(profile.values if profile else {}) | values
        transient = SimpleNamespace(id=profile.id if profile else None, values=merged)
        return self._build(target, transient, result, None)[0]  # type: ignore[arg-type]

    def file_generate(
        self,
        system: str,
        product: str,
        result: PartnerReadResult,
        values: dict[str, Any],
        organization_id: uuid.UUID | None = None,
        client_key: str = DEFAULT_CLIENT,
        vat: dict[str, tuple[str, str | None]] | None = None,
    ) -> GeneratedFile:
        """Fichier pour des partenaires venus d'un FICHIER : réglages enregistrés dans le profil de
        l'organisation (une seule saisie, repris ensuite), numéros de compte réservés pour les lignes écrites
        (source sans instance : un même numéro du client garde le même compte d'un fichier à l'autre)."""
        profile = self.profiles.get(system, product, organization_id, client_key)
        merged = dict(profile.values if profile else {}) | values
        try:
            saved = self.profiles.save(system, product, merged, organization_id, client_key)
        except ProfileInvalid as exc:
            raise ExportNotReady(
                "Export settings are incomplete: " + "; ".join(exc.errors.values())
            ) from None
        return self._write(self.profiles.target(system, product), saved, result, None, vat)

    def _write(
        self,
        target: ExportTarget,
        profile: ExportProfileRecord,
        result: PartnerReadResult,
        iid: uuid.UUID | None,
        vat: dict[str, tuple[str, str | None]] | None = None,
    ) -> GeneratedFile:
        """`vat` (source fichier) : état du numéro de TVA de chaque partenaire écrit, retenu avec son compte
        (nouveau ou existant : un client corrigé et exporté de nouveau met l'état à jour)."""
        preview, accounts = self._build(target, profile, result, iid)  # réservation : lignes écrites
        written = [r for r in preview.records if r.status != RecordStatus.BLOCKED]
        if not written:
            raise ExportError("No record can be written: every record is blocked")
        encoding = profile.values.get("encoding") or target.binding.encodings[0]
        now = utcnow()
        content = target.writer.render(
            target.resolved.header_fields,
            header_values(target.resolved, profile.values, now.replace(tzinfo=None)),
            target.resolved.fields,
            [r.values for r in written],
            encoding,
        )
        user = self.ctx.user_id if isinstance(self.ctx, TenantContext | AdminContext) else None
        run = ExportRunRecord(
            organization_id=profile.organization_id,
            profile_id=profile.id,
            instance_id=iid,
            created_by=user,
            target_label=target.label,
            target_snapshot_id=target.resolved.snapshot_id,
            binding_version=target.binding.binding_version,
            read_plan_checksum=result.plan_checksum,
            encoding=encoding,
            records=len(written),
            warnings=sum(1 for r in written if r.status == RecordStatus.WARNING),
            blocked=len(preview.records) - len(written),
            file_sha256=hashlib.sha256(content).hexdigest(),
            file_size=len(content),
            details={"source": "file"} if iid is None else None,  # source fichier : reconnue sans instance
        )
        self.s.add(run)
        self.s.flush()
        keep = {(r.source_ref, r.role) for r in written}
        new = 0
        for key in sorted(accounts.new & keep):
            state = (vat or {}).get(key[0], (None, None))
            self.s.add(
                ExportAccountAssignmentRecord(
                    profile_id=profile.id,
                    instance_id=iid,
                    source_ref=key[0],
                    role=key[1],
                    account_number=accounts.numbers[key],
                    origin=accounts.origin[key],
                    export_run_id=run.id,
                    vat_status=state[0],
                    vat_country=state[1],
                )
            )
            new += 1
        if vat:
            for row in self.s.scalars(
                select(ExportAccountAssignmentRecord).where(
                    ExportAccountAssignmentRecord.profile_id == profile.id,
                    ExportAccountAssignmentRecord.instance_id.is_(None),
                    ExportAccountAssignmentRecord.source_ref.in_([k[0] for k in keep - accounts.new]),
                )
            ):
                if (row.source_ref, row.role) in keep and row.source_ref in vat:
                    row.vat_status, row.vat_country = vat[row.source_ref]
        run.new_accounts = new
        self.s.flush()
        stamp = now.strftime("%Y%m%d_%H%M%S")
        t = target.binding.target
        name = f"{t.system.upper()}_{t.product}_{stamp}.{target.writer.extension}"
        return GeneratedFile(run=run, content=content, filename=name, media_type=target.writer.media_type)

    def runs(self, organization_id: uuid.UUID | None = None) -> list[tuple[ExportRunRecord, str | None]]:
        """Journal des générations de l'organisation (50 dernières), avec le nom du système source."""
        org = _org(self.ctx, organization_id)
        rows = self.s.execute(
            select(ExportRunRecord, m.Instance.name)
            .outerjoin(m.Instance, m.Instance.id == ExportRunRecord.instance_id)
            .where(ExportRunRecord.organization_id == org)
            .order_by(ExportRunRecord.created_at.desc())
            .limit(50)
        ).all()
        return [(run, name) for run, name in rows]

    # ------------------------------------------------------------------ #
    def _cross_source(
        self,
        profile: ExportProfileRecord,
        instance_id: uuid.UUID | None,
        needs: list[tuple[Partner, PartnerRole]],
        accounts: AccountPlan,
    ) -> dict[tuple[str, str], TargetIssue]:
        """Problème bloquant par (partenaire, rôle) qui recevrait un NOUVEAU numéro alors que la même identité
        forte a déjà un numéro pour ce rôle dans ce profil, depuis une autre source (relue à la demande). Une
        source illisible ne permet pas de vérifier : ses candidats sont bloqués aussi (jamais un doublon
        silencieux). Un partenaire sans identité forte n'est pas comparé (jamais par le nom). Source fichier
        (`instance_id` None) : non comparée aux systèmes connectés (limite connue, aucune lecture)."""
        if instance_id is None:
            return {}
        wanted = {
            (p.source_ref, role.value): vat
            for p, role in needs
            if (p.source_ref, role.value) in accounts.new and (vat := strong_identity(p))
        }
        if not wanted:
            return {}
        others: dict[uuid.UUID, dict[tuple[str, str], str]] = {}
        for r in self.s.scalars(
            select(ExportAccountAssignmentRecord).where(
                ExportAccountAssignmentRecord.profile_id == profile.id,
                ExportAccountAssignmentRecord.instance_id.is_not(None),
                ExportAccountAssignmentRecord.instance_id != instance_id,
            )
        ):
            others.setdefault(r.instance_id, {})[(r.source_ref, r.role)] = r.account_number
        known: dict[tuple[str, str], tuple[str, str]] = {}  # (identité, rôle) → (source, numéro)
        unreadable: list[str] = []
        for iid, numbers in others.items():
            try:
                name = self.partners.instances.get(iid).name
            except NotFound:
                continue  # hors de ce contexte : jamais lu
            try:
                partners = self.partners.read(iid).partners
            except Exception:  # noqa: BLE001 — source illisible : vérification impossible, signalée
                unreadable.append(name)
                continue
            for p in partners:
                vat = strong_identity(p)
                for role in p.roles:
                    number = numbers.get((p.source_ref, role.value))
                    if vat and number:
                        known.setdefault((vat, role.value), (name, number))
        out: dict[tuple[str, str], TargetIssue] = {}
        for (ref, role), vat in wanted.items():
            hit = known.get((vat, role))
            if hit:
                message = f"Already exported from {hit[0]} as {hit[1]} (same VAT ID): not exported twice"
            elif unreadable:
                message = f"Duplicate check not possible: {', '.join(sorted(unreadable))} could not be read"
            else:
                continue
            out[(ref, role)] = TargetIssue(level=IssueLevel.BLOCKING, message=message, rule_id="CROSS_SOURCE")
        return out

    def _build(
        self,
        target: ExportTarget,
        profile: ExportProfileRecord,
        result: PartnerReadResult,
        instance_id: uuid.UUID | None,
    ) -> tuple[ExportPreview, AccountPlan]:
        b = target.binding
        encoding = profile.values.get("encoding") or b.encodings[0]
        not_exported = [
            NotExported(source_ref=p.source_ref, name=p.name, reason="Neither a customer nor a supplier")
            for p in result.partners
            if not p.roles
        ]
        needs = [(p, role) for p in result.partners for role in p.roles]
        accounts = plan_accounts(self.s, profile, b, instance_id, needs)
        duplicates = self._cross_source(profile, instance_id, needs, accounts)
        records = []
        for partner, role in needs:
            key = (partner.source_ref, role.value)
            number = accounts.numbers.get(key)
            rec = map_record(
                partner.mapping_view(role),
                target.resolved,
                target.writer,
                encoding,
                source_ref=partner.source_ref,
                target_values={"account_number": number},
            )
            issues = list(rec.issues)
            if key in duplicates:
                issues.insert(0, duplicates[key])
            if number is None and role.value in accounts.exhausted:
                issues.insert(
                    0,
                    TargetIssue(
                        level=IssueLevel.BLOCKING, message="No free account number left in the range"
                    ),
                )
            records.append(
                PreviewRecord(
                    source_ref=partner.source_ref,
                    name=partner.name,
                    role=role.value,
                    account_number=number,
                    account_new=key in accounts.new,
                    status=RecordStatus.BLOCKED
                    if any(i.level == IssueLevel.BLOCKING for i in issues)
                    else rec.status,
                    issues=issues,
                    values=rec.values,
                )
            )
        by_ref: dict[str, list[str]] = {}
        for i in result.issues:
            by_ref.setdefault(i.source_ref, []).append(i.message)
        preview = ExportPreview(
            target=target.label,
            format_version=target.resolved.format_version,
            encoding=encoding,
            partners_read=len(result.partners),
            records=records,
            not_exported=not_exported,
            issues_by_ref=by_ref,
            truncated=result.truncated,
        )
        return preview, accounts
