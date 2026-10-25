"""Vocabulaire des VALEURS d'unités écrites dans les fichiers (toutes langues courantes) : chaque écriture
est ramenée à un code canonique (« Stück », « Stk », « St. » → unit ; « Meter » → m). Aucun système ici : le
nom de chaque code dans la cible vient du domain pack (`DomainPack.unit_names`).

Les unités d'emballage (Set, Packung, Sack, Eimer, Dose, Rolle, Kiste…) n'ont pas d'équivalent standard :
elles ne sont jamais traduites seules, l'utilisateur décide (compter chacune comme une pièce, ou les
garder)."""

from __future__ import annotations

import re

_ALIASES: dict[str, tuple[str, ...]] = {
    "unit": (
        "unit", "units", "piece", "pieces", "pc", "pcs", "each", "ea", "stück", "stueck", "stk", "st", "stck",
        "pce", "pièce", "pièces", "unité", "unités", "u", "un",
    ),
    "dozen": ("dozen", "dozens", "dz", "doz", "dutzend", "douzaine"),
    "kg": ("kg", "kgs", "kilo", "kilos", "kilogram", "kilograms", "kilogramm", "kilogramme", "kilogrammes"),
    "g": ("g", "gr", "gram", "grams", "gramm", "gramme", "grammes"),
    "t": ("t", "ton", "tons", "tonne", "tonnes"),
    "lb": ("lb", "lbs", "pound", "pounds"),
    "oz": ("oz", "ounce", "ounces"),
    "m": ("m", "meter", "metre", "meters", "metres", "lfm", "laufmeter", "mètre", "mètres"),
    "cm": ("cm", "centimeter", "centimetre", "zentimeter"),
    "mm": ("mm", "millimeter", "millimetre"),
    "km": ("km", "kilometer", "kilometre"),
    "in": ("in", "inch", "inches"),
    "ft": ("ft", "foot", "feet"),
    "l": ("l", "liter", "litre", "liters", "litres", "ltr", "lt"),
    "ml": ("ml", "milliliter", "millilitre"),
    "m2": ("m2", "m²", "qm", "sqm", "quadratmeter"),
    "m3": ("m3", "m³", "cbm", "kubikmeter"),
    "h": ("h", "hr", "hrs", "hour", "hours", "stunde", "stunden", "std"),
    "day": ("day", "days", "tag", "tage"),
}  # fmt: skip
PACKAGE_WORDS = frozenset(
    """set sets packung packungen pack packs package packages pkg paket pakete karton kartons carton cartons
    box boxes kiste kisten sack säcke bag bags eimer bucket buckets dose dosen can cans rolle rollen roll
    rolls paar pair pairs flasche flaschen bottle bottles beutel tüte tube tuben palette pallet""".split()
)
UNIT_VALUES: dict[str, str] = {alias: code for code, names in _ALIASES.items() for alias in names}
_CLEAN = re.compile(r"[.\s]+$")


def _key(value: str) -> str:
    return _CLEAN.sub("", value.strip()).casefold()


def unit_code(value: str) -> str | None:
    """Code canonique d'une valeur d'unité (« St. » → unit) ; None : inconnue ou unité d'emballage."""
    return UNIT_VALUES.get(_key(value))


def is_package(value: str) -> bool:
    """Unité d'emballage (« Packung », « Set »…) : sans équivalent standard, décision de l'utilisateur."""
    return _key(value) in PACKAGE_WORDS
