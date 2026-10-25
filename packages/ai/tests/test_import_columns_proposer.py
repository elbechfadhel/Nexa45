"""V2c.3 : proposition par lot pour les colonnes d'un import (contrat du Core, validation commune, masquage
des exemples, Null / Replay / Recording, OpenAI / Anthropic sur transport simulé). Aucun réseau."""

from __future__ import annotations

import json

import anthropic
import httpx2
import openai
import pytest
from test_llm_providers import KEY, _message
from uie_ai import LlmSettings, NullProposer, RecordingProposer, ReplayProposer
from uie_ai.anthropic_proposer import AnthropicProposer
from uie_ai.openai_proposer import OpenAIProposer
from uie_core.domains import PromptSpec, ProposalMeta
from uie_core.imports import (
    CandidateColumn,
    ColumnDecision,
    ColumnMappingProposer,
    ColumnProposalBatch,
    ColumnProposalRequest,
    TargetChoice,
    mask_example,
)

PROMPT = PromptSpec(version="t1", system="Map each column to a target, NOT_RELEVANT or UNKNOWN.")
META = ProposalMeta(provider="test", model="m1")


def batch_request(examples: tuple[str, ...] = ("19.90", "25.00")) -> ColumnProposalRequest:
    return ColumnProposalRequest(
        request_ref="imp-0000",
        domain="PRODUCT",
        system="shop",
        version="1",
        columns=[
            CandidateColumn(
                position=3,
                header="Retail price",
                detected_type="decimal",
                fill_ratio=1.0,
                examples=list(examples),
            ),
            CandidateColumn(
                position=5, header="Theme: layout", detected_type="text", fill_ratio=1.0, examples=["left"]
            ),
            CandidateColumn(position=8, header="Shade", detected_type="text", fill_ratio=0.5),
        ],
        targets=[
            TargetChoice(key=k, label=k, description=k, group="product", data_type=t)
            for k, t in (("list_price", "decimal"), ("name", "text"))
        ],
        prompt=PROMPT,
    )


RAW = {
    "columns": [
        {
            "position": 3,
            "source_header": "Retail price",
            "decision": "TARGET",
            "semantic_key": "list_price",
            "confidence": 0.957,
            "reason": "Public selling price.",
        },
        {
            "position": 5,
            "source_header": "Theme: layout",
            "decision": "NOT_RELEVANT",
            "semantic_key": "",
            "confidence": 0.97,
            "reason": "Theme setting.",
        },
        {
            "position": 8,
            "source_header": "Shade",
            "decision": "UNKNOWN",
            "semantic_key": "",
            "confidence": 0.2,
            "reason": "No colour target.",
        },
    ]
}


# --------------------------------------------------------------------------- #
# Validation commune
# --------------------------------------------------------------------------- #
def test_valid_batch_is_accepted_in_request_order():
    b = ColumnProposalBatch.from_raw(RAW, META, batch_request())
    got = [(p.position, p.decision, p.semantic_key, p.confidence) for p in b.proposals]
    assert got == [
        (3, ColumnDecision.TARGET, "list_price", 0.957),
        (5, ColumnDecision.NOT_RELEVANT, None, 0.97),
        (8, ColumnDecision.UNKNOWN, None, 0.2),
    ]
    assert b.error is None and b.errors == []


def test_a_target_outside_the_offered_ones_is_unknown_never_invented():
    raw = {"columns": [{**RAW["columns"][0], "semantic_key": "x_new_field"}]}
    b = ColumnProposalBatch.from_raw(raw, META, batch_request())
    assert b.by_position()[3].decision == ColumnDecision.UNKNOWN
    assert "hors des cibles" in b.errors[0]


@pytest.mark.parametrize(
    "item",
    [
        {"position": 3, "decision": "TARGET", "semantic_key": "list_price", "confidence": 1.5},
        {"position": 3, "decision": "TARGET", "semantic_key": "list_price", "confidence": True},
        {"position": 3, "decision": "TARGET", "semantic_key": "", "confidence": 0.9},
        {"position": 3, "decision": "EXCLUDE", "semantic_key": "", "confidence": 0.9},
    ],
)
def test_invalid_answers_become_unknown(item):
    b = ColumnProposalBatch.from_raw({"columns": [item]}, META, batch_request())
    assert b.by_position()[3].decision == ColumnDecision.UNKNOWN and b.errors


def test_missing_unknown_or_duplicate_positions_and_extra_fields():
    raw = {
        "columns": [
            {**RAW["columns"][1], "relevance": "EXCLUDED"},
            {**RAW["columns"][1], "position": 99},
            {**RAW["columns"][1], "decision": "UNKNOWN"},
        ]
    }
    b = ColumnProposalBatch.from_raw(raw, META, batch_request())
    assert [p.decision for p in b.proposals] == [
        ColumnDecision.UNKNOWN,  # 3 : sans réponse
        ColumnDecision.NOT_RELEVANT,  # 5 : la première réponse compte
        ColumnDecision.UNKNOWN,  # 8 : sans réponse
    ]
    assert b.ignored_fields == ["relevance"]


@pytest.mark.parametrize("raw", [None, [], {"columns": "x"}, "text"])
def test_malformed_batches_fail_as_unknown(raw):
    b = ColumnProposalBatch.from_raw(raw, META, batch_request())
    assert b.error and {p.decision for p in b.proposals} == {ColumnDecision.UNKNOWN}


def test_examples_are_masked_generically():
    assert mask_example("contact: jane.doe@shop.example") == "contact: <email>"
    assert mask_example("see https://shop.example/p/1") == "see <url>"
    assert mask_example("+49 30 1234 5678") == "<number>"
    assert mask_example("DE89 3704 0044 0532 0130 00") == "<number>"
    assert mask_example("2024-01-15") == "2024-01-15"  # une date reste lisible
    assert mask_example("149.00") == "149.00"
    assert mask_example("<p>Solid <b>oak</b> tray</p>") == "Solid oak tray"
    assert len(mask_example("x" * 200)) == 40


def test_column_checksum_ignores_position_and_batch_but_not_the_content():
    a, b = batch_request(), batch_request()
    col = a.columns[0]
    moved = col.model_copy(update={"position": 42})
    alone = b.model_copy(update={"columns": [moved], "request_ref": "other"})
    assert a.column_checksum(col, "openai", "m") == alone.column_checksum(moved, "openai", "m")
    assert a.column_checksum(col, "openai", "m") != a.column_checksum(col, "openai", "m2")
    other = batch_request(("1.00",))
    assert a.column_checksum(col, "openai", "m") != other.column_checksum(other.columns[0], "openai", "m")
    fewer = a.model_copy(update={"targets": a.targets[:1]})  # autres cibles offertes → autre réponse possible
    assert a.column_checksum(col, "openai", "m") != fewer.column_checksum(col, "openai", "m")


# --------------------------------------------------------------------------- #
# Null / Replay / Recording
# --------------------------------------------------------------------------- #
class _Fake:
    provider, model = "fake", "f1"

    def propose_columns(self, request):  # noqa: ANN001, ANN201
        return ColumnProposalBatch.from_raw(
            RAW,
            ProposalMeta(provider="fake", model="f1", input_tokens=10, output_tokens=5, cost_usd=0.001),
            request,
        )


def test_null_proposer_answers_unknown_for_every_column():
    p = NullProposer()
    assert isinstance(p, ColumnMappingProposer)
    b = p.propose_columns(batch_request())
    assert {x.decision for x in b.proposals} == {ColumnDecision.UNKNOWN} and b.error


def test_recording_then_replay_goes_through_the_same_validation(tmp_path):
    path = tmp_path / "replay.json"
    rec = RecordingProposer(_Fake(), path)
    first = rec.propose_columns(batch_request())
    data = json.loads(path.read_text("utf-8"))
    assert set(data["import_columns"]) == {"retail price", "theme: layout", "shade"}
    assert data["import_meta"]["input_tokens"] == 10
    replay = ReplayProposer(path)
    again = replay.propose_columns(batch_request())
    assert [p.model_dump() for p in again.proposals] == [p.model_dump() for p in first.proposals]
    assert len(replay.column_calls) == 1
    # une réponse enregistrée hors des cibles offertes reste refusée au rejeu
    data["import_columns"]["retail price"]["semantic_key"] = "x_new_field"
    path.write_text(json.dumps(data), "utf-8")
    assert ReplayProposer(path).propose_columns(batch_request()).by_position()[3].decision == "UNKNOWN"


# --------------------------------------------------------------------------- #
# Fournisseurs réels, transport simulé
# --------------------------------------------------------------------------- #
def _openai(handler) -> OpenAIProposer:  # noqa: ANN001
    client = openai.OpenAI(
        api_key=KEY,
        max_retries=0,
        http_client=openai.DefaultHttpxClient(transport=httpx2.MockTransport(handler)),
    )
    return OpenAIProposer(LlmSettings(provider="openai", model="model-x", timeout=5), client=client)


def _completion(content: str) -> dict:
    return {
        "id": "chatcmpl-9",
        "object": "chat.completion",
        "created": 0,
        "model": "model-x",
        "choices": [
            {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}
        ],
        "usage": {"prompt_tokens": 1200, "completion_tokens": 300, "total_tokens": 1500},
    }


def test_openai_batch_request_is_one_structured_call():
    seen: list[dict] = []

    def handler(req: httpx2.Request) -> httpx2.Response:
        seen.append(json.loads(req.content))
        return httpx2.Response(200, json=_completion(json.dumps(RAW)))

    b = _openai(handler).propose_columns(batch_request())
    assert len(seen) == 1 and b.by_position()[3].semantic_key == "list_price"
    body = seen[0]
    schema = body["response_format"]["json_schema"]
    assert schema["strict"] is True and schema["name"] == "import_column_mapping"
    item = schema["schema"]["properties"]["columns"]["items"]
    assert item["properties"]["semantic_key"]["enum"] == ["list_price", "name", ""]
    user = json.loads(body["messages"][1]["content"])
    assert [c["header"] for c in user["columns"]] == ["Retail price", "Theme: layout", "Shade"]
    assert "request_ref" not in user and (b.meta.input_tokens, b.meta.request_id) == (1200, "chatcmpl-9")


def test_openai_batch_failures_never_raise():
    b = _openai(lambda req: httpx2.Response(503, json={"error": {"message": "down"}})).propose_columns(
        batch_request()
    )
    assert "503" in b.error and {p.decision for p in b.proposals} == {ColumnDecision.UNKNOWN}
    b = _openai(lambda req: httpx2.Response(200, json=_completion("{not json"))).propose_columns(
        batch_request()
    )
    assert b.error == "JSON illisible"


def test_anthropic_batch_request_and_answer():
    seen: list[dict] = []

    def handler(req: httpx2.Request) -> httpx2.Response:
        seen.append(json.loads(req.content))
        return httpx2.Response(200, json=_message(RAW))

    client = anthropic.Anthropic(
        api_key=KEY,
        max_retries=0,
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)),
    )
    b = AnthropicProposer(LlmSettings(provider="anthropic", timeout=5), client=client).propose_columns(
        batch_request()
    )
    assert b.by_position()[5].decision == ColumnDecision.NOT_RELEVANT
    fmt = seen[0]["output_config"]["format"]
    assert fmt["type"] == "json_schema" and "columns" in fmt["schema"]["properties"]
