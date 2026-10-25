"""API Semantic Enrichment (V1b) : analyze → questions → confirm / change / ignore → analysis complete."""

from __future__ import annotations

from test_product_domain_api import client, iid  # noqa: F401 — fixtures partagées (instance découverte)

BASE = "/admin/instances/{iid}/product-domain"


def _field(iid: str, entity: str, field: str, action: str) -> str:  # noqa: F811
    return f"{BASE.format(iid=iid)}/fields/{entity}/{field}/{action}"


def test_catalog_analysis_cycle(client, iid):  # noqa: F811
    base = BASE.format(iid=iid)
    assert client.get(f"{base}/analysis").status_code == 404  # jamais analysé
    r = client.post(f"{base}/analyze")
    assert r.status_code == 200, r.text
    enrichment = r.json()["enrichment"]
    assert enrichment["pending"] == 3 and enrichment["created"] > 0

    analysis = client.get(f"{base}/analysis").json()
    assert analysis["pending"] == 3 and not analysis["complete"]
    total = sum(
        analysis[k] for k in ("identified", "excluded_auto", "excluded_by_supplier", "possible", "pending")
    )
    assert total == analysis["total"]
    assert {c["role"]: c["present"] for c in analysis["structure"]}["PRIMARY"] is True

    questions = client.get(f"{base}/questions").json()
    assert [q["field_technical_name"] for q in questions] == [
        "x_pack_code",
        "x_supplier_ref",
        "x_notes_internal",
    ]
    sup = questions[1]
    assert (sup["semantic_key"], sup["semantic_label"], sup["source"], sup["rule_id"]) == (
        "supplier_reference",
        "Supplier product code",
        "RULE",
        "R08",
    )
    assert sup["field"]["label"] == "Supplier Reference" and sup["field"]["native_type"] == "char"

    excluded = client.get(f"{base}/exclusions", params={"extensions_only": True}).json()
    assert {x["field_technical_name"] for x in excluded} >= {"project_id", "service_policy", "rating_ids"}
    assert all(x["reason"] and x["rule_id"] for x in excluded)

    vocab = client.get("/admin/vocabulary/product").json()
    groups = {g["group"]: [k["key"] for k in g["keys"]] for g in vocab["groups"]}
    assert "packaging_code" in groups["logistics"] and "supplier_reference" in groups["supplier"]

    # réponses du supplier
    tmpl = "product.template"
    r = client.post(_field(iid, tmpl, "x_supplier_ref", "confirm"), json={})
    assert r.status_code == 200, r.text
    assert r.json()["meaning_status"] == "CONFIRMED"
    assert (
        client.post(
            _field(iid, tmpl, "x_pack_code", "change"), json={"semantic_key": "free text"}
        ).status_code
        == 400
    )
    r = client.post(_field(iid, tmpl, "x_pack_code", "change"), json={"semantic_key": "packaging_code"})
    assert r.json()["meaning_status"] == "CONFIRMED" and r.json()["semantic_key"] == "packaging_code"
    r = client.post(_field(iid, tmpl, "x_notes_internal", "ignore"), json={"note": "internal only"})
    assert (r.json()["meaning_status"], r.json()["note"]) == ("IGNORED", "internal only")
    assert client.post(_field(iid, tmpl, "nope", "confirm"), json={}).status_code == 404

    done = client.get(f"{base}/analysis").json()
    assert done["complete"] and done["pending"] == 0 and done["excluded_by_supplier"] == 1
    assert client.get(f"{base}/questions").json() == []
    confirmed = client.get(f"{base}/bindings", params={"status": "CONFIRMED"}).json()
    assert {b["path"] for b in confirmed} == {
        "product.template.x_supplier_ref",
        "product.template.x_pack_code",
        "product.product.x_supplier_ref",  # copie related : suit sa source
    }

    # réponses listées (la plus récente d'abord), puis correction d'une erreur : reopen
    answers = client.get(f"{base}/decisions").json()
    assert [a["field_technical_name"] for a in answers] == [
        "x_notes_internal",
        "x_pack_code",
        "x_supplier_ref",
    ]
    assert done["supplier_decisions"] == 3
    r = client.post(_field(iid, tmpl, "x_pack_code", "reopen"), json={"note": "wrong meaning"})
    assert r.status_code == 200, r.text
    assert (r.json()["meaning_status"], r.json()["pending"], r.json()["semantic_key"]) == (
        "PROPOSED",
        True,
        "packaging_code",
    )
    assert r.json()["previous"]["meaning_status"] == "CONFIRMED"
    assert client.post(_field(iid, tmpl, "x_pack_code", "reopen")).status_code == 400  # déjà une question
    assert [q["field_technical_name"] for q in client.get(f"{base}/questions").json()] == ["x_pack_code"]
    assert len(client.get(f"{base}/decisions").json()) == 2
    r = client.post(_field(iid, tmpl, "x_pack_code", "confirm"), json={})
    assert r.json()["meaning_status"] == "CONFIRMED" and not r.json()["pending"]

    # contestation d'une exclusion automatique : redevient une question
    r = client.post(_field(iid, tmpl, "project_id", "include"))
    assert r.status_code == 200 and r.json()["pending"] and r.json()["source"] == "HUMAN"
    assert [q["field_technical_name"] for q in client.get(f"{base}/questions").json()] == ["project_id"]

    # nouvelle découverte + analyse : décisions réappliquées, rien de nouveau à demander
    assert client.post(f"/admin/instances/{iid}/discover-schema").status_code == 201
    again = client.post(f"{base}/analyze").json()["enrichment"]
    assert (again["reapplied"], again["pending"]) == (4, 1)
