"""Relevé bancaire CAMT.053 : lecture sûre, soldes, transactions comptabilisées seulement, mouvements groupés,
table au format d'un export bancaire. Relevés INVENTÉS."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from uie_core.imports.profile import ImportFileError, read_table
from uie_core.sources.camt import CamtError, read_camt


def _entry(amount: str, side: str, day: str, ref: str, details: str, status: str = "BOOK") -> str:
    return (
        f"<Ntry><Amt Ccy='EUR'>{amount}</Amt><CdtDbtInd>{side}</CdtDbtInd><Sts><Cd>{status}</Cd></Sts>"
        f"<BookgDt><Dt>{day}</Dt></BookgDt><ValDt><Dt>{day}</Dt></ValDt><AcctSvcrRef>{ref}</AcctSvcrRef>"
        f"<BkTxCd><Prtry><Cd>Gutschrift</Cd></Prtry></BkTxCd><NtryDtls>{details}</NtryDtls></Ntry>"
    )


def _tx(party: str, name: str, iban: str, purpose: str, amount: str = "") -> str:
    amt = f"<Amt Ccy='EUR'>{amount}</Amt>" if amount else ""
    return (
        f"<TxDtls>{amt}<RltdPties><{party}><Pty><Nm>{name}</Nm></Pty></{party}><{party}Acct><Id><IBAN>{iban}"
        f"</IBAN></Id></{party}Acct></RltdPties><RmtInf><Ustrd>{purpose}</Ustrd></RmtInf></TxDtls>"
    )


def statement(version: str = "08") -> bytes:
    entries = (
        _entry(
            "100.00", "CRDT", "2026-10-01", "R1", _tx("Dbtr", "Muster GmbH", "DE89370400440532013000", "RE-1")
        )
        + _entry(
            "40.00", "DBIT", "2026-10-02", "R2", _tx("Cdtr", "Papier AG", "DE02120300000000202051", "R-7")
        )
        + _entry(
            "30.00",
            "CRDT",
            "2026-10-03",
            "R3",
            _tx("Dbtr", "A GmbH", "DE1", "RE-2", "10.00") + _tx("Dbtr", "B GmbH", "DE2", "RE-3", "20.00"),
        )
        + _entry("5.00", "DBIT", "2026-10-04", "R4", _tx("Cdtr", "X", "DE3", "pending"), status="PDNG")
    )
    return (
        "<?xml version='1.0' encoding='UTF-8'?>"
        f"<Document xmlns='urn:iso:std:iso:20022:tech:xsd:camt.053.001.{version}'><BkToCstmrStmt>"
        "<GrpHdr><MsgId>1</MsgId></GrpHdr><Stmt><Id>1</Id>"
        "<Acct><Id><IBAN>DE38850503001234567890</IBAN></Id><Ccy>EUR</Ccy></Acct>"
        "<Bal><Tp><CdOrPrtry><Cd>OPBD</Cd></CdOrPrtry></Tp><Amt Ccy='EUR'>1000.00</Amt>"
        "<CdtDbtInd>CRDT</CdtDbtInd><Dt><Dt>2026-09-30</Dt></Dt></Bal>"
        "<Bal><Tp><CdOrPrtry><Cd>CLBD</Cd></CdOrPrtry></Tp><Amt Ccy='EUR'>1090.00</Amt>"
        "<CdtDbtInd>CRDT</CdtDbtInd><Dt><Dt>2026-10-31</Dt></Dt></Bal>"
        f"{entries}</Stmt></BkToCstmrStmt></Document>"
    ).encode()


def test_a_camt_statement_gives_its_account_balances_and_booked_transactions():
    s = read_camt(statement())
    assert (s.iban, s.currency, s.opening, s.closing) == (
        "DE38850503001234567890",
        "EUR",
        Decimal("1000.00"),
        Decimal("1090.00"),
    )
    assert s.not_booked == 1  # PDNG : jamais une ligne du relevé
    assert [(ln.amount, ln.reference, ln.counterparty_name, ln.purpose) for ln in s.lines] == [
        (Decimal("100.00"), "R1", "Muster GmbH", "RE-1"),
        (Decimal("-40.00"), "R2", "Papier AG", "R-7"),
        (Decimal("10.00"), "R3/1", "A GmbH", "RE-2"),  # mouvement groupé : une ligne par transaction
        (Decimal("20.00"), "R3/2", "B GmbH", "RE-3"),
    ]
    assert s.lines[1].counterparty_iban == "DE02120300000000202051" and s.lines[0].booking_date == date(
        2026, 10, 1
    )
    assert s.opening + sum(ln.amount for ln in s.lines) == s.closing
    assert read_camt(statement("02")).iban == "DE38850503001234567890"  # autre version, même lecture


def test_a_camt_file_is_read_as_a_bank_export_table():
    fmt, df = read_table(statement(), "Kontoauszug.camt053.xml")
    assert fmt == "xml" and df.height == 4
    assert df.columns[:3] == ["Auftragskonto", "Buchungstag", "Valutadatum"]
    assert df.get_column("Betrag").to_list() == ["100.00", "-40.00", "10.00", "20.00"]
    assert df.get_column("Bankreferenz").to_list() == ["R1", "R2", "R3/1", "R3/2"]


def test_an_unsafe_or_foreign_xml_is_refused_with_a_simple_message():
    bomb = (
        b"<?xml version='1.0'?><!DOCTYPE d [<!ENTITY a 'aaaaaaaaaa'><!ENTITY b '&a;&a;&a;&a;'>]>"
        b"<Document xmlns='urn:iso:std:iso:20022:tech:xsd:camt.053.001.08'>&b;</Document>"
    )
    with pytest.raises(CamtError, match="declarations that are not allowed"):
        read_camt(bomb)
    with pytest.raises(ImportFileError, match=r"not a bank statement \(CAMT.053\)"):
        read_table(b"<?xml version='1.0'?><Invoice><Id>1</Id></Invoice>", "facture.xml")
    with pytest.raises(ImportFileError, match="couldn't read this XML file"):
        read_table(b"<Document", "broken.xml")
