"""« Prepare data for DATEV » : relevé bancaire (domaine 4), par la Supplier API. Fichiers INVENTÉS qui
reproduisent les pièges de la vérité terrain ; cibles : specs DATEV générées localement (module sauté si
absentes). Rien n'est envoyé à DATEV."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from test_wizard_datev_api import (  # noqa: F401 - fixture et aides partagées
    CUSTOMERS,
    INVOICES,
    _download,
    _flow,
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
