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
