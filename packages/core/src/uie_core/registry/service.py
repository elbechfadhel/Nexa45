"""RegistryService : API métier du System Knowledge Registry, sans FastAPI.

Responsabilités :
- garantir la hiérarchie System → Product → Version → Interface (get-or-create) ;
- publier un `SchemaSnapshotModel` comme snapshot IMMUTABLE (checksum, source) ;
- relire un snapshot en `SchemaSnapshotModel` (pour compare / mapping) ;
- naviguer (browse) et comparer des versions.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from uie_core.identity.context import Context, ensure_context, require_platform
from uie_core.registry import models as m
from uie_core.registry.compare import CompareResult, compare_snapshots
from uie_core.registry.schema import (
    Capability,
    ConstraintDef,
    EntityDef,
    FieldDef,
    ModuleDef,
    PhysicalColumnDef,
    PhysicalTableDef,
    RelationDef,
    SchemaSnapshotModel,
)


class RegistryError(Exception):
    pass


class NotFound(RegistryError):
    pass


class SnapshotAlreadyExists(RegistryError):
    pass


@dataclass(frozen=True)
class SnapshotRef:
    """Adresse lisible d'un snapshot : odoo/odoo/17/orm@1.0.0"""

    system: str
    version: str
    product: str | None = None
    interface: str | None = None
    snapshot_version: str | None = None  # None = dernier publié

    def __str__(self) -> str:
        parts = [self.system, self.product or "*", self.version, self.interface or "*"]
        return "/".join(parts) + (f"@{self.snapshot_version}" if self.snapshot_version else "")


KNOWN_STATUS = "known"  # système identifié, sans schéma publié (aucun produit, version ni snapshot)


class RegistryService:
    """Connaissance plateforme : lisible dans tout contexte, écrite seulement en Admin / System (V1d)."""

    def __init__(self, session: Session, ctx: Context) -> None:
        self.s = session
        self.ctx = ensure_context(ctx)

    # ------------------------------------------------------------------ #
    # Hiérarchie standard
    # ------------------------------------------------------------------ #
    def ensure_system(self, code: str, name: str | None = None, **kw: Any) -> m.System:
        require_platform(self.ctx, "écrire le Registry standard")
        sys_ = self.s.scalar(select(m.System).where(m.System.code == code))
        if sys_ is None:
            sys_ = m.System(code=code, name=name or code.title(), **kw)
            self.s.add(sys_)
            self.s.flush()
        return sys_

    def register_known_system(
        self,
        code: str,
        name: str,
        *,
        category: str,
        vendor: str | None = None,
        description: str | None = None,
    ) -> m.System:
        """Système CONNU (identifié) sans schéma : une ligne `systems` de statut `known`, sans produit ni
        version ni snapshot. Idempotent ; un système qui a déjà un schéma publié n'est jamais modifié ni
        rétrogradé."""
        require_platform(self.ctx, "écrire le Registry standard")
        sys_ = self.s.scalar(select(m.System).where(m.System.code == code))
        if sys_ is not None and sys_.status != KNOWN_STATUS:
            return sys_
        meta = {"description": description} if description else {}
        if sys_ is None:
            sys_ = m.System(
                code=code, name=name, vendor=vendor, category=category, status=KNOWN_STATUS, metadata_=meta
            )
            self.s.add(sys_)
        else:
            sys_.name, sys_.vendor, sys_.category, sys_.metadata_ = name, vendor, category, meta
        self.s.flush()
        return sys_

    def ensure_product(self, system: m.System, code: str, name: str | None = None) -> m.SystemProduct:
        prod = self.s.scalar(
            select(m.SystemProduct).where(
                m.SystemProduct.system_id == system.id, m.SystemProduct.code == code
            )
        )
        if prod is None:
            prod = m.SystemProduct(system=system, code=code, name=name or code)
            self.s.add(prod)
            self.s.flush()
        return prod

    def ensure_version(self, product: m.SystemProduct, version: str, edition: str = "") -> m.SystemVersion:
        ver = self.s.scalar(
            select(m.SystemVersion).where(
                m.SystemVersion.product_id == product.id,
                m.SystemVersion.version == version,
                m.SystemVersion.edition == edition,
            )
        )
        if ver is None:
            ver = m.SystemVersion(product=product, version=version, edition=edition)
            self.s.add(ver)
            self.s.flush()
        return ver

    def ensure_interface(
        self, version: m.SystemVersion, code: str, type_: str, direction: str = "bidirectional"
    ) -> m.SystemInterface:
        itf = self.s.scalar(
            select(m.SystemInterface).where(
                m.SystemInterface.version_id == version.id, m.SystemInterface.code == code
            )
        )
        if itf is None:
            itf = m.SystemInterface(version=version, code=code, type=type_, direction=direction)
            self.s.add(itf)
            self.s.flush()
        return itf

    # ------------------------------------------------------------------ #
    # Publication d'un snapshot (immutable)
    # ------------------------------------------------------------------ #
    def publish_snapshot(
        self,
        snapshot: SchemaSnapshotModel,
        snapshot_version: str | None = None,
        *,
        skip_if_unchanged: bool = True,
    ) -> m.SchemaSnapshot:
        """Persiste le snapshot. Jamais de mise à jour d'un snapshot existant.

        - `snapshot_version` absent → auto-incrément (1.0.0, 1.0.1, ...).
        - `skip_if_unchanged` → si le dernier snapshot a le même checksum, on le retourne
          sans en créer un nouveau.
        """
        require_platform(self.ctx, "publier un snapshot standard")
        # noms d'affichage fournis par le loader (utilisés seulement à la création du système / produit)
        system = self.ensure_system(snapshot.system, snapshot.metadata.get("system_name"))
        if system.status == KNOWN_STATUS:
            system.status = "supported"  # un système connu qui reçoit son premier schéma publié
        product = self.ensure_product(system, snapshot.product, snapshot.metadata.get("product_name"))
        version = self.ensure_version(product, snapshot.version, snapshot.edition)
        interface = self.ensure_interface(version, snapshot.interface, snapshot.interface_type)

        checksum = snapshot.checksum()
        latest = self._latest_snapshot(interface.id)
        if skip_if_unchanged and latest is not None and latest.checksum == checksum:
            return latest

        if snapshot_version is None:
            snapshot_version = self._next_version(latest)
        exists = self.s.scalar(
            select(m.SchemaSnapshot).where(
                m.SchemaSnapshot.interface_id == interface.id,
                m.SchemaSnapshot.snapshot_version == snapshot_version,
            )
        )
        if exists is not None:
            raise SnapshotAlreadyExists(
                f"Snapshot {snapshot_version} existe déjà pour {interface.code} "
                f"(immutable : publier une nouvelle version)"
            )

        row = m.SchemaSnapshot(
            interface=interface,
            snapshot_version=snapshot_version,
            source=snapshot.source,
            checksum=checksum,
            entity_count=len(snapshot.entities),
            field_count=snapshot.field_count,
            metadata_=snapshot.metadata,
        )
        self.s.add(row)
        self.s.flush()

        modules: dict[str, m.SchemaModule] = {}
        for mod in snapshot.modules:
            row_mod = m.SchemaModule(
                snapshot_id=row.id,
                technical_name=mod.technical_name,
                display_name=mod.display_name,
                metadata_=mod.metadata,
            )
            self.s.add(row_mod)
            modules[mod.technical_name] = row_mod
        self.s.flush()

        entities: dict[str, m.SchemaEntity] = {}
        fields: dict[tuple[str, str], m.SchemaField] = {}
        for ent in snapshot.entities:
            mod_row = modules.get(ent.module) if ent.module else None
            if ent.module and mod_row is None:  # module non déclaré → créé à la volée
                mod_row = m.SchemaModule(snapshot_id=row.id, technical_name=ent.module)
                self.s.add(mod_row)
                self.s.flush()
                modules[ent.module] = mod_row
            row_ent = m.SchemaEntity(
                snapshot_id=row.id,
                module_id=mod_row.id if mod_row else None,
                technical_name=ent.technical_name,
                display_name=ent.display_name,
                kind=ent.kind,
                physical_name=ent.physical_name,
                metadata_=ent.metadata,
            )
            self.s.add(row_ent)
            entities[ent.technical_name] = row_ent
        self.s.flush()

        for ent in snapshot.entities:
            row_ent = entities[ent.technical_name]
            for f in ent.fields:
                row_f = m.SchemaField(
                    entity_id=row_ent.id,
                    technical_name=f.technical_name,
                    label=f.label,
                    data_type=f.data_type.value,
                    native_type=f.native_type,
                    required=f.required,
                    readonly=f.readonly,
                    stored=f.stored,
                    computed=f.computed,
                    physical_column=f.physical_column,
                    help=f.help,
                    metadata_=f.metadata,
                )
                self.s.add(row_f)
                fields[(ent.technical_name, f.technical_name)] = row_f
            for cap, supported in ent.capabilities.items():
                self.s.add(
                    m.SchemaCapability(
                        snapshot_id=row.id,
                        entity_id=row_ent.id,
                        scope="entity",
                        capability=cap.value,
                        supported=supported,
                    )
                )
        self.s.flush()

        for ent in snapshot.entities:
            row_ent = entities[ent.technical_name]
            for r in ent.relations:
                target = entities.get(r.target_entity)
                src_field = fields.get((ent.technical_name, r.source_field)) if r.source_field else None
                self.s.add(
                    m.SchemaRelation(
                        snapshot_id=row.id,
                        source_entity_id=row_ent.id,
                        source_field_id=src_field.id if src_field else None,
                        target_entity_id=target.id if target else None,
                        target_entity_name=r.target_entity,
                        relation_type=r.relation_type.value,
                        inverse_field=r.inverse_field,
                        metadata_=r.metadata,
                    )
                )
            for c in ent.constraints:
                fld = fields.get((ent.technical_name, c.field)) if c.field else None
                self.s.add(
                    m.SchemaConstraint(
                        snapshot_id=row.id,
                        entity_id=row_ent.id,
                        field_id=fld.id if fld else None,
                        type=c.type,
                        definition=c.definition,
                    )
                )
        for cap, supported in snapshot.capabilities.items():
            self.s.add(
                m.SchemaCapability(
                    snapshot_id=row.id, scope="interface", capability=cap.value, supported=supported
                )
            )
        for pt in snapshot.physical_tables:
            ent_row = entities.get(pt.entity) if pt.entity else None
            row_t = m.PhysicalTable(
                snapshot_id=row.id,
                entity_id=ent_row.id if ent_row else None,
                schema_name=pt.schema_name,
                table_name=pt.table_name,
            )
            row_t.columns = [
                m.PhysicalColumn(column_name=c.column_name, db_type=c.db_type, nullable=c.nullable)
                for c in pt.columns
            ]
            self.s.add(row_t)
        self.s.flush()
        return row

    def _latest_snapshot(self, interface_id: uuid.UUID) -> m.SchemaSnapshot | None:
        return self.s.scalar(
            select(m.SchemaSnapshot)
            .where(m.SchemaSnapshot.interface_id == interface_id)
            .order_by(m.SchemaSnapshot.created_at.desc())
            .limit(1)
        )

    @staticmethod
    def _next_version(latest: m.SchemaSnapshot | None) -> str:
        if latest is None:
            return "1.0.0"
        try:
            major, minor, patch = (int(x) for x in latest.snapshot_version.split("."))
            return f"{major}.{minor}.{patch + 1}"
        except ValueError:
            return f"{latest.snapshot_version}.1"

    # ------------------------------------------------------------------ #
    # Lecture
    # ------------------------------------------------------------------ #
    def list_systems(self) -> list[m.System]:
        return list(
            self.s.scalars(
                select(m.System)
                .options(
                    selectinload(m.System.products)
                    .selectinload(m.SystemProduct.versions)
                    .selectinload(m.SystemVersion.interfaces)
                    .selectinload(m.SystemInterface.snapshots)
                )
                .order_by(m.System.code)
            )
        )

    def resolve_snapshot(self, ref: SnapshotRef) -> m.SchemaSnapshot:
        q = (
            select(m.SchemaSnapshot)
            .join(m.SystemInterface)
            .join(m.SystemVersion)
            .join(m.SystemProduct)
            .join(m.System)
            .where(m.System.code == ref.system, m.SystemVersion.version == ref.version)
        )
        if ref.product:
            q = q.where(m.SystemProduct.code == ref.product)
        if ref.interface:
            q = q.where(m.SystemInterface.code == ref.interface)
        if ref.snapshot_version:
            q = q.where(m.SchemaSnapshot.snapshot_version == ref.snapshot_version)
        q = q.order_by(m.SchemaSnapshot.created_at.desc()).limit(1)
        snap = self.s.scalar(q)
        if snap is None:
            raise NotFound(f"Aucun snapshot pour {ref}")
        return snap

    def get_snapshot(self, snapshot_id: uuid.UUID) -> m.SchemaSnapshot:
        snap = self.s.scalar(
            select(m.SchemaSnapshot)
            .where(m.SchemaSnapshot.id == snapshot_id)
            .options(
                selectinload(m.SchemaSnapshot.interface)
                .selectinload(m.SystemInterface.version)
                .selectinload(m.SystemVersion.product)
                .selectinload(m.SystemProduct.system)
            )
        )
        if snap is None:
            raise NotFound(f"Snapshot {snapshot_id} introuvable")
        return snap

    def list_entities(self, snapshot_id: uuid.UUID, search: str | None = None) -> list[m.SchemaEntity]:
        return self.page_entities(snapshot_id, search)[0]

    def page_entities(
        self,
        snapshot_id: uuid.UUID,
        search: str | None = None,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[list[m.SchemaEntity], int]:
        """Entités triées par nom technique, paginées → (page, total filtré)."""
        cond = [m.SchemaEntity.snapshot_id == snapshot_id]
        if search:
            cond.append(m.SchemaEntity.technical_name.ilike(f"%{search}%"))
        total = self.s.scalar(select(func.count()).select_from(m.SchemaEntity).where(*cond)) or 0
        q = (
            select(m.SchemaEntity)
            .where(*cond)
            .options(selectinload(m.SchemaEntity.module))
            .order_by(m.SchemaEntity.technical_name)
            .offset(offset)
            .limit(limit)
        )
        return list(self.s.scalars(q)), total

    def get_entity(self, snapshot_id: uuid.UUID, technical_name: str) -> m.SchemaEntity:
        ent = self.s.scalar(
            select(m.SchemaEntity)
            .where(
                m.SchemaEntity.snapshot_id == snapshot_id,
                m.SchemaEntity.technical_name == technical_name,
            )
            .options(selectinload(m.SchemaEntity.fields), selectinload(m.SchemaEntity.module))
        )
        if ent is None:
            raise NotFound(f"Entité '{technical_name}' absente du snapshot")
        return ent

    def get_entity_by_id(self, entity_id: uuid.UUID) -> m.SchemaEntity:
        ent = self.s.scalar(
            select(m.SchemaEntity)
            .where(m.SchemaEntity.id == entity_id)
            .options(selectinload(m.SchemaEntity.fields), selectinload(m.SchemaEntity.module))
        )
        if ent is None:
            raise NotFound(f"Entité {entity_id} introuvable")
        return ent

    def list_relations(self, entity_id: uuid.UUID) -> list[m.SchemaRelation]:
        return list(
            self.s.scalars(
                select(m.SchemaRelation)
                .where(m.SchemaRelation.source_entity_id == entity_id)
                .options(selectinload(m.SchemaRelation.source_field))
                .order_by(m.SchemaRelation.target_entity_name)
            )
        )

    def field_counts(self, snapshot_id: uuid.UUID) -> dict[uuid.UUID, int]:
        """Nombre de champs par entité d'un snapshot (une requête)."""
        q = (
            select(m.SchemaField.entity_id, func.count())
            .join(m.SchemaEntity, m.SchemaEntity.id == m.SchemaField.entity_id)
            .where(m.SchemaEntity.snapshot_id == snapshot_id)
            .group_by(m.SchemaField.entity_id)
        )
        return {row[0]: row[1] for row in self.s.execute(q)}

    def search_fields(
        self, snapshot_id: uuid.UUID, pattern: str, limit: int = 200
    ) -> list[tuple[str, m.SchemaField]]:
        q = (
            select(m.SchemaEntity.technical_name, m.SchemaField)
            .join(m.SchemaField, m.SchemaField.entity_id == m.SchemaEntity.id)
            .where(
                m.SchemaEntity.snapshot_id == snapshot_id,
                m.SchemaField.technical_name.ilike(f"%{pattern}%"),
            )
            .order_by(m.SchemaEntity.technical_name, m.SchemaField.technical_name)
            .limit(limit)
        )
        return [(row[0], row[1]) for row in self.s.execute(q)]

    def stats(self, snapshot_id: uuid.UUID) -> dict[str, int]:
        ents = self.s.scalar(
            select(func.count()).select_from(m.SchemaEntity).where(m.SchemaEntity.snapshot_id == snapshot_id)
        )
        flds = self.s.scalar(
            select(func.count())
            .select_from(m.SchemaField)
            .join(m.SchemaEntity, m.SchemaEntity.id == m.SchemaField.entity_id)
            .where(m.SchemaEntity.snapshot_id == snapshot_id)
        )
        rels = self.s.scalar(
            select(func.count())
            .select_from(m.SchemaRelation)
            .where(m.SchemaRelation.snapshot_id == snapshot_id)
        )
        return {"entities": ents or 0, "fields": flds or 0, "relations": rels or 0}

    # ------------------------------------------------------------------ #
    # Reconstruction en modèle Pydantic (pour compare, mapping, export)
    # ------------------------------------------------------------------ #
    def load_snapshot_model(
        self, snap: m.SchemaSnapshot, entities: Collection[str] | None = None
    ) -> SchemaSnapshotModel:
        """Snapshot complet, ou réduit aux `entities` demandées (ex. le scope d'un domaine métier)."""
        itf = snap.interface
        ver = itf.version
        prod = ver.product
        sys_ = prod.system

        ent_q = select(m.SchemaEntity).where(m.SchemaEntity.snapshot_id == snap.id)
        if entities is not None:
            ent_q = ent_q.where(m.SchemaEntity.technical_name.in_(list(entities)))
        ents = list(
            self.s.scalars(
                ent_q.options(
                    selectinload(m.SchemaEntity.fields), selectinload(m.SchemaEntity.module)
                ).order_by(m.SchemaEntity.technical_name)
            )
        )
        ent_ids = [e.id for e in ents]

        def scoped(model: Any, column: Any) -> list[Any]:
            q = select(model).where(model.snapshot_id == snap.id)
            if entities is not None:
                q = q.where(column.in_(ent_ids))
            return list(self.s.scalars(q))

        rels = scoped(m.SchemaRelation, m.SchemaRelation.source_entity_id)
        cons = scoped(m.SchemaConstraint, m.SchemaConstraint.entity_id)
        caps = list(
            self.s.scalars(select(m.SchemaCapability).where(m.SchemaCapability.snapshot_id == snap.id))
        )
        if entities is not None:
            caps = [c for c in caps if c.entity_id is None or c.entity_id in set(ent_ids)]
        mods = list(self.s.scalars(select(m.SchemaModule).where(m.SchemaModule.snapshot_id == snap.id)))
        tables_q = (
            select(m.PhysicalTable)
            .where(m.PhysicalTable.snapshot_id == snap.id)
            .options(selectinload(m.PhysicalTable.columns))
        )
        if entities is not None:
            tables_q = tables_q.where(m.PhysicalTable.entity_id.in_(ent_ids))
        tables = list(self.s.scalars(tables_q))

        field_name_by_id = {f.id: f.technical_name for e in ents for f in e.fields}
        ent_name_by_id = {e.id: e.technical_name for e in ents}
        rels_by_ent: dict[uuid.UUID, list[RelationDef]] = {}
        for r in rels:
            rels_by_ent.setdefault(r.source_entity_id, []).append(
                RelationDef(
                    source_field=field_name_by_id.get(r.source_field_id) if r.source_field_id else None,
                    target_entity=r.target_entity_name,
                    relation_type=r.relation_type,  # type: ignore[arg-type]
                    inverse_field=r.inverse_field,
                    metadata=r.metadata_,
                )
            )
        cons_by_ent: dict[uuid.UUID, list[ConstraintDef]] = {}
        for c in cons:
            if c.entity_id:
                cons_by_ent.setdefault(c.entity_id, []).append(
                    ConstraintDef(
                        type=c.type,
                        field=field_name_by_id.get(c.field_id) if c.field_id else None,
                        definition=c.definition,
                    )
                )
        caps_by_ent: dict[uuid.UUID, dict[Capability, bool]] = {}
        itf_caps: dict[Capability, bool] = {}
        for c in caps:
            if c.scope == "entity" and c.entity_id:
                caps_by_ent.setdefault(c.entity_id, {})[Capability(c.capability)] = c.supported
            else:
                itf_caps[Capability(c.capability)] = c.supported

        entities = [
            EntityDef(
                technical_name=e.technical_name,
                display_name=e.display_name,
                module=e.module.technical_name if e.module else None,
                kind=e.kind,
                physical_name=e.physical_name,
                fields=[
                    FieldDef(
                        technical_name=f.technical_name,
                        label=f.label,
                        data_type=f.data_type,  # type: ignore[arg-type]
                        native_type=f.native_type,
                        required=f.required,
                        readonly=f.readonly,
                        stored=f.stored,
                        computed=f.computed,
                        physical_column=f.physical_column,
                        help=f.help,
                        metadata=f.metadata_,
                    )
                    for f in e.fields
                ],
                relations=rels_by_ent.get(e.id, []),
                constraints=cons_by_ent.get(e.id, []),
                capabilities=caps_by_ent.get(e.id, {}),
                metadata=e.metadata_,
            )
            for e in ents
        ]
        return SchemaSnapshotModel(
            system=sys_.code,
            product=prod.code,
            version=ver.version,
            edition=ver.edition,
            interface=itf.code,
            interface_type=itf.type,
            source=snap.source,
            modules=[
                ModuleDef(technical_name=x.technical_name, display_name=x.display_name, metadata=x.metadata_)
                for x in mods
            ],
            entities=entities,
            physical_tables=[
                PhysicalTableDef(
                    table_name=t.table_name,
                    schema_name=t.schema_name,
                    entity=ent_name_by_id.get(t.entity_id) if t.entity_id else None,
                    columns=[
                        PhysicalColumnDef(column_name=c.column_name, db_type=c.db_type, nullable=c.nullable)
                        for c in t.columns
                    ],
                )
                for t in tables
            ],
            capabilities=itf_caps,
            metadata=snap.metadata_,
        )

    def entity_ids(self, snapshot_id: uuid.UUID, names: Collection[str]) -> dict[str, uuid.UUID]:
        rows = self.s.execute(
            select(m.SchemaEntity.technical_name, m.SchemaEntity.id).where(
                m.SchemaEntity.snapshot_id == snapshot_id, m.SchemaEntity.technical_name.in_(list(names))
            )
        )
        return {name: id_ for name, id_ in rows}

    def list_domain_bindings(
        self, snapshot_id: uuid.UUID, domain: str | None = None
    ) -> list[m.DomainBinding]:
        """Entités d'un snapshot standard qui participent à un domaine métier (matérialisé par le domaine)."""
        q = select(m.DomainBinding).where(m.DomainBinding.snapshot_id == snapshot_id)
        if domain:
            q = q.where(m.DomainBinding.domain == domain)
        return list(self.s.scalars(q.order_by(m.DomainBinding.domain, m.DomainBinding.role)))

    # ------------------------------------------------------------------ #
    # Version Compare
    # ------------------------------------------------------------------ #
    def compare(self, a: SnapshotRef, b: SnapshotRef, *, include_unchanged: bool = False) -> CompareResult:
        return self._compare_rows(
            self.resolve_snapshot(a), self.resolve_snapshot(b), include_unchanged=include_unchanged
        )

    def compare_by_id(
        self,
        from_id: uuid.UUID,
        to_id: uuid.UUID,
        *,
        entity: str | None = None,
        include_unchanged: bool = False,
    ) -> CompareResult:
        res = self._compare_rows(
            self.get_snapshot(from_id), self.get_snapshot(to_id), include_unchanged=include_unchanged
        )
        return res.for_entity(entity) if entity else res

    def _compare_rows(
        self, sa: m.SchemaSnapshot, sb: m.SchemaSnapshot, *, include_unchanged: bool
    ) -> CompareResult:
        return compare_snapshots(
            self.load_snapshot_model(sa),
            self.load_snapshot_model(sb),
            include_unchanged=include_unchanged,
            source_label=self.snapshot_label(sa),
            target_label=self.snapshot_label(sb),
        )

    @staticmethod
    def snapshot_label(snap: m.SchemaSnapshot) -> str:
        ver = snap.interface.version
        system, product = ver.product.system.code, ver.product.code
        # produit nommé seulement s'il se distingue du système (« datev buchungsstapel 13 », « odoo 17 »)
        name = system if product == system else f"{system} {product}"
        return f"{name} {ver.version} @{snap.snapshot_version}"
