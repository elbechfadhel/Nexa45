"""Schema Loader Lexware Office : un contrat `uie-lexware-api/1` → un snapshot du Registry.

    lexware / public-api / v1 / rest [api] @<révision du contrat>

- `version` du Registry = version de ROUTE de l'API (« v1 ») ; la révision du contrat est le
  `snapshot_version` (attribué à la publication) et sa date est dans la provenance (`source`) ;
- une ressource = une entité ; un champ = un chemin JSON (`technical_name` sans indice de liste,
  `metadata.json_path` avec) ;
- capacités PAR ENTITÉ (lire, chercher, créer, modifier, supprimer, événements) ; les opérations sans
  capacité générique restent dans `metadata.operations`.

Le loader ne lit QUE le fichier de contrat (aucun réseau, aucune clé). Sans option, le contrat livré avec le
connecteur."""

from __future__ import annotations

import hashlib
from pathlib import Path
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

from uie_connectors.lexware.spec import DEFAULT_CONTRACT, ApiField, ApiResource, LexwareApiSpec

INTERFACE_TYPE = "api"

_TYPES = {
    "string": DataType.STRING,
    "uuid": DataType.STRING,
    "integer": DataType.INTEGER,
    "number": DataType.DECIMAL,
    "boolean": DataType.BOOLEAN,
    "datetime": DataType.DATETIME,
    "enum": DataType.ENUM,
    "object": DataType.JSON,
    "list": DataType.COLLECTION,
}


@LoaderRegistry.register
class LexwareSchemaLoader(SchemaLoader):
    """Options : `spec` = chemin d'un contrat `uie-lexware-api/1` (défaut : celui du connecteur)."""

    system_code = "lexware"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self.path = Path((config or {}).get("spec") or DEFAULT_CONTRACT)
        try:
            self.spec = LexwareApiSpec.load(self.path)
        except (OSError, ValueError) as exc:
            raise SchemaLoaderError(
                f"Contrat Lexware illisible : {self.path.name} ({type(exc).__name__})"
            ) from None

    def identify_system(self) -> tuple[str, str]:
        return self.spec.system, self.spec.product

    def identify_version(self) -> tuple[str, str, str, str]:
        return self.spec.api_version, "", self.spec.interface, INTERFACE_TYPE

    def source_description(self) -> dict[str, Any]:
        return {
            "loader": "lexware-contract",
            "spec": self.spec.spec,
            "contract_revision": self.spec.contract_revision,
            "api_version": self.spec.api_version,
            "documentation": list(self.spec.sources),
            "contract_file": self.path.name,
            "contract_sha256": hashlib.sha256(self.path.read_bytes()).hexdigest(),
        }

    def extract_modules(self) -> list[ModuleDef]:
        return []

    def extract_capabilities(self) -> dict[str, bool]:
        """Capacités de l'interface : vraies dès qu'une ressource du contrat les offre."""
        out = dict.fromkeys((c.value for c in Capability), False)
        for r in self.spec.resources:
            for cap, on in _capabilities(r).items():
                out[cap.value] = out[cap.value] or on
        return out

    def extract_entities(self) -> list[EntityDef]:
        return [
            EntityDef(
                technical_name=r.name,
                display_name=r.label,
                kind="resource",
                physical_name=r.path,
                fields=[_field(f, i) for i, f in enumerate(r.fields, start=1)],
                capabilities=_capabilities(r),
                metadata={
                    "path": r.path,
                    "documentation": r.doc,
                    "operations": sorted(r.operations.extra),
                },
            )
            for r in self.spec.resources
        ]

    def build_snapshot(self):  # noqa: ANN201
        snap = super().build_snapshot()
        meta = {
            "system_name": self.spec.system_name,
            "product_name": self.spec.product_name,
            "api_version": self.spec.api_version,
            "contract_revision": self.spec.contract_revision,
        }
        return snap.model_copy(update={"metadata": {**snap.metadata, **meta}})


def _capabilities(r: ApiResource) -> dict[Capability, bool]:
    o = r.operations
    return {
        Capability.READ: o.read,
        Capability.SEARCH: o.search,
        Capability.CREATE: o.create,
        Capability.UPDATE: o.update,
        Capability.DELETE: o.delete,
        Capability.EVENTS: o.events,
    }


def _field(f: ApiField, position: int) -> FieldDef:
    # `position` : rang du champ dans le contrat (ordre d'affichage stable, celui de la documentation)
    meta: dict[str, Any] = {"json_path": f.path, "position": position}
    if f.length is not None:
        meta["length"] = f.length
    if f.decimals is not None:
        meta["decimals"] = f.decimals
    if f.enum:
        meta["enum"] = list(f.enum)
    if f.presence:
        meta["presence"] = True
    if f.protocol:
        meta["protocol"] = True
    if f.required_group:
        meta["required_group"] = f.required_group
    if f.pattern:
        meta["pattern"] = f.pattern
    return FieldDef(
        technical_name=f.technical_name,
        label=f.label,
        data_type=_TYPES[f.type],
        native_type=f.type,
        required=f.required,
        readonly=f.readonly,
        metadata=meta,
    )
