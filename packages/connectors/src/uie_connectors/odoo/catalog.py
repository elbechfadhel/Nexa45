"""Lecteur de catalogue Odoo (V2a) : implémente le contrat `CatalogReader` du Core, en LECTURE SEULE.

Il sait lire des enregistrements Odoo, pas ce qu'ils veulent dire : entités, champs et liens lui sont donnés
par le plan de lecture du Core (semantic bindings). Même connexion que la discovery (authentification,
`ReadOnlyObjectProxy` : search_read / search_count / read seulement).

Particularités Odoo traduites ici (jamais dans le Core) :
    référence  [id, "nom"] | False     → Ref(id, label) | None
    vide       False (hors booléen)     → None
    binaire    contexte bin_size        → présence (bool) ; contenu base64 → octets par read_binary
    archivés   contexte active_test=False quand include_archived
"""

from __future__ import annotations

import base64
import binascii
from typing import Any

from uie_core.catalog import (
    CatalogReader,
    CatalogReaderRegistry,
    CatalogReadError,
    CatalogSource,
    Condition,
    FieldRequest,
    Ref,
    SourceRecord,
)
from uie_core.instances import ConnectionContext, redact

from uie_connectors.odoo.discovery import OdooSchemaDiscoveryProvider
from uie_connectors.odoo.metadata import XmlRpcMetadataSource

LABEL_FIELD = "display_name"


@CatalogReaderRegistry.register
class OdooCatalogReader(CatalogReader):
    system_code = "odoo"

    def __init__(self, proxy_factory: Any = None) -> None:
        self._provider = OdooSchemaDiscoveryProvider(proxy_factory=proxy_factory)

    def open(self, ctx: ConnectionContext) -> CatalogSource:
        secret = ctx.secret.get_secret_value() if ctx.secret else None
        try:
            source = self._provider._source(ctx)  # même connexion lecture seule que la discovery
            source.login()
        except Exception as exc:  # noqa: BLE001 — refus, réseau, Fault XML-RPC : jamais de secret
            raise CatalogReadError(redact(f"{type(exc).__name__}: {exc}", [secret]) or "") from None
        return OdooCatalogSource(source, secret)


class OdooCatalogSource(CatalogSource):
    def __init__(self, source: XmlRpcMetadataSource, secret: str | None) -> None:
        self._src = source
        self._secret = secret

    # ------------------------------------------------------------------ #
    def count(
        self, entity: str, where: list[Condition] | None = None, *, include_archived: bool = True
    ) -> int:
        return int(self._call(entity, "search_count", [_domain(where)], {"context": _ctx(include_archived)}))

    def read_page(
        self,
        entity: str,
        fields: list[FieldRequest],
        *,
        where: list[Condition] | None = None,
        offset: int = 0,
        limit: int = 200,
        include_archived: bool = True,
    ) -> list[SourceRecord]:
        rows = self._call(
            entity,
            "search_read",
            [_domain(where)],
            {
                "fields": _names(fields),
                "offset": offset,
                "limit": limit,
                "order": "id asc",
                "context": _ctx(include_archived, bin_size=True),
            },
        )
        return [_record(r, fields) for r in rows]

    def read(
        self, entity: str, ids: list[str], fields: list[FieldRequest], *, include_archived: bool = True
    ) -> list[SourceRecord]:
        if not ids:
            return []
        rows = self._call(
            entity,
            "read",
            [[_id(i) for i in ids]],
            {"fields": _names(fields), "context": _ctx(include_archived, bin_size=True)},
        )
        return [_record(r, fields) for r in rows]

    def read_binary(self, entity: str, record_id: str, field: str) -> bytes | None:
        rows = self._call(entity, "read", [[_id(record_id)]], {"fields": [field], "context": _ctx(True)})
        value = rows[0].get(field) if rows else None
        if not value:
            return None
        try:
            return base64.b64decode(value)
        except (binascii.Error, TypeError) as exc:
            raise CatalogReadError(f"Image illisible : {type(exc).__name__}") from None

    # ------------------------------------------------------------------ #
    def _call(self, entity: str, method: str, args: list[Any], kw: dict[str, Any]) -> Any:
        try:
            return self._src.call(entity, method, args, kw)
        except Exception as exc:  # noqa: BLE001 — jamais de secret dans le message
            raise CatalogReadError(redact(f"{type(exc).__name__}: {exc}", [self._secret]) or "") from None


def _ctx(include_archived: bool, *, bin_size: bool = False) -> dict[str, Any]:
    ctx: dict[str, Any] = {}
    if include_archived:
        ctx["active_test"] = False
    if bin_size:
        ctx["bin_size"] = True  # binaires : taille au lieu du contenu (présence seulement)
    return ctx


def _id(value: str) -> int | str:
    return int(value) if value.isdigit() else value


def _domain(where: list[Condition] | None) -> list[Any]:
    """Conditions génériques → domaine Odoo : in → « in », eq → « = », between → « >= » ET « <= »."""
    out: list[Any] = []
    for c in where or []:
        if c.op == "in":
            out.append([c.field, "in", [_id(v) for v in c.values]])
        elif c.op == "eq":
            out.append([c.field, "=", _typed(c.values[0], c.data_type)])
        else:
            low, high = (_typed(v, c.data_type) for v in c.values)
            out += [[c.field, ">=", low], [c.field, "<=", high]]
    return out


def _typed(value: str, data_type: str | None) -> Any:
    """Valeur d'une condition au type attendu par Odoo : référence / entier → identifiant entier, booléen,
    nombre ; sinon texte (dates ISO « AAAA-MM-JJ » comprises)."""
    if data_type in ("reference", "collection", "integer"):
        return _id(value)
    if data_type == "boolean":
        return value.lower() in ("1", "true")
    if data_type == "decimal":
        return float(value)
    return value


def _names(fields: list[FieldRequest]) -> list[str]:
    return sorted({f.name for f in fields} | {LABEL_FIELD})


def _value(raw: Any, data_type: str) -> Any:
    if data_type == "boolean":
        return bool(raw)
    if data_type == "binary":
        return bool(raw)
    if raw is False or raw is None:
        return None
    if data_type == "reference":
        if isinstance(raw, list | tuple) and raw:
            return Ref(id=str(raw[0]), label=str(raw[1]) if len(raw) > 1 and raw[1] else None)
        return Ref(id=str(raw))
    if data_type == "collection":
        return [str(x) for x in raw] if isinstance(raw, list | tuple) else []
    return raw


def _record(row: dict[str, Any], fields: list[FieldRequest]) -> SourceRecord:
    label = row.get(LABEL_FIELD)
    return SourceRecord(
        id=str(row["id"]),
        label=str(label) if label else None,
        values={f.name: _value(row.get(f.name, False), f.data_type) for f in fields},
    )
