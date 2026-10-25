"""Client HTTP de l'API publique Lexware Office : transport seulement, AUCUNE logique métier.

- adresse officielle fixe (`API_BASE_URL`) : elle appartient au connecteur, jamais à l'utilisateur ;
- authentification `Authorization: Bearer <clé>` ; la clé reste un SecretStr, n'est jamais journalisée ni
  recopiée dans une erreur (`redact`) ;
- limite de débit de l'API respectée par un seau à jetons partagé par clé (`ratelimit`) ;
- 429 : attente indiquée par `Retry-After` si présent, sinon attente croissante BORNÉE, puis abandon ;
- réponses d'erreur traduites dans les erreurs génériques de `uie_connectors.errors` ; le corps brut n'est
  jamais remonté (seulement les noms des champs en cause et l'identifiant de trace).

LECTURE SEULE par défaut : seule la méthode GET est autorisée (`ALLOWED_METHODS`) ; toute autre méthode est
refusée AVANT tout appel réseau (`ReadOnlyViolation`). Phase 1b : un client construit EXPLICITEMENT avec
`writable=True` (expéditeur seulement) accepte aussi POST et PUT (`WRITE_METHODS`) ; DELETE jamais."""

from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Callable
from typing import Any

import httpx
from pydantic import SecretStr
from uie_core.instances import redact

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
from uie_connectors.lexware.ratelimit import RATE_LIMIT_PER_SECOND, TokenBucket, shared_bucket

API_BASE_URL = "https://api.lexware.io"  # passerelle officielle de l'API publique
API_VERSION = "v1"  # version de ROUTE (segment d'adresse), distincte de la révision du contrat du Registry
BASE_URL_ENV = "UIE_LEXWARE_API_URL"  # DEV / tests seulement : faux serveur local
TIMEOUT_SECONDS = 30.0  # délai de la passerelle officielle
MAX_RETRIES_429 = 3
BACKOFF_SECONDS = (1.0, 2.0, 4.0)
RETRY_AFTER_CAP = 30.0  # une attente demandée plus longue est un échec, pas une requête bloquée
ALLOWED_METHODS = frozenset({"GET"})
WRITE_METHODS = frozenset({"POST", "PUT"})  # jamais DELETE : aucune suppression dans un système client

_ERRORS: dict[int, type[ConnectorError]] = {
    400: ValidationError,
    401: AuthenticationError,
    402: AuthorizationError,  # action hors du contrat du compte
    403: AuthorizationError,
    404: NotFoundError,
    405: ValidationError,
    406: ValidationError,  # validation des données chez ce système
    409: ConflictError,
    415: ValidationError,
    429: RateLimitError,
}


class ReadOnlyViolation(PermissionError):
    """Méthode HTTP refusée : le connecteur est en lecture seule."""


def api_base_url() -> str:
    """Adresse de l'API : l'officielle, sauf surcharge explicite du serveur (développement, tests)."""
    return (os.environ.get(BASE_URL_ENV) or API_BASE_URL).rstrip("/")


class LexwareClient:
    def __init__(
        self,
        api_key: SecretStr,
        *,
        base_url: str | None = None,
        transport: httpx.BaseTransport | None = None,
        bucket: TokenBucket | None = None,
        timeout: float = TIMEOUT_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        writable: bool = False,
    ) -> None:
        self._key = api_key
        self._methods = ALLOWED_METHODS | WRITE_METHODS if writable else ALLOWED_METHODS
        self.base_url = (base_url or api_base_url()).rstrip("/")
        self._sleep = sleep
        # un seau par clé (empreinte de la clé, jamais la clé) : la limite vaut pour un client de l'API
        self._bucket = bucket or shared_bucket(
            hashlib.sha256(api_key.get_secret_value().encode()).hexdigest()
        )
        self._http = httpx.Client(transport=transport, timeout=timeout, follow_redirects=False)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> LexwareClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET d'une ressource (`/v1/...`) → JSON décodé."""
        return self.request("GET", path, params=params)

    def request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None, json: Any = None
    ) -> Any:
        if method.upper() not in self._methods:
            raise ReadOnlyViolation(f"Méthode {method} refusée : connecteur en lecture seule")
        url = f"{self.base_url}/{path.lstrip('/')}"
        attempt = 0
        while True:
            self._bucket.acquire()
            response = self._send(method.upper(), url, params, json)
            if response.status_code != 429:
                return self._decode(response)
            wait = self._wait_after_429(response, attempt)
            if wait is None:
                raise RateLimitError("Limite de débit atteinte malgré les attentes", status=429)
            self._sleep(wait)
            attempt += 1

    # ------------------------------------------------------------------ #
    def _send(self, method: str, url: str, params: dict[str, Any] | None, json: Any = None) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {self._key.get_secret_value()}",
            "Accept": "application/json",
        }
        try:
            return self._http.request(method, url, params=params, headers=headers, json=json)
        except httpx.TimeoutException:
            raise RemoteTimeoutError("Pas de réponse dans le délai imparti") from None
        except httpx.HTTPError as exc:
            # le message d'une erreur réseau peut citer l'adresse, jamais la clé ; masquée par précaution
            raise NetworkError(self._safe(f"{type(exc).__name__}: {exc}")) from None

    @staticmethod
    def _wait_after_429(response: httpx.Response, attempt: int) -> float | None:
        """Attente avant un nouvel essai, ou None pour abandonner (nombre d'essais ou attente trop longue)."""
        if attempt >= MAX_RETRIES_429:
            return None
        header = response.headers.get("Retry-After")
        if header is not None:
            try:
                wait = float(header)
            except ValueError:
                wait = BACKOFF_SECONDS[attempt]
            return wait if 0 <= wait <= RETRY_AFTER_CAP else None
        return BACKOFF_SECONDS[attempt]

    def _decode(self, response: httpx.Response) -> Any:
        status = response.status_code
        if 200 <= status < 300:
            if status == 204 or not response.content:
                return None
            try:
                return response.json()
            except ValueError:
                raise RemoteServerError("Réponse illisible (JSON attendu)", status=status) from None
        raise self._error(response)

    def _error(self, response: httpx.Response) -> ConnectorError:
        status = response.status_code
        cls = _ERRORS.get(status) or (RemoteTimeoutError if status == 504 else RemoteServerError)
        trace_id, fields = None, ()
        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            trace = body.get("traceId")
            trace_id = trace if isinstance(trace, str) else None
            fields = _fields_in_error(body)
        # jamais le corps brut : une réponse de la passerelle peut recopier l'en-tête d'autorisation
        return cls(f"HTTP {status}", status=status, trace_id=trace_id, fields=fields)

    def _safe(self, text: str) -> str:
        return redact(text, [self._key]) or ""


def _fields_in_error(body: dict[str, Any]) -> tuple[str, ...]:
    """NOMS des champs en cause, dans les deux formats d'erreur de l'API (jamais de valeur ni de message)."""
    names: list[str] = []
    details = body.get("details")
    if isinstance(details, list):  # format courant : details[].field
        names += [d["field"] for d in details if isinstance(d, dict) and isinstance(d.get("field"), str)]
    issues = body.get("IssueList")
    if isinstance(issues, list):  # ancien format : IssueList[].source
        names += [i["source"] for i in issues if isinstance(i, dict) and isinstance(i.get("source"), str)]
    return tuple(dict.fromkeys(n for n in names if len(n) <= 120))


__all__ = [
    "ALLOWED_METHODS",
    "API_BASE_URL",
    "API_VERSION",
    "BASE_URL_ENV",
    "RATE_LIMIT_PER_SECOND",
    "WRITE_METHODS",
    "LexwareClient",
    "ReadOnlyViolation",
    "api_base_url",
]
