"""AnthropicProposer : API Messages de Claude, sortie structurée (output_config.format, schéma JSON strict).

Un appel par champ (timeout par champ), effort « low » (classification courte), fallback serveur activé en
cas de refus (le serveur relance la même requête sur un autre modèle). Tout échec (réseau, timeout, refus,
réponse illisible) rend NO_CONFIDENT_MATCH : jamais d'exception jusqu'au supplier. La clé API n'est jamais
journalisée ni renvoyée. Un seul transport (`_complete`) pour les deux usages : un champ (V1c, `propose`)
et un lot de colonnes d'import (V2c.3, `propose_columns`).
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

import anthropic
from uie_core.domains import ProposalMeta, ProposalRequest, ProposalResult
from uie_core.imports import ColumnProposalBatch, ColumnProposalRequest

from uie_ai.prompting import (
    answer_schema,
    columns_schema,
    columns_system_text,
    columns_user_text,
    estimate_cost,
    system_text,
    user_text,
)

if TYPE_CHECKING:
    from uie_ai.config import LlmSettings

DEFAULT_MODEL = "claude-opus-5"
# prix publics (USD / million de tokens, input / output) ; surchargeables par UIE_LLM_PRICE_*
PRICES = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-fable-5-1": (10.0, 50.0),
}
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProposer:
    provider = "anthropic"

    def __init__(self, settings: LlmSettings, client: anthropic.Anthropic | None = None) -> None:
        if client is None and settings.anthropic_api_key is None:
            raise ValueError("UIE_ANTHROPIC_API_KEY manquante")
        self.model = settings.model or DEFAULT_MODEL
        self.timeout = settings.timeout
        default_in, default_out = PRICES.get(self.model, (None, None))
        self.price_in = settings.price_input if settings.price_input is not None else default_in
        self.price_out = settings.price_output if settings.price_output is not None else default_out
        self.client = client or anthropic.Anthropic(
            api_key=settings.anthropic_api_key.get_secret_value(),  # type: ignore[union-attr]
            max_retries=1,
        )

    def propose(self, request: ProposalRequest) -> ProposalResult:
        raw, meta, error = self._complete(
            system_text(request), user_text(request), answer_schema(request), 4096
        )
        if error is not None:
            return ProposalResult.failure(error, meta)
        return ProposalResult.from_raw(raw, meta, request)

    def propose_columns(self, request: ColumnProposalRequest) -> ColumnProposalBatch:
        raw, meta, error = self._complete(
            columns_system_text(request), columns_user_text(request), columns_schema(request), 16384
        )
        if error is not None:
            return ColumnProposalBatch.failure(error, meta, request)
        return ColumnProposalBatch.from_raw(raw, meta, request)

    def _complete(
        self, system: str, user: str, schema: dict[str, Any], max_tokens: int
    ) -> tuple[Any, ProposalMeta, str | None]:
        """Un appel à sortie structurée → (réponse JSON, provenance, erreur). Ne lève jamais."""
        started = time.monotonic()
        meta = ProposalMeta(provider=self.provider, model=self.model)
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={
                    "effort": "low",
                    "format": {"type": "json_schema", "schema": schema},
                },
                betas=[FALLBACK_BETA],
                fallbacks="default",
                timeout=self.timeout,
            )
        except anthropic.APITimeoutError:
            return None, self._timed(meta, started), "timeout"
        except anthropic.APIConnectionError as exc:
            return None, self._timed(meta, started), f"connexion : {type(exc).__name__}"
        except anthropic.APIStatusError as exc:
            return None, self._timed(meta, started), f"HTTP {exc.status_code} : {exc.message}"[:300]

        usage = response.usage
        meta = ProposalMeta(
            provider=self.provider,
            model=response.model or self.model,
            request_id=getattr(response, "_request_id", None),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            latency_ms=int((time.monotonic() - started) * 1000),
            cost_usd=estimate_cost(usage.input_tokens, usage.output_tokens, self.price_in, self.price_out),
        )
        if response.stop_reason == "refusal":
            return None, meta, "refus du modèle"
        text = next((b.text for b in response.content if b.type == "text"), None)
        if response.stop_reason == "max_tokens" or text is None:
            return None, meta, f"réponse incomplète ({response.stop_reason})"
        try:
            return json.loads(text), meta, None
        except json.JSONDecodeError:
            return None, meta, "JSON illisible"

    def _timed(self, meta: ProposalMeta, started: float) -> ProposalMeta:
        return meta.model_copy(update={"latency_ms": int((time.monotonic() - started) * 1000)})
