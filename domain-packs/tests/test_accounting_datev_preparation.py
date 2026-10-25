"""ACCOUNTING phase 4 : Accounting CDM → préparation DATEV Buchungsstapel (APERÇU validé, aucun fichier).

Source : schéma RÉEL d'accounting17 (l10n_de, SKR03) et pièces INVENTÉES, servis par le faux serveur
Odoo. Cible : spec RÉELLE Buchungsstapel v13 du Registry (locale, non versionnée : tests sautés si elle
est absente). Comptes de personne : posés comme l'export Debitoren/Kreditoren les attribue (table
existante, lecture seule côté ACCOUNTING). Les valeurs de correspondance saisies ici (comptes, clés de
taxe) sont des VALEURS DE TEST : aucune clé DATEV n'est déduite ni recommandée par le moteur.
"""

# ruff: noqa: F811  (fixture « world » importée du module de lecture et utilisée en paramètre)
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from test_accounting_read import COMPANY, JANUARY, _answer, world  # noqa: F401  (fixture)
from uie_cdm import (
    AccountingDocument,
    DocumentLine,
    DocumentStatus,
    DocumentType,
    LedgerAccountRef,
    LineKind,
    PartnerRef,
    PartnerRole,
    TaxComputation,
    TaxRef,
    TaxScope,
)
from uie_connectors.datev import DatevSchemaLoader
from uie_connectors.odoo.discovery import READ_ONLY_METHODS
from uie_core.accounting import (
    AccountingPeriod,
    AccountingTargetError,
    AccountingTargetNotReady,
    AccountingTargetService,
    MappingInvalid,
    MappingOrigin,
    MappingStatus,
    Side,
    gross_bookings,
)
from uie_core.exports import ExportAccountAssignmentRecord, ExportProfileRecord
from uie_core.identity import GO45_DEV_ORG_ID, SystemContext
from uie_core.mapping import LookupKind, RecordStatus
from uie_core.registry import RegistryService

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "var" / "datev" / "specs" / "extf_700" / "buchungsstapel_v13.yaml"
real_spec = pytest.mark.skipif(not SPEC.exists(), reason="spec DATEV locale absente (spec_builder)")
CTX = SystemContext("test")
DATEV, BS = "datev", "buchungsstapel"
SHARED = {  # réglages du dossier DATEV, saisis une fois dans l'export Debitoren/Kreditoren (valeurs de test)
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


# --------------------------------------------------------------------------- #
# Écritures dérivées (modèle générique du Core, sans format cible)
# --------------------------------------------------------------------------- #
def _items(world):  # noqa: ANN001, ANN202
    _answer(world)
    return {i.document.reference: i for i in world["svc"].read(world["inst"].id, COMPANY, JANUARY).items}


def test_customer_invoice_gives_one_gross_booking_per_account_and_tax(world):
    bs = gross_bookings(_items(world)["UIE-ACC-B"].document)
    assert bs.issues == []
    assert [
        (b.counter_account.code, b.tax.name, b.net_amount, b.gross_amount, b.amount, b.partner_side, b.lines)
        for b in bs.bookings
    ] == [
        ("8400", "19%", Decimal("-550.0"), Decimal("-654.5"), Decimal("654.5"), Side.DEBIT, [0, 1]),
        ("8300", "7%", Decimal("-79.8"), Decimal("-85.39"), Decimal("85.39"), Side.DEBIT, [2]),
    ]
    assert bs.partner_total == Decimal("739.89")  # = somme des écritures : équilibre vérifié
    assert [b.tax_amount for b in bs.bookings] == [Decimal("-104.5"), Decimal("-5.59")]  # = lignes TAX
    assert all(isinstance(b.amount, Decimal) for b in bs.bookings)


def test_partner_side_follows_the_cdm_sign_for_every_document_type(world):
    items = _items(world)
    sides = {
        ref: [b.partner_side for b in gross_bookings(items[ref].document).bookings]
        for ref in ("UIE-ACC-A", "UIE-ACC-C", "UIE-ACC-D", "UIE-ACC-E")
    }
    assert sides == {
        "UIE-ACC-A": [Side.DEBIT],  # facture client : le client doit
        "UIE-ACC-C": [Side.CREDIT],  # avoir client : sens inverse
        "UIE-ACC-D": [Side.CREDIT],  # facture fournisseur : le fournisseur est crédité
        "UIE-ACC-E": [Side.DEBIT],  # avoir fournisseur : sens inverse
    }
    for ref in sides:
        assert gross_bookings(items[ref].document).issues == []


def test_multiple_taxes_on_a_line_give_no_booking(world):
    bs = gross_bookings(_items(world)["UIE-ACC-L"].document)
    assert bs.bookings == [] and [i.code for i in bs.issues] == ["MULTIPLE_TAXES"]


def _doc(lines: list[DocumentLine], total: str = "119.00", tax: str = "19.00") -> AccountingDocument:
    return AccountingDocument(
        source_ref="1",
        document_type=DocumentType.CUSTOMER_INVOICE,
        status=DocumentStatus.POSTED,
        number="INV-1",
        accounting_date=JANUARY.first_day,
        currency="EUR",
        partner=PartnerRef(source_ref="7", role=PartnerRole.CUSTOMER, name="Test GmbH"),
        total_amount=Decimal(total),
        tax_amount=Decimal(tax),
        lines=lines,
    )


VAT = TaxRef(
    source_ref="t1", name="19%", computation=TaxComputation.PERCENT, rate=Decimal("19"), scope=TaxScope.SALE
)
REV = LedgerAccountRef(source_ref="a1", code="8400")
REC = LedgerAccountRef(source_ref="a2", code="1410")
OUT = LedgerAccountRef(source_ref="a3", code="1776")


def test_unbalanced_and_inconsistent_tax_documents_are_never_corrected():
    item = DocumentLine(
        kind=LineKind.ITEM, account=REV, amount=Decimal("-100"), gross_amount=Decimal("-119"), taxes=[VAT]
    )
    tax = DocumentLine(kind=LineKind.TAX, account=OUT, amount=Decimal("-19"), taxes=[VAT])
    assert (
        gross_bookings(
            _doc([item, tax, DocumentLine(kind=LineKind.OPEN_ITEM, account=REC, amount=Decimal("119"))])
        ).issues
        == []
    )
    unbalanced = gross_bookings(
        _doc([item, tax, DocumentLine(kind=LineKind.OPEN_ITEM, account=REC, amount=Decimal("118.99"))])
    )
    assert [i.code for i in unbalanced.issues] == ["UNBALANCED_BOOKINGS"]
    wrong_tax = DocumentLine(kind=LineKind.TAX, account=OUT, amount=Decimal("-18.99"), taxes=[VAT])
    mismatch = gross_bookings(
        _doc([item, wrong_tax, DocumentLine(kind=LineKind.OPEN_ITEM, account=REC, amount=Decimal("119"))])
    )
    assert "TAX_MISMATCH" in [i.code for i in mismatch.issues]


# --------------------------------------------------------------------------- #
# Préparation Buchungsstapel (Registry v13 réel)
# --------------------------------------------------------------------------- #
@pytest.fixture
def prep(world):  # noqa: ANN001, ANN201
    RegistryService(world["session"], CTX).publish_snapshot(
        DatevSchemaLoader({"spec": SPEC}).build_snapshot()
    )
    _answer(world)
    world["target"] = AccountingTargetService(world["session"], CTX, world["svc"])
    return world


def _shared_profile(world, **values):  # noqa: ANN001, ANN003, ANN202
    row = ExportProfileRecord(
        organization_id=GO45_DEV_ORG_ID,
        source_model="partner",
        target_system=DATEV,
        target_product="debitoren-kreditoren",
        values=SHARED | values,
    )
    world["session"].add(row)
    world["session"].flush()
    return row


def _partner_accounts(world, profile) -> dict[tuple[str, str], str]:  # noqa: ANN001
    """Numéros tels que l'export Debitoren/Kreditoren les a attribués (un par partenaire et par rôle)."""
    numbers: dict[tuple[str, str], str] = {}
    nxt = {"CUSTOMER": 10000, "SUPPLIER": 70000}
    for item in world["svc"].read(world["inst"].id, COMPANY, JANUARY).items:
        key = (item.document.partner.source_ref, item.document.partner.role.value)
        if key not in numbers:
            numbers[key] = str(nxt[key[1]])
            nxt[key[1]] += 1
            world["session"].add(
                ExportAccountAssignmentRecord(
                    profile_id=profile.id,
                    instance_id=world["inst"].id,
                    source_ref=key[0],
                    role=key[1],
                    account_number=numbers[key],
                    origin="ASSIGNED",
                )
            )
    world["session"].flush()
    return numbers


def _ready_world(prep, tax_keys: dict[str, str | None] | None = None):  # noqa: ANN001, ANN202
    """Dossier réglé, comptes de personne attribués, suggestions de comptes confirmées, taxes décidées."""
    profile = _shared_profile(prep)
    numbers = _partner_accounts(prep, profile)
    svc, iid = prep["target"], prep["inst"].id
    svc.save_settings(iid, DATEV, BS, {"currency": "EUR"})
    first = svc.preview(iid, COMPANY, JANUARY, DATEV, BS)
    for m in first.mappings:
        if m.kind == LookupKind.ACCOUNT and m.status == MappingStatus.SUGGESTED:
            svc.decide(
                iid,
                DATEV,
                BS,
                m.kind,
                m.source_ref,
                value=m.suggestion,
                accepted_suggestion=MappingOrigin.IDENTICAL_CODE,
                source_code=m.source_code,
            )
        if m.kind == LookupKind.TAX:
            key = (tax_keys or {}).get(m.source_code)
            svc.decide(
                iid,
                DATEV,
                BS,
                m.kind,
                m.source_ref,
                value=key,
                no_value=key is None,
                source_code=m.source_code,
            )
    return svc.preview(iid, COMPANY, JANUARY, DATEV, BS), numbers


def _by_ref(preview):  # noqa: ANN001, ANN202
    return {d.reference: d for d in preview.documents}


@real_spec
def test_settings_are_shared_with_the_partner_export_and_required(prep):
    svc, iid = prep["target"], prep["inst"].id
    assert [(t.system, t.product, t.versions) for t in svc.targets()] == [(DATEV, BS, ["13"])]
    with pytest.raises(AccountingTargetNotReady):
        svc.preview(iid, COMPANY, JANUARY, DATEV, BS)  # aucun dossier DATEV
    _shared_profile(prep)
    settings = svc.settings(iid, DATEV, BS)
    assert [v.name for v in settings.values if v.inherited] == [
        "advisor_number",
        "client_number",
        "fiscal_year_start",
        "account_length",
        "chart_of_accounts",
        "origin_code",
    ]
    assert settings.missing == ["Currency of the DATEV client (WKZ)"]
    settings = svc.save_settings(iid, DATEV, BS, {"currency": "EUR"})
    assert settings.ready and settings.shared_with == "DATEV Debitoren/Kreditoren (EXTF)"


@real_spec
def test_version_is_explicit_and_bound_to_the_registry(prep):
    _shared_profile(prep)
    svc, iid = prep["target"], prep["inst"].id
    svc.save_settings(iid, DATEV, BS, {"currency": "EUR"})
    preview = svc.preview(iid, COMPANY, JANUARY, DATEV, BS, version="13")
    assert (preview.format_version, preview.snapshot_label) == ("13", "datev buchungsstapel 13 @1.0.0")
    with pytest.raises(AccountingTargetError, match="Version 12"):
        svc.preview(iid, COMPANY, JANUARY, DATEV, BS, version="12")  # en-tête officiel : 13 seulement


@real_spec
def test_without_decisions_nothing_is_ready_and_suggestions_are_not_applied(prep):
    _shared_profile(prep)
    svc, iid = prep["target"], prep["inst"].id
    svc.save_settings(iid, DATEV, BS, {"currency": "EUR"})
    p = svc.preview(iid, COMPANY, JANUARY, DATEV, BS)
    accounts = {m.source_code: m for m in p.mappings if m.kind == LookupKind.ACCOUNT}
    taxes = {m.source_code: m for m in p.mappings if m.kind == LookupKind.TAX}
    assert sorted(accounts) == ["3400", "8200", "8300", "8400"]  # comptes de contrepartie seulement
    assert all(
        m.status == MappingStatus.SUGGESTED and m.suggestion == m.source_code for m in accounts.values()
    )
    assert (
        accounts["8400"].origin == MappingOrigin.IDENTICAL_CODE
        and "Confirm it only" in accounts["8400"].reason
    )
    assert sorted(taxes) == ["19%", "19% I", "7%"]
    assert all(m.status == MappingStatus.MISSING and m.suggestion is None for m in taxes.values())  # D4
    # éligibilité source (phase 3) ≠ préparation cible : 10 éligibles, aucune prête
    assert (p.eligible, p.count(RecordStatus.READY), p.count(RecordStatus.BLOCKED), p.not_prepared) == (
        10,
        0,
        11,
        2,
    )
    b = _by_ref(p)["UIE-ACC-B"]
    messages = [i.message for bk in b.bookings for i in bk.issues]
    assert (
        "Customer Lena Hoffmann has no DATEV account yet: export your customers & suppliers " in messages[0]
    )
    assert any("the suggested DATEV value 8400 is not confirmed yet" in m for m in messages)
    assert any("Tax key (BU-Schlüssel) 19% has no DATEV mapping yet" in m for m in messages)


@real_spec
def test_re_2026_00002_becomes_two_ready_bookings(prep):
    p, numbers = _ready_world(prep)
    assert (p.count(RecordStatus.READY), p.count(RecordStatus.WARNING), p.count(RecordStatus.BLOCKED)) == (
        8,  # A, B, C, D, E, F1, F2, I
        2,  # G (date de pièce d'un autre mois), H (Buchungstext > 60 : omis)
        1,  # L (deux taxes sur une ligne)
    )
    b = _by_ref(p)["UIE-ACC-B"]
    assert (b.number, b.target_status, b.eligible, b.balance) == (
        "RE/2026/00002",
        RecordStatus.READY,
        True,
        0,
    )
    lena = b.bookings[0].partner_account
    assert (
        lena in numbers.values() and len(lena) == 5
    )  # Sachkontenlänge 4 + 1, attribué par l'export partenaires
    cells = [{c.field: c.value for c in bk.cells} for bk in b.bookings]
    assert cells == [
        {
            "001": "654,50",
            "002": '"S"',
            "003": '"EUR"',
            "007": lena,
            "008": "8400",
            "010": "1501",
            "011": '"RE/2026/00002"',
            "014": '"Lena Hoffmann, RE/2026/00002"',
        },
        {
            "001": "85,39",
            "002": '"S"',
            "003": '"EUR"',
            "007": lena,
            "008": "8300",
            "010": "1501",
            "011": '"RE/2026/00002"',
            "014": '"Lena Hoffmann, RE/2026/00002"',
        },
    ]  # 009 vide : « aucune clé » décidée (valeur de test)
    first = b.bookings[0]
    assert (first.amount, first.net_amount, first.gross_amount) == (
        Decimal("654.5"),
        Decimal("-550.0"),
        Decimal("-654.5"),
    )
    assert [(sl.position, sl.description, sl.account_code, sl.amount) for sl in first.source_lines] == [
        (0, "Beratung (Stunde)", "8400", Decimal("-360.0")),
        (1, "Beratung Planung", "8400", Decimal("-190.0")),
    ]
    assert first.tax_no_value and first.tax_target is None


@real_spec
def test_every_document_type_keeps_its_direction(prep):
    p, numbers = _ready_world(prep)
    docs = _by_ref(p)
    side = {ref: [c.value for bk in docs[ref].bookings for c in bk.cells if c.field == "002"] for ref in docs}
    assert side["UIE-ACC-A"] == ['"S"'] and side["UIE-ACC-C"] == ['"H"']
    assert side["UIE-ACC-D"] == ['"H"'] and side["UIE-ACC-E"] == ['"S"']
    d = docs["UIE-ACC-D"].bookings[0]
    assert (d.counter_target, d.amount) == ("3400", Decimal("1237.6"))
    assert d.partner_account in numbers.values() and d.partner_account.startswith("7")  # plage créanciers
    assert all(
        docs[r].target_status == RecordStatus.READY
        for r in ("UIE-ACC-A", "UIE-ACC-C", "UIE-ACC-D", "UIE-ACC-E")
    )
    # Rhein Logistik AG : client ET fournisseur → deux comptes différents
    f1, f2 = docs["UIE-ACC-F1"].bookings[0], docs["UIE-ACC-F2"].bookings[0]
    assert f1.partner_account != f2.partner_account


@real_spec
def test_draft_cancelled_and_multi_tax_are_never_ready(prep):
    p, _ = _ready_world(prep)
    docs = _by_ref(p)
    assert docs["UIE-ACC-J"].target_status is None and "Draft" in docs["UIE-ACC-J"].not_prepared_reason
    assert docs["UIE-ACC-K"].target_status is None and "Cancelled" in docs["UIE-ACC-K"].not_prepared_reason
    lt = docs["UIE-ACC-L"]
    assert lt.target_status == RecordStatus.BLOCKED and lt.bookings == []
    assert [i.message for i in lt.issues] == [
        "In your system: A line has several taxes",
        "A line has several taxes",
    ]


@real_spec
def test_warnings_are_explained_and_nothing_is_truncated(prep):
    p, _ = _ready_world(prep)
    docs = _by_ref(p)
    g = docs["UIE-ACC-G"]  # facturée en décembre, comptabilisée en janvier
    assert g.target_status == RecordStatus.WARNING
    assert [i.message for i in g.issues] == ["The document date is in another month than the accounting date"]
    h = docs["UIE-ACC-H"]  # partenaire au nom de plus de 50 caractères : Buchungstext > 60
    assert h.target_status == RecordStatus.WARNING
    assert [i.message for i in h.bookings[0].issues] == [
        "Buchungstext is longer than 60 characters: left out"
    ]
    assert "014" not in {c.field for c in h.bookings[0].cells}


@real_spec
def test_confirmed_mappings_are_reused_and_suggestions_stay_suggestions(prep):
    _ready_world(prep)
    svc, iid = prep["target"], prep["inst"].id
    february = svc.preview(iid, COMPANY, AccountingPeriod.parse("2026-02"), DATEV, BS)
    m = {x.source_code: x for x in february.mappings}
    assert (
        m["8400"].status == MappingStatus.CONFIRMED and m["8400"].origin == MappingOrigin.SUGGESTION_ACCEPTED
    )
    assert m["19%"].status == MappingStatus.CONFIRMED and m["19%"].no_value
    assert february.count(RecordStatus.READY) == 1  # UIE-ACC-M, sans nouvelle décision
    # retirer une décision : la suggestion revient, l'écriture est de nouveau bloquée
    svc.clear(iid, DATEV, BS, LookupKind.ACCOUNT, m["8400"].source_ref)
    again = svc.preview(iid, COMPANY, AccountingPeriod.parse("2026-02"), DATEV, BS)
    assert {x.source_code: x.status for x in again.mappings}["8400"] == MappingStatus.SUGGESTED
    assert again.count(RecordStatus.BLOCKED) == 1


@real_spec
def test_mapping_values_are_validated_by_the_registry_and_settings(prep):
    _shared_profile(prep)
    svc, iid = prep["target"], prep["inst"].id
    with pytest.raises(MappingInvalid, match="must have 4 digits"):
        svc.decide(iid, DATEV, BS, LookupKind.ACCOUNT, "1151", value="84000")
    with pytest.raises(MappingInvalid, match="format expected by the target"):
        svc.decide(iid, DATEV, BS, LookupKind.TAX, "14", value="3")  # code Odoo l10n_de : pas une clé v13
    with pytest.raises(MappingInvalid, match="a value is required"):
        svc.decide(iid, DATEV, BS, LookupKind.ACCOUNT, "1151", value=None, no_value=True)
    row = svc.decide(iid, DATEV, BS, LookupKind.TAX, "14", value="1234")  # valeur de TEST, 4 chiffres
    assert (row.target_value, row.origin) == ("1234", "MANUAL")


@real_spec
def test_a_configured_tax_key_is_written(prep):
    p, _ = _ready_world(prep, tax_keys={"7%": "1234"})  # valeur de TEST
    b = _by_ref(p)["UIE-ACC-B"]
    assert [{c.field: c.value for c in bk.cells}.get("009") for bk in b.bookings] == [None, '"1234"']


@real_spec
def test_dossier_rules_block_documents(prep):
    profile = _shared_profile(prep, fiscal_year_start="2025-01-01")
    _partner_accounts(prep, profile)
    svc, iid = prep["target"], prep["inst"].id
    svc.save_settings(iid, DATEV, BS, {"currency": "USD"})
    b = _by_ref(svc.preview(iid, COMPANY, JANUARY, DATEV, BS))["UIE-ACC-B"]
    assert [i.message for i in b.issues] == [
        "The document currency EUR is not the currency of the DATEV client (USD)",
        "The accounting date is outside the fiscal year starting 2025-01-01",
    ]
    assert b.target_status == RecordStatus.BLOCKED


@real_spec
def test_preparation_never_writes_to_the_source(prep):
    _ready_world(prep)
    assert prep["fake"].methods_called <= READ_ONLY_METHODS | {"authenticate", "version"}
