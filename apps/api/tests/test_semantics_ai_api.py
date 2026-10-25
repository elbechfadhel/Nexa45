"""API V1c : ai-status, enrich-ai (Ask AI), provenance IA dans les questions ; aucune lecture n'appelle
le LLM. Proposer scripté injecté par dependency_overrides (aucun réseau, aucune clé)."""

from __future__ import annotations

import pytest
from test_product_domain_api import client, iid  # noqa: F401 — fixtures partagées (instance découverte)
from uie_ai import NullProposer
from uie_api import deps
from uie_core.domains import DomainKey, DomainPackRegistry, ProposalMeta, ProposalRequest, ProposalResult

BASE = "/admin/instances/{iid}/product-domain"
PROMPT_VERSION = DomainPackRegistry.prompt(DomainKey.PRODUCT).version


class Scripted:
    provider, model = "scripted", "model-t"

    def __init__(self, answers: dict[str, dict] | None = None) -> None:
        self.answers = answers or {}
        self.calls: list[ProposalRequest] = []

    def propose(self, request: ProposalRequest) -> ProposalResult:
        self.calls.append(request)
        raw = self.answers.get(request.path, {"outcome": "NO_CONFIDENT_MATCH", "reason": "UI helper"})
        meta = ProposalMeta(
            provider=self.provider,
            model=self.model,
            request_id="req-1",
            input_tokens=500,
            output_tokens=40,
            cost_usd=0.0035,
        )
        return ProposalResult.from_raw(raw, meta, request)


class Exploding:
    provider, model = "exploding", None

    def propose(self, request: ProposalRequest) -> ProposalResult:
        raise AssertionError("aucun appel LLM attendu")


def _use(client, proposer) -> None:  # noqa: ANN001, F811
    client.app.dependency_overrides[deps.get_semantic_proposer] = lambda: proposer


@pytest.fixture
def analyzed(client, iid):  # noqa: F811, ANN201
    base = BASE.format(iid=iid)
    assert client.post(f"{base}/analyze").status_code == 200
    return base


def test_without_provider_ai_is_off(client, analyzed):  # noqa: F811
    _use(client, NullProposer())
    status = client.get(f"{analyzed}/ai-status").json()
    assert (status["provider"], status["configured"], status["calls"]) == ("none", False, 0)
    assert status["candidates"] > 0 and status["prompt_version"] == PROMPT_VERSION
    r = client.post(f"{analyzed}/enrich-ai", json={})
    assert r.status_code == 400 and "No AI provider" in r.json()["detail"]


def test_reads_never_call_the_llm(client, analyzed):  # noqa: F811
    _use(client, Exploding())
    for route in ("analysis", "questions", "bindings", "exclusions", "decisions", "ai-status"):
        assert client.get(f"{analyzed}/{route}").status_code == 200, route
    assert client.post(f"{analyzed}/analyze").status_code == 200


def test_ask_ai_cycle(client, analyzed):  # noqa: F811
    status = None
    p = Scripted()
    _use(client, p)
    candidates = client.get(f"{analyzed}/ai-status").json()["candidates"]
    r = client.post(f"{analyzed}/enrich-ai", json={})
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["provider"], out["model"], out["prompt_version"]) == ("scripted", "model-t", PROMPT_VERSION)
    assert out["targets"] == candidates and out["no_confident_match"] + out["proposed"] >= 1
    questions = {q["path"]: q for q in client.get(f"{analyzed}/questions").json()}
    unsure = [q for q in questions.values() if q["note_ai"] == "UI helper"]
    assert unsure and all(q["semantic_key"] is None for q in unsure)
    status = client.get(f"{analyzed}/ai-status").json()
    assert status["calls"] == len(p.calls) and status["candidates"] == 0 and status["ai_unresolved"] >= 1
    assert status["input_tokens"] == 500 * len(p.calls)

    # Ask AI sur un champ précis, forcé : proposition du LLM visible dans la question
    target = unsure[0]
    entity, field = target["entity_technical_name"], target["field_technical_name"]
    offered = next(r.vocabulary for r in p.calls if r.path == target["path"])
    if not offered:
        pytest.skip("aucune clé compatible pour ce champ dans la fixture réduite")
    key = offered[0].key
    _use(
        client,
        Scripted(
            {
                target["path"]: {
                    "outcome": "MATCH",
                    "semantic_key": key,
                    "confidence": 0.9,
                    "justification": "Same meaning.",
                }
            }
        ),
    )
    r = client.post(
        f"{analyzed}/enrich-ai", json={"fields": [{"entity": entity, "field": field}], "force": True}
    )
    assert r.json()["proposed"] + r.json()["low_confidence"] + r.json()["no_confident_match"] == 1
    q = {q["path"]: q for q in client.get(f"{analyzed}/questions").json()}[target["path"]]
    if r.json()["proposed"]:
        assert (q["source"], q["semantic_key"], q["justification"], q["meaning_status"]) == (
            "LLM",
            key,
            "Same meaning.",
            "PROPOSED",
        )


def test_unknown_field_is_404(client, analyzed):  # noqa: F811
    _use(client, Scripted())
    r = client.post(
        f"{analyzed}/enrich-ai", json={"fields": [{"entity": "product.template", "field": "nope"}]}
    )
    assert r.status_code == 404


def test_cli_enrich_ai_with_replay_and_record(client, analyzed, session, monkeypatch, tmp_path):  # noqa: F811
    """La CLI appelle le même service que l'API ; --replay-file rejoue, --record-replay enregistre."""
    import json
    from contextlib import contextmanager

    from typer.testing import CliRunner
    from uie_cli import product_domain as cli_pd
    from uie_cli.main import app as cli_app

    @contextmanager
    def test_session():
        yield session

    monkeypatch.setattr(cli_pd, "session_scope", test_session)
    monkeypatch.setenv("COLUMNS", "250")
    for name in ("UIE_LLM_PROVIDER", "UIE_ANTHROPIC_API_KEY", "UIE_OPENAI_API_KEY", "UIE_LLM_REPLAY_FILE"):
        monkeypatch.delenv(name, raising=False)
    runner = CliRunner()
    assert (
        "Aucun fournisseur"
        in runner.invoke(cli_app, ["product-domain", "enrich-ai", "-i", "customer17"]).output
    )

    target = client.get(f"{analyzed}/ai-status").json()
    n = min(3, target["candidates"])
    assert n > 0
    replay = tmp_path / "replay.json"
    replay.write_text(
        json.dumps(
            {"format": "uie-llm-replay/1", "provider": "anthropic", "model": "m-rec", "responses": {}}
        ),
        encoding="utf-8",
    )
    recorded = tmp_path / "recorded.json"
    res = runner.invoke(
        cli_app,
        [
            "product-domain",
            "enrich-ai",
            "-i",
            "customer17",
            "--replay-file",
            str(replay),
            "--record-replay",
            str(recorded),
            "--max-fields",
            "3",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "Ask AI replay" in res.output and f"erreurs {n}" in res.output  # champs non enregistrés
    data = json.loads(recorded.read_text("utf-8"))
    assert data["format"] == "uie-llm-replay/1" and len(data["responses"]) == n
    status = runner.invoke(cli_app, ["product-domain", "ai-status", "-i", "customer17"])
    assert status.exit_code == 0 and f"appels {n}" in status.output


def test_web_calls_enrich_ai_only_from_a_click():
    """Console : l'endpoint enrich-ai n'est appelé que par une mutation (clic « Ask AI »), jamais par une
    requête exécutée à l'ouverture de la page."""
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "web" / "src"
    users = [
        p for p in src.rglob("*.ts*") if "enrich-ai" in p.read_text("utf-8") and "schema.d.ts" not in p.name
    ]
    assert [p.name for p in users] == ["hooks.ts"]
    hooks = (src / "lib" / "api" / "hooks.ts").read_text("utf-8")
    block = re.search(r"export function useEnrichAi\(.*?\n}\n", hooks, re.S).group(0)
    assert "useMutation(" in block and "useQuery(" not in block and hooks.count("enrich-ai") == 1
    view = (src / "app" / "instances" / "[id]" / "catalog" / "catalog-view.tsx").read_text("utf-8")
    assert (
        "enrich.mutate(" in view
        and "useEffect" not in view.split("function AskAi")[1].split("\nfunction ")[0]
    )
