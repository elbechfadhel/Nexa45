"""AI Migration Wizard : transport OpenAI (sortie structurée stricte, modèle configuré, aucune clé dans la
requête ni dans la réponse validée), sans appel réel (transport simulé)."""

from __future__ import annotations

import json

import httpx2
import openai
from uie_ai import LlmSettings
from uie_ai.openai_proposer import OpenAIProposer
from uie_ai.wizard_prompt import WIZARD_PROMPT_VERSION
from uie_core.wizard import ChoiceSource, MigrationWizardProposer, WizardAnswer, WizardRequest
from uie_core.wizard.proposer import RegistrySystemFact

KEY = "sk-test-not-a-real-key"


def _proposer(handler) -> OpenAIProposer:  # noqa: ANN001
    client = openai.OpenAI(
        api_key=KEY,
        max_retries=0,
        http_client=openai.DefaultHttpxClient(transport=httpx2.MockTransport(handler)),
    )
    return OpenAIProposer(LlmSettings(provider="openai", model="gpt-test", timeout=5), client=client)


def _completion(content: dict) -> httpx2.Response:
    return httpx2.Response(
        200,
        json={
            "id": "chatcmpl-w",
            "object": "chat.completion",
            "created": 0,
            "model": "gpt-test",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": json.dumps(content)},
                }
            ],
            "usage": {"prompt_tokens": 700, "completion_tokens": 120, "total_tokens": 820},
        },
    )


def _request() -> WizardRequest:
    return WizardRequest(
        answers=[
            WizardAnswer(
                question_id="target_system",
                topic="target_system",
                question="Which target system?",
                value="big_suite",
                label="Big Suite",
                source=ChoiceSource.AI,
            )
        ],
        registry=[RegistrySystemFact(code="demo", name="Demo ERP", versions=["17"], interfaces=["orm"])],
        max_questions=12,
        sample_rows=5,
    )


def test_question_call_uses_the_model_a_strict_schema_and_the_registry_facts():
    seen: dict = {}

    def handler(req: httpx2.Request) -> httpx2.Response:
        seen["body"] = json.loads(req.content)
        return _completion({"done": True, "remaining": 0, "question": None})

    p = _proposer(handler)
    assert isinstance(p, MigrationWizardProposer)
    raw = p.wizard_question(_request())
    assert raw.error is None and raw.data["done"] is True
    assert (raw.meta.model, raw.meta.input_tokens, raw.meta.output_tokens) == ("gpt-test", 700, 120)
    body = seen["body"]
    assert body["model"] == "gpt-test"
    fmt = body["response_format"]["json_schema"]
    assert fmt["name"] == "wizard_question" and fmt["strict"] is True
    user = json.loads(body["messages"][1]["content"])
    assert user["registry_systems"] == []  # cible déjà choisie : la liste n'est plus envoyée
    p.wizard_question(_request().model_copy(update={"answers": []}))
    first = json.loads(seen["body"]["messages"][1]["content"])
    assert first["registry_systems"][0] == {"name": "Demo ERP", "verified_schema": True, "versions": ["17"]}
    assert user["answers"] == [
        {"topic": "target_system", "question": "Which target system?", "answer": "Big Suite"}
    ]
    assert KEY not in json.dumps(body)
    assert WIZARD_PROMPT_VERSION in p.cache_identity


def test_target_call_and_failures_never_raise():
    p = _proposer(lambda req: _completion({"title": "x"}))
    assert p.wizard_target(_request()).data == {"title": "x"}
    down = _proposer(lambda req: httpx2.Response(503, json={"error": {"message": "down"}}))
    raw = down.wizard_target(_request())
    assert raw.data is None and "503" in (raw.error or "") and KEY not in (raw.error or "")
