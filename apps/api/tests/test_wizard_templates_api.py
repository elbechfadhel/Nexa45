"""Modèles de questionnaire du Wizard : par utilisateur, persistants, questions et réponses seulement."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from test_lexware_partner_api import _client
from test_supplier_api import PW
from test_wizard_api import world  # noqa: F401 - fixture partagée
from uie_core.identity import Organization, OrganizationMembership, User, hash_password
from uie_core.wizard.templates import WizardTemplateRecord

QUESTION = {
    "id": "target_system",
    "topic": "target_system",
    "title": "Which target system?",
    "help": None,
    "choices": [
        {"value": "big_suite", "label": "Big Suite", "description": None, "group": None, "source": "ai"}
    ],
    "allow_other": True,
    "other_label": None,
}
ANSWER = {
    "question_id": "target_system",
    "topic": "target_system",
    "question": "Which target system?",
    "value": "big_suite",
    "label": "Big Suite",
    "source": "ai",
}
STEPS = [{"question": QUESTION, "answer": ANSWER}]


def _user(session: Any, email: str, org: Organization) -> None:
    user = User(email=email, display_name=email, password_hash=hash_password(SecretStr(PW)))
    session.add(user)
    session.flush()
    session.add(OrganizationMembership(organization_id=org.id, user_id=user.id, role="MEMBER"))
    session.flush()


@pytest.fixture
def people(world, session):  # noqa: ANN001, ANN201, F811
    acme = session.scalars(select(Organization).where(Organization.slug == "acme")).one()
    other = Organization(name="Other", slug="other")
    session.add(other)
    session.flush()
    _user(session, "bob@acme.io", acme)
    _user(session, "carol@other.io", other)
    users = {"alice": "alice@acme.io", "bob": "bob@acme.io", "carol": "carol@other.io"}
    return {"app": world["app"], **{name: _client(world["app"], email) for name, email in users.items()}}


def test_a_user_saves_lists_renames_and_deletes_templates(people, session):  # noqa: ANN001
    alice = people["alice"]
    assert alice.get("/api/wizard/templates").json() == []
    r = alice.post("/api/wizard/templates", json={"name": "  Odoo   products ", "steps": STEPS})
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["name"] == "Odoo products" and t["answered"] == 1
    assert t["steps"] == [{"question": QUESTION, "answer": ANSWER}]  # rejouable tel quel, sans LLM
    # persistant : relu par une nouvelle connexion du même utilisateur
    again = _client(people["app"], "alice@acme.io")
    assert [x["name"] for x in again.get("/api/wizard/templates").json()] == ["Odoo products"]
    # nom déjà pris → 409 ; renommage
    assert (
        alice.post("/api/wizard/templates", json={"name": "Odoo products", "steps": STEPS}).status_code == 409
    )
    renamed = alice.patch(f"/api/wizard/templates/{t['id']}", json={"name": "Shop catalog"})
    assert renamed.status_code == 200 and renamed.json()["name"] == "Shop catalog"
    # seules les questions et réponses sont stockées : ni fichier, ni correspondance, ni donnée
    record = session.scalars(select(WizardTemplateRecord)).one()
    assert set(record.steps[0]) == {"question", "answer"}
    assert alice.delete(f"/api/wizard/templates/{t['id']}").status_code == 204
    assert alice.get("/api/wizard/templates").json() == []


def test_templates_belong_to_one_user_in_one_organization(people):  # noqa: ANN001
    t = people["alice"].post("/api/wizard/templates", json={"name": "Mine", "steps": STEPS}).json()
    for other in ("bob", "carol"):  # même organisation ou autre : invisible, même 404 qu'un id inexistant
        client = people[other]
        assert client.get("/api/wizard/templates").json() == []
        assert client.patch(f"/api/wizard/templates/{t['id']}", json={"name": "x"}).status_code == 404
        assert client.delete(f"/api/wizard/templates/{t['id']}").status_code == 404
    # le même nom reste libre pour un autre utilisateur
    assert (
        people["bob"].post("/api/wizard/templates", json={"name": "Mine", "steps": STEPS}).status_code == 201
    )
    assert [x["name"] for x in people["alice"].get("/api/wizard/templates").json()] == ["Mine"]


def test_a_template_never_takes_anything_but_questions_and_answers(people):  # noqa: ANN001
    alice = people["alice"]
    extra = [{**STEPS[0], "mapping": {"title": "name"}}]
    assert alice.post("/api/wizard/templates", json={"name": "x", "steps": extra}).status_code == 422
    with_file = {"name": "x", "steps": STEPS, "file": "data"}
    assert alice.post("/api/wizard/templates", json=with_file).status_code == 422
    assert alice.post("/api/wizard/templates", json={"name": "x", "steps": []}).status_code == 422
    assert alice.post("/api/wizard/templates", json={"name": "   ", "steps": STEPS}).status_code == 409
