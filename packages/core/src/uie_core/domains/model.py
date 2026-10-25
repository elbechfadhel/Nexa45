"""Contrats du domaine métier (Product Domain V1a) : vocabulaire générique et modèles immutables.

Le Core connaît les clés de domaine, les rôles d'entité, les groupes de champs, la définition standard
et le snapshot effectif d'une instance. Il ne connaît aucun nom d'entité ni de champ d'un système concret :
cette connaissance est de la DONNÉE (domain packs), résolue contre un snapshot par un `DomainPack`.

    Registry            → WHAT EXISTS?
    Product Domain      → WHAT BELONGS TO THE CATALOG?
    Semantic Enrichment → WHAT DOES IT MEAN?   (V1b / V1c)
    Mapping             → WHERE DOES IT GO?    (futur)
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from uie_core.instances.diff import Origin
from uie_core.registry.schema import _canonical

# Origine d'un élément : classification d'Instance Diff (STANDARD / CUSTOM / THIRD_PARTY / UNKNOWN)
FieldOrigin = Origin


class DomainKey(StrEnum):
    PRODUCT = "PRODUCT"
    CUSTOMER = "CUSTOMER"
    SUPPLIER = "SUPPLIER"
    INVOICE = "INVOICE"
    PARTNER = "PARTNER"  # partenaire commercial (client et / ou fournisseur) : rôles dans le CDM Partner
    ACCOUNTING = "ACCOUNTING"  # pièces comptables (factures, avoirs) : Accounting CDM


class EntityRole(StrEnum):
    PRIMARY = "PRIMARY"
    VARIANT = "VARIANT"
    CATEGORY = "CATEGORY"
    ATTRIBUTE = "ATTRIBUTE"
    ATTRIBUTE_VALUE = "ATTRIBUTE_VALUE"
    ATTRIBUTE_LINE = "ATTRIBUTE_LINE"
    VARIANT_VALUE = "VARIANT_VALUE"
    UOM = "UOM"
    SUPPLIER_INFO = "SUPPLIER_INFO"
    MEDIA = "MEDIA"
    BANK_ACCOUNT = "BANK_ACCOUNT"
    BANK = "BANK"
    COUNTRY = "COUNTRY"
    DOCUMENT = "DOCUMENT"  # pièce comptable
    DOCUMENT_LINE = "DOCUMENT_LINE"  # ligne d'une pièce
    LEDGER_ACCOUNT = "LEDGER_ACCOUNT"  # compte du plan comptable de la source
    TAX = "TAX"
    CURRENCY = "CURRENCY"
    OTHER = "OTHER"


class FieldGroup(StrEnum):
    IDENTITY = "identity"
    COMMERCIAL = "commercial"
    DESCRIPTION = "description"
    CLASSIFICATION = "classification"
    LOGISTICS = "logistics"
    VARIANTS = "variants"
    SUPPLIER = "supplier"
    MEDIA = "media"
    ADDRESS = "address"
    CONTACT = "contact"
    TAX = "tax"
    BANKING = "banking"
    AMOUNTS = "amounts"
    DATES = "dates"
    LEDGER = "ledger"
    TECHNICAL = "technical"
    OTHER = "other"


class Relevance(StrEnum):
    RELEVANT = "RELEVANT"
    POSSIBLE = "POSSIBLE"
    NOT_RELEVANT = "NOT_RELEVANT"
    UNRESOLVED = "UNRESOLVED"  # à trancher par Semantic Enrichment (V1b), jamais deviné ici


class MediaRole(StrEnum):
    MAIN = "main"  # image source de plus haute résolution
    DERIVATIVE = "derivative"  # tailles générées : pas des images métier distinctes
    VARIANT = "variant"  # image propre à une variante
    ADDITIONAL = "additional"  # galerie, seulement si le schéma le prouve


# champs ajoutés après V1a : absents du calcul quand ils sont vides (checksums V1a inchangés)
_OPTIONAL = frozenset({"semantic_rules_version", "semantic_rules_checksum"})


class _Model(BaseModel):
    # sortie : champs à valeur par défaut toujours présents (requis dans le schéma OpenAPI)
    model_config = ConfigDict(extra="forbid", frozen=True, json_schema_serialization_defaults_required=True)


class DomainEntityDef(_Model):
    technical_name: str
    role: EntityRole
    present: bool
    origin: FieldOrigin = FieldOrigin.STANDARD
    display_name: str | None = None
    entity_id: uuid.UUID | None = None  # schema_entities.id du snapshot standard (None pour une instance)
    notes: str | None = None


class DomainFieldDef(_Model):
    entity_technical_name: str
    field_technical_name: str
    present: bool = True
    group: FieldGroup
    origin: FieldOrigin = FieldOrigin.STANDARD
    relevance: Relevance
    listed: bool = False  # déclaré explicitement par le domain pack
    is_media: bool = False
    media_role: MediaRole | None = None
    origin_reason: str | None = None  # raison de la classification (Instance Diff)
    # métadonnées techniques copiées du snapshot (jamais ressaisies, jamais réécrites par la sémantique)
    label: str | None = None
    help: str | None = None  # aide du système (métadonnée, jamais une valeur)
    data_type: str | None = None
    native_type: str | None = None
    required: bool = False
    readonly: bool = False
    stored: bool = True
    computed: bool = False
    technical: bool = False
    relation_target: str | None = None
    related: str | None = None  # chemin « lien.champ » d'un champ related (copie d'une autre entité)
    inverse_field: str | None = None  # one2many : champ de la cible qui porte la relation
    relation_table: str | None = None  # many2many : table de relation (partagée par les deux côtés)
    selection: list[Any] | None = None
    modules: list[str] = []

    @property
    def path(self) -> str:
        return f"{self.entity_technical_name}.{self.field_technical_name}"


class DomainMissingElement(_Model):
    """Élément du standard (présent dans la baseline) absent de l'instance."""

    object_type: Literal["entity", "field"]
    entity_technical_name: str
    field_technical_name: str | None = None
    role: EntityRole | None = None
    group: FieldGroup | None = None
    origin: FieldOrigin = FieldOrigin.STANDARD
    reason: str | None = None


class ResolvedDomain(_Model):
    """Résultat de la résolution d'un domain pack contre UN snapshot (standard ou instance)."""

    entities: list[DomainEntityDef]
    fields: list[DomainFieldDef]


class ProductDomainDefinition(_Model):
    """Définition STANDARD du domaine pour une version : résolue sur un snapshot standard exact."""

    domain_key: DomainKey
    system: str
    product: str
    version: str
    base_schema_snapshot_id: uuid.UUID
    base_schema_snapshot_checksum: str
    pack: str  # identifiant du domain pack (provenance)
    definition_version: str
    entities: list[DomainEntityDef]
    fields: list[DomainFieldDef]
    # règles sémantiques du pack (V1b) : provenance des décisions, enrichissement reproductible
    semantic_rules_version: str | None = None
    semantic_rules_checksum: str | None = None
    checksum: str

    @staticmethod
    def compute_checksum(data: dict[str, Any]) -> str:
        """Contenu seulement : ids de base de données exclus → identique d'un environnement à l'autre."""
        payload = {
            k: v
            for k, v in data.items()
            if k not in {"checksum", "base_schema_snapshot_id"} and not (k in _OPTIONAL and v is None)
        }
        payload["entities"] = [{k: v for k, v in e.items() if k != "entity_id"} for e in payload["entities"]]
        return _sha256(payload)


class ProductDomainSnapshot(_Model):
    """Domaine EFFECTIF d'une instance (IMMUTABLE), lié à des snapshots exacts.

    Standard (résolu sur la baseline) + éléments du scope présents dans l'instance + extensions
    (CUSTOM / THIRD_PARTY du scope) − éléments standard absents de l'instance.
    """

    domain_key: DomainKey
    instance_id: uuid.UUID
    instance_schema_snapshot_id: uuid.UUID
    instance_schema_snapshot_checksum: str
    base_schema_snapshot_id: uuid.UUID
    standard_definition_checksum: str
    definition_version: str
    entities: list[DomainEntityDef]
    fields: list[DomainFieldDef]
    extensions: list[DomainFieldDef]
    missing_standard: list[DomainMissingElement]
    semantic_rules_version: str | None = None
    semantic_rules_checksum: str | None = None
    analyzed_at: datetime
    checksum: str

    @staticmethod
    def compute_checksum(data: dict[str, Any]) -> str:
        excluded = {
            "checksum",
            "analyzed_at",
            "instance_id",
            "instance_schema_snapshot_id",
            "base_schema_snapshot_id",
        }
        payload = {k: v for k, v in data.items() if k not in excluded and not (k in _OPTIONAL and v is None)}
        payload["entities"] = [{k: v for k, v in e.items() if k != "entity_id"} for e in payload["entities"]]
        return _sha256(payload)


class MediaFieldRef(_Model):
    path: str
    media_role: MediaRole


class DomainSummary(_Model):
    """Chiffres clés d'une définition ou d'un domaine effectif (mêmes chiffres pour l'API et la CLI)."""

    entities_by_role: dict[str, list[str]]  # rôle → entités présentes
    absent_entities: list[str]
    field_count: int  # champs présents
    fields_by_group: dict[str, int]
    fields_by_origin: dict[str, int]
    fields_by_relevance: dict[str, int]
    media: list[MediaFieldRef]
    extension_count: int = 0
    missing_count: int = 0


def summarize_domain(
    entities: list[DomainEntityDef],
    fields: list[DomainFieldDef],
    extensions: list[DomainFieldDef] | None = None,
    missing: list[DomainMissingElement] | None = None,
) -> DomainSummary:
    present = [f for f in fields if f.present]
    by_role: dict[str, list[str]] = {}
    for e in entities:
        if e.present:
            by_role.setdefault(e.role.value, []).append(e.technical_name)

    def count(values: list[str]) -> dict[str, int]:
        out: dict[str, int] = {}
        for v in values:
            out[v] = out.get(v, 0) + 1
        return dict(sorted(out.items()))

    return DomainSummary(
        entities_by_role=by_role,
        absent_entities=[e.technical_name for e in entities if not e.present],
        field_count=len(present),
        fields_by_group=count([f.group.value for f in present]),
        fields_by_origin=count([f.origin.value for f in present]),
        fields_by_relevance=count([f.relevance.value for f in present]),
        media=[MediaFieldRef(path=f.path, media_role=f.media_role) for f in present if f.media_role],
        extension_count=len(extensions or []),
        missing_count=len(missing or []),
    )


def _sha256(payload: dict[str, Any]) -> str:
    raw = json.dumps(
        _canonical(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
