"""PartnerReadService : lit les partenaires d'un système source et les rend en Partner CDM, À LA DEMANDE.

    système source ──► CatalogSource (lecture seule) ──► plan PARTNER (semantic bindings) ──► Partner CDM

Aucune donnée de partenaire n'est stockée dans UIE (données personnelles / commerciales) : chaque lecture
interroge la source et rend le CDM en mémoire. Refusée tant que l'analyse PARTNER n'est pas complète
(`PartnerNotReady` → 409). Aucun nom de système : le sens vient des clés du vocabulaire PARTNER.

Source à SCHÉMA FIXE (un binding de source existe pour son système) : ni découverte ni analyse ; le plan vient
du binding (contrôlé contre le contrat du Registry), la lecture d'un `RecordSource` ; même conversion.

Partenaires retenus : actifs et sans partenaire parent (les contacts et adresses rattachés à une société ne
sont pas des partenaires commerciaux à part entière). Rôles : clé `customer_rank` / `supplier_rank` positive
(ou vraie). Une valeur inutilisable (code pays inconnu, langue illisible) n'est jamais devinée : elle est
omise et signalée (`issues`)."""

from __future__ import annotations

import os
import re
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from uie_cdm import (
    Address,
    BankAccount,
    ContactInfo,
    Identifier,
    IdentifierScheme,
    Partner,
    PartnerKind,
    PartnerRole,
    PersonName,
    TaxInfo,
    is_valid_iban,
)

from uie_core.catalog.plan import BoundField, EntityPlan
from uie_core.catalog.reader import (
    CatalogReaderResolver,
    CatalogSource,
    FieldRequest,
    Ref,
    SourceRecord,
)
from uie_core.domains.enrichment import SemanticEnrichmentService
from uie_core.domains.model import DomainKey, EntityRole
from uie_core.domains.semantics import DecisionSource, MeaningStatus
from uie_core.identity.context import Context, ensure_context
from uie_core.instances import InstanceService
from uie_core.partners.plan import (
    FIXED_PRIMARY_KEYS,
    PartnerPlanError,
    PartnerReadPlan,
    build_partner_plan,
)
from uie_core.registry import models as m
from uie_core.registry.service import NotFound, RegistryError
from uie_core.sources import (
    RecordSourceError,
    RecordSourceResolver,
    SourceBinding,
    SourceBindingError,
    SourceBindingRegistry,
)
from uie_core.sources.catalog import BoundRecordSource, fixed_entity_plan

BATCH = 200
DEFAULT_MAX_PARTNERS = 10_000
USABLE = (MeaningStatus.KNOWN.value, MeaningStatus.CONFIRMED.value)
_COUNTRY = re.compile(r"^[A-Z]{2}$")
_LANG = re.compile(r"^([a-zA-Z]{2})(?:[_-].*)?$")


def partner_read_max() -> int:
    return int(os.environ.get("UIE_PARTNER_READ_MAX", DEFAULT_MAX_PARTNERS))


class PartnerError(RegistryError):
    """Lecture des partenaires impossible (→ HTTP 400 / 502 selon l'entrypoint)."""


class PartnerNotReady(PartnerError):
    """Analyse PARTNER absente ou incomplète (→ 409)."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PartnerIssue(_Model):
    source_ref: str
    name: str | None
    message: str  # phrase simple (anglais), sans nom technique


class PartnerReadResult(_Model):
    plan_checksum: str
    partners: list[Partner]
    issues: list[PartnerIssue] = []
    read: int = 0  # enregistrements actifs lus dans la source
    skipped_children: int = 0  # contacts / adresses rattachés à un partenaire parent
    truncated: bool = False  # plafond atteint : la liste n'est pas complète


class PartnerReadService:
    def __init__(
        self,
        session: Session,
        ctx: Context,
        instances: InstanceService,
        enrichment: SemanticEnrichmentService,
        readers: CatalogReaderResolver | None = None,
        max_partners: int | None = None,
        sources: RecordSourceResolver | None = None,
    ) -> None:
        self.s = session
        self.ctx = ensure_context(ctx)
        if instances.ctx != self.ctx or enrichment.ctx != self.ctx:
            raise ValueError("Les services de lecture des partenaires doivent partager le même contexte")
        if enrichment.domain_key != DomainKey.PARTNER:
            raise ValueError("PartnerReadService attend l'enrichissement du domaine PARTNER")
        self.instances = instances
        self.enrichment = enrichment
        self.domains = enrichment.domains
        self.readers = readers or CatalogReaderResolver()
        self.sources = sources or RecordSourceResolver()
        self.max_partners = max_partners or partner_read_max()

    # ------------------------------------------------------------------ #
    # Source à schéma fixe
    # ------------------------------------------------------------------ #
    @staticmethod
    def source_binding(inst: m.Instance) -> SourceBinding | None:
        """Binding de source du système de l'instance (None : source analysée, chemin habituel)."""
        ver = inst.interface.version
        return SourceBindingRegistry.find(
            DomainKey.PARTNER.value, ver.product.system.code, ver.version, inst.interface.code
        )

    def _fixed_plan(self, inst: m.Instance, binding: SourceBinding) -> PartnerReadPlan:
        snap = self.s.scalar(
            select(m.SchemaSnapshot)
            .where(m.SchemaSnapshot.interface_id == inst.interface_id)
            .order_by(m.SchemaSnapshot.created_at.desc())
            .limit(1)
        )
        if snap is None:
            raise PartnerError("The contract of this system is not in the Registry")
        try:
            binding.check_contract(
                {e.technical_name: {f.technical_name for f in e.fields} for e in snap.entities}
            )
            primary = fixed_entity_plan(binding, EntityRole.PRIMARY, FIXED_PRIMARY_KEYS)
        except SourceBindingError as exc:
            raise PartnerError(str(exc)) from None
        if primary is None or primary.get("name") is None:
            raise PartnerPlanError("No partner name in the source binding")
        return PartnerReadPlan(primary=primary, source_checksum=binding.checksum)

    # ------------------------------------------------------------------ #
    def read_plan(self, instance_id: uuid.UUID) -> PartnerReadPlan:
        """Plan courant : dernier domaine PARTNER analysé + bindings KNOWN / CONFIRMED du domaine (ou binding
        de source d'un système à schéma fixe)."""
        inst = self.domains.instance(instance_id)  # contrôle d'appartenance
        binding = self.source_binding(inst)
        if binding is not None:
            return self._fixed_plan(inst, binding)
        stored = self.domains.get_instance_domain(inst.id)
        rows = self.s.scalars(
            select(m.FieldSemanticBinding).where(
                m.FieldSemanticBinding.instance_id == inst.id,
                m.FieldSemanticBinding.domain_key == DomainKey.PARTNER.value,
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
        return build_partner_plan(
            snap.entities, [*snap.fields, *snap.extensions], bound, domain_snapshot_id=stored.id
        )

    def ensure_ready(self, instance_id: uuid.UUID) -> m.Instance:
        inst = self.instances.get(instance_id)  # contrôle d'appartenance AVANT tout secret
        if self.source_binding(inst) is not None:
            return inst  # schéma fixe : rien à analyser
        try:
            summary = self.enrichment.summary(inst.id)
        except NotFound:
            raise PartnerNotReady("Partner data not analyzed yet: run the partner analysis first") from None
        if not summary.complete:
            raise PartnerNotReady(
                f"Partner analysis not complete: {summary.pending} question(s) to answer first"
            )
        return inst

    def read(self, instance_id: uuid.UUID) -> PartnerReadResult:
        """Partenaires commerciaux actifs du système, en Partner CDM (lecture seule, rien n'est stocké)."""
        inst = self.ensure_ready(instance_id)
        plan = self.read_plan(inst.id)
        _, ctx = self.instances.connection_context(inst.id)
        ver = inst.interface.version
        binding = self.source_binding(inst)
        if binding is not None:
            try:
                with self.sources.open(ver.product.system.code, inst.interface.code, ctx) as raw:
                    src = BoundRecordSource(raw, binding, self.max_partners)
                    return read_partners(src, plan, self.max_partners)
            except RecordSourceError as exc:
                raise PartnerError(f"The partners could not be read: {exc}") from None
        reader = self.readers.resolve(ver.product.system.code, ver.version, inst.interface.code)
        return read_partners(reader.open(ctx), plan, self.max_partners)


# --------------------------------------------------------------------------- #
# Lecture + conversion (pures sur une CatalogSource)
# --------------------------------------------------------------------------- #
def read_partners(src: CatalogSource, plan: PartnerReadPlan, max_partners: int) -> PartnerReadResult:
    p = plan.primary
    total = src.count(p.entity, include_archived=False)
    wanted = min(total, max_partners)
    records: list[SourceRecord] = []
    for offset in range(0, wanted, BATCH):
        records += src.read_page(
            p.entity, _requests(p), offset=offset, limit=min(BATCH, wanted - offset), include_archived=False
        )
    parent = p.get("parent_partner")
    children = [r for r in records if parent and isinstance(r.values.get(parent.field), Ref)]
    kept = [r for r in records if r not in children]

    countries = _related(src, plan.country, {_ref(r, p, "country") for r in kept})
    accounts = _related(src, plan.bank_account, {i for r in kept for i in _ids(r, p, "bank_accounts")})
    banks = _related(src, plan.bank, {_ref(a, plan.bank_account, "bank") for a in accounts.values()})

    partners: list[Partner] = []
    issues: list[PartnerIssue] = []
    for rec in kept:
        partner, problems = _partner(rec, plan, countries, accounts, banks)
        name = partner.name if partner else _text(_get(rec, p, "name"))
        issues += [PartnerIssue(source_ref=rec.id, name=name, message=msg) for msg in problems]
        if partner is not None:
            partners.append(partner)
    return PartnerReadResult(
        plan_checksum=plan.checksum,
        partners=partners,
        issues=issues,
        read=len(records),
        skipped_children=len(children),
        truncated=total > max_partners,
    )


def _partner(
    rec: SourceRecord,
    plan: PartnerReadPlan,
    countries: dict[str, SourceRecord],
    accounts: dict[str, SourceRecord],
    banks: dict[str, SourceRecord],
) -> tuple[Partner | None, list[str]]:
    p = plan.primary
    problems: list[str] = []
    name = _text(_get(rec, p, "name"))
    if name is None:
        return None, ["The partner has no name and cannot be used"]

    flag = _get(rec, p, "is_organization")
    kind = PartnerKind.UNKNOWN if flag is None else PartnerKind.ORGANIZATION if flag else PartnerKind.PERSON
    roles = [
        role
        for key, role in (("customer_rank", PartnerRole.CUSTOMER), ("supplier_rank", PartnerRole.SUPPLIER))
        if _positive(_get(rec, p, key))
    ]
    identifiers = [
        Identifier(scheme=scheme, value=value)
        for key, scheme in (
            ("customer_number", IdentifierScheme.CUSTOMER_NUMBER),
            ("supplier_number", IdentifierScheme.SUPPLIER_NUMBER),
            ("partner_reference", IdentifierScheme.PARTNER_REFERENCE),
            ("company_registry", IdentifierScheme.COMPANY_REGISTRY),
        )
        if (value := _text(_get(rec, p, key))) is not None
    ]

    country_code = None
    direct = _text(_get(rec, p, "country_code"))  # source à schéma fixe : code ISO sur le partenaire
    if direct is not None:
        if _COUNTRY.match(direct.upper()):
            country_code = direct.upper()
        else:
            problems.append("The country of the address has no ISO code and is left out")
    country_ref = _ref(rec, p, "country")
    if country_ref is not None and plan.country is not None:
        raw = (
            _text(_get(countries[country_ref], plan.country, "country_code"))
            if country_ref in countries
            else None
        )
        code = raw.upper() if raw else None
        if code and _COUNTRY.match(code):
            country_code = code
        else:
            problems.append("The country of the address has no ISO code and is left out")
    address = Address(
        street=_text(_get(rec, p, "street")),
        street2=_text(_get(rec, p, "street2")),
        po_box=_text(_get(rec, p, "po_box")),
        postal_code=_text(_get(rec, p, "postal_code")),
        city=_text(_get(rec, p, "city")),
        region=_label(rec, p, "region"),
        country_code=country_code,
    )

    language = None
    raw_lang = _text(_get(rec, p, "language"))
    if raw_lang:
        found = _LANG.match(raw_lang)
        if found:
            language = found.group(1).lower()
        else:
            problems.append("The language of the partner is not recognized and is left out")

    bank_accounts, bank_problems = _bank_accounts(rec, plan, accounts, banks)
    problems += bank_problems

    try:
        partner = Partner(
            source_ref=rec.id,
            kind=kind,
            name=name,
            organization_name=name if kind == PartnerKind.ORGANIZATION else None,
            person=PersonName(
                full_name=name,
                first_name=_text(_get(rec, p, "first_name")),  # seulement si la SOURCE les sépare
                last_name=_text(_get(rec, p, "last_name")),
            )
            if kind == PartnerKind.PERSON
            else None,
            short_name=_text(_get(rec, p, "short_name")),
            roles=roles,
            identifiers=identifiers,
            addresses=[] if address.is_empty() else [address],
            tax=TaxInfo(
                vat_id=_compact(_text(_get(rec, p, "vat_id"))), tax_number=_text(_get(rec, p, "tax_number"))
            ),
            contact=ContactInfo(
                email=_text(_get(rec, p, "email")),
                phone=_text(_get(rec, p, "phone")),
                mobile=_text(_get(rec, p, "mobile")),
                fax=_text(_get(rec, p, "fax")),
                website=_text(_get(rec, p, "website")),
            ),
            bank_accounts=bank_accounts,
            language=language,
            active=_get(rec, p, "active") is not False,
        )
    except ValidationError as exc:  # filet de sécurité : jamais d'exception pour un enregistrement
        return None, [f"The partner data is not usable ({exc.error_count()} problem(s))"]
    return partner, problems


def _bank_accounts(
    rec: SourceRecord,
    plan: PartnerReadPlan,
    accounts: dict[str, SourceRecord],
    banks: dict[str, SourceRecord],
) -> tuple[list[BankAccount], list[str]]:
    ap = plan.bank_account
    if ap is None:
        return [], []
    ids = [i for i in _ids(rec, plan.primary, "bank_accounts") if i in accounts]
    ordered = sorted(
        (accounts[i] for i in ids),
        key=lambda a: (_number(_get(a, ap, "bank_priority")) or 0, _int_id(a.id)),
    )
    out: list[BankAccount] = []
    problems: list[str] = []
    for a in ordered:
        number = _compact(_text(_get(a, ap, "bank_account_number")))
        if number is None:
            continue
        bank = banks.get(_ref(a, ap, "bank") or "")
        bic = _text(_get(bank, plan.bank, "bic")) if bank and plan.bank else None
        iban = number.upper() if is_valid_iban(number) else None
        if iban is None:
            problems.append("A bank account is not a valid IBAN: only its account number is kept")
        out.append(
            BankAccount(
                iban=iban,
                account_number=None if iban else number,
                bic=bic.upper() if bic else None,
                bank_name=_text(_get(bank, plan.bank, "bank_name")) if bank and plan.bank else None,
                holder_name=_text(_get(a, ap, "account_holder")),
                is_main=not out,
            )
        )
    return out, problems


# --------------------------------------------------------------------------- #
def _requests(ep: EntityPlan) -> list[FieldRequest]:
    return [FieldRequest(name=f.field, data_type=f.data_type) for f in ep.requested()]


def _related(src: CatalogSource, ep: EntityPlan | None, ids: set[str | None]) -> dict[str, SourceRecord]:
    wanted = sorted(i for i in ids if i)
    if ep is None or not wanted:
        return {}
    out: dict[str, SourceRecord] = {}
    for start in range(0, len(wanted), BATCH):
        for r in src.read(ep.entity, wanted[start : start + BATCH], _requests(ep)):
            out[r.id] = r
    return out


def _get(rec: SourceRecord | None, ep: EntityPlan | None, key: str) -> Any:
    if rec is None or ep is None:
        return None
    f = ep.get(key)
    return rec.values.get(f.field) if f else None


def _ref(rec: SourceRecord, ep: EntityPlan | None, key: str) -> str | None:
    v = _get(rec, ep, key)
    return v.id if isinstance(v, Ref) else None


def _label(rec: SourceRecord, ep: EntityPlan, key: str) -> str | None:
    v = _get(rec, ep, key)
    return _text(v.label) if isinstance(v, Ref) else _text(v)


def _ids(rec: SourceRecord, ep: EntityPlan, key: str) -> list[str]:
    v = _get(rec, ep, key)
    return [str(i) for i in v] if isinstance(v, list) else []


def _text(v: Any) -> str | None:
    if v is None or isinstance(v, bool):
        return None
    s = str(v).strip()
    return s or None


def _compact(v: str | None) -> str | None:
    return re.sub(r"\s+", "", v) if v else None


def _number(v: Any) -> float | None:
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None


def _positive(v: Any) -> bool:
    return v is True or (_number(v) or 0) > 0


def _int_id(v: str) -> tuple[int, str]:
    return (int(v), v) if v.isdigit() else (0, v)
