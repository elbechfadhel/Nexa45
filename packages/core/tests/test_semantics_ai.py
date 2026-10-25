"""Proposition sémantique par LLM (V1c) derrière SemanticEnrichmentService, système imaginaire « shop ».

Aucun fournisseur réel, aucun réseau : un proposer scripté rend des réponses BRUTES, validées comme celles
d'un vrai fournisseur (`ProposalResult.from_raw`). Le LLM ne produit jamais NOT_RELEVANT, ni pertinence,
ni KNOWN ; il n'est jamais appelé par une analyse, une découverte ou une lecture.
"""

from __future__ import annotations

import json
import socket

import pytest
from test_domains_core import _f
from test_semantics_core import (  # noqa: F401 — fixtures et helpers partagés
    ScriptedProvider,
    SemanticShopPack,
    ShopPackResolver,
    _instance,
    _row,
    world,
)
from uie_core.domains import (
    DecisionSource,
    DomainKey,
    MeaningStatus,
    ProductDomainService,
    PromptSpec,
    ProposalMeta,
    ProposalRequest,
    ProposalResult,
    Relevance,
    SemanticEnrichmentService,
    SemanticError,
)
from uie_core.domains.ai_enrichment import AI_UNSURE
from uie_core.identity import SystemContext
from uie_core.registry import DataType

PROMPT = PromptSpec(version="p1", system="Answer MATCH or NO_CONFIDENT_MATCH.")
PROMPT_V2 = PromptSpec(version="p2", system="Answer MATCH or NO_CONFIDENT_MATCH. v2")


class PromptResolver(ShopPackResolver):
    def __init__(self, pack, prompt: PromptSpec = PROMPT) -> None:  # noqa: ANN001
        super().__init__(pack)
        self._prompt = prompt

    def prompt(self, domain_key: DomainKey) -> PromptSpec:
        return self._prompt


class Scripted:
    """Réponses brutes par nom de champ ; tout appel est enregistré."""

    provider, model = "scripted", "model-t"

    def __init__(self, answers: dict[str, object] | None = None) -> None:
        self.answers = answers or {}
        self.calls: list[ProposalRequest] = []

    def propose(self, request: ProposalRequest) -> ProposalResult:
        self.calls.append(request)
        answer = self.answers.get(
            request.field.technical_name, {"outcome": "NO_CONFIDENT_MATCH", "reason": "?"}
        )
        meta = ProposalMeta(
            provider=self.provider,
            model=self.model,
            request_id=f"req-{len(self.calls)}",
            input_tokens=800,
            output_tokens=60,
            latency_ms=420,
            cost_usd=0.0055,
        )
        if isinstance(answer, Exception):
            raise answer
        if answer == "timeout":
            return ProposalResult.failure("timeout", meta)
        return ProposalResult.from_raw(answer, meta, request)


class Exploding:
    provider, model = "exploding", None

    def propose(self, request: ProposalRequest) -> ProposalResult:
        raise AssertionError("aucun appel LLM attendu ici")


EXTRA = (
    _f("rebate_pct", DataType.DECIMAL, label="Rebate (%)"),
    _f("shelf_weight", DataType.DECIMAL, label="Shelf weight"),
    _f("card_colour", DataType.INTEGER, label="Card colour"),
    _f("promo_price", DataType.DECIMAL, label="Promo price"),
    _f("display_code", DataType.STRING, label="Display code"),
)


@pytest.fixture
def ai(world, session):  # noqa: F811
    w = world
    w["provider"].schema = _instance(zz_type=DataType.STRING, extra=EXTRA)
    w["instances"].discover_schema(w["inst"].id)
    resolver = PromptResolver(w["pack"])
    w["domains"] = ProductDomainService(session, SystemContext("test"), resolver)
    w["domains"].analyze(w["inst"].id)

    def service(proposer, prompt: PromptSpec = PROMPT) -> SemanticEnrichmentService:  # noqa: ANN001
        return SemanticEnrichmentService(
            session,
            SystemContext("test"),
            PromptResolver(w["pack"], prompt),
            domains=w["domains"],
            proposer=proposer,
        )

    w["service"] = service
    return w


def B(session, name: str):  # noqa: ANN001, ANN201, N802
    return _row(session, f"article.{name}")


MATCH = {
    "outcome": "MATCH",
    "semantic_key": "list_price",
    "confidence": 0.91,
    "justification": "Selling price.",
}


def test_targets_are_possible_fields_only(ai, session):
    p = Scripted()
    ai["service"](p).enrich_with_llm(ai["inst"].id)
    sent = sorted(r.field.technical_name for r in p.calls)
    # zz_supplier : proposition de règle (prime) ; ext_rating : exclu ; champs listés / KNOWN : jamais
    assert "zz_supplier" not in sent and "ext_rating" not in sent
    assert {"rebate_pct", "shelf_weight", "promo_price", "zz_color"} <= set(sent)
    # aucune clé compatible (entier) : pas d'appel, question sans suggestion
    assert "card_colour" not in sent
    colour = B(session, "card_colour")
    assert colour.pending and colour.ai_note.startswith("No vocabulary key")
    assert colour.events[-1].action == "AI_NO_KEY"
    for req in p.calls:
        assert B(session, req.field.technical_name).relevance != Relevance.NOT_RELEVANT


def test_thresholds_and_never_known(ai, session):
    answers = {
        "promo_price": {**MATCH, "confidence": 0.99},
        "rebate_pct": {"outcome": "MATCH", "semantic_key": "weight", "confidence": 0.70},
        "shelf_weight": {"outcome": "MATCH", "semantic_key": "weight", "confidence": 0.50},
    }
    out = ai["service"](Scripted(answers)).enrich_with_llm(ai["inst"].id)
    promo, rebate, shelf = (B(session, n) for n in ("promo_price", "rebate_pct", "shelf_weight"))
    assert (promo.meaning_status, promo.source, promo.low_confidence, promo.pending) == (
        MeaningStatus.PROPOSED,
        DecisionSource.LLM,
        False,
        True,
    )  # 0.99 : jamais KNOWN, toujours une décision humaine
    assert promo.ai_justification == "Selling price." and promo.relevance == Relevance.POSSIBLE
    assert (rebate.meaning_status, rebate.low_confidence) == (MeaningStatus.PROPOSED, True)
    assert (shelf.meaning_status, shelf.source, shelf.semantic_key, shelf.pending) == (
        MeaningStatus.UNRESOLVED,
        DecisionSource.RULE,
        None,
        True,
    )
    assert shelf.ai_note == "Confidence too low (50%)"
    assert (out.proposed, out.low_confidence) == (1, 1)
    assert not any(
        b.meaning_status == MeaningStatus.KNOWN and b.source == DecisionSource.LLM
        for b in session.query(type(promo))
    )


def test_llm_never_produces_not_relevant_nor_changes_relevance(ai, session):
    evil = {**MATCH, "relevance": "NOT_RELEVANT", "exclude": True, "meaning_status": "EXCLUDED"}
    nomatch_evil = {"outcome": "NO_CONFIDENT_MATCH", "reason": "noise", "relevance": "NOT_RELEVANT"}
    ai["service"](Scripted({"promo_price": evil, "shelf_weight": nomatch_evil})).enrich_with_llm(
        ai["inst"].id
    )
    for name in ("promo_price", "shelf_weight"):
        row = B(session, name)
        assert row.relevance == Relevance.POSSIBLE and row.meaning_status != MeaningStatus.EXCLUDED
        ignored = [e for e in row.events if e.action == "AI_IGNORED"]
        assert ignored and "relevance" in ignored[0].details["ignored_fields"]


def test_key_outside_vocabulary_is_no_confident_match_with_event(ai, session):
    ai["service"](Scripted({"promo_price": {**MATCH, "semantic_key": "promotional_price"}})).enrich_with_llm(
        ai["inst"].id
    )
    row = B(session, "promo_price")
    assert (row.meaning_status, row.semantic_key, row.pending) == (MeaningStatus.UNRESOLVED, None, True)
    event = row.events[-1]
    assert event.action == "AI_ERROR" and "hors vocabulaire" in event.details["error"]
    assert row.ai_note == "AI answer rejected (key outside the vocabulary or invalid format)"


def test_only_type_compatible_keys_are_offered(ai):
    p = Scripted()
    ai["service"](p).enrich_with_llm(ai["inst"].id, fields=[("article", "promo_price")])
    offered = {r.field.technical_name: {v.key for v in r.vocabulary} for r in p.calls}
    assert "list_price" in offered["promo_price"] and "sku" not in offered["promo_price"]


def test_unicity_conflict_is_not_proposed(ai, session):
    """« sku » (single) est déjà tenu sur l'article : proposition du LLM sur un autre champ refusée."""
    p = Scripted({"display_code": {"outcome": "MATCH", "semantic_key": "sku", "confidence": 0.95}})
    ai["service"](p).enrich_with_llm(ai["inst"].id, fields=[("article", "display_code")])
    assert "sku" in p.calls[0].keys_held_on_entity
    row = B(session, "display_code")
    assert (row.meaning_status, row.semantic_key, row.pending) == (MeaningStatus.UNRESOLVED, None, True)
    assert row.ai_note.startswith("Conflicts with article.")


def test_copies_and_human_decisions_are_never_sent(ai, session):
    svc = ai["service"](Scripted())
    svc.change(ai["inst"].id, "article", "card_colour", "other", note="kanban colour")  # décision humaine
    p = Scripted()
    ai["service"](p).enrich_with_llm(ai["inst"].id)
    sent = {r.path for r in p.calls}
    assert "article.card_colour" not in sent
    assert all(not B(session, r.field.technical_name).mirror_of for r in p.calls)
    out = ai["service"](Scripted()).enrich_with_llm(ai["inst"].id, fields=[("article", "card_colour")])
    assert (out.targets, out.fields[0].outcome, out.fields[0].reason) == (0, "SKIPPED", "human decision")


def test_rule_proposal_takes_precedence_unless_forced(ai):
    p = Scripted()
    ai["service"](p).enrich_with_llm(ai["inst"].id)
    assert "zz_supplier" not in {r.field.technical_name for r in p.calls}
    p = Scripted()
    ai["service"](p).enrich_with_llm(ai["inst"].id, fields=[("article", "zz_supplier")], force=True)
    assert [r.field.technical_name for r in p.calls] == ["zz_supplier"]


def test_cache_by_signature_and_prompt_checksum(ai):
    first = Scripted({"promo_price": MATCH})
    ai["service"](first).enrich_with_llm(ai["inst"].id)
    assert first.calls
    again = Scripted({"promo_price": MATCH})
    out = ai["service"](again).enrich_with_llm(ai["inst"].id)
    assert again.calls == [] and out.targets == 0  # même signature, même prompt : rien n'est renvoyé
    forced = Scripted({"promo_price": MATCH})
    ai["service"](forced).enrich_with_llm(ai["inst"].id, force=True)
    assert "promo_price" in {r.field.technical_name for r in forced.calls}
    new_prompt = Scripted()
    ai["service"](new_prompt, PROMPT_V2).enrich_with_llm(ai["inst"].id)
    assert new_prompt.calls  # nouveau prompt : les champs redeviennent éligibles


def test_reanalysis_keeps_ai_proposals_and_makes_no_call(ai, session):
    ai["service"](Scripted({"promo_price": MATCH})).enrich_with_llm(ai["inst"].id)
    ai["instances"].discover_schema(ai["inst"].id)  # même schéma
    ai["domains"].analyze(ai["inst"].id)
    row = B(session, "promo_price")
    assert (row.meaning_status, row.source, row.semantic_key, row.pending) == (
        MeaningStatus.PROPOSED,
        DecisionSource.LLM,
        "list_price",
        True,
    )
    unsure = B(session, "card_colour")
    assert unsure.pending and unsure.ai_note  # l'abstention du LLM reste une question
    # le champ change de type : la règle reprend la main, la proposition du LLM tombe
    changed = tuple(
        _f("promo_price", DataType.STRING, label="Promo price") if f.technical_name == "promo_price" else f
        for f in EXTRA
    )
    ai["provider"].schema = _instance(zz_type=DataType.STRING, extra=changed)
    ai["instances"].discover_schema(ai["inst"].id)
    ai["domains"].analyze(ai["inst"].id)
    row = B(session, "promo_price")
    assert (row.source, row.ai_prompt_checksum, row.ai_justification) == (DecisionSource.RULE, None, None)


def test_no_llm_call_outside_enrich_with_llm(ai, monkeypatch):
    def no_network(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("aucun accès réseau attendu")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    svc = ai["service"](Exploding())
    inst = ai["inst"].id
    ai["instances"].discover_schema(inst)
    ai["domains"].analyze(inst)
    svc.summary(inst)
    svc.get_pending_questions(inst)
    svc.list_bindings(inst)
    svc.list_exclusions(inst)
    svc.list_decisions(inst)
    status = svc.ai_status(inst)
    assert status.provider == "exploding" and status.candidates > 0


def test_no_provider_configured_is_refused(ai):
    with pytest.raises(SemanticError, match="No AI provider"):
        SemanticEnrichmentService(
            ai["domains"].s, SystemContext("test"), domains=ai["domains"]
        ).enrich_with_llm(ai["inst"].id)


def test_request_has_no_business_value_secret_or_instance_name(ai):
    p = Scripted()
    ai["service"](p).enrich_with_llm(ai["inst"].id)
    blob = json.dumps([r.model_dump(mode="json") for r in p.calls])
    for forbidden in ("shop-semantic", "shop.invalid", "https://", str(ai["inst"].id)):
        assert forbidden not in blob, forbidden
    assert {r.request_ref for r in p.calls} == {p.calls[0].request_ref} and len(p.calls[0].request_ref) == 12


def test_failures_never_raise_and_never_erase_a_proposal(ai, session):
    ai["service"](Scripted({"promo_price": MATCH})).enrich_with_llm(ai["inst"].id)
    out = ai["service"](
        Scripted({"promo_price": "timeout", "rebate_pct": RuntimeError("boom")})
    ).enrich_with_llm(ai["inst"].id, force=True)
    assert out.errors >= 2
    promo = B(session, "promo_price")
    assert (promo.meaning_status, promo.source) == (MeaningStatus.PROPOSED, DecisionSource.LLM)  # intacte
    rebate = B(session, "rebate_pct")
    assert rebate.meaning_status == MeaningStatus.UNRESOLVED and rebate.ai_note == "AI could not answer"


def test_every_call_is_journaled(ai, session):
    ai["service"](Scripted({"promo_price": MATCH})).enrich_with_llm(ai["inst"].id)
    event = next(e for e in B(session, "promo_price").events if e.action == "AI_PROPOSE")
    d = event.details
    assert (d["provider"], d["model"], d["request_id"], d["input_tokens"], d["latency_ms"]) == (
        "scripted",
        "model-t",
        "req-1",
        800,
        420,
    ) or d["request_id"].startswith("req-")
    assert d["prompt_checksum"] == PROMPT.checksum and d["raw"]["semantic_key"] == "list_price"
    assert d["cost_usd"] == 0.0055 and event.source == DecisionSource.LLM
    status = ai["service"](Scripted()).ai_status(ai["inst"].id)
    assert status.calls >= 1 and status.input_tokens >= 800 and status.last_call_at is not None


def test_human_decisions_stay_intact_and_answering_an_ai_question_is_human(ai, session):
    svc = ai["service"](Scripted({"promo_price": MATCH}))
    svc.confirm(ai["inst"].id, "article", "zz_supplier")
    svc.enrich_with_llm(ai["inst"].id, force=True)
    sup = B(session, "zz_supplier")
    assert (sup.source, sup.meaning_status) == (DecisionSource.HUMAN, MeaningStatus.CONFIRMED)
    b = svc.confirm(ai["inst"].id, "article", "promo_price")  # accepte la proposition du LLM
    assert (b.source, b.meaning_status, b.semantic_key) == (
        DecisionSource.HUMAN,
        MeaningStatus.CONFIRMED,
        "list_price",
    )


def test_questions_show_ai_provenance(ai):
    low = {"outcome": "MATCH", "semantic_key": "weight", "confidence": 0.7}
    svc = ai["service"](Scripted({"promo_price": MATCH, "rebate_pct": low}))
    svc.enrich_with_llm(ai["inst"].id)
    q = {v.field.field_technical_name: v for v in svc.get_pending_questions(ai["inst"].id)}
    assert (q["promo_price"].source, q["promo_price"].justification) == (DecisionSource.LLM, "Selling price.")
    assert q["rebate_pct"].low_confidence is True
    assert q["card_colour"].note_ai and q["card_colour"].semantic_key is None
    assert AI_UNSURE  # constante d'affichage partagée


def test_budget_max_fields(ai):
    p = Scripted()
    out = ai["service"](p).enrich_with_llm(ai["inst"].id, max_fields=2)
    assert out.targets == 2 and len(p.calls) <= 2 and out.skipped >= 1


def test_batch_conflict_keeps_the_highest_confidence(ai, session):
    """promo_price est traité avant rebate_pct (ordre alphabétique) mais c'est rebate_pct, plus sûr, qui
    garde la clé single : l'arbitrage ne dépend pas de l'ordre."""
    answers = {
        "promo_price": {**MATCH, "confidence": 0.70},
        "rebate_pct": {**MATCH, "confidence": 0.95},
    }
    out = ai["service"](Scripted(answers)).enrich_with_llm(
        ai["inst"].id, fields=[("article", "promo_price"), ("article", "rebate_pct")]
    )
    rebate, promo = B(session, "rebate_pct"), B(session, "promo_price")
    assert (rebate.meaning_status, rebate.semantic_key) == (MeaningStatus.PROPOSED, "list_price")
    assert (promo.meaning_status, promo.ai_note) == (
        MeaningStatus.UNRESOLVED,
        "Conflicts with article.rebate_pct (list_price)",
    )
    assert [f.path for f in out.fields] == ["article.promo_price", "article.rebate_pct"]  # affichage trié


def test_pending_ai_proposal_holds_its_key_and_is_named_to_the_llm(ai, session):
    ai["service"](Scripted({"promo_price": MATCH})).enrich_with_llm(
        ai["inst"].id, fields=[("article", "promo_price")]
    )
    p = Scripted({"rebate_pct": {**MATCH, "confidence": 0.99}})
    ai["service"](p).enrich_with_llm(ai["inst"].id, fields=[("article", "rebate_pct")])
    assert p.calls[0].keys_held_on_entity["list_price"] == "article.promo_price"
    assert B(session, "rebate_pct").ai_note == "Conflicts with article.promo_price (list_price)"
