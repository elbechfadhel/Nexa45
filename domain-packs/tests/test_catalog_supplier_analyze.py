"""V2b.2 : « Analyze » du fournisseur = orchestration du moteur V1 (connexion → découverte → analyse → IA si
nécessaire), sur le domaine RÉEL de customer17 (dump complet) servi par le faux serveur Odoo.

Re-analyze : redécouverte systématique (F2), décisions réutilisées, aucune question résolue reposée, aucun
appel LLM inutile (cache V1c, F1). Aucune synchronisation du catalogue (F3).
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from test_catalog_customer17_v1b1 import DUMPS, PASSWORD
from test_catalog_odoo_instance import FakeOdooResolver
from uie_ai import ReplayProposer
from uie_connectors.odoo import OdooSchemaLoader
from uie_connectors.odoo.testing import FakeOdooServer
from uie_core.catalog.records import CatalogSyncRecord
from uie_core.domains import MeaningStatus, SemanticEnrichmentService
from uie_core.domains.catalog_analysis import CatalogAnalysisFailed, CatalogAnalysisService
from uie_core.identity import MembershipRole, Organization, SystemContext, TenantContext
from uie_core.instances import FernetSecretStore, InstanceCreate, InstanceService
from uie_core.registry import NotFound, RegistryService
from uie_core.registry import models as m

REPLAY = Path(__file__).resolve().parents[1] / "catalog" / "llm" / "replay" / "customer17_v1c.json"


class CountingProposer:
    """Enveloppe un proposer et compte les appels réels au LLM."""

    def __init__(self, inner) -> None:  # noqa: ANN001
        self.inner, self.calls = inner, 0
        self.provider, self.model = inner.provider, inner.model

    def propose(self, request):  # noqa: ANN001, ANN201
        self.calls += 1
        return self.inner.propose(request)


@pytest.fixture
def world(session):  # noqa: ANN201
    RegistryService(session, SystemContext("test")).publish_snapshot(
        OdooSchemaLoader({"json": DUMPS / "odoo17_ref.json"}).build_snapshot()
    )
    fake = FakeOdooServer.from_dump(DUMPS / "odoo17_customer.json", password=PASSWORD)
    store = FernetSecretStore(FernetSecretStore.generate_key())
    orgs = {s: Organization(name=s.title(), slug=s) for s in ("acme", "globex")}
    session.add_all(orgs.values())
    session.flush()
    inst = InstanceService(session, SystemContext("test"), store, FakeOdooResolver(fake)).create(
        InstanceCreate(
            organization_id=orgs["acme"].id,
            name="customer17",
            system="odoo",
            version="17",
            endpoint="http://odoo:8069",
            config={"db": fake.db, "username": "admin"},
            password=SecretStr(PASSWORD),
        )
    )

    def service(slug: str, proposer=None):  # noqa: ANN001, ANN202
        ctx = TenantContext(uuid.uuid4(), orgs[slug].id, MembershipRole.MEMBER)
        instances = InstanceService(session, ctx, store, FakeOdooResolver(fake))
        enrichment = SemanticEnrichmentService(session, ctx, proposer=proposer)
        return CatalogAnalysisService(ctx, instances, enrichment), enrichment

    return {"inst": inst, "fake": fake, "service": service, "session": session}


def _snapshots(session, iid) -> int:  # noqa: ANN001
    return session.scalar(
        select(func.count())
        .select_from(m.InstanceSchemaSnapshot)
        .where(m.InstanceSchemaSnapshot.instance_id == iid)
    )


def test_analyze_without_llm_then_answers_then_reanalyze(world):
    analysis, enrichment = world["service"]("acme")
    iid = world["inst"].id
    first = analysis.analyze(iid)
    assert first.ai is None  # aucun fournisseur LLM configuré : aucun appel
    assert first.snapshot_sequence == 1 and not first.summary.complete
    questions = enrichment.get_pending_questions(iid)
    assert [q.field_technical_name for q in questions] == [
        "x_pack_code",
        "x_supplier_ref",
        "x_notes_internal",
    ]

    enrichment.confirm(iid, "product.template", "x_supplier_ref")
    enrichment.change(iid, "product.template", "x_pack_code", "packaging_code")
    enrichment.ignore(iid, "product.template", "x_notes_internal")
    assert enrichment.summary(iid).complete

    again = analysis.analyze(iid)  # Re-analyze sans changement
    assert again.snapshot_sequence == 2  # la structure est redécouverte (F2)
    assert again.summary.complete and again.summary.pending == 0  # aucune question reposée
    assert again.summary.supplier_decisions == 3  # décisions réutilisées
    statuses = {q.field_technical_name: q.meaning_status for q in enrichment.list_decisions(iid)}
    assert statuses == {
        "x_supplier_ref": MeaningStatus.CONFIRMED,
        "x_pack_code": MeaningStatus.CONFIRMED,
        "x_notes_internal": MeaningStatus.IGNORED,
    }
    assert (
        world["session"].scalar(select(func.count()).select_from(CatalogSyncRecord)) == 0
    )  # pas de sync (F3)


def test_llm_is_called_once_then_never_again_without_changes(world):
    """Cache V1c : un champ qui a reçu une réponse du LLM (proposition ou abstention) n'est JAMAIS
    renvoyé tant que le champ et le prompt sont inchangés. Seul un appel en ERREUR (réseau, délai, réponse
    absente) est retenté à l'analyse suivante — ici x_notes_internal, absent du fichier de replay V1c (le
    fournisseur l'avait ignoré avant l'appel à l'IA lors de l'enregistrement)."""
    proposer = CountingProposer(ReplayProposer(REPLAY))
    analysis, enrichment = world["service"]("acme", proposer)
    iid = world["inst"].id
    first = analysis.analyze(iid)
    assert first.ai is not None and proposer.calls > 0  # targets compte aussi les champs sans clé compatible
    assert first.ai.errors == 1  # x_notes_internal : pas de réponse enregistrée
    first_calls = proposer.calls
    pending = first.summary.pending

    again = analysis.analyze(iid)  # rien n'a changé
    assert again.ai is not None and again.ai.targets == first.ai.errors  # seule l'erreur est retentée
    assert proposer.calls == first_calls + first.ai.errors  # 1 seul appel : la réponse en erreur
    assert again.summary.pending == pending  # aucune nouvelle question
    calls = proposer.calls

    for q in enrichment.get_pending_questions(iid):  # le fournisseur répond à tout
        if q.semantic_key:
            enrichment.confirm(iid, q.entity_technical_name, q.field_technical_name)
        else:
            enrichment.ignore(iid, q.entity_technical_name, q.field_technical_name)
    answered = enrichment.summary(iid).supplier_decisions

    final = analysis.analyze(iid)
    assert final.summary.complete and final.summary.pending == 0
    assert final.summary.supplier_decisions == answered  # décisions réutilisées
    assert final.ai is None and proposer.calls == calls  # 0 appel : plus aucun candidat


def test_connection_failure_stops_before_reading_the_structure(world):
    analysis, _ = world["service"]("acme")
    world["fake"].password = "rotated-Password-1"
    with pytest.raises(CatalogAnalysisFailed) as exc:
        analysis.analyze(world["inst"].id)
    assert exc.value.step == "connection" and PASSWORD not in str(exc.value)
    assert _snapshots(world["session"], world["inst"].id) == 0


def test_another_tenant_cannot_analyze(world):
    analysis, _ = world["service"]("globex")
    before = len(world["fake"].calls)
    with pytest.raises(NotFound):
        analysis.analyze(world["inst"].id)
    assert len(world["fake"].calls) == before  # aucune connexion
    assert _snapshots(world["session"], world["inst"].id) == 0
