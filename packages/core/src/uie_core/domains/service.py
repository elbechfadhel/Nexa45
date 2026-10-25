"""ProductDomainService : définition standard, overlay instance, snapshot immutable (Product Domain V1a).

Générique : ne connaît que le contrat `DomainPack` (résolu par domaine + système + version), le Registry
et les snapshots d'instance PERSISTÉS. Ne lit jamais une instance en direct, n'appelle aucun LLM et
n'applique aucune règle sémantique (V1b / V1c). Entrypoints (CLI, API) : appellent ce service puis commitent.

Domaine effectif d'une instance :
    standard (résolu sur la baseline EXACTE du snapshot d'instance)
    + entités / champs du scope présents dans l'instance
    + extensions : champs ADDED du scope d'origine CUSTOM / THIRD_PARTY (Instance Diff), relevance UNRESOLVED
    − missing_standard : éléments présents dans la baseline, absents de l'instance
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from uie_core.db.base import utcnow
from uie_core.domains.model import (
    DomainEntityDef,
    DomainFieldDef,
    DomainKey,
    DomainMissingElement,
    EntityRole,
    FieldGroup,
    FieldOrigin,
    ProductDomainDefinition,
    ProductDomainSnapshot,
    Relevance,
)
from uie_core.domains.pack import DomainPack, DomainPackResolver
from uie_core.identity.context import Context, ensure_context, tenant_organization
from uie_core.registry import models as m
from uie_core.registry.schema import SchemaSnapshotModel
from uie_core.registry.service import NotFound, RegistryError, RegistryService, SnapshotRef

if TYPE_CHECKING:
    from uie_core.domains.enrichment import EnrichmentResult

log = logging.getLogger(__name__)

EXTENSION_ORIGINS = frozenset({FieldOrigin.CUSTOM, FieldOrigin.THIRD_PARTY})


class ProductDomainError(RegistryError):
    pass


@dataclass(frozen=True)
class StoredDefinition:
    id: uuid.UUID
    created_at: datetime
    definition: ProductDomainDefinition


@dataclass(frozen=True)
class StoredDomainSnapshot:
    id: uuid.UUID
    definition_id: uuid.UUID
    instance_schema_snapshot_sequence: int
    created: bool  # False : snapshot existant retourné (même snapshot d'instance, même définition)
    snapshot: ProductDomainSnapshot


@dataclass(frozen=True)
class AnalysisOutcome:
    stored: StoredDomainSnapshot
    enrichment: EnrichmentResult | None  # None : le domain pack ne fournit pas de règles sémantiques


@dataclass(frozen=True)
class _DiffEntry:
    change_type: str
    origin: FieldOrigin
    reason: str | None
    modules: list[str]


class ProductDomainService:
    """Moteur de domaine générique ; le domaine vient de `domain_key` (PRODUCT ici, sous-classes pour les
    autres). Définitions, domain snapshots et bindings sont filtrés par domaine."""

    domain_key = DomainKey.PRODUCT

    def __init__(self, session: Session, ctx: Context, packs: DomainPackResolver | None = None) -> None:
        self.s = session
        self.ctx = ensure_context(ctx)  # V1d : obligatoire ; filtre toute instance (et ses données dérivées)
        self.packs = packs or DomainPackResolver()
        self.registry = RegistryService(session, self.ctx)

    # ------------------------------------------------------------------ #
    # Définition standard
    # ------------------------------------------------------------------ #
    def get_standard_definition(
        self,
        system: str,
        version: str,
        snapshot_version: str | None = None,
        *,
        product: str | None = None,
        interface: str | None = None,
    ) -> StoredDefinition:
        snap = self.registry.resolve_snapshot(
            SnapshotRef(
                system, version, product=product, interface=interface, snapshot_version=snapshot_version
            )
        )
        return self.definition_for_snapshot(snap.id)

    def definition_for_snapshot(self, base_snapshot_id: uuid.UUID) -> StoredDefinition:
        """Résout le domain pack sur ce snapshot standard ; persiste la définition (et les domain_bindings)
        la première fois. Même snapshot + même definition_version → même définition (immutable)."""
        snap = self.registry.get_snapshot(base_snapshot_id)
        pack = self._pack(snap)
        model = self.registry.load_snapshot_model(snap, entities=pack.scope_entities())
        resolved = pack.resolve(model)
        semantics = pack.semantics()
        ids = self.registry.entity_ids(snap.id, [e.technical_name for e in resolved.entities if e.present])
        entities = [e.model_copy(update={"entity_id": ids.get(e.technical_name)}) for e in resolved.entities]
        ver = snap.interface.version
        draft = ProductDomainDefinition(
            domain_key=self.domain_key,
            system=ver.product.system.code,
            product=ver.product.code,
            version=ver.version,
            base_schema_snapshot_id=snap.id,
            base_schema_snapshot_checksum=snap.checksum,
            pack=pack.name,
            definition_version=pack.definition_version,
            entities=entities,
            fields=resolved.fields,
            semantic_rules_version=semantics.version if semantics else None,
            semantic_rules_checksum=semantics.checksum if semantics else None,
            checksum="",
        )
        definition = draft.model_copy(
            update={"checksum": ProductDomainDefinition.compute_checksum(draft.model_dump(mode="json"))}
        )

        row = self.s.scalar(
            select(m.ProductDomainDefinitionRecord).where(
                m.ProductDomainDefinitionRecord.domain_key == self.domain_key.value,
                m.ProductDomainDefinitionRecord.base_schema_snapshot_id == snap.id,
                m.ProductDomainDefinitionRecord.definition_version == definition.definition_version,
            )
        )
        if row is not None:
            if row.checksum != definition.checksum:
                raise ProductDomainError(
                    f"Le domain pack {pack.name} a changé sans nouvelle definition_version "
                    f"({definition.definition_version}) : une définition persistée est immutable"
                )
            return self._stored_definition(row)

        row = m.ProductDomainDefinitionRecord(
            domain_key=definition.domain_key.value,
            system=definition.system,
            product=definition.product,
            version=definition.version,
            base_schema_snapshot_id=snap.id,
            pack=definition.pack,
            definition_version=definition.definition_version,
            checksum=definition.checksum,
            entity_count=sum(1 for e in definition.entities if e.present),
            field_count=sum(1 for f in definition.fields if f.present),
            definition=definition.model_dump(mode="json"),
        )
        self.s.add(row)
        self.s.flush()
        self._bind_entities(snap.id, row, definition)
        log.info(
            "product domain definition %s %s %s checksum=%s",
            definition.system,
            definition.version,
            definition.definition_version,
            definition.checksum[:12],
        )
        return self._stored_definition(row)

    def _bind_entities(
        self,
        snapshot_id: uuid.UUID,
        row: m.ProductDomainDefinitionRecord,
        definition: ProductDomainDefinition,
    ) -> None:
        """domain_bindings : le Registry sait quelles entités participent au domaine, sans le domain pack."""
        existing = {
            (b.entity_id, b.role)
            for b in self.registry.list_domain_bindings(snapshot_id, self.domain_key.value)
        }
        for e in definition.entities:
            if e.entity_id is None or (e.entity_id, e.role.value) in existing:
                continue
            self.s.add(
                m.DomainBinding(
                    domain=self.domain_key.value,
                    snapshot_id=snapshot_id,
                    entity_id=e.entity_id,
                    role=e.role.value,
                    metadata_={
                        "definition_id": str(row.id),
                        "definition_version": definition.definition_version,
                        "pack": definition.pack,
                    },
                )
            )
        self.s.flush()

    # ------------------------------------------------------------------ #
    # Domaine effectif d'une instance
    # ------------------------------------------------------------------ #
    def build_instance_domain(
        self, instance_id: uuid.UUID, instance_schema_snapshot_id: uuid.UUID | None = None
    ) -> StoredDomainSnapshot:
        """Construit (ou retrouve) le domaine effectif du snapshot d'instance demandé (dernier par défaut)."""
        inst = self._instance(instance_id)
        isnap = self._instance_snapshot(inst.id, instance_schema_snapshot_id)
        stored_def = self.definition_for_snapshot(isnap.base_snapshot_id)  # baseline EXACTE du snapshot

        existing = self.s.scalar(
            select(m.ProductDomainSnapshotRecord).where(
                m.ProductDomainSnapshotRecord.instance_schema_snapshot_id == isnap.id,
                m.ProductDomainSnapshotRecord.definition_id == stored_def.id,
            )
        )
        if existing is not None:
            return self._stored_snapshot(existing, isnap.sequence, created=False)

        pack = self._pack(self.registry.get_snapshot(isnap.base_snapshot_id))
        snapshot = self._effective_domain(inst, isnap, stored_def.definition, pack)
        row = m.ProductDomainSnapshotRecord(
            instance_id=inst.id,
            instance_schema_snapshot_id=isnap.id,
            base_schema_snapshot_id=isnap.base_snapshot_id,
            definition_id=stored_def.id,
            domain_key=snapshot.domain_key.value,
            checksum=snapshot.checksum,
            analyzed_at=snapshot.analyzed_at,
            entity_count=sum(1 for e in snapshot.entities if e.present),
            field_count=len(snapshot.fields),
            extension_count=len(snapshot.extensions),
            missing_count=len(snapshot.missing_standard),
            snapshot=snapshot.model_dump(mode="json"),
        )
        self.s.add(row)
        self.s.flush()
        log.info(
            "product domain instance=%s snapshot=#%s fields=%s extensions=%s missing=%s",
            inst.id,
            isnap.sequence,
            row.field_count,
            row.extension_count,
            row.missing_count,
        )
        return self._stored_snapshot(row, isnap.sequence, created=True)

    def analyze(
        self, instance_id: uuid.UUID, instance_schema_snapshot_id: uuid.UUID | None = None
    ) -> AnalysisOutcome:
        """Domaine effectif (créé ou retrouvé) puis Semantic Enrichment (règles + décisions existantes).
        Seul point qui recalcule : ouvrir une page ou lire l'API ne recalcule rien."""
        from uie_core.domains.enrichment import SemanticEnrichmentService  # import circulaire sinon

        stored = self.build_instance_domain(instance_id, instance_schema_snapshot_id)
        enrichment = SemanticEnrichmentService(self.s, self.ctx, self.packs, domains=self).enrich(stored)
        return AnalysisOutcome(stored=stored, enrichment=enrichment)

    def _effective_domain(
        self,
        inst: m.Instance,
        isnap: m.InstanceSchemaSnapshot,
        standard: ProductDomainDefinition,
        pack: DomainPack,
    ) -> ProductDomainSnapshot:
        scope = set(pack.scope_entities())
        raw = isnap.schema_model
        model = SchemaSnapshotModel.model_validate(
            {**raw, "entities": [e for e in raw.get("entities", []) if e.get("technical_name") in scope]}
        )
        resolved = pack.resolve(model)  # même pack, résolu cette fois sur le schéma réel de l'instance
        diff = {
            d.object_path: _DiffEntry(
                change_type=d.change_type,
                origin=FieldOrigin(d.origin),
                reason=d.classification.get("reason"),
                modules=list(d.classification.get("modules") or []),
            )
            for d in isnap.diffs
            if d.entity in scope
        }

        std_entities = {e.technical_name: e for e in standard.entities}
        std_fields = {(f.entity_technical_name, f.field_technical_name): f for f in standard.fields}

        entities: list[DomainEntityDef] = []
        missing: list[DomainMissingElement] = []
        present_in_instance: set[str] = set()
        for e in resolved.entities:
            std = std_entities.get(e.technical_name)
            std_present = bool(std and std.present)
            if e.present:
                present_in_instance.add(e.technical_name)
            origin = FieldOrigin.STANDARD
            if e.present and not std_present:
                d = diff.get(e.technical_name)
                origin = d.origin if d and d.change_type == "ADDED" else FieldOrigin.UNKNOWN
            entities.append(
                e.model_copy(update={"origin": origin, "entity_id": std.entity_id if std else None})
            )
            if std_present and not e.present:
                d = diff.get(e.technical_name)
                missing.append(
                    DomainMissingElement(
                        object_type="entity",
                        entity_technical_name=e.technical_name,
                        role=e.role,
                        origin=d.origin if d else FieldOrigin.UNKNOWN,
                        reason=d.reason if d else None,
                    )
                )

        fields: list[DomainFieldDef] = []
        extensions: list[DomainFieldDef] = []
        seen: set[tuple[str, str]] = set()
        for f in resolved.fields:
            if not f.present:
                continue
            key = (f.entity_technical_name, f.field_technical_name)
            seen.add(key)
            std = std_fields.get(key)
            d = diff.get(f.path)
            if d is None or d.change_type != "ADDED":
                # hérite d'une entité ajoutée (ses champs ne sont pas listés un à un dans le diff)
                ent = diff.get(f.entity_technical_name)
                d = ent if ent and ent.change_type == "ADDED" else d
            if d is not None and d.change_type == "ADDED":
                origin = d.origin
            else:
                origin = FieldOrigin.STANDARD if std and std.present else FieldOrigin.UNKNOWN
            update: dict[str, Any] = {"origin": origin}
            if origin != FieldOrigin.STANDARD:
                update |= {"relevance": Relevance.UNRESOLVED, "origin_reason": d.reason if d else None}
                if d and d.modules and not f.modules:
                    update["modules"] = d.modules
            field = f.model_copy(update=update)
            fields.append(field)
            if origin in EXTENSION_ORIGINS:
                extensions.append(field)

        for key, std in std_fields.items():
            if not std.present or key in seen or key[0] not in present_in_instance:
                continue  # entité absente : signalée une seule fois, au niveau entité
            d = diff.get(std.path)
            missing.append(
                DomainMissingElement(
                    object_type="field",
                    entity_technical_name=std.entity_technical_name,
                    field_technical_name=std.field_technical_name,
                    group=std.group,
                    origin=d.origin if d else FieldOrigin.UNKNOWN,
                    reason=d.reason if d else None,
                )
            )

        draft = ProductDomainSnapshot(
            domain_key=self.domain_key,
            instance_id=inst.id,
            instance_schema_snapshot_id=isnap.id,
            instance_schema_snapshot_checksum=isnap.checksum,
            base_schema_snapshot_id=isnap.base_snapshot_id,
            standard_definition_checksum=standard.checksum,
            definition_version=standard.definition_version,
            entities=entities,
            fields=fields,
            extensions=extensions,
            missing_standard=missing,
            semantic_rules_version=standard.semantic_rules_version,
            semantic_rules_checksum=standard.semantic_rules_checksum,
            analyzed_at=utcnow(),
            checksum="",
        )
        return draft.model_copy(
            update={"checksum": ProductDomainSnapshot.compute_checksum(draft.model_dump(mode="json"))}
        )

    # ------------------------------------------------------------------ #
    # Lecture
    # ------------------------------------------------------------------ #
    def get_instance_domain(
        self, instance_id: uuid.UUID, product_domain_snapshot_id: uuid.UUID | None = None
    ) -> StoredDomainSnapshot:
        inst = self._instance(instance_id)
        q = (
            select(m.ProductDomainSnapshotRecord, m.InstanceSchemaSnapshot.sequence)
            .join(
                m.InstanceSchemaSnapshot,
                m.InstanceSchemaSnapshot.id == m.ProductDomainSnapshotRecord.instance_schema_snapshot_id,
            )
            .where(
                m.ProductDomainSnapshotRecord.instance_id == inst.id,
                m.ProductDomainSnapshotRecord.domain_key == self.domain_key.value,
            )
        )
        if product_domain_snapshot_id:
            q = q.where(m.ProductDomainSnapshotRecord.id == product_domain_snapshot_id)
        hit = self.s.execute(
            q.order_by(
                m.ProductDomainSnapshotRecord.analyzed_at.desc(), m.InstanceSchemaSnapshot.sequence.desc()
            )
        ).first()
        if hit is None:
            raise NotFound(
                f"Domain snapshot {product_domain_snapshot_id} introuvable"
                if product_domain_snapshot_id
                else "Aucun domaine produit analysé pour cette instance : lancer analyze"
            )
        return self._stored_snapshot(hit[0], hit[1], created=False)

    def list_instance_domains(self, instance_id: uuid.UUID) -> list[StoredDomainSnapshot]:
        """Historique : un domain snapshot par (snapshot d'instance, définition), le plus récent d'abord."""
        inst = self._instance(instance_id)
        rows = self.s.execute(
            select(m.ProductDomainSnapshotRecord, m.InstanceSchemaSnapshot.sequence)
            .join(
                m.InstanceSchemaSnapshot,
                m.InstanceSchemaSnapshot.id == m.ProductDomainSnapshotRecord.instance_schema_snapshot_id,
            )
            .where(
                m.ProductDomainSnapshotRecord.instance_id == inst.id,
                m.ProductDomainSnapshotRecord.domain_key == self.domain_key.value,
            )
            .order_by(
                m.ProductDomainSnapshotRecord.analyzed_at.desc(), m.InstanceSchemaSnapshot.sequence.desc()
            )
        ).all()
        return [self._stored_snapshot(r, seq, created=False) for r, seq in rows]

    def list_domain_entities(
        self,
        instance_id: uuid.UUID,
        product_domain_snapshot_id: uuid.UUID | None = None,
        *,
        role: EntityRole | None = None,
    ) -> list[DomainEntityDef]:
        snap = self.get_instance_domain(instance_id, product_domain_snapshot_id).snapshot
        return [e for e in snap.entities if role is None or e.role == role]

    def list_domain_fields(
        self,
        instance_id: uuid.UUID,
        product_domain_snapshot_id: uuid.UUID | None = None,
        *,
        group: FieldGroup | None = None,
        origin: FieldOrigin | None = None,
        relevance: Relevance | None = None,
        entity: str | None = None,
    ) -> list[DomainFieldDef]:
        snap = self.get_instance_domain(instance_id, product_domain_snapshot_id).snapshot
        return filter_fields(snap.fields, group=group, origin=origin, relevance=relevance, entity=entity)

    def list_extensions(
        self, instance_id: uuid.UUID, product_domain_snapshot_id: uuid.UUID | None = None
    ) -> list[DomainFieldDef]:
        return self.get_instance_domain(instance_id, product_domain_snapshot_id).snapshot.extensions

    def list_missing_standard(
        self, instance_id: uuid.UUID, product_domain_snapshot_id: uuid.UUID | None = None
    ) -> list[DomainMissingElement]:
        return self.get_instance_domain(instance_id, product_domain_snapshot_id).snapshot.missing_standard

    # ------------------------------------------------------------------ #
    # interne
    # ------------------------------------------------------------------ #
    def _pack(self, snap: m.SchemaSnapshot) -> DomainPack:
        ver = snap.interface.version
        return self.packs.resolve(self.domain_key, ver.product.system.code, ver.version)

    def pack_for_instance(self, inst: m.Instance) -> DomainPack:
        ver = inst.interface.version
        return self.packs.resolve(self.domain_key, ver.product.system.code, ver.version)

    def instance(self, instance_id: uuid.UUID) -> m.Instance:
        """Contrôle d'appartenance (V1d) : une instance d'un autre tenant est introuvable, exactement comme un
        id inexistant. Domaine, bindings, questions, réponses et IA passent tous par ici."""
        q = select(m.Instance).where(m.Instance.id == instance_id)
        org = tenant_organization(self.ctx)
        if org is not None:
            q = q.where(m.Instance.organization_id == org)
        inst = self.s.scalar(q)
        if inst is None:
            raise NotFound(f"Instance {instance_id} introuvable")
        return inst

    _instance = instance

    def _instance_snapshot(
        self, instance_id: uuid.UUID, snapshot_id: uuid.UUID | None
    ) -> m.InstanceSchemaSnapshot:
        self._instance(instance_id)  # V1d : contrôle d'appartenance avant toute donnée dérivée
        q = (
            select(m.InstanceSchemaSnapshot)
            .where(m.InstanceSchemaSnapshot.instance_id == instance_id)
            .options(selectinload(m.InstanceSchemaSnapshot.diffs))
        )
        if snapshot_id:
            q = q.where(m.InstanceSchemaSnapshot.id == snapshot_id)
        snap = self.s.scalar(q.order_by(m.InstanceSchemaSnapshot.sequence.desc()).limit(1))
        if snap is None:
            raise NotFound(
                f"Snapshot d'instance {snapshot_id} introuvable pour cette instance"
                if snapshot_id
                else "Aucun snapshot d'instance : lancer discover-schema"
            )
        return snap

    @staticmethod
    def _stored_definition(row: m.ProductDomainDefinitionRecord) -> StoredDefinition:
        return StoredDefinition(
            id=row.id,
            created_at=row.created_at,
            definition=ProductDomainDefinition.model_validate(row.definition),
        )

    @staticmethod
    def _stored_snapshot(
        row: m.ProductDomainSnapshotRecord, sequence: int, *, created: bool
    ) -> StoredDomainSnapshot:
        return StoredDomainSnapshot(
            id=row.id,
            definition_id=row.definition_id,
            instance_schema_snapshot_sequence=sequence,
            created=created,
            snapshot=ProductDomainSnapshot.model_validate(row.snapshot),
        )


class PartnerDomainService(ProductDomainService):
    """Domaine PARTNER (client / fournisseur) : même moteur, packs `domain-packs/partner/<système>/`."""

    domain_key = DomainKey.PARTNER


class AccountingDomainService(ProductDomainService):
    """Domaine ACCOUNTING (pièces comptables) : même moteur, packs `domain-packs/accounting/<système>/`."""

    domain_key = DomainKey.ACCOUNTING


def filter_fields(
    fields: list[DomainFieldDef],
    *,
    group: FieldGroup | None = None,
    origin: FieldOrigin | None = None,
    relevance: Relevance | None = None,
    entity: str | None = None,
) -> list[DomainFieldDef]:
    return [
        f
        for f in fields
        if (group is None or f.group == group)
        and (origin is None or f.origin == origin)
        and (relevance is None or f.relevance == relevance)
        and (entity is None or f.entity_technical_name == entity)
    ]
