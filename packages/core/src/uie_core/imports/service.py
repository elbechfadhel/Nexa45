"""Imports de fichiers catalogue (V2c.1) : dépôt, compatibilité avec un système, décisions du fournisseur.

    upload    fichier → profil (forme seulement) → FileStore ; organisation = celle du CONTEXTE
    analyze   système cible (même organisation, analyse du catalogue terminée) → cibles du plan de lecture V2a
              → correspondance déterministe des colonnes (aucun LLM)
    choose_target / ignore_column / ignore_unmatched   décisions du fournisseur, propres à CET import
    mémoire (V2c.2)  un choix explicite (Choose / Change target, Confirm) est retenu pour l'ORGANISATION :
              en-tête normalisé → semantic_key, réappliqué aux analyses suivantes (source LEARNED)
    V2c.2b    un Ignore explicite (colonne REVIEW / UNMATCHED, Ignore all unmatched, correction d'un mapping
              appris / choisi) est retenu pour l'organisation ET le système cible du Registry (NOT_RELEVANT) :
              colonne ignorée d'office aux analyses suivantes vers ce système. L'Ignore d'une colonne que les
              règles ont trouvée seules (MATCHED, AUTO) reste ponctuel : jamais mémorisé.
    V2c.3     fallback LLM À L'ANALYSE, après mémoire et règles, pour les colonnes restantes seulement (un
              appel par lot, cache PAR COLONNE propre à l'organisation) ; une réponse du LLM
              n'écrit JAMAIS la mémoire : seule la confirmation ou la correction du fournisseur l'alimente
              (`choose_target`, `ignore_column`, `ignore_ai_not_relevant`).
    V2d.1     `prepare` (import READY) : décisions validées + cibles courantes + fichier lu UNE fois → plan
              d'import et lignes préparées (`uie_core.imports.plan`), réutilisés tels quels par
              l'import réel (V2d.2) ; `preview` les résume. Lecture seule : ni système cible, ni LLM,
              ni écriture en base.

Contexte obligatoire (V1d) : un import ou un système d'un autre tenant est introuvable, exactement comme un id
inexistant. Aucune écriture vers le système cible.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from uie_core.catalog.service import CatalogNotReady, CatalogSyncService
from uie_core.db.base import utcnow
from uie_core.domains.semantics import SemanticSpec
from uie_core.identity.context import PermissionDenied, TenantContext, tenant_organization
from uie_core.imports.matching import (
    IMPORT_KEYS,
    ColumnMatch,
    ColumnStatus,
    ImportTarget,
    MatchSource,
    ReviewReason,
    ai_candidates,
    apply_ai_proposals,
    header_key,
    import_targets,
    match_by_rules,
    match_columns,
)
from uie_core.imports.plan import (
    ImportPlan,
    ImportPreview,
    NormalizedRow,
    ValidatedColumn,
    build_import_plan,
    normalize_rows,
    summarize,
)
from uie_core.imports.profile import ColumnProfile, ColumnType, FileProfile, profile_file, read_table
from uie_core.imports.proposer import (
    BATCH_SIZE,
    IMPORT_PROMPT,
    MAX_EXAMPLES,
    CandidateColumn,
    ColumnDecision,
    ColumnMappingProposer,
    ColumnProposal,
    ColumnProposalRequest,
    TargetChoice,
    mask_example,
)
from uie_core.imports.records import CatalogImportColumnRecord, CatalogImportRecord, LearnedColumnMapping
from uie_core.imports.storage import FileStore
from uie_core.registry import models as m
from uie_core.registry.service import NotFound, RegistryError

UPLOADED, ANALYZED, READY = "UPLOADED", "ANALYZED", "READY"
AUTO, LEARNED, AI, SUPPLIER = "AUTO", "LEARNED", "AI", "SUPPLIER"
DECIDED_BY = {MatchSource.AUTO: AUTO, MatchSource.LEARNED: LEARNED, MatchSource.AI: AI}
MAPPED, NOT_RELEVANT = "MAPPED", "NOT_RELEVANT"
KNOWN_ANSWERS_SCAN = 100  # imports récents de l'organisation consultés par le cache par colonne (V2c.3)


class ImportConflict(RegistryError):
    """Décision impossible dans l'état courant de l'import (→ HTTP 409)."""


@dataclass(frozen=True)
class ImportView:
    record: CatalogImportRecord
    status: str  # statut effectif : UPLOADED si le système cible a été supprimé
    system_name: str | None
    system_kind: str | None  # nom du système source (« Odoo »), pour le libellé de l'import
    columns: list[CatalogImportColumnRecord]


class CatalogImportService:
    def __init__(
        self,
        session: Session,
        catalog: CatalogSyncService,
        files: FileStore,
        proposer: ColumnMappingProposer | None = None,  # None ou provider « none » : aucun appel LLM
    ) -> None:
        self.s = session
        self.proposer = proposer
        self.ctx = catalog.ctx
        self.catalog = catalog
        self.instances = catalog.instances
        self.enrichment = catalog.enrichment
        self.files = files

    # ------------------------------------------------------------------ #
    def upload(self, filename: str, data: bytes) -> ImportView:
        if not isinstance(self.ctx, TenantContext):
            raise PermissionDenied("Un import appartient à une organisation fournisseur")
        name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()[:255] or "file"
        profile = profile_file(data, name)
        rec = CatalogImportRecord(
            id=uuid.uuid4(),
            organization_id=self.ctx.organization_id,
            filename=name,
            file_format=profile.file_format,
            status=UPLOADED,
            row_count=profile.row_count,
            column_count=len(profile.columns),
            file_size=len(data),
            file_sha256=hashlib.sha256(data).hexdigest(),
            storage_ref="",
        )
        rec.storage_ref = self.files.put(rec.organization_id, rec.id, profile.file_format, data)
        ref = rec.storage_ref
        self._on_rollback(lambda: self.files.delete(ref))  # jamais de fichier sans import
        self.s.add(rec)
        self.s.flush()
        return self._view(rec)

    def list_imports(self) -> list[ImportView]:
        q = select(CatalogImportRecord).order_by(CatalogImportRecord.created_at.desc())
        if (org := tenant_organization(self.ctx)) is not None:
            q = q.where(CatalogImportRecord.organization_id == org)
        return [self._view(r) for r in self.s.scalars(q)]

    def get(self, import_id: uuid.UUID) -> ImportView:
        return self._view(self._record(import_id))

    def analyze(self, import_id: uuid.UUID, instance_id: uuid.UUID) -> ImportView:
        rec = self._record(import_id)
        inst = self._target_system(rec, instance_id)
        targets, spec = self._targets(inst)
        profile = profile_file(self.files.get(rec.storage_ref), rec.filename)
        mapped, ignored = self._memory(rec.organization_id, _system_id(inst))
        learned = {k: row.semantic_key for k, row in mapped.items() if row.semantic_key}
        found = match_columns(profile.columns, targets, spec, learned, frozenset(ignored))
        proposals = self._ask_ai(rec, inst, profile, found, targets, spec)
        if proposals:
            found = apply_ai_proposals(found, proposals, targets, profile.columns)
        matches = {mt.position: mt for mt in found}
        by_id = {t.id: t for t in targets}
        rec.columns.clear()
        self.s.flush()
        for col in profile.columns:
            mt = matches[col.position]
            target = by_id.get(mt.target_id or "")
            rec.columns.append(
                CatalogImportColumnRecord(
                    position=col.position,
                    source_name=col.name[:255],
                    detected_type=col.detected_type.value,
                    filled_values=col.filled,
                    invalid_values=col.invalid,
                    semantic_key=mt.semantic_key,
                    target_id=target.id if target else None,
                    target_field=f"{target.entity}.{target.field}" if target else None,
                    confidence=mt.confidence,
                    status=mt.status.value,
                    reason=mt.reason.value if mt.reason else None,
                    ignored=mt.ignored,
                    decided_by=DECIDED_BY[mt.source],
                )
            )
            if mt.source == MatchSource.LEARNED:
                row = (ignored if mt.ignored else mapped)[header_key(col.name)]
                row.usage_count, row.last_used_at = row.usage_count + 1, utcnow()
        # KPI figés : ce que le moteur a résolu seul, et ce qu'il reste à décider au fournisseur
        states = [(mt.status, mt.source) for mt in matches.values() if not mt.ignored]
        rec.resolved_auto = states.count((ColumnStatus.MATCHED, MatchSource.AUTO))
        rec.resolved_learned = states.count((ColumnStatus.MATCHED, MatchSource.LEARNED))
        rec.resolved_ignored = sum(1 for mt in matches.values() if mt.ignored)
        rec.resolved_ai = states.count((ColumnStatus.MATCHED, MatchSource.AI))
        ai_review = [mt.reason for mt in matches.values() if mt.source == MatchSource.AI and not mt.ignored]
        rec.not_relevant_ai = ai_review.count(ReviewReason.AI_NOT_RELEVANT)
        rec.suggested_ai = sum(
            1 for r in ai_review if r in (ReviewReason.AI_SUGGESTION, ReviewReason.TARGET_TAKEN)
        )
        rec.needs_attention = sum(
            1 for st, _ in states if st in (ColumnStatus.REVIEW, ColumnStatus.UNMATCHED)
        )
        rec.instance_id, rec.analyzed_at = inst.id, utcnow()
        rec.row_count, rec.column_count = profile.row_count, len(profile.columns)
        self._refresh_status(rec)
        self.s.flush()
        return self._view(rec)

    def targets(self, import_id: uuid.UUID) -> list[ImportTarget]:
        """Cibles offertes au fournisseur (« Choose target ») : champs du catalogue de SON système."""
        rec = self._analyzed(import_id)
        return self._targets(self.instances.get(rec.instance_id))[0]

    def choose_target(self, import_id: uuid.UUID, position: int, target_id: str) -> ImportView:
        rec = self._analyzed(import_id)
        col = self._column(rec, position)
        if col.status == ColumnStatus.EMPTY.value:
            raise ImportConflict("This column has no values in the file.")
        inst = self.instances.get(rec.instance_id)
        targets, spec = self._targets(inst)
        target = next((t for t in targets if t.id == target_id), None)
        if target is None:
            raise NotFound(f"Cible inconnue : {target_id}")
        other = next(
            (c for c in rec.columns if c.position != position and c.target_id == target_id and not c.ignored),
            None,
        )
        if other is not None:
            raise ImportConflict(f"This target is already used by the column “{other.source_name}”.")
        col.semantic_key, col.target_id = target.semantic_key, target.id
        col.target_field = f"{target.entity}.{target.field}"
        col.status, col.reason, col.confidence = ColumnStatus.MATCHED.value, None, None
        col.ignored, col.decided_by = False, SUPPLIER
        self._learn(rec.organization_id, _system_id(inst), col, target, targets, spec)
        self._refresh_status(rec)
        self.s.flush()
        return self._view(rec)

    def ignore_column(self, import_id: uuid.UUID, position: int) -> ImportView:
        rec = self._analyzed(import_id)
        col = self._column(rec, position)
        if col.status == ColumnStatus.EMPTY.value:
            raise ImportConflict("This column has no values in the file.")
        # colonne trouvée par les règles seules puis ignorée ici : décision ponctuelle, jamais mémorisée
        if not (col.status == ColumnStatus.MATCHED.value and col.decided_by == AUTO):
            self._learn_ignore(rec, col)
        col.ignored, col.decided_by = True, SUPPLIER
        self._refresh_status(rec)
        self.s.flush()
        return self._view(rec)

    def ignore_unmatched(self, import_id: uuid.UUID) -> ImportView:
        """« Ignore all unmatched columns » : chaque colonne UNMATCHED devient IGNORED (conservée)."""
        rec = self._analyzed(import_id)
        for col in rec.columns:
            if col.status == ColumnStatus.UNMATCHED.value and not col.ignored:
                self._learn_ignore(rec, col)  # action confirmée : une décision explicite par colonne
                col.ignored, col.decided_by = True, SUPPLIER
        self._refresh_status(rec)
        self.s.flush()
        return self._view(rec)

    def prepare(self, import_id: uuid.UUID) -> tuple[ImportPlan, list[NormalizedRow]]:
        """Ce que l'import essaierait d'écrire : plan (décisions validées × cibles COURANTES de l'instance) et
        lignes préparées. Aucune analyse sémantique, ni LLM, ni connexion au système, ni écriture."""
        rec = self._analyzed(import_id)  # organisation du contexte
        if rec.status != READY:
            raise ImportConflict("This file is not ready for import yet.")
        inst = self.instances.get(rec.instance_id)
        targets, spec = self._targets(inst)
        decisions = [
            ValidatedColumn(
                position=c.position,
                source_name=c.source_name,
                detected_type=ColumnType(c.detected_type),
                status=ColumnStatus(c.status),
                reason=c.reason,
                ignored=c.ignored,
                target_id=c.target_id,
            )
            for c in rec.columns
        ]
        plan = build_import_plan(
            import_id=rec.id,
            instance_id=inst.id,
            file_sha256=rec.file_sha256,
            decisions=decisions,
            targets=targets,
            spec=spec,
        )
        _, table = read_table(self.files.get(rec.storage_ref), rec.filename)  # le fichier, une seule fois
        return plan, normalize_rows(table.iter_rows(), plan)

    def preview(self, import_id: uuid.UUID) -> ImportPreview:
        """Résumé de `prepare` pour le fournisseur (V2d.1, lecture seule)."""
        return summarize(*self.prepare(import_id))

    def ignore_ai_not_relevant(self, import_id: uuid.UUID) -> ImportView:
        """« Ignore all AI not relevant » (confirmé à l'écran) : chaque colonne que le LLM juge inutile
        devient IGNORED ; la décision, désormais humaine, est apprise pour ce système (mécanisme V2c.2b)."""
        rec = self._analyzed(import_id)
        for col in rec.columns:
            if col.reason == ReviewReason.AI_NOT_RELEVANT.value and not col.ignored:
                self._learn_ignore(rec, col)
                col.ignored, col.decided_by = True, SUPPLIER
        self._refresh_status(rec)
        self.s.flush()
        return self._view(rec)

    def delete(self, import_id: uuid.UUID) -> None:
        rec = self._record(import_id)
        ref = rec.storage_ref
        self.s.delete(rec)
        self.s.flush()
        self._on_commit(lambda: self.files.delete(ref))  # le fichier suit l'import, après le commit

    # ------------------------------------------------------------------ #
    # interne
    # ------------------------------------------------------------------ #
    def _record(self, import_id: uuid.UUID) -> CatalogImportRecord:
        rec = self.s.get(CatalogImportRecord, import_id)
        org = tenant_organization(self.ctx)
        if rec is None or (org is not None and rec.organization_id != org):
            raise NotFound(f"Import introuvable : {import_id}")  # même erreur pour un autre tenant
        return rec

    def _analyzed(self, import_id: uuid.UUID) -> CatalogImportRecord:
        rec = self._record(import_id)
        if rec.instance_id is None or rec.status == UPLOADED:
            raise ImportConflict("Choose a target system and analyze the file first.")
        return rec

    def _target_system(self, rec: CatalogImportRecord, instance_id: uuid.UUID) -> m.Instance:
        inst = self.instances.get(instance_id)  # filtré par le contexte
        if inst.organization_id != rec.organization_id:  # Admin / System : jamais entre deux organisations
            raise NotFound(f"Instance introuvable : {instance_id}")
        return inst

    def _targets(self, inst: m.Instance) -> tuple[list[ImportTarget], SemanticSpec]:
        try:
            summary = self.enrichment.summary(inst.id)
        except NotFound:
            raise CatalogNotReady("Analyze this system's catalog first.") from None
        if not summary.complete:
            raise CatalogNotReady("Finish this system's catalog analysis first.")
        spec = self.catalog.domains.pack_for_instance(inst).semantics()
        return import_targets(self.catalog.read_plan(inst.id, IMPORT_KEYS), spec), spec

    def _ask_ai(
        self,
        rec: CatalogImportRecord,
        inst: m.Instance,
        profile: FileProfile,
        matches: list[ColumnMatch],
        targets: list[ImportTarget],
        spec: SemanticSpec,
    ) -> dict[int, ColumnProposal]:
        """Fallback LLM des colonnes restantes. Cache PAR COLONNE de l'organisation : une colonne déjà
        analysée (même empreinte) reprend sa réponse ; seules les autres partent au LLM, un appel par lot
        (BATCH_SIZE). Aucun candidat ou aucun fournisseur → aucun appel. Échec : jamais mis en cache."""
        candidates = ai_candidates(matches)
        if self.proposer is None or self.proposer.provider == "none" or not candidates or not targets:
            rec.ai_request_checksum, rec.ai_result = None, None
            return {}
        provider, model = self.proposer.provider, self.proposer.model
        template = self._ai_request(inst, profile, candidates, targets, spec)
        sums = {c.position: template.column_checksum(c, provider, model) for c in template.columns}
        known = self._known_answers(rec.organization_id)
        missing = [c for c in template.columns if sums[c.position] not in known]
        result = self._call_ai(template, missing)
        headers = {c.position: c.header for c in template.columns}
        reused = [
            {**known[sums[pos]], "position": pos, "header": headers[pos], "column_checksum": sums[pos]}
            for pos in headers
            if sums[pos] in known
        ]
        for p in result["proposals"]:
            p["column_checksum"] = sums[p["position"]]
        result["proposals"] = sorted(result["proposals"] + reused, key=lambda p: p["position"])
        result["reused_columns"] = len(reused)
        if result.get("error"):  # UNKNOWN pour les lots en échec ; retenté à l'analyse suivante
            rec.ai_request_checksum, rec.ai_result = None, None
            return _proposals(result)
        rec.ai_request_checksum = hashlib.sha256("|".join(sorted(sums.values())).encode("utf-8")).hexdigest()
        rec.ai_result = result
        return _proposals(result)

    def _known_answers(self, organization_id: uuid.UUID) -> dict[str, dict[str, Any]]:
        """Réponses déjà obtenues par CETTE organisation, par empreinte de colonne (la plus récente gagne)."""
        rows = self.s.scalars(
            select(CatalogImportRecord.ai_result)
            .where(
                CatalogImportRecord.organization_id == organization_id,  # jamais entre organisations
                CatalogImportRecord.ai_result.is_not(None),
            )
            .order_by(CatalogImportRecord.analyzed_at.desc())
            .limit(KNOWN_ANSWERS_SCAN)
        )
        known: dict[str, dict[str, Any]] = {}
        for result in rows:
            for p in (result or {}).get("proposals", []):  # JSON null des lignes antérieures
                if (cs := p.get("column_checksum")) and cs not in known:
                    known[cs] = {k: p.get(k) for k in ("decision", "semantic_key", "confidence", "reason")}
        return known

    def _ai_request(
        self,
        inst: m.Instance,
        profile: FileProfile,
        candidates: list[ColumnMatch],
        targets: list[ImportTarget],
        spec: SemanticSpec,
    ) -> ColumnProposalRequest:
        """Requête de TOUTES les colonnes candidates (découpée en lots à l'appel)."""
        ver = inst.interface.version
        cols = {c.position: c for c in profile.columns}
        choices: dict[str, TargetChoice] = {}
        for t in targets:  # une clé = une cible offerte (même ordre de préférence que les règles)
            entry = spec.vocabulary.get(t.semantic_key)
            choices.setdefault(
                t.semantic_key,
                TargetChoice(
                    key=t.semantic_key,
                    label=t.label,
                    description=entry.description if entry else t.label,
                    group=t.group,
                    data_type=t.data_type,
                ),
            )
        columns = [
            CandidateColumn(
                position=mt.position,
                header=cols[mt.position].name,
                detected_type=cols[mt.position].detected_type.value,
                fill_ratio=round(cols[mt.position].filled / profile.row_count, 2)
                if profile.row_count
                else 0.0,
                examples=[e for v in cols[mt.position].examples[:MAX_EXAMPLES] if (e := mask_example(v))],
                rules_suggestion=mt.semantic_key if mt.status == ColumnStatus.REVIEW else None,
            )
            for mt in candidates
        ]
        return ColumnProposalRequest(
            request_ref="imp-template",
            domain=self.catalog.domains.domain_key.value,
            system=ver.product.system.code,
            version=ver.version,
            columns=columns,
            targets=list(choices.values()),
            prompt=self.catalog.domains.packs.prompt(self.catalog.domains.domain_key, IMPORT_PROMPT),
        )

    def _call_ai(self, template: ColumnProposalRequest, columns: list[CandidateColumn]) -> dict[str, Any]:
        """Appels (un par lot de `columns`) → résultat validé + provenance agrégée ; aucune colonne → aucun
        appel. Les exemples ne sont pas conservés."""
        assert self.proposer is not None
        result: dict[str, Any] = {
            "provider": self.proposer.provider,
            "model": self.proposer.model,
            "prompt_version": template.prompt.version,
            "prompt_checksum": template.prompt.checksum,
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": 0.0,
            "latency_ms": 0,
            "request_ids": [],
            "proposals": [],
            "errors": [],
            "ignored_fields": [],
            "error": None,
        }
        for i in range(0, len(columns), BATCH_SIZE):
            request = template.model_copy(
                update={
                    "columns": columns[i : i + BATCH_SIZE],
                    "request_ref": f"imp-{uuid.uuid4().hex[:12]}",  # opaque : ni organisation, ni fichier
                }
            )
            batch = self.proposer.propose_columns(request)
            meta = batch.meta
            result["calls"] += 1
            result["model"] = meta.model or result["model"]
            for k in ("input_tokens", "output_tokens", "latency_ms"):
                result[k] += getattr(meta, k) or 0
            result["cost_usd"] = round(result["cost_usd"] + (meta.cost_usd or 0.0), 6)
            if meta.request_id:
                result["request_ids"].append(meta.request_id)
            headers = {c.position: c.header for c in request.columns}
            result["proposals"] += [
                {**p.model_dump(mode="json"), "header": headers[p.position]} for p in batch.proposals
            ]
            result["errors"] += batch.errors
            result["ignored_fields"] = sorted(set(result["ignored_fields"]) | set(batch.ignored_fields))
            if batch.error:
                result["error"] = batch.error
        return result

    def _memory(
        self, organization_id: uuid.UUID, system_id: uuid.UUID
    ) -> tuple[dict[str, LearnedColumnMapping], dict[str, LearnedColumnMapping]]:
        """(mappings de l'organisation, NOT_RELEVANT de l'organisation pour CE système), par `header_key`."""
        rows = self.s.scalars(
            select(LearnedColumnMapping).where(
                LearnedColumnMapping.organization_id == organization_id,
                LearnedColumnMapping.system_id.is_(None) | (LearnedColumnMapping.system_id == system_id),
            )
        )
        mapped: dict[str, LearnedColumnMapping] = {}
        ignored: dict[str, LearnedColumnMapping] = {}
        for r in rows:
            (ignored if r.decision == NOT_RELEVANT else mapped)[r.header_key] = r
        return mapped, ignored

    def _learn_ignore(self, rec: CatalogImportRecord, col: CatalogImportColumnRecord) -> None:
        """Ignore explicite → NOT_RELEVANT pour l'organisation et le système cible. Un mapping de
        l'organisation est conservé (il sert aux autres systèmes) : sur celui-ci, le NOT_RELEVANT prime."""
        key = header_key(col.source_name)
        if not key:
            return
        system_id = _system_id(self.instances.get(rec.instance_id))
        if key in self._memory(rec.organization_id, system_id)[1]:
            return
        self.s.add(
            LearnedColumnMapping(
                organization_id=rec.organization_id,
                header_key=key,
                decision=NOT_RELEVANT,
                system_id=system_id,
            )
        )
        self.s.flush()

    def _learn(
        self,
        organization_id: uuid.UUID,
        system_id: uuid.UUID,
        col: CatalogImportColumnRecord,
        target: ImportTarget,
        targets: list[ImportTarget],
        spec: SemanticSpec,
    ) -> None:
        """Choix explicite du fournisseur → mémoire de SON organisation (en-tête normalisé → semantic_key).
        Rien n'est mémorisé si le vocabulaire et les règles donnent déjà ce résultat seuls (connaissance
        déjà couverte) ; une ancienne mémoire de cet en-tête est alors retirée, sinon remplacée. Un
        NOT_RELEVANT de cet en-tête pour ce système est retiré dans tous les cas (V2c.2b)."""
        key = header_key(col.source_name)
        if not key:
            return
        mapped, ignored = self._memory(organization_id, system_id)
        if key in ignored:
            self.s.delete(ignored[key])
        profile = ColumnProfile(
            position=col.position,
            name=col.source_name,
            detected_type=ColumnType(col.detected_type),
            filled=col.filled_values,
        )
        by_rules = match_by_rules(profile, targets, spec)
        covered = by_rules.status == ColumnStatus.MATCHED and by_rules.semantic_key == target.semantic_key
        row = mapped.get(key)
        if covered:
            if row is not None:
                self.s.delete(row)
            return
        if row is None:
            self.s.add(
                LearnedColumnMapping(
                    organization_id=organization_id, header_key=key, semantic_key=target.semantic_key
                )
            )
        elif row.semantic_key != target.semantic_key:
            row.semantic_key, row.updated_at = target.semantic_key, utcnow()

    @staticmethod
    def _column(rec: CatalogImportRecord, position: int) -> CatalogImportColumnRecord:
        col = next((c for c in rec.columns if c.position == position), None)
        if col is None:
            raise NotFound(f"Colonne introuvable : {position}")
        return col

    @staticmethod
    def _refresh_status(rec: CatalogImportRecord) -> None:
        settled = {
            ColumnStatus.MATCHED.value,
            ColumnStatus.EMPTY.value,
        }  # une colonne vide n'a rien à importer
        done = all(c.ignored or c.status in settled for c in rec.columns)
        rec.status = READY if rec.columns and done else ANALYZED

    def _view(self, rec: CatalogImportRecord) -> ImportView:
        if rec.instance_id is None:  # jamais analysé, ou système cible supprimé depuis
            return ImportView(record=rec, status=UPLOADED, system_name=None, system_kind=None, columns=[])
        inst = self.s.get(m.Instance, rec.instance_id)
        return ImportView(
            record=rec,
            status=rec.status,
            system_name=inst.name if inst else None,
            system_kind=inst.interface.version.product.system.name if inst else None,
            columns=list(rec.columns),
        )

    def _on_commit(self, action: Callable[[], None]) -> None:
        """`action` au commit de la transaction courante ; oubliée si elle est annulée."""
        self._hook(action, on_commit=True)

    def _on_rollback(self, action: Callable[[], None]) -> None:
        """`action` si la transaction courante est annulée ; oubliée à son commit."""
        self._hook(action, on_commit=False)

    def _hook(self, action: Callable[[], None], *, on_commit: bool) -> None:
        # un écouteur ne se retire jamais lui-même pendant l'événement (interdit) : drapeau `done`
        done = False

        def committed(_session: Session) -> None:
            nonlocal done
            if not done:
                done = True
                if on_commit:
                    action()

        def rolled_back(_session: Session, previous: Any) -> None:
            nonlocal done
            if done or getattr(previous, "nested", False):
                return  # déjà traité, ou annulation d'un savepoint : la transaction principale continue
            done = True
            event.remove(self.s, "after_commit", committed)
            if not on_commit:
                action()

        event.listen(self.s, "after_commit", committed, once=True)
        event.listen(self.s, "after_soft_rollback", rolled_back)


def _proposals(result: dict[str, Any]) -> dict[int, ColumnProposal]:
    return {
        p["position"]: ColumnProposal(
            position=p["position"],
            decision=ColumnDecision(p["decision"]),
            semantic_key=p.get("semantic_key"),
            confidence=p.get("confidence") or 0.0,
            reason=p.get("reason"),
        )
        for p in result.get("proposals", [])
    }


def _system_id(inst: m.Instance) -> uuid.UUID:
    """Système du Registry de l'instance : portée d'un NOT_RELEVANT appris."""
    return inst.interface.version.product.system.id
