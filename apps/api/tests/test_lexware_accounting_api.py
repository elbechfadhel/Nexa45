"""Parcours portail « Read accounting / Prepare for DATEV » pour une source à comptabilité DÉCLARÉE (schéma
fixe), par la Supplier API, sans aucune analyse : période, pièces, correspondances, aperçu DATEV.

Faux serveur Lexware (contacts et pièces INVENTÉS, GET seulement), specs DATEV locales (sinon sauté).
Les comptes de personne sont attribués par l'export Debitoren/Kreditoren EXISTANT dans la base de TEST
(transaction annulée). Aucun export réel, aucune écriture dans le système source."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select
from test_lexware_partner_api import _client, world  # noqa: F401  (fixture réutilisée)
from test_lexware_source_export_api import EXPORT, PROFILE, FakeSources
from test_supplier_api import TECHNICAL_KEYS
from uie_api import deps
from uie_connectors.datev import DatevSchemaLoader
from uie_connectors.lexware.testing import FAKE_KEY
from uie_core.exports import ExportDocumentRecord
from uie_core.identity import SystemContext
from uie_core.registry import RegistryService

ROOT = Path(__file__).resolve().parents[3]
SPECS = ROOT / "var" / "datev" / "specs" / "extf_700"
DK, BS_SPEC = SPECS / "debitoren-kreditoren_v5.yaml", SPECS / "buchungsstapel_v13.yaml"
pytestmark = pytest.mark.skipif(
    not (DK.exists() and BS_SPEC.exists()), reason="specs DATEV locales absentes (spec_builder)"
)
SALES = "00000000-0000-4000-8000-00000000c001"  # catégorie du faux serveur « Einnahmen »
ADDRESS = {"billing": [{"street": "Teststraße 1", "zip": "20457", "city": "Hamburg", "countryCode": "DE"}]}
CONTACT = {
    "id": "k-neu-1",
    "archived": False,
    "roles": {"customer": {"number": 10501}},
    "company": {"name": "Neukunde Beispiel GmbH", "vatRegistrationId": "DE900000001"},
    "addresses": ADDRESS,
}
VOUCHER = {
    "id": "neu-1",
    "type": "salesinvoice",
    "voucherStatus": "open",
    "voucherNumber": "RE-UIE-2026-0201",
    "voucherDate": "2026-02-10T00:00:00.000+01:00",
    "dueDate": "2026-02-24T00:00:00.000+01:00",
    "totalGrossAmount": 595.0,
    "totalTaxAmount": 95.0,
    "taxType": "gross",
    "useCollectiveContact": False,
    "contactId": "k-neu-1",
    "voucherItems": [{"amount": 595.0, "taxAmount": 95.0, "taxRatePercent": 19.0, "categoryId": SALES}],
    "version": 1,
    "_list": {"contactName": "Neukunde Beispiel GmbH"},
}
PREP = "/api/systems/{sid}/accounting/targets/datev/buchungsstapel"
QUERY = {"company_id": "single", "period": "2026-02", "version": "13"}


def _keys(body) -> set[str]:  # noqa: ANN001
    if isinstance(body, dict):
        return set(body) | {k for v in body.values() for k in _keys(v)}
    if isinstance(body, list):
        return {k for v in body for k in _keys(v)}
    return set()


def test_declared_accounting_read_mappings_and_datev_preview_without_analysis(world):  # noqa: F811
    w = world
    w["lexware"].contacts, w["lexware"].vouchers = [dict(CONTACT)], [dict(VOUCHER)]
    w["app"].dependency_overrides[deps.get_record_sources] = lambda: FakeSources(w["lexware"])
    registry = RegistryService(w["session"], SystemContext("test"))
    for spec in (DK, BS_SPEC):
        registry.publish_snapshot(DatevSchemaLoader({"spec": spec}).build_snapshot())
    alice, bob = _client(w["app"], "alice@acme.io"), _client(w["app"], "bob@globex.io")
    sid = alice.post(
        "/api/systems", json={"system": "lexware", "name": "Books", "fields": {"api_key": FAKE_KEY}}
    ).json()["id"]

    # le système annonce une comptabilité DÉCLARÉE : le portail affiche le parcours sans analyse
    assert "accounting" in alice.get(f"/api/systems/{sid}").json()["declared"]

    # société et période : une seule société pour une source à schéma fixe
    companies = alice.get(f"/api/systems/{sid}/accounting/companies")
    assert companies.status_code == 200 and companies.json() == [{"id": "single", "name": "Books"}]
    read = alice.get(
        f"/api/systems/{sid}/accounting/documents", params={"company_id": "single", "period": "2026-02"}
    )
    assert read.status_code == 200, read.text
    body = read.json()
    doc = body["documents"][0]
    assert (doc["number"], float(doc["total_amount"]), doc["exportable"]) == ("RE-UIE-2026-0201", 595.0, True)
    assert [(ln["account_code"], ln["category"]) for ln in doc["lines"]] == [(None, "Einnahmen")]
    assert any("ledger account" in u["message"] for u in body["unavailable"])  # rien d'inventé, c'est dit

    # comptes de personne : export Debitoren/Kreditoren EXISTANT (base de test)
    assert alice.put(f"{EXPORT}/profile", json={"values": PROFILE}).status_code == 200
    assert alice.post(f"{EXPORT}/files", json={"system_id": sid}).status_code == 200

    # préparation DATEV : réglages, catégorie et taux à décider (aucune suggestion), puis décisions explicites
    prep = PREP.format(sid=sid)
    saved = alice.put(f"{prep}/settings", json={"values": {"currency": "EUR"}})
    assert saved.status_code == 200, saved.text
    first = alice.get(f"{prep}/preparation", params=QUERY).json()
    kinds = {(m["kind"], m["source_code"], m["status"]) for m in first["mappings"]}
    assert kinds == {("category", "Einnahmen", "missing"), ("tax", "19 % (sales)", "missing")}
    assert first["documents"][0]["target_status"] == "blocked"
    refs = {m["kind"]: m["ref"] for m in first["mappings"]}
    assert (
        alice.put(f"{prep}/mappings/category/{refs['category']}", json={"value": "8400"}).status_code == 200
    )
    assert alice.put(f"{prep}/mappings/tax/{refs['tax']}", json={"no_value": True}).status_code == 200
    ready = alice.get(f"{prep}/preparation", params=QUERY).json()
    assert ready["documents"][0]["target_status"] == "ready", ready["documents"][0]
    assert not (_keys(ready) & TECHNICAL_KEYS)

    # aperçu seulement : aucun registre par pièce ; Lexware : GET seulement ; autre organisation : 404
    assert w["session"].scalar(select(func.count()).select_from(ExportDocumentRecord)) == 0
    assert w["lexware"].writes == []
    assert bob.get(f"{prep}/preparation", params=QUERY).status_code == 404
