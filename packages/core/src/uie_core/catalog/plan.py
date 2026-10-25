"""Plan de lecture du catalogue (V2a) : QUOI lire, déduit du Product Domain et des semantic bindings.

Aucun nom de système ici. Les entités sont trouvées par leur RÔLE (PRIMARY, VARIANT, CATEGORY, VARIANT_VALUE,
SUPPLIER_INFO), les champs par leur SEMANTIC KEY, les liens par des clés de relation :
    VARIANT.product_template → PRIMARY          PRIMARY.category → CATEGORY
    VARIANT.variant_identity → VARIANT_VALUE    SUPPLIER_INFO.product_template → PRIMARY
Seuls les bindings KNOWN / CONFIRMED sont lus : un champ exclu, ignoré ou encore en question ne l'est jamais.

Plusieurs champs pour une clé : choix déterministe — décision humaine, puis champ propre plutôt qu'une copie
related, puis stocké, puis clé déclarée par le pack, puis champ listé, puis nom. Sur une variante, une copie
related du produit (`<lien>.<champ>`) n'est pas lue : c'est la donnée du produit.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from uie_core.domains.model import DomainEntityDef, DomainFieldDef, EntityRole, FieldOrigin

MediaSize = Callable[[str], tuple[str, int] | None]  # champ média → (famille, taille), fourni par le pack

SCALAR_TYPES = {"string", "text", "integer", "decimal", "boolean", "date", "datetime", "enum", "json"}
Kind = Literal["value", "label", "labels", "link", "collection", "media"]


@dataclass(frozen=True)
class KeySpec:
    # value = scalaire ; label = référence lue par son libellé ; labels = collection lue par libellés (tags) ;
    # link / collection = relation vers une entité d'un rôle du domaine ; media = image
    kind: Kind
    target: EntityRole | None = None


# Clés lues par rôle d'entité (les autres restent dans l'Admin Console)
READ_KEYS: dict[EntityRole, dict[str, KeySpec]] = {
    EntityRole.PRIMARY: {
        "name": KeySpec("value"),
        "sku": KeySpec("value"),
        "ean_gtin": KeySpec("value"),
        "list_price": KeySpec("value"),
        "currency": KeySpec("label"),
        "description_sale": KeySpec("value"),
        "weight": KeySpec("value"),
        "volume": KeySpec("value"),
        "uom": KeySpec("label"),
        "product_type": KeySpec("value"),
        "active": KeySpec("value"),
        "category": KeySpec("link", EntityRole.CATEGORY),
        "main_image": KeySpec("media"),
    },
    EntityRole.VARIANT: {
        "product_template": KeySpec("link", EntityRole.PRIMARY),
        "name": KeySpec("value"),
        "sku": KeySpec("value"),
        "ean_gtin": KeySpec("value"),
        "list_price": KeySpec("value"),
        "weight": KeySpec("value"),
        "active": KeySpec("value"),
        "variant_identity": KeySpec("collection", EntityRole.VARIANT_VALUE),
        "variant_image": KeySpec("media"),
    },
    EntityRole.CATEGORY: {
        "category_path": KeySpec("value"),
        "category_name": KeySpec("value"),
    },
    EntityRole.VARIANT_VALUE: {
        "attribute": KeySpec("label"),
        "attribute_value_name": KeySpec("value"),
    },
    EntityRole.SUPPLIER_INFO: {
        "product_template": KeySpec("link", EntityRole.PRIMARY),
        "supplier_name": KeySpec("label"),
        "supplier_reference": KeySpec("value"),
        "supplier_price": KeySpec("value"),
        "currency": KeySpec("label"),
        "supplier_min_qty": KeySpec("value"),
        "supplier_lead_time": KeySpec("value"),
    },
}
EXTENSION_ORIGINS = {FieldOrigin.CUSTOM, FieldOrigin.THIRD_PARTY}


@dataclass(frozen=True)
class BoundField:
    """Binding sémantique utilisable (KNOWN / CONFIRMED) d'un champ d'instance."""

    entity: str
    field: str
    key: str
    human: bool = False  # décision du fournisseur
    pack: bool = False  # clé déclarée par le domain pack


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PlannedField(_Model):
    key: str
    field: str
    data_type: str
    target: str | None = None  # entité cible d'une relation
    selection: list[list[str]] | None = None  # valeurs d'une sélection → libellés lisibles
    label: str | None = None  # libellé métier (attributs supplémentaires)
    # obligatoire dans le système (import, V2d.1) ; hors JSON et checksum : plan de lecture V2a inchangé
    required: bool = Field(default=False, exclude=True)


class MediaPlan(_Model):
    main: str
    thumb: str
    large: str


class EntityPlan(_Model):
    entity: str
    role: EntityRole
    fields: dict[str, PlannedField]  # semantic key → champ
    media: MediaPlan | None = None
    extras: list[PlannedField] = []  # extensions confirmées (attributs supplémentaires)

    def get(self, key: str) -> PlannedField | None:
        return self.fields.get(key)

    def requested(self) -> list[PlannedField]:
        """Champs à lire (sans doublon, médias en présence seulement)."""
        seen: dict[str, PlannedField] = {}
        for f in [*self.fields.values(), *self.extras]:
            seen.setdefault(f.field, f)
        if self.media:
            seen.setdefault(
                self.media.thumb, PlannedField(key="_thumb", field=self.media.thumb, data_type="binary")
            )
        return list(seen.values())


class CatalogReadPlan(_Model):
    domain_snapshot_id: uuid.UUID | None = None
    primary: EntityPlan
    variant: EntityPlan | None = None
    category: EntityPlan | None = None
    variant_value: EntityPlan | None = None
    supplier_info: EntityPlan | None = None

    @property
    def checksum(self) -> str:
        data = self.model_dump(mode="json", exclude={"domain_snapshot_id"})
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


class CatalogPlanError(Exception):
    """Le domaine ne permet pas de lire un catalogue (ex. aucune entité produit identifiée)."""


def build_read_plan(
    entities: list[DomainEntityDef],
    fields: list[DomainFieldDef],
    bindings: list[BoundField],
    *,
    media_size: MediaSize | None = None,
    labels: dict[str, str] | None = None,
    domain_snapshot_id: uuid.UUID | None = None,
    keys: dict[EntityRole, dict[str, KeySpec]] | None = None,
) -> CatalogReadPlan:
    """`keys` : clés retenues par rôle (défaut : `READ_KEYS`, l'affichage V2a) ; l'import de fichiers (V2c.4)
    passe les siennes, avec le même choix déterministe du champ de chaque clé."""
    key_specs = READ_KEYS if keys is None else keys
    by_role: dict[EntityRole, str] = {}
    for e in entities:
        if e.present:
            by_role.setdefault(e.role, e.technical_name)
    role_of = {name: role for role, name in by_role.items()}
    defs = {(f.entity_technical_name, f.field_technical_name): f for f in fields if f.present}
    usable = [b for b in bindings if (b.entity, b.field) in defs]

    def plan_for(role: EntityRole) -> EntityPlan | None:
        entity = by_role.get(role)
        if entity is None:
            return None
        specs = key_specs.get(role, {})
        on_entity = [b for b in usable if b.entity == entity]
        chosen = choose_fields(specs, on_entity, defs, role_of)
        media = _media(
            entity, chosen.pop("main_image", None) or chosen.pop("variant_image", None), fields, media_size
        )
        extras = []
        if role == EntityRole.PRIMARY:
            taken = {p.field for p in chosen.values()}
            for b in sorted(on_entity, key=lambda b: (b.key, b.field)):
                f = defs[(b.entity, b.field)]
                if (
                    f.origin in EXTENSION_ORIGINS
                    and f.data_type in SCALAR_TYPES
                    and f.field_technical_name not in taken
                ):
                    extras.append(_planned(b.key, f, label=(labels or {}).get(b.key)))
                    taken.add(f.field_technical_name)
        return EntityPlan(entity=entity, role=role, fields=chosen, media=media, extras=extras)

    primary = plan_for(EntityRole.PRIMARY)
    if primary is None or not primary.fields:
        raise CatalogPlanError("Aucune entité produit identifiée dans le domaine analysé")
    variant = plan_for(EntityRole.VARIANT)
    if variant is not None and variant.get("product_template") is None:
        variant = None  # variantes non rattachables au produit : on ne les lit pas
    supplier_info = plan_for(EntityRole.SUPPLIER_INFO)
    if supplier_info is not None and supplier_info.get("product_template") is None:
        supplier_info = None
    category = plan_for(EntityRole.CATEGORY) if primary.get("category") else None
    variant_value = (
        plan_for(EntityRole.VARIANT_VALUE) if variant and variant.get("variant_identity") else None
    )
    return CatalogReadPlan(
        domain_snapshot_id=domain_snapshot_id,
        primary=primary,
        variant=variant,
        category=category,
        variant_value=variant_value,
        supplier_info=supplier_info,
    )


def choose_fields(
    specs: dict[str, KeySpec],
    bindings: list[BoundField],
    defs: dict[tuple[str, str], DomainFieldDef],
    role_of: dict[str, EntityRole],
) -> dict[str, PlannedField]:
    """Champ retenu pour chaque clé d'UNE entité (ses bindings), choix déterministe (voir en-tête).
    Générique : sert au catalogue et à tout autre domaine (PARTNER)."""
    chosen: dict[str, PlannedField] = {}
    link_prefix: str | None = None
    # le lien vers le parent d'abord : il sert à écarter les copies related du parent
    for key in sorted(specs, key=lambda k: specs[k].kind != "link"):
        spec = specs[key]
        candidates = [
            (b, defs[(b.entity, b.field)])
            for b in bindings
            if b.key == key and _fits(defs[(b.entity, b.field)], spec, role_of)
        ]
        if link_prefix:
            candidates = [(b, f) for b, f in candidates if not (f.related or "").startswith(link_prefix)]
        if not candidates:
            continue
        _, f = min(candidates, key=_rank)
        chosen[key] = _planned(key, f)
        if spec.kind == "link" and spec.target == EntityRole.PRIMARY:
            link_prefix = f"{f.field_technical_name}."
    return chosen


def _fits(f: DomainFieldDef, spec: KeySpec, role_of: dict[str, EntityRole]) -> bool:
    if spec.kind == "value":
        return f.data_type in SCALAR_TYPES
    if spec.kind == "label":
        return f.data_type == "reference"
    if spec.kind == "media":
        return f.data_type == "binary" or f.is_media
    if spec.kind == "labels":
        return f.data_type == "collection"
    wanted = "reference" if spec.kind == "link" else "collection"
    return f.data_type == wanted and role_of.get(f.relation_target or "") == spec.target


def _rank(item: tuple[BoundField, DomainFieldDef]) -> tuple[bool, bool, bool, bool, bool, str]:
    b, f = item
    return (not b.human, bool(f.related), not f.stored, not b.pack, not f.listed, f.field_technical_name)


def _planned(key: str, f: DomainFieldDef, label: str | None = None) -> PlannedField:
    selection = None
    if f.selection:
        selection = [
            [str(v[0]), str(v[1])] for v in f.selection if isinstance(v, list | tuple) and len(v) >= 2
        ]
    return PlannedField(
        key=key,
        field=f.field_technical_name,
        data_type=f.data_type or "unknown",
        target=f.relation_target,
        selection=selection or None,
        label=label,
        required=f.required,
    )


def _media(
    entity: str, main: PlannedField | None, fields: list[DomainFieldDef], media_size: MediaSize | None
) -> MediaPlan | None:
    """Vignette = plus petite taille de la même famille ; grand format = plus grande taille ≤ 1024."""
    if main is None:
        return None
    family = media_size(main.field) if media_size else None
    if family is None:
        return MediaPlan(main=main.field, thumb=main.field, large=main.field)
    sizes = []
    for f in fields:
        if f.entity_technical_name == entity and f.present and f.is_media:
            got = media_size(f.field_technical_name) if media_size else None
            if got and got[0] == family[0]:
                sizes.append((got[1], f.field_technical_name))
    sizes.sort()
    thumb = sizes[0][1] if sizes else main.field
    large = max((s for s in sizes if s[0] <= 1024), default=(family[1], main.field))[1]
    return MediaPlan(main=main.field, thumb=thumb, large=large)
