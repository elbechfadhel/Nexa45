"""SemanticEnrichmentService (Product Domain V1b) : règles + décisions humaines, sans LLM.

Travaille sur un domain snapshot PERSISTÉ (jamais sur l'instance en direct) et sur l'état de décision
`field_semantic_bindings` (clé logique : instance, domaine, entité, champ).

Réutilisation à chaque analyse :
- décision HUMAN (CONFIRMED / IGNORED / include / reopen, « other ») : signature technique compatible →
  réappliquée telle quelle ; signature changée → STALE, question reposée avec l'ancienne réponse ;
  champ disparu → ORPHANED (conservé, invisible) ;
- décision de règle : recalculée (les règles peuvent avoir évolué) ; une règle n'écrase JAMAIS une
  décision HUMAN ;
- proposition du LLM (V1c, `enrich_with_llm`, jamais déclenchée par l'analyse) : conservée tant que la
  signature technique n'a pas changé et que la règle n'a rien de plus sûr à dire (sinon la règle reprend
  la main) ;
- chaque changement de statut est journalisé (field_semantic_binding_events).
Entrypoints (CLI, API) : appellent ce service puis commitent.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from uie_core.db.base import utcnow
from uie_core.domains.ai_enrichment import AiEnrichmentMixin
from uie_core.domains.model import (
    DomainEntityDef,
    DomainFieldDef,
    EntityRole,
    FieldOrigin,
    Relevance,
)
from uie_core.domains.pack import DomainPackResolver
from uie_core.domains.proposer import NoProposer, SemanticProposer
from uie_core.domains.semantics import (
    OTHER_KEY,
    DecisionSource,
    FieldDecision,
    MeaningStatus,
    SemanticSpec,
    Vocabulary,
    classify,
    propose_key,
    signature_compatible,
    technical_signature,
)
from uie_core.domains.service import ProductDomainError, ProductDomainService, StoredDomainSnapshot
from uie_core.identity.context import Context, ensure_context
from uie_core.registry import models as m
from uie_core.registry.service import NotFound

log = logging.getLogger(__name__)

EXTENSION_ORIGINS = frozenset({FieldOrigin.CUSTOM.value, FieldOrigin.THIRD_PARTY.value})
_ORIGIN_ORDER = {"CUSTOM": 0, "THIRD_PARTY": 1, "UNKNOWN": 2, "STANDARD": 3}


class SemanticError(ProductDomainError):
    """Réponse refusée (clé hors vocabulaire, action impossible) : 400 côté API."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, json_schema_serialization_defaults_required=True)


class BindingView(_Model):
    """Un champ d'instance, sa décision courante et ses métadonnées techniques (affichage)."""

    entity_technical_name: str
    field_technical_name: str
    path: str
    semantic_key: str | None
    semantic_label: str | None  # libellé de la clé dans le vocabulaire
    relevance: Relevance
    meaning_status: MeaningStatus
    source: DecisionSource
    rule_id: str | None
    confidence: float | None
    reason: str | None
    origin: FieldOrigin
    pending: bool
    mirror_of: str | None
    previous: dict[str, Any] | None  # ancienne réponse (STALE)
    note: str | None
    human_label: str | None
    confirmed_at: datetime | None
    updated_at: datetime
    field: DomainFieldDef
    # V1c : proposition du LLM (source LLM) ou raison de son abstention
    justification: str | None = None
    low_confidence: bool = False
    note_ai: str | None = None


class EnrichmentResult(_Model):
    domain_snapshot_id: uuid.UUID
    instance_schema_snapshot_sequence: int
    rules_version: str
    rules_checksum: str
    created: int
    updated: int  # décisions de règle recalculées différemment
    unchanged: int
    reapplied: int  # décisions humaines réappliquées (signature compatible)
    stale: int
    orphaned: int
    pending: int


class StructureCheck(_Model):
    role: EntityRole
    present: bool


class CatalogAnalysisSummary(_Model):
    """Chiffres de la page « Product Catalog Analysis » (mêmes chiffres pour l'API et la CLI).

    Chaque champ (hors ORPHANED) est dans exactement un compteur :
    pending + excluded_by_supplier + excluded_auto + identified + possible == total.
    """

    domain_snapshot_id: uuid.UUID
    instance_schema_snapshot_sequence: int
    analyzed_at: datetime
    rules_version: str | None
    rules_checksum: str | None
    enriched: bool  # au moins une analyse sémantique a eu lieu
    total: int
    identified: int  # RELEVANT & (KNOWN | CONFIRMED)
    identified_with_key: int
    excluded_auto: int  # NOT_RELEVANT par règle
    excluded_auto_by_rule: dict[str, int]
    excluded_by_supplier: int  # IGNORED
    possible: int  # sans question ouverte
    pending: int  # questions ouvertes
    extensions: int
    extensions_excluded_auto: int
    extensions_pending: int
    orphaned: int
    supplier_decisions: int  # réponses données par le supplier (hors questions ouvertes), hors partition
    structure: list[StructureCheck]
    complete: bool  # enriched and pending == 0


def bucket(row: m.FieldSemanticBinding) -> str:
    """Compteur du résumé auquel appartient un binding (un seul)."""
    if row.pending:
        return "pending"
    if row.meaning_status == MeaningStatus.IGNORED:
        return "excluded_by_supplier"
    if row.relevance == Relevance.NOT_RELEVANT:
        return "excluded_auto"
    if row.relevance == Relevance.RELEVANT and row.meaning_status in (
        MeaningStatus.KNOWN,
        MeaningStatus.CONFIRMED,
    ):
        return "identified"
    return "possible"


class SemanticEnrichmentService(AiEnrichmentMixin):
    def __init__(
        self,
        session: Session,
        ctx: Context,
        packs: DomainPackResolver | None = None,
        *,
        domains: ProductDomainService | None = None,
        proposer: SemanticProposer | None = None,
    ) -> None:
        self.s = session
        self.ctx = ensure_context(
            ctx
        )  # V1d : obligatoire ; toutes les lectures passent par domains.instance()
        self.packs = packs or DomainPackResolver()
        self.domains = domains or ProductDomainService(session, self.ctx, self.packs)
        if self.domains.ctx != self.ctx:
            raise ValueError(
                "SemanticEnrichmentService et ProductDomainService doivent partager le même contexte"
            )
        self.domain_key = self.domains.domain_key
        # V1c : le Core ne connaît que le contrat ; sans proposer injecté, aucun LLM
        self.proposer: SemanticProposer = proposer or NoProposer()

    # ------------------------------------------------------------------ #
    # Enrichissement (déclenché par l'analyse)
    # ------------------------------------------------------------------ #
    def enrich(self, stored: StoredDomainSnapshot) -> EnrichmentResult | None:
        """Applique les règles au domain snapshot et réconcilie avec les décisions existantes.
        None si le domain pack ne fournit pas de règles sémantiques."""
        snap = stored.snapshot
        inst = self.domains.instance(snap.instance_id)
        spec = self.domains.pack_for_instance(inst).semantics()
        if spec is None:
            return None
        now = utcnow()
        fields = {f.path: f for f in snap.fields if f.present}
        # copies related en dernier : elles reprennent l'état FINAL de leur champ d'origine
        decisions = sorted(classify(snap.fields, snap.entities, spec), key=lambda d: d.mirror_of is not None)
        rows = {self._path(r): r for r in self._rows(inst.id)}
        known = dict(rows)
        counts = dict.fromkeys(("created", "updated", "unchanged", "reapplied", "stale", "orphaned"), 0)
        isnap_id = snap.instance_schema_snapshot_id

        for decision in decisions:
            path = decision.path
            field = fields[path]
            if decision.mirror_of and decision.mirror_of in known:
                decision = self._follow(decision, known[decision.mirror_of])
            signature = technical_signature(field)
            row = rows.pop(path, None)
            if row is None:
                row = self._new_row(inst.id, field, decision, signature, isnap_id, now)
                self.s.add(row)
                known[path] = row
                self._event(row, "ENRICH", None, isnap_id, now)
                counts["created"] += 1
                continue
            row.field_def = field.model_dump(mode="json")
            row.origin = field.origin.value
            row.last_seen_snapshot_id = isnap_id
            if row.meaning_status == MeaningStatus.ORPHANED:
                self._restore(row)
            if row.source == DecisionSource.HUMAN:
                counts[self._reconcile_human(row, signature, isnap_id, now)] += 1
            elif row.ai_prompt_checksum and self._keeps_ai(row, decision, signature):
                row.technical_signature = signature
                counts["unchanged"] += 1
            else:
                self._clear_ai(row)
                counts[self._apply_rule(row, decision, signature, isnap_id, now)] += 1

        for row in rows.values():  # champs disparus de l'instance
            if row.meaning_status != MeaningStatus.ORPHANED:
                before = row.meaning_status
                row.previous = self._answer(row)
                row.meaning_status = MeaningStatus.ORPHANED
                row.pending = False
                row.updated_at = now
                self._event(row, "ORPHAN", before, isnap_id, now)
                counts["orphaned"] += 1

        self.s.flush()
        pending = sum(
            1 for r in self._rows(inst.id) if r.pending and r.meaning_status != MeaningStatus.ORPHANED
        )
        log.info("semantic enrichment instance=%s %s pending=%s", inst.id, counts, pending)
        return EnrichmentResult(
            domain_snapshot_id=stored.id,
            instance_schema_snapshot_sequence=stored.instance_schema_snapshot_sequence,
            rules_version=spec.version,
            rules_checksum=spec.checksum,
            pending=pending,
            **counts,
        )

    def _new_row(
        self,
        instance_id: uuid.UUID,
        field: DomainFieldDef,
        d: FieldDecision,
        signature: dict[str, Any],
        isnap_id: uuid.UUID,
        now: datetime,
    ) -> m.FieldSemanticBinding:
        return m.FieldSemanticBinding(
            instance_id=instance_id,
            domain_key=self.domain_key.value,
            entity_technical_name=field.entity_technical_name,
            field_technical_name=field.field_technical_name,
            semantic_key=d.semantic_key,
            relevance=d.relevance.value,
            meaning_status=d.status.value,
            source=d.source.value,
            rule_id=d.rule_id,
            confidence=d.confidence,
            reason=d.reason,
            origin=field.origin.value,
            pending=d.pending,
            mirror_of=d.mirror_of,
            technical_signature=signature,
            field_def=field.model_dump(mode="json"),
            decided_on_snapshot_id=isnap_id,
            last_seen_snapshot_id=isnap_id,
            updated_at=now,
        )

    def _apply_rule(
        self,
        row: m.FieldSemanticBinding,
        d: FieldDecision,
        signature: dict[str, Any],
        isnap_id: uuid.UUID,
        now: datetime,
    ) -> str:
        """Décision de règle recalculée : l'ancienne est écrasée."""
        new = {
            "semantic_key": d.semantic_key,
            "relevance": d.relevance.value,
            "meaning_status": d.status.value,
            "source": d.source.value,
            "rule_id": d.rule_id,
            "confidence": d.confidence,
            "reason": d.reason,
            "pending": d.pending,
            "mirror_of": d.mirror_of,
        }
        row.technical_signature = signature
        if all(getattr(row, k) == v for k, v in new.items()):
            return "unchanged"
        before = row.meaning_status
        for k, v in new.items():
            setattr(row, k, v)
        row.previous = None
        row.decided_on_snapshot_id = isnap_id
        row.updated_at = now
        if before != row.meaning_status:
            self._event(row, "ENRICH", before, isnap_id, now)
        return "updated"

    def _reconcile_human(
        self, row: m.FieldSemanticBinding, signature: dict[str, Any], isnap_id: uuid.UUID, now: datetime
    ) -> str:
        """Une décision humaine n'est jamais recalculée : réappliquée, ou reposée si le champ a changé."""
        if signature_compatible(row.technical_signature, signature):
            row.technical_signature = signature  # ex. nouvelles valeurs de sélection (sur-ensemble)
            return "stale" if row.meaning_status == MeaningStatus.STALE else "reapplied"
        before = row.meaning_status
        if before != MeaningStatus.STALE:
            row.previous = {**self._answer(row), "technical_signature": row.technical_signature}
        row.technical_signature = signature
        row.meaning_status = MeaningStatus.STALE
        row.pending = True
        row.reason = "The field changed since your last answer (type, link target or selection values)"
        row.updated_at = now
        if before != MeaningStatus.STALE:
            self._event(row, "STALE", before, isnap_id, now)
        return "stale"

    @staticmethod
    def _follow(d: FieldDecision, src: m.FieldSemanticBinding) -> FieldDecision:
        """Décision d'une copie related = état courant de son champ d'origine (règle ou humain)."""
        status = src.meaning_status
        return d.model_copy(
            update={
                "semantic_key": src.semantic_key,
                "relevance": Relevance(src.relevance),
                "confidence": src.confidence,
                "status": MeaningStatus.UNRESOLVED
                if status == MeaningStatus.ORPHANED
                else MeaningStatus(status),
                "pending": False,
            }
        )

    @staticmethod
    def _answer(row: m.FieldSemanticBinding) -> dict[str, Any]:
        return {
            "meaning_status": row.meaning_status,
            "semantic_key": row.semantic_key,
            "relevance": row.relevance,
            "pending": row.pending,
        }

    @staticmethod
    def _restore(row: m.FieldSemanticBinding) -> None:
        """Champ réapparu : on repart de la décision d'avant la disparition."""
        prev = row.previous or {}
        row.meaning_status = prev.get("meaning_status", MeaningStatus.UNRESOLVED.value)
        row.pending = bool(prev.get("pending", False))
        row.previous = None

    def _sync_mirrors(
        self, rows: list[m.FieldSemanticBinding], isnap_id: uuid.UUID | None, now: datetime
    ) -> None:
        """Les copies related suivent la décision (règle ou humaine) de leur champ d'origine."""
        by_path = {self._path(r): r for r in rows}
        for row in rows:
            if not row.mirror_of or row.source == DecisionSource.HUMAN:
                continue
            src = by_path.get(row.mirror_of)
            if src is None or row.meaning_status == MeaningStatus.ORPHANED:
                continue
            before = row.meaning_status
            row.semantic_key = src.semantic_key
            row.relevance = src.relevance
            row.confidence = src.confidence
            row.meaning_status = (
                src.meaning_status
                if src.meaning_status != MeaningStatus.ORPHANED
                else MeaningStatus.UNRESOLVED
            )
            row.pending = False
            if before != row.meaning_status:
                row.updated_at = now
                self._event(row, "MIRROR", before, isnap_id, now)

    # ------------------------------------------------------------------ #
    # Décisions humaines
    # ------------------------------------------------------------------ #
    def confirm(
        self,
        instance_id: uuid.UUID,
        entity: str,
        field: str,
        semantic_key: str | None = None,
        note: str | None = None,
    ) -> BindingView:
        """Accepte la proposition (semantic_key None) ou confirme une clé du vocabulaire → CONFIRMED."""
        row, spec = self._editable(instance_id, entity, field)
        key = semantic_key or row.semantic_key
        if key is None:
            raise SemanticError(
                f"{entity}.{field} : aucune proposition à confirmer, choisir une clé (change)"
            )
        return self._decide(row, spec, "CONFIRM", key, note)

    def change(
        self, instance_id: uuid.UUID, entity: str, field: str, semantic_key: str, note: str | None = None
    ) -> BindingView:
        """Autre clé du vocabulaire → CONFIRMED ; « other » → UNRESOLVED avec la note (réponse donnée)."""
        row, spec = self._editable(instance_id, entity, field)
        if semantic_key == OTHER_KEY:
            before = row.meaning_status
            self._set_human(
                row,
                status=MeaningStatus.UNRESOLVED,
                key=None,
                relevance=Relevance.POSSIBLE,
                note=note,
                label=None,
            )
            self._event(row, "CHANGE", before, row.last_seen_snapshot_id, utcnow(), note)
            return self._after_decision(row, spec)
        return self._decide(row, spec, "CHANGE", semantic_key, note)

    def ignore(self, instance_id: uuid.UUID, entity: str, field: str, note: str | None = None) -> BindingView:
        """Le supplier exclut le champ du catalogue → IGNORED, NOT_RELEVANT."""
        row, spec = self._editable(instance_id, entity, field)
        before = row.meaning_status
        self._set_human(
            row,
            status=MeaningStatus.IGNORED,
            key=None,
            relevance=Relevance.NOT_RELEVANT,
            note=note,
            label=None,
        )
        self._event(row, "IGNORE", before, row.last_seen_snapshot_id, utcnow(), note)
        return self._after_decision(row, spec)

    def include(
        self, instance_id: uuid.UUID, entity: str, field: str, note: str | None = None
    ) -> BindingView:
        """Contestation d'une exclusion (règle ou supplier) : le champ redevient une question."""
        row, spec = self._editable(instance_id, entity, field)
        if row.relevance != Relevance.NOT_RELEVANT:
            raise SemanticError(f"{entity}.{field} n'est pas exclu : rien à inclure")
        field_def = DomainFieldDef.model_validate(row.field_def)
        proposal = propose_key(field_def, self._entities(instance_id), spec)
        before = row.meaning_status
        self._set_human(
            row,
            status=MeaningStatus.PROPOSED if proposal else MeaningStatus.UNRESOLVED,
            key=proposal.semantic_key if proposal else None,
            relevance=Relevance.POSSIBLE,
            note=note,
            label=None,
        )
        row.confidence = proposal.confidence if proposal else None
        row.pending = True
        row.confirmed_at = None
        row.reason = "Exclusion contested by the supplier"
        self._event(row, "INCLUDE", before, row.last_seen_snapshot_id, utcnow(), note)
        return self._after_decision(row, spec)

    def reopen(self, instance_id: uuid.UUID, entity: str, field: str, note: str | None = None) -> BindingView:
        """Revient sur une réponse du supplier (erreur de saisie) : le champ redevient une question.

        La clé confirmée reste pré-sélectionnée ; sinon (IGNORED, « other ») la proposition des règles.
        La décision reste HUMAN : une nouvelle analyse ne la referme pas."""
        row, spec = self._editable(instance_id, entity, field)
        if row.source != DecisionSource.HUMAN or row.pending:
            raise SemanticError(f"{entity}.{field} : aucune réponse du supplier à rouvrir")
        answer = self._answer(row)
        field_def = DomainFieldDef.model_validate(row.field_def)
        proposal = propose_key(field_def, self._entities(instance_id), spec)
        key = row.semantic_key if row.meaning_status == MeaningStatus.CONFIRMED else None
        if key is None and proposal is not None:
            key = proposal.semantic_key
        before = row.meaning_status
        self._set_human(
            row,
            status=MeaningStatus.PROPOSED if key else MeaningStatus.UNRESOLVED,
            key=key,
            relevance=Relevance.POSSIBLE,
            note=note,
            label=None,
        )
        row.confidence = proposal.confidence if proposal and proposal.semantic_key == key else None
        row.pending = True
        row.previous = answer
        row.reason = "Answer reopened by the supplier"
        self._event(row, "REOPEN", before, row.last_seen_snapshot_id, utcnow(), note)
        return self._after_decision(row, spec)

    def _decide(
        self, row: m.FieldSemanticBinding, spec: SemanticSpec, action: str, key: str, note: str | None
    ) -> BindingView:
        entry = spec.vocabulary.get(key)
        if entry is None:
            raise SemanticError(
                f"semantic_key inconnue '{key}' : choisir une clé du vocabulaire "
                f"{spec.vocabulary.domain_key.value} ou '{OTHER_KEY}'"
            )
        before, proposed = row.meaning_status, row.semantic_key
        self._set_human(
            row,
            status=MeaningStatus.CONFIRMED,
            key=key,
            relevance=Relevance.RELEVANT,
            note=note,
            label=entry.label,
        )
        if key != proposed:
            row.confidence = None  # la confiance était celle de la proposition, pas de la réponse
        self._event(row, action, before, row.last_seen_snapshot_id, utcnow(), note)
        return self._after_decision(row, spec)

    @staticmethod
    def _set_human(
        row: m.FieldSemanticBinding,
        *,
        status: MeaningStatus,
        key: str | None,
        relevance: Relevance,
        note: str | None,
        label: str | None,
    ) -> None:
        now = utcnow()
        row.meaning_status = status.value
        row.semantic_key = key
        row.relevance = relevance.value
        row.source = DecisionSource.HUMAN.value
        row.pending = False
        row.note = note
        row.human_label = label
        row.previous = None
        row.mirror_of = None  # une décision directe détache une copie related
        row.decided_on_snapshot_id = row.last_seen_snapshot_id
        row.confirmed_at = now if status == MeaningStatus.CONFIRMED else None
        row.updated_at = now
        if status != MeaningStatus.PROPOSED:
            row.reason = "Supplier decision"

    def _after_decision(self, row: m.FieldSemanticBinding, spec: SemanticSpec) -> BindingView:
        self.s.flush()
        self._sync_mirrors(self._rows(row.instance_id), row.last_seen_snapshot_id, utcnow())
        self.s.flush()
        return self._view(row, spec.vocabulary)

    def _editable(
        self, instance_id: uuid.UUID, entity: str, field: str
    ) -> tuple[m.FieldSemanticBinding, SemanticSpec]:
        inst = self.domains.instance(instance_id)
        row = self.s.scalar(
            select(m.FieldSemanticBinding).where(
                m.FieldSemanticBinding.instance_id == inst.id,
                m.FieldSemanticBinding.domain_key == self.domain_key.value,
                m.FieldSemanticBinding.entity_technical_name == entity,
                m.FieldSemanticBinding.field_technical_name == field,
            )
        )
        if row is None or row.meaning_status == MeaningStatus.ORPHANED:
            raise NotFound(f"Champ {entity}.{field} absent du domaine analysé de cette instance")
        return row, self._spec(inst)

    # ------------------------------------------------------------------ #
    # Lecture (n'effectue aucun calcul : état persisté)
    # ------------------------------------------------------------------ #
    def get_pending_questions(self, instance_id: uuid.UUID) -> list[BindingView]:
        """Questions ouvertes : extensions (CUSTOM d'abord), puis confiance décroissante."""
        inst = self.domains.instance(instance_id)
        vocab = self._spec(inst).vocabulary
        rows = [
            r
            for r in self._rows(inst.id)
            if r.pending
            and r.meaning_status != MeaningStatus.ORPHANED
            and r.relevance != Relevance.NOT_RELEVANT
        ]
        rows.sort(key=lambda r: (_ORIGIN_ORDER.get(r.origin, 9), -(r.confidence or 0.0), self._path(r)))
        return [self._view(r, vocab) for r in rows]

    def list_exclusions(self, instance_id: uuid.UUID, *, extensions_only: bool = False) -> list[BindingView]:
        """Champs exclus automatiquement (règle), avec leur raison : contestables (include)."""
        inst = self.domains.instance(instance_id)
        vocab = self._spec(inst).vocabulary
        rows = [
            r
            for r in self._rows(inst.id)
            if r.meaning_status == MeaningStatus.EXCLUDED
            and (not extensions_only or r.origin in EXTENSION_ORIGINS)
        ]
        rows.sort(key=lambda r: (_ORIGIN_ORDER.get(r.origin, 9), r.rule_id or "", self._path(r)))
        return [self._view(r, vocab) for r in rows]

    def list_decisions(self, instance_id: uuid.UUID) -> list[BindingView]:
        """Réponses données par le supplier (confirm / change / ignore), la plus récente d'abord :
        modifiables (change, ignore) ou à rouvrir (reopen) en cas d'erreur."""
        inst = self.domains.instance(instance_id)
        vocab = self._spec(inst).vocabulary
        rows = [r for r in self._rows(inst.id) if self._is_answer(r)]
        rows.sort(key=lambda r: (-r.updated_at.timestamp(), self._path(r)))
        return [self._view(r, vocab) for r in rows]

    @staticmethod
    def _is_answer(row: m.FieldSemanticBinding) -> bool:
        return (
            row.source == DecisionSource.HUMAN
            and not row.pending
            and row.meaning_status != MeaningStatus.ORPHANED
        )

    def list_bindings(
        self,
        instance_id: uuid.UUID,
        *,
        status: MeaningStatus | None = None,
        relevance: Relevance | None = None,
        include_orphaned: bool = False,
    ) -> list[BindingView]:
        inst = self.domains.instance(instance_id)
        vocab = self._spec(inst).vocabulary
        rows = [
            r
            for r in self._rows(inst.id)
            if (status is None or r.meaning_status == status)
            and (relevance is None or r.relevance == relevance)
            and (
                include_orphaned
                or status == MeaningStatus.ORPHANED
                or r.meaning_status != MeaningStatus.ORPHANED
            )
        ]
        rows.sort(key=self._path)
        return [self._view(r, vocab) for r in rows]

    def history(self, instance_id: uuid.UUID, entity: str, field: str) -> list[m.FieldSemanticBindingEvent]:
        row, _ = self._editable(instance_id, entity, field)
        return list(row.events)

    def summary(self, instance_id: uuid.UUID) -> CatalogAnalysisSummary:
        stored = self.domains.get_instance_domain(instance_id)
        snap = stored.snapshot
        rows = self._rows(snap.instance_id)
        live = [r for r in rows if r.meaning_status != MeaningStatus.ORPHANED]
        buckets = {
            k: 0 for k in ("pending", "excluded_by_supplier", "excluded_auto", "identified", "possible")
        }
        by_rule: dict[str, int] = {}
        for r in live:
            b = bucket(r)
            buckets[b] += 1
            if b == "excluded_auto":
                by_rule[r.rule_id or "?"] = by_rule.get(r.rule_id or "?", 0) + 1
        ext = [r for r in live if r.origin in EXTENSION_ORIGINS]
        spec = self._spec(self.domains.instance(snap.instance_id))
        return CatalogAnalysisSummary(
            domain_snapshot_id=stored.id,
            instance_schema_snapshot_sequence=stored.instance_schema_snapshot_sequence,
            analyzed_at=snap.analyzed_at,
            rules_version=snap.semantic_rules_version,
            rules_checksum=snap.semantic_rules_checksum,
            enriched=bool(rows),
            total=len(live),
            identified_with_key=sum(1 for r in live if bucket(r) == "identified" and r.semantic_key),
            excluded_auto_by_rule=dict(sorted(by_rule.items())),
            extensions=len(ext),
            extensions_excluded_auto=sum(1 for r in ext if bucket(r) == "excluded_auto"),
            extensions_pending=sum(1 for r in ext if r.pending),
            orphaned=len(rows) - len(live),
            supplier_decisions=sum(1 for r in live if self._is_answer(r)),
            structure=self._structure(snap.entities, snap.fields, spec),
            complete=bool(rows) and buckets["pending"] == 0,
            **buckets,
        )

    def vocabulary(self) -> Vocabulary:
        return self.packs.vocabulary(self.domain_key)

    # ------------------------------------------------------------------ #
    # interne
    # ------------------------------------------------------------------ #
    @staticmethod
    def _structure(
        entities: list[DomainEntityDef], fields: list[DomainFieldDef], spec: SemanticSpec
    ) -> list[StructureCheck]:
        present = {e.role for e in entities if e.present}
        has_media = any(f.present and f.is_media for f in fields)
        return [
            StructureCheck(role=role, present=role in present or (role == EntityRole.MEDIA and has_media))
            for role in spec.structure_checks
        ]

    def _spec(self, inst: m.Instance) -> SemanticSpec:
        spec = self.domains.pack_for_instance(inst).semantics()
        if spec is None:
            raise SemanticError("Le domain pack de cette instance ne fournit pas de règles sémantiques")
        return spec

    def _entities(self, instance_id: uuid.UUID) -> list[DomainEntityDef]:
        return self.domains.get_instance_domain(instance_id).snapshot.entities

    def _rows(self, instance_id: uuid.UUID) -> list[m.FieldSemanticBinding]:
        return list(
            self.s.scalars(
                select(m.FieldSemanticBinding).where(
                    m.FieldSemanticBinding.instance_id == instance_id,
                    m.FieldSemanticBinding.domain_key == self.domain_key.value,
                )
            )
        )

    @staticmethod
    def _path(row: m.FieldSemanticBinding) -> str:
        return f"{row.entity_technical_name}.{row.field_technical_name}"

    def _event(
        self,
        row: m.FieldSemanticBinding,
        action: str,
        from_status: str | None,
        snapshot_id: uuid.UUID | None,
        at: datetime,
        note: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        row.events.append(
            m.FieldSemanticBindingEvent(
                at=at,
                action=action,
                from_status=from_status,
                to_status=row.meaning_status,
                semantic_key=row.semantic_key,
                source=row.source,
                snapshot_id=snapshot_id,
                note=note,
                details=details,
            )
        )

    def _view(self, row: m.FieldSemanticBinding, vocab: Vocabulary) -> BindingView:
        entry = vocab.get(row.semantic_key) if row.semantic_key else None
        return BindingView(
            entity_technical_name=row.entity_technical_name,
            field_technical_name=row.field_technical_name,
            path=self._path(row),
            semantic_key=row.semantic_key,
            semantic_label=entry.label if entry else None,
            relevance=Relevance(row.relevance),
            meaning_status=MeaningStatus(row.meaning_status),
            source=DecisionSource(row.source),
            rule_id=row.rule_id,
            confidence=row.confidence,
            reason=row.reason,
            origin=FieldOrigin(row.origin),
            pending=row.pending,
            mirror_of=row.mirror_of,
            previous=row.previous,
            note=row.note,
            human_label=row.human_label,
            confirmed_at=row.confirmed_at,
            updated_at=row.updated_at,
            field=DomainFieldDef.model_validate(row.field_def),
            justification=row.ai_justification if row.source == DecisionSource.LLM else None,
            low_confidence=bool(row.low_confidence) and row.source == DecisionSource.LLM,
            note_ai=row.ai_note,
        )
