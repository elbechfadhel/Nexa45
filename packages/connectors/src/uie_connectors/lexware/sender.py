"""Expéditeur Lexware Office du contrat générique `RecordSender` (contacts, pièces, articles).

Ce module sait PARLER à l'API, pas ce que les données veulent dire ni d'où elles viennent :
- création : `POST /v1/<ressource>` avec `version: 0` (exigé par l'API à la création des contacts et des
  articles) ;
- mise à jour SANS rien effacer : relecture, contrôle de la version connue (sinon CONFLICT : la cible a été
  modifiée depuis le dernier envoi), fusion de la requête dans l'enregistrement relu (objets fusionnés,
  listes remplacées), `PUT` avec la version relue ; ce que la requête ne porte pas reste tel quel ;
- valeurs vides renvoyées par l'API (`""`) traitées comme absentes ;
- rapprochement : contacts par nom (filtre `name`) et par e-mail (filtre `email`), puis égalité stricte
  normalisée (casse, espaces) ; pièces et articles par leur numéro (`voucherNumber`, `articleNumber` = SKU),
  égalité exacte : jamais de fusion, seulement des candidats ;
- erreurs du transport → `SendError` classée ; une écriture sans réponse sûre (délai, 504, 5xx, réseau) =
  `UNKNOWN_OUTCOME`, jamais retentée ici.

Le client d'écriture n'est créé qu'ici (`writable=True`) ; la clé ne sort jamais du `SecretStr`."""

from __future__ import annotations

import copy
from typing import Any

from uie_core.delivery import (
    RecordSender,
    RecordSenderRegistry,
    RemoteRecord,
    SendError,
    SendErrorKind,
    TargetChoice,
)
from uie_core.instances import ConnectionContext

from uie_connectors.errors import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    ConnectorError,
    NetworkError,
    NotFoundError,
    RateLimitError,
    RemoteServerError,
    RemoteTimeoutError,
    ValidationError,
)
from uie_connectors.lexware.client import API_VERSION, LexwareClient

READ_ONLY_KEYS = ("id", "organizationId", "archived", "createdDate", "updatedDate")  # jamais renvoyés
# Ressources dont la création porte `version: 0` (contacts, articles) ; une pièce comptable se crée sans
# version (exemple de création de la documentation officielle).
VERSIONED_CREATE = frozenset({"contacts", "articles"})
# ressource → champ d'identité métier (recherche de doublons par le filtre de liste de l'API)
NUMBERED = {"vouchers": "voucherNumber", "articles": "articleNumber"}
# Prix d'un article : seul le prix DIRECTEUR est envoyé, l'API calcule l'autre ; à la mise à jour,
# l'autre prix relu (calculé sur l'ancien montant) est retiré pour ne pas contredire le nouveau.
DERIVED_PRICE = {"articles": ("price", "leadingPrice", {"NET": "grossPrice", "GROSS": "netPrice"})}
# Pièces d'ACHAT : le numéro est celui du FOURNISSEUR (deux fournisseurs peuvent avoir le même) → un doublon
# est le même numéro ET le même contact ; sans contact comparable, la correspondance reste proposée.
NUMBER_PER_CONTACT_TYPES = ("purchase",)
PAGE_SIZE = 25
MIN_NAME_FILTER = 3  # longueur minimale du filtre `name` de l'API


def _kind(exc: ConnectorError, *, write: bool) -> SendErrorKind:
    if isinstance(exc, AuthenticationError | AuthorizationError):
        return SendErrorKind.ACCESS
    if isinstance(exc, RateLimitError):
        return SendErrorKind.RATE_LIMITED
    if isinstance(exc, ConflictError):
        return SendErrorKind.CONFLICT
    if isinstance(exc, NotFoundError):
        return SendErrorKind.NOT_FOUND
    if isinstance(exc, ValidationError):
        return SendErrorKind.REJECTED
    if isinstance(exc, RemoteTimeoutError | RemoteServerError | NetworkError):
        return SendErrorKind.UNKNOWN_OUTCOME if write else SendErrorKind.UNAVAILABLE
    return SendErrorKind.UNKNOWN_OUTCOME if write else SendErrorKind.UNAVAILABLE


def _error(exc: ConnectorError, *, write: bool) -> SendError:
    return SendError(
        _kind(exc, write=write),
        f"{type(exc).__name__}: {exc}",  # déjà sans secret ni corps brut (client)
        status=exc.status,
        trace_id=exc.trace_id,
        fields=tuple(exc.fields),
    )


def _clean(value: Any) -> Any:
    """Sans les chaînes vides que l'API renvoie pour une valeur absente."""
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items() if v != "" and v is not None}
    if isinstance(value, list):
        return [_clean(v) for v in value if v != "" and v is not None]
    return value


def merge(current: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    """Requête fusionnée dans l'enregistrement relu : objets fusionnés récursivement, autres valeurs
    (listes comprises) remplacées. Rien de ce que la requête ne porte n'est retiré."""
    out = copy.deepcopy(current)
    for key, value in request.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _norm(text: str | None) -> str:
    return " ".join((text or "").split()).casefold()


def _label(record: dict[str, Any]) -> str | None:
    company = record.get("company") or {}
    if company.get("name"):
        return str(company["name"])
    person = record.get("person") or {}
    name = " ".join(p for p in (person.get("firstName"), person.get("lastName")) if p)
    return name or None


def _emails(record: dict[str, Any]) -> set[str]:
    groups = record.get("emailAddresses") or {}
    return {_norm(e) for values in groups.values() if isinstance(values, list) for e in values if e}


def _remote(record: dict[str, Any]) -> RemoteRecord:
    version = record.get("version")
    return RemoteRecord(
        id=str(record["id"]),
        version=version if isinstance(version, int) else None,
        label=_label(record),
        archived=bool(record.get("archived")),
    )


@RecordSenderRegistry.register
class LexwareRecordSender(RecordSender):
    system_code = "lexware"
    interface_code = "rest"

    def __init__(self, ctx: ConnectionContext, client: LexwareClient | None = None) -> None:
        super().__init__(ctx)
        if client is None:
            if ctx.secret is None or not ctx.secret.get_secret_value():
                raise SendError(SendErrorKind.ACCESS, "API key missing")
            client = LexwareClient(ctx.secret, writable=True)
        self.client = client

    def close(self) -> None:
        self.client.close()

    @staticmethod
    def _path(resource: str, target_id: str | None = None) -> str:
        base = f"/{API_VERSION}/{resource}"
        return f"{base}/{target_id}" if target_id else base

    def _read(self, path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            return self.client.get(path, params=params)
        except ConnectorError as exc:
            raise _error(exc, write=False) from None

    def _write(self, method: str, path: str, body: dict[str, Any]) -> RemoteRecord:
        try:
            out = self.client.request(method, path, json=body)
        except ConnectorError as exc:
            raise _error(exc, write=True) from None
        if not isinstance(out, dict) or not out.get("id"):
            # écrit peut-être, mais sans identifiant lisible : issue inconnue
            raise SendError(SendErrorKind.UNKNOWN_OUTCOME, "Response without identifier")
        return _remote(out)

    # ------------------------------------------------------------------ #
    def create(self, resource: str, body: dict[str, Any]) -> RemoteRecord:
        payload = {**body, "version": 0} if resource in VERSIONED_CREATE else dict(body)
        return self._write("POST", self._path(resource), payload)

    def get(self, resource: str, target_id: str) -> RemoteRecord:
        record = self._read(self._path(resource, target_id))
        if not isinstance(record, dict) or not record.get("id"):
            raise SendError(SendErrorKind.UNAVAILABLE, "Unreadable record")
        return _remote(record)

    def update(
        self, resource: str, target_id: str, expected_version: int | None, body: dict[str, Any]
    ) -> RemoteRecord:
        current = self._read(self._path(resource, target_id))
        if not isinstance(current, dict) or not isinstance(current.get("version"), int):
            raise SendError(SendErrorKind.UNAVAILABLE, "Unreadable record")
        if expected_version is not None and current["version"] != expected_version:
            raise SendError(SendErrorKind.CONFLICT, "Changed in the target system since the last send")
        merged = merge(_clean(current), body)
        for key in READ_ONLY_KEYS:
            merged.pop(key, None)
        if resource in DERIVED_PRICE:
            group, leading, derived = DERIVED_PRICE[resource]
            prices = merged.get(group)
            if isinstance(prices, dict) and prices.get(leading) in derived:
                prices.pop(derived[prices[leading]], None)
        merged["version"] = current["version"]
        return self._write("PUT", self._path(resource, target_id), merged)

    def find_candidates(self, resource: str, body: dict[str, Any]) -> list[RemoteRecord]:
        if resource in NUMBERED:
            return self._same_number(resource, NUMBERED[resource], body)
        name = _label(body)
        emails = _emails(body)
        found: dict[str, dict[str, Any]] = {}
        queries: list[dict[str, Any]] = []
        if name and len(name.strip()) >= MIN_NAME_FILTER:
            queries.append({"name": name.strip()})
        queries += [{"email": e} for e in sorted(emails)]
        for params in queries:
            page = self._read(self._path(resource), {**params, "page": 0, "size": PAGE_SIZE})
            for record in (page or {}).get("content") or []:
                if isinstance(record, dict) and record.get("id"):
                    found[str(record["id"])] = record
        out = []
        for record in found.values():
            same_name = name is not None and _norm(_label(record)) == _norm(name)
            same_email = bool(emails & _emails(record))
            if same_name or same_email:
                out.append(_remote(record))
        return sorted(out, key=lambda r: (r.archived, r.label or "", r.id))

    def _same_number(self, resource: str, field: str, body: dict[str, Any]) -> list[RemoteRecord]:
        """Enregistrements portant EXACTEMENT le même numéro (filtre de l'API, puis égalité stricte)."""
        number = body.get(field)
        if not number:
            return []
        page = self._read(self._path(resource), {field: number})
        records = (page or {}).get("content") if isinstance(page, dict) else page
        per_contact = str(body.get("type") or "").startswith(NUMBER_PER_CONTACT_TYPES)
        contact = body.get("contactId")
        out = []
        for r in records or []:
            if not (isinstance(r, dict) and r.get("id") and r.get(field) == number):
                continue
            other = r.get("contactId")
            if per_contact and contact and other and other != contact:
                continue  # même numéro chez un AUTRE contact : une autre pièce, pas un doublon
            # même contact, ou contact inconnu d'un côté (ambigu) : toujours proposé, jamais ignoré
            label = f"{number} – {r['title']}" if r.get("title") else str(number)  # article : n° et titre
            out.append(_remote(r).model_copy(update={"label": label}))
        return out

    def options(self, resource: str) -> list[TargetChoice]:
        """Liste d'une ressource de référence (ex. catégories de comptabilisation) : GET seulement."""
        records = self._read(self._path(resource))
        if not isinstance(records, list):
            raise SendError(SendErrorKind.UNAVAILABLE, "Unreadable list")
        return [
            TargetChoice(
                id=str(r["id"]),
                label=str(r.get("name") or r["id"]),
                group=r.get("groupName") or None,
                kind=r.get("type") or None,
            )
            for r in records
            if isinstance(r, dict) and r.get("id")
        ]
