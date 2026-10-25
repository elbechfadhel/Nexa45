"""Une source à schéma fixe vue comme une `CatalogSource` : les lectures existantes des domaines (conversion
vers le CDM) s'appliquent sans changement.

Les enregistrements bruts sont lus UNE fois par ressource (plafond), traduits par le binding en valeurs par
semantic key ; le « champ » d'un plan de lecture est alors la semantic key elle-même. Les filtres de période
ou de société (`where`) ne sont pas pris en charge ici : un domaine qui en a besoin le refuse avant."""

from __future__ import annotations

from typing import Any

from uie_core.catalog.plan import EntityPlan, KeySpec, PlannedField
from uie_core.catalog.reader import CatalogSource, Condition, FieldRequest, SourceRecord
from uie_core.domains.model import EntityRole
from uie_core.sources.binding import SourceBinding, SourceBindingError, SourceEntity
from uie_core.sources.contract import RecordSource

SCALAR_KINDS = ("value", "label")  # une source à schéma fixe donne des valeurs, pas des liens


def fixed_entity_plan(
    binding: SourceBinding, role: EntityRole, keys: dict[str, KeySpec]
) -> EntityPlan | None:
    """Plan de lecture d'un rôle d'après le binding : champ = semantic key. Clé inconnue du domaine, ou lien
    vers une autre entité (non pris en charge par une source à schéma fixe) → binding refusé."""
    spec = binding.entity(role.value)
    if spec is None:
        return None
    fields: dict[str, PlannedField] = {}
    for f in spec.fields:
        ks = keys.get(f.key)
        if ks is None or ks.kind not in SCALAR_KINDS:
            raise SourceBindingError(f"{binding.name}: key {f.key!r} cannot be read from {spec.resource!r}")
        fields[f.key] = PlannedField(key=f.key, field=f.key, data_type="string")
    return EntityPlan(entity=spec.resource, role=role, fields=fields)


class BoundRecordSource(CatalogSource):
    def __init__(self, src: RecordSource, binding: SourceBinding, limit: int) -> None:
        self.src = src
        self.binding = binding
        self.limit = limit
        self._cache: dict[str, tuple[list[tuple[str, bool, dict[str, Any]]], bool]] = {}

    def _spec(self, entity: str) -> SourceEntity:
        spec = next((e for e in self.binding.entities if e.resource == entity), None)
        if spec is None:
            raise SourceBindingError(f"{self.binding.name}: resource {entity!r} is not bound")
        return spec

    def _rows(
        self, entity: str, include_archived: bool
    ) -> tuple[list[tuple[str, bool, dict[str, Any]]], bool]:
        if entity not in self._cache:
            spec = self._spec(entity)
            batch = self.src.records(entity, limit=self.limit)
            rows = []
            for raw in batch.records:
                rid, archived, values = spec.evaluate(raw)
                if rid is not None:  # sans identifiant : inutilisable (relecture, idempotence)
                    rows.append((rid, archived, values))
            self._cache[entity] = (rows, batch.truncated)
        rows, truncated = self._cache[entity]
        return ([r for r in rows if include_archived or not r[1]], truncated)

    @staticmethod
    def _no_filter(where: list[Condition] | None) -> None:
        if where:
            raise SourceBindingError("Filtered reads are not supported by a fixed-schema source")

    def count(
        self, entity: str, where: list[Condition] | None = None, *, include_archived: bool = True
    ) -> int:
        self._no_filter(where)
        rows, truncated = self._rows(entity, include_archived)
        return len(rows) + (1 if truncated else 0)  # tronqué : le domaine le signale (plafond atteint)

    def read_page(
        self,
        entity: str,
        fields: list[FieldRequest],
        *,
        where: list[Condition] | None = None,
        offset: int = 0,
        limit: int = 200,
        include_archived: bool = True,
    ) -> list[SourceRecord]:
        self._no_filter(where)
        rows, _ = self._rows(entity, include_archived)
        wanted = {f.name for f in fields}
        return [
            SourceRecord(id=rid, values={k: v for k, v in values.items() if k in wanted})
            for rid, _, values in rows[offset : offset + limit]
        ]

    def read(
        self, entity: str, ids: list[str], fields: list[FieldRequest], *, include_archived: bool = True
    ) -> list[SourceRecord]:
        rows, _ = self._rows(entity, include_archived)
        wanted, keep = {f.name for f in fields}, set(ids)
        return [
            SourceRecord(id=rid, values={k: v for k, v in values.items() if k in wanted})
            for rid, _, values in rows
            if rid in keep
        ]

    def read_binary(self, entity: str, record_id: str, field: str) -> bytes | None:
        return None
