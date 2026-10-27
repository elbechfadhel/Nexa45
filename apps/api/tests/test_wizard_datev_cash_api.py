"""« Prepare data for DATEV » : livre de caisse (domaine 3) et règles communes du parcours (Supplier API).

Fichiers INVENTÉS (pièges de la vérité terrain du domaine 3). Cibles : specs DATEV générées localement : le
module est sauté si elles sont absentes. Rien n'est envoyé à DATEV."""

from __future__ import annotations

from typing import Any

from test_wizard_datev_api import (  # noqa: F401 - fixture et aides partagées
    BUCHUNGEN,
    CUSTOMERS,
    _by,
    _check_against_spec,
    _download,
    _flow,
    _rows,
    pytestmark,
    world,
)
from uie_api import deps
from uie_core.domains import ProposalMeta
from uie_core.imports import ColumnProposal, ColumnProposalBatch
from uie_core.imports.proposer import ColumnDecision

# journal de factures dont la colonne du montant brut n'a pas un nom connu : seule l'IA la reconnaît
UNKNOWN_AMOUNT = (
    "Re-Nr.;Re-Datum;Kd-Nr.;Kunde;Netto €;USt %;Endsumme Kunde\n"
    "RE-1;05.07.2026;1001;Muster Bürotechnik GmbH;100,00;19;119,00\n"
)


# livre de caisse INVENTÉ qui reproduit chaque piège de la vérité terrain du domaine 3
CASH_BOOK = (
    "Kassenbuch September 2026 – Testladen GmbH – Ladenkasse;;;;;;;;\n"
    "Datum;Beleg-Nr.;Buchungstext;Kategorie;Einnahme €;Ausgabe €;USt %;Kassenbestand €;Bemerkung\n"
    "01.09.2026;;Anfangsbestand;;;;;850,00;Übertrag August\n"
    "01.09.2026;K-001;Tageslosung Laden;Barverkauf;329,50;;19;1179,50;\n"
    "02.09.2026;K-002;Tageslosung Laden;Barverkauf;358,03;;19;1537,53;\n"
    "02.09.2026;K-003;Tageslosung Laden Bücher;Barverkauf;77,71;;7;1615,24;\n"
    "03.09.2026;K-004;Tageslosung Laden;Barverkauf;406,19;;19;2021,43;\n"
    "03.09.2026;K-005;Briefmarken;Porto;;17,00;0;2004,43;\n"
    "04.09.2026;K-006;Tageslosung Laden;Barverkauf;357,58;;19;2362,01;\n"
    "04.09.2026;K-007;Druckerpapier Baumarkt;Bürobedarf;;23,80;19;2338,21;\n"
    "07.09.2026;K-008;Tageslosung Laden;Barverkauf;401,36;;19;2739,57;\n"
    "07.09.2026;K-009;Tageslosung Laden Bücher;Barverkauf;31,74;;7;2771,31;\n"
    "08.09.2026;K-010;Tageslosung Laden;Barverkauf;291,75;;19;3063,06;\n"
    "08.09.2026;K-011;Einzahlung auf Bankkonto;Bankeinzahlung;;1200,00;;1863,06;\n"
    "09.09.2026;K-012;Tageslosung Laden;Barverkauf;406,41;;19;2269,47;\n"
    "10.09.2026;K-013;Tageslosung Laden;Barverkauf;335,75;;19;2605,22;\n"
    "10.09.2026;K-014;Tageslosung EC-Karte;Barverkauf;640,00;;19;3245,22;Kartenzahlung\n"
    "11.09.2026;K-015;Tageslosung Laden;Barverkauf;396,22;;19;3641,44;\n"
    "11.09.2026;K-016;Geschäftsessen Kunde Müller;Bewirtung;;95,20;19;3546,24;\n"
    "14.09.2026;K-017;Barentnahme Inhaber;Privatentnahme;;300,00;;3246,24;\n"
    "14.09.2026;K-018;Tageslosung Laden;Barverkauf;207,17;;19;3453,41;\n"
    "15.09.2026;K-019;Tageslosung Laden;Barverkauf;292,58;;19;3745,99;\n"
    "15.09.2026;K-020;Einzahlung auf Bankkonto;Bankeinzahlung;;4246,00;;-500,01;\n"
    "16.09.2026;K-021;Abhebung vom Bankkonto für Wechselgeld;Bankabhebung;250,00;;;-250,01;\n"
    "16.09.2026;K-022;Tageslosung Laden;Barverkauf;239,18;;19;-10,83;\n"
    "17.09.2026;K-023;Tageslosung Laden;Barverkauf;310,50;;19;299,67;\n"
    "17.09.2026;;Kaffee und Milch Büro;Bürobedarf;;12,40;19;287,27;\n"
    "18.09.2026;K-024;Tageslosung Laden;Barverkauf;317,75;;19;605,02;\n"
    "18.09.2026;K-025;Tageslosung Laden;Barverkauf;210,00;35,00;19;780,02;\n"
    "21.09.2026;K-026;Tageslosung Laden;Barverkauf;183,15;;19;963,17;\n"
    "21.09.2026;K-027;Toner Drucker;Bürobedarf;;59,50;19;903,67;\n"
    "22.09.2026;K-028;Tageslosung Laden;Barverkauf;357,00;;19;1260,67;\n"
    "22.09.2026;K-028;Tageslosung Laden;Barverkauf;357,00;;19;1617,67;\n"
    "19.09.2026;K-029;Tageslosung Laden;Barverkauf;288,00;;19;1905,67;\n"
    "23.09.2026;K-030;Tageslosung Laden;Barverkauf;305,00;;16;2210,67;\n"
    "24.09.2026;K-031;Tageslosung Laden;Barverkauf;199,00;;19;2419,67;\n"
    ";;;;;;;;\n"
    "Summe;;;;7548,57;5988,90;;2419,67;\n"
)


class AmountLLM:
    """LLM simulé : reconnaît « Endsumme Kunde » comme montant brut, avec une confiance élevée."""

    provider = "fake"
    model = "fake"
    cache_identity = "fake-cash-amount"

    def wizard_columns(self, request: Any) -> ColumnProposalBatch:
        return ColumnProposalBatch(
            proposals=[
                ColumnProposal(
                    position=c.position,
                    decision=ColumnDecision.TARGET,
                    semantic_key="gross_amount",
                    confidence=0.97,
                )
                for c in request.columns
                if c.header == "Endsumme Kunde"
            ],
            meta=ProposalMeta(provider="fake", model="fake"),
        )


def _same_folder(c: Any) -> None:
    """Même dossier que les domaines précédents : la liste des clients a déjà été exportée (réglages du
    dossier DATEV : plages de comptes, Berater, Mandant)."""
    customers = _flow(
        c, "Debitoren/Kreditoren 5", CUSTOMERS, "Kundenliste.csv", {"partner:kind": "ORGANIZATION"}
    )
    assert _download(c, customers).status_code == 200


def test_an_amount_recognized_only_by_the_ai_is_to_confirm_never_chosen(world):  # noqa: F811
    """D9 : dans « Prepare data for DATEV », une colonne de MONTANT reconnue seulement par l'IA est « à
    confirmer » (jamais retenue d'office) ; les colonnes reconnues par leur nom restent sûres."""
    world["app"].dependency_overrides[deps.get_wizard_proposer] = lambda: AmountLLM()
    flow = _flow(world["client"], "Buchungsstapel 13", UNKNOWN_AMOUNT, "Rechnungen.csv")
    columns = {c["header"]: (c["status"], c["target"]) for c in flow["start"]["mapping"]["columns"]}
    assert columns["Endsumme Kunde"] == ("to_confirm", "gross_amount")
    assert columns["Netto €"] == ("matched", "net_amount")
    decided = {d["target"] for d in flow["start"]["decisions"]}
    assert "gross_amount" not in decided and "net_amount" in decided


def test_a_cash_book_is_booked_on_the_cash_account_without_customer_or_supplier(world):  # noqa: F811
    """Livre de caisse → Buchungsstapel : type et format reconnus sans IA ; Konto = compte de la caisse du
    dossier (réglage présélectionné à 1000 depuis la table, à valider) ; recette S, dépense H ; contre-compte
    de la catégorie (table de la caisse) ; aucun Debitor ni Kreditor ; Buchungstext = texte + pièce (D7)."""
    c = world["client"]
    _same_folder(c)
    flow = _flow(c, "Buchungsstapel 13", CASH_BOOK, "Kassenbuch.csv", {"setting:lock_bookings": "0"})
    start, pre = flow["start"], flow["preview"]
    assert (start["data_type"], start["data_type_from"]) == ("Cash book", "rules")
    columns = {x["header"]: (x["status"], x["target"]) for x in start["mapping"]["columns"]}
    assert columns["Einnahme €"] == ("matched", "cash_in") and columns["Ausgabe €"] == ("matched", "cash_out")
    assert columns["Kategorie"] == ("matched", "cash_category")
    applied = {q["decision"]: (q["default"], q["default_from"]) for q in start["applied"]}
    assert applied["setting:cash_account"] == ("1000", "usual_default")
    cash = next(q for q in start["applied"] if q["decision"] == "setting:cash_account")
    assert cash["summary"] == "Cash account {value}" and "to be validated" in cash["question"]["help"]
    assert applied["map:CATEGORY:cash:barverkauf:19"] == ("8400", "usual_default")
    assert applied["map:CATEGORY:cash:barverkauf:7"] == ("8300", "usual_default")
    assert applied["map:CATEGORY:cash:bürobedarf:19"] == ("4930", "usual_default")
    assert applied["map:CATEGORY:cash:porto:0"] == ("4910", "usual_default")
    assert applied["map:CATEGORY:cash:bankeinzahlung"] == ("1360", "usual_default")
    assert applied["map:TAX:rate:PURCHASE:19"] == ("0009", "usual_default")
    # solde négatif (K-020 : décision jamais présélectionnée), Bewirtung (absente de la table), Privatentnahme
    # (forme juridique) : jamais devinés ; la Festschreibung n'est pas encore dans les réglages du dossier ici
    assert [q["decision"] for q in start["questions"]] == [
        "setting:lock_bookings",
        "cash:negative",
        "map:CATEGORY:cash:bewirtung:19",
    ]
    assert start["more_questions"] == 1  # Privatentnahme
    assert "map:CATEGORY:cash:privatentnahme" not in applied
    serious = [i for i in pre["value_issues"] if i["severity"] == "serious"]
    assert len(serious) == 1 and serious[0]["message"].startswith("The cash balance becomes negative with K-020")
    reasons = {e["row"]: (e["kind"], e["reason"]) for e in pre["excluded_rows"]}
    assert reasons[1][0] == "opening"
    assert "has no DATEV mapping yet" in reasons[17][1] and "has no DATEV mapping yet" in reasons[18][1]

    res = _download(c, flow)
    assert res.status_code == 200, res.text
    assert _check_against_spec(BUCHUNGEN, res.content) == []
    _, rows = _rows(res.content)
    witness = _by(rows, 11, "K-027")  # ligne témoin dépense
    assert [witness[i] for i in (0, 1, 6, 7, 8, 9, 13)] == [
        "59,50",
        '"H"',
        "1000",
        "4930",
        '"0009"',
        "2109",
        '"Toner Drucker, K-027"',
    ]
    k028 = [r for r in rows if r[10] == '"K-028"']
    assert len(k028) == 1 and [k028[0][i] for i in (0, 1, 6, 7, 8, 9)] == [
        "357,00",
        '"S"',
        "1000",
        "8400",
        '""',
        "2209",
    ]
    k011 = _by(rows, 11, "K-011")  # dépôt à la banque : Geldtransit, sans TVA
    assert [k011[i] for i in (0, 1, 7, 8)] == ["1200,00", '"H"', "1360", '""']
    assert {r[6] for r in rows} == {"1000"}  # aucun Debitor ni Kreditor
