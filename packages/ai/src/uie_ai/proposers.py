"""Proposers sans réseau : Null (aucun fournisseur), Replay (réponses enregistrées), Recording (enregistre).

Format d'un fichier de replay (`uie-llm-replay/1`) :
    {"format": "uie-llm-replay/1", "provider": ..., "model": ..., "prompt_version": ...,
     "prompt_checksum": ..., "recorded_at": ...,
     "responses": {"<entité>.<champ>": {"raw": {...} | null, "error": str | null, "meta": {...}}},
     "import_columns": {"<en-tête normalisé>": {"decision": ..., "semantic_key": ..., "confidence": ...,
                        "reason": ...}},          (V2c.3, colonnes d'un import, facultatif)
     "import_meta": {...}}
Les réponses brutes sont rejouées à travers la MÊME validation que celles d'un fournisseur réel
(`ProposalResult.from_raw`) : un replay ne contourne aucun garde-fou.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from uie_core.domains import (
    ProposalMeta,
    ProposalOutcome,
    ProposalRequest,
    ProposalResult,
    SemanticProposer,
)
from uie_core.imports import ColumnProposalBatch, ColumnProposalRequest, header_key

REPLAY_FORMAT = "uie-llm-replay/1"


class NullProposer:
    """Aucun fournisseur : jamais de proposition (défaut sans clé API)."""

    provider = "none"
    model: str | None = None

    def propose(self, request: ProposalRequest) -> ProposalResult:
        return ProposalResult(
            outcome=ProposalOutcome.NO_CONFIDENT_MATCH,
            reason="No AI provider configured",
            meta=ProposalMeta(provider=self.provider),
        )

    def propose_columns(self, request: ColumnProposalRequest) -> ColumnProposalBatch:
        return ColumnProposalBatch.failure(
            "No AI provider configured", ProposalMeta(provider=self.provider), request
        )


class ReplayProposer:
    """Rejoue un fichier enregistré (tests, démo hors-ligne, reproductibilité). Champ absent du fichier →
    NO_CONFIDENT_MATCH (erreur « not recorded »)."""

    provider = "replay"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if data.get("format") != REPLAY_FORMAT:
            raise ValueError(f"{self.path} : format de replay attendu {REPLAY_FORMAT}")
        self.recorded_provider: str | None = data.get("provider")
        self.model: str | None = data.get("model")
        self.prompt_checksum: str | None = data.get("prompt_checksum")
        self.responses: dict[str, dict[str, Any]] = data.get("responses", {})
        self.import_columns: dict[str, dict[str, Any]] = data.get("import_columns", {})
        self.import_meta: dict[str, Any] = data.get("import_meta", {})
        self.calls: list[ProposalRequest] = []
        self.column_calls: list[ColumnProposalRequest] = []

    def propose(self, request: ProposalRequest) -> ProposalResult:
        self.calls.append(request)
        recorded = self.responses.get(request.path)
        meta = ProposalMeta(provider=self.provider, model=self.model)
        if recorded is None:
            return ProposalResult.failure(f"not recorded in {self.path.name}", meta)
        meta = ProposalMeta.model_validate({**recorded.get("meta", {}), "provider": self.provider})
        if recorded.get("error"):
            return ProposalResult.failure(recorded["error"], meta, recorded.get("raw"))
        return ProposalResult.from_raw(recorded.get("raw"), meta, request)

    def propose_columns(self, request: ColumnProposalRequest) -> ColumnProposalBatch:
        """Réponse enregistrée par en-tête normalisé ; colonne absente du fichier → UNKNOWN."""
        self.column_calls.append(request)
        meta = ProposalMeta.model_validate({**self.import_meta, "provider": self.provider})
        items = [
            {**recorded, "position": c.position, "source_header": c.header}
            for c in request.columns
            if (recorded := self.import_columns.get(header_key(c.header))) is not None
        ]
        return ColumnProposalBatch.from_raw({"columns": items}, meta, request)


class RecordingProposer:
    """Enveloppe un vrai proposer et enregistre chaque réponse brute dans un fichier de replay."""

    def __init__(self, inner: SemanticProposer, path: str | Path) -> None:
        self.inner = inner
        self.path = Path(path)
        self.provider = inner.provider
        self.model = inner.model
        self._data: dict[str, Any] = {
            "format": REPLAY_FORMAT,
            "provider": inner.provider,
            "model": inner.model,
            "prompt_version": None,
            "prompt_checksum": None,
            "recorded_at": None,
            "responses": {},
        }
        self.column_calls = 0
        self.wizard_calls: list[str] = []  # appels de l'assistant de migration, dans l'ordre (sorte)

    def propose(self, request: ProposalRequest) -> ProposalResult:
        result = self.inner.propose(request)
        self._data["prompt_version"] = request.prompt.version
        self._data["prompt_checksum"] = request.prompt.checksum
        self._data["recorded_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        self._data["responses"][request.path] = {
            "raw": result.raw,
            "error": result.error if result.raw is None else None,
            "meta": result.meta.model_dump(mode="json", exclude={"provider"}),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        return result

    def propose_columns(self, request: ColumnProposalRequest) -> ColumnProposalBatch:
        result = self.inner.propose_columns(request)  # type: ignore[attr-defined]
        self.column_calls += 1
        headers = {c.position: c.header for c in request.columns}
        section = self._data.setdefault("import_columns", {})
        for item in (result.raw or {}).get("columns", []):
            if isinstance(item, dict) and item.get("position") in headers:
                answer = {k: v for k, v in item.items() if k not in ("position", "source_header")}
                section[header_key(headers[item["position"]])] = answer
        meta = self._data.setdefault("import_meta", {})
        for k, v in result.meta.model_dump(mode="json", exclude={"provider"}).items():
            if isinstance(v, int | float) and not isinstance(v, bool) and k != "latency_ms":
                meta[k] = (meta.get(k) or 0) + v  # cumul sur les lots
            else:
                meta[k] = v
        self._data["import_prompt_version"] = request.prompt.version
        self._data["import_prompt_checksum"] = request.prompt.checksum
        self._data["recorded_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        return result

    # ------------------------------------------------------------------ #
    # Assistant de migration : chaque appel est transmis au vrai proposer, compté et journalisé (sorte,
    # erreur, provenance ; jamais la réponse brute, qui peut citer des en-têtes du fichier du client)
    # ------------------------------------------------------------------ #
    @property
    def cache_identity(self) -> str:
        return getattr(self.inner, "cache_identity", f"{self.provider}:{self.model}")

    def _wizard(self, kind: str, request: Any) -> Any:
        result = getattr(self.inner, f"wizard_{kind}")(request)
        self.wizard_calls.append(kind)
        meta = getattr(result, "meta", None)
        self._data.setdefault("wizard_calls", []).append(
            {
                "kind": kind,
                "error": getattr(result, "error", None),
                "meta": meta.model_dump(mode="json", exclude={"provider"}) if meta is not None else None,
            }
        )
        self._data["recorded_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        return result

    def wizard_question(self, request: Any) -> Any:
        return self._wizard("question", request)

    def wizard_target(self, request: Any) -> Any:
        return self._wizard("target", request)

    def wizard_missing(self, request: Any) -> Any:
        return self._wizard("missing", request)

    def wizard_columns(self, request: Any) -> Any:
        return self._wizard("columns", request)

    def wizard_data_type(self, request: Any) -> Any:
        return self._wizard("data_type", request)
