"""V2c.3 : fallback LLM des imports (domaine RÉEL de customer17, proposer factice, aucun réseau).

EMPTY → NOT_RELEVANT appris → mapping appris → règles → LLM pour ce qui reste. Le LLM ne passe jamais devant
la mémoire ni les règles, ne prend jamais une cible tenue, n'écrit jamais la mémoire ; un appel par lot,
cache par organisation, échec jamais mis en cache.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from test_catalog_imports_service import (  # noqa: F401
    LEARN_CSV,
    _cols,
    _learn_setup,
    _memory_rows,
    files,
    svc,
)
from test_catalog_sync import _answer, world  # noqa: F401 (fixture)
from uie_core.domains import ProposalMeta
from uie_core.imports import CatalogImportService, ColumnProposalBatch, ColumnProposalRequest
from uie_core.imports.records import CatalogImportRecord


class FakeProposer:
    """Répond d'après l'en-tête ; enregistre chaque requête (ce qui est envoyé, nombre d'appels)."""

    provider, model = "fake", "fake-1"

    def __init__(self, answers: dict[str, dict], error: str | None = None) -> None:
        self.answers, self.error = answers, error
        self.calls: list[ColumnProposalRequest] = []

    def propose_columns(self, request: ColumnProposalRequest) -> ColumnProposalBatch:
        self.calls.append(request)
        meta = ProposalMeta(
            provider="fake", model="fake-1", input_tokens=100, output_tokens=20, cost_usd=0.01
        )
        if self.error:
            return ColumnProposalBatch.failure(self.error, meta, request)
        items = [
            {
                "position": c.position,
                "source_header": c.header,
                "semantic_key": "",
                "reason": "test",
                **self.answers[c.header],
            }
            for c in request.columns
            if c.header in self.answers
        ]
        return ColumnProposalBatch.from_raw({"columns": items}, meta, request)


def TARGET(key: str, confidence: float) -> dict:  # noqa: N802
    return {"decision": "TARGET", "semantic_key": key, "confidence": confidence}


def NOT_RELEVANT(confidence: float) -> dict:  # noqa: N802
    return {"decision": "NOT_RELEVANT", "confidence": confidence}


@pytest.fixture
def ai(world, session, files):  # noqa: ANN001, ANN201, F811
    def make(slug: str, proposer: FakeProposer) -> CatalogImportService:
        sync, _ = world["tenant"](slug)
        return CatalogImportService(session, sync, files, proposer)

    return make


def _state(view):  # noqa: ANN001, ANN202
    return {c.source_name: (c.status, c.reason, c.target_id, c.decided_by, c.ignored) for c in view.columns}


def _analyze(svc, world, data=LEARN_CSV, slug="acme"):  # noqa: ANN001, ANN202, F811
    imp = svc.upload("first.csv", data).record
    return svc.analyze(imp.id, world["inst"][slug].id)


def test_only_the_remaining_columns_are_sent_once_and_high_confidence_resolves(ai, world, session):  # noqa: F811
    _answer(world)
    fake = FakeProposer(
        {"Kuerzel": TARGET("sku", 0.95), "Price": TARGET("list_price", 0.96), "Material": NOT_RELEVANT(0.97)}
    )
    view = _analyze(ai("acme", fake), world)
    assert len(fake.calls) == 1
    req = fake.calls[0]
    sent = {c.header: c for c in req.columns}
    assert list(sent) == ["Kuerzel", "Price", "Material"]  # « Product Name » (règles) n'est pas envoyée
    assert sent["Price"].rules_suggestion == "list_price" and sent["Kuerzel"].rules_suggestion is None
    assert sent["Material"].examples == ["Oak", "Steel"] and sent["Price"].fill_ratio == 1.0
    assert {t.key for t in req.targets} >= {"sku", "list_price", "name"} and req.system == "odoo"
    assert req.request_ref.startswith("imp-") and "acme" not in req.model_dump_json()

    assert _state(view) == {
        "Product Name": ("MATCHED", None, "product:name", "AUTO", False),
        "Kuerzel": ("MATCHED", None, "product:sku", "AI", False),
        "Price": ("MATCHED", None, "product:list_price", "AI", False),  # la même clé que les règles
        "Material": ("REVIEW", "AI_NOT_RELEVANT", None, "AI", False),  # jamais ignorée d'office
    }
    rec = view.record
    assert (rec.resolved_auto, rec.resolved_ai, rec.suggested_ai, rec.not_relevant_ai) == (1, 2, 0, 1)
    assert rec.needs_attention == 1 and view.status == "ANALYZED"
    assert _cols(view)["Kuerzel"].confidence == 0.95
    assert _memory_rows(session) == {}  # le LLM n'écrit jamais la mémoire
    assert rec.ai_result["calls"] == 1 and rec.ai_result["input_tokens"] == 100
    assert "Oak" not in str(rec.ai_result)  # les exemples envoyés ne sont jamais conservés


def test_medium_low_invalid_and_disagreement(ai, world):  # noqa: F811
    _answer(world)
    fake = FakeProposer(
        {
            "Kuerzel": TARGET("sku", 0.75),  # moyen → suggestion
            "Price": TARGET("name", 0.99),  # contredit la suggestion des règles → ignoré
            "Material": NOT_RELEVANT(0.8),  # NOT_RELEVANT sous le seuil → inchangé
        }
    )
    got = _state(_analyze(ai("acme", fake), world))
    assert got["Kuerzel"] == ("REVIEW", "AI_SUGGESTION", "product:sku", "AI", False)
    assert got["Price"] == ("REVIEW", "LOW_CONFIDENCE", "product:list_price", "AUTO", False)
    assert got["Material"] == ("UNMATCHED", None, None, "AUTO", False)

    fake = FakeProposer({"Kuerzel": TARGET("sku", 0.5), "Material": TARGET("x_invented", 0.99)})
    fake.model = "fake-2"  # autre empreinte : sinon le cache de l'organisation répondrait (même fichier)
    got = _state(_analyze(ai("acme", fake), world))
    assert got["Kuerzel"][0] == got["Material"][0] == "UNMATCHED"  # faible, puis cible inventée


def test_ai_never_takes_a_held_target_nor_an_incompatible_type(ai, world):  # noqa: F811
    _answer(world)
    fake = FakeProposer(
        {
            "Material": TARGET("name", 0.97),  # tenue par « Product Name » (règles)
            "Kuerzel": TARGET("weight", 0.97),  # texte → poids : jamais automatique
            "Price": TARGET("list_price", 0.8),  # même clé mais pas assez sûr : suggestion des règles gardée
        }
    )
    got = _state(_analyze(ai("acme", fake), world))
    assert got["Material"] == ("REVIEW", "TARGET_TAKEN", None, "AI", False)
    assert got["Kuerzel"] == ("REVIEW", "AI_SUGGESTION", "product:weight", "AI", False)
    assert got["Price"] == ("REVIEW", "LOW_CONFIDENCE", "product:list_price", "AUTO", False)


def test_two_ai_columns_on_one_target_the_most_confident_keeps_it(ai, world):  # noqa: F811
    _answer(world)
    fake = FakeProposer({"Kuerzel": TARGET("sku", 0.91), "Material": TARGET("sku", 0.96)})
    got = _state(_analyze(ai("acme", fake), world))
    assert got["Material"][:3] == ("MATCHED", None, "product:sku")
    assert got["Kuerzel"][:3] == ("REVIEW", "TARGET_TAKEN", None)


def test_cache_per_organization_and_failures_are_retried(ai, world, session):  # noqa: F811
    _answer(world)
    fake = FakeProposer({"Material": NOT_RELEVANT(0.97)})
    acme = ai("acme", fake)
    first = _analyze(acme, world)
    acme.analyze(first.record.id, world["inst"]["acme"].id)  # ré-analyse
    _analyze(acme, world)  # même fichier, nouvel import
    assert len(fake.calls) == 1
    assert _cols(acme.get(first.record.id))["Material"].reason == "AI_NOT_RELEVANT"

    # autre organisation : jamais le cache d'une autre
    world["enrichment"].domains.analyze(world["inst"]["globex"].id)
    e, gid = world["enrichment"], world["inst"]["globex"].id
    e.confirm(gid, "product.template", "x_supplier_ref")
    e.change(gid, "product.template", "x_pack_code", "packaging_code")
    e.ignore(gid, "product.template", "x_notes_internal")
    other = FakeProposer({"Material": NOT_RELEVANT(0.97)})
    _analyze(ai("globex", other), world, slug="globex")
    assert len(other.calls) == 1

    # échec : colonnes inchangées, rien en cache, retenté à l'analyse suivante
    broken = FakeProposer({}, error="timeout")
    view = _analyze(ai("acme", broken), world, data=b"Colour,Shade\nred,dark\n")
    assert {c.status for c in view.columns} == {"UNMATCHED"} and view.record.ai_result is None
    ai("acme", broken).analyze(view.record.id, world["inst"]["acme"].id)
    assert len(broken.calls) == 2


def test_memory_and_rules_come_first_and_nothing_to_ask_means_no_call(ai, world, svc):  # noqa: F811
    _answer(world)
    acme, first = _learn_setup(svc, world)  # sans LLM : résultats V2c.2b
    cols = _cols(first)
    acme.choose_target(first.record.id, cols["Kuerzel"].position, "product:sku")
    acme.choose_target(first.record.id, cols["Price"].position, "product:list_price")
    acme.ignore_column(first.record.id, cols["Material"].position)
    fake = FakeProposer({"Material": TARGET("name", 0.99)})
    view = _analyze(ai("acme", fake), world)
    assert fake.calls == []  # tout est réglé par la mémoire : aucun appel
    assert view.status == "READY" and view.record.ai_result is None


def test_confirming_or_ignoring_ai_results_is_what_the_memory_learns(ai, world, session):  # noqa: F811
    _answer(world)
    fake = FakeProposer(
        {"Kuerzel": TARGET("sku", 0.75), "Price": TARGET("list_price", 0.96), "Material": NOT_RELEVANT(0.97)}
    )
    acme = ai("acme", fake)
    view = _analyze(acme, world)
    cols = _cols(view)
    acme.choose_target(view.record.id, cols["Kuerzel"].position, "product:sku")  # Confirm de la suggestion
    view = acme.ignore_ai_not_relevant(view.record.id)  # « Ignore all AI not relevant »
    assert _cols(view)["Material"].ignored and view.status == "READY"
    assert _memory_rows(session) == {
        "kuerzel": ("MAPPED", "sku", False),
        "material": ("NOT_RELEVANT", None, True),
    }  # Price (résolu par l'AI, non touché) n'est pas appris
    view = _analyze(acme, world)
    got = _state(view)
    assert got["Kuerzel"][3] == got["Material"][3] == "LEARNED"
    assert len(fake.calls) == 1  # Price (seul reste) : déjà analysée, cache par colonne → aucun appel

    # Ignore d'une colonne résolue par l'AI = correction humaine → apprise
    acme.ignore_column(view.record.id, _cols(view)["Price"].position)
    assert _memory_rows(session)["price"] == ("NOT_RELEVANT", None, True)


def test_without_a_provider_nothing_changes(svc, world, session):  # noqa: F811
    _answer(world)
    view = _analyze(svc("acme"), world)
    rec = view.record
    assert (rec.resolved_ai, rec.suggested_ai, rec.not_relevant_ai, rec.ai_result) == (0, 0, 0, None)
    assert session.scalar(select(CatalogImportRecord.ai_request_checksum)) is None


def test_column_cache_only_asks_the_columns_never_seen(ai, world, session):  # noqa: F811
    """2e import : les colonnes déjà analysées pour l'organisation reprennent leur réponse (0 appel) ; une
    colonne nouvelle ou différente (autres valeurs) part seule au LLM."""
    _answer(world)
    fake = FakeProposer(
        {"Kuerzel": TARGET("sku", 0.95), "Price": TARGET("list_price", 0.96), "Material": NOT_RELEVANT(0.97)}
    )
    acme = ai("acme", fake)
    first = _analyze(acme, world)
    acme.ignore_ai_not_relevant(first.record.id)  # Material → mémoire ; Kuerzel et Price restent à l'AI
    second = _analyze(acme, world)
    assert len(fake.calls) == 1  # Kuerzel et Price : réponses reprises, aucun appel
    assert _state(second)["Kuerzel"][:4] == ("MATCHED", None, "product:sku", "AI")
    assert second.status == "READY" and second.record.ai_result["calls"] == 0
    assert second.record.ai_result["reused_columns"] == 2

    # mêmes en-têtes, autres valeurs pour Kuerzel : seule cette colonne est redemandée
    fake.answers["Kuerzel"] = TARGET("sku", 0.93)
    _analyze(
        acme,
        world,
        data=b"Product Name,Kuerzel,Price,Material\nChair,ZX-9,12.50,Oak\nDesk,ZX-8,99.00,Steel\n",
    )
    assert [c.header for c in fake.calls[-1].columns] == ["Kuerzel"] and len(fake.calls) == 2


def test_llm_auto_mapping_needs_095_rules_and_not_relevant_thresholds_unchanged(ai, world):  # noqa: F811
    """V2c.5b : 0.90-0.949 = suggestion à confirmer (jamais AUTO) ; ≥ 0.95 = AUTO. Seuil des règles (0.90) et
    seuil « Probably not needed » (0.90) inchangés."""
    from uie_core.imports.matching import AI_AUTO_THRESHOLD, AI_NOT_RELEVANT_THRESHOLD, MATCHED_THRESHOLD

    assert (AI_AUTO_THRESHOLD, MATCHED_THRESHOLD, AI_NOT_RELEVANT_THRESHOLD) == (0.95, 0.90, 0.90)
    _answer(world)
    fake = FakeProposer(
        {
            "Kuerzel": TARGET("sku", 0.949),  # sous 0.95 : suggestion pré-remplie
            "Price": TARGET(
                "list_price", 0.92
            ),  # même clé que les règles mais < 0.95 : suggestion des règles
            "Material": NOT_RELEVANT(0.92),  # « Probably not needed » dès 0.90 (inchangé)
        }
    )
    got = _state(_analyze(ai("acme", fake), world))
    assert got["Kuerzel"] == ("REVIEW", "AI_SUGGESTION", "product:sku", "AI", False)
    assert got["Price"] == ("REVIEW", "LOW_CONFIDENCE", "product:list_price", "AUTO", False)
    assert got["Material"] == ("REVIEW", "AI_NOT_RELEVANT", None, "AI", False)

    fake = FakeProposer({"Kuerzel": TARGET("sku", 0.95), "Price": TARGET("list_price", 0.95)})
    fake.model = "fake-2"  # autre empreinte (sinon le cache par colonne répond)
    got = _state(_analyze(ai("acme", fake), world))
    assert got["Kuerzel"][:4] == ("MATCHED", None, "product:sku", "AI")
    assert got["Price"][:4] == ("MATCHED", None, "product:list_price", "AI")
