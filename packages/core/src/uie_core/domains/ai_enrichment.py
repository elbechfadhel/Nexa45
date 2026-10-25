"""Proposition sémantique par LLM derrière SemanticEnrichmentService (Product Domain V1c).

    Rules → LLM semantic proposal → Human confirmation when needed.

Le LLM aide à comprendre un champ ; il ne décide JAMAIS de le retirer du catalogue : il ne produit ni
NOT_RELEVANT, ni pertinence, ni exclusion, ni KNOWN. Le seul chemin vers l'exclusion reste une règle ou un
humain. Opération EXPLICITE (`enrich_with_llm`, bouton « Ask AI », CLI, API) : jamais déclenchée par une
analyse, une découverte, une lecture ou l'ouverture d'une page.

Cibles : champs POSSIBLE / UNRESOLVED sans décision humaine, hors copies related (elles suivent leur
origine), hors propositions de règle (la règle prime, sauf force), hors champs déjà analysés avec la même
signature technique et le même prompt (cache, sauf force). Arbitrage d'un lot : deux champs qui visent
la même clé `single` → la plus haute confiance la garde. Résultat, par champ :
    MATCH ≥ 0.85            → PROPOSED, source LLM, justification (question au supplier)
    0.60 ≤ MATCH < 0.85     → PROPOSED, source LLM, low_confidence
    sinon (MATCH < 0.60, NO_CONFIDENT_MATCH, clé invalide, conflit d'unicité, échec)
                            → UNRESOLVED, source inchangée, raison dans ai_note ; question sans suggestion
Chaque appel est journalisé (fournisseur, modèle, checksum du prompt, request id, tokens, latence, coût,
réponse structurée brute). Aucune valeur métier, aucun secret, aucun nom d'instance n'est envoyé.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

from uie_core.db.base import utcnow
from uie_core.domains.model import DomainEntityDef, DomainFieldDef, EntityRole, Relevance
from uie_core.domains.pack import DomainPackError
from uie_core.domains.proposer import (
    FieldMetadata,
    PromptSpec,
    ProposalMeta,
    ProposalOutcome,
    ProposalRequest,
    ProposalResult,
    VocabularyChoice,
)
from uie_core.domains.semantics import (
    DecisionSource,
    MeaningStatus,
    SemanticSpec,
    compatible_keys,
    selection_values,
)
from uie_core.registry import models as m
from uie_core.registry.service import NotFound

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from uie_core.domains.proposer import SemanticProposer
    from uie_core.domains.service import ProductDomainService

log = logging.getLogger(__name__)

HIGH_CONFIDENCE = 0.85
LOW_CONFIDENCE = 0.60
MAX_FIELDS = 50
AI_UNSURE = "AI could not identify this field with confidence"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, json_schema_serialization_defaults_required=True)


AiOutcomeKind = Literal["PROPOSED", "LOW_CONFIDENCE", "NO_CONFIDENT_MATCH", "ERROR", "SKIPPED"]


class AiFieldOutcome(_Model):
    path: str
    outcome: AiOutcomeKind
    semantic_key: str | None = None
    confidence: float | None = None
    reason: str | None = None


class AiEnrichmentResult(_Model):
    provider: str
    model: str | None
    prompt_version: str
    prompt_checksum: str
    targets: int  # champs envoyés (ou résolus sans appel : aucune clé compatible)
    proposed: int  # MATCH ≥ 0.85
    low_confidence: int  # 0.60 ≤ MATCH < 0.85
    no_confident_match: int
    errors: int  # échec réseau / timeout / réponse invalide (comptés à part, jamais une exception)
    skipped: int
    fields: list[AiFieldOutcome]


class AiStatus(_Model):
    provider: str  # "none" : aucun fournisseur configuré
    model: str | None
    configured: bool
    prompt_version: str | None
    candidates: int  # champs que « Ask AI » enverrait maintenant (sans force)
    ai_proposed: int  # questions portant une proposition du LLM
    ai_unresolved: int  # questions sans suggestion après passage du LLM
    calls: int
    last_call_at: datetime | None
    input_tokens: int
    output_tokens: int
    cost_usd: float | None


class AiEnrichmentMixin:
    """Partie V1c de SemanticEnrichmentService (mêmes attributs et helpers internes)."""

    s: Session
    proposer: SemanticProposer
    domains: ProductDomainService

    # ------------------------------------------------------------------ #
    def enrich_with_llm(
        self,
        instance_id: uuid.UUID,
        fields: list[tuple[str, str]] | None = None,
        *,
        force: bool = False,
        max_fields: int = MAX_FIELDS,
    ) -> AiEnrichmentResult:
        from uie_core.domains.enrichment import SemanticError  # import circulaire sinon

        inst = self.domains.instance(instance_id)  # d'abord l'accès (V1d) : autre tenant → 404, jamais 400
        if self.proposer.provider == "none":
            raise SemanticError("No AI provider configured (UIE_LLM_PROVIDER)")
        spec: SemanticSpec = self._spec(inst)  # type: ignore[attr-defined]
        prompt = self.packs.prompt(self.domain_key)  # type: ignore[attr-defined]
        entities = self._entities(inst.id)  # type: ignore[attr-defined]
        rows = [r for r in self._rows(inst.id) if r.meaning_status != MeaningStatus.ORPHANED]  # type: ignore[attr-defined]
        by_path = {self._path(r): r for r in rows}  # type: ignore[attr-defined]

        outcomes: list[AiFieldOutcome] = []
        wanted = None if fields is None else [f"{e}.{f}" for e, f in fields]
        for path in wanted or []:
            if path not in by_path:
                raise NotFound(f"Champ {path} absent du domaine analysé de cette instance")
        targets = []
        for path, row in sorted(by_path.items()):
            if wanted is not None and path not in wanted:
                continue
            skip = self._ai_skip_reason(row, prompt.checksum, force)
            if skip:
                if wanted is not None:
                    outcomes.append(AiFieldOutcome(path=path, outcome="SKIPPED", reason=skip))
                continue
            targets.append(row)
        for row in targets[max_fields:]:
            outcomes.append(
                AiFieldOutcome(path=self._path(row), outcome="SKIPPED", reason="budget (max_fields)")
            )  # type: ignore[attr-defined]
        targets = targets[:max_fields]

        held = self._held_keys(rows, spec, exclude={self._path(r) for r in targets})  # type: ignore[attr-defined]
        ref = hashlib.sha256(str(inst.id).encode()).hexdigest()[:12]  # opaque : jamais le nom
        ver = inst.interface.version
        system, version = ver.product.system.code, ver.version
        answered: list[tuple[m.FieldSemanticBinding, ProposalRequest, ProposalResult]] = []
        for row in targets:
            request = self._ai_request(row, entities, spec, prompt, held, ref, system, version)
            if not request.vocabulary:  # aucune clé compatible : inutile d'appeler le LLM
                result = ProposalResult(
                    outcome=ProposalOutcome.NO_CONFIDENT_MATCH,
                    reason="No vocabulary key is compatible with this field type",
                    meta=ProposalMeta(provider=self.proposer.provider, model=self.proposer.model),
                )
            else:
                result = self._call(request)
            answered.append((row, request, result))
        # arbitrage d'un lot : les MATCH utilisables d'abord, par confiance décroissante ; deux champs qui
        # visent la même clé `single` → la plus haute confiance la garde, l'autre devient un conflit
        for row, request, result in sorted(answered, key=_arbitration_order):
            outcomes.append(self._apply_ai(row, request, result, held, spec, called=bool(request.vocabulary)))
        outcomes.sort(key=lambda o: o.path)

        self.s.flush()
        self._sync_mirrors(self._rows(inst.id), None, utcnow())  # type: ignore[attr-defined]
        self.s.flush()
        count = {k: sum(1 for o in outcomes if o.outcome == k) for k in AiOutcomeKind.__args__}  # type: ignore[attr-defined]
        log.info("enrich_with_llm instance=%s provider=%s %s", inst.id, self.proposer.provider, count)
        return AiEnrichmentResult(
            provider=self.proposer.provider,
            model=self.proposer.model,
            prompt_version=prompt.version,
            prompt_checksum=prompt.checksum,
            targets=len(targets),
            proposed=count["PROPOSED"],
            low_confidence=count["LOW_CONFIDENCE"],
            no_confident_match=count["NO_CONFIDENT_MATCH"],
            errors=count["ERROR"],
            skipped=count["SKIPPED"],
            fields=outcomes,
        )

    def ai_status(self, instance_id: uuid.UUID) -> AiStatus:
        inst = self.domains.instance(instance_id)
        rows = [r for r in self._rows(inst.id) if r.meaning_status != MeaningStatus.ORPHANED]  # type: ignore[attr-defined]
        try:
            prompt: PromptSpec | None = self.packs.prompt(self.domain_key)  # type: ignore[attr-defined]
        except DomainPackError:
            prompt = None
        candidates = sum(1 for r in rows if prompt and not self._ai_skip_reason(r, prompt.checksum, False))
        ev = m.FieldSemanticBindingEvent
        calls = self.s.execute(
            select(
                func.count(ev.id),
                func.max(ev.at),
                func.sum(ev.details["input_tokens"].as_integer()),
                func.sum(ev.details["output_tokens"].as_integer()),
                func.sum(ev.details["cost_usd"].as_float()),
            )
            .join(m.FieldSemanticBinding, m.FieldSemanticBinding.id == ev.binding_id)
            .where(m.FieldSemanticBinding.instance_id == inst.id, ev.action.in_(AI_CALL_ACTIONS))
        ).one()
        return AiStatus(
            provider=self.proposer.provider,
            model=self.proposer.model,
            configured=self.proposer.provider != "none",
            prompt_version=prompt.version if prompt else None,
            candidates=candidates,
            ai_proposed=sum(1 for r in rows if r.source == DecisionSource.LLM and r.pending),
            ai_unresolved=sum(
                1 for r in rows if r.pending and r.ai_note and r.meaning_status == MeaningStatus.UNRESOLVED
            ),
            calls=calls[0] or 0,
            last_call_at=calls[1],
            input_tokens=int(calls[2] or 0),
            output_tokens=int(calls[3] or 0),
            cost_usd=round(float(calls[4]), 6) if calls[4] is not None else None,
        )

    # ------------------------------------------------------------------ #
    # interne
    # ------------------------------------------------------------------ #
    @staticmethod
    def _ai_skip_reason(row: m.FieldSemanticBinding, prompt_checksum: str, force: bool) -> str | None:
        if row.source == DecisionSource.HUMAN:
            return "human decision"
        if row.mirror_of:
            return "related copy: follows its source"
        if row.relevance == Relevance.NOT_RELEVANT:
            return "excluded"
        if row.meaning_status not in (MeaningStatus.UNRESOLVED, MeaningStatus.PROPOSED):
            return f"already {row.meaning_status.lower()}"
        if row.meaning_status == MeaningStatus.PROPOSED and row.source != DecisionSource.LLM and not force:
            return "rule proposal takes precedence"
        if (
            not force
            and row.ai_prompt_checksum == prompt_checksum
            and row.ai_signature == row.technical_signature
        ):
            return "already analysed by AI (same field, same prompt)"
        return None

    def _keeps_ai(self, row: m.FieldSemanticBinding, decision: Any, signature: dict[str, Any]) -> bool:
        """Ré-analyse : la proposition (ou l'abstention) du LLM tient tant que le champ n'a pas changé et
        que la règle ne décide rien de plus sûr (exclusion, clé, copie related)."""
        return (
            row.ai_signature == signature
            and decision.relevance != Relevance.NOT_RELEVANT
            and decision.semantic_key is None
            and decision.mirror_of is None
            and decision.status == MeaningStatus.UNRESOLVED
        )

    @staticmethod
    def _clear_ai(row: m.FieldSemanticBinding) -> None:
        row.ai_justification = None
        row.ai_note = None
        row.low_confidence = False
        row.ai_prompt_checksum = None
        row.ai_signature = None

    @staticmethod
    def _held_keys(
        rows: list[m.FieldSemanticBinding], spec: SemanticSpec, exclude: set[str] | None = None
    ) -> dict[str, dict[str, str]]:
        """Clés `single` déjà tenues par entité : clé → champ. Tenue = KNOWN, CONFIRMED, ou proposée par le
        LLM hors du lot en cours (`exclude` : les champs qui vont être re-décidés)."""
        held: dict[str, dict[str, str]] = {}
        for r in rows:
            if exclude and f"{r.entity_technical_name}.{r.field_technical_name}" in exclude:
                continue
            entry = spec.vocabulary.get(r.semantic_key) if r.semantic_key else None
            holds = r.meaning_status in (MeaningStatus.KNOWN, MeaningStatus.CONFIRMED) or (
                r.meaning_status == MeaningStatus.PROPOSED and r.source == DecisionSource.LLM
            )
            if entry and entry.cardinality == "single" and holds:
                held.setdefault(r.entity_technical_name, {})[entry.key] = (
                    f"{r.entity_technical_name}.{r.field_technical_name}"
                )
        return held

    def _ai_request(
        self,
        row: m.FieldSemanticBinding,
        entities: list[DomainEntityDef],
        spec: SemanticSpec,
        prompt: PromptSpec,
        held: dict[str, dict[str, str]],
        ref: str,
        system: str,
        version: str,
    ) -> ProposalRequest:
        f = DomainFieldDef.model_validate(row.field_def)
        roles: dict[str, EntityRole] = {e.technical_name: e.role for e in entities if e.present}
        return ProposalRequest(
            request_ref=ref,
            domain_key=spec.domain_key,
            system=system,
            version=version,
            entity_role=roles.get(f.entity_technical_name),
            field=FieldMetadata(
                entity=f.entity_technical_name,
                technical_name=f.field_technical_name,
                label=f.label,
                help=f.help,
                native_type=f.native_type,
                data_type=f.data_type,
                required=f.required,
                stored=f.stored,
                computed=f.computed,
                related=f.related,
                relation_target=f.relation_target,
                relation_target_role=roles.get(f.relation_target or ""),
                selection_values=selection_values(f.selection),
                modules=f.modules,
                origin=f.origin,
            ),
            vocabulary=[
                VocabularyChoice(
                    key=k.key,
                    label=k.label,
                    description=k.description,
                    group=k.group.value,
                    data_type=k.data_type.value,
                    expected_types=[t.value for t in k.expected_types],
                )
                for k in compatible_keys(f, entities, spec)
            ],
            keys_held_on_entity=dict(sorted(held.get(f.entity_technical_name, {}).items())),
            prompt=prompt,
        )

    def _call(self, request: ProposalRequest) -> ProposalResult:
        """Le contrat interdit de lever ; protection quand même : jamais d'exception jusqu'au supplier."""
        try:
            return self.proposer.propose(request)
        except Exception as exc:  # noqa: BLE001
            meta = ProposalMeta(provider=self.proposer.provider, model=self.proposer.model)
            return ProposalResult.failure(f"{type(exc).__name__}", meta)

    def _apply_ai(
        self,
        row: m.FieldSemanticBinding,
        request: ProposalRequest,
        result: ProposalResult,
        held: dict[str, dict[str, str]],
        spec: SemanticSpec,
        *,
        called: bool = True,
    ) -> AiFieldOutcome:
        now = utcnow()
        path = request.path
        before = row.meaning_status
        transport_error = result.error is not None and result.raw is None
        key, conf = result.semantic_key, result.confidence
        conflict = None
        usable = result.outcome == ProposalOutcome.MATCH and conf is not None and conf >= LOW_CONFIDENCE
        if usable and key:
            entry = spec.vocabulary.get(key)
            holder = (
                held.get(row.entity_technical_name, {}).get(key)
                if entry and entry.cardinality == "single"
                else None
            )
            conflict = holder if holder and holder != path else None

        details = {
            **result.meta.model_dump(mode="json"),
            "request_ref": request.request_ref,
            "prompt_version": request.prompt.version,
            "prompt_checksum": request.prompt.checksum,
            "outcome": result.outcome.value,
            "semantic_key": key,
            "confidence": conf,
            "raw": result.raw,
            "ignored_fields": result.ignored_fields,
            "error": result.error,
        }
        if transport_error and row.source == DecisionSource.LLM:
            # une proposition valable n'est jamais effacée par un échec réseau
            self._event(row, "AI_ERROR", before, row.last_seen_snapshot_id, now, result.error, details)  # type: ignore[attr-defined]
            return AiFieldOutcome(path=path, outcome="ERROR", reason=result.error)

        if usable and not conflict:
            low = conf < HIGH_CONFIDENCE  # type: ignore[operator]
            row.meaning_status = MeaningStatus.PROPOSED.value
            row.source = DecisionSource.LLM.value
            row.semantic_key = key
            row.confidence = conf
            row.ai_justification = result.justification
            row.low_confidence = low
            row.ai_note = None
            row.reason = "Suggested by AI"
            entry = spec.vocabulary.get(key or "")
            if entry and entry.cardinality == "single":
                held.setdefault(row.entity_technical_name, {})[entry.key] = path
            kind: AiOutcomeKind = "LOW_CONFIDENCE" if low else "PROPOSED"
            action, note = "AI_PROPOSE", None
        else:
            if conflict:
                note = f"Conflicts with {conflict} ({key})"
            elif result.error:
                note = (
                    "AI could not answer"
                    if transport_error
                    else "AI answer rejected (key outside the vocabulary or invalid format)"
                )
            elif result.outcome == ProposalOutcome.MATCH:
                note = f"Confidence too low ({conf:.0%})"
            else:
                note = result.reason or AI_UNSURE
            row.meaning_status = MeaningStatus.UNRESOLVED.value
            if row.source == DecisionSource.LLM:  # ancienne proposition remplacée : retour à la règle
                row.source = DecisionSource.RULE.value
            row.semantic_key = None
            row.confidence = None
            row.ai_justification = None
            row.low_confidence = False
            row.ai_note = note
            kind = "ERROR" if result.error else "NO_CONFIDENT_MATCH"
            action = "AI_ERROR" if result.error else ("AI_NO_MATCH" if called else "AI_NO_KEY")
        row.pending = True  # le LLM ne tranche jamais : une question au supplier, avec ou sans suggestion
        row.updated_at = now
        if not transport_error:  # une réponse du modèle (même invalide) est mise en cache ; un échec non
            row.ai_prompt_checksum = request.prompt.checksum
            row.ai_signature = dict(row.technical_signature)
        self._event(row, action, before, row.last_seen_snapshot_id, now, note, details)  # type: ignore[attr-defined]
        if result.ignored_fields:
            self._event(  # type: ignore[attr-defined]
                row,
                "AI_IGNORED",
                row.meaning_status,
                row.last_seen_snapshot_id,
                now,
                f"Ignored answer fields: {', '.join(result.ignored_fields)}",
                {"ignored_fields": result.ignored_fields, "raw": result.raw},
            )
        return AiFieldOutcome(
            path=path,
            outcome=kind,
            semantic_key=row.semantic_key,
            confidence=row.confidence,
            reason=note or result.justification,
        )


AI_CALL_ACTIONS = ("AI_PROPOSE", "AI_NO_MATCH", "AI_ERROR")


def _arbitration_order(item: tuple[m.FieldSemanticBinding, ProposalRequest, ProposalResult]) -> tuple:
    _, request, result = item
    usable = result.outcome == ProposalOutcome.MATCH and (result.confidence or 0) >= LOW_CONFIDENCE
    return (0 if usable else 1, -(result.confidence or 0), request.path)
