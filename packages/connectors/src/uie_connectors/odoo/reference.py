"""Valeurs de référence d'un Odoo connecté (contrat `ReferenceLookup` du Wizard) : LECTURE SEULE.

Même connexion que la discovery (proxy lecture seule). Une colonne « champ/id » désigne un enregistrement par
son identifiant externe : les identifiants sont lus dans `ir.model.data` (jamais composés) ; un enregistrement
sans identifiant externe n'est pas proposé. Sans attribut, la valeur est le nom affiché de
l'enregistrement."""

from __future__ import annotations

from typing import Any

from uie_core.instances import ConnectionContext
from uie_core.wizard.lookup import MAX_OPTIONS, ReferenceLookup, ReferenceLookupRegistry, ReferenceOption

from uie_connectors.odoo.discovery import OdooSchemaDiscoveryProvider

EXTERNAL_ID = "id"


@ReferenceLookupRegistry.register
class OdooReferenceLookup(ReferenceLookup):
    system_code = "odoo"

    def __init__(self, provider: OdooSchemaDiscoveryProvider | None = None) -> None:
        self._provider = provider or OdooSchemaDiscoveryProvider()

    def company_currency(self, ctx: ConnectionContext) -> str | None:
        """Devise de la société de l'utilisateur connecté (code ISO du `res.currency`) ; lecture seule."""
        source = self._provider._source(ctx)
        users: list[dict[str, Any]] = source.call(
            "res.users",
            "search_read",
            [[["login", "=", ctx.config.get("username")]]],
            {"fields": ["company_id"], "limit": 1},
        )
        company = users[0].get("company_id") if users else None
        domain = [["id", "=", company[0]]] if isinstance(company, list) and company else []
        companies: list[dict[str, Any]] = source.call(
            "res.company", "search_read", [domain], {"fields": ["currency_id"], "limit": 1, "order": "id"}
        )
        currency = companies[0].get("currency_id") if companies else None
        return str(currency[1]) if isinstance(currency, list) and len(currency) > 1 else None

    def options(
        self, ctx: ConnectionContext, entity: str, attribute: str | None, limit: int = MAX_OPTIONS
    ) -> list[ReferenceOption]:
        source = self._provider._source(ctx)  # même connexion lecture seule que la discovery
        rows: list[dict[str, Any]] = source.call(
            entity, "search_read", [[]], {"fields": ["display_name"], "limit": limit, "order": "id"}
        )
        labels = {int(r["id"]): str(r.get("display_name") or r["id"]) for r in rows}
        if attribute != EXTERNAL_ID:
            return [ReferenceOption(value=label, label=label) for label in labels.values()]
        if not labels:
            return []
        xmlids: list[dict[str, Any]] = source.call(
            "ir.model.data",
            "search_read",
            [[["model", "=", entity], ["res_id", "in", list(labels)]]],
            {"fields": ["module", "name", "res_id"]},
        )
        out: dict[int, ReferenceOption] = {}
        for x in xmlids:
            rid = int(x["res_id"])
            if rid in labels and rid not in out:
                out[rid] = ReferenceOption(value=f"{x['module']}.{x['name']}", label=labels[rid])
        return [out[rid] for rid in labels if rid in out]
