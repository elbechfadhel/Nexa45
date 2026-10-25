"""OpenAIProposer : même contrat, SDK OpenAI (Chat Completions, response_format json_schema strict).

Trois usages, un seul transport (`_complete`) : proposition sémantique d'un champ (V1c, `propose`),
correspondance d'un lot de colonnes d'import (V2c.3, `propose_columns`) et assistant de migration
(`wizard_question`, `wizard_target`).
Le modèle doit être choisi explicitement (UIE_LLM_MODEL) : aucun défaut n'est supposé ici.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

import openai
from uie_core.domains import ProposalMeta, ProposalRequest, ProposalResult
from uie_core.imports import ColumnProposalBatch, ColumnProposalRequest
from uie_core.wizard import (
    WizardColumnsRequest,
    WizardDataTypeRequest,
    WizardMissingRequest,
    WizardRaw,
    WizardRequest,
)

from uie_ai.prompting import (
    COLUMNS_SCHEMA_NAME,
    SCHEMA_NAME,
    answer_schema,
    columns_schema,
    columns_system_text,
    columns_user_text,
    estimate_cost,
    minimal_columns_schema,
    system_text,
    user_text,
)
from uie_ai.wizard_prompt import (
    DATA_TYPE_SCHEMA_NAME,
    MISSING_SCHEMA,
    MISSING_SCHEMA_NAME,
    QUESTION_SCHEMA,
    QUESTION_SCHEMA_NAME,
    TARGET_SCHEMA,
    TARGET_SCHEMA_NAME,
    WIZARD_COLUMNS_PROMPT,
    WIZARD_PROMPT_VERSION,
    data_type_schema,
    data_type_system_text,
    data_type_user_text,
    missing_system_text,
    missing_user_text,
    question_system_text,
    target_system_text,
)
from uie_ai.wizard_prompt import user_text as wizard_user_text

if TYPE_CHECKING:
    from uie_ai.config import LlmSettings


class OpenAIProposer:
    provider = "openai"

    def __init__(self, settings: LlmSettings, client: openai.OpenAI | None = None) -> None:
        if not settings.model:
            raise ValueError("UIE_LLM_MODEL requis pour le fournisseur openai")
        if client is None and settings.openai_api_key is None:
            raise ValueError("UIE_OPENAI_API_KEY manquante")
        self.model = settings.model
        self.columns_model = settings.columns_model or settings.model
        self.columns_effort = settings.columns_effort
        self.timeout = settings.timeout
        self.price_in, self.price_out = settings.price_input, settings.price_output
        self.client = client or openai.OpenAI(
            api_key=settings.openai_api_key.get_secret_value(),  # type: ignore[union-attr]
            max_retries=1,
        )

    def propose(self, request: ProposalRequest) -> ProposalResult:
        raw, meta, error = self._complete(
            system_text(request), user_text(request), SCHEMA_NAME, answer_schema(request), 2048
        )
        if error is not None:
            return ProposalResult.failure(error, meta)
        return ProposalResult.from_raw(raw, meta, request)

    def propose_columns(self, request: ColumnProposalRequest) -> ColumnProposalBatch:
        raw, meta, error = self._complete(
            columns_system_text(request),
            columns_user_text(request),
            COLUMNS_SCHEMA_NAME,
            columns_schema(request),
            8192,
            # toutes les colonnes en un appel : réponse longue (amazon-products.csv, 54 colonnes : ~50 s)
            timeout=max(self.timeout, 180.0),
        )
        if error is not None:
            return ColumnProposalBatch.failure(error, meta, request)
        return ColumnProposalBatch.from_raw(raw, meta, request)

    # ------------------------------------------------------------------ #
    # Assistant de migration (contrat `uie_core.wizard.MigrationWizardProposer`)
    # ------------------------------------------------------------------ #
    @property
    def cache_identity(self) -> str:
        return (
            f"{self.provider}:{self.model}:{WIZARD_PROMPT_VERSION}:"
            f"{self.columns_model}:{self.columns_effort}:{WIZARD_COLUMNS_PROMPT.version}"
        )

    def wizard_question(self, request: WizardRequest) -> WizardRaw:
        raw, meta, error = self._complete(
            question_system_text(request),
            wizard_user_text(request),
            QUESTION_SCHEMA_NAME,
            QUESTION_SCHEMA,
            4000,
        )
        return WizardRaw(data=raw, meta=meta, error=error)

    def wizard_target(self, request: WizardRequest) -> WizardRaw:
        raw, meta, error = self._complete(
            target_system_text(request),
            wizard_user_text(request),
            TARGET_SCHEMA_NAME,
            TARGET_SCHEMA,
            16000,
            timeout=max(self.timeout, 180.0),  # structure complète + lignes d'exemple : réponse longue
        )
        return WizardRaw(data=raw, meta=meta, error=error)

    def wizard_missing(self, request: WizardMissingRequest) -> WizardRaw:
        raw, meta, error = self._complete(
            missing_system_text(request),
            missing_user_text(request),
            MISSING_SCHEMA_NAME,
            MISSING_SCHEMA,
            4000,
        )
        return WizardRaw(data=raw, meta=meta, error=error)

    def wizard_data_type(self, request: WizardDataTypeRequest) -> WizardRaw:
        """Type de données d'un fichier parmi les options offertes (règles sans résultat seulement)."""
        raw, meta, error = self._complete(
            data_type_system_text(request),
            data_type_user_text(request),
            DATA_TYPE_SCHEMA_NAME,
            data_type_schema(request),
            1000,
        )
        return WizardRaw(data=raw, meta=meta, error=error)

    def wizard_columns(self, request: WizardColumnsRequest) -> ColumnProposalBatch:
        """Colonnes du fichier du client → champs de la structure proposée : même transport et même
        validation que les imports (`propose_columns`, clés limitées aux cibles offertes), prompt du
        Wizard."""
        req = ColumnProposalRequest(
            request_ref=request.request_ref,
            domain=request.data_type[:120],
            system=request.system[:120],
            version=request.version[:60],
            columns=request.columns,
            targets=request.targets,
            prompt=WIZARD_COLUMNS_PROMPT,
        )
        # réponse minimale, modèle et effort propres à cet appel (UIE_LLM_COLUMNS_MODEL / _EFFORT)
        raw, meta, error = self._complete(
            columns_system_text(req),
            columns_user_text(req),
            COLUMNS_SCHEMA_NAME,
            minimal_columns_schema(req),
            8192,
            timeout=max(self.timeout, 180.0),
            model=self.columns_model,
            effort=self.columns_effort,
        )
        if error is not None:
            return ColumnProposalBatch.failure(error, meta, req)
        return ColumnProposalBatch.from_raw(raw, meta, req)

    def _complete(
        self,
        system: str,
        user: str,
        schema_name: str,
        schema: dict[str, Any],
        max_tokens: int,
        *,
        timeout: float | None = None,
        model: str | None = None,
        effort: str | None = None,
    ) -> tuple[Any, ProposalMeta, str | None]:
        """Un appel à sortie structurée → (réponse JSON, provenance, erreur). Ne lève jamais."""
        started = time.monotonic()
        model = model or self.model
        meta = ProposalMeta(provider=self.provider, model=model)
        extra: dict[str, Any] = {"reasoning_effort": effort} if effort else {}
        try:
            response = self.client.chat.completions.create(
                model=model,
                **extra,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": schema_name, "schema": schema, "strict": True},
                },
                max_completion_tokens=max_tokens,
                timeout=timeout or self.timeout,
            )
        except openai.APITimeoutError:
            return None, self._timed(meta, started), "timeout"
        except openai.APIConnectionError as exc:
            return None, self._timed(meta, started), f"connexion : {type(exc).__name__}"
        except openai.APIStatusError as exc:
            return None, self._timed(meta, started), f"HTTP {exc.status_code}"[:300]

        usage = response.usage
        tokens_in = usage.prompt_tokens if usage else None
        tokens_out = usage.completion_tokens if usage else None
        meta = ProposalMeta(
            provider=self.provider,
            model=response.model or model,
            request_id=response.id,
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            latency_ms=int((time.monotonic() - started) * 1000),
            cost_usd=estimate_cost(tokens_in, tokens_out, self.price_in, self.price_out),
        )
        choice = response.choices[0] if response.choices else None
        message = choice.message if choice else None
        if message is None or getattr(message, "refusal", None) or not message.content:
            return None, meta, "refus ou réponse vide"
        try:
            return json.loads(message.content), meta, None
        except json.JSONDecodeError:
            return None, meta, "JSON illisible"

    def _timed(self, meta: ProposalMeta, started: float) -> ProposalMeta:
        return meta.model_copy(update={"latency_ms": int((time.monotonic() - started) * 1000)})
