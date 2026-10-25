"""Plan de lecture PARTNER : QUOI lire, déduit du domaine PARTNER et de ses semantic bindings.

Aucun nom de système : entités par RÔLE (PRIMARY, BANK_ACCOUNT, BANK, COUNTRY), champs par SEMANTIC KEY du
vocabulaire PARTNER, liens par clés (`country` → COUNTRY, `bank_accounts` → BANK_ACCOUNT, `bank` → BANK).
Seuls les bindings KNOWN / CONFIRMED : un champ exclu, ignoré ou en question n'est jamais lu ; une extension
confirmée par l'utilisateur (ex. un numéro de client) est lue sous sa clé. Même choix déterministe du champ
de chaque clé que le catalogue (`choose_fields`).
"""

from __future__ import annotations

import hashlib
import json
import uuid

from pydantic import BaseModel, ConfigDict

from uie_core.catalog.plan import BoundField, EntityPlan, KeySpec, choose_fields
from uie_core.domains.model import DomainEntityDef, DomainFieldDef, EntityRole

V = KeySpec("value")

PARTNER_KEYS: dict[EntityRole, dict[str, KeySpec]] = {
    EntityRole.PRIMARY: {
        "name": V,
        "is_organization": V,
        "short_name": V,
        "partner_reference": V,
        "customer_number": V,
        "supplier_number": V,
        "company_registry": V,
        "active": V,
        "parent_partner": KeySpec("link", EntityRole.PRIMARY),
        "customer_rank": V,
        "supplier_rank": V,
        "street": V,
        "street2": V,
        "po_box": V,
        "postal_code": V,
        "city": V,
        "region": KeySpec("label"),
        "country": KeySpec("link", EntityRole.COUNTRY),
        "email": V,
        "phone": V,
        "mobile": V,
        "fax": V,
        "website": V,
        "language": V,
        "vat_id": V,
        "tax_number": V,
        "bank_accounts": KeySpec("collection", EntityRole.BANK_ACCOUNT),
    },
    EntityRole.BANK_ACCOUNT: {
        "bank_account_number": V,
        "account_holder": V,
        "bank_priority": V,
        "bank": KeySpec("link", EntityRole.BANK),
    },
    EntityRole.BANK: {"bic": V, "bank_name": V},
    EntityRole.COUNTRY: {"country_code": V},
}

# Clés supplémentaires d'une SOURCE À SCHÉMA FIXE (binding de source, jamais une analyse sémantique) : code
# pays ISO porté par le partenaire lui-même, prénom et nom séparés PAR LA SOURCE. Absentes du vocabulaire et
# de PARTNER_KEYS : les plans des sources analysées (et leurs empreintes) ne changent pas.
FIXED_PRIMARY_KEYS: dict[str, KeySpec] = {
    **PARTNER_KEYS[EntityRole.PRIMARY],
    "country_code": V,
    "first_name": V,
    "last_name": V,
}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PartnerReadPlan(_Model):
    domain_snapshot_id: uuid.UUID | None = None
    source_checksum: str | None = None  # binding de source à schéma fixe (provenance du plan)
    primary: EntityPlan
    bank_account: EntityPlan | None = None
    bank: EntityPlan | None = None
    country: EntityPlan | None = None

    @property
    def checksum(self) -> str:
        # sans binding de source : même empreinte qu'avant l'ajout du champ (plans des sources analysées)
        skip = {"domain_snapshot_id"} | ({"source_checksum"} if self.source_checksum is None else set())
        data = self.model_dump(mode="json", exclude=skip)
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


class PartnerPlanError(Exception):
    """Le domaine analysé ne permet pas de lire des partenaires (ex. aucun nom identifié)."""


def build_partner_plan(
    entities: list[DomainEntityDef],
    fields: list[DomainFieldDef],
    bindings: list[BoundField],
    *,
    domain_snapshot_id: uuid.UUID | None = None,
) -> PartnerReadPlan:
    by_role: dict[EntityRole, str] = {}
    for e in entities:
        if e.present:
            by_role.setdefault(e.role, e.technical_name)
    role_of = {name: role for role, name in by_role.items()}
    defs = {(f.entity_technical_name, f.field_technical_name): f for f in fields if f.present}
    usable = [b for b in bindings if (b.entity, b.field) in defs]

    def plan_for(role: EntityRole) -> EntityPlan | None:
        entity = by_role.get(role)
        if entity is None:
            return None
        chosen = choose_fields(PARTNER_KEYS[role], [b for b in usable if b.entity == entity], defs, role_of)
        return EntityPlan(entity=entity, role=role, fields=chosen) if chosen else None

    primary = plan_for(EntityRole.PRIMARY)
    if primary is None or primary.get("name") is None:
        raise PartnerPlanError("No partner name identified in the analyzed partner domain")
    bank_account = plan_for(EntityRole.BANK_ACCOUNT) if primary.get("bank_accounts") else None
    bank = plan_for(EntityRole.BANK) if bank_account and bank_account.get("bank") else None
    country = plan_for(EntityRole.COUNTRY) if primary.get("country") else None
    return PartnerReadPlan(
        domain_snapshot_id=domain_snapshot_id,
        primary=primary,
        bank_account=bank_account,
        bank=bank,
        country=country,
    )
