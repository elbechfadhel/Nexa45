"""AI Migration Wizard par la Supplier API : une question à la fois, structure cible, Excel ; réservé à
un membre connecté ; aucune donnée technique (fournisseur, modèle, prompt) dans les réponses ; faux
proposer (aucun appel)."""

from __future__ import annotations

import io
from typing import Any

import polars as pl
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from test_lexware_partner_api import _client
from test_supplier_api import PW, TECHNICAL_KEYS
from uie_api import deps
from uie_api.app import create_app
from uie_core.domains import ProposalMeta
from uie_core.identity import Organization, OrganizationMembership, User, hash_password
from uie_core.wizard import WizardRaw, WizardRequest, clear_cache


class FakeProposer:
    cache_identity = "fake-api:1"

    def __init__(self) -> None:
        self.calls = 0

    def _raw(self, data: Any) -> WizardRaw:
        self.calls += 1
        return WizardRaw(data=data, meta=ProposalMeta(provider="fake", model="fake"))

    def wizard_question(self, request: WizardRequest) -> WizardRaw:
        if request.answers:
            return self._raw({"done": True, "remaining": 0, "question": None})
        return self._raw(
            {
                "done": False,
                "remaining": 1,
                "question": {
                    "id": "target_system",
                    "topic": "target_system",
                    "title": "Which target system?",
                    "help": None,
                    "allow_other": True,
                    "choices": [{"value": "big_suite", "label": "Big Suite", "description": None}],
                },
            }
        )

    def wizard_target(self, request: WizardRequest) -> WizardRaw:
        return self._raw(
            {
                "title": "Big Suite products",
                "summary": "Proposal.",
                "entity": "Product",
                "template_name": None,
                "configuration": [],
                "fields": [
                    {
                        "name": "PRODUCT",
                        "label": "Product",
                        "type": "text",
                        "length": 40,
                        "required": True,
                        "example": "P-1",
                        "description": None,
                    }
                ],
                "sample_rows": [[f"P-{i}"] for i in range(5)],
                "notes": [],
            }
        )


@pytest.fixture
def world(session):  # noqa: ANN001, ANN201
    clear_cache()
    org = Organization(name="Acme", slug="acme")
    session.add(org)
    session.flush()
    user = User(email="alice@acme.io", display_name="Alice", password_hash=hash_password(SecretStr(PW)))
    session.add(user)
    session.flush()
    session.add(OrganizationMembership(organization_id=org.id, user_id=user.id, role="OWNER"))
    session.flush()
    fake = FakeProposer()
    app = create_app()
    app.dependency_overrides[deps.get_session] = lambda: session
    app.dependency_overrides[deps.get_wizard_proposer] = lambda: fake
    yield {"app": app, "fake": fake}
    clear_cache()


def _walk(node: Any) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            assert k not in TECHNICAL_KEYS, k
            _walk(v)
    elif isinstance(node, list):
        for v in node:
            _walk(v)


def test_wizard_end_to_end_through_the_supplier_api(world):
    assert TestClient(world["app"]).post("/api/wizard/next", json={"answers": []}).status_code == 401
    alice = _client(world["app"], "alice@acme.io")

    first = alice.post("/api/wizard/next", json={"answers": []})
    assert first.status_code == 200, first.text
    q = first.json()["question"]
    assert q["topic"] == "target_system" and q["choices"][0]["source"] == "ai"
    answer = {
        "question_id": q["id"],
        "topic": q["topic"],
        "question": q["title"],
        "value": "big_suite",
        "label": "Big Suite",
        "source": "ai",
    }
    done = alice.post("/api/wizard/next", json={"answers": [answer]}).json()
    assert done["done"] is True and done["question"] is None

    proposal = alice.post("/api/wizard/proposal", json={"answers": [answer]})
    assert proposal.status_code == 200, proposal.text
    p = proposal.json()
    assert p["verification"] == "ai" and p["fields"][0]["verified"] is False and len(p["rows"]) == 5

    body = {
        k: p[k] for k in ("title", "summary", "entity", "template_name", "configuration", "rows", "notes")
    }
    body["fields"] = [
        {k: v for k, v in f.items() if k not in ("verified", "allowed", "reference")} for f in p["fields"]
    ]
    xlsx = alice.post("/api/wizard/excel", json={"answers": [answer], "proposal": body})
    assert xlsx.status_code == 200, xlsx.text
    assert xlsx.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert pl.read_excel(io.BytesIO(xlsx.content), sheet_name="Data").height == 5
    calls = world["fake"].calls
    alice.post("/api/wizard/proposal", json={"answers": [answer]})  # même réponses : reprise, aucun appel
    assert world["fake"].calls == calls
    for body in (first.json(), done, p):
        _walk(body)


def test_without_ai_the_wizard_says_it_is_not_configured(world):
    world["app"].dependency_overrides[deps.get_wizard_proposer] = lambda: None
    alice = _client(world["app"], "alice@acme.io")
    r = alice.post("/api/wizard/next", json={"answers": []})
    assert r.status_code == 409 and "not configured" in r.json()["detail"]


def test_simple_mode_file_flow_through_the_supplier_api(world):
    import json

    from uie_core.imports import ColumnProposalBatch

    fake = world["fake"]
    fake.wizard_columns = lambda request: ColumnProposalBatch(  # type: ignore[attr-defined]
        proposals=[], meta=ProposalMeta(provider="fake", model="fake")
    )
    alice = _client(world["app"], "alice@acme.io")
    answer = {
        "question_id": "target_system",
        "topic": "target_system",
        "question": "Which target system?",
        "value": "big_suite",
        "label": "Big Suite",
        "source": "ai",
    }
    p = alice.post("/api/wizard/proposal", json={"answers": [answer], "mode": "simple"}).json()
    proposal = {
        k: p[k] for k in ("title", "summary", "entity", "template_name", "configuration", "rows", "notes")
    }
    proposal["fields"] = [
        {k: v for k, v in f.items() if k not in ("verified", "allowed", "reference")} for f in p["fields"]
    ]
    csv = b"PRODUCT,Colour\nP-1,red\nP-2,blue\n"

    def send(path: str, decisions: list[dict[str, Any]] | None = None, content: bytes = csv, **extra: Any):  # noqa: ANN202
        payload = json.dumps(
            {"answers": [answer], "proposal": proposal, "decisions": decisions or [], **extra}
        )
        return alice.post(
            f"/api/wizard/file/{path}",
            files={"file": ("mine.csv", content, "text/csv")},
            data={"payload": payload},
        )

    mapping = send("mapping")
    assert mapping.status_code == 200, mapping.text
    m = mapping.json()
    assert (m["rows"], m["matched"], m["to_confirm"], m["unmatched"]) == (2, 1, 0, 1)
    assert m["columns"][0]["target"] == "PRODUCT" and m["verification"] == "ai"

    preview = send("preview", [{"position": 0, "target": "PRODUCT"}])
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["preview"] == [["P-1"], ["P-2"]] and body["mapped"] == 1 and body["missing_required"] == []
    _walk(m)
    _walk(body)

    xlsx = send("prepared", [{"position": 0, "target": "PRODUCT"}])
    assert xlsx.status_code == 200 and xlsx.headers["content-disposition"].endswith('_prepared.xlsx"')
    assert pl.read_excel(io.BytesIO(xlsx.content), sheet_name="Data").height == 2

    assert send("preview", [{"position": 0, "target": "UNKNOWN_FIELD"}]).status_code == 422

    # liste JSON dans un champ à valeur unique : une question pour la colonne, jamais écrite sans réponse
    listy = b'PRODUCT,currency\n"[""Wood"",""P-1""]",USD\n'
    held = send("preview", [{"position": 0, "target": "PRODUCT"}], listy).json()
    assert held["preview"] == [[""]] and [i["kind"] for i in held["value_issues"]] == ["list"]
    issue = held["value_issues"][0]  # lignes du fichier + solution simple + gravité
    assert (issue["rows"], issue["severity"]) == ([1], "not_written") and "question" in issue["fix"]
    question = held["value_questions"][0]
    assert (question["key"], question["field"]["name"], question["answer"]) == ("list:0", "PRODUCT", None)
    assert [o["value"] for o in question["options"]] == ["last", "path", "skip"]
    assert question["options"][0]["examples"] == [['["Wood","P-1"]', "P-1"]]  # avant → après
    assert held["status"] != "ready"
    chosen = send("preview", [{"position": 0, "target": "PRODUCT"}], listy, choices={"list:0": "last"})
    assert chosen.status_code == 200, chosen.text
    assert chosen.json()["preview"] == [["P-1"]] and chosen.json()["value_issues"] == []
    assert chosen.json()["value_transforms"][0]["examples"] == [['["Wood","P-1"]', "P-1"]]
    _walk(chosen.json())
    assert (
        send("preview", [{"position": 0, "target": "PRODUCT"}], listy, choices={"list:0": "x"}).status_code
        == 422
    )
    bad = alice.post(
        "/api/wizard/file/mapping",
        files={"file": ("mine.pdf", b"x", "application/pdf")},
        data={"payload": json.dumps({"answers": [answer], "proposal": proposal})},
    )
    assert bad.status_code == 400


def test_complete_missing_information_through_the_supplier_api(world):
    import json

    from uie_core.imports import ColumnProposalBatch

    world["fake"].wizard_columns = lambda request: ColumnProposalBatch(  # type: ignore[attr-defined]
        proposals=[], meta=ProposalMeta(provider="fake", model="fake")
    )
    alice = _client(world["app"], "alice@acme.io")
    answer = {
        "question_id": "target_system",
        "topic": "target_system",
        "question": "Which target system?",
        "value": "big_suite",
        "label": "Big Suite",
        "source": "ai",
    }
    p = alice.post("/api/wizard/proposal", json={"answers": [answer], "mode": "simple"}).json()
    proposal = {
        k: p[k] for k in ("title", "summary", "entity", "template_name", "configuration", "rows", "notes")
    }
    fields = [
        {k: v for k, v in f.items() if k not in ("verified", "allowed", "reference")} for f in p["fields"]
    ]
    fields.append(
        {
            "name": "UNIT",
            "label": "Unit",
            "type": "text",
            "length": None,
            "required": True,
            "example": "EA",
            "description": None,
        }
    )
    proposal["fields"] = fields
    csv = b"PRODUCT\nP-1\nP-2\n"
    decisions = [{"position": 0, "target": "PRODUCT"}]

    def send(path: str, fills: list[dict[str, Any]] | None = None):  # noqa: ANN202
        payload = json.dumps(
            {"answers": [answer], "proposal": proposal, "decisions": decisions, "fills": fills or []}
        )
        return alice.post(
            f"/api/wizard/file/{path}",
            files={"file": ("mine.csv", csv, "text/csv")},
            data={"payload": payload},
        )

    missing = send("missing")
    assert missing.status_code == 200, missing.text
    info = missing.json()
    assert [q["field"] for q in info["questions"]] == ["UNIT"] and info["row_count"] == 2
    assert info["questions"][0]["warning"] and len(info["rows"]) == 2
    _walk(info)

    assert (
        send("preview", [{"field": "UNIT", "common": "EA"}]).status_code == 422
    )  # non vérifiable : confirmer
    done = send("preview", [{"field": "UNIT", "common": "EA", "confirmed": True}])
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["missing_required"] == [] and [f["name"] for f in body["completed"]] == ["UNIT"]
    assert body["warnings"] and body["preview"][0] == ["P-1", "EA"]

    # réponse métier gardée : la question revient avec elle, le champ reste « à compléter »
    again = send("missing", [{"field": "UNIT", "hint": "Each"}]).json()
    assert again["questions"][0]["previous_hint"] == "Each" and again["questions"][0]["resolution"]
    flagged = send("preview", [{"field": "UNIT", "hint": "Each"}]).json()
    assert [f["name"] for f in flagged["missing_required"]] == ["UNIT"]
    assert flagged["data_type"] == p["title"]  # aucune question de type de données : titre de la structure
