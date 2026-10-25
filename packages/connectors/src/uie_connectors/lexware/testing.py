"""Faux serveur de l'API publique Lexware Office pour les tests hors ligne (aucun réseau, aucune clé réelle).

Il sert des données INVENTÉES sur les trois ressources de la phase 1a et ENREGISTRE chaque requête : les
tests prouvent ainsi qu'aucune écriture n'est tentée. Toute méthode autre que GET répond 405 et reste
enregistrée, sauf avec `writable=True` (phase 1b) : création (`version: 0` exigé, numéros attribués),
relecture, mise à jour avec contrôle de version (409), filtres `name` / `email` de la liste ; articles
(`version: 0` à la création, PUT avec version, filtre `articleNumber`, prix non directeur calculé).
`fail` accepte un chemin ou un couple (méthode, chemin). Ce faux serveur ne prouve RIEN sur le comportement
réel de l'API."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

FAKE_KEY = "fake-key-INVENTED-0000"  # noqa: S105 — valeur de test inventée, jamais une vraie clé
ORGANIZATION_ID = "00000000-0000-4000-8000-0000000000aa"

PROFILE = {
    "organizationId": ORGANIZATION_ID,
    "companyName": "Musterbetrieb Test GmbH",
    "created": {
        "userId": "00000000-0000-4000-8000-0000000000bb",
        "userName": "Erika Beispiel",
        "userEmail": "erika.beispiel@example.test",
        "date": "2026-10-01T09:00:00.000+02:00",
    },
    "connectionId": "00000000-0000-4000-8000-0000000000cc",
    "taxType": "net",
    "businessFeatures": ["INVOICING", "BOOKKEEPING"],
    "smallBusiness": False,
}
COUNTRIES = [
    {
        "countryCode": "DE",
        "countryNameDE": "Deutschland",
        "countryNameEN": "Germany",
        "taxClassification": "de",
    },
    {
        "countryCode": "FR",
        "countryNameDE": "Frankreich",
        "countryNameEN": "France",
        "taxClassification": "intraCommunity",
    },
]

# catégories de comptabilisation INVENTÉES (identifiants fixes pour les tests)
POSTING_CATEGORIES = [
    {
        "id": "00000000-0000-4000-8000-00000000c001",
        "name": "Einnahmen",
        "type": "income",
        "contactRequired": False,
        "splitAllowed": True,
        "groupName": "Einnahmen",
    },
    {
        "id": "00000000-0000-4000-8000-00000000c002",
        "name": "Dienstleistungen",
        "type": "income",
        "contactRequired": False,
        "splitAllowed": True,
        "groupName": "Einnahmen",
    },
    {
        "id": "00000000-0000-4000-8000-00000000c003",
        "name": "Wareneinkauf",
        "type": "outgo",
        "contactRequired": False,
        "splitAllowed": True,
        "groupName": "Material/Waren",
    },
]


@dataclass
class Call:
    method: str
    path: str
    authorization: str | None
    body: bytes
    query: str = ""  # paramètres de l'URL (filtres de liste)


@dataclass
class FakeLexwareServer:
    """`transport` se branche sur `LexwareClient(..., transport=...)`. `fail` impose un statut à un chemin ;
    `queue` impose une suite de réponses (statut, en-têtes, corps) consommées dans l'ordre."""

    api_key: str = FAKE_KEY
    calls: list[Call] = field(default_factory=list)
    fail: dict[Any, int] = field(default_factory=dict)  # chemin, ou (méthode, chemin)
    queue: list[tuple[int, dict[str, str], Any]] = field(default_factory=list)
    contacts: list[dict[str, Any]] = field(default_factory=list)
    writable: bool = False
    vouchers: list[dict[str, Any]] = field(default_factory=list)
    articles: list[dict[str, Any]] = field(default_factory=list)
    next_numbers: dict[str, int] = field(default_factory=lambda: {"customer": 10001, "vendor": 70001})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    @property
    def writes(self) -> list[Call]:
        return [c for c in self.calls if c.method != "GET"]

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(
            Call(
                request.method,
                path,
                request.headers.get("Authorization"),
                request.content,
                request.url.query.decode()
                if isinstance(request.url.query, bytes)
                else str(request.url.query),
            )
        )
        if self.queue:
            status, headers, body = self.queue.pop(0)
            if isinstance(body, bytes | str):
                return httpx.Response(status, headers=headers, content=body)
            return httpx.Response(status, headers=headers, json=body)
        if request.headers.get("Authorization") != f"Bearer {self.api_key}":
            return httpx.Response(401, json={"message": "Unauthorized"})
        status = self.fail.get((request.method, path), self.fail.get(path))
        if status is not None:
            return httpx.Response(status, json={"status": status, "traceId": "trace-test"})
        if request.method != "GET":
            if not self.writable or request.method not in ("POST", "PUT"):
                return httpx.Response(405, json={"status": 405})
            return self._write(request)
        if path.startswith("/v1/vouchers/"):
            found = next((v for v in self.vouchers if v["id"] == path.rsplit("/", 1)[1]), None)
            if found is None:
                return httpx.Response(404, json={"status": 404})
            return httpx.Response(200, json={k: v for k, v in found.items() if k != "_list"})
        if path == "/v1/voucherlist":
            if "voucherType" not in request.url.params or "voucherStatus" not in request.url.params:
                return httpx.Response(400, json={"status": 400})
            return httpx.Response(200, json=_page(self._voucherlist(request.url.params), request.url.params))
        if path == "/v1/vouchers":
            number = request.url.params.get("voucherNumber")
            shown = [v for v in self.vouchers if v.get("voucherNumber") == number]
            return httpx.Response(
                200,
                json={
                    "content": shown,
                    "totalElements": len(shown),
                    "first": True,
                    "last": True,
                    "number": 0,
                    "size": 25,
                    "totalPages": 1,
                },
            )
        if path.startswith("/v1/contacts/"):
            found = self._contact(path.rsplit("/", 1)[1])
            return httpx.Response(200, json=found) if found else httpx.Response(404, json={"status": 404})
        if path.startswith("/v1/articles/"):
            found = self._article(path.rsplit("/", 1)[1])
            return httpx.Response(200, json=found) if found else httpx.Response(404, json={"status": 404})
        if path == "/v1/articles":
            number = request.url.params.get("articleNumber")
            shown = [a for a in self.articles if number is None or a.get("articleNumber") == number]
            return httpx.Response(200, json=_page(shown, request.url.params))
        if path == "/v1/profile":
            return httpx.Response(200, json=PROFILE)
        if path == "/v1/countries":
            return httpx.Response(200, json=COUNTRIES)
        if path == "/v1/posting-categories":
            return httpx.Response(200, json=POSTING_CATEGORIES)
        if path == "/v1/contacts" and "page" in request.url.params:
            return httpx.Response(200, json=_page(self.contacts, request.url.params))
        if path == "/v1/contacts":
            shown = [c for c in self.contacts if _matches(c, request.url.params)]
            return httpx.Response(
                200,
                json={
                    "content": shown,
                    "totalElements": len(shown),
                    "totalPages": 1,
                    "first": True,
                    "last": True,
                    "number": 0,
                    "size": 25,
                    "numberOfElements": len(shown),
                },
            )
        return httpx.Response(404, json={"status": 404})

    # ------------------------------------------------------------------ #
    def _voucherlist(self, params: Any) -> list[dict[str, Any]]:
        """Entrées de la liste comme l'API : types demandés, dates bornées (jour de la pièce). `_list` d'une
        pièce de test remplace des valeurs de la LISTE seulement (ex. statut de paiement « overdue »)."""
        types = set(params.get("voucherType", "").split(","))
        start, end = params.get("voucherDateFrom"), params.get("voucherDateTo")
        out = []
        for v in self.vouchers:
            day = str(v.get("voucherDate", ""))[:10]
            if (
                ("any" not in types and v.get("type") not in types)
                or (start and day < start)
                or (end and day > end)
            ):
                continue
            out.append(
                {
                    "id": v["id"],
                    "voucherType": v.get("type"),
                    "voucherStatus": v.get("voucherStatus"),
                    "voucherNumber": v.get("voucherNumber"),
                    "voucherDate": v.get("voucherDate"),
                    "contactId": v.get("contactId"),
                    "totalAmount": v.get("totalGrossAmount"),
                    "currency": "EUR",
                    **v.get("_list", {}),
                }
            )
        return out

    def _contact(self, contact_id: str) -> dict[str, Any] | None:
        return next((c for c in self.contacts if c["id"] == contact_id), None)

    def _article(self, article_id: str) -> dict[str, Any] | None:
        return next((a for a in self.articles if a["id"] == article_id), None)

    def _write_article(self, request: httpx.Request, body: dict[str, Any]) -> httpx.Response:
        """Articles comme la documentation : `version: 0` à la création, PUT complet avec la version relue
        (409 sinon), prix directeur obligatoire, l'autre prix calculé par le serveur (taux appliqué)."""
        path = request.url.path
        current = None
        if request.method == "PUT":
            current = self._article(path.rsplit("/", 1)[1])
            if current is None:
                return httpx.Response(404, json={"status": 404})
            if body.get("version") != current["version"]:
                return httpx.Response(409, json={"status": 409, "traceId": "t-conflict"})
            if set(body) & {"id", "organizationId", "createdDate", "updatedDate"}:
                return httpx.Response(400, json={"details": [{"field": "id"}]})
        elif body.get("version") != 0:
            return httpx.Response(400, json={"details": [{"field": "version"}], "traceId": "t-version"})
        price = body.get("price") or {}
        leading = price.get("leadingPrice")
        missing = [k for k in ("title", "type", "unitName") if not body.get(k)]
        if leading not in ("NET", "GROSS") or price.get("taxRate") not in (0, 7, 19):
            missing.append("price")
        if missing:
            return httpx.Response(406, json={"IssueList": [{"source": m} for m in missing]})
        rate = 1 + price["taxRate"] / 100
        if leading == "NET":
            price["grossPrice"] = round(price["netPrice"] * rate, 2)
        else:
            price["netPrice"] = round(price["grossPrice"] / rate, 2)
        if current is None:
            record = {**body, "id": str(uuid.uuid4()), "organizationId": ORGANIZATION_ID, "archived": False}
            record["version"] = 1
            self.articles.append(record)
        else:
            version = body["version"] + 1
            current.clear()
            current.update(body, id=path.rsplit("/", 1)[1], organizationId=ORGANIZATION_ID, archived=False)
            current["version"] = version
            record = current
        return httpx.Response(200, json=_created(record, "articles"))

    def _write(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        path = request.url.path
        if (
            path == "/v1/articles"
            and request.method == "POST"
            or (request.method == "PUT" and path.startswith("/v1/articles/"))
        ):
            return self._write_article(request, body)
        if request.method == "POST" and path == "/v1/contacts":
            if body.get("version") != 0:
                return httpx.Response(400, json={"details": [{"field": "version"}], "traceId": "t-version"})
            missing = _missing(body)
            if missing:
                return httpx.Response(406, json={"IssueList": [{"source": m} for m in missing]})
            record = {**body, "id": str(uuid.uuid4()), "organizationId": ORGANIZATION_ID, "archived": False}
            record["version"] = 1
            record["roles"] = self._numbers(body.get("roles") or {}, {})
            self.contacts.append(record)
            return httpx.Response(200, json=_created(record))
        if request.method == "POST" and path == "/v1/vouchers":
            if "version" in body or "id" in body:
                return httpx.Response(400, json={"details": [{"field": "version"}]})
            missing = [k for k in ("type", "voucherNumber", "voucherDate", "voucherItems") if not body.get(k)]
            if missing:
                return httpx.Response(406, json={"IssueList": [{"source": m} for m in missing]})
            record = {
                **body,
                "id": str(uuid.uuid4()),
                "organizationId": ORGANIZATION_ID,
                "version": 1,
                "voucherStatus": "open",
                "files": [],
            }
            self.vouchers.append(record)
            return httpx.Response(200, json=_created(record))
        if request.method == "PUT" and path.startswith("/v1/contacts/"):
            contact_id = path.rsplit("/", 1)[1]
            current = self._contact(contact_id)
            if current is None:
                return httpx.Response(404, json={"status": 404})
            if body.get("version") != current["version"]:
                return httpx.Response(409, json={"status": 409, "traceId": "t-conflict"})
            if set(body) & {"id", "organizationId"}:
                return httpx.Response(400, json={"details": [{"field": "id"}]})
            missing = _missing(body)
            if missing:
                return httpx.Response(406, json={"IssueList": [{"source": m} for m in missing]})
            roles = self._numbers(body.get("roles") or {}, current.get("roles") or {})
            current.clear()
            current.update(body)
            current.update(id=contact_id, organizationId=ORGANIZATION_ID, archived=False, roles=roles)
            current["version"] = body["version"] + 1
            return httpx.Response(200, json=_created(current))
        return httpx.Response(405, json={"status": 405})

    def _numbers(self, roles: dict[str, Any], before: dict[str, Any]) -> dict[str, Any]:
        out = {}
        for role in roles:
            number = (before.get(role) or {}).get("number")
            if number is None:
                number = self.next_numbers[role]
                self.next_numbers[role] += 1
            out[role] = {"number": number}
        return out


def _missing(body: dict[str, Any]) -> list[str]:
    out = []
    if not body.get("roles"):
        out.append("roles")
    company, person = body.get("company") or {}, body.get("person") or {}
    if not company.get("name") and not person.get("lastName"):
        out.append("company.name")
    return out


def _page(items: list[dict[str, Any]], params: Any) -> dict[str, Any]:
    """Page de liste comme l'API (`page` à partir de 0, `size`) ; utilisée seulement si `page` est demandé."""
    page, size = int(params.get("page", 0)), int(params.get("size", 25))
    total_pages = max(1, -(-len(items) // size))
    content = items[page * size : (page + 1) * size]
    return {
        "content": content,
        "totalElements": len(items),
        "totalPages": total_pages,
        "first": page == 0,
        "last": page >= total_pages - 1,
        "number": page,
        "size": size,
        "numberOfElements": len(content),
    }


def _created(record: dict[str, Any], resource: str = "contacts") -> dict[str, Any]:
    return {
        "id": record["id"],
        "resourceUri": f"https://api.lexware.io/v1/{resource}/{record['id']}",
        "createdDate": "2026-10-13T10:00:00.000+02:00",
        "updatedDate": "2026-10-13T10:00:00.000+02:00",
        "version": record["version"],
    }


def _matches(contact: dict[str, Any], params: Any) -> bool:
    name, email = params.get("name"), params.get("email")
    if name:
        company, person = contact.get("company") or {}, contact.get("person") or {}
        label = company.get("name") or " ".join(
            p for p in (person.get("firstName"), person.get("lastName")) if p
        )
        if name.casefold() not in (label or "").casefold():
            return False
    if email:
        emails = [e for v in (contact.get("emailAddresses") or {}).values() for e in v]
        if not any(email.casefold() in e.casefold() for e in emails):
            return False
    return True
