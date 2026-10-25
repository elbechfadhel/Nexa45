"""Écrivain de fichier EXTF (format DATEV) : implémente le contrat `RecordFileWriter` du Core.

Il sait ÉCRIRE le format, pas ce que les valeurs veulent dire : colonnes, types, décimales et motifs
viennent du Registry (snapshot datev/<format>/<version>/extf), les valeurs du moteur de mapping. Règles
d'écriture du format (sources officielles : description du format, fichiers d'exemple) :

    séparateur « ; », fin de ligne CRLF (y compris la dernière), CP1252 par défaut (UTF-8 avec BOM en option)
    ligne 1 = en-tête EXTF, ligne 2 = noms des colonnes (sans guillemets), puis un enregistrement par ligne
    Text   entre guillemets (guillemet doublé), y compris vide (« "" ») — sauf un champ dont le motif officiel
           n'a pas de guillemets (champs d'en-tête réservés : vides)
    Zahl / Konto / Betrag / Datum   sans guillemets, vide = rien ; décimales à la virgule ; Datum = TTMMJJJJ
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, ClassVar

from uie_core.mapping import RecordFileWriter, RecordFileWriterRegistry
from uie_core.registry import FieldDef

SEPARATOR = ";"
EOL = "\r\n"
_NUMERIC = {"zahl", "konto", "betrag"}


def _quoted(fd: FieldDef) -> bool:
    if (fd.native_type or "").casefold() != "text":
        return False
    pattern = fd.metadata.get("pattern")
    return not isinstance(pattern, str) or '"' in pattern


def _number(value: Any, decimals: int | None) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    text = str(value).strip()
    if isinstance(value, float | Decimal) or decimals:
        try:
            amount = Decimal(text.replace(",", "."))
        except InvalidOperation:
            return text  # le motif du Registry signalera la valeur
        if decimals:
            return f"{amount:.{decimals}f}".replace(".", ",")
        return format(amount.normalize(), "f").replace(".", ",")
    return text


@RecordFileWriterRegistry.register
class ExtfWriter(RecordFileWriter):
    system_code: ClassVar[str] = "datev"
    interface_code: ClassVar[str] = "extf"
    media_type: ClassVar[str] = "text/csv"
    extension: ClassVar[str] = "csv"

    def format_cell(self, field: FieldDef, value: Any) -> str:
        kind = (field.native_type or "").casefold()
        if value is None or value == "":
            return '""' if _quoted(field) else ""
        if kind == "datum":
            if isinstance(value, datetime | date):
                return value.strftime("%d%m%Y")
            return str(value)
        if kind in _NUMERIC:
            decimals = field.metadata.get("decimals")
            return _number(value, decimals if kind == "betrag" or (decimals or 0) > 0 else None)
        text = str(value)
        return f'"{text.replace(chr(34), chr(34) * 2)}"' if _quoted(field) else text

    def render(
        self,
        header_fields: list[FieldDef],
        header_values: dict[str, Any],
        fields: list[FieldDef],
        rows: list[dict[str, Any]],
        encoding: str,
    ) -> bytes:
        lines = []
        if header_fields:
            lines.append(
                SEPARATOR.join(
                    self.format_cell(f, header_values.get(f.technical_name)) for f in header_fields
                )
            )
        lines.append(SEPARATOR.join(f.label or f.technical_name for f in fields))
        for row in rows:
            lines.append(SEPARATOR.join(self.format_cell(f, row.get(f.technical_name)) for f in fields))
        return "".join(line + EOL for line in lines).encode(encoding)
