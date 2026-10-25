"""Mise en forme commune d'une ProposalRequest pour un fournisseur (texte système, message, schéma).

Le contenu du prompt (instructions, exemples) vient du domain pack (`request.prompt`) ; ce module ne fait
que l'assembler. Le schéma de réponse contraint la sortie : `outcome` ∈ {MATCH, NO_CONFIDENT_MATCH} et
`semantic_key` ∈ les clés PROPOSÉES pour ce champ (ou "" sans correspondance). La validation finale reste
celle du Core (`ProposalResult.from_raw`).
"""

from __future__ import annotations

import json
from typing import Any

from uie_core.domains import ProposalRequest
from uie_core.imports import ColumnProposalRequest

SCHEMA_NAME = "semantic_proposal"


def system_text(request: ProposalRequest) -> str:
    parts = [request.prompt.system.strip()]
    if request.prompt.few_shots:
        parts.append("Examples (input → expected answer):")
        for shot in request.prompt.few_shots:
            parts.append(json.dumps(shot, ensure_ascii=False, sort_keys=True))
    return "\n\n".join(parts)


def user_text(request: ProposalRequest) -> str:
    """Le champ à comprendre : métadonnées techniques + clés proposables. Jamais de valeur métier."""
    payload = {
        "context": {"domain": request.domain_key.value, "system": request.system, "version": request.version},
        "entity_role": request.entity_role.value if request.entity_role else None,
        "field": request.field.model_dump(mode="json", exclude_none=True),
        "vocabulary": [v.model_dump(mode="json") for v in request.vocabulary],
        "keys_already_used_on_this_entity": request.keys_held_on_entity,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def answer_schema(request: ProposalRequest) -> dict[str, Any]:
    keys = sorted(request.allowed_keys)
    return {
        "type": "object",
        "properties": {
            "outcome": {"type": "string", "enum": ["MATCH", "NO_CONFIDENT_MATCH"]},
            "semantic_key": {"type": "string", "enum": [*keys, ""]},
            "confidence": {"type": "number"},
            "justification": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": ["outcome", "semantic_key", "confidence", "justification", "reason"],
        "additionalProperties": False,
    }


def estimate_cost(
    input_tokens: int | None, output_tokens: int | None, price_in: float | None, price_out: float | None
) -> float | None:
    """Coût estimé en USD (prix par million de tokens) ; None si un élément manque."""
    if None in (input_tokens, output_tokens, price_in, price_out):
        return None
    return round((input_tokens * price_in + output_tokens * price_out) / 1_000_000, 6)  # type: ignore[operator]


# --------------------------------------------------------------------------- #
# Colonnes d'un import de catalogue (V2c.3) : un lot, une réponse par colonne
# --------------------------------------------------------------------------- #
COLUMNS_SCHEMA_NAME = "import_column_mapping"


def columns_system_text(request: ColumnProposalRequest) -> str:
    parts = [request.prompt.system.strip()]
    if request.prompt.few_shots:
        parts.append("Examples (input → expected answer):")
        for shot in request.prompt.few_shots:
            parts.append(json.dumps(shot, ensure_ascii=False, sort_keys=True))
    return "\n\n".join(parts)


def columns_user_text(request: ColumnProposalRequest) -> str:
    """Les colonnes ambiguës (exemples déjà masqués par le Core) et les cibles offertes. Rien d'autre."""
    payload = {
        "context": {"domain": request.domain, "system": request.system, "version": request.version},
        "columns": [c.model_dump(mode="json", exclude_none=True) for c in request.columns],
        "targets": [t.model_dump(mode="json") for t in request.targets],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def minimal_columns_schema(request: ColumnProposalRequest) -> dict[str, Any]:
    """Réponse MINIMALE (assistant de migration) : position, décision, clé, confiance ; ni l'en-tête
    recopié, ni d'explication (moins de jetons générés, même validation `ColumnProposalBatch.from_raw`)."""
    keys = sorted(request.allowed_keys)
    column = {
        "type": "object",
        "properties": {
            "position": {"type": "integer"},
            "decision": {"type": "string", "enum": ["TARGET", "NOT_RELEVANT", "UNKNOWN"]},
            "semantic_key": {"type": "string", "enum": [*keys, ""]},
            "confidence": {"type": "number"},
        },
        "required": ["position", "decision", "semantic_key", "confidence"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"columns": {"type": "array", "items": column}},
        "required": ["columns"],
        "additionalProperties": False,
    }


def columns_schema(request: ColumnProposalRequest) -> dict[str, Any]:
    keys = sorted(request.allowed_keys)
    column = {
        "type": "object",
        "properties": {
            "position": {"type": "integer"},
            "source_header": {"type": "string"},
            "decision": {"type": "string", "enum": ["TARGET", "NOT_RELEVANT", "UNKNOWN"]},
            "semantic_key": {"type": "string", "enum": [*keys, ""]},
            "confidence": {"type": "number"},
            "reason": {"type": "string"},
        },
        "required": ["position", "source_header", "decision", "semantic_key", "confidence", "reason"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"columns": {"type": "array", "items": column}},
        "required": ["columns"],
        "additionalProperties": False,
    }
