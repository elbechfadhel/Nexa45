"""AnthropicProposer / OpenAIProposer contre un transport HTTP simulé (aucun réseau, aucune clé réelle) :
requête (sortie structurée, clés autorisées, aucune valeur métier), réponse, échecs → NO_CONFIDENT_MATCH."""

from __future__ import annotations

import json

import anthropic
import httpx2
import openai
from pydantic import SecretStr
from test_proposers import request
from uie_ai import LlmSettings, build_proposer
from uie_ai.anthropic_proposer import AnthropicProposer
from uie_ai.openai_proposer import OpenAIProposer
from uie_core.domains import ProposalOutcome

KEY = "sk-test-not-a-real-key"


def _anthropic(handler) -> AnthropicProposer:  # noqa: ANN001
    client = anthropic.Anthropic(
        api_key=KEY,
        max_retries=0,
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)),
    )
    return AnthropicProposer(LlmSettings(provider="anthropic", timeout=5), client=client)


def _message(answer: dict | None, stop_reason: str = "end_turn") -> dict:
    content = [] if answer is None else [{"type": "text", "text": json.dumps(answer)}]
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5",
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 1000, "output_tokens": 200},
    }


def test_anthropic_request_and_match():
    seen = {}

    def handler(req: httpx2.Request) -> httpx2.Response:
        seen["body"] = json.loads(req.content)
        seen["headers"] = req.headers
        answer = {
            "outcome": "MATCH",
            "semantic_key": "supplier_discount",
            "confidence": 0.93,
            "justification": "Percentage discount on the supplier price.",
            "reason": "",
        }
        return httpx2.Response(200, json=_message(answer), headers={"request-id": "req_abc"})

    p = _anthropic(handler)
    r = p.propose(request())
    assert (r.outcome, r.semantic_key, r.confidence) == (ProposalOutcome.MATCH, "supplier_discount", 0.93)
    assert (r.meta.provider, r.meta.model, r.meta.request_id) == ("anthropic", "claude-opus-5", "req_abc")
    assert (r.meta.input_tokens, r.meta.output_tokens, r.meta.cost_usd) == (
        1000,
        200,
        0.01,
    )  # 5 $ + 25 $ / MTok
    body = seen["body"]
    assert body["model"] == "claude-opus-5" and body["output_config"]["effort"] == "low"
    schema = body["output_config"]["format"]["schema"]
    assert schema["properties"]["semantic_key"]["enum"] == ["supplier_discount", "supplier_price", ""]
    assert schema["additionalProperties"] is False
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in seen["headers"]["anthropic-beta"]
    assert json.loads(body["messages"][0]["content"])["field"]["technical_name"] == "discount"


def test_anthropic_failures_never_raise():
    def status(code: int):  # noqa: ANN202
        return lambda req: httpx2.Response(
            code, json={"type": "error", "error": {"type": "x", "message": "boom"}}
        )

    def timeout(req: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("slow", request=req)

    def refusal(req: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=_message(None, "refusal"))

    def garbage(req: httpx2.Request) -> httpx2.Response:
        msg = _message({})
        msg["content"] = [{"type": "text", "text": "not json"}]
        return httpx2.Response(200, json=msg)

    for handler, expected in [
        (status(500), "HTTP 500"),
        (status(401), "HTTP 401"),
        (timeout, "timeout"),
        (refusal, "refus"),
        (garbage, "JSON"),
    ]:
        r = _anthropic(handler).propose(request())
        assert r.outcome == ProposalOutcome.NO_CONFIDENT_MATCH, expected
        assert expected in r.error and KEY not in r.error


def _openai(handler) -> OpenAIProposer:  # noqa: ANN001
    client = openai.OpenAI(
        api_key=KEY,
        max_retries=0,
        http_client=openai.DefaultHttpxClient(transport=httpx2.MockTransport(handler)),
    )
    return OpenAIProposer(LlmSettings(provider="openai", model="model-x", timeout=5), client=client)


def test_openai_request_and_no_confident_match():
    seen = {}

    def handler(req: httpx2.Request) -> httpx2.Response:
        seen["body"] = json.loads(req.content)
        answer = {
            "outcome": "NO_CONFIDENT_MATCH",
            "semantic_key": "",
            "confidence": 0.1,
            "justification": "",
            "reason": "Kanban colour index, not product data",
        }
        return httpx2.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "created": 0,
                "model": "model-x",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": json.dumps(answer)},
                    }
                ],
                "usage": {"prompt_tokens": 900, "completion_tokens": 50, "total_tokens": 950},
            },
        )

    r = _openai(handler).propose(request())
    assert (r.outcome, r.reason) == (
        ProposalOutcome.NO_CONFIDENT_MATCH,
        "Kanban colour index, not product data",
    )
    assert (r.meta.request_id, r.meta.input_tokens) == ("chatcmpl-1", 900)
    fmt = seen["body"]["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True


def test_openai_failure_never_raises():
    r = _openai(lambda req: httpx2.Response(503, json={"error": {"message": "down"}})).propose(request())
    assert r.outcome == ProposalOutcome.NO_CONFIDENT_MATCH and "503" in r.error


def test_build_proposer_selects_the_real_providers():
    a = build_proposer(LlmSettings(provider="anthropic", anthropic_api_key=SecretStr(KEY)))
    assert (a.provider, a.model) == ("anthropic", "claude-opus-5")
    o = build_proposer(LlmSettings(provider="openai", model="model-x", openai_api_key=SecretStr(KEY)))
    assert (o.provider, o.model) == ("openai", "model-x")


def test_the_wizard_columns_use_gpt_4_1_mini_by_default_with_openai(monkeypatch):
    for name in ("UIE_LLM_COLUMNS_MODEL", "UIE_LLM_MODEL", "UIE_LLM_COLUMNS_EFFORT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("UIE_LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_MODEL", "model-x")
    settings = LlmSettings.from_env()
    assert (settings.model, settings.columns_model) == ("model-x", "gpt-4.1-mini")
    monkeypatch.setenv("UIE_LLM_COLUMNS_MODEL", "model-y")  # toujours remplaçable
    assert LlmSettings.from_env().columns_model == "model-y"
    monkeypatch.delenv("UIE_LLM_COLUMNS_MODEL")
    monkeypatch.setenv("UIE_LLM_PROVIDER", "anthropic")  # autre fournisseur : son propre modèle
    assert LlmSettings.from_env().columns_model is None
