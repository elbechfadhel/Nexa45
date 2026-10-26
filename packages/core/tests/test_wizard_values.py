"""AI Migration Wizard : moteur générique de valeurs (sans base ni LLM).

Cas génériques (nombres, dates, oui / non, valeurs admises, listes, unités, devises) et un fichier INVENTÉ de
la forme d'un export de place de marché. Aucun système nommé."""

from __future__ import annotations

import io
from typing import Any

import polars as pl
import pytest
from uie_core.wizard import MappingDecision, TargetField, TargetProposal, ValueChoiceError
from uie_core.wizard.mapping import prepare, render_prepared
from uie_core.wizard.model import AllowedValue
from uie_core.wizard.values import (
    ValueRuleSuggestion,
    check_column,
    detect_currency,
    is_money,
    suggest_rules,
    unwrap,
)


def _f(name: str, type_: str, label: str | None = None, **kw: object) -> TargetField:
    return TargetField(name=name, label=label or name.title(), type=type_, **kw)  # type: ignore[arg-type]


def _run(f: TargetField, values: list[str], choices: dict[str, str] | None = None):  # noqa: ANN202
    return check_column(f, "col", 0, values, choices)


# --------------------------------------------------------------------------- #
# Transformations déterministes
# --------------------------------------------------------------------------- #
def test_numbers_only_change_form_never_value() -> None:
    price = _f("price", "float")
    r = _run(
        price, ['"57.79"', "1.795000000000000e+01", "3,479.00", "1.234,56", "12,5", "1 299,50", "null", ""]
    )
    assert r.values == ["57.79", "17.95", "3479", "1234.56", "12.5", "1299.5", "", ""]
    assert {t.kind for t in r.transforms} == {"quotes", "scientific", "separators"}
    assert r.questions == [] and r.issues == []
    # avant → après montré
    sci = next(t for t in r.transforms if t.kind == "scientific")
    assert sci.examples == [["1.795000000000000e+01", "17.95"]]


def test_a_number_that_can_be_read_two_ways_is_a_question_unless_the_column_proves_it() -> None:
    price = _f("price", "float")
    asked = _run(price, ["1,234", "2,500"])
    assert [q.kind for q in asked.questions] == ["decimal"] and asked.values == ["", ""]
    assert asked.issues[0].kind == "decimal" and asked.issues[0].count == 2
    point = _run(price, ["1,234", "2,500"], {"decimal:0": "point"})
    assert point.values == ["1234", "2500"]
    comma = _run(price, ["1,234", "2,500"], {"decimal:0": "comma"})
    assert comma.values == ["1.234", "2.5"]
    # la colonne prouve le séparateur (« 12.50 ») : aucune question, lecture cohérente
    proven = _run(price, ["1,234", "12.50"])
    assert proven.questions == [] and proven.values == ["1234", "12.50"]
    with pytest.raises(ValueChoiceError):
        _run(price, ["1,234"], {"decimal:0": "guess"})


def test_integers_booleans_and_allowed_values() -> None:
    qty = _f("qty", "integer")
    r = _run(qty, ["12.0", "12.5", "7"])
    assert r.values == ["12", "", "7"] and r.issues[0].kind == "invalid"
    flag = _f("ok", "boolean")
    assert _run(flag, ["Yes", "non", "1", "maybe"]).values == ["true", "false", "true", ""]
    kind = _f("kind", "selection", allowed=[AllowedValue(value="consu", label="Goods")])
    assert _run(kind, ["goods", "consu", "Robots"]).values == ["consu", "consu", ""]
    code = _f("code", "char", length=4)
    assert _run(code, ["ABCDE"]).issues[0].kind == "too_long"  # jamais coupé


def test_dates_are_written_in_one_form_and_never_guessed() -> None:
    day = _f("day", "date")
    r = _run(day, ["2024-03-04", "February 17, 2022", "2023-08-08 00:00:00.000", "31/12/2024", "2024/1/5"])
    assert r.values == ["2024-03-04", "2022-02-17", "2023-08-08", "2024-12-31", "2024-01-05"]
    assert r.questions == []  # « 31/12 » prouve l'ordre jour / mois : aucune question
    asked = _run(day, ["03/04/2024", "05/06/2024"])
    assert [q.kind for q in asked.questions] == ["date_order"] and asked.values == ["", ""]
    assert _run(day, ["03/04/2024"], {"date_order:0": "mdy"}).values == ["2024-03-04"]
    assert _run(day, ["03/04/2024"], {"date_order:0": "dmy"}).values == ["2024-04-03"]
    lost = _run(day, ["2024-03-04 10:30:00", "04/03/24", "2024-03-04T10:00:00+02:00"])
    assert [i.kind for i in lost.issues] == ["time_lost", "two_digit_year", "time_zone"]
    when = _f("when", "datetime")
    assert _run(when, ["2024-03-04T10:30:00"]).values == ["2024-03-04 10:30:00"]


def test_lists_units_and_structured_values_wait_for_a_decision_by_type_not_by_row() -> None:
    cat = _f("categ", "many2one", "Category")
    r = _run(cat, ['["Shoes","Running","Trail"]', '["Home"]', "[]", '{"a": 1}'])
    assert [q.key for q in r.questions] == ["list:0"]
    assert {i.kind: i.count for i in r.issues} == {"list": 2, "structured": 1}
    last = r.questions[0].options[0]
    assert last.value == "last" and last.examples[0][1] == "Trail"  # avant → après de l'option
    assert _run(cat, ['["Shoes","Trail"]'], {"list:0": "path"}).values == ["Shoes / Trail"]
    skipped = _run(cat, ['["Shoes","Trail"]'], {"list:0": "skip"})
    assert skipped.values == [""] and skipped.issues[0].kind == "left_out"
    tags = _f("tags", "many2many")
    assert _run(tags, ['["a","b"]']).questions == []  # champ à plusieurs valeurs : pas de question


# --------------------------------------------------------------------------- #
# Unités : une question, conversions exactes, jamais entre dimensions
# --------------------------------------------------------------------------- #
WEIGHTS = ["4.66 pounds", "\u200e0.05 Pounds", "399 g", "0.634 ounces", "0.2 Kilograms", "120 Milligrams"]


def test_one_question_for_the_target_unit_offers_every_compatible_unit() -> None:
    w = _run(_f("weight", "float"), ["4.66 pounds", "8 oz"])
    q = w.questions[0]
    # unités du fichier d'abord, puis les unités de même dimension absentes du fichier
    assert [o.value for o in q.options] == ["lb", "oz", "kg", "g", "mg", "none"]
    assert [o.label for o in q.options][:3] == ["pounds (1 value)", "ounces (1 value)", "kilograms"]
    assert q.options[2].examples == [["4.66 pounds", "2.1137"], ["8 oz", "0.227"]]  # avant → après
    assert w.values == ["", ""]  # rien d'écrit avant la réponse


def test_compatible_units_are_converted_with_exact_factors_and_the_source_is_kept() -> None:
    to_kg = _run(_f("weight", "float"), WEIGHTS, {"unit:0": "kg"})
    assert to_kg.values == ["2.1137", "0.0227", "0.399", "0.017974", "0.2", "0.00012"]
    assert to_kg.issues == []
    assert to_kg.conversions[0] == (0, "4.66 pounds", "2.1137")  # ligne, valeur source, valeur écrite
    labels = {t.kind: t.label for t in to_kg.transforms}
    assert labels["convert:lb"] == "Converted from pounds to kilograms (× 0.45359237)"
    to_lb = _run(_f("weight", "float"), WEIGHTS, {"unit:0": "lb"})
    assert to_lb.values == ["4.66", "0.05", "0.8796", "0.039625", "0.44", "0.0002646"]
    # facteurs exacts : 2 lb = 32 oz, 1.5 kg = 1500 g, 1 lb = 453.59237 g
    oz = _run(_f("w", "float"), ["2 lb", "1.5 kg", "1 lb"], {"unit:0": "oz"})
    assert oz.values[0] == "32"
    grams = _run(_f("w", "float"), ["1.5 kg", "1 lb"], {"unit:0": "g"})
    assert grams.values == ["1500", "453.6"]  # précision : celle de la source, + 1 chiffre


def test_never_converted_between_dimensions_never_rounded_never_guessed() -> None:
    mixed = _run(_f("weight", "float"), ["2 kg", "30 cm", "12 %", "5", "2.5 lbs 3 lbs"], {"unit:0": "lb"})
    assert mixed.values == ["4.4", "", "", "", ""]
    assert {i.kind: i.count for i in mixed.issues} == {
        "incompatible_unit": 2,  # cm, % : autre dimension (ou inconnue) — jamais converties
        "no_unit": 1,  # « 5 » sans unité au milieu d'unités : jamais deviné
        "invalid": 1,  # valeur composée : jamais découpée
    }
    whole = _run(_f("grams", "integer"), ["1 lb", "2 kg"], {"unit:0": "g"})
    assert whole.values == ["", "2000"] and whole.issues[0].kind == "not_whole"  # 453.59237 : jamais arrondi
    left = _run(_f("weight", "float"), ["2 kg"], {"unit:0": "none"})
    assert left.values == [""] and left.issues[0].kind == "left_out"


def test_a_new_dimension_is_only_new_rows_in_the_unit_table(monkeypatch: pytest.MonkeyPatch) -> None:
    from decimal import Decimal

    from uie_core.wizard import values

    table = dict(values.CONVERTIBLE_UNITS)
    table["m"] = values.Unit("m", "length", Decimal("1"), "metres")
    table["cm"] = values.Unit("cm", "length", Decimal("0.01"), "centimetres")
    monkeypatch.setattr(values, "CONVERTIBLE_UNITS", table)
    r = _run(_f("depth", "float"), ["30 cm", "1.2 m", "2 kg"], {"unit:0": "m"})
    assert r.values == ["0.3", "1.2", ""]  # kg : autre dimension, jamais converti
    assert {i.kind for i in r.issues} == {"incompatible_unit"}


def test_money_and_currency_detection_is_generic() -> None:
    assert is_money(_f("list_price", "float", "Sales Price"))
    assert is_money(_f("x", "monetary", "Montant"))
    assert not is_money(_f("weight", "float", "Weight"))
    found = detect_currency(["a", "cur"], [["x", "y", "z"], ["USD", '"INR"', ""]])
    assert found is not None and found.counts == {"USD": 1, "INR": 1}
    amount = _f("price", "float", "Price")
    r = _run(amount, ["€12,50", "12 EUR", "$5", "£3"])
    assert r.values == ["12.5", "12", "", "3"] and r.currencies == ["EUR", "EUR", "$", "GBP"]
    assert r.issues[0].kind == "currency_sign"  # « $ » : plusieurs devises possibles, jamais deviné
    assert unwrap('"Foo" bar "baz"') == ('"Foo" bar "baz"', False)  # guillemets intérieurs : intacts
    # caractères invisibles et espaces retirés en BORD seulement : l'intérieur d'un texte n'est jamais touché
    assert unwrap("Ring\xa096410") == ("Ring\xa096410", False)
    assert unwrap("‏  ‎ October 25, 2004") == ("October 25, 2004", True)


def test_ai_suggests_an_offered_option_only_and_never_applies_it() -> None:
    q = _run(_f("day", "date"), ["03/04/2024"]).questions

    class Fake:
        def __init__(self, value: str) -> None:
            self.value = value
            self.requests: list[object] = []

        def wizard_value_rule(self, request):  # noqa: ANN001, ANN201
            self.requests.append(request)
            return ValueRuleSuggestion(value=self.value, confidence=0.95)

    good = suggest_rules(q, Fake("dmy"), {}, {"date_order:0": ["03/04/2024"]})
    assert good[0].suggested == "dmy" and good[0].suggested_by == "ai" and good[0].answer is None
    invented = suggest_rules(q, Fake("2024-04-03"), {}, {})
    assert invented[0].suggested is None  # une valeur n'est jamais une option


# --------------------------------------------------------------------------- #
# Fichier entier
# --------------------------------------------------------------------------- #
MARKET_CSV = b"\n".join(
    [
        b"title,final_price,currency,categories,item_weight,first_available",
        b'Trail shoe,"""57.79""",USD,"[""Shoes"",""Running"",""Trail""]",4.66 pounds,"February 17, 2022"',
        b'Desk lamp,1.795000000000000e+01,USD,"[""Home"",""Lighting""]",399 g,',
        b'Tea set,"""799""",INR,"[""Kitchen""]",1.2 pounds,"March 14, 2005"',
        b'Mug,"""3,479.00""",USD,"[""Kitchen"",""Mugs""]",,',
        b"Vest,null,GBP,[],null,",
        b"",
    ]
)


def _proposal() -> TargetProposal:
    return TargetProposal(
        title="Products",
        summary="",
        configuration=[],
        rows=[],
        fields=[
            _f("name", "char", "Name", required=True),
            _f("list_price", "float", "Sales Price"),
            _f("categ_id", "many2one", "Product Category", reference=True),
            _f("weight", "float", "Weight"),
            _f("launch", "date", "Launch date"),
        ],
    )


DECISIONS = [
    MappingDecision(position=0, target="name"),
    MappingDecision(position=1, target="list_price"),
    MappingDecision(position=3, target="categ_id"),
    MappingDecision(position=4, target="weight"),
    MappingDecision(position=5, target="launch"),
]


def test_a_whole_file_asks_one_question_per_column_and_kind() -> None:
    summary, rows, _ = prepare(_proposal(), MARKET_CSV, "market.csv", DECISIONS)
    assert [q.key for q in summary.value_questions] == ["list:3", "unit:4", "currency"]
    assert all(q.answer is None for q in summary.value_questions)
    assert all(r[1] == "" and r[2] == "" and r[3] == "" for r in rows)  # rien d'écrit en attendant
    assert [r[4] for r in rows] == ["2022-02-17", "", "2005-03-14", "", ""]  # dates : déterministes


def test_with_the_decisions_rows_in_another_currency_keep_everything_but_their_amounts() -> None:
    """Ligne en INR, cible en USD : la ligne est GARDÉE avec toutes ses autres valeurs ; son prix n'est pas
    écrit (jamais converti), il est listé avec sa devise d'origine (« Values left empty », « Problems »)."""
    choices = {"list:3": "last", "unit:4": "lb", "currency": "usd"}
    summary, rows, pairs = prepare(_proposal(), MARKET_CSV, "market.csv", DECISIONS, choices=choices)
    assert summary.currency == "USD" and (summary.source_rows, summary.rows) == (5, 5)
    assert summary.excluded_rows == [] and summary.currency_notice is None  # 1 ligne sur 5 : pas de bandeau
    # 399 g : converti en livres (facteur exact), source conservée ; prix INR non écrit ; prix ABSENT signalé
    assert [(i.kind, i.rows, i.severity) for i in summary.value_issues] == [
        ("currency_other", [3], "not_written"),
        ("missing_price", [5], "to_check"),
    ]
    assert summary.value_issues[0].message == (
        "1 amount of Sales Price in INR, not USD: not written, never converted."
    )
    assert [(c.row, c.source, c.written) for c in summary.value_conversions] == [(2, "399 g", "0.8796")]
    content = render_prepared(_proposal(), summary, rows, pairs)
    data = pl.read_excel(io.BytesIO(content), sheet_name="Data", infer_schema_length=0)
    assert data["list_price"].to_list() == ["57.79", "17.95", None, "3479", None]
    assert data["categ_id"].to_list() == ["Trail", "Lighting", "Kitchen", "Mugs", None]
    problems = pl.read_excel(io.BytesIO(content), sheet_name="Problems", infer_schema_length=0)
    assert problems.row(0)[0] == "Not written" and "in INR, not USD" in problems.row(0)[3]
    about = dict(pl.read_excel(io.BytesIO(content), sheet_name="Read me", has_header=False).rows())
    assert about["Currency"].startswith("Amounts kept in USD")
    assert "Scientific notation" in about["Transformations"]
    assert "lists" in about["Your decisions"]
    conv = pl.read_excel(io.BytesIO(content), sheet_name="Converted values", infer_schema_length=0)
    assert conv.rows() == [("2", "item_weight", "weight", "399 g", "0.8796")]
    assert data["weight"].to_list() == ["4.66", "0.8796", "1.2", None, None]
    with pytest.raises(ValueChoiceError):
        prepare(_proposal(), MARKET_CSV, "market.csv", DECISIONS, choices={"currency": "XYZ"})


def test_escaped_quotes_of_an_export_are_written_as_plain_quotes() -> None:
    r"""« Men\'s », « 5\" (125 mm) » (échappements d'un export JSON / SQL) → « Men's », « 5" (125 mm) » ; une
    autre barre oblique inverse est une donnée (« Twin\TwinXL »), gardée ; transformation listée."""
    name = _f("name", "char", "Name")
    raw = ["Saucony Men\\'s Kinvara 13", 'Straight 5\\" (125 mm) Auger', "Twin\\TwinXL", "Plain"]
    r = check_column(name, "title", 0, raw)
    assert r.values == ["Saucony Men's Kinvara 13", 'Straight 5" (125 mm) Auger', "Twin\\TwinXL", "Plain"]
    (t,) = r.transforms
    assert t.kind == "escapes" and t.count == 2 and "plain quotes" in t.label
    assert r.issues == []


def test_more_than_half_of_the_rows_in_another_currency_gives_a_notice_and_keeps_every_row() -> None:
    proposal = TargetProposal(
        title="Products",
        summary="",
        configuration=[],
        rows=[],
        fields=[_f("name", "char", "Name", required=True), _f("list_price", "float", "Sales Price")],
    )
    csv = b"name,price,currency\nA,10,USD\nB,11,USD\nC,12,EUR\nD,13,USD\nE,,USD\n"
    decisions = [MappingDecision(position=0, target="name"), MappingDecision(position=1, target="list_price")]
    summary, rows, _ = prepare(
        proposal, csv, "shop.csv", decisions, choices={"currency": "EUR"}, noun="products"
    )
    assert summary.rows == 5 and summary.excluded_rows == []  # aucune ligne retirée
    notice = summary.currency_notice
    assert notice and (notice.target, notice.file, notice.rows, notice.fields) == (
        "EUR",
        ["USD"],
        3,
        ["Sales Price"],
    )
    assert [r[1] for r in rows] == ["", "", "12", "", ""]  # seul le prix en EUR est écrit
    # « Import the products without their prices » : toutes les lignes, aucun montant
    summary, rows, _ = prepare(
        proposal, csv, "shop.csv", decisions, choices={"currency": "without_prices"}, noun="products"
    )
    assert summary.rows == 5 and not any(r[1] for r in rows) and summary.currency is None
    (issue,) = [i for i in summary.value_issues if i.kind == "currency_left_out"]
    assert issue.count == 4 and "import the products without their prices" in issue.message
    (q,) = [q for q in summary.value_questions if q.kind == "currency"]
    assert q.options[-1].value == "without_prices" and q.options[-1].label == (
        "Import the products without their prices"
    )


def test_the_message_says_what_the_target_import_writes_instead_of_an_empty_amount() -> None:
    """Montants laissés vides : la phrase dit ce que l'import de la cible écrira (valeur par défaut si la
    colonne est absente du fichier préparé, valeur d'une cellule vide sinon) ; Nexa45 ne les écrit jamais."""
    from uie_core.domains import ImportDefault

    proposal = TargetProposal(
        title="Products",
        summary="",
        configuration=[],
        rows=[],
        fields=[_f("name", "char", "Name", required=True), _f("list_price", "float", "Sales Price")],
    )
    decisions = [MappingDecision(position=0, target="name"), MappingDecision(position=1, target="list_price")]
    defaults = {"list_price": ImportDefault(absent="1.00", empty="0.00")}

    def run(csv: bytes) -> tuple[Any, list[list[str]]]:
        summary, rows, _ = prepare(
            proposal,
            csv,
            "shop.csv",
            decisions,
            choices={"currency": "EUR"},
            noun="products",
            import_defaults=defaults,
            target_label="Odoo 17",
        )
        return summary, rows

    # tous les prix dans une autre devise : colonne absente du fichier → valeur par défaut de la cible
    summary, rows = run(b"name,price,currency\nA,10,USD\nB,11,USD\nC,12,USD\n")
    assert not any(r[1] for r in rows)  # rien d'écrit, jamais 0
    note = "Odoo 17 will use its default Sales Price, 1.00, for these products."
    assert summary.currency_notice and summary.currency_notice.note == note
    (issue,) = [i for i in summary.value_issues if i.kind == "currency_other"]
    assert issue.message.endswith(note)
    # un prix en EUR : la colonne est écrite, les autres cellules restent VIDES → valeur d'une cellule vide
    summary, rows = run(b"name,price,currency\nA,10,USD\nB,11,USD\nC,12,EUR\nD,13,USD\n")
    assert [r[1] for r in rows] == ["", "", "12", ""]
    assert (
        summary.currency_notice
        and summary.currency_notice.note == "Odoo 17 imports an empty Sales Price as 0.00."
    )
    # cible sans valeurs connues : aucune phrase inventée
    summary, _, _ = prepare(
        proposal, b"name,price,currency\nA,10,USD\n", "s.csv", decisions, choices={"currency": "EUR"}
    )
    assert summary.currency_notice and summary.currency_notice.note == ""


def test_a_two_digit_year_is_a_decision_preselected_with_the_century_of_the_other_dates() -> None:
    f = _f("document_date", "date", "Invoice date")
    values = ["2026-07-05 00:00:00", "27.07.2026", "5.9.26", "12.09.2026"]
    r = check_column(f, "Re-Datum", 1, values, {})
    (q,) = r.questions
    assert (q.key, q.suggested, q.answer) == ("century:1", "20", None)  # présélection, jamais appliquée seule
    assert r.values[2] == "" and [i.kind for i in r.issues] == ["two_digit_year"]
    r = check_column(f, "Re-Datum", 1, values, {"century:1": "20"})
    assert r.values == ["2026-07-05", "2026-07-27", "2026-09-05", "2026-09-12"] and r.issues == []


def test_only_interpreted_dates_are_listed_as_written_differently() -> None:
    """« Written differently » : une date seulement remise au format standard (« 2026-07-28 00:00:00 »,
    « 27.07.2026 ») n'est pas listée ; une année à deux chiffres complétée par le siècle choisi l'est."""
    f = _f("document_date", "date", "Invoice date")
    values = ["2026-07-28 00:00:00", "27.07.2026", "5.9.26", "2026-09-04"]
    r = check_column(f, "Re-Datum", 1, values, {"century:1": "20"})
    assert r.values == ["2026-07-28", "2026-07-27", "2026-09-05", "2026-09-04"]
    assert [(t.kind, t.examples) for t in r.transforms] == [("date_century", [["5.9.26", "2026-09-05"]])]
    # aucune interprétation : rien à lister
    plain = check_column(f, "Re-Datum", 1, ["2026-07-28 00:00:00", "27.07.2026"], {})
    assert plain.transforms == []
    # date ambiguë lue dans l'ordre décidé : listée
    ambiguous = check_column(f, "Datum", 2, ["03/04/2024", "05/06/2024"], {"date_order:2": "dmy"})
    assert [t.kind for t in ambiguous.transforms] == ["date_read_order"]


def test_a_date_order_proven_by_another_value_of_the_column_is_not_listed() -> None:
    """« 13.10.2026 » prouve « le jour d'abord » : les autres dates (05.10.2026…) ne sont ni une question ni
    « read two ways » ; seule une décision visible (la question) est dite « as decided »."""
    f = _f("document_date", "date", "Invoice date")
    r = check_column(f, "Re-Datum", 1, ["05.10.2026", "06.10.2026", "13.10.2026"], {})
    assert r.values == ["2026-10-05", "2026-10-06", "2026-10-13"]
    assert r.questions == [] and r.transforms == []
