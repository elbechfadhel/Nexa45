"""Correspondance des CATÉGORIES d'une source sans plan comptable vers les comptes du plan cible (phase 1b.3).

Source : pièces INVENTÉES servies par le faux serveur Lexware (CDM 1.1 : lignes ITEM par catégorie et taux,
sans compte). Cible : préparation DATEV Buchungsstapel EXISTANTE (spec v13 locale, tests sautés si absente).
Aucune correspondance automatique : chaque catégorie et chaque taux sont décidés explicitement ; sans
décision, l'écriture est BLOQUÉE. Aucune ligne TAX ni OPEN_ITEM créée, aucun fichier, aucune migration.
Les valeurs saisies (8400, 3400, clés de taxe) sont des VALEURS DE TEST, jamais déduites par le moteur."""

# ruff: noqa: F811  (fixture « acc » importée et utilisée en paramètre)
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select
from test_accounting_lexware_source import JANUARY, PURCHASE, SALES, acc  # noqa: F401  (fixture)
from uie_connectors.datev import DatevSchemaLoader
from uie_core.accounting import AccountingTargetService, MappingStatus
from uie_core.exports import ExportAccountAssignmentRecord, ExportProfileRecord
from uie_core.exports.records import ExportValueMappingRecord
from uie_core.identity import SystemContext
from uie_core.mapping import LookupKind, RecordStatus
from uie_core.registry import RegistryService

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "var" / "datev" / "specs" / "extf_700" / "buchungsstapel_v13.yaml"
pytestmark = pytest.mark.skipif(not SPEC.exists(), reason="spec DATEV locale absente (spec_builder)")
DATEV, BS = "datev", "buchungsstapel"
SHARED = {  # réglages du dossier DATEV (valeurs de test)
    "advisor_number": 1001,
    "client_number": 1,
    "fiscal_year_start": "2026-01-01",
    "account_length": 4,
    "chart_of_accounts": "03",
    "origin_code": "RE",
    "debtor_range": {"start": 10000, "end": 69999},
    "creditor_range": {"start": 70000, "end": 99999},
    "encoding": "cp1252",
}


@pytest.fixture
def cat(acc):  # noqa: ANN001, ANN201
    session, inst = acc["session"], acc["inst"]
    RegistryService(session, SystemContext("test")).publish_snapshot(
        DatevSchemaLoader({"spec": SPEC}).build_snapshot()
    )
    profile = ExportProfileRecord(
        organization_id=inst.organization_id,
        source_model="partner",
        target_system=DATEV,
        target_product="debitoren-kreditoren",
        values=SHARED,
    )
    session.add(profile)
    session.flush()
    # comptes de personne tels que l'export Debitoren/Kreditoren les attribue (même source)
    for role, number in (("CUSTOMER", "10000"), ("SUPPLIER", "70000")):
        session.add(
            ExportAccountAssignmentRecord(
                profile_id=profile.id,
                instance_id=inst.id,
                source_ref="contact-1",
                role=role,
                account_number=number,
                origin="ASSIGNED",
            )
        )
    session.flush()
    svc = AccountingTargetService(session, acc["reader"].ctx, acc["reader"])
    svc.save_settings(inst.id, DATEV, BS, {"currency": "EUR"})
    return acc | {"svc": svc}


def preview(cat):  # noqa: ANN001, ANN201
    return cat["svc"].preview(cat["inst"].id, "single", JANUARY, DATEV, BS)


def by_number(p):  # noqa: ANN001, ANN201
    return {d.number: d for d in p.documents}


def decide(cat, kind, ref, value=None, *, no_value=False):  # noqa: ANN001, ANN201
    return cat["svc"].decide(cat["inst"].id, DATEV, BS, kind, ref, value=value, no_value=no_value)


def test_categories_and_rates_without_decision_are_listed_and_block_bookings(cat):
    p = preview(cat)
    cats = {m.source_ref: m for m in p.mappings if m.kind == LookupKind.CATEGORY}
    rates = {m.source_ref: m for m in p.mappings if m.kind == LookupKind.TAX}
    assert set(cats) == {SALES, PURCHASE}
    assert all(
        m.status == MappingStatus.MISSING and m.suggestion is None for m in cats.values()
    )  # jamais suggéré
    assert "No automatic mapping for a source category" in cats[SALES].reason
    # le NOM de la catégorie est affiché ; l'identifiant reste la référence de la décision
    assert (cats[SALES].source_code, cats[PURCHASE].source_code) == ("Einnahmen", "Wareneinkauf")
    assert set(rates) == {
        "rate:SALE:19",
        "rate:SALE:7",
        "rate:PURCHASE:19",
    }  # taux ET sens : décisions distinctes
    assert {m.source_code for m in rates.values()} == {"19 % (sales)", "7 % (sales)", "19 % (purchases)"}
    assert not any(
        m.kind == LookupKind.ACCOUNT for m in p.mappings
    )  # aucun compte inventé à faire correspondre
    v1 = by_number(p)["NR-v-1"]
    assert v1.target_status == RecordStatus.BLOCKED and len(v1.bookings) == 2
    messages = [i.message for bk in v1.bookings for i in bk.issues]
    assert any("Category Einnahmen has no DATEV mapping yet" in m for m in messages)
    assert all(bk.counter_account is None and bk.counter_category.source_ref == SALES for bk in v1.bookings)


def test_confirmed_category_and_rate_make_the_booking_ready_without_tax_or_partner_lines(cat):
    decide(cat, LookupKind.CATEGORY, SALES, "8400")
    decide(cat, LookupKind.TAX, "rate:SALE:19", no_value=True)  # ex. compte automatique : test seulement
    decide(cat, LookupKind.TAX, "rate:SALE:7", "0002")  # clé de test
    p = preview(cat)
    v1 = by_number(p)["NR-v-1"]
    assert v1.target_status == RecordStatus.READY, [i.message for bk in v1.bookings for i in bk.issues]
    assert [(bk.counter_target, bk.tax_target, bk.partner_account) for bk in v1.bookings] == [
        ("8400", None, "10000"),
        ("8400", "0002", "10000"),
    ]
    assert [bk.amount for bk in v1.bookings] == [119, 107]  # TTC par (catégorie, taux), rien d'arrondi
    # une décision ne vaut que pour SA catégorie et SON sens : l'achat reste bloqué
    assert by_number(p)["NR-v-10"].target_status == RecordStatus.BLOCKED


def test_category_value_is_validated_like_an_account_and_decisions_are_scoped(cat):
    from uie_core.accounting import MappingInvalid

    with pytest.raises(MappingInvalid):
        decide(cat, LookupKind.CATEGORY, SALES, "84")  # longueur du plan cible (4 chiffres)
    decide(cat, LookupKind.CATEGORY, PURCHASE, "3400")
    rows = cat["session"].scalars(select(ExportValueMappingRecord)).all()
    assert [(r.kind, r.source_ref, r.target_value, r.instance_id) for r in rows] == [
        ("CATEGORY", PURCHASE, "3400", cat["inst"].id)  # par profil (organisation × format) et par source
    ]


def test_preview_writes_nothing_and_never_calls_the_source_for_writes(cat):
    session = cat["session"]
    before = session.scalar(select(func.count()).select_from(ExportValueMappingRecord))
    preview(cat)
    assert session.scalar(select(func.count()).select_from(ExportValueMappingRecord)) == before
    assert cat["server"].writes == []
