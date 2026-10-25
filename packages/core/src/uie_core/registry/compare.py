"""Comparaison de deux snapshots (Version Compare et Instance Diff).

Fonction pure sur `SchemaSnapshotModel` : sert à la fois pour Odoo 17 → 18
et pour standard → instance client. Résultat : ADDED / REMOVED / CHANGED / UNCHANGED.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from uie_core.registry.schema import EntityDef, FieldDef, RelationDef, SchemaSnapshotModel


class ChangeType(StrEnum):
    ADDED = "ADDED"
    REMOVED = "REMOVED"
    CHANGED = "CHANGED"
    UNCHANGED = "UNCHANGED"


class Change(BaseModel):
    object_type: str  # entity | field | relation
    entity: str  # entité concernée : les noms techniques contiennent des points, le path est ambigu
    path: str  # "res.partner", "res.partner.email" ou "res.partner[country_id->res.country]"
    change_type: ChangeType
    details: dict[str, Any] = Field(default_factory=dict)


class CompareResult(BaseModel):
    source: str
    target: str
    changes: list[Change] = Field(default_factory=list)
    include_unchanged: bool = False

    def counts(self) -> dict[str, int]:
        out = {c.value: 0 for c in ChangeType}
        for ch in self.changes:
            out[ch.change_type.value] += 1
        return out

    def filtered(self, *types: ChangeType) -> list[Change]:
        return [c for c in self.changes if c.change_type in types]

    def for_entity(self, entity: str) -> CompareResult:
        """Restreint le résultat à une entité (l'entité elle-même, ses champs et relations)."""
        return self.model_copy(update={"changes": [c for c in self.changes if c.entity == entity]})


# Attributs de champ dont la modification compte comme CHANGED
_FIELD_ATTRS = ("data_type", "native_type", "required", "readonly", "stored", "computed", "label")
_RELATION_ATTRS = ("relation_type", "inverse_field")
# métadonnées comparées quand un loader les fournit (formats de fichier : longueur et décimales d'une colonne)
_FIELD_METADATA = ("length", "decimals")


def _field_diff(a: FieldDef, b: FieldDef) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for attr in _FIELD_ATTRS:
        va, vb = getattr(a, attr), getattr(b, attr)
        if va != vb:
            out[attr] = {"from": va, "to": vb}
    sa, sb = a.metadata.get("selection"), b.metadata.get("selection")
    if sa != sb and (sa or sb):
        out["selection"] = {"from": sa, "to": sb}
    for key in _FIELD_METADATA:
        va, vb = a.metadata.get(key), b.metadata.get(key)
        if va != vb:
            out[key] = {"from": va, "to": vb}
    return out


def _relation_key(r: RelationDef) -> str:
    return f"{r.source_field or '*'}->{r.target_entity}"


def _relation_diff(a: RelationDef, b: RelationDef) -> dict[str, Any]:
    return {
        attr: {"from": getattr(a, attr), "to": getattr(b, attr)}
        for attr in _RELATION_ATTRS
        if getattr(a, attr) != getattr(b, attr)
    }


def _compare_entity(ea: EntityDef, eb: EntityDef, include_unchanged: bool) -> list[Change]:
    changes: list[Change] = []
    fa = {f.technical_name: f for f in ea.fields}
    fb = {f.technical_name: f for f in eb.fields}
    name = ea.technical_name
    for fname in sorted(set(fa) | set(fb)):
        path = f"{name}.{fname}"
        if fname not in fa:
            changes.append(
                Change(
                    object_type="field",
                    entity=name,
                    path=path,
                    change_type=ChangeType.ADDED,
                    details={"data_type": fb[fname].data_type, "label": fb[fname].label},
                )
            )
        elif fname not in fb:
            changes.append(
                Change(
                    object_type="field",
                    entity=name,
                    path=path,
                    change_type=ChangeType.REMOVED,
                    details={"data_type": fa[fname].data_type, "label": fa[fname].label},
                )
            )
        else:
            d = _field_diff(fa[fname], fb[fname])
            if d:
                changes.append(
                    Change(
                        object_type="field", entity=name, path=path, change_type=ChangeType.CHANGED, details=d
                    )
                )
            elif include_unchanged:
                changes.append(
                    Change(object_type="field", entity=name, path=path, change_type=ChangeType.UNCHANGED)
                )

    ra = {_relation_key(r): r for r in ea.relations}
    rb = {_relation_key(r): r for r in eb.relations}
    for key in sorted(set(ra) | set(rb)):
        path = f"{name}[{key}]"
        if key not in ra:
            changes.append(
                Change(
                    object_type="relation",
                    entity=name,
                    path=path,
                    change_type=ChangeType.ADDED,
                    details={"relation_type": rb[key].relation_type},
                )
            )
        elif key not in rb:
            changes.append(
                Change(
                    object_type="relation",
                    entity=name,
                    path=path,
                    change_type=ChangeType.REMOVED,
                    details={"relation_type": ra[key].relation_type},
                )
            )
        else:
            d = _relation_diff(ra[key], rb[key])
            if d:
                changes.append(
                    Change(
                        object_type="relation",
                        entity=name,
                        path=path,
                        change_type=ChangeType.CHANGED,
                        details=d,
                    )
                )
    return changes


def compare_snapshots(
    a: SchemaSnapshotModel,
    b: SchemaSnapshotModel,
    *,
    include_unchanged: bool = False,
    source_label: str | None = None,
    target_label: str | None = None,
) -> CompareResult:
    """Différences de a vers b (a = ancien/standard, b = nouveau/instance)."""
    result = CompareResult(
        source=source_label or f"{a.system} {a.version}",
        target=target_label or f"{b.system} {b.version}",
        include_unchanged=include_unchanged,
    )
    ea = {e.technical_name: e for e in a.entities}
    eb = {e.technical_name: e for e in b.entities}
    for name in sorted(set(ea) | set(eb)):
        if name not in ea:
            result.changes.append(
                Change(
                    object_type="entity",
                    entity=name,
                    path=name,
                    change_type=ChangeType.ADDED,
                    details={"fields": len(eb[name].fields), "module": eb[name].module},
                )
            )
        elif name not in eb:
            result.changes.append(
                Change(
                    object_type="entity",
                    entity=name,
                    path=name,
                    change_type=ChangeType.REMOVED,
                    details={"fields": len(ea[name].fields), "module": ea[name].module},
                )
            )
        else:
            sub = _compare_entity(ea[name], eb[name], include_unchanged)
            real = [c for c in sub if c.change_type != ChangeType.UNCHANGED]
            if real:
                result.changes.append(
                    Change(
                        object_type="entity",
                        entity=name,
                        path=name,
                        change_type=ChangeType.CHANGED,
                        details={"changes": len(real)},
                    )
                )
            elif include_unchanged:
                result.changes.append(
                    Change(object_type="entity", entity=name, path=name, change_type=ChangeType.UNCHANGED)
                )
            result.changes.extend(sub)
    return result
