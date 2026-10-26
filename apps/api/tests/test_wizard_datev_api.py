"""Mode Simple vers DATEV par la Supplier API : fichier du client → CDM → binding DATEV → fichier EXTF.

Fichiers INVENTÉS qui reproduisent chaque piège de la vérité terrain du mandant de démo (liste de clients,
journal de factures de sortie). Cibles : specs DATEV générées localement (sources non versionnées) : le module
est sauté si elles sont absentes. Aucun LLM (toutes les colonnes sont reconnues par le tableau de source),
rien n'est envoyé à DATEV. Chaque cellule du fichier produit est contrôlée contre la spec LOCALE (motifs),
indépendamment du Registry."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import SecretStr
from test_lexware_partner_api import _client
from test_supplier_api import PW
from uie_api import deps
from uie_api.app import create_app
from uie_connectors.datev import DatevSchemaLoader
from uie_core.identity import Organization, OrganizationMembership, SystemContext, User, hash_password
from uie_core.registry import RegistryService
from uie_core.wizard import clear_cache

SPECS = Path(__file__).resolve().parents[3] / "var" / "datev" / "specs" / "extf_700"
DEBITOREN, BUCHUNGEN = SPECS / "debitoren-kreditoren_v5.yaml", SPECS / "buchungsstapel_v13.yaml"
pytestmark = pytest.mark.skipif(
    not (DEBITOREN.exists() and BUCHUNGEN.exists()), reason="specs DATEV locales absentes (spec_builder)"
)

CUSTOMERS = (
    "Kundenstamm Testfirma GmbH – Export 20.10.2026;;;;;;;;;;;\n"
    ";;;;;;;;;;;\n"
    "Kd-Nr.;Firma;Ansprechpartner;Straße;PLZ;Ort;Land;USt-IdNr.;E-Mail;Telefon;IBAN;Zahlungsziel\n"
    "1001;Muster Bürotechnik GmbH;Herr Muster;Hauptstraße 12;01067;Dresden;Deutschland;DE123456789;"
    "info@muster-buero.de;0351 123456;DE89 3704 0044 0532 0130 00;14 Tage\n"
    "1002;Beispiel Architekten PartG;Frau Beispiel;Königstraße 5;70173;Stuttgart;DE;DE234567891;"
    "kontakt@beispiel.de;+49 711 987654;;30\n"
    "1003;Probe IT-Service;Herr Probe;Lindenallee 8;10115;Berlin;D;;kontakt@probe-it;030 1234567;;14 Tage\n"
    "1004;Alpen Handels GmbH;Frau Huber;Mariahilfer Straße 99;1060;Wien;Österreich;ATU12345678;"
    "office@alpen.at;+43 1 5556677;;30 Tage\n"
    "1005;Lefèvre Distribution SARL;M. Lefèvre;12 rue de la Paix;75002;Paris;Frankreich;FR123;"
    "compta@lefevre.fr;+33 1 23456789;;45 Tage\n"
    "1006;Fischer Gastronomie GmbH;Frau Fischer;Elbufer 1;1307;Dresden;Deutschland;DE789123456;"
    "fischer@gastro.de;0351 334455;;sofort\n"
    ";Neumann Consulting;Frau Neumann;Schillerstraße 30;60313;Frankfurt am Main;Deutschland;DE912345678;"
    "neumann@consulting.de;069 112233;;14 Tage\n"
    "1008;Mueller Bürotechnik GmbH;Hr. Muster;Hauptstr. 12;1067;Dresden;Deutschland;DE123456789;"
    "info@muster-buero.de;0351-123456;;14 Tage\n"
    "1009;Schulz Sanitär GmbH;Herr Schulz;Rohrweg 6;30159;Hannover;Deutschland;DE 135 792 468;"
    "schulz@sanitaer.de;0511 246810;;30 Tage\n"
)

INVOICES = (
    "Rechnungsausgangsbuch Q3 2026;;;;;;;;\n"
    ";;;;;;;;\n"
    "Re-Nr.;Re-Datum;Kd-Nr.;Kunde;Netto €;USt %;USt €;Brutto €;Bemerkung\n"
    "RE-1;05.07.2026;1001;Muster Bürotechnik GmbH;1299,00;19;246,81;1545,81;Preis inkl. Lieferung\n"
    "RE-2;27.07.2026;1002;Beispiel Architekten PartG;100,00;7;7,00;107,00;\n"
    "RE-3;15.09.2026;1004;Alpen Handels GmbH;3200,00;0;0,00;3200,00;innergemeinschaftliche Lieferung\n"
    "RE-4;5.9.26;1002;Beispiel Architekten PartG;450,00;19;85,50;535,50;\n"
    "RE-5;12.09.2026;1006;Fischer Gastronomie GmbH;2500,00;16;400,00;2900,00;\n"
    "RE-6;20.09.2026;1009;Schulz Sanitär GmbH;1000,00;19;190,00;1180,00;\n"
    "RE-7;14.07.2026;1002;Beispiel Architekten PartG;1738,68;19;330,35;2069,03;\n"
    "RE-7;2026-07-14;1002;Beispiel Architekten PartG;1738,68;19;330,35;2069,03;nochmal erfasst\n"
    "RE-8;24.09.2026;1001;Muster Bürotechnik GmbH;-250,00;19;-47,50;-297,50;Gutschrift\n"
    ";25.09.2026;1003;Probe IT-Service;640,00;19;121,60;761,60;\n"
    "RE-9;26.09.2026;1099;Meier Druckerei;410,00;19;77,90;487,90;\n"
    "RE-10;15.12.2025;1003;Probe IT-Service;720,00;19;136,80;856,80;\n"
    "RE-11;18.09.2026;1005;Lefèvre Distribution SARL;1850,00;0;0,00;1850,00;EU-Lieferung\n"
    "Summe;;;;;;;;\n"
)
ANSWERS = {"setting:advisor_number": "1234567", "setting:client_number": "10001"}


@pytest.fixture
def world(session):  # noqa: ANN001, ANN201
    clear_cache()
    registry = RegistryService(session, SystemContext("test"))
    for spec in (DEBITOREN, BUCHUNGEN):
        registry.publish_snapshot(DatevSchemaLoader({"spec": spec}).build_snapshot())
    org = Organization(name="Acme", slug="acme")
    session.add(org)
    session.flush()
    user = User(email="alice@acme.io", display_name="Alice", password_hash=hash_password(SecretStr(PW)))
    session.add(user)
    session.flush()
    session.add(OrganizationMembership(organization_id=org.id, user_id=user.id, role="OWNER"))
    session.flush()
    app = create_app()
    app.dependency_overrides[deps.get_session] = lambda: session
    app.dependency_overrides[deps.get_wizard_proposer] = lambda: None  # aucun LLM
    yield {"app": app, "client": _client(app, "alice@acme.io"), "session": session}
    clear_cache()


def _target(c: Any, fmt: str) -> str:
    return next(t["value"] for t in c.get("/api/wizard/targets").json() if t.get("format") == fmt)


def _flow(c: Any, fmt: str, content: str, name: str, extra: dict[str, str] | None = None) -> dict[str, Any]:
    files = {"file": (name, content.encode("cp1252"), "text/csv")}
    start = c.post(
        "/api/wizard/quick-start", files=files, data={"payload": json.dumps({"target": _target(c, fmt)})}
    )
    assert start.status_code == 200, start.text
    body = start.json()
    choices = dict(body["choices"]) | {
        k: v for k, v in ANSWERS.items() if k in {q["decision"] for q in body["questions"] + body["applied"]}
    }
    choices |= extra or {}
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


def _download(c: Any, flow: dict[str, Any]) -> Any:
    return c.post(
        "/api/wizard/file/prepared", files=flow["files"], data={"payload": json.dumps(flow["payload"])}
    )


def _rows(content: bytes) -> tuple[list[str], list[list[str]]]:
    lines = content.decode("cp1252").split("\r\n")
    return lines[0].split(";"), [line.split(";") for line in lines[2:] if line]


def _check_against_spec(spec_path: Path, content: bytes) -> list[str]:
    """Chaque cellule non vide (en-tête et données) contre le motif de sa position dans la spec LOCALE."""
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    header, rows = _rows(content)
    bad = []
    for kind, cells in [("header", header), *(("fields", r) for r in rows)]:
        by_pos = {f["position"]: f for f in spec[kind]}
        for pos, cell in enumerate(cells, start=1):
            pattern = (by_pos.get(pos) or {}).get("pattern")
            if cell not in ("", '""') and pattern and not re.match(pattern, cell):
                bad.append(f"{kind} {pos} {by_pos[pos]['label']}: {cell!r}")
    return bad


def _by(rows: list[list[str]], position: int, value: str) -> list[str]:
    return next(r for r in rows if r[position - 1].strip('"') == value)


def test_customers_list_to_datev_debitoren_with_every_trap(world):
    c = world["client"]
    flow = _flow(c, "Debitoren/Kreditoren 5", CUSTOMERS, "Kundenliste.csv")
    body, pre = flow["start"], flow["preview"]
    columns = {x["header"]: x["target"] for x in body["mapping"]["columns"]}
    # en-têtes allemands reliés aux champs (Ansprechpartner compris), sans LLM ; Zahlungsziel : non relié
    assert columns == {
        "Kd-Nr.": "customer_number",
        "Firma": "name",
        "Ansprechpartner": "contact_person",
        "Straße": "street",
        "PLZ": "postal_code",
        "Ort": "city",
        "Land": "country",
        "USt-IdNr.": "vat_id",
        "E-Mail": "email",
        "Telefon": "phone",
        "IBAN": "iban",
        "Zahlungsziel": None,
    }
    asked = [q["decision"] for q in body["questions"]]
    # questions : les numéros du cabinet (jamais inventés) ; forme juridique introuvable : « Companies »
    # présélectionné, donc dans « Decided for you » (modifiable d'un clic)
    assert asked == ["setting:advisor_number", "setting:client_number"]
    kind = next(q for q in body["applied"] if q["decision"] == "partner:kind")
    assert kind["default"] == "ORGANIZATION"
    assert kind["question"]["title"] == "2 names do not show a legal form"
    assert "Probe IT-Service" in kind["question"]["help"] and "Neumann Consulting" in kind["question"]["help"]
    applied = {q["decision"]: q["default"] for q in body["applied"]}
    # règle « 10000 + Kd-Nr. » présélectionnée, visible et modifiable ; exercice de l'année des données
    assert applied["setting:account_offset"] == "10000"
    assert applied["setting:debtor_range"] == "10000-69999" and applied["setting:account_length"] == "4"
    assert applied["setting:fiscal_year_start"].startswith("20")
    assert applied["partner:postal_zero"] == "add" and applied["partner:duplicates"] == "first"
    assert applied["partner:no_number"] == "assign"
    # pièges : doublon probable (même USt-IdNr), USt-IdNr / e-mail invalides signalés et non écrits
    assert [(e["row"], e["kind"]) for e in pre["excluded_rows"]] == [(8, "probable_duplicate")]
    assert pre["excluded_rows"][0]["reason"] == "Probable duplicate (same VAT ID as row 1): not exported."
    messages = {i["message"]: i["rows"] for i in pre["value_issues"]}
    assert messages["1 VAT ID is not valid for its country: not written."] == [5]
    assert messages["1 email address is not valid: not written."] == [3]
    transforms = {t["label"]: t["examples"] for t in pre["value_transforms"]}
    assert transforms["VAT ID written without spaces"] == [["DE 135 792 468", "DE135792468"]]
    assert transforms["Leading zero added to the postal code (your choice)"] == [["1307", "01307"]]
    assert pre["rows"] == 8 and pre["delivery"] == "format"

    res = _download(c, flow)
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("text/csv")
    assert _check_against_spec(DEBITOREN, res.content) == []
    header, rows = _rows(res.content)
    assert header[:5] == ['"EXTF"', "700", "16", '"Debitoren/Kreditoren"', "5"]
    assert header[10:14] == ["1234567", "10001", "20260101", "4"]
    muster = _by(rows, 1, "11001")  # 10000 + 1001
    assert muster[1] == '"Muster Bürotechnik GmbH"' and muster[6] == '"2"'  # GmbH → entreprise
    assert (muster[8], muster[9]) == ('"DE"', '"123456789"')  # USt-IdNr = pays + numéro
    assert muster[101] == '"Herr Muster"'  # Ansprechpartner
    assert _by(rows, 1, "11004")[17:20] == ['"1060"', '"Wien"', '"AT"']  # PLZ autrichienne gardée
    assert _by(rows, 1, "11006")[17] == '"01307"'  # zéro initial ajouté (votre choix)
    assert _by(rows, 1, "11005")[8:10] == ['""', '""']  # FR123 : non écrit
    assert _by(rows, 1, "11003")[32] == '""'  # e-mail invalide : non écrit
    neumann = next(r for r in rows if r[1] == '"Neumann Consulting"' or r[5] == '"Neumann Consulting"')
    assert neumann[0] == "10000"  # sans Kd-Nr. : premier numéro libre (votre choix), jamais en silence
    assert len(rows) == 8


def test_invoices_to_datev_buchungsstapel_with_every_trap_and_no_double_export(world):
    c = world["client"]
    customers = _flow(
        c, "Debitoren/Kreditoren 5", CUSTOMERS, "Kundenliste.csv", {"partner:kind": "ORGANIZATION"}
    )
    assert _download(c, customers).status_code == 200  # comptes des clients d'abord (11001…)

    flow = _flow(c, "Buchungsstapel 13", INVOICES, "Rechnungen.csv")
    body = flow["start"]
    assert body["data_type"] == "Invoices"
    asked = [q["decision"] for q in body["questions"]]
    # réglages du cabinet repris (« from your settings ») ; Festschreibung et 0 % : décisions explicites
    # 0 % : justifié ou refusé d'après l'état des USt-IdNr retenu à l'export des clients, plus de question
    assert set(asked) == {"setting:lock_bookings"}
    applied = {q["decision"]: (q["default"], q["default_from"]) for q in body["applied"]}
    assert applied["setting:advisor_number"] == ("1234567", "your_settings")
    assert applied["map:CATEGORY:sale:19"] == ("8400", "usual_default")
    assert applied["map:CATEGORY:sale:7"] == ("8300", "usual_default")
    assert applied["map:TAX:rate:SALE:19"] == ("none", "usual_default")
    assert applied["century:1"] == ("20", "usual_default")  # « 5.9.26 » : siècle des autres dates

    flow = _flow(
        c,
        "Buchungsstapel 13",
        INVOICES,
        "Rechnungen.csv",
        {"setting:lock_bookings": "0", "accounting:zero_rate": "export"},
    )
    pre = flow["preview"]
    reasons = {e["row"]: e["reason"] for e in pre["excluded_rows"]}
    assert "16 % is not a valid VAT rate" in reasons[5]
    assert "not the gross amount 1180: never corrected" in reasons[6]
    assert "entered twice" in reasons[8]
    assert "No invoice number" in reasons[10]
    assert "Meier Druckerei has no DATEV account yet" in reasons[11]
    assert "outside the fiscal year" in reasons[12]
    # FR123 dans la liste des clients : livraison intracommunautaire impossible à justifier, signalée
    assert "VAT ID of Lefèvre Distribution SARL is not valid in your customer list (FR)" in reasons[13]
    # 0 % vers l'Autriche (ATU valide) sans colonne Leistungsart : marchandise ou service jamais deviné
    assert "goods or services is not known (Leistungsart), never guessed" in reasons[3]
    assert len(reasons) == 8 and pre["rows"] == 5

    res = _download(c, flow)
    assert res.status_code == 200, res.text
    assert _check_against_spec(BUCHUNGEN, res.content) == []
    header, rows = _rows(res.content)
    assert header[14:17] == ["20260701", "20260930", '"Buchungen 07/2026 bis 09/2026"']
    witness = _by(rows, 11, "RE-1")  # ligne témoin
    assert witness[:3] == ["1545,81", '"S"', '"EUR"']
    assert (witness[6], witness[7], witness[9]) == ("11001", "8400", "0507")
    assert _by(rows, 11, "RE-2")[7] == "8300"  # 7 %
    assert not [r for r in rows if r[10] == '"RE-3"']  # nature inconnue : jamais sur un compte deviné
    assert _by(rows, 11, "RE-4")[9] == "0509"  # 5.9.26 → 05.09.2026
    credit = _by(rows, 11, "RE-8")
    assert credit[:2] == ["297,50", '"H"']  # avoir : montant positif, sens inversé
    assert [r[10].strip('"') for r in rows].count("RE-7") == 1  # doublon : écrit une fois

    again = _download(c, flow)  # deuxième export des mêmes factures
    assert again.status_code == 422 and "No new document" in again.json()["detail"]
    second = _flow(
        c,
        "Buchungsstapel 13",
        INVOICES,
        "Rechnungen.csv",
        {"setting:lock_bookings": "0", "accounting:zero_rate": "export"},
    )["preview"]
    assert second["rows"] == 0
    assert sum(1 for e in second["excluded_rows"] if e["kind"] == "already_exported") == 5

    # remise à zéro de la démo (CLI) : comptes réservés et pièces journalisées retirés, réglages gardés
    from uie_core.exports.demo import reset_file_exports
    from uie_core.identity import Organization

    session = world["session"]
    org = session.query(Organization).filter_by(slug="acme").one()
    done = reset_file_exports(session, SystemContext("test"), org.id)
    assert (done.accounts, done.documents, done.runs) == (8, 5, 2)  # le 2e export refusé n'a rien écrit
    assert _download(c, customers).status_code == 200  # la démo se rejoue entièrement
    replay = _download(c, flow)
    assert replay.status_code == 200 and _rows(replay.content)[1] == rows


def test_an_unknown_data_type_is_chosen_by_the_user_and_the_targets_say_what_they_receive(world):
    c = world["client"]
    targets = {t["format"]: t for t in c.get("/api/wizard/targets").json() if t.get("system") == "datev"}
    assert targets["Buchungsstapel 13"]["latest"]
    assert targets["Buchungsstapel 13"]["receives"] == ["Invoices", "Purchase invoices"]
    assert targets["Buchungsstapel 13"]["product_name"] == "Buchungsstapel"
    assert targets["Debitoren/Kreditoren 5"]["receives"] == [
        "Customers & suppliers",
        "Customers",
        "Suppliers",
    ]

    unknown = "Spalte A;Spalte B\nx;y\n".encode("cp1252")
    files = {"file": ("liste.csv", unknown, "text/csv")}
    target = _target(c, "Debitoren/Kreditoren 5")
    res = c.post("/api/wizard/quick-start", files=files, data={"payload": json.dumps({"target": target})})
    assert res.status_code == 422
    detail = res.json()["detail"]
    assert detail["code"] == "data_type_unknown"  # jamais un arrêt sans issue : le choix est proposé
    assert detail["data_types"] == ["Customers & suppliers", "Customers", "Suppliers"]
    chosen = c.post(
        "/api/wizard/quick-start",
        files=files,
        data={"payload": json.dumps({"target": target, "data_type": "Customers"})},
    )
    assert chosen.status_code == 200, chosen.text
    assert (chosen.json()["data_type"], chosen.json()["data_type_from"]) == ("Customers", "user")
    other = c.post(
        "/api/wizard/quick-start",
        files=files,
        data={"payload": json.dumps({"target": target, "data_type": "Products"})},
    )
    assert other.status_code == 422 and other.json()["detail"]["code"] == "data_type_unknown"


def test_a_journal_that_names_neither_customers_nor_suppliers_lets_the_user_choose(world):
    """Buchungsstapel reçoit des ventes ET des achats : un journal aux en-têtes inhabituels ne dit pas lequel,
    l'utilisateur choisit (jamais deviné, jamais un arrêt sans issue)."""
    c = world["client"]
    odd = "Nummer;Tag;Wer;Summe\nA-1;01.09.2026;1001;119,00\n".encode("cp1252")  # en-têtes inhabituels
    files = {"file": ("journal.csv", odd, "text/csv")}
    target = _target(c, "Buchungsstapel 13")
    res = c.post("/api/wizard/quick-start", files=files, data={"payload": json.dumps({"target": target})})
    assert res.status_code == 422
    assert res.json()["detail"]["data_types"] == ["Invoices", "Purchase invoices"]
    chosen = c.post(
        "/api/wizard/quick-start",
        files=files,
        data={"payload": json.dumps({"target": target, "data_type": "Invoices"})},
    )
    assert chosen.status_code == 200, chosen.text
    assert (chosen.json()["data_type"], chosen.json()["data_type_from"]) == ("Invoices", "user")


def test_invoices_before_the_customer_list_still_finish_the_analysis(world):
    """Aucun client exporté : toutes les factures sont bloquées. L'analyse aboutit (jamais « did not
    finish »),
    chaque ligne avec son motif ; seul le fichier est refusé."""
    c = world["client"]
    flow = _flow(c, "Buchungsstapel 13", INVOICES, "Rechnungen.csv", {"setting:lock_bookings": "0"})
    pre = flow["preview"]
    assert pre["rows"] == 0 and pre["status"] == "to_complete"
    assert any("every invoice is blocked" in n for n in pre["notices"])
    reasons = {e["row"]: e["reason"] for e in pre["excluded_rows"]}
    assert "Muster Bürotechnik GmbH has no DATEV account yet" in reasons[1]
    res = _download(c, flow)
    assert res.status_code == 422  # rien à écrire : jamais de fichier vide


def test_a_file_of_another_kind_is_never_forced_into_the_only_kind_a_target_takes(world):
    """Liste de clients envoyée au journal de factures : jamais 15 « factures sans numéro », le désaccord
    est dit ;
    à l'inverse, des factures vers Debitoren/Kreditoren aussi. Un choix explicite reste possible."""
    c = world["client"]
    files = {"file": ("Kundenliste.csv", CUSTOMERS.encode("cp1252"), "text/csv")}
    target = _target(c, "Buchungsstapel 13")
    res = c.post("/api/wizard/quick-start", files=files, data={"payload": json.dumps({"target": target})})
    assert res.status_code == 422
    detail = res.json()["detail"]
    assert detail["code"] == "data_type_unknown"
    assert detail["data_types"] == ["Invoices", "Purchase invoices"]
    assert "looks like a list of customers" in detail["message"]
    assert "Choose another target" in detail["message"]
    forced = c.post(
        "/api/wizard/quick-start",
        files=files,
        data={"payload": json.dumps({"target": target, "data_type": "Invoices"})},
    )
    assert forced.status_code == 200 and forced.json()["data_type_from"] == "user"

    files = {"file": ("Rechnungen.csv", INVOICES.encode("cp1252"), "text/csv")}
    target = _target(c, "Debitoren/Kreditoren 5")
    res = c.post("/api/wizard/quick-start", files=files, data={"payload": json.dumps({"target": target})})
    assert res.status_code == 422 and "looks like a list of invoices" in res.json()["detail"]["message"]


def test_the_format_is_chosen_from_the_file_as_soon_as_it_is_dropped(world):
    """Prepare data for DATEV : dès le dépôt, la liste de clients choisit Customers and suppliers et les
    factures choisissent Bookings, par les règles (aucun LLM) ; un fichier dont le type ne mène à aucun des
    deux formats ne présélectionne rien."""
    c = world["client"]
    bookings, partners = _target(c, "Buchungsstapel 13"), _target(c, "Debitoren/Kreditoren 5")
    payload = {"payload": json.dumps({"targets": [bookings, partners]})}

    def detect(name: str, content: str) -> dict[str, Any]:
        files = {"file": (name, content.encode("cp1252"), "text/csv")}
        res = c.post("/api/wizard/detect-target", files=files, data=payload)
        assert res.status_code == 200, res.text
        return res.json()

    assert detect("Kundenliste.csv", CUSTOMERS)["target"] == partners
    assert detect("Rechnungen.csv", INVOICES) == {"target": bookings, "data_type": "Invoices"}
    articles = "Art.-Nr.;Artikelbezeichnung;EAN;VK netto;Warengruppe\nA-1;Tisch;4006381333931;99,00;Möbel\n"
    assert detect("Artikel.csv", articles) == {"target": None, "data_type": "Products"}
    assert detect("Notizen.csv", "Spalte A;Spalte B\nx;y\n") == {"target": None, "data_type": None}
