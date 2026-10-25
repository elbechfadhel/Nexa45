"""Schema Loader DATEV (D1) : une spec `uie-datev-format/1` → un snapshot du Registry.

DATEV n'a pas d'instance à interroger : la source de vérité est la documentation officielle du format,
ramenée à
notre représentation minimale par `spec_builder`. Le loader ne lit QUE ce fichier (aucun réseau).

    datev / <format> / <version de format> / extf @<snapshot>
    entités : `header` (ligne d'en-tête EXTF) et l'entité du format (une colonne = un champ)
    champ   : technical_name = numéro de champ DATEV sur trois chiffres (« 125 ») ; label, type DATEV,
    longueur,
              décimales, obligatoire et motif dans les attributs / métadonnées
"""

from __future__ import annotations

import re
from typing import Any

from uie_core.registry import (
    Capability,
    DataType,
    EntityDef,
    FieldDef,
    LoaderRegistry,
    ModuleDef,
    SchemaLoader,
    SchemaLoaderError,
)

from uie_connectors.datev.spec import DatevField, DatevFormatSpec

INTERFACE = "extf"
SYSTEM_NAME = "DATEV"


def data_type_of(f: DatevField) -> DataType:
    """Type DATEV → type logique du Registry (le type DATEV reste dans `native_type`)."""
    kind = f.datev_type.casefold()
    if kind == "betrag":
        return DataType.DECIMAL
    if kind == "zahl":
        return DataType.DECIMAL if f.decimals else DataType.INTEGER
    if kind == "datum":
        return DataType.DATE
    if kind in ("text", "konto"):  # un numéro de compte est un identifiant, pas une quantité
        return DataType.STRING
    return DataType.UNKNOWN


def header_type_of(pattern: str | None) -> str:
    """Type d'un champ d'en-tête, déduit de son motif officiel (le portail ne donne pas de type)."""
    if not pattern:
        return "Text"
    if '["]' in pattern or '"' in pattern:
        return "Text"
    if re.search(r"\\,|\[,\]", pattern):
        return "Betrag"
    return "Zahl" if re.search(r"\\d|\[0-9\]|\d", pattern) else "Text"


@LoaderRegistry.register
class DatevSchemaLoader(SchemaLoader):
    """Options : `spec` = chemin d'un fichier `uie-datev-format/1`."""

    system_code = "datev"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        path = (config or {}).get("spec")
        if not path:
            raise SchemaLoaderError("Loader DATEV : option « spec » (fichier uie-datev-format/1) requise")
        try:
            self.spec = DatevFormatSpec.load(path)
        except (OSError, ValueError) as exc:
            raise SchemaLoaderError(f"Spec DATEV illisible : {path} ({type(exc).__name__})") from None
        self._check(self.spec)

    @staticmethod
    def _check(spec: DatevFormatSpec) -> None:
        for name, fields in (("header", spec.header), (spec.entity, spec.fields)):
            positions = [f.position for f in fields]
            if positions != list(range(1, len(fields) + 1)):
                raise SchemaLoaderError(f"Spec DATEV {spec.file_name} : positions non contiguës dans {name}")

    def identify_system(self) -> tuple[str, str]:
        return "datev", self.spec.format

    def identify_version(self) -> tuple[str, str, str, str]:
        return self.spec.format_version, "", INTERFACE, "file"

    def source_description(self) -> dict[str, Any]:
        return {"loader": "datev-spec", "spec": self.spec.spec, **self.spec.source}

    def extract_modules(self) -> list[ModuleDef]:
        return []

    def extract_capabilities(self) -> dict[str, bool]:
        # un fichier EXTF s'importe dans DATEV par lot ; aucune lecture ni écriture unitaire
        return {c.value: c == Capability.BULK_WRITE for c in Capability}

    def extract_entities(self) -> list[EntityDef]:
        return [
            EntityDef(
                technical_name="header",
                display_name=f"EXTF header {self.spec.header_version}",
                kind="record",
                fields=[self._field(f, self.spec.pattern_syntax) for f in self.spec.header],
                metadata={"header_version": self.spec.header_version},
            ),
            EntityDef(
                technical_name=self.spec.entity,
                display_name=self.spec.format_name,
                kind="record",
                fields=[self._field(f, self.spec.pattern_syntax) for f in self.spec.fields],
                metadata={
                    "data_category": self.spec.data_category,
                    "format_version": self.spec.format_version,
                },
            ),
        ]

    def build_snapshot(self):  # noqa: ANN201
        snap = super().build_snapshot()
        meta = {
            "system_name": SYSTEM_NAME,
            "product_name": self.spec.format_name,
            "data_category": self.spec.data_category,
            "header_version": self.spec.header_version,
        }
        return snap.model_copy(update={"metadata": {**snap.metadata, **meta}})

    @staticmethod
    def _field(f: DatevField, pattern_syntax: str) -> FieldDef:
        meta: dict[str, Any] = {"position": f.position}
        for key in ("length", "decimals", "pattern", "importable", "exportable", "source_field_id"):
            value = getattr(f, key)
            if value is not None:
                meta[key] = value
        if f.pattern:
            meta["pattern_syntax"] = pattern_syntax  # le moteur évalue le motif dans ce dialecte
        if f.aliases:
            meta["aliases"] = list(f.aliases)
        return FieldDef(
            technical_name=f"{f.position:03d}",
            label=f.label,
            data_type=data_type_of(f),
            native_type=f.datev_type,
            required=f.required,
            metadata=meta,
        )
