"""Exécution d'un import vers le système cible (V2d.2) : la PREMIÈRE écriture chez un client.

    prepare(import_id)  (V2d.1 : plan + lignes, même préparation que le preview, jamais refaite autrement)
      → run (request_key : même clé = même exécution) → écrivain du système (contrat `CatalogWriter`)
      → relations (libellés distincts → resolve_labels, une fois) → identités (find_by, un appel)
      → une ligne = un item : PENDING commité AVANT l'appel, puis DONE / FAILED / SKIPPED, commité

Aucun nom de système : entités, champs, relations et identité viennent du plan (bindings de l'instance) ; le
connecteur traduit. Politique :
- valeur vide ou invalide : rien n'est envoyé (une mise à jour n'efface jamais une valeur existante) ;
- ligne non importable (obligatoire manquant) : SKIPPED ;
- identité (SKU) trouvée une fois → UPDATE ; absente → CREATE ; plusieurs → FAILED (jamais deviner) ;
  sans identité → CREATE ; même identité deux fois dans le fichier → la 2e ligne met à jour la 1re ;
- relation ambiguë : valeur non écrite + avertissement ; manquante : créée (résolution déterministe) ;
- colonnes non encore prises en charge (images, offre fournisseur) : « not imported yet ».
Idempotence : `(import_id, request_key)` unique ; un item DONE n'est jamais rejoué ; une création passe par
`create_with_key(external_key)` : rejouée après une réponse perdue, elle ne crée pas de doublon. Une coupure
de connexion arrête l'exécution ; la même clé la reprend (items PENDING rejoués, lignes restantes traitées).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from uie_core.db.base import utcnow
from uie_core.imports.plan import (
    BLOCKING,
    ImportPlan,
    IssueCode,
    Labels,
    NormalizedRow,
    PlannedColumn,
    Ref,
)
from uie_core.imports.records import CatalogImportRunItemRecord, CatalogImportRunRecord
from uie_core.imports.service import READY, CatalogImportService, ImportConflict
from uie_core.imports.writer import (
    CatalogTarget,
    CatalogWriteError,
    CatalogWriterResolver,
    LabelStatus,
    RecordId,
    RecordIds,
    WriteErrorKind,
    WriteValue,
)
from uie_core.instances.discovery import CapabilityStatus
from uie_core.registry.schema import Capability
from uie_core.registry.service import NotFound

RUNNING, COMPLETED, WITH_WARNINGS = "RUNNING", "COMPLETED", "COMPLETED_WITH_WARNINGS"
PARTIAL, FAILED = "PARTIALLY_COMPLETED", "FAILED"
PENDING, DONE, ITEM_FAILED, SKIPPED = "PENDING", "DONE", "FAILED", "SKIPPED"
CREATE, UPDATE, SKIP = "CREATE", "UPDATE", "SKIP"
WRITABLE_KINDS = {"value", "label", "link", "labels"}  # images : V2d.2.1
WRITABLE_GROUPS = {"product"}  # offre fournisseur : plus tard
MAX_ROWS = 1000  # exécution synchrone (V2d.2)
ITEM_WARNINGS = {
    "DUPLICATE_IDENTITY",
    "AMBIGUOUS_IDENTITY",
}  # avertissements propres à l'item (fixés à sa création)


def is_writable(group: str, kind: str) -> bool:
    """Une donnée préparée est-elle écrite par cette version de l'import ?"""
    return group in WRITABLE_GROUPS and kind in WRITABLE_KINDS


class CatalogImportExecutionService:
    def __init__(
        self,
        imports: CatalogImportService,
        writers: CatalogWriterResolver | None = None,
        commit: Callable[[], None] | None = None,
    ) -> None:
        self.imports = imports
        self.s: Session = imports.s
        self.writers = writers or CatalogWriterResolver()
        # commit PAR ITEM (API : session.commit) ; tests : flush (transaction annulée en fin de test)
        self.commit = commit or self.s.flush

    # ------------------------------------------------------------------ #
    def execute(self, import_id: uuid.UUID, request_key: str) -> CatalogImportRunRecord:
        rec = self.imports._analyzed(import_id)  # organisation du contexte (même NotFound qu'un id inconnu)
        key = (request_key or "").strip()
        if not key or len(key) > 64:
            raise ImportConflict("A request key is required.")
        run = self._run(rec.id, key)
        if run is not None and not self._resumable(run):
            return run  # même demande, déjà traitée : rien n'est rejoué
        plan, rows = self.imports.prepare(import_id)  # 409 si le fichier n'est plus prêt
        if len(rows) > MAX_ROWS:
            raise ImportConflict(f"This version imports at most {MAX_ROWS} products at a time.")
        inst = self.imports.instances.get(rec.instance_id)
        self._check_capabilities(inst)
        if run is None:
            run = self._start(rec, inst.id, key, plan, len(rows))
        elif run.plan_checksum != plan.checksum:
            raise ImportConflict("The file or its mapping changed since this import started.")
        else:
            run.status, run.error, run.error_kind, run.finished_at = RUNNING, None, None, None
            self.commit()
        try:
            _, ctx = self.imports.instances.connection_context(inst.id)
            ver = inst.interface.version
            writer = self.writers.resolve(ver.product.system.code, ver.version, inst.interface.code)
            target = writer.open(ctx)
            self._write(run, plan, rows, target)
        except CatalogWriteError as exc:
            run.error_kind, run.error = exc.kind.value, exc.message
        self._finish(run)
        self.commit()
        return run

    def get_run(self, import_id: uuid.UUID, run_id: uuid.UUID) -> CatalogImportRunRecord:
        rec = self.imports._record(import_id)  # organisation
        run = self.s.get(CatalogImportRunRecord, run_id)
        if run is None or run.import_id != rec.id:
            raise NotFound(f"Exécution introuvable : {run_id}")
        return run

    def list_runs(self, import_id: uuid.UUID) -> list[CatalogImportRunRecord]:
        rec = self.imports._record(import_id)
        q = (
            select(CatalogImportRunRecord)
            .where(CatalogImportRunRecord.import_id == rec.id)
            .order_by(CatalogImportRunRecord.started_at.desc())
        )
        return list(self.s.scalars(q))

    def last_runs(self, import_ids: list[uuid.UUID]) -> dict[uuid.UUID, CatalogImportRunRecord]:
        """Dernière exécution de chaque import (liste des imports) ; imports déjà filtrés par organisation."""
        if not import_ids:
            return {}
        q = (
            select(CatalogImportRunRecord)
            .where(CatalogImportRunRecord.import_id.in_(import_ids))
            .order_by(CatalogImportRunRecord.started_at.desc())
        )
        out: dict[uuid.UUID, CatalogImportRunRecord] = {}
        for run in self.s.scalars(q):
            if run.import_id is not None:
                out.setdefault(run.import_id, run)
        return out

    # ------------------------------------------------------------------ #
    def _run(self, import_id: uuid.UUID, key: str) -> CatalogImportRunRecord | None:
        return self.s.scalar(
            select(CatalogImportRunRecord).where(
                CatalogImportRunRecord.import_id == import_id, CatalogImportRunRecord.request_key == key
            )
        )

    @staticmethod
    def _resumable(run: CatalogImportRunRecord) -> bool:
        """Interrompue (coupure, arrêt du processus) : des lignes restent à traiter."""
        return run.status == RUNNING or run.pending > 0 or run.error_kind == WriteErrorKind.CONNECTION

    def _check_capabilities(self, inst: Any) -> None:
        caps = {c.capability: c.status for c in inst.capabilities}
        for cap in (Capability.CREATE, Capability.UPDATE):
            if caps.get(cap.value, caps.get(cap)) == CapabilityStatus.UNAVAILABLE.value:
                raise ImportConflict("This system does not accept imports.")

    def _start(
        self, rec: Any, instance_id: uuid.UUID, key: str, plan: ImportPlan, rows: int
    ) -> CatalogImportRunRecord:
        if self.s.scalar(
            select(CatalogImportRunRecord.id).where(
                CatalogImportRunRecord.import_id == rec.id, CatalogImportRunRecord.status == RUNNING
            )
        ):
            raise ImportConflict("An import of this file is already running.")
        run = CatalogImportRunRecord(
            organization_id=rec.organization_id,
            instance_id=instance_id,
            import_id=rec.id,
            request_key=key,
            filename=rec.filename,
            plan_checksum=plan.checksum,
            file_sha256=rec.file_sha256,
            status=RUNNING,
            rows=rows,
            relations={},
            not_imported_yet=sorted(
                {c.label for c in plan.columns if not is_writable(c.group, c.kind)}, key=str.casefold
            ),
            started_at=utcnow(),
        )
        try:
            with self.s.begin_nested():
                self.s.add(run)
                self.s.flush()
        except IntegrityError:  # double clic simultané : l'autre requête a créé le run
            existing = self._run(rec.id, key)
            if existing is None:
                raise ImportConflict("An import of this file is already running.") from None
            return existing
        self.commit()
        return run

    # ------------------------------------------------------------------ #
    def _write(
        self, run: CatalogImportRunRecord, plan: ImportPlan, rows: list[NormalizedRow], target: CatalogTarget
    ) -> None:
        columns = [c for c in plan.columns if is_writable(c.group, c.kind)]
        entity = columns[0].entity if columns else None
        items = {i.source_row: i for i in run.items}
        if entity is None:
            return
        relations = self._relations(run, columns, rows, target)
        identity = next((c for c in columns if c.identity), None)
        known = self._identities(identity, rows, target, entity) if identity else {}
        created_by_identity = {
            i.identity_value: i.target_record_id
            for i in run.items
            if i.status == DONE and i.action == CREATE and i.identity_value and i.target_record_id
        }
        for row in rows:
            item = items.get(row.source_row)
            if item is not None and item.status != PENDING:
                if item.status == DONE and item.action == CREATE and item.identity_value:
                    created_by_identity[item.identity_value] = item.target_record_id
                continue  # déjà traité : jamais rejoué
            if item is None:
                item = self._new_item(run, row, entity, identity, known, created_by_identity)
                if item.status == SKIPPED:
                    self.commit()
                    continue
            values, warnings = self._values(row, columns, relations)
            # avertissements de l'item (identité) + ligne (preview) + relations ; recalculés à la reprise
            own = [w for w in item.warnings or [] if w.get("code") in ITEM_WARNINGS]
            item.warnings = own + _row_warnings(row, columns) + warnings
            if item.status == ITEM_FAILED:
                self.commit()
                continue
            self.commit()  # l'item (et sa clé externe) existe AVANT tout appel au système
            try:
                if item.action == CREATE:
                    item.target_record_id = target.create_with_key(entity, item.external_key, values)
                    if item.identity_value:
                        created_by_identity[item.identity_value] = item.target_record_id
                else:
                    target.update(entity, item.target_record_id or "", values)
                item.status, item.error_code, item.error = DONE, None, None
            except CatalogWriteError as exc:
                if exc.kind == WriteErrorKind.CONNECTION:
                    item.updated_at = utcnow()
                    self.commit()
                    raise  # coupure : la suite reste à faire (reprise avec la même clé)
                item.status, item.error_code, item.error = ITEM_FAILED, exc.kind.value, exc.message
            item.updated_at = utcnow()
            self.commit()

    def _new_item(
        self,
        run: CatalogImportRunRecord,
        row: NormalizedRow,
        entity: str,
        identity: PlannedColumn | None,
        known: dict[str, list[str]],
        created_by_identity: dict[str, str | None],
    ) -> CatalogImportRunItemRecord:
        value = None
        if identity is not None:
            raw = row.records.get(identity.group, {}).get(identity.field)
            value = str(raw) if raw is not None else None
        item = CatalogImportRunItemRecord(
            source_row=row.source_row,
            identity_value=value,
            external_key=uuid.uuid4().hex,
            entity=entity,
            action=CREATE,
            status=PENDING,
            warnings=[],
        )
        run.items.append(item)
        if not row.importable:
            blocking = next((i for i in row.issues if i.code in BLOCKING), None)
            item.action, item.status = SKIP, SKIPPED
            item.error_code = blocking.code.value if blocking else IssueCode.MISSING_REQUIRED.value
            item.warnings = [{"code": item.error_code, "target_id": blocking.target_id if blocking else None}]
            return item
        if value is None:
            return item  # aucune identité fiable : création
        if value in created_by_identity:  # même identité plus haut dans le fichier : on met à jour celle-ci
            item.action, item.target_record_id = UPDATE, created_by_identity[value]
            item.warnings = [
                {"code": "DUPLICATE_IDENTITY", "target_id": identity.target_id if identity else None}
            ]
            return item
        ids = known.get(value, [])
        if len(ids) == 1:
            item.action, item.target_record_id = UPDATE, ids[0]
        elif len(ids) > 1:
            item.status, item.error_code = ITEM_FAILED, "AMBIGUOUS_IDENTITY"
            item.warnings = [
                {"code": "AMBIGUOUS_IDENTITY", "target_id": identity.target_id if identity else None}
            ]
        return item

    def _identities(
        self, identity: PlannedColumn, rows: list[NormalizedRow], target: CatalogTarget, entity: str
    ) -> dict[str, list[str]]:
        values = sorted(
            {
                str(v)
                for r in rows
                if r.importable and (v := r.records.get(identity.group, {}).get(identity.field)) is not None
            }
        )
        return target.find_by(entity, identity.field, values) if values else {}

    def _relations(
        self,
        run: CatalogImportRunRecord,
        columns: list[PlannedColumn],
        rows: list[NormalizedRow],
        target: CatalogTarget,
    ) -> dict[str, dict[str, dict[str, Any]]]:
        """Colonne → libellé (sans casse) → {label, status, id} ; une fois par run (reprise : relue)."""
        resolved: dict[str, dict[str, dict[str, Any]]] = dict(run.relations or {})
        for col in columns:
            if col.kind not in ("label", "link", "labels") or not col.relation_entity:
                continue
            if col.target_id in resolved:
                continue
            labels: dict[str, str] = {}
            for r in rows:
                v = r.records.get(col.group, {}).get(col.field)
                for label in v.labels if isinstance(v, Labels) else [v.label] if isinstance(v, Ref) else []:
                    labels.setdefault(label.casefold(), label)
            results = target.resolve_labels(col.relation_entity, list(labels.values()), create_missing=True)
            resolved[col.target_id] = {
                r.label.casefold(): {"label": r.label, "status": r.status.value, "id": r.id} for r in results
            }
            run.relations = {**resolved}
            self.commit()  # carte conservée : une reprise ne résout (ni ne crée) rien deux fois
        return resolved

    @staticmethod
    def _values(
        row: NormalizedRow, columns: list[PlannedColumn], relations: dict[str, dict[str, dict[str, Any]]]
    ) -> tuple[dict[str, WriteValue], list[dict[str, Any]]]:
        values: dict[str, WriteValue] = {}
        warnings: list[dict[str, Any]] = []
        for col in columns:
            v = row.records.get(col.group, {}).get(col.field)
            if v is None:
                continue  # vide ou invalide : rien n'est envoyé (aucune valeur existante écrasée)
            if isinstance(v, Ref):
                hit = relations.get(col.target_id, {}).get(v.label.casefold())
                if hit and hit["id"] and hit["status"] in (LabelStatus.FOUND, LabelStatus.CREATED):
                    values[col.field] = RecordId(id=hit["id"])
                else:
                    warnings.append({"code": "AMBIGUOUS_RELATION", "target_id": col.target_id})
            elif isinstance(v, Labels):
                ids, missing = [], False
                for label in v.labels:
                    hit = relations.get(col.target_id, {}).get(label.casefold())
                    if hit and hit["id"] and hit["status"] in (LabelStatus.FOUND, LabelStatus.CREATED):
                        ids.append(hit["id"])
                    else:
                        missing = True
                if missing:
                    warnings.append({"code": "AMBIGUOUS_RELATION", "target_id": col.target_id})
                if ids:
                    values[col.field] = RecordIds(ids=list(dict.fromkeys(ids)))
            elif isinstance(v, str | int | float | bool):
                values[col.field] = v
        return values, warnings

    def _finish(self, run: CatalogImportRunRecord) -> None:
        items = run.items
        done = [i for i in items if i.status == DONE]
        run.created = sum(1 for i in done if i.action == CREATE)
        run.updated = sum(1 for i in done if i.action == UPDATE)
        run.skipped = sum(1 for i in items if i.status == SKIPPED)
        run.failed = sum(1 for i in items if i.status == ITEM_FAILED)
        run.with_warnings = sum(1 for i in done if i.warnings)
        run.pending = run.rows - len(done) - run.skipped - run.failed
        succeeded = len(done)
        if succeeded == 0 and run.rows > 0:
            run.status = FAILED
        elif run.failed or run.skipped or run.pending or run.error:
            run.status = PARTIAL
        elif run.with_warnings:
            run.status = WITH_WARNINGS
        else:
            run.status = COMPLETED
        run.finished_at = utcnow()


def _row_warnings(row: NormalizedRow, columns: list[PlannedColumn]) -> list[dict[str, Any]]:
    """Avertissements du preview pour les données écrites par cette version (images : pas encore écrites)."""
    writable = {c.target_id for c in columns}
    return [
        {"code": i.code.value, "target_id": i.target_id}
        for i in row.issues
        if i.code not in BLOCKING and i.target_id in writable
    ]


__all__ = ["READY", "CatalogImportExecutionService", "is_writable"]
