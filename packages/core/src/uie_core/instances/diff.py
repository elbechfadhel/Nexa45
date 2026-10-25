"""Instance Diff : vocabulaire générique + résumé.

Répartition des rôles :
- le Core calcule les différences (`compare_snapshots`, inchangé) et le résumé (`build_summary`) ;
- le provider du système classe chaque changement (`DiffAnalysis`) car lui seul connaît les
  conventions (préfixes custom, modules, ...). Le Core n'interprète jamais un nom ou un module.

Règles génériques du résumé (identiques pour tous les systèmes) :
- custom / third-party = éléments ADDED classés CUSTOM / THIRD_PARTY ;
- modules standard absents = uniquement ceux que le provider a PROUVÉS absents ;
- éléments standard manquants = REMOVED non expliqués par un module absent ;
- risques de compatibilité = champs STANDARD modifiés (CHANGED).
"""

from __future__ import annotations

from collections import Counter
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from uie_core.registry.compare import Change, ChangeType, CompareResult


class Origin(StrEnum):
    STANDARD = "STANDARD"
    CUSTOM = "CUSTOM"
    THIRD_PARTY = "THIRD_PARTY"
    UNKNOWN = "UNKNOWN"


class _Model(BaseModel):
    # sortie : les champs à valeur par défaut sont toujours présents (requis dans le schéma OpenAPI)
    model_config = ConfigDict(extra="forbid", frozen=True, json_schema_serialization_defaults_required=True)


class ClassifiedChange(_Model):
    path: str
    origin: Origin
    modules: list[str] = []  # modules / packages d'origine selon le système (informatif)
    reason: str | None = None
    explained_by_missing: str | None = None  # REMOVED expliqué par ce module/package absent


class MissingModule(_Model):
    name: str
    evidence: str  # pourquoi l'absence est démontrée
    removed_elements: int = 0


class DiffAnalysis(_Model):
    """Produit par le provider : une classification par chemin de changement."""

    classified: dict[str, ClassifiedChange]
    missing_modules: list[MissingModule] = []
    extra_modules: list[str] = []  # présents dans l'instance, absents de la base
    notes: dict[str, Any] = {}


class DiffItem(_Model):
    path: str
    entity: str
    object_type: str
    change_type: ChangeType
    origin: Origin
    modules: list[str] = []
    reason: str | None = None
    details: dict[str, Any] = {}


class DiffCounts(_Model):
    added: int = 0
    removed: int = 0
    changed: int = 0


class InstanceDiffSummary(_Model):
    entities: DiffCounts
    fields: DiffCounts
    relations: DiffCounts
    by_origin: dict[Origin, int]

    custom_entities: list[DiffItem] = Field(default_factory=list)
    custom_fields: list[DiffItem] = Field(default_factory=list)
    third_party_entities: list[DiffItem] = Field(default_factory=list)
    third_party_fields: list[DiffItem] = Field(default_factory=list)
    third_party_modules: list[str] = Field(default_factory=list)
    missing_standard_modules: list[MissingModule] = Field(default_factory=list)
    missing_standard_elements: list[DiffItem] = Field(default_factory=list)
    compatibility_risks: list[DiffItem] = Field(default_factory=list)


def classify_items(result: CompareResult, analysis: DiffAnalysis) -> list[DiffItem]:
    """Associe à chaque changement réel sa classification (UNKNOWN si le provider n'a rien dit)."""
    items = []
    for c in result.changes:
        if c.change_type == ChangeType.UNCHANGED:
            continue
        cl = analysis.classified.get(c.path)
        items.append(_item(c, cl))
    return items


def _item(c: Change, cl: ClassifiedChange | None) -> DiffItem:
    return DiffItem(
        path=c.path,
        entity=c.entity,
        object_type=c.object_type,
        change_type=c.change_type,
        origin=cl.origin if cl else Origin.UNKNOWN,
        modules=cl.modules if cl else [],
        reason=cl.reason if cl else "non classé par le provider",
        details=c.details,
    )


def build_summary(
    result: CompareResult, analysis: DiffAnalysis
) -> tuple[InstanceDiffSummary, list[DiffItem]]:
    items = classify_items(result, analysis)
    counts: dict[str, Counter[ChangeType]] = {"entity": Counter(), "field": Counter(), "relation": Counter()}
    for it in items:
        # une entité CHANGED ne fait que regrouper ses champs/relations : on ne la compte pas deux fois
        if it.object_type == "entity" and it.change_type == ChangeType.CHANGED:
            counts["entity"][ChangeType.CHANGED] += 1
            continue
        counts[it.object_type][it.change_type] += 1

    def dc(kind: str) -> DiffCounts:
        c = counts[kind]
        return DiffCounts(
            added=c[ChangeType.ADDED], removed=c[ChangeType.REMOVED], changed=c[ChangeType.CHANGED]
        )

    explained = {p for p, cl in analysis.classified.items() if cl.explained_by_missing}
    real = [it for it in items if not (it.object_type == "entity" and it.change_type == ChangeType.CHANGED)]

    def pick(object_type: str, change: ChangeType, origin: Origin) -> list[DiffItem]:
        return [
            i for i in real if i.object_type == object_type and i.change_type == change and i.origin == origin
        ]

    summary = InstanceDiffSummary(
        entities=dc("entity"),
        fields=dc("field"),
        relations=dc("relation"),
        by_origin={o: sum(1 for i in real if i.origin == o) for o in Origin},
        custom_entities=pick("entity", ChangeType.ADDED, Origin.CUSTOM),
        custom_fields=pick("field", ChangeType.ADDED, Origin.CUSTOM),
        third_party_entities=pick("entity", ChangeType.ADDED, Origin.THIRD_PARTY),
        third_party_fields=pick("field", ChangeType.ADDED, Origin.THIRD_PARTY),
        third_party_modules=sorted(analysis.extra_modules),
        missing_standard_modules=analysis.missing_modules,
        missing_standard_elements=[
            i
            for i in real
            if i.change_type == ChangeType.REMOVED and i.object_type != "relation" and i.path not in explained
        ],
        compatibility_risks=pick("field", ChangeType.CHANGED, Origin.STANDARD),
    )
    return summary, items
