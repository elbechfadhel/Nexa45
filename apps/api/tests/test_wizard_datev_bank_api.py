"""« Prepare data for DATEV » : relevé bancaire (domaine 4), par la Supplier API. Fichiers INVENTÉS qui
reproduisent les pièges de la vérité terrain ; cibles : specs DATEV générées localement (module sauté si
absentes). Rien n'est envoyé à DATEV."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from test_wizard_datev_api import (  # noqa: F401 - fixture et aides partagées
    BUCHUNGEN,
    CUSTOMERS,
    INVOICES,
    _by,
    _check_against_spec,
    _download,
    _flow,
    _rows,
    _target,
    pytestmark,
    world,
)
from uie_core.exports.open_items import OpenItemRegister
from uie_core.exports.records import ExportAccountAssignmentRecord
from uie_core.identity import Organization


def _folder(c: Any) -> None:
    """Même dossier que les domaines précédents : clients puis factures de sortie exportés."""
    customers = _flow(
        c, "Debitoren/Kreditoren 5", CUSTOMERS, "Kundenliste.csv", {"partner:kind": "ORGANIZATION"}
    )
    assert _download(c, customers).status_code == 200
    invoices = _flow(
        c,
        "Buchungsstapel 13",
        INVOICES,
        "Rechnungen.csv",
        {"setting:lock_bookings": "0", "accounting:supply_kind": "GOODS"},
    )
    assert _download(c, invoices).status_code == 200


def test_exported_invoices_fill_the_open_item_register_of_the_folder_only(world):  # noqa: F811
    """Registre des factures ouvertes (D4) : une ligne par facture ÉCRITE (numéro, partenaire, rôle, montant
    brut signé, date), une seule fois ; nom et IBAN du partenaire retenus avec son compte (D5)."""
    c, session = world["client"], world["session"]
    _folder(c)
    org = session.query(Organization).filter_by(slug="acme").one()
    items = {
        i.number: i for i in OpenItemRegister(session, org.id, "default", "datev", "buchungsstapel").items()
    }
    assert set(items) == {"RE-1", "RE-2", "RE-3", "RE-4", "RE-7", "RE-8"}  # les factures écrites seulement
    re1 = items["RE-1"]
    assert (re1.role, re1.partner_ref, re1.amount, re1.open_amount) == (
        "CUSTOMER",
        "file:1001",
        Decimal("1545.81"),
        Decimal("1545.81"),
    )
    assert (re1.account, re1.partner_iban) == ("11001", "DE89370400440532013000")
    assert items["RE-8"].amount == Decimal("-297.50")  # avoir : négatif
    müller = session.query(ExportAccountAssignmentRecord).filter_by(source_ref="file:1001").one()
    assert (müller.partner_name, müller.iban) == ("Muster Bürotechnik GmbH", "DE89370400440532013000")
    # le même fichier une seconde fois : rien de neuf, le registre ne change pas
    again = _flow(c, "Buchungsstapel 13", INVOICES, "Rechnungen.csv", {"accounting:supply_kind": "GOODS"})
    assert again["preview"]["rows"] == 0
    assert len(OpenItemRegister(session, org.id, "default", "datev", "buchungsstapel").items()) == 6
    # un autre dossier n'en voit rien
    assert OpenItemRegister(session, org.id, "other", "datev", "buchungsstapel").items() == []


def _camt(entries: list[tuple[str, str, str, str, str, str, str]], opening: str, closing: str) -> str:
    """Relevé CAMT.053 INVENTÉ : (date, CRDT|DBIT, montant, contrepartie, IBAN, motif, référence)."""
    body = ""
    for day, side, amount, name, iban, purpose, ref in entries:
        party = "Dbtr" if side == "CRDT" else "Cdtr"
        body += (
            f"<Ntry><Amt Ccy='EUR'>{amount}</Amt><CdtDbtInd>{side}</CdtDbtInd><Sts><Cd>BOOK</Cd></Sts>"
            f"<BookgDt><Dt>{day}</Dt></BookgDt><ValDt><Dt>{day}</Dt></ValDt><AcctSvcrRef>{ref}</AcctSvcrRef>"
            f"<NtryDtls><TxDtls><RltdPties><{party}><Pty><Nm>{name}</Nm></Pty></{party}>"
            f"<{party}Acct><Id><IBAN>{iban}</IBAN></Id></{party}Acct></RltdPties>"
            f"<RmtInf><Ustrd>{purpose}</Ustrd></RmtInf></TxDtls></NtryDtls></Ntry>"
        )
    return (
        "<?xml version='1.0' encoding='UTF-8'?>"
        "<Document xmlns='urn:iso:std:iso:20022:tech:xsd:camt.053.001.08'><BkToCstmrStmt><Stmt><Id>1</Id>"
        f"<Acct><Id><IBAN>{BANK_IBAN}</IBAN></Id><Ccy>EUR</Ccy></Acct>"
        f"<Bal><Tp><CdOrPrtry><Cd>OPBD</Cd></CdOrPrtry></Tp><Amt Ccy='EUR'>{opening}</Amt>"
        "<CdtDbtInd>CRDT</CdtDbtInd><Dt><Dt>2026-09-30</Dt></Dt></Bal>"
        f"<Bal><Tp><CdOrPrtry><Cd>CLBD</Cd></CdOrPrtry></Tp><Amt Ccy='EUR'>{closing}</Amt>"
        "<CdtDbtInd>CRDT</CdtDbtInd><Dt><Dt>2026-10-31</Dt></Dt></Bal>"
        f"{body}</Stmt></BkToCstmrStmt></Document>"
    )


BANK_IBAN = "DE38850503001234567890"
MOVEMENTS = [
    ("2026-10-01", "CRDT", "1545.81", "Muster Bürotechnik GmbH", "DE89370400440532013000", "RE-1", "B1"),
    (
        "2026-10-02",
        "CRDT",
        "50.00",
        "Beispiel Architekten PartG",
        "DE00000000000000000002",
        "Teilzahlung RE-2",
        "B2",
    ),
    ("2026-10-03", "DBIT", "12.90", "", "", "Entgelt Kontoführung 09/2026", "B3"),
    ("2026-10-04", "CRDT", "99.00", "Max Mustermann", "DE00000000000000000099", "Danke", "B4"),
    (
        "2026-10-05",
        "DBIT",
        "1500.00",
        "Immobilien Vogel GbR",
        "DE27100777770209299700",
        "Miete 10/2026",
        "B5",
    ),
]
STATEMENT = _camt(MOVEMENTS, "1000.00", "1181.91")


def _bank(c: Any, content: str, name: str, extra: dict[str, str] | None = None) -> dict[str, Any]:
    """Même parcours que `_flow`, fichier en UTF-8 (un relevé CAMT déclare son encodage UTF-8)."""
    files = {"file": (name, content.encode("utf-8"), "application/xml")}
    start = c.post(
        "/api/wizard/quick-start",
        files=files,
        data={"payload": json.dumps({"target": _target(c, "Buchungsstapel 13")})},
    )
    assert start.status_code == 200, start.text
    body = start.json()
    choices = dict(body["choices"]) | {"setting:lock_bookings": "0"} | (extra or {})
    proposal = {
        k: body["proposal"][k] for k in ("title", "summary", "entity", "template_name", "configuration")
    }
    proposal |= {
        "rows": [],
        "notes": [],
        "fields": [
            {k: v for k, v in f.items() if k not in ("verified", "allowed", "reference")}
            for f in body["proposal"]["fields"]
        ],
    }
    payload = {
        "answers": body["answers"],
        "proposal": proposal,
        "decisions": body["decisions"],
        "fills": [],
        "choices": choices,
    }
    preview = c.post("/api/wizard/file/preview", files=files, data={"payload": json.dumps(payload)})
    assert preview.status_code == 200, preview.text
    return {"start": body, "preview": preview.json(), "files": files, "payload": payload}


def test_a_camt_statement_is_booked_on_the_bank_account_and_settles_the_exported_invoices(world):  # noqa: F811
    """CAMT.053 → Buchungsstapel : type et format reconnus sans IA ; Konto = compte bancaire du dossier (1200,
    à valider) ; paiement rapproché : Gegenkonto = compte du client, Belegfeld 1 = facture réglée ; frais sur
    la table ; payeur inconnu et loyer jamais devinés ; règlements inscrits au registre au téléchargement ;
    compte bancaire repris pour le même IBAN."""
    c, session = world["client"], world["session"]
    _folder(c)
    flow = _bank(c, STATEMENT, "Kontoauszug.camt053.xml")
    start, pre = flow["start"], flow["preview"]
    assert (start["data_type"], start["data_type_from"]) == ("Bank statement", "rules")
    applied = {q["decision"]: (q["default"], q["default_from"]) for q in start["applied"]}
    assert applied["setting:bank_account"] == ("1200", "usual_default")
    assert applied["map:CATEGORY:bank:bank_fees"] == ("4970", "usual_default")
    rent = "map:CATEGORY:bank:rent:DE27100777770209299700"
    assert rent in {q["decision"] for q in start["questions"]}  # loyer : jamais deviné
    reasons = {e["row"]: e["kind"] for e in pre["excluded_rows"]}
    assert reasons[4] == "unknown_payment"
    balance = [i for i in pre["value_issues"] if i["kind"] == "bank_balance_checked"]
    assert (
        balance[0]["message"]
        == "Balance checked: opening 1000.00 € + movements 181.91 € = closing 1181.91 €."
    )
    partial = [i for i in pre["value_issues"] if i["kind"] == "bank_partial"]
    assert partial[0]["message"] == "1 partial payment: invoice RE-2, 57.00 € still open."

    flow = _bank(c, STATEMENT, "Kontoauszug.camt053.xml", {rent: "4210", "setting:bank_account": "1210"})
    res = _download(c, flow)
    assert res.status_code == 200, res.text
    assert _check_against_spec(BUCHUNGEN, res.content) == []
    _, rows = _rows(res.content)
    paid = _by(rows, 11, "RE-1")  # paiement exact : lettrage par le numéro de la facture
    assert [paid[i] for i in (0, 1, 6, 7, 8, 9)] == ["1545,81", '"S"', "1210", "11001", '""', "0110"]
    assert [_by(rows, 11, "RE-2")[i] for i in (0, 1, 7)] == ["50,00", '"S"', "11002"]  # partiel
    fees = next(r for r in rows if r[7] == "4970")
    assert [fees[i] for i in (0, 1, 6, 8)] == ["12,90", '"H"', "1210", '""']
    assert next(r for r in rows if r[7] == "4210")[1] == '"H"'  # loyer : votre réponse
    org = session.query(Organization).filter_by(slug="acme").one()
    items = {
        i.number: i for i in OpenItemRegister(session, org.id, "default", "datev", "buchungsstapel").items()
    }
    assert (items["RE-1"].open_amount, items["RE-2"].open_amount) == (Decimal("0.00"), Decimal("57.00"))
    # relevé suivant du même compte : compte bancaire repris pour cet IBAN
    again = _bank(c, _camt(MOVEMENTS[:1], "1000.00", "2545.81"), "Kontoauszug-2.camt053.xml")["start"]
    applied = {q["decision"]: (q["default"], q["default_from"]) for q in again["applied"]}
    assert applied["setting:bank_account"] == ("1210", "your_settings")


def _csv_line(day: str, text: str, purpose: str, name: str, iban: str, amount: str) -> str:
    cells = [BANK_IBAN, day, day, text, purpose, "", "", "NOTPROVIDED", "", "", "", name, iban, "", amount]
    return ";".join(f'"{c}"' for c in [*cells, "EUR", "Umsatz gebucht"]) + "\n"


CSV_HEADER = (
    '"Auftragskonto";"Buchungstag";"Valutadatum";"Buchungstext";"Verwendungszweck";"Glaeubiger ID";'
    '"Mandatsreferenz";"Kundenreferenz (End-to-End)";"Sammlerreferenz";"Lastschrift Ursprungsbetrag";'
    '"Auslagenersatz Ruecklastschrift";"Beguenstigter/Zahlungspflichtiger";"Kontonummer/IBAN";'
    '"BIC (SWIFT-Code)";"Betrag";"Waehrung";"Info"\n'
)
MUSTER = ("Muster Bürotechnik GmbH", "DE89370400440532013000")
EXPORT = (
    CSV_HEADER
    # même mouvement que le relevé CAMT (paiement de RE-1) : jamais comptabilisé deux fois
    + _csv_line("01.10.26", "GUTSCHRIFT", "RE-1", *MUSTER, "1545,81")
    + _csv_line("04.11.26", "ENTGELTABSCHLUSS", "Entgelt Kontoführung 10/2026", "", "", "-12,90")
    + _csv_line("04.11.26", "ENTGELTABSCHLUSS", "Entgelt Kontoführung 10/2026", "", "", "-12,90")
)


def test_a_bank_export_never_books_a_movement_twice_and_says_it_has_no_balances(world):  # noqa: F811
    """Export « CSV-CAMT » après le relevé CAMT du même compte : le mouvement déjà exporté par le CAMT n'est
    pas écrit une seconde fois ; deux lignes identiques sans référence de la banque : une fois (décision
    « Export both » proposée) ; dates « TT.MM.JJ » de ce format : siècle déduit et listé (D7) ; soldes : non
    contrôlables, dit."""
    c = world["client"]
    _folder(c)
    rent = {"map:CATEGORY:bank:rent:DE27100777770209299700": "4210"}
    first = _bank(c, STATEMENT, "Kontoauszug.camt053.xml", rent)
    assert _download(c, first).status_code == 200
    flow = _bank(c, EXPORT, "Umsaetze.csv")
    start, pre = flow["start"], flow["preview"]
    assert start["data_type"] == "Bank statement"
    applied = {q["decision"]: q["default"] for q in start["applied"]}
    assert applied["bank:duplicates"] == "once" and applied["century:1"] == "20"
    reasons = {e["row"]: e["kind"] for e in pre["excluded_rows"]}
    assert reasons == {1: "already_exported_movement", 3: "duplicate"}
    assert pre["rows"] == 1  # les frais de novembre, une fois
    kinds = {t["kind"] for t in pre["value_transforms"]}
    assert "date_century" in kinds  # siècle complété : listé
    assert any(i["kind"] == "bank_no_balance" for i in pre["value_issues"])


NOVEMBER = (
    CSV_HEADER
    + _csv_line(
        "03.11.26",
        "DAUERAUFTRAG",
        "Miete 11/2026",
        "Immobilien Vogel GbR",
        "DE27100777770209299700",
        "-1500,00",
    )
    + _csv_line("05.11.26", "GUTSCHRIFT", "Rechnung RE-6", "Alpen Handels GmbH", "", "3200,00")
)


def test_bank_questions_stay_few_and_answers_are_remembered_for_the_folder(world):  # noqa: F811
    """Au plus 3 questions par fichier ; le loyer de novembre reprend la réponse d'octobre (même IBAN de la
    contrepartie, « from your settings ») ; numéro inconnu mais même partenaire et même montant qu'une facture
    ouverte : proposé « à confirmer », jamais rapproché sans la réponse."""
    c = world["client"]
    _folder(c)
    rent = "map:CATEGORY:bank:rent:DE27100777770209299700"
    october = _bank(c, STATEMENT, "Kontoauszug.camt053.xml", {rent: "4210"})
    assert len(october["start"]["questions"]) <= 3
    assert _download(c, october).status_code == 200

    flow = _bank(c, NOVEMBER, "Umsaetze.csv")
    start, pre = flow["start"], flow["preview"]
    asked = {q["decision"] for q in start["questions"]}
    assert len(asked) <= 3 and rent not in asked
    applied = {q["decision"]: (q["default"], q["default_from"]) for q in start["applied"]}
    assert applied[rent] == ("4210", "your_settings")
    confirm = [q for q in pre["value_questions"] if q["kind"] == "bank_confirm"]
    assert [q["title"] for q in confirm] == ["Payment from Alpen Handels GmbH: is it invoice RE-3?"]
    assert {e["row"]: e["kind"] for e in pre["excluded_rows"]} == {2: "bank_confirm"}

    flow = _bank(c, NOVEMBER, "Umsaetze.csv", {confirm[0]["key"]: "settle"})
    assert flow["preview"]["excluded_rows"] == []
    res = _download(c, flow)
    assert res.status_code == 200, res.text
    _, rows = _rows(res.content)
    paid = _by(rows, 11, "RE-3")
    assert [paid[i] for i in (0, 1, 6, 7)] == ["3200,00", '"S"', "1200", "11004"]
    assert next(r for r in rows if r[7] == "4210")[1] == '"H"'
