"""V2b.2 : Analyze depuis la Supplier API sur un système que le fournisseur vient de connecter (aucune
découverte préalable) ; réponses ; Re-analyze ; erreur présentée simplement ; pas de synchronisation."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from test_supplier_api import PASSWORD, Supplier, _reset_limiter, world  # noqa: F401 — fixtures partagées
from uie_core.catalog.records import CatalogSyncRecord
from uie_core.instances import ALLOW_PRIVATE_ENV

FIELD = "/api/systems/{sid}/catalog/fields/product.template/{field}/{action}"
TECHNICAL_TEXT = ("PermissionError", "Fault", "xmlrpc", "Traceback", "snapshot", "discovery", "rotated")


@pytest.fixture(autouse=True)
def _private(monkeypatch):
    monkeypatch.setenv(ALLOW_PRIVATE_ENV, "true")


def _connect(w, client: Supplier, name: str) -> str:  # noqa: ANN001
    form = {"url": "http://odoo.example.test:8069", "database": w["fake"].db, "username": "admin"}
    body = {"system": "odoo", "name": name, "fields": {**form, "password": PASSWORD}}
    r = client.post("/api/systems", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_analyze_answer_and_reanalyze_a_newly_connected_system(world):  # noqa: F811
    alice = Supplier(world["app"], "alice@acme.io")
    sid = _connect(world, alice, "Fresh Odoo")
    assert alice.get(f"/api/systems/{sid}").json()["catalog"]["status"] == "awaiting_setup"

    r = alice.post(f"/api/systems/{sid}/catalog/analyze")
    assert r.status_code == 200, r.text
    cat = r.json()
    assert (cat["status"], cat["questions"], cat["complete"]) == ("questions_open", 3, False)
    assert {s["label"]: s["present"] for s in cat["structure"]}["Products"] is True  # structure détectée

    base = {"sid": sid}
    alice.post(FIELD.format(**base, field="x_supplier_ref", action="confirm"), json={})
    alice.post(FIELD.format(**base, field="x_pack_code", action="change"), json={"meaning": "packaging_code"})
    alice.post(FIELD.format(**base, field="x_notes_internal", action="ignore"), json={})
    done = alice.get(f"/api/systems/{sid}/catalog").json()
    assert (done["status"], done["questions"], done["complete"]) == ("complete", 0, True)

    again = alice.post(f"/api/systems/{sid}/catalog/analyze").json()  # Re-analyze sans changement
    assert (again["status"], again["questions"], again["answers"]) == ("complete", 0, 3)
    assert alice.get(f"/api/systems/{sid}/catalog/questions").json() == []  # aucune question reposée
    # Analyze ne synchronise jamais le catalogue (F3)
    assert world["session"].scalar(select(func.count()).select_from(CatalogSyncRecord)) == 0
    assert alice.get(f"/api/systems/{sid}/catalog/sync").json() is None


def test_analysis_failure_is_a_simple_message(world):  # noqa: F811
    alice = Supplier(world["app"], "alice@acme.io")
    sid = _connect(world, alice, "Soon broken")
    world["fake"].password = "rotated-Password-1"  # le mot de passe a changé côté Odoo
    r = alice.post(f"/api/systems/{sid}/catalog/analyze")
    assert r.status_code == 502
    assert r.json() == {
        "detail": "We couldn't analyze your system. Please check the connection and try again."
    }
    assert not any(t in r.text for t in TECHNICAL_TEXT) and PASSWORD not in r.text
