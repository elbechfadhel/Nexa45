"""Lecture Lexware Office du contrat générique `RecordSource` (source à schéma fixe, phases source 1a / 1b.1).

Ce module sait LIRE des pages de l'API, pas ce que les données veulent dire : le sens des champs est dans un
binding de source (données). Pièces comptables : liste `voucherlist` (types que sert `vouchers`, tous
statuts, filtre de dates de l'API) puis détail `vouchers/{id}` de chaque pièce, fusionné (le détail prime).
Client en LECTURE SEULE (`GET`, jamais `writable`) ; pages de `PAGE_SIZE` jusqu'à la dernière ou jusqu'au
plafond demandé ; erreurs du transport → `RecordSourceError` classée, sans corps de réponse ni clé."""

from __future__ import annotations

from typing import Any

from uie_core.instances import ConnectionContext
from uie_core.sources import (
    RecordBatch,
    RecordPeriod,
    RecordSource,
    RecordSourceError,
    RecordSourceReader,
    RecordSourceRegistry,
)

from uie_connectors.errors import (
    AuthenticationError,
    AuthorizationError,
    ConnectorError,
    NetworkError,
    RateLimitError,
    RemoteTimeoutError,
)
from uie_connectors.lexware.client import API_VERSION, LexwareClient

PAGE_SIZE = 100
# Ressources listées par pages (`content`, `last`) et paramètres OBLIGATOIRES de leur liste
LIST_PARAMS: dict[str, dict[str, str]] = {
    "contacts": {},
    # types de pièces de comptabilité servis par `vouchers/{id}`, tous statuts (le domaine trie)
    "voucherlist": {
        "voucherType": "salesinvoice,salescreditnote,purchaseinvoice,purchasecreditnote",
        "voucherStatus": "any",
    },
}
# Ressources rendues en UNE liste simple (sans pages), lues pour des libellés (ex. noms des catégories)
PLAIN_LISTS = frozenset({"posting-categories"})
# Filtres de période de l'API : (liste, chemin de la date) → (paramètre début, paramètre fin)
PERIOD_PARAMS = {("voucherlist", "voucherDate"): ("voucherDateFrom", "voucherDateTo")}
DETAILS = frozenset({"vouchers"})  # ressources relues par identifiant


def _error(exc: ConnectorError) -> RecordSourceError:
    if isinstance(exc, AuthenticationError | AuthorizationError):
        return RecordSourceError("access refused by the system (API key)", kind="ACCESS")
    if isinstance(exc, RateLimitError):
        return RecordSourceError("too many requests, try again later", kind="RATE_LIMITED")
    if isinstance(exc, NetworkError | RemoteTimeoutError):
        return RecordSourceError("the system cannot be reached", kind="UNREACHABLE")
    return RecordSourceError("unexpected answer from the system", kind="UNEXPECTED")


class LexwareRecordSource(RecordSource):
    def __init__(self, client: LexwareClient) -> None:
        self.client = client

    def close(self) -> None:
        self.client.close()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            return self.client.get(path, params)
        except ConnectorError as exc:
            raise _error(exc) from None

    def records(
        self,
        resource: str,
        *,
        limit: int,
        period: RecordPeriod | None = None,
        detail: str | None = None,
    ) -> RecordBatch:
        if resource in PLAIN_LISTS and period is None and detail is None:
            data = self._get(f"/{API_VERSION}/{resource}")
            if not isinstance(data, list):
                raise RecordSourceError("unexpected answer from the system")
            rows = [r for r in data if isinstance(r, dict)]
            return RecordBatch(records=rows[:limit], truncated=len(rows) > limit)
        if resource not in LIST_PARAMS:
            raise RecordSourceError(f"reading {resource!r} is not supported yet")
        if detail is not None and detail not in DETAILS:
            raise RecordSourceError(f"reading {detail!r} by id is not supported yet")
        params: dict[str, Any] = dict(LIST_PARAMS[resource])
        if period is not None:
            names = PERIOD_PARAMS.get((resource, period.path))
            if names is None:  # jamais une lecture non filtrée en silence
                raise RecordSourceError(f"{resource!r} cannot be filtered on {period.path!r}")
            params |= {names[0]: period.start.isoformat(), names[1]: period.end.isoformat()}
        out: list[dict[str, Any]] = []
        truncated = False
        page = 0
        while not truncated:
            data = self._get(f"/{API_VERSION}/{resource}", {**params, "page": page, "size": PAGE_SIZE})
            if not isinstance(data, dict) or not isinstance(data.get("content"), list):
                raise RecordSourceError("unexpected answer from the system")
            for item in data["content"]:
                if isinstance(item, dict):
                    if len(out) >= limit:
                        truncated = True
                        break
                    out.append(item)
            if data.get("last", True) or not data["content"]:
                break
            page += 1
        if detail is not None:
            out = [self._merged(item, detail) for item in out]
        return RecordBatch(records=out, truncated=truncated)

    def _merged(self, item: dict[str, Any], detail: str) -> dict[str, Any]:
        rid = item.get("id")
        if not rid:
            return item
        full = self._get(f"/{API_VERSION}/{detail}/{rid}")
        if not isinstance(full, dict):
            raise RecordSourceError("unexpected answer from the system")
        return {**item, **full}  # le détail prime (ex. statut de la pièce plutôt que l'état de paiement)


@RecordSourceRegistry.register
class LexwareRecordSourceReader(RecordSourceReader):
    system_code = "lexware"
    interface_code = "rest"

    def open(self, ctx: ConnectionContext) -> RecordSource:
        if ctx.secret is None or not ctx.secret.get_secret_value():
            raise RecordSourceError("API key missing", kind="ACCESS")
        return LexwareRecordSource(LexwareClient(ctx.secret))  # lecture seule (GET)
