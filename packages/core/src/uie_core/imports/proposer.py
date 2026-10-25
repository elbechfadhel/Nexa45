"""Contrat du fallback LLM des imports de catalogue (V2c.3) : le Core ne connaît que ceci.

    EMPTY → NOT_RELEVANT appris → mapping appris → règles → LLM (colonnes restantes seulement)

UN appel par lot de colonnes encore ambiguës (jamais le fichier) : en-tête, type détecté, taux de
remplissage, au plus 3 valeurs d'exemple MASQUÉES, et les cibles réellement offertes par le catalogue du
système. Réponse par colonne : TARGET (une clé offerte), NOT_RELEVANT ou UNKNOWN, une confiance, une raison
courte. La validation se fait ICI, pour tous les fournisseurs : une clé hors des cibles offertes, une
confiance invalide ou une colonne sans réponse deviennent UNKNOWN ; un échec rend tout le lot UNKNOWN,
jamais une exception. Le LLM propose ; seul le Core décide de ce qu'il en fait (seuils, priorités), et
seule une décision humaine alimente la mémoire.

Les implémentations (Null, Replay, Recording, OpenAI, Anthropic) vivent dans `uie_ai`.
"""

from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from uie_core.domains.proposer import PromptSpec, ProposalMeta

IMPORT_PROMPT = "import_columns"  # nom du prompt dans le domain pack (catalog/llm/*.yaml)
MAX_EXAMPLES = 3
MAX_EXAMPLE_LENGTH = 40
MAX_REASON = 120
BATCH_SIZE = 60  # colonnes par appel (WooCommerce : 29 → un seul appel)
COLUMN_KEYS = frozenset({"position", "source_header", "decision", "semantic_key", "confidence", "reason"})

# masquage GÉNÉRIQUE des valeurs d'exemple (motifs, jamais une liste de valeurs)
_MASKS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+"), "<email>"),
    (re.compile(r"(https?://|www\.)\S+", re.IGNORECASE), "<url>"),
]
_DIGITS = re.compile(r"(?:\b[A-Z]{2})?\+?\d[\d\s().-]{7,}\d")  # téléphone, IBAN, carte, code-barres...
_TAGS = re.compile(r"<[^>]{1,200}>")


def _long_number(m: re.Match[str]) -> str:
    # au moins 9 chiffres : une date (2024-01-15) ou un prix restent lisibles
    return "<number>" if sum(ch.isdigit() for ch in m.group(0)) >= 9 else m.group(0)


def mask_example(value: str) -> str:
    """Valeur d'exemple envoyée au LLM : balises HTML retirées, motifs sensibles masqués, tronquée."""
    text = _TAGS.sub(" ", value)
    for pattern, token in _MASKS:
        text = pattern.sub(token, text)
    text = _DIGITS.sub(_long_number, text)
    text = " ".join(text.split())
    return text[:MAX_EXAMPLE_LENGTH]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ColumnDecision(StrEnum):
    TARGET = "TARGET"
    NOT_RELEVANT = "NOT_RELEVANT"
    UNKNOWN = "UNKNOWN"


class CandidateColumn(_Model):
    position: int
    header: str
    detected_type: str
    fill_ratio: float  # 0..1
    examples: list[str] = []  # masquées (`mask_example`), jamais écrites en base
    rules_suggestion: str | None = None  # semantic_key suggérée (faiblement) par les règles


class TargetChoice(_Model):
    """Une cible offerte (celles de « Choose target ») : jamais un nom technique du système."""

    key: str
    label: str
    description: str
    group: str  # product | supplier
    data_type: str


class ColumnProposalRequest(_Model):
    request_ref: str  # opaque (corrélation), jamais l'organisation, l'instance ni le fichier
    domain: str
    system: str
    version: str
    columns: list[CandidateColumn]
    targets: list[TargetChoice]
    prompt: PromptSpec

    @property
    def allowed_keys(self) -> frozenset[str]:
        return frozenset(t.key for t in self.targets)

    def column_checksum(self, column: CandidateColumn, provider: str, model: str | None) -> str:
        """Empreinte du cache PAR COLONNE : même prompt, fournisseur / modèle, contexte et cibles, et même
        colonne (en-tête, type, remplissage, exemples masqués, suggestion des règles ; position exclue)."""
        payload = {
            "prompt": self.prompt.checksum,
            "provider": provider,
            "model": model,
            "context": [self.domain, self.system, self.version],
            "targets": [t.model_dump(mode="json") for t in self.targets],
            "column": column.model_dump(mode="json", exclude={"position"}),
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ColumnProposal(_Model):
    position: int
    decision: ColumnDecision
    semantic_key: str | None = None  # TARGET seulement
    confidence: float = Field(default=0.0, ge=0, le=1)
    reason: str | None = None  # debug / Admin, jamais affichée au fournisseur


class ColumnProposalBatch(_Model):
    proposals: list[ColumnProposal] = []  # une par colonne demandée, dans l'ordre de la requête
    raw: dict[str, Any] | None = None  # réponse structurée brute (journal)
    ignored_fields: list[str] = []  # clés hors contrat d'une réponse de colonne
    errors: list[str] = []  # colonnes invalidées (clé hors cibles, confiance invalide...)
    error: str | None = None  # échec du lot (réseau, timeout, JSON illisible) : non mis en cache
    meta: ProposalMeta

    def by_position(self) -> dict[int, ColumnProposal]:
        return {p.position: p for p in self.proposals}

    @classmethod
    def failure(
        cls, error: str, meta: ProposalMeta, request: ColumnProposalRequest, raw: dict[str, Any] | None = None
    ) -> ColumnProposalBatch:
        unknown = [
            ColumnProposal(position=c.position, decision=ColumnDecision.UNKNOWN, reason="AI could not answer")
            for c in request.columns
        ]
        return cls(proposals=unknown, raw=raw, error=error[:500], meta=meta)

    @classmethod
    def from_raw(cls, raw: Any, meta: ProposalMeta, request: ColumnProposalRequest) -> ColumnProposalBatch:
        """Validation commune de la réponse d'un fournisseur (ou d'un replay)."""
        if not isinstance(raw, dict) or not isinstance(raw.get("columns"), list):
            return cls.failure('réponse structurée attendue : {"columns": [...]}', meta, request)
        asked = {c.position for c in request.columns}
        answers: dict[int, ColumnProposal] = {}
        errors: list[str] = []
        ignored: set[str] = set()
        for item in raw["columns"]:
            if not isinstance(item, dict):
                continue
            ignored |= {k for k in item if k not in COLUMN_KEYS}
            pos = item.get("position")
            if isinstance(pos, bool) or not isinstance(pos, int) or pos not in asked or pos in answers:
                continue
            answers[pos] = _validate(item, pos, request, errors)
        proposals = [
            answers.get(c.position)
            or ColumnProposal(position=c.position, decision=ColumnDecision.UNKNOWN, reason="No answer")
            for c in request.columns
        ]
        return cls(proposals=proposals, raw=raw, ignored_fields=sorted(ignored), errors=errors, meta=meta)


def _validate(
    item: dict[str, Any], pos: int, request: ColumnProposalRequest, errors: list[str]
) -> ColumnProposal:
    reason = item.get("reason")
    reason = reason.strip()[:MAX_REASON] if isinstance(reason, str) and reason.strip() else None
    confidence = item.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, int | float) or not 0 <= confidence <= 1:
        errors.append(f"{pos}: confidence invalide {confidence!r}")
        return ColumnProposal(position=pos, decision=ColumnDecision.UNKNOWN, reason=reason)
    confidence = round(float(confidence), 3)  # 3 décimales : 0.949 reste sous le seuil AUTO 0.95 (V2c.5b)
    decision = item.get("decision")
    if decision == ColumnDecision.NOT_RELEVANT:
        return ColumnProposal(
            position=pos, decision=ColumnDecision.NOT_RELEVANT, confidence=confidence, reason=reason
        )
    if decision == ColumnDecision.TARGET:
        key = item.get("semantic_key")
        if key in request.allowed_keys:
            return ColumnProposal(
                position=pos,
                decision=ColumnDecision.TARGET,
                semantic_key=key,
                confidence=confidence,
                reason=reason,
            )
        errors.append(f"{pos}: semantic_key hors des cibles offertes {key!r}")  # jamais une cible inventée
    elif decision != ColumnDecision.UNKNOWN:
        errors.append(f"{pos}: decision inattendue {decision!r}")
    return ColumnProposal(position=pos, decision=ColumnDecision.UNKNOWN, confidence=confidence, reason=reason)


@runtime_checkable
class ColumnMappingProposer(Protocol):
    """Propose une décision pour chaque colonne d'un lot. Ne lève jamais : un échec rend UNKNOWN."""

    provider: str  # "none" = aucun fournisseur configuré : aucun appel
    model: str | None

    def propose_columns(self, request: ColumnProposalRequest) -> ColumnProposalBatch: ...
