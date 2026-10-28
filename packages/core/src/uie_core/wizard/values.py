"""Moteur générique de validation et de transformation des valeurs (AI Migration Wizard, fichier préparé).

Pour chaque colonne mappée, chaque valeur est RECONNUE selon le type de son champ cible (nombre, entier, date,
oui / non, valeurs admises, texte, liste JSON, unité, devise), puis :
- transformée seulement si la transformation est DÉTERMINISTE et sans perte (guillemets, « null », notation
  scientifique, séparateurs non ambigus, date écrite autrement, oui / non) : comptée, avec avant / après ;
- sinon une QUESTION est posée une fois pour la colonne (jamais par ligne) : quelle valeur d'une liste, quel
  séparateur décimal, quel ordre jour / mois, quelle unité, quelle devise ; tant qu'elle est ouverte, les
  valeurs concernées ne sont pas écrites ;
- une valeur qu'aucune règle ne lit n'est jamais écrite ni devinée : elle est comptée, groupée par sorte,
  avec des exemples.

Jamais de conversion (unité, devise, fuseau horaire), jamais de valeur inventée, jamais de référence créée
dans la cible (une référence reste du texte, vérifiée ailleurs). L'IA peut SUGGÉRER une option d'une
question (`ValueRuleProposer`), validée parmi les options offertes, jamais appliquée seule et jamais une
valeur. Aucun nom de système : seuls le type du champ (Registry ou structure) et les valeurs comptent."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from uie_core.imports import mask_example
from uie_core.wizard.model import TargetField
from uie_core.wizard.text import say


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------- #
# Résultats
# --------------------------------------------------------------------------- #
class ValueIssue(_Model):
    """Valeurs NON écrites d'une colonne, groupées par sorte : comptées, avec des exemples."""

    field: str
    column: str
    kind: str
    count: int
    examples: list[str]
    message: str
    # numéros de ligne du fichier du client (1 = première ligne de données), au plus ROWS_KEPT ;
    # `count` = total
    rows: list[int] = []
    fix: str = ""  # solution simple pour l'utilisateur (jamais une valeur proposée à sa place)
    # not_written : valeur NON écrite ; to_check : valeur écrite (ou absente) à vérifier avant l'import ;
    # serious : anomalie grave du fichier (affichée en tête) ; info : information, rien à faire
    severity: str = "not_written"


class ValueTransform(_Model):
    """Transformation déterministe appliquée (même valeur, autre écriture) : comptée, avant → après."""

    field: str
    column: str
    kind: str
    label: str
    count: int
    examples: list[list[str]]  # [avant, après]


class QuestionOption(_Model):
    value: str
    label: str
    examples: list[list[str]] = []  # [avant, après] si cette option est choisie


class ValueQuestion(_Model):
    """Décision de l'utilisateur, une fois par colonne et par sorte (ou une fois pour le fichier : devise)."""

    key: str  # « list:3 », « decimal:6 », « date_order:2 », « unit:9 », « currency »
    kind: str
    field: str | None
    column: str | None
    title: str
    help: str
    count: int  # valeurs concernées
    options: list[QuestionOption]
    allow_other: bool = False  # devise : un autre code ISO 4217 peut être saisi
    suggested: str | None = None  # option suggérée (règle ou IA) : jamais appliquée seule
    suggested_by: str | None = None  # rule | ai
    answer: str | None = None  # choix de l'utilisateur


# réponse à la question de la devise : les lignes sont importées SANS leurs montants (aucun n'est écrit)
WITHOUT_PRICES = "without_prices"


class CurrencyColumn(_Model):
    """Colonne du fichier dont les valeurs sont des codes de devise : nombre de lignes par code."""

    position: int
    header: str
    counts: dict[str, int]


class ExcludedRow(_Model):
    row: int  # numéro de ligne dans le fichier du client (1 = première ligne de données)
    reason: str
    kind: str = ""  # sorte du motif, pour les regrouper : "currency" (autre devise), "duplicate" (doublon)


class ValueChoiceError(ValueError):
    """Choix invalide pour une question ouverte (→ 422)."""


# --------------------------------------------------------------------------- #
# Connaissances génériques (normes et conventions, aucun système)
# --------------------------------------------------------------------------- #
ISO_4217 = frozenset(
    """AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BRL BSD BTN BWP BYN BZD
    CAD CDF CHF CLP CNY COP CRC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD GNF
    GTQ GYD HKD HNL HTG HUF IDR ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP
    LKR LRD LSL LYD MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB
    PEN PGK PHP PKR PLN PYG QAR RON RSD RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SOS SRD SSP STN SVC SYP SZL
    THB TJS TMT TND TOP TRY TTD TWD TZS UAH UGX USD UYU UZS VES VND VUV WST XAF XCD XOF XPF YER ZAR ZMW
    ZWL""".split()
)
# signes sans ambiguïté seulement ; « $ », « ¥ », « kr » désignent plusieurs devises : jamais devinés
CURRENCY_SIGNS = {"€": "EUR", "£": "GBP", "₹": "INR", "₩": "KRW", "₺": "TRY", "₽": "RUB", "₪": "ILS"}
AMBIGUOUS_SIGNS = frozenset({"$", "¥", "kr"})
MONEY_WORDS = frozenset(
    "price prices cost costs amount amounts total subtotal fee fees tariff prix cout coût montant tarif "
    "preis kosten betrag summe precio coste importe prezzo costo importo".split()
)
MONEY_TYPES = frozenset({"monetary", "money", "currency", "amount", "price"})
NUMBER_TYPES = frozenset({"number", "decimal", "float", "monetary", "money", "currency", "amount", "price"})
INTEGER_TYPES = frozenset({"integer", "int"})
BOOLEAN_TYPES = frozenset({"boolean", "bool"})
DATE_TYPES = frozenset({"date"})
DATETIME_TYPES = frozenset({"datetime", "timestamp"})
MULTI_TYPES = frozenset({"many2many", "one2many", "list", "tags", "array", "multiple"})
NULL_TOKENS = frozenset({"null", "none", "nil", "nan", "n/a", "undefined"})
TRUE_WORDS = frozenset({"true", "yes", "y", "1", "oui", "ja", "si", "sí", "vrai", "wahr"})
FALSE_WORDS = frozenset({"false", "no", "n", "0", "non", "nein", "faux", "falsch"})
# orthographes d'une même unité (regroupement seulement : aucune conversion entre unités)
UNIT_ALIASES = {
    "g": "g", "gr": "g", "gram": "g", "grams": "g", "gramme": "g", "grammes": "g",
    "kg": "kg", "kgs": "kg", "kilogram": "kg", "kilograms": "kg", "kilogramme": "kg", "kilogrammes": "kg",
    "lb": "lb", "lbs": "lb", "pound": "lb", "pounds": "lb",
    "oz": "oz", "ounce": "oz", "ounces": "oz",
    "mg": "mg", "milligram": "mg", "milligrams": "mg",
    "t": "t", "ton": "t", "tons": "t", "tonne": "t", "tonnes": "t",
    "mm": "mm", "cm": "cm", "m": "m", "km": "km", "in": "in", "inch": "in", "inches": "in", "ft": "ft",
    "feet": "ft", "foot": "ft", "l": "l", "liter": "l", "liters": "l", "litre": "l", "litres": "l",
    "ml": "ml", "milliliter": "ml", "milliliters": "ml", "%": "%", "percent": "%",
    "m3": "m3", "m³": "m3", "cbm": "m3", "meter": "m", "metre": "m", "meters": "m", "metres": "m",
    "kilogramm": "kg", "gramm": "g", "zentimeter": "cm", "millimeter": "mm", "kilometer": "km",
}  # fmt: skip
UNIT_NAMES = {
    "g": "grams", "kg": "kilograms", "lb": "pounds", "oz": "ounces", "mg": "milligrams", "t": "tonnes",
    "mm": "millimetres", "cm": "centimetres", "m": "metres", "km": "kilometres", "in": "inches", "ft": "feet",
    "l": "litres", "ml": "millilitres", "%": "percent", "m3": "cubic metres",
}  # fmt: skip
# valeurs non écrites par un CHOIX explicite de l'utilisateur (listées, jamais bloquantes pour « Ready »)
LEFT_OUT_BY_CHOICE = frozenset({"left_out"})
EXAMPLES = 3
ROWS_KEPT = 200  # numéros de ligne gardés par anomalie (le total reste dans `count`)


@dataclass(frozen=True)
class Unit:
    """Unité convertible : dimension et facteur EXACT vers l'unité de base de cette dimension."""

    code: str
    dimension: str
    factor: Decimal
    name: str


# Facteurs DÉFINIS, donc exacts (SI ; livre et once avoirdupois de l'accord international de 1959).
# Ajouter une dimension (longueur, volume…) = ajouter ses unités ici, avec l'unité de base de facteur 1 :
# rien d'autre à changer. On ne convertit jamais entre deux dimensions.
CONVERTIBLE_UNITS: dict[str, Unit] = {
    u.code: u
    for u in (
        Unit("kg", "mass", Decimal("1"), "kilograms"),
        Unit("g", "mass", Decimal("0.001"), "grams"),
        Unit("mg", "mass", Decimal("0.000001"), "milligrams"),
        Unit("lb", "mass", Decimal("0.45359237"), "pounds"),
        Unit("oz", "mass", Decimal("0.028349523125"), "ounces"),
        Unit("m", "length", Decimal("1"), "metres"),
        Unit("cm", "length", Decimal("0.01"), "centimetres"),
        Unit("mm", "length", Decimal("0.001"), "millimetres"),
        Unit("km", "length", Decimal("1000"), "kilometres"),
        Unit("in", "length", Decimal("0.0254"), "inches"),
        Unit("ft", "length", Decimal("0.3048"), "feet"),
        Unit("m3", "volume", Decimal("1"), "cubic metres"),
        Unit("l", "volume", Decimal("0.001"), "litres"),
        Unit("ml", "volume", Decimal("0.000001"), "millilitres"),
    )
}
_HEADER_UNIT = re.compile(r"[(\[]\s*([^)\]]+?)\s*[)\]]\s*$")


def column_unit(header: str) -> str | None:
    """Unité d'une colonne donnée par son en-tête (« Gewicht (kg) », « Length [cm] ») ; None : aucune."""
    m = _HEADER_UNIT.search(header)
    code = UNIT_ALIASES.get(m.group(1).casefold()) if m else None
    return code if code in CONVERTIBLE_UNITS else None


def convert(value: str, source: Unit, target: Unit, integer: bool = False) -> str | None:
    """Valeur (écriture décimale) convertie de `source` vers `target`, ou None si la conversion est
    impossible : dimensions différentes, ou champ entier et résultat non entier (jamais arrondi). Précision :
    celle de la valeur source, ajustée au rapport des unités (mêmes chiffres significatifs + 1)."""
    if source.dimension != target.dimension:
        return None
    if source.code == target.code:
        return value
    ratio = source.factor / target.factor
    exact = Decimal(value) * source.factor / target.factor
    if integer:
        return str(int(exact)) if exact == exact.to_integral_value() else None
    exponent = Decimal(value).as_tuple().exponent
    decimals = -exponent if isinstance(exponent, int) and exponent < 0 else 0
    places = decimals + max(0, math.ceil(-ratio.log10())) + 1
    return _decimal(str(exact.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)))


PATH_SEPARATOR = " / "
# caractères invisibles et espaces en BORD de valeur seulement : l'intérieur d'un texte n'est jamais touché
_EDGE = r"[\s\u200b-\u200f\u202a-\u202e\u2060\ufeff]"
_EDGES = re.compile(rf"^{_EDGE}+|{_EDGE}+$")
_PLAIN = re.compile(r"^-?\d+(\.\d+)?$")
_SCIENTIFIC = re.compile(r"^-?\d+(\.\d+)?[eE][+-]?\d+$")
_NUMBERISH = re.compile(r"^[+-]?[\d.,' ]+$")
_AMBIGUOUS = re.compile(r"^-?[1-9]\d{0,2}[.,]\d{3}$")  # « 1,234 » / « 1.234 » : milliers OU décimales
_MARK = re.compile(
    r"^(?P<a>[A-Z]{3}|[^\w\s\d.,+-]{1,2}|kr)?\s*(?P<n>[+-]?[\d.,' ]*\d)\s*"
    r"(?P<b>[A-Z]{3}|[^\w\s\d.,]{1,2}|kr)?$"
)
_UNIT = re.compile(r"^(?P<n>[+-]?[\d.,' ]*\d)\s*(?P<u>%|[^\W\d_][^\d]{0,20})$")
_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_ISO_TIME = re.compile(
    r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?(?P<tz>Z|[+-]\d{2}:?\d{2})?$"
)
_YMD = re.compile(r"^(\d{4})[/.](\d{1,2})[/.](\d{1,2})$")
_NUMERIC_DATE = re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{2}|\d{4})$")
_TEXT_DATES = (
    "%B %d, %Y",
    "%b %d, %Y",
    "%B %d %Y",
    "%b %d %Y",
    "%d %B %Y",
    "%d %b %Y",
    "%d. %B %Y",
    "%d-%b-%Y",
)


def _clip(value: str, size: int = 80) -> str:
    return value if len(value) <= size else value[: size - 1] + "…"


# --------------------------------------------------------------------------- #
# Sortes de champ, montants, devises
# --------------------------------------------------------------------------- #
def kind_of(f: TargetField) -> str:
    """Sorte de valeur attendue par le champ (d'après son type, ou ses valeurs admises)."""
    t = f.type.casefold()
    if f.allowed:
        return "choice"
    for kind, types in (
        ("integer", INTEGER_TYPES),
        ("number", NUMBER_TYPES),
        ("boolean", BOOLEAN_TYPES),
        ("date", DATE_TYPES),
        ("datetime", DATETIME_TYPES),
        ("multi", MULTI_TYPES),
    ):
        if t in types:
            return kind
    return "text"


def is_money(f: TargetField) -> bool:
    """Champ numérique qui porte un montant (type monétaire, ou un mot de montant dans son nom / libellé)."""
    if kind_of(f) not in {"number", "integer"}:
        return False
    if f.type.casefold() in MONEY_TYPES:
        return True
    words = set(re.split(r"[^\wÀ-ÿ]+", f"{f.name} {f.label}".replace("_", " ").casefold()))
    return bool(words & MONEY_WORDS)


_ESCAPED_QUOTE = re.compile(r"\\(['\"])")  # \' ou \" d'un export (JSON, SQL, PHP…)


def unwrap(value: str) -> tuple[str, bool]:
    """Forme seulement : espaces et caractères invisibles, guillemets autour de la valeur, « null » → vide."""
    v = _EDGES.sub("", value)
    out = v
    # guillemets AUTOUR de toute la valeur seulement (« "Foo" bar "baz" » reste intact)
    while len(out) >= 2 and out[0] == out[-1] and out[0] in "\"'" and out[0] not in out[1:-1]:
        out = out[1:-1].strip()
    if out.casefold() in NULL_TOKENS:
        out = ""
    return out, out != value.strip()


def structured(value: str) -> list[Any] | dict[str, Any] | None:
    t = value.strip()
    if not t or t[0] not in "[{":
        return None
    try:
        parsed = json.loads(t)
    except ValueError:
        return None
    return parsed if isinstance(parsed, list | dict) else None


def _scalars(parsed: list[Any] | dict[str, Any]) -> list[str] | None:
    if not isinstance(parsed, list) or any(isinstance(x, list | dict) for x in parsed):
        return None
    return [str(x).strip() for x in parsed if x is not None and str(x).strip()]


def detect_currency(headers: list[str], columns: list[list[str]]) -> CurrencyColumn | None:
    """Première colonne dont TOUTES les valeurs non vides sont des codes ISO 4217."""
    for pos, (header, values) in enumerate(zip(headers, columns, strict=True)):
        filled = [c for c in (unwrap(v)[0].upper() for v in values) if c]
        if filled and all(c in ISO_4217 for c in filled):
            return CurrencyColumn(position=pos, header=header, counts=dict(Counter(filled).most_common()))
    return None


def scan_currencies(columns: list[list[str]]) -> Counter[str]:
    """Devises citées dans les VALEURS du fichier, toutes colonnes (ex. « 1.299,00 € » d'une colonne non
    importée) : code ISO ou signe sans ambiguïté → code ; signe ambigu ($, ¥, kr) → le signe tel quel (jamais
    deviné). Rien n'est modifié."""
    found: Counter[str] = Counter()
    for values in columns:
        for raw in values:
            v = unwrap(raw)[0] if raw else ""
            m = _MARK.match(v) if v else None
            if not m or bool(m.group("a")) == bool(m.group("b")):
                continue
            mark = m.group("a") or m.group("b")
            code = mark if mark in ISO_4217 else CURRENCY_SIGNS.get(mark)
            if code:
                found[code] += 1
            elif mark in AMBIGUOUS_SIGNS:
                found[mark] += 1
    return found


# --------------------------------------------------------------------------- #
# Lecture des nombres
# --------------------------------------------------------------------------- #
@dataclass
class _Num:
    value: str | None = None  # écriture décimale (point), si lue sans ambiguïté
    alt: tuple[str, str] | None = None  # « 1,234 » : (milliers, décimales)
    sep: str | None = None  # séparateur décimal PROUVÉ par cette valeur
    unit: str | None = None
    currency: str | None = None  # code (ou signe ambigu) de la valeur
    form: str | None = None  # transformation de forme appliquée
    bad: str | None = None  # sorte d'anomalie


def _decimal(text: str) -> str:
    out = format(Decimal(text), "f")
    if "." in out:
        out = out.rstrip("0").rstrip(".")
    return "0" if out in {"", "-0"} else out


def _plain_number(n: str) -> _Num:  # noqa: PLR0911
    """Nombre seul (sans unité ni devise)."""
    n = n.replace(" ", "").replace("'", "")
    if _PLAIN.match(n):
        if _AMBIGUOUS.match(n):  # « 1.234 »
            return _Num(alt=(n.replace(".", ""), n), form="separators")
        sep = "." if "." in n else None
        return _Num(value=n, sep=sep)
    if _SCIENTIFIC.match(n):
        return _Num(value=_decimal(n), form="scientific")
    if _AMBIGUOUS.match(n):  # « 1,234 »
        return _Num(alt=(n.replace(",", ""), n.replace(",", ".")), form="separators")
    if not _NUMBERISH.match(n) or n.count(",") + n.count(".") == 0:
        return _Num(bad="invalid")
    last = max(n.rfind(","), n.rfind("."))
    dec, other = n[last], ("." if n[last] == "," else ",")
    head, tail = n[:last], n[last + 1 :]
    groups = head.lstrip("+-").split(other) if other in head else [head.lstrip("+-")]
    if dec in head:  # le même signe deux fois : séparateur de milliers (« 1,234,567 »)
        parts = n.lstrip("+-").split(dec)
        if all(len(p) == 3 for p in parts[1:]) and 1 <= len(parts[0]) <= 3:
            return _Num(value=n.replace(dec, ""), form="separators")
        return _Num(bad="invalid")
    if other in head and not (1 <= len(groups[0]) <= 3 and all(len(g) == 3 for g in groups[1:])):
        return _Num(bad="invalid")
    if not tail.isdigit():
        return _Num(bad="invalid")
    value = head.replace(other, "") + "." + tail
    return _Num(value=_decimal(value), sep=dec, form="separators")


def _read_number(v: str, money: bool) -> _Num:
    """Nombre avec, éventuellement, une devise (montant) ou une unité."""
    m = _MARK.match(v)
    if money and m and (m.group("a") or m.group("b")) and not (m.group("a") and m.group("b")):
        mark = m.group("a") or m.group("b")
        code = mark if mark in ISO_4217 else CURRENCY_SIGNS.get(mark)
        num = _plain_number(m.group("n"))
        if code is None and mark not in AMBIGUOUS_SIGNS:
            return _Num(bad="invalid")
        num.currency = code or mark
        num.form = num.form or "currency_mark"
        return num
    u = _UNIT.match(v)
    if u:
        text = u.group("u").strip().rstrip(".")
        num = _plain_number(u.group("n"))
        num.unit = UNIT_ALIASES.get(text.casefold(), text.casefold())
        return num
    return _plain_number(v)


# --------------------------------------------------------------------------- #
# Lecture des dates
# --------------------------------------------------------------------------- #
@dataclass
class _Date:
    value: str | None = None
    options: tuple[str, str] | None = None  # (jour/mois, mois/jour) quand l'ordre n'est pas prouvé
    order: str | None = None  # ordre PROUVÉ par cette valeur : dmy | mdy
    form: str | None = None
    bad: str | None = None


def _iso(y: int, m: int, d: int) -> str | None:
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def _read_date(v: str, with_time: bool) -> _Date:  # noqa: PLR0911
    if _ISO_DATE.match(v):
        return _Date(value=v) if _iso(*map(int, v.split("-"))) else _Date(bad="invalid")
    t = _ISO_TIME.match(v)
    if t:
        if t.group("tz"):
            return _Date(bad="time_zone")  # jamais converti : le fuseau de la cible est inconnu
        hh, mm, ss, frac = t.group(2), t.group(3), t.group(4) or "00", t.group(5) or ""
        if frac.strip("0"):
            return _Date(bad="time_precision")
        if with_time:
            out = f"{t.group(1)} {hh}:{mm}:{ss}"
            return _Date(value=out, form=None if out == v else "date_format")
        if (hh, mm, ss) != ("00", "00", "00"):
            return _Date(bad="time_lost")  # une heure ne se perd jamais en silence
        return _Date(value=t.group(1), form="date_format")
    ymd = _YMD.match(v)
    if ymd:
        iso = _iso(int(ymd.group(1)), int(ymd.group(2)), int(ymd.group(3)))
        return _Date(value=iso, form="date_format") if iso else _Date(bad="invalid")
    num = _NUMERIC_DATE.match(v)
    if num:
        a, b, y = num.groups()
        if len(y) == 2:
            return _Date(bad="two_digit_year")  # siècle inconnu : jamais deviné
        dmy, mdy = _iso(int(y), int(b), int(a)), _iso(int(y), int(a), int(b))
        if dmy and mdy and dmy != mdy:
            return _Date(options=(dmy, mdy), form="date_format")
        if dmy or mdy:
            return _Date(value=dmy or mdy, order="dmy" if dmy else "mdy", form="date_format")
        return _Date(bad="invalid")
    for fmt in _TEXT_DATES:
        try:
            return _Date(value=datetime.strptime(v, fmt).date().isoformat(), form="date_format")  # noqa: DTZ007
        except ValueError:
            continue
    return _Date(bad="invalid")


# --------------------------------------------------------------------------- #
# Une colonne
# --------------------------------------------------------------------------- #
_FORM_LABEL = {
    "quotes": "Quotes, invisible characters or “null” removed",
    "escapes": "Escaped quotes (\\' or \\\") written as plain quotes",
    "scientific": "Scientific notation written as a decimal number",
    "separators": "Thousands / decimal separators written in standard form",
    "currency_mark": "Currency sign or code moved out of the amount",
    "unit": "Unit removed (the unit you confirmed)",
    "convert": "Converted to the unit you confirmed (exact factor)",
    "date_format": "Date written in standard form (YYYY-MM-DD)",
    # dates vraiment INTERPRÉTÉES (les seules listées) ; une date seulement remise au format standard ne
    # l'est pas
    "date_century": "Two-digit year completed with the century you chose",
    "date_read_order": "Date that can be read two ways, read day or month first as decided",
    "boolean": "Yes / no written as true / false",
    "choice": "Allowed value written with its code",
    "integer": "Whole number written without decimals",
    "list": "One value taken from each list (your choice)",
}
_ISSUE_TEXT = {
    "invalid": "{n} [value|values] cannot be read as {what} for {label}: not written.",
    "too_long": "{n} [value is|values are] longer than {label} allows: not written (never cut).",
    "structured": "{n} [value is|values are] structured data that {label} cannot take: not written.",
    "list": "{n} [value is a list|values are lists]: waiting for your decision.",
    "decimal": "{n} [value|values] like 1,234 can be read two ways: waiting for your decision.",
    "date_order": "{n} [date|dates] like 03/04/2024 can be read two ways: waiting for your decision.",
    "unit": "{n} [value has|values have] a unit: waiting for your decision.",
    "incompatible_unit": "{n} [value|values] in a unit that cannot be converted to the one you chose "
    "(another kind of unit, or an unknown unit): not written.",
    "no_unit": "{n} [value has|values have] no unit while others do: not written (the unit is never "
    "guessed).",
    "not_whole": "{n} converted [value is not a whole number|values are not whole numbers], but {label} "
    "takes whole numbers: not written (never rounded).",
    "time_zone": "{n} [date|dates] with a time zone: not written, never converted.",
    "time_lost": "{n} [date has|dates have] a time of day that {label} cannot keep: not written.",
    "time_precision": "{n} [date has|dates have] fractions of a second: not written.",
    "two_digit_year": "{n} [date has|dates have] a two-digit year: not written (the century is never "
    "guessed).",
    "currency_sign": "{n} [amount uses|amounts use] an ambiguous currency sign ($, ¥, kr): not written.",
    "left_out": "{n} [value|values] left out by your choice.",
}
_FIX = {
    "invalid": "Correct these values in your file, then upload it again.",
    "too_long": "Shorten these values in your file (they are never cut for you).",
    "structured": "Keep a single plain value in these cells.",
    "list": "Answer the question about lists above.",
    "decimal": "Answer the question about number formats above.",
    "date_order": "Answer the question about dates above.",
    "unit": "Answer the question about units above.",
    "incompatible_unit": "Write these values in the unit you chose, or leave them empty.",
    "no_unit": "Add the unit to these values in your file.",
    "not_whole": "Write whole numbers in your file, or choose another unit.",
    "time_zone": "Write these dates without a time zone.",
    "time_lost": "Write these dates without a time of day.",
    "time_precision": "Write these dates without fractions of a second.",
    "two_digit_year": "Write the year with four digits (2024, not 24).",
    "currency_sign": "Write the currency code (USD, CAD…) instead of the sign.",
    "left_out": "Nothing to do: you chose to leave these values out.",
}


def rows_of(indexes: list[int] | set[int]) -> list[int]:
    """Indices (0 = première ligne de données) → numéros de ligne affichés, triés, au plus ROWS_KEPT."""
    return sorted({i + 1 for i in indexes})[:ROWS_KEPT]


_WHAT = {
    "number": "a number",
    "integer": "a whole number",
    "date": "a date",
    "datetime": "a date and time",
    "boolean": "yes / no",
    "choice": "one of the allowed values",
}


@dataclass
class ColumnCheck:
    """Résultat d'une colonne : valeurs écrites (même rang que le fichier), anomalies, transformations,
    questions, devise de chaque ligne (montants)."""

    values: list[str]
    issues: list[ValueIssue] = field(default_factory=list)
    transforms: list[ValueTransform] = field(default_factory=list)
    questions: list[ValueQuestion] = field(default_factory=list)
    currencies: list[str | None] = field(default_factory=list)
    conversions: list[tuple[int, str, str]] = field(default_factory=list)  # (ligne, source, écrite)


class _Acc:
    def __init__(self, f: TargetField, column: str) -> None:
        self.f, self.column = f, column
        self.bad: dict[str, list[str]] = defaultdict(list)
        self.bad_rows: dict[str, set[int]] = defaultdict(set)
        self.forms: dict[str, list[list[str]]] = defaultdict(list)
        self.labels: dict[str, str] = {}
        self.conversions: list[tuple[int, str, str]] = []  # (ligne, valeur source, valeur écrite)

    def issue(self, kind: str, raw: str, row: int) -> str:
        self.bad[kind].append(raw)
        self.bad_rows[kind].add(row)
        return ""

    def form(self, kind: str | None, raw: str, out: str) -> str:
        # séparateurs de milliers / décimales : même nombre écrit en forme standard, rien d'interprété (un
        # séparateur ambigu est une QUESTION) : pas listé
        if kind and kind != "separators" and out and out != raw.strip():
            self.forms[kind].append([raw, out])
        return out

    def results(self) -> tuple[list[ValueIssue], list[ValueTransform]]:
        what = _WHAT.get(kind_of(self.f), "a value")
        issues = [
            ValueIssue(
                field=self.f.name,
                column=self.column,
                kind=kind,
                count=len(raws),
                examples=[_clip(x) for x in list(dict.fromkeys(raws))[:EXAMPLES]],
                message=say(_ISSUE_TEXT[kind], len(raws), label=self.f.label, what=what),
                rows=rows_of(self.bad_rows[kind]),
                fix=_FIX[kind],
            )
            for kind, raws in self.bad.items()
        ]
        transforms = [
            ValueTransform(
                field=self.f.name,
                column=self.column,
                kind=kind,
                label=self.labels.get(kind) or _FORM_LABEL[kind],
                count=len(pairs),
                examples=[
                    [_clip(a), _clip(b)] for a, b in list({p[0]: p for p in pairs}.values())[:EXAMPLES]
                ],
            )
            for kind, pairs in self.forms.items()
        ]
        return issues, transforms


def merge_transforms(transforms: list[ValueTransform]) -> list[ValueTransform]:
    """Une ligne par transformation (même sorte, même libellé), les colonnes nommées ensemble : l'année
    complétée de deux colonnes de dates n'est pas dite deux fois."""
    merged: dict[tuple[str, str], ValueTransform] = {}
    for t in transforms:
        key = (t.kind, t.label)
        if key not in merged:
            merged[key] = t
            continue
        m = merged[key]
        seen = {e[0] for e in m.examples}
        merged[key] = m.model_copy(
            update={
                "column": ", ".join(dict.fromkeys([*m.column.split(", "), t.column])),
                "count": m.count + t.count,
                "examples": [*m.examples, *(e for e in t.examples if e[0] not in seen)][:EXAMPLES],
            }
        )
    return list(merged.values())


def _pairs(raws: list[str], fn: Callable[[str], str]) -> list[list[str]]:
    return [[_clip(r), _clip(fn(r))] for r in list(dict.fromkeys(raws))[:EXAMPLES]]


def _question(  # noqa: PLR0913
    key: str,
    kind: str,
    f: TargetField,
    column: str,
    title: str,
    help_: str,
    count: int,
    options: list[QuestionOption],
    choices: dict[str, str],
) -> ValueQuestion:
    answer = choices.get(key)
    if answer is not None and answer not in {o.value for o in options}:
        raise ValueChoiceError(f"Unknown choice {answer!r} for {column}")
    return ValueQuestion(
        key=key,
        kind=kind,
        field=f.name,
        column=column,
        title=title,
        help=help_,
        count=count,
        options=options,
        answer=answer,
    )


def check_column(  # noqa: C901, PLR0912, PLR0915
    f: TargetField,
    column: str,
    position: int,
    values: list[str],
    choices: dict[str, str] | None = None,
    target_unit: str | None = None,
) -> ColumnCheck:
    """Valeurs écrites d'une colonne mappée sur `f`, selon les choix déjà faits (`choices` : question →
    option). `target_unit` : unité du champ dans la cible (domain pack) ; l'unité de l'en-tête de la colonne
    vaut pour les valeurs écrites sans unité."""
    choices = choices or {}
    kind = kind_of(f)
    acc = _Acc(f, column)
    out: list[str] = []
    currencies: list[str | None] = []
    questions: list[ValueQuestion] = []
    # 1. forme, listes
    cleaned: list[str] = []
    lists: list[tuple[int, str, list[str]]] = []
    for i, raw in enumerate(values):
        v, changed = unwrap(raw)
        parsed = structured(v) if v and kind != "multi" else None
        if parsed is not None:
            items = _scalars(parsed)
            if items is None:
                v = acc.issue("structured", raw, i)
            elif not items:
                v = ""
            else:
                lists.append((i, raw, items))
                v = ""
        elif changed and v:
            acc.form("quotes", raw, v)
        cleaned.append(v)
    if lists:
        key = f"list:{position}"
        sample = [r for _, r, _ in lists]
        last = lambda r: (_scalars(structured(unwrap(r)[0]) or []) or [""])[-1]  # noqa: E731
        path = lambda r: PATH_SEPARATOR.join(_scalars(structured(unwrap(r)[0]) or []) or [])  # noqa: E731
        q = _question(
            key,
            "list",
            f,
            column,
            f"Your column “{column}” contains lists",
            f"{f.label} takes one value. Which value of each list should be used?",
            len(lists),
            [
                QuestionOption(value="last", label="The last item", examples=_pairs(sample, last)),
                QuestionOption(value="path", label="The full path", examples=_pairs(sample, path)),
                QuestionOption(value="skip", label="Leave these values out"),
            ],
            choices,
        )
        questions.append(q)
        for i, raw, items in lists:
            if q.answer == "last":
                cleaned[i] = acc.form("list", raw, items[-1])
            elif q.answer == "path":
                cleaned[i] = acc.form("list", raw, PATH_SEPARATOR.join(items))
            elif q.answer == "skip":
                acc.issue("left_out", raw, i)
            else:
                acc.issue("list", raw, i)
    # 2. lecture selon la sorte du champ
    if kind in {"number", "integer"}:
        money = is_money(f)
        reads = [(_read_number(v, money) if v else None) for v in cleaned]
        seps = {r.sep for r in reads if r and r.sep}
        units = Counter(r.unit for r in reads if r and r.unit and not r.bad)
        unit_raws: dict[str, list[str]] = defaultdict(list)
        for i, r in enumerate(reads):
            if r and r.unit and not r.bad:
                unit_raws[r.unit].append(cleaned[i])
        # séparateur décimal : prouvé par la colonne, sinon question (seulement s'il y a des valeurs ambiguës)
        ambiguous = [cleaned[i] for i, r in enumerate(reads) if r and r.alt and not r.bad]
        decimal_choice: str | None = None
        if ambiguous and len(seps) == 1:
            decimal_choice = "point" if seps == {"."} else "comma"
        elif ambiguous:
            q = _question(
                f"decimal:{position}",
                "decimal",
                f,
                column,
                f"How are numbers written in “{column}”?",
                "Some numbers, like 1,234 or 1.234, can be read two ways. Which sign separates the decimals?",
                len(ambiguous),
                [
                    QuestionOption(
                        value="point",
                        label="A point: 1,234.50 (comma = thousands)",
                        examples=_pairs(ambiguous, lambda v: _read_number(v, money).alt[0]),  # type: ignore[index]
                    ),
                    QuestionOption(
                        value="comma",
                        label="A comma: 1.234,50 (point = thousands)",
                        examples=_pairs(ambiguous, lambda v: _read_number(v, money).alt[1]),  # type: ignore[index]
                    ),
                ],
                choices,
            )
            questions.append(q)
            decimal_choice = q.answer
        # unité de la colonne lue dans son en-tête : valeurs sans unité exprimées dans celle-ci
        header_unit = column_unit(column)
        if header_unit and units:
            for r in reads:
                if r and not r.bad and not r.unit and r.value is not None:
                    r.unit = header_unit
            units = Counter(r.unit for r in reads if r and r.unit and not r.bad)
            unit_raws = defaultdict(list)
            for i, r in enumerate(reads):
                if r and r.unit and not r.bad:
                    unit_raws[r.unit].append(cleaned[i])
        # unité du champ dans la cible (connue) : les unités de même nature y sont converties, sans question
        known = target_unit if target_unit in CONVERTIBLE_UNITS else header_unit
        if units and known and f"unit:{position}" not in choices:
            dims = {CONVERTIBLE_UNITS[u].dimension for u in units if u in CONVERTIBLE_UNITS}
            if dims == {CONVERTIBLE_UNITS[known].dimension}:
                choices = {**choices, f"unit:{position}": known}
        unit_choice: str | None = None
        if units:
            dims = {CONVERTIBLE_UNITS[u].dimension for u in units if u in CONVERTIBLE_UNITS}
            offered = [u for u, _ in units.most_common()] + [
                c for c, u in CONVERTIBLE_UNITS.items() if u.dimension in dims and c not in units
            ]
            samples = [unit_raws[u][0] for u, _ in units.most_common()][:EXAMPLES]

            def preview(v: str, target: str) -> str:
                r = _read_number(v, money)
                src, dst = CONVERTIBLE_UNITS.get(r.unit or ""), CONVERTIBLE_UNITS.get(target)
                if r.value is None:
                    return "?"
                if r.unit == target:
                    return r.value
                done = convert(r.value, src, dst, kind == "integer") if src and dst else None
                return done if done is not None else "not written"

            q = _question(
                f"unit:{position}",
                "unit",
                f,
                column,
                f"Values of “{column}” have units",
                f"Which unit does {f.label} use in your target system? Values in another unit of the same "
                "kind are converted with exact factors; values that cannot be converted are not written.",
                sum(units.values()),
                [
                    QuestionOption(
                        value=u,
                        label=f"{UNIT_NAMES.get(u, u)}"
                        + (f" ({units[u]} value{'s' if units[u] > 1 else ''})" if u in units else ""),
                        examples=[[_clip(v), preview(v, u)] for v in samples],
                    )
                    for u in offered
                ]
                + [QuestionOption(value="none", label="None of these: leave values with a unit out")],
                choices,
            )
            questions.append(q)
            unit_choice = q.answer
        for i, r in enumerate(reads):
            raw = values[i]
            currencies.append(r.currency if r else None)
            if r is None:
                out.append("")
                continue
            if r.bad:
                out.append(acc.issue(r.bad, raw, i))
                continue
            if r.currency in AMBIGUOUS_SIGNS:
                out.append(acc.issue("currency_sign", raw, i))
                continue
            if units and not r.unit:
                out.append(acc.issue("no_unit", raw, i))
                continue
            if r.unit and unit_choice is None:
                out.append(acc.issue("unit", raw, i))
                continue
            if r.unit and unit_choice == "none":
                out.append(acc.issue("left_out", raw, i))
                continue
            value = r.value
            form = r.form
            if r.alt:
                if decimal_choice is None:
                    out.append(acc.issue("decimal", raw, i))
                    continue
                value = r.alt[0] if decimal_choice == "point" else r.alt[1]
                value = _decimal(value)
            assert value is not None
            if r.unit and r.unit != unit_choice:
                src, dst = CONVERTIBLE_UNITS.get(r.unit), CONVERTIBLE_UNITS.get(unit_choice or "")
                if src is None or dst is None or src.dimension != dst.dimension:
                    out.append(acc.issue("incompatible_unit", raw, i))
                    continue
                done = convert(value, src, dst, kind == "integer")
                if done is None:
                    out.append(acc.issue("not_whole", raw, i))
                    continue
                key = f"convert:{src.code}"
                acc.labels[key] = (
                    f"Converted from {src.name} to {dst.name} (× {src.factor / dst.factor:.10g})"
                )
                acc.conversions.append((i, raw, done))
                out.append(acc.form(key, raw, done))
                continue
            if kind == "integer" and "." in value:
                whole, frac = value.split(".", 1)
                if frac.strip("0"):
                    out.append(acc.issue("invalid", raw, i))
                    continue
                value, form = whole, form or "integer"
            out.append(acc.form("unit" if r.unit else form, raw, value))
    elif kind in {"date", "datetime"}:
        reads_d = [(_read_date(v, kind == "datetime") if v else None) for v in cleaned]
        # année sur deux chiffres (« 5.9.26 ») : le siècle est une DÉCISION, présélectionnée seulement quand
        # toutes les autres dates de la colonne sont du même siècle ; jamais deviné en silence
        short = [i for i, r in enumerate(reads_d) if r and r.bad == "two_digit_year"]
        centuried: set[int] = set()  # lignes dont le siècle a été complété (interprétation listée)
        if short:
            centuries = {r.value[:2] for r in reads_d if r and r.value and len(r.value) >= 4}
            q = _question(
                f"century:{position}",
                "century",
                f,
                column,
                say("{n} [date|dates] in “{column}” [has|have] a two-digit year", len(short), column=column),
                "For example " + ", ".join(cleaned[i] for i in short[:3]) + ". Which century?",
                len(short),
                [
                    *(
                        QuestionOption(value=c, label=f"{c}xx, like the other dates of this column")
                        for c in sorted(centuries)
                    ),
                    # aucune autre date de la colonne ne le dit : siècle en cours proposé, jamais choisi seul
                    *(
                        [
                            QuestionOption(
                                value=f"{date.today().year // 100}", label=f"{date.today().year // 100}xx"
                            )
                        ]
                        if not centuries
                        else []
                    ),
                    QuestionOption(value="skip", label="Leave these dates empty"),
                ],
                choices,
            )
            if len(centuries) == 1:
                q = q.model_copy(update={"suggested": next(iter(centuries)), "suggested_by": "rule"})
            questions.append(q)
            if q.answer and q.answer != "skip":
                for i in short:
                    a, b, y = _NUMERIC_DATE.match(cleaned[i]).groups()  # type: ignore[union-attr]
                    read = _read_date(f"{a}.{b}.{q.answer}{y}", kind == "datetime")
                    # siècle complété : une vraie interprétation, listée (ses options d'ordre restent
                    # possibles)
                    reads_d[i] = replace(read, form="date_century") if not read.options else read
                    centuried.add(i)
        orders = {r.order for r in reads_d if r and r.order}
        both = [cleaned[i] for i, r in enumerate(reads_d) if r and r.options]
        # lecture de chaque valeur ambiguë (siècle déjà complété le cas échéant) : exemples des deux ordres
        readings = {cleaned[i]: r.options for i, r in enumerate(reads_d) if r and r.options}
        order: str | None = None
        # ordre PROUVÉ par une autre valeur de la colonne (« 13.10.2026 » : le jour d'abord) : aucune
        # interprétation à lister ; décidé par la question : listé (« as decided », la décision est visible)
        proven = False
        if both and len(orders) == 1:
            order, proven = orders.pop(), True
        elif both:
            q = _question(
                f"date_order:{position}",
                "date_order",
                f,
                column,
                f"How are dates written in “{column}”?",
                "Some dates, like 03/04/2024, can be read two ways. Which comes first?",
                len(both),
                [
                    QuestionOption(
                        value="dmy",
                        label="Day first: 03/04/2024 = 3 April",
                        examples=_pairs(both, lambda v: readings[v][0]),
                    ),
                    QuestionOption(
                        value="mdy",
                        label="Month first: 03/04/2024 = March 4",
                        examples=_pairs(both, lambda v: readings[v][1]),
                    ),
                ],
                choices,
            )
            questions.append(q)
            order = q.answer
        for i, r in enumerate(reads_d):
            raw = values[i]
            if r is None:
                out.append("")
            elif r.bad:
                out.append(acc.issue(r.bad, raw, i))
            elif r.options:
                if order is None:
                    out.append(acc.issue("date_order", raw, i))
                else:
                    value = r.options[0] if order == "dmy" else r.options[1]
                    form = "date_century" if i in centuried else None if proven else "date_read_order"
                    out.append(acc.form(form, raw, value))
            else:
                # simple mise au format standard (ISO, heure 00:00:00 retirée, 05.09.2026…) : jamais listée
                out.append(acc.form(r.form if r.form == "date_century" else None, raw, r.value or ""))
    else:
        for i, v in enumerate(cleaned):
            raw = values[i]
            if not v:
                out.append("")
            elif kind == "boolean":
                low = v.casefold()
                if low in TRUE_WORDS or low in FALSE_WORDS:
                    out.append(acc.form("boolean", raw, "true" if low in TRUE_WORDS else "false"))
                else:
                    out.append(acc.issue("invalid", raw, i))
            elif kind == "choice":
                exact = next((a.value for a in f.allowed if v == a.value), None)
                loose = next(
                    (a.value for a in f.allowed if v.casefold() in {a.value.casefold(), a.label.casefold()}),
                    None,
                )
                if exact or loose:
                    out.append(acc.form(None if exact else "choice", raw, exact or loose or ""))
                else:
                    out.append(acc.issue("invalid", raw, i))
            else:
                # guillemets échappés par l'export de la source (\' et \") : le caractère seul ; une autre
                # barre oblique inverse (« Twin\TwinXL ») est une donnée, gardée
                text = _ESCAPED_QUOTE.sub(r"\1", v)
                if f.length and len(text) > f.length:
                    out.append(acc.issue("too_long", raw, i))
                else:
                    out.append(acc.form("escapes", raw, text) if text != v else v)
    issues, transforms = acc.results()
    return ColumnCheck(
        values=out,
        issues=issues,
        transforms=transforms,
        questions=questions,
        currencies=currencies or [None] * len(values),
        conversions=acc.conversions,
    )


def currency_question(
    codes: Counter[str], header: str, choices: dict[str, str], noun: str = "rows"
) -> ValueQuestion:
    """Devise du système cible : une question pour le fichier. Les lignes dans une autre devise ne sont pas
    écrites (jamais converties)."""
    answer = choices.get("currency")
    if answer is not None and answer != WITHOUT_PRICES:
        answer = answer.strip().upper()
        if answer not in ISO_4217:
            raise ValueChoiceError(f"Unknown currency {answer!r}")
    listed = ", ".join(f"{c} ({n})" for c, n in codes.most_common())
    return ValueQuestion(
        key="currency",
        kind="currency",
        field=None,
        column=header,
        title=f"Your amounts use {', '.join(c for c, _ in codes.most_common())}",
        help=f"Rows per currency: {listed}. Which currency does your target system use? Rows in another "
        "currency keep their other values; their amounts are left empty and listed. Nothing is converted.",
        count=sum(codes.values()),
        options=[QuestionOption(value=c, label=c) for c, _ in codes.most_common() if c in ISO_4217]
        + [QuestionOption(value=WITHOUT_PRICES, label=f"Import the {noun} without their prices")],
        allow_other=True,
        suggested=next(iter(codes)) if len(codes) == 1 and next(iter(codes)) in ISO_4217 else None,
        suggested_by="rule" if len(codes) == 1 else None,
        answer=answer,
    )


# --------------------------------------------------------------------------- #
# L'IA suggère une OPTION (jamais une valeur, jamais appliquée seule)
# --------------------------------------------------------------------------- #
class ValueRuleRequest(_Model):
    request_ref: str  # opaque
    kind: str
    title: str
    field_label: str | None
    field_type: str | None
    options: list[dict[str, str]]  # {value, label}
    examples: list[str]  # MASQUÉS


class ValueRuleSuggestion(_Model):
    value: str | None  # option choisie, ou None (abstention)
    confidence: float = 0.0


@runtime_checkable
class ValueRuleProposer(Protocol):
    def wizard_value_rule(self, request: ValueRuleRequest) -> ValueRuleSuggestion: ...


AI_RULE_THRESHOLD = 0.8


def suggest_rules(
    questions: list[ValueQuestion],
    proposer: ValueRuleProposer,
    fields: dict[str, TargetField],
    examples: dict[str, list[str]],
) -> list[ValueQuestion]:
    """Suggestion de l'IA pour chaque question OUVERTE sans suggestion : seule une option offerte est gardée ;
    la question reste ouverte (l'utilisateur décide). Un échec du proposer ne change rien."""
    out = []
    for q in questions:
        if q.answer is not None or q.suggested is not None or not q.options:
            out.append(q)
            continue
        f = fields.get(q.field or "")
        request = ValueRuleRequest(
            request_ref=hashlib.sha256(q.key.encode()).hexdigest()[:16],
            kind=q.kind,
            title=q.title,
            field_label=f.label if f else None,
            field_type=f.type if f else None,
            options=[{"value": o.value, "label": o.label} for o in q.options],
            examples=[mask_example(e) for e in examples.get(q.key, [])[:EXAMPLES]],
        )
        try:
            s = proposer.wizard_value_rule(request)
        except Exception:  # noqa: BLE001 - une suggestion n'est jamais nécessaire
            out.append(q)
            continue
        offered = {o.value for o in q.options}
        if s.value in offered and s.confidence >= AI_RULE_THRESHOLD:
            out.append(q.model_copy(update={"suggested": s.value, "suggested_by": "ai"}))
        else:
            out.append(q)
    return out


__all__ = [
    "ISO_4217",
    "LEFT_OUT_BY_CHOICE",
    "ColumnCheck",
    "CurrencyColumn",
    "ExcludedRow",
    "QuestionOption",
    "ValueChoiceError",
    "ValueIssue",
    "ValueQuestion",
    "ValueRuleProposer",
    "ValueRuleRequest",
    "ValueRuleSuggestion",
    "ValueTransform",
    "check_column",
    "currency_question",
    "detect_currency",
    "is_money",
    "kind_of",
    "structured",
    "suggest_rules",
    "unwrap",
]
