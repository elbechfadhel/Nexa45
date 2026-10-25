"""Client HTTP Lexware Office (phase 1a) : transport, authentification, limite de débit, erreurs, secrets.

Hors ligne : faux serveur (`FakeLexwareServer`, données INVENTÉES) branché sur le transport httpx. Ces tests
prouvent le comportement de NOTRE client ; ils ne prouvent rien sur l'API distante réelle. Aucune attente
réelle : horloge et sommeil sont injectés."""

from __future__ import annotations

import ast
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from uie_connectors import errors
from uie_connectors.lexware.client import (
    ALLOWED_METHODS,
    API_BASE_URL,
    BASE_URL_ENV,
    MAX_RETRIES_429,
    LexwareClient,
    ReadOnlyViolation,
    api_base_url,
)
from uie_connectors.lexware.ratelimit import RATE_LIMIT_PER_SECOND, TokenBucket, shared_bucket
from uie_connectors.lexware.testing import FAKE_KEY, PROFILE, FakeLexwareServer

KEY = SecretStr(FAKE_KEY)


class Clock:
    """Horloge factice : `sleep` avance le temps, rien n'attend réellement."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _client(server: FakeLexwareServer, clock: Clock | None = None, key: SecretStr = KEY) -> LexwareClient:
    clock = clock or Clock()
    bucket = TokenBucket(clock=clock, sleep=clock.sleep)
    return LexwareClient(key, transport=server.transport, bucket=bucket, sleep=clock.sleep)


# --------------------------------------------------------------------------- #
# Adresse, authentification, lecture seule
# --------------------------------------------------------------------------- #
def test_official_gateway_is_fixed_and_bearer_auth_is_sent(monkeypatch):
    monkeypatch.delenv(BASE_URL_ENV, raising=False)
    assert API_BASE_URL == "https://api.lexware.io" and api_base_url() == API_BASE_URL
    server = FakeLexwareServer()
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return server._handle(request)

    clock = Clock()
    client = LexwareClient(
        KEY, transport=httpx.MockTransport(handler), bucket=TokenBucket(clock=clock, sleep=clock.sleep)
    )
    assert client.get("/v1/profile") == PROFILE
    (request,) = seen
    assert str(request.url) == "https://api.lexware.io/v1/profile"
    assert request.headers["Authorization"] == f"Bearer {FAKE_KEY}"
    assert request.headers["Accept"] == "application/json"
    assert client._http.timeout.read == 30.0  # délai de la passerelle officielle


def test_gateway_override_is_a_server_setting_for_development_only(monkeypatch):
    monkeypatch.setenv(BASE_URL_ENV, "http://127.0.0.1:9999/")
    assert api_base_url() == "http://127.0.0.1:9999"


def test_phase_1a_client_is_read_only_before_any_network_call():
    assert ALLOWED_METHODS == frozenset({"GET"})
    server = FakeLexwareServer()
    client = _client(server)
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        with pytest.raises(ReadOnlyViolation):
            client.request(method, "/v1/contacts")
    assert server.calls == []  # refusé AVANT tout appel
    # le client n'expose aucune méthode d'écriture
    assert not any(hasattr(client, name) for name in ("post", "put", "delete", "patch"))


# --------------------------------------------------------------------------- #
# Erreurs
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (400, errors.ValidationError),
        (401, errors.AuthenticationError),
        (402, errors.AuthorizationError),
        (403, errors.AuthorizationError),
        (404, errors.NotFoundError),
        (406, errors.ValidationError),
        (409, errors.ConflictError),
        (415, errors.ValidationError),
        (500, errors.RemoteServerError),
        (503, errors.RemoteServerError),
        (504, errors.RemoteTimeoutError),
    ],
)
def test_http_statuses_become_generic_connector_errors(status, expected):
    server = FakeLexwareServer(fail={"/v1/profile": status})
    with pytest.raises(expected) as exc:
        _client(server).get("/v1/profile")
    assert exc.value.status == status and exc.value.trace_id == "trace-test"
    assert isinstance(exc.value, errors.ConnectorError)


def test_error_never_carries_the_raw_body_or_the_key():
    """Un 403 de la passerelle peut recopier l'en-tête d'autorisation : le corps n'est jamais remonté."""
    leaked = f"'{FAKE_KEY}' not a valid key=value pair in Authorization header: 'Bearer {FAKE_KEY}'."
    server = FakeLexwareServer(queue=[(403, {}, {"message": leaked})])
    with pytest.raises(errors.AuthorizationError) as exc:
        _client(server).get("/v1/profile")
    assert FAKE_KEY not in str(exc.value) and FAKE_KEY not in repr(exc.value)
    assert str(exc.value) == "HTTP 403"


def test_validation_error_keeps_field_names_only_in_both_error_formats():
    current = {"status": 406, "traceId": "t-1", "details": [{"field": "company.name", "message": "x"}]}
    legacy = {"IssueList": [{"i18nKey": "missing_entity", "source": "person.lastName", "type": "v"}]}
    server = FakeLexwareServer(queue=[(406, {}, current), (406, {}, legacy)])
    client = _client(server)
    with pytest.raises(errors.ValidationError) as first:
        client.get("/v1/contacts")
    with pytest.raises(errors.ValidationError) as second:
        client.get("/v1/contacts")
    assert first.value.fields == ("company.name",) and first.value.trace_id == "t-1"
    assert second.value.fields == ("person.lastName",)


def test_malformed_json_network_error_and_timeout():
    server = FakeLexwareServer(queue=[(200, {"Content-Type": "application/json"}, "<html>not json</html>")])
    with pytest.raises(errors.RemoteServerError):
        _client(server).get("/v1/profile")

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url} with Bearer {FAKE_KEY}")

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    clock = Clock()
    for handler, expected in ((refuse, errors.NetworkError), (slow, errors.RemoteTimeoutError)):
        client = LexwareClient(
            KEY, transport=httpx.MockTransport(handler), bucket=TokenBucket(clock=clock, sleep=clock.sleep)
        )
        with pytest.raises(expected) as exc:
            client.get("/v1/profile")
        assert FAKE_KEY not in str(exc.value)  # la clé est masquée même si l'erreur réseau la citait


# --------------------------------------------------------------------------- #
# Limite de débit
# --------------------------------------------------------------------------- #
def test_token_bucket_spaces_requests_at_the_official_rate_without_real_sleep():
    assert RATE_LIMIT_PER_SECOND == 2.0  # limite officielle : 2 requêtes par seconde pour toute l'API
    clock = Clock()
    bucket = TokenBucket(clock=clock, sleep=clock.sleep)
    waits = [bucket.acquire() for _ in range(5)]
    assert waits[0] == 0.0 and waits[1:] == [0.5, 0.5, 0.5, 0.5]
    # jamais plus de 2 requêtes dans une même seconde : 5 requêtes prennent au moins 2 secondes
    assert clock.now == pytest.approx(2.0)
    clock.now += 10  # une longue pause ne crée aucune rafale : un seul jeton de capacité
    assert [bucket.acquire() for _ in range(2)] == [0.0, 0.5]


def test_requests_go_through_the_limiter_and_one_bucket_is_shared_per_client():
    server, clock = FakeLexwareServer(), Clock()
    client = _client(server, clock)
    for _ in range(3):
        client.get("/v1/countries")
    assert clock.slept == [0.5, 0.5]
    assert shared_bucket("abc") is shared_bucket("abc") and shared_bucket("abc") is not shared_bucket("xyz")


def test_429_respects_retry_after_then_succeeds():
    server = FakeLexwareServer(queue=[(429, {"Retry-After": "3"}, {"status": 429})])
    clock = Clock()
    assert _client(server, clock).get("/v1/profile") == PROFILE
    assert 3.0 in clock.slept and len(server.calls) == 2


def test_429_without_retry_after_uses_bounded_backoff_and_then_gives_up():
    server = FakeLexwareServer(queue=[(429, {}, {"status": 429})] * (MAX_RETRIES_429 + 1))
    clock = Clock()
    with pytest.raises(errors.RateLimitError):
        _client(server, clock).get("/v1/profile")
    assert len(server.calls) == MAX_RETRIES_429 + 1  # jamais de boucle sans fin
    assert [s for s in clock.slept if s >= 1.0] == [1.0, 2.0, 4.0]


def test_an_excessive_retry_after_is_a_failure_not_a_blocked_request():
    server = FakeLexwareServer(queue=[(429, {"Retry-After": "3600"}, {"status": 429})])
    clock = Clock()
    with pytest.raises(errors.RateLimitError):
        _client(server, clock).get("/v1/profile")
    assert len(server.calls) == 1 and 3600 not in clock.slept


# --------------------------------------------------------------------------- #
# Frontière : le client ne connaît aucun domaine métier
# --------------------------------------------------------------------------- #
def test_http_client_knows_no_business_domain():
    src = Path(__file__).resolve().parents[1] / "src" / "uie_connectors" / "lexware"
    for name in ("client.py", "ratelimit.py"):
        tree = ast.parse((src / name).read_text(encoding="utf-8"))
        imported = {
            (node.module or "") if isinstance(node, ast.ImportFrom) else alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import | ast.ImportFrom)
            for alias in node.names
        }
        assert not any(
            m.startswith(("uie_cdm", "uie_core.partners", "uie_core.mapping")) for m in imported
        ), name
