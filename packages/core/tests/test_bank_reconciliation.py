"""Rapprochement d'un relevé bancaire avec le registre des factures ouvertes du dossier : règles seulement,
jamais par le seul montant. Factures, partenaires et relevé INVENTÉS (mêmes pièges que la vérité terrain du
domaine 4)."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import uie_domain_packs  # noqa: F401 - enregistre le tableau de source
from uie_cdm import DocumentType
from uie_core.domains.model import DomainKey
from uie_core.exports.open_items import OpenItem
from uie_core.sources.table import FileTableRegistry
from uie_core.wizard.cdm_bank import CONFIRM_KEY, documents_from_bank_rows, fingerprint

TABLE = FileTableRegistry.get(DomainKey.ACCOUNTING)
ACCOUNT = "DE38850503001234567890"


def item(
    number: str, partner: str, amount: str, role: str = "CUSTOMER", name: str = "", iban: str = ""
) -> OpenItem:
    return OpenItem(
        id=uuid.uuid4(),
        document_ref=f"file:{'purchase:' + partner + ':' if role == 'SUPPLIER' else ''}{number}",
        number=number,
        role=role,
        partner_ref=f"file:{partner}",
        partner_name=name or f"Partner {partner}",
        partner_iban=iban or None,
        account=("11" if role == "CUSTOMER" else "72") + partner[-3:],
        amount=Decimal(amount),
        open_amount=Decimal(amount),
        currency="EUR",
        document_date=date(2026, 9, 1),
    )


OPEN = [
    item("RE-2026042", "1001", "1545.81", name="Müller Bürotechnik GmbH", iban="DE89370400440532013000"),
    item("RE-2026033", "1005", "1166.20", name="Hofmann Logistik AG"),
    item("RE-2026011", "1015", "307.09", name="Schulz Sanitär GmbH"),
    item("RE-2026012", "1015", "759.02", name="Schulz Sanitär GmbH"),
    item("RE-2026015", "1006", "1642.03", name="Krüger IT-Service"),
    item("RE-2026005", "1002", "2058.28", name="Schäfer Architekten PartG"),
    item("RE-2026003", "1012", "420.63", name="Becker Medien UG (haftungsbeschränkt)"),
    item(
        "1001",
        "2001",
        "595.00",
        role="SUPPLIER",
        name="Papier Union Großhandel GmbH",
        iban="DE11111111111111111111",
    ),
    item(
        "1001",
        "2009",
        "952.00",
        role="SUPPLIER",
        name="Werbeagentur Pixel GmbH",
        iban="DE22222222222222222222",
    ),
    item("R-99200", "2001", "1190.00", role="SUPPLIER", name="Papier Union Großhandel GmbH"),
]


def mv(day: str, amount: str, name: str, purpose: str, iban: str = "", text: str = "") -> dict[str, str]:
    row = {
        "account_iban": ACCOUNT,
        "document_date": day,
        "amount": amount,
        "counterparty_name": name,
        "counterparty_iban": iban,
        "purpose": purpose,
        "bank_text": text,
        "currency": "EUR",
    }
    return {k: v for k, v in row.items() if v}


ROWS = [
    mv("2026-10-01", "1545.81", "Müller Bürotechnik GmbH", "RE-2026042", "DE89370400440532013000"),  # 1 exact
    mv("2026-10-05", "500.00", "Hofmann Logistik AG", "Teilzahlung RE-2026033"),  # 2 partiel
    mv("2026-10-06", "1066.11", "Schulz Sanitär GmbH", "RE-2026011, RE-2026012"),  # 3 deux factures
    mv("2026-10-07", "1609.19", "Krüger IT-Service", "RE-2026015 abzgl. 2% Skonto"),  # 4 escompte
    mv("2026-10-09", "250.00", "Max Mustermann", "Danke"),  # 5 payeur inconnu
    mv("2026-10-12", "-1190.00", "Papier Union Großhandel GmbH", "R-99200"),  # 6 achat
    mv(
        "2026-10-14", "-1500.00", "Immobilien Vogel GbR", "Miete 10/2026", "DE27100777770209299700"
    ),  # 7 loyer
    mv("2026-10-15", "-12.90", "", "Entgelt Kontoführung 09/2026"),  # 8 frais
    mv("2026-10-16", "1200.00", "", "Bareinzahlung Filiale"),  # 9 dépôt
    mv(
        "2026-10-19", "631.04", "Kartenzahlungen Abrechnungsstelle GmbH", "Kartenumsätze 18.10.2026"
    ),  # 10 cartes
    mv("2026-10-20", "-2310.40", "Finanzamt Dresden-Nord", "USt-VZ 09/2026 StNr 203/123/45678"),  # 11 impôt
    mv("2026-10-22", "2085.28", "Schäfer Architekten PartG", "RE-2026005"),  # 12 montant différent
    mv("2026-10-23", "420.63", "Becker Medien UG (haftungsbeschränkt)", "Rechnung RE-2026999"),  # 13 proposé
    mv("2026-10-27", "1850.00", "Lefèvre Distribution SARL", "RE-2026032"),  # 14 jamais exportée
    mv(
        "2026-10-28", "-595.00", "Papier Union Großhandel GmbH", "Rechnung 1001", "DE11111111111111111111"
    ),  # 15
]


def test_payments_are_reconciled_with_the_open_invoices_by_number_and_amount_never_by_amount_alone():
    t = documents_from_bank_rows(TABLE, ROWS, {}, "EUR", OPEN, {})
    docs = {t.row_of[d.source_ref]: d for d in t.documents}
    settled = {
        row: [(ln.settles.number, ln.gross_amount) for ln in d.lines if ln.settles] for row, d in docs.items()
    }
    assert settled[1] == [("RE-2026042", Decimal("-1545.81"))]  # entrée : le client au crédit
    assert docs[1].document_type == DocumentType.BANK_RECEIPT and docs[1].partner.source_ref == "file:1001"
    assert settled[2] == [("RE-2026033", Decimal("-500.00"))]  # partiel (D9)
    assert settled[3] == [("RE-2026011", Decimal("-307.09")), ("RE-2026012", Decimal("-759.02"))]
    assert settled[6] == [("R-99200", Decimal("1190.00"))]  # sortie : le fournisseur au débit
    assert settled[15] == [("1001", Decimal("595.00"))] and docs[15].partner.source_ref == "file:2001"  # D5
    cats = {row: d.lines[0].category.source_ref for row, d in docs.items() if d.lines[0].category}
    assert cats == {
        7: "bank:rent:DE27100777770209299700",
        8: "bank:bank_fees",
        9: "bank:cash_deposit",
        11: "bank:vat_prepayment",
    }
    reasons = {e.row: e.kind for e in t.skipped}
    assert reasons == {
        4: "discount",
        5: "unknown_payment",
        10: "card_settlement",
        12: "amount_differs",
        13: "bank_confirm",
        14: "unknown_invoice",
    }
    (partial,) = [i for i in t.issues if i.kind == "bank_partial"]
    assert (
        partial.message == "1 partial payment: invoice RE-2026033, 666.20 € still open."
        and partial.severity == "info"
    )
    assert t.settlements[docs[3].source_ref] == [
        ("file:RE-2026011", Decimal("307.09")),
        ("file:RE-2026012", Decimal("759.02")),
    ]
    (q,) = t.questions
    assert (
        q.key.startswith(CONFIRM_KEY) and q.answer is None and q.suggested is None
    )  # jamais présélectionnée
    assert q.title == "Payment from Becker Medien UG (haftungsbeschränkt): is it invoice RE-2026003?"
    yes = documents_from_bank_rows(TABLE, ROWS, {}, "EUR", OPEN, {q.key: "settle"})
    becker = next(d for d in yes.documents if yes.row_of[d.source_ref] == 13)
    assert becker.lines[0].settles.number == "RE-2026003"


def test_a_number_shared_by_two_suppliers_is_never_guessed_without_the_counterparty():
    rows = [mv("2026-10-28", "-595.00", "Someone Else", "Rechnung 1001")]
    t = documents_from_bank_rows(TABLE, rows, {}, "EUR", OPEN, {})
    assert t.documents == [] and t.skipped[0].kind == "ambiguous_invoice"
    by_name = documents_from_bank_rows(
        TABLE, [mv("2026-10-28", "-952.00", "Werbeagentur Pixel GmbH", "Rechnung 1001")], {}, "EUR", OPEN, {}
    )
    assert by_name.documents[0].partner.source_ref == "file:2009"  # nom exact de la contrepartie


def test_the_identity_of_a_movement_does_not_depend_on_the_file_format():
    camt = mv("2026-10-30", "166.92", "Bäckerei Groß e.K.", "RE-2026043", "DE02120300000000202051")
    csv = {**camt, "bank_reference": None, "bank_text": "GUTSCHRIFT", "value_date": "2026-10-30"}
    assert fingerprint(camt) == fingerprint({k: v for k, v in csv.items() if v})


def test_the_statement_balance_is_checked_or_said_to_be_uncheckable():
    rows = ROWS[:2]  # 1545.81 + 500.00
    ok = documents_from_bank_rows(
        TABLE, rows, {}, "EUR", OPEN, {}, balances=(Decimal("100"), Decimal("2145.81"))
    )
    (info,) = [i for i in ok.issues if i.kind == "bank_balance_checked"]
    assert (
        info.severity == "info"
        and "opening 100.00 € + movements 2045.81 € = closing 2145.81 €" in info.message
    )
    bad = documents_from_bank_rows(
        TABLE, rows, {}, "EUR", OPEN, {}, balances=(Decimal("100"), Decimal("2000"))
    )
    (serious,) = [i for i in bad.issues if i.kind == "bank_balance_differs"]
    assert serious.severity == "serious" and "but the closing balance is 2000.00 €" in serious.message
    csv = documents_from_bank_rows(TABLE, rows, {}, "EUR", OPEN, {}, balances=None)
    (none,) = [i for i in csv.issues if i.kind == "bank_no_balance"]
    assert none.message.endswith("the account balance cannot be checked in this format.")


def test_identical_movements_without_a_bank_reference_are_exported_once_unless_both_are_chosen():
    from uie_core.wizard.cdm_bank import DUPLICATE_KEY

    cloud = mv("2026-11-10", "-1190.00", "Papier Union Großhandel GmbH", "R-99200")
    t = documents_from_bank_rows(TABLE, [cloud, dict(cloud)], {}, "EUR", OPEN, {})
    assert len(t.documents) == 1 and [(e.row, e.kind) for e in t.skipped] == [(2, "duplicate")]
    (q,) = t.questions
    assert (q.key, q.suggested, [o.value for o in q.options]) == (DUPLICATE_KEY, "once", ["once", "both"])
    both = documents_from_bank_rows(TABLE, [cloud, dict(cloud)], {}, "EUR", OPEN, {DUPLICATE_KEY: "both"})
    # le second paiement trouve la facture déjà réglée par le premier (montant ouvert diminué dans le fichier)
    assert len(both.documents) == 1 and [e.kind for e in both.skipped] == ["settled"]
    # une référence de la banque différente : deux mouvements, jamais des doublons
    refs = [{**cloud, "bank_reference": "R1"}, {**cloud, "bank_reference": "R2"}]
    fees = [{**r, "purpose": "Entgelt Kontoführung 10/2026", "counterparty_name": ""} for r in refs]
    two = documents_from_bank_rows(TABLE, fees, {}, "EUR", OPEN, {})
    assert len(two.documents) == 2 and two.questions == []
    assert two.documents[0].source_ref != two.documents[1].source_ref
