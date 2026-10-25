"""uie_ai : contrat du proposer, validation commune des réponses, Null / Replay / Recording, configuration.
Aucun réseau."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from pydantic import SecretStr
from uie_ai import LlmSettings, NullProposer, RecordingProposer, ReplayProposer, build_proposer
from uie_core.domains import (
    DomainKey,
    EntityRole,
    FieldMetadata,
    PromptSpec,
    ProposalMeta,
    ProposalOutcome,
    ProposalRequest,
    ProposalResult,
    SemanticProposer,
    VocabularyChoice,
)

CORE_SRC = Path(__file__).resolve().parents[2] / "core" / "src" / "uie_core"
PROMPT = PromptSpec(version="t1", system="Map the field to a key, or answer NO_CONFIDENT_MATCH.")


def request(
    name: str = "discount", keys: tuple[str, ...] = ("supplier_discount", "supplier_price")
) -> ProposalRequest:
    return ProposalRequest(
        request_ref="i-0000",
        domain_key=DomainKey.PRODUCT,
        system="shop",
        version="1",
        entity_role=EntityRole.SUPPLIER_INFO,
        field=FieldMetadata(
            entity="shop.offer", technical_name=name, label="Discount (%)", data_type="decimal"
        ),
        vocabulary=[
            VocabularyChoice(key=k, label=k, description=k, group="supplier", data_type="decimal")
            for k in keys
        ],
        prompt=PROMPT,
    )


META = ProposalMeta(provider="test", model="m1")


# --------------------------------------------------------------------------- #
# Validation commune (tous fournisseurs, replay compris)
# --------------------------------------------------------------------------- #
def test_match_is_accepted_and_justification_is_bounded():
    r = ProposalResult.from_raw(
        {
            "outcome": "MATCH",
            "semantic_key": "supplier_discount",
            "confidence": 0.912,
            "justification": "x" * 400,
        },
        META,
        request(),
    )
    assert (r.outcome, r.semantic_key, r.confidence, r.error) == (
        ProposalOutcome.MATCH,
        "supplier_discount",
        0.91,
        None,
    )
    assert len(r.justification) == 200


def test_key_outside_the_vocabulary_is_no_confident_match_with_error():
    r = ProposalResult.from_raw(
        {"outcome": "MATCH", "semantic_key": "discount_rate", "confidence": 0.99}, META, request()
    )
    assert r.outcome == ProposalOutcome.NO_CONFIDENT_MATCH and r.semantic_key is None
    assert "hors vocabulaire" in r.error and r.raw["semantic_key"] == "discount_rate"


def test_relevance_or_exclusion_in_an_answer_is_ignored_and_reported():
    raw = {
        "outcome": "MATCH",
        "semantic_key": "supplier_discount",
        "confidence": 0.9,
        "relevance": "NOT_RELEVANT",
        "exclude": True,
    }
    r = ProposalResult.from_raw(raw, META, request())
    assert r.outcome == ProposalOutcome.MATCH and r.ignored_fields == ["exclude", "relevance"]
    assert not hasattr(r, "relevance")


@pytest.mark.parametrize(
    "raw",
    [
        {"outcome": "MATCH", "semantic_key": "supplier_discount", "confidence": 1.4},
        {"outcome": "MATCH", "semantic_key": "supplier_discount", "confidence": True},
        {"outcome": "MATCH", "semantic_key": "supplier_discount"},
        {"outcome": "EXCLUDE"},
        ["not", "an", "object"],
        None,
    ],
)
def test_malformed_answers_become_no_confident_match(raw):
    r = ProposalResult.from_raw(raw, META, request())
    assert r.outcome == ProposalOutcome.NO_CONFIDENT_MATCH and r.error


def test_no_confident_match_keeps_its_reason():
    r = ProposalResult.from_raw(
        {"outcome": "NO_CONFIDENT_MATCH", "reason": "Kanban colour index"}, META, request()
    )
    assert (r.outcome, r.reason, r.error) == (ProposalOutcome.NO_CONFIDENT_MATCH, "Kanban colour index", None)


# --------------------------------------------------------------------------- #
# Proposers
# --------------------------------------------------------------------------- #
def test_null_proposer_never_proposes():
    p = NullProposer()
    assert isinstance(p, SemanticProposer) and p.provider == "none"
    assert p.propose(request()).outcome == ProposalOutcome.NO_CONFIDENT_MATCH


class Scripted:
    provider, model = "scripted", "m-test"

    def __init__(self, answers: dict[str, dict]) -> None:
        self.answers = answers

    def propose(self, req: ProposalRequest) -> ProposalResult:
        raw = self.answers.get(req.field.technical_name)
        if raw is None:
            return ProposalResult.failure("timeout", ProposalMeta(provider=self.provider, model=self.model))
        meta = ProposalMeta(provider=self.provider, model=self.model, request_id="req-1", input_tokens=120)
        return ProposalResult.from_raw(raw, meta, req)


def test_recording_then_replay_is_identical(tmp_path):
    answers = {
        "discount": {
            "outcome": "MATCH",
            "semantic_key": "supplier_discount",
            "confidence": 0.93,
            "justification": "j",
        },
        "color": {"outcome": "NO_CONFIDENT_MATCH", "reason": "UI colour index"},
    }
    path = tmp_path / "replay.json"
    rec = RecordingProposer(Scripted(answers), path)
    live = {n: rec.propose(request(n)) for n in ("discount", "color", "timeout_field")}
    data = json.loads(path.read_text("utf-8"))
    assert data["format"] == "uie-llm-replay/1" and data["prompt_checksum"] == PROMPT.checksum
    assert data["responses"]["shop.offer.timeout_field"] == {
        "raw": None,
        "error": "timeout",
        "meta": {
            "model": "m-test",
            "request_id": None,
            "input_tokens": None,
            "output_tokens": None,
            "latency_ms": None,
            "cost_usd": None,
        },
    }

    replay = ReplayProposer(path)
    for name, original in live.items():
        again = replay.propose(request(name))
        assert (again.outcome, again.semantic_key, again.confidence, again.reason, again.error) == (
            original.outcome,
            original.semantic_key,
            original.confidence,
            original.reason,
            original.error,
        )
    assert replay.propose(request("never_seen")).error.startswith("not recorded")


def test_replay_goes_through_the_same_validation(tmp_path):
    path = tmp_path / "evil.json"
    path.write_text(
        json.dumps(
            {
                "format": "uie-llm-replay/1",
                "responses": {
                    "shop.offer.discount": {
                        "raw": {
                            "outcome": "MATCH",
                            "semantic_key": "made_up",
                            "confidence": 0.99,
                            "relevance": "NOT_RELEVANT",
                        },
                        "meta": {},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    r = ReplayProposer(path).propose(request())
    assert r.outcome == ProposalOutcome.NO_CONFIDENT_MATCH and "hors vocabulaire" in r.error
    assert r.ignored_fields == ["relevance"]


# --------------------------------------------------------------------------- #
# Configuration et architecture
# --------------------------------------------------------------------------- #
def test_default_is_null_and_a_missing_key_never_breaks_startup(monkeypatch):
    for name in ("UIE_LLM_PROVIDER", "UIE_ANTHROPIC_API_KEY", "UIE_OPENAI_API_KEY", "UIE_LLM_REPLAY_FILE"):
        monkeypatch.delenv(name, raising=False)
    assert build_proposer().provider == "none"
    monkeypatch.setenv("UIE_LLM_PROVIDER", "anthropic")  # clé absente
    assert build_proposer().provider == "none"
    assert build_proposer(LlmSettings(provider="openai", openai_api_key=None)).provider == "none"


def test_replay_provider_from_settings(tmp_path):
    path = tmp_path / "r.json"
    path.write_text(
        json.dumps({"format": "uie-llm-replay/1", "model": "m", "responses": {}}), encoding="utf-8"
    )
    p = build_proposer(LlmSettings(provider="replay", replay_file=str(path)))
    assert (p.provider, p.model) == ("replay", "m")


def test_api_key_is_never_printable():
    s = LlmSettings(provider="anthropic", anthropic_api_key=SecretStr("sk-ant-secret-value"))
    assert "sk-ant-secret-value" not in repr(s) and "sk-ant-secret-value" not in str(s)


def test_core_imports_no_ai_provider():
    forbidden = {"anthropic", "openai", "httpx", "requests", "uie_ai"}
    offenders = []
    for path in CORE_SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            offenders += [f"{path.name}:{n}" for n in names if n.split(".")[0] in forbidden]
    assert offenders == []


def test_standard_openai_variable_names_are_accepted_but_never_enable_by_themselves(monkeypatch):
    for name in ("UIE_LLM_PROVIDER", "UIE_LLM_MODEL", "UIE_OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-standard-name")
    monkeypatch.setenv("OPENAI_MODEL", "model-std")
    assert build_proposer().provider == "none"  # une clé seule n'active jamais l'envoi
    monkeypatch.setenv("UIE_LLM_PROVIDER", "openai")
    s = LlmSettings.from_env()
    assert (s.model, s.openai_api_key.get_secret_value()) == ("model-std", "sk-test-standard-name")
    assert (build_proposer().provider, build_proposer().model) == ("openai", "model-std")
    monkeypatch.setenv("UIE_LLM_MODEL", "model-uie")  # les noms UIE_* priment
    assert LlmSettings.from_env().model == "model-uie"
