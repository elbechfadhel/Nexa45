"""Mesure V1c figée : customer17 (dump complet) + réponses enregistrées d'un vrai fournisseur (replay).

domain-packs/catalog/llm/replay/customer17_v1c.json : réponses brutes de openai / gpt-5.6-sol au prompt
1.1.0, enregistrées une fois (uie product-domain enrich-ai --record-replay), puis rejouées hors ligne à
travers la même validation que le fournisseur réel. Aucun réseau.

Mesure à trois niveaux :
    Rules only       430 champs → 15 ambiguïtés (POSSIBLE hors copies related)
    Rules + LLM      15 → 10 sans proposition fiable (5 propositions ≥ 0.85, 0 faible confiance)
    Human            15 questions → 0
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest
from test_catalog_customer17_v1b1 import _analyze
from uie_ai import ReplayProposer
from uie_core.domains import DecisionSource, MeaningStatus, ProductDomainService, SemanticEnrichmentService
from uie_core.identity import SystemContext

REPLAY = Path(__file__).resolve().parents[1] / "catalog" / "llm" / "replay" / "customer17_v1c.json"

EXPECTED_PROPOSALS = {
    "product.attribute.value.image": "attribute_value_image",
    "product.supplierinfo.discount": "supplier_discount",
    "product.supplierinfo.price_discounted": "supplier_net_price",
    "product.template.product_properties": "property_values",
    "uom.uom.factor_inv": "uom_ratio",
}
NO_COMPATIBLE_KEY = {"product.template.attribute.value.color", "product.template.color", "uom.uom.color"}
# prompt 1.1.0 : clé déjà tenue → le modèle s'abstient en nommant le champ qui la tient
HELD_BY_OTHER = {"product.product.code": "Same data as product.product.default_code (sku)"}
# arbitrage d'un lot : ratio et factor_inv visent uom_ratio, la plus haute confiance (factor_inv) la garde
CONFLICTS = {"uom.uom.ratio": "Conflicts with uom.uom.factor_inv (uom_ratio)"}


@pytest.fixture
def customer17(session, monkeypatch):  # noqa: ANN001, ANN201
    inst, _, _ = _analyze(session, "17", "odoo17_customer.json")
    replay = ReplayProposer(REPLAY)
    svc = SemanticEnrichmentService(session, SystemContext("test"), proposer=replay)
    # réponses de référence du supplier (V1b) : décisions humaines, jamais envoyées au LLM
    svc.confirm(inst.id, "product.template", "x_supplier_ref")
    svc.change(inst.id, "product.template", "x_pack_code", "packaging_code")
    svc.ignore(inst.id, "product.template", "x_notes_internal")

    def no_network(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("replay : aucun accès réseau attendu")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    return inst, svc, replay


def test_replay_file_is_clean():
    data = json.loads(REPLAY.read_text("utf-8"))
    assert (data["format"], data["provider"], data["prompt_version"]) == (
        "uie-llm-replay/1",
        "openai",
        "1.1.0",
    )
    text = REPLAY.read_text("utf-8")
    for forbidden in ("customer17", "localhost", "8027", "sk-", "password"):
        assert forbidden not in text, forbidden


def test_three_level_measure(customer17):
    inst, svc, replay = customer17
    rules_only = svc.summary(inst.id)
    assert (rules_only.total, rules_only.pending) == (430, 0)
    assert svc.ai_status(inst.id).candidates == 15  # Rules only : 430 → 15 ambiguïtés

    out = svc.enrich_with_llm(inst.id)
    assert (out.targets, out.proposed, out.low_confidence, out.no_confident_match, out.errors) == (
        15,
        5,
        0,
        10,
        0,
    )
    assert len(replay.calls) == 12  # 3 champs sans clé compatible : aucun appel
    by_path = {f.path: f for f in out.fields}
    assert {p: f.semantic_key for p, f in by_path.items() if f.outcome == "PROPOSED"} == EXPECTED_PROPOSALS
    assert all(by_path[p].confidence >= 0.85 for p in EXPECTED_PROPOSALS)
    assert {
        p for p, f in by_path.items() if (f.reason or "").startswith("No vocabulary key")
    } == NO_COMPATIBLE_KEY
    for path, reason in {**HELD_BY_OTHER, **CONFLICTS}.items():
        assert by_path[path].reason.startswith(reason), (path, by_path[path].reason)

    after = svc.summary(inst.id)
    assert after.pending == 15  # Rules + LLM : 5 propositions + 10 questions sans suggestion
    questions = svc.get_pending_questions(inst.id)
    assert sum(1 for q in questions if q.source == DecisionSource.LLM) == 5
    assert sum(1 for q in questions if q.note_ai and q.semantic_key is None) == 10

    # Human : 15 → 0 (ici : on accepte les propositions, on ignore le reste)
    for q in questions:
        if q.source == DecisionSource.LLM:
            svc.confirm(inst.id, q.entity_technical_name, q.field_technical_name)
        else:
            svc.ignore(inst.id, q.entity_technical_name, q.field_technical_name)
    done = svc.summary(inst.id)
    assert (done.pending, done.complete) == (0, True)


def test_human_decisions_stay_intact_and_no_call_on_reanalysis(customer17, session):
    inst, svc, replay = customer17
    before = {d.path: (d.meaning_status, d.semantic_key) for d in svc.list_decisions(inst.id)}
    svc.enrich_with_llm(inst.id)
    after = {d.path: (d.meaning_status, d.semantic_key) for d in svc.list_decisions(inst.id)}
    assert after == before and len(before) == 3
    calls = len(replay.calls)
    # nouvelle découverte + analyse : propositions conservées, aucun appel LLM

    ProductDomainService(session, SystemContext("test")).analyze(inst.id)
    assert len(replay.calls) == calls
    q = {v.path: v for v in svc.get_pending_questions(inst.id)}
    assert (q["product.supplierinfo.discount"].source, q["product.supplierinfo.discount"].meaning_status) == (
        DecisionSource.LLM,
        MeaningStatus.PROPOSED,
    )
    assert svc.ai_status(inst.id).candidates == 0  # cache : rien à renvoyer
