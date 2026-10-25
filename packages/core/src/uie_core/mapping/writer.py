"""Contrat d'écriture d'un fichier cible (format d'échange) : le connecteur sait ÉCRIRE le format, pas ce que
les valeurs veulent dire. Le Core lui demande la forme sérialisée d'une cellule (pour la contrôler avec le
motif du Registry) puis le fichier complet. Enregistré par un connecteur
(`@RecordFileWriterRegistry.register`), jamais importé par le Core."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from uie_core.registry import FieldDef


class RecordFileWriterError(Exception):
    pass


class ValueFormat(ABC):
    """Forme sérialisée d'une valeur dans la cible : ce que le moteur contrôle avec le Registry (motif,
    encodage). Un écrivain de fichier en est un ; une cible par API utilise `PlainValueFormat`."""

    @abstractmethod
    def format_cell(self, field: FieldDef, value: Any) -> str:
        """Forme écrite d'une valeur (None = vide), d'après le type natif du Registry."""

    def encodable(self, text: str, encoding: str) -> bool:
        try:
            text.encode(encoding)
        except UnicodeEncodeError:
            return False
        return True


class PlainValueFormat(ValueFormat):
    """Cible structurée (requête d'API) : la valeur est transmise telle quelle ; sa forme texte ne sert
    qu'aux contrôles et à l'empreinte du contenu."""

    def format_cell(self, field: FieldDef, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)


class RecordFileWriter(ValueFormat):
    system_code: ClassVar[str]
    interface_code: ClassVar[str]
    media_type: ClassVar[str] = "text/csv"
    extension: ClassVar[str] = "csv"

    @abstractmethod
    def format_cell(self, field: FieldDef, value: Any) -> str:
        """Forme écrite d'une valeur (None = cellule vide), d'après le type natif du Registry."""

    @abstractmethod
    def render(
        self,
        header_fields: list[FieldDef],
        header_values: dict[str, Any],
        fields: list[FieldDef],
        rows: list[dict[str, Any]],
        encoding: str,
    ) -> bytes:
        """Fichier complet : ligne d'en-tête (si le format en a une), ligne des colonnes, une ligne par
        enregistrement (positions absentes = cellules vides)."""


class RecordRequestBuilder:
    """Requête structurée d'un enregistrement pour une cible par API : le connecteur sait la FORME de la
    requête (objets imbriqués, listes), pas ce que les valeurs veulent dire. Par défaut, la structure vient
    des métadonnées du Registry : `json_path` (« a.b[0].c ») et `presence` (objet vide = « présent »).
    Enregistré par un connecteur (`@RecordRequestBuilderRegistry.register`), jamais importé par le Core."""

    system_code: ClassVar[str]
    interface_code: ClassVar[str]

    def build(self, fields: list[FieldDef], values: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        by_name = {f.technical_name: f for f in fields}
        for name in sorted(values):
            fd = by_name[name]
            value: Any = {} if fd.metadata.get("presence") else values[name]
            _put(out, str(fd.metadata.get("json_path") or name), value)
        return out


def _put(root: dict[str, Any], path: str, value: Any) -> None:
    """Pose `value` au chemin « a.b[0].c » (objets et listes créés au besoin)."""
    node: Any = root
    parts = path.split(".")
    for i, part in enumerate(parts):
        key, _, index = part.partition("[")
        last = i == len(parts) - 1
        if not index:
            if last:
                node[key] = value
            else:
                node = node.setdefault(key, {})
            continue
        pos = int(index.rstrip("]"))
        items = node.setdefault(key, [])
        while len(items) <= pos:
            items.append(None if last else {})
        if last:
            items[pos] = value
        else:
            node = items[pos]


class RecordRequestBuilderRegistry:
    _builders: ClassVar[dict[tuple[str, str], type[RecordRequestBuilder]]] = {}

    @classmethod
    def register(cls, builder_cls: type[RecordRequestBuilder]) -> type[RecordRequestBuilder]:
        cls._builders[(builder_cls.system_code, builder_cls.interface_code)] = builder_cls
        return builder_cls

    @classmethod
    def get(cls, system: str, interface: str) -> RecordRequestBuilder:
        try:
            return cls._builders[(system, interface)]()
        except KeyError:
            raise RecordFileWriterError(
                f"Aucun constructeur de requête pour {system} ({interface})"
            ) from None


class RecordFileWriterRegistry:
    _writers: ClassVar[dict[tuple[str, str], type[RecordFileWriter]]] = {}

    @classmethod
    def register(cls, writer_cls: type[RecordFileWriter]) -> type[RecordFileWriter]:
        cls._writers[(writer_cls.system_code, writer_cls.interface_code)] = writer_cls
        return writer_cls

    @classmethod
    def get(cls, system: str, interface: str) -> RecordFileWriter:
        try:
            return cls._writers[(system, interface)]()
        except KeyError:
            raise RecordFileWriterError(f"Aucun écrivain de fichier pour {system} ({interface})") from None
