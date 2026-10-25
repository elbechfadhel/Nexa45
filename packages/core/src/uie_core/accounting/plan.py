"""Plan de lecture ACCOUNTING : QUOI lire, déduit du domaine ACCOUNTING et de ses semantic bindings.

Aucun nom de système : entités par RÔLE (DOCUMENT, DOCUMENT_LINE, LEDGER_ACCOUNT, TAX, CURRENCY), champs par
SEMANTIC KEY du vocabulaire ACCOUNTING, liens par clés (`lines`, `document`, `ledger_account`, `line_taxes`,
`line_tax`, `currency`). Seuls les bindings KNOWN / CONFIRMED ; même choix du champ que les autres domaines
(`choose_fields`). Les filtres (période, société, types de pièce) sont posés par le service sur les champs
de CE plan, jamais sur des noms de champs d'un système.
"""

from __future__ import annotations

import hashlib
import json
import uuid

from pydantic import BaseModel, ConfigDict

from uie_core.catalog.plan import BoundField, EntityPlan, KeySpec, choose_fields
from uie_core.domains.model import DomainEntityDef, DomainFieldDef, EntityRole

V = KeySpec("value")

ACCOUNTING_KEYS: dict[EntityRole, dict[str, KeySpec]] = {
    EntityRole.DOCUMENT: {
        "document_number": V,
        "document_reference": V,
        "document_type": V,
        "document_status": V,
        "partner": KeySpec("label"),
        "company": KeySpec("label"),
        "description": V,
        "accounting_date": V,
        "document_date": V,
        "due_date": V,
        "currency": KeySpec("link", EntityRole.CURRENCY),
        "total_amount": V,
        "tax_amount": V,
        "lines": KeySpec("collection", EntityRole.DOCUMENT_LINE),
    },
    EntityRole.DOCUMENT_LINE: {
        "document": KeySpec("link", EntityRole.DOCUMENT),
        "line_kind": V,
        "line_description": V,
        "ledger_account": KeySpec("link", EntityRole.LEDGER_ACCOUNT),
        "line_amount": V,
        "line_amount_currency": V,
        "line_gross_amount": V,
        "line_taxes": KeySpec("collection", EntityRole.TAX),
        "line_tax": KeySpec("link", EntityRole.TAX),
        "currency": KeySpec("link", EntityRole.CURRENCY),
    },
    EntityRole.LEDGER_ACCOUNT: {"account_code": V, "account_name": V},
    EntityRole.TAX: {
        "tax_name": V,
        "tax_rate": V,
        "tax_computation": V,
        "tax_usage": V,
        "tax_price_included": V,
    },
    EntityRole.CURRENCY: {"currency_code": V},
}

# clés sans lesquelles une pièce ne peut pas être lue (le plan est refusé, jamais deviné)
REQUIRED = {
    EntityRole.DOCUMENT: (
        "document_number",
        "document_type",
        "document_status",
        "accounting_date",
        "partner",
        "company",
        "currency",
        "lines",
    ),
    EntityRole.DOCUMENT_LINE: ("document", "line_kind", "ledger_account", "line_amount"),
    EntityRole.LEDGER_ACCOUNT: ("account_code",),
    EntityRole.TAX: ("tax_name",),
    EntityRole.CURRENCY: ("currency_code",),
}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AccountingReadPlan(_Model):
    domain_snapshot_id: uuid.UUID | None = None
    document: EntityPlan
    line: EntityPlan
    account: EntityPlan
    tax: EntityPlan
    currency: EntityPlan

    @property
    def checksum(self) -> str:
        data = self.model_dump(mode="json", exclude={"domain_snapshot_id"})
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


class AccountingPlanError(Exception):
    """Le domaine ACCOUNTING analysé ne permet pas de lire des pièces (clé indispensable non identifiée)."""


def build_accounting_plan(
    entities: list[DomainEntityDef],
    fields: list[DomainFieldDef],
    bindings: list[BoundField],
    *,
    domain_snapshot_id: uuid.UUID | None = None,
) -> AccountingReadPlan:
    by_role: dict[EntityRole, str] = {}
    for e in entities:
        if e.present:
            by_role.setdefault(e.role, e.technical_name)
    role_of = {name: role for role, name in by_role.items()}
    defs = {(f.entity_technical_name, f.field_technical_name): f for f in fields if f.present}
    usable = [b for b in bindings if (b.entity, b.field) in defs]

    plans: dict[EntityRole, EntityPlan] = {}
    for role, specs in ACCOUNTING_KEYS.items():
        entity = by_role.get(role)
        if entity is None:
            raise AccountingPlanError(f"No {role.value.lower()} entity identified in the accounting domain")
        chosen = choose_fields(specs, [b for b in usable if b.entity == entity], defs, role_of)
        missing = [k for k in REQUIRED[role] if k not in chosen]
        if missing:
            raise AccountingPlanError(
                f"Accounting data not identified ({role.value.lower()}): {', '.join(missing)}"
            )
        plans[role] = EntityPlan(entity=entity, role=role, fields=chosen)
    return AccountingReadPlan(
        domain_snapshot_id=domain_snapshot_id,
        document=plans[EntityRole.DOCUMENT],
        line=plans[EntityRole.DOCUMENT_LINE],
        account=plans[EntityRole.LEDGER_ACCOUNT],
        tax=plans[EntityRole.TAX],
        currency=plans[EntityRole.CURRENCY],
    )
