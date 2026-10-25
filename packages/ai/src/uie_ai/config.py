"""Choix du proposer d'après l'environnement (racines de composition uniquement).

    UIE_LLM_PROVIDER      none (défaut) | anthropic | openai | replay
    UIE_LLM_MODEL         modèle du fournisseur (défaut propre à chaque fournisseur ; repli : OPENAI_MODEL
                          pour openai)
    UIE_ANTHROPIC_API_KEY clé Anthropic (repli : ANTHROPIC_API_KEY ; jamais journalisée ni exposée)
    UIE_OPENAI_API_KEY    clé OpenAI (repli : OPENAI_API_KEY)
    UIE_LLM_REPLAY_FILE   fichier de réponses enregistrées (provider replay)
    UIE_LLM_TIMEOUT       timeout par champ, en secondes (défaut 30)
    UIE_LLM_PRICE_INPUT / UIE_LLM_PRICE_OUTPUT  prix par million de tokens (USD), pour le coût estimé
    UIE_LLM_COLUMNS_MODEL / UIE_LLM_COLUMNS_EFFORT  modèle et effort de raisonnement (none | minimal | low |
                          medium | high) de la correspondance des colonnes du Wizard ; défaut : gpt-4.1-mini
                          pour openai (même précision mesurée, deux fois plus rapide), sinon ceux du reste

Fournisseur demandé mais clé absente : NullProposer (le produit démarre, « AI not configured »).
Une clé seule ne suffit jamais : UIE_LLM_PROVIDER est l'interrupteur explicite de l'envoi à un fournisseur.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from pydantic import SecretStr
from uie_core.domains import SemanticProposer

from uie_ai.proposers import NullProposer, ReplayProposer

# colonnes du Wizard avec openai : même précision que gpt-5.6-sol sur les fichiers de mesure
# (temp/RESULTAT_WIZARD_SIMPLE_FRONT.md), deux fois plus rapide ; UIE_LLM_COLUMNS_MODEL la remplace
DEFAULT_OPENAI_COLUMNS_MODEL = "gpt-4.1-mini"


@dataclass(frozen=True)
class LlmSettings:
    provider: str = "none"
    model: str | None = None
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    replay_file: str | None = None
    timeout: float = 30.0
    price_input: float | None = None  # USD / million de tokens
    price_output: float | None = None
    # correspondance des colonnes de l'assistant de migration : modèle et effort de raisonnement propres
    columns_model: str | None = None
    columns_effort: str | None = None

    @classmethod
    def from_env(cls) -> LlmSettings:
        def secret(*names: str) -> SecretStr | None:
            value = next((v for n in names if (v := os.environ.get(n, "").strip())), "")
            return SecretStr(value) if value else None

        def number(name: str) -> float | None:
            value = os.environ.get(name, "").strip()
            return float(value) if value else None

        provider = os.environ.get("UIE_LLM_PROVIDER", "none").strip().lower() or "none"
        columns_model = os.environ.get("UIE_LLM_COLUMNS_MODEL", "").strip()
        if not columns_model and provider == "openai":
            columns_model = DEFAULT_OPENAI_COLUMNS_MODEL
        model = os.environ.get("UIE_LLM_MODEL", "").strip()
        if not model and provider == "openai":
            model = os.environ.get("OPENAI_MODEL", "").strip()
        return cls(
            provider=provider,
            model=model or None,
            anthropic_api_key=secret("UIE_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
            openai_api_key=secret("UIE_OPENAI_API_KEY", "OPENAI_API_KEY"),
            replay_file=os.environ.get("UIE_LLM_REPLAY_FILE", "").strip() or None,
            timeout=number("UIE_LLM_TIMEOUT") or 30.0,
            price_input=number("UIE_LLM_PRICE_INPUT"),
            price_output=number("UIE_LLM_PRICE_OUTPUT"),
            columns_model=columns_model or None,
            columns_effort=os.environ.get("UIE_LLM_COLUMNS_EFFORT", "").strip().lower() or None,
        )


def build_proposer(settings: LlmSettings | None = None) -> SemanticProposer:
    s = settings or LlmSettings.from_env()
    if s.provider == "replay" and s.replay_file:
        return ReplayProposer(s.replay_file)
    if s.provider == "anthropic" and s.anthropic_api_key:
        from uie_ai.anthropic_proposer import AnthropicProposer

        return AnthropicProposer(s)
    if s.provider == "openai" and s.openai_api_key:
        from uie_ai.openai_proposer import OpenAIProposer

        return OpenAIProposer(s)
    return NullProposer()
