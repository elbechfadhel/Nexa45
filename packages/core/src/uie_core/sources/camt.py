"""Relevé bancaire ISO 20022 CAMT.053 (Bank to Customer Statement, versions 001.02 à 001.08) : lecture SÛRE du
XML d'un utilisateur (defusedxml : ni entités, ni bombe d'expansion), sans aucun système ni banque nommés.

Sortie : le compte du relevé (IBAN, devise), ses soldes d'ouverture et de clôture, et une LIGNE par
transaction COMPTABILISÉE (statut BOOK ; un mouvement groupé de plusieurs transactions : une ligne chacune) :
date de comptabilisation et de valeur, montant SIGNÉ (crédit > 0, débit < 0), contrepartie (nom, IBAN), motif
de paiement, libellé de la banque et référence unique de la banque (AcctSvcrRef). Les mouvements non
comptabilisés (PDNG, INFO) ne sont pas des lignes du relevé : ils sont comptés.

`camt_table` met ces lignes sous la forme d'un export bancaire tabulaire (en-têtes du format « CSV-CAMT » des
banques allemandes) : le reste du parcours (colonnes, valeurs, doublons) est celui de tout fichier."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import polars as pl
from defusedxml import ElementTree as SafeTree
from defusedxml.common import DefusedXmlException

# en-têtes de la table produite (ceux d'un export bancaire « CSV-CAMT ») et colonne propre au CAMT
COLUMNS = (
    "Auftragskonto",
    "Buchungstag",
    "Valutadatum",
    "Buchungstext",
    "Verwendungszweck",
    "Beguenstigter/Zahlungspflichtiger",
    "Kontonummer/IBAN",
    "Betrag",
    "Waehrung",
    "Bankreferenz",
)


class CamtError(ValueError):
    """XML illisible, dangereux ou qui n'est pas un relevé CAMT.053 (message simple, anglais)."""


@dataclass(frozen=True)
class CamtLine:
    booking_date: date
    value_date: date | None
    amount: Decimal  # signé : crédit (entrée d'argent) > 0, débit < 0
    currency: str
    counterparty_name: str
    counterparty_iban: str
    purpose: str
    bank_text: str
    reference: str  # référence unique de la banque (AcctSvcrRef), « /n » pour la n-ième transaction groupée


@dataclass(frozen=True)
class CamtStatement:
    iban: str
    currency: str
    opening: Decimal | None
    closing: Decimal | None
    opening_date: date | None
    closing_date: date | None
    lines: list[CamtLine] = field(default_factory=list)
    not_booked: int = 0  # mouvements non comptabilisés (PDNG, INFO), jamais des lignes


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find(node: Any, path: str) -> Any:
    """Premier descendant par chemin de noms LOCAUX (« Bal/Tp/CdOrPrtry/Cd ») : indépendant de la version."""
    current = [node]
    for name in path.split("/"):
        current = [c for n in current for c in list(n) if _local(c.tag) == name]
        if not current:
            return None
    return current[0]


def _all(node: Any, name: str) -> list[Any]:
    return [c for c in list(node) if _local(c.tag) == name]


def _text(node: Any, path: str) -> str:
    found = _find(node, path)
    return " ".join((found.text or "").split()) if found is not None else ""


def _date(node: Any, path: str) -> date | None:
    """Date d'un élément <Dt> ou <DtTm> (seule la date compte)."""
    for leaf in ("Dt", "DtTm"):
        value = _text(node, f"{path}/{leaf}")
        if value:
            try:
                return date.fromisoformat(value[:10])
            except ValueError:
                return None
    return None


def _amount(text: str) -> Decimal | None:
    try:
        return Decimal(text) if text else None
    except InvalidOperation:
        return None


def _balance(stmt: Any, code: str) -> tuple[Decimal | None, date | None]:
    for bal in _all(stmt, "Bal"):
        if _text(bal, "Tp/CdOrPrtry/Cd") == code:
            value = _amount(_text(bal, "Amt"))
            if value is not None and _text(bal, "CdtDbtInd") == "DBIT":
                value = -value
            return value, _date(bal, "Dt")
    return None, None


def read_camt(data: bytes) -> CamtStatement:
    """Premier relevé (`Stmt`) d'un fichier CAMT.053."""
    try:
        root = SafeTree.fromstring(data, forbid_dtd=True)
    except DefusedXmlException:
        raise CamtError(
            "This XML file contains declarations that are not allowed in a bank statement."
        ) from None
    except SafeTree.ParseError:
        raise CamtError("We couldn't read this XML file. Please check its format.") from None
    namespace = root.tag[1:].split("}", 1)[0] if root.tag.startswith("{") else ""
    if _local(root.tag) != "Document" or "camt.053" not in namespace:
        raise CamtError("This XML file is not a bank statement (CAMT.053).")
    stmt = _find(root, "BkToCstmrStmt/Stmt")
    if stmt is None:
        raise CamtError("This bank statement has no statement (Stmt).")
    opening, opened = _balance(stmt, "OPBD")
    if opening is None:
        opening, opened = _balance(stmt, "PRCD")  # solde final précédent : même valeur, autre code
    closing, closed = _balance(stmt, "CLBD")
    lines: list[CamtLine] = []
    not_booked = 0
    for entry in _all(stmt, "Ntry"):
        status = _text(entry, "Sts/Cd") or _text(entry, "Sts")
        if status and status != "BOOK":
            not_booked += 1
            continue
        credit = _text(entry, "CdtDbtInd") == "CRDT"
        booked = _date(entry, "BookgDt")
        if booked is None:
            raise CamtError("A booked entry of this statement has no booking date.")
        details = [tx for d in _all(entry, "NtryDtls") for tx in _all(d, "TxDtls")] or [entry]
        reference = _text(entry, "AcctSvcrRef")
        amt = _find(entry, "Amt")
        currency = (amt.get("Ccy") if amt is not None else None) or ""
        for n, tx in enumerate(details, start=1):
            # une seule transaction : le montant du mouvement ; groupé : celui de chaque transaction
            amount = _amount(_text(entry if len(details) == 1 else tx, "Amt"))
            if amount is None:
                raise CamtError("An entry of this statement has no amount.")
            # la contrepartie est le débiteur d'un crédit, le créancier d'un débit (v02 : sans « Pty »)
            party = "Dbtr" if credit else "Cdtr"
            name = _text(tx, f"RltdPties/{party}/Pty/Nm") or _text(tx, f"RltdPties/{party}/Nm")
            purpose = " ".join(
                " ".join((u.text or "").split()) for r in _all(tx, "RmtInf") for u in _all(r, "Ustrd")
            )
            lines.append(
                CamtLine(
                    booking_date=booked,
                    value_date=_date(entry, "ValDt"),
                    amount=amount if credit else -amount,
                    currency=currency,
                    counterparty_name=name,
                    counterparty_iban=_text(tx, f"RltdPties/{party}Acct/Id/IBAN"),
                    purpose=purpose or _text(entry, "AddtlNtryInf"),
                    bank_text=_text(entry, "BkTxCd/Prtry/Cd") or _text(entry, "BkTxCd/Domn/Fmly/Cd"),
                    reference=reference if len(details) == 1 else f"{reference}/{n}",
                )
            )
    return CamtStatement(
        iban=_text(stmt, "Acct/Id/IBAN"),
        currency=_text(stmt, "Acct/Ccy"),
        opening=opening,
        closing=closing,
        opening_date=opened,
        closing_date=closed,
        lines=lines,
        not_booked=not_booked,
    )


def camt_table(data: bytes) -> pl.DataFrame:
    """Lignes du relevé sous la forme d'un export bancaire tabulaire (une ligne par transaction, texte)."""
    s = read_camt(data)
    rows = [
        (
            s.iban,
            ln.booking_date.isoformat(),
            ln.value_date.isoformat() if ln.value_date else "",
            ln.bank_text,
            ln.purpose,
            ln.counterparty_name,
            ln.counterparty_iban,
            f"{ln.amount:f}",
            ln.currency or s.currency,
            ln.reference,
        )
        for ln in s.lines
    ]
    return pl.DataFrame(
        {name: [r[i] or None for r in rows] for i, name in enumerate(COLUMNS)},
        schema={name: pl.Utf8 for name in COLUMNS},
    )


__all__ = ["COLUMNS", "CamtError", "CamtLine", "CamtStatement", "camt_table", "read_camt"]
