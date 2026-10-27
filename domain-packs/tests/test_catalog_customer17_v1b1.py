"""Non-régression et mesure V1b.1 sur le schéma RÉEL complet de customer17 et des Odoo standard 17 / 18 / 19.

docs/dumps/odoo17_customer.json : dump de l'instance Docker odoo17-customer (même domaine PRODUCT que le
snapshot d'instance #6 de la mesure de référence V1b). Chaîne réelle : baseline standard publiée → instance →
découverte via le faux serveur XML-RPC → analyse (domaine effectif + Semantic Enrichment), sans LLM.

Mesure de référence V1b.1 (règles 1.3.0+vocabulary-1.2.0) sur customer17 : 430 champs, 23 POSSIBLE
(contre 125 en V1b), 3 questions. Le seuil du test est ce chiffre atteint, pas un objectif forcé.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest
import uie_domain_packs  # noqa: F401 — enregistre les packs découverts
from pydantic import SecretStr
from test_catalog_odoo_instance import FakeOdooResolver
from uie_connectors.odoo import OdooSchemaLoader
from uie_connectors.odoo.testing import FakeOdooServer
from uie_core.domains import ProductDomainService, SemanticEnrichmentService
from uie_core.domains.enrichment import bucket
from uie_core.identity import GO45_DEV_ORG_ID, SystemContext
from uie_core.instances import FernetSecretStore, InstanceCreate, InstanceService
from uie_core.registry import RegistryService

ROOT = Path(__file__).resolve().parents[2]
DUMPS = ROOT / "docs" / "dumps"


def odoo_dump(name: str) -> Path:
    """Dump de référence ; absent (le dump Odoo 19 n'est pas publié sur le dépôt distant, donc pas en CI) :
    test sauté avec sa raison, jamais une erreur."""
    path = DUMPS / name
    if not path.exists():
        pytest.skip(f"docs/dumps/{name} absent (not published on the remote repository)")
    return path


V1B = json.loads((Path(__file__).parent / "fixtures" / "customer17_identified_v1b.json").read_text("utf-8"))
PASSWORD = "Odoo-Cust0mer-S3cret"
POSSIBLE_V1B1 = 23  # chiffre atteint (objectif < 40)


def _analyze(session, version: str, instance_dump: str):  # noqa: ANN001, ANN202
    RegistryService(session, SystemContext("test")).publish_snapshot(
        OdooSchemaLoader({"json": odoo_dump(f"odoo{version}_ref.json")}).build_snapshot()
    )
    fake = FakeOdooServer.from_dump(DUMPS / instance_dump, password=PASSWORD)
    instances = InstanceService(
        session,
        SystemContext("test"),
        FernetSecretStore(FernetSecretStore.generate_key()),
        FakeOdooResolver(fake),
    )
    inst = instances.create(
        InstanceCreate(
            organization_id=GO45_DEV_ORG_ID,
            name=f"odoo{version}-{instance_dump.split('_')[1].split('.')[0]}",
            system="odoo",
            version=version,
            endpoint="http://odoo:8069",
            config={"db": fake.db, "username": "admin"},
            password=SecretStr(PASSWORD),
        )
    )
    isnap = instances.discover_schema(inst.id)
    domains = ProductDomainService(session, SystemContext("test"))
    domains.analyze(inst.id)
    return inst, isnap, SemanticEnrichmentService(session, SystemContext("test"), domains=domains)


@pytest.fixture
def customer17(session):  # noqa: ANN001, ANN201
    return _analyze(session, "17", "odoo17_customer.json")


def test_dump_reproduces_the_reference_domain(customer17):
    """Le dump reproduit le domaine du snapshot live de référence (430 champs, 16 extensions). Le checksum
    global du snapshot diffère du live (ordre des modules des entités, `depends` de modèles ir.* absents
    du dump) : aucun de ces écarts ne touche un champ du domaine PRODUCT."""
    inst, _, svc = customer17
    s = svc.summary(inst.id)
    assert (s.total, s.extensions) == (430, 16)


def test_customer17_still_three_questions(customer17):
    inst, _, svc = customer17
    assert sorted(q.path for q in svc.get_pending_questions(inst.id)) == [
        "product.template.x_notes_internal",
        "product.template.x_pack_code",
        "product.template.x_supplier_ref",
    ]


def test_customer17_possible_below_threshold(customer17):
    inst, _, svc = customer17
    s = svc.summary(inst.id)
    assert s.total == 430
    assert s.possible <= POSSIBLE_V1B1 < 40
    assert s.pending + s.excluded_auto + s.excluded_by_supplier + s.identified + s.possible == s.total


def _buckets(svc, inst) -> dict[str, str]:  # noqa: ANN001
    return {b.path: bucket(b) for b in svc.list_bindings(inst.id)}  # type: ignore[arg-type]


def test_customer17_identified_after_v1b_stay_identified(customer17):
    inst, _, svc = customer17
    buckets = _buckets(svc, inst)
    assert sorted(p for p in V1B["identified_by_rule"] if buckets[p] != "identified") == []
    # mêmes réponses que la mesure V1b : les 121 restent identifiés
    tmpl = "product.template"
    svc.confirm(inst.id, tmpl, "x_supplier_ref")
    svc.change(inst.id, tmpl, "x_pack_code", "packaging_code")
    svc.ignore(inst.id, tmpl, "x_notes_internal")
    buckets = _buckets(svc, inst)
    everything = [*V1B["identified_by_rule"], *V1B["identified_by_answer"]]
    assert len(everything) == 121
    assert sorted(p for p in everything if buckets[p] != "identified") == []


@pytest.mark.parametrize("version", ["17", "18", "19"])
def test_standard_instances_measure(session, version):
    """Instances standard (sans custom) : ratio de POSSIBLE du même ordre sur les trois versions (une règle
    qui ne marcherait que sur 17 serait suspecte), aucune question."""
    inst, _, svc = _analyze(session, version, f"odoo{version}_ref.json")
    s = svc.summary(inst.id)
    bindings = svc.list_bindings(inst.id)
    by_rule = Counter(b.rule_id for b in bindings if bucket(b) == "excluded_auto")  # type: ignore[arg-type]
    print(
        f"\nodoo {version} standard: total={s.total} possible={s.possible} excluded={s.excluded_auto} "
        f"identified={s.identified} pending={s.pending} by_rule={dict(sorted(by_rule.items()))}"
    )
    assert s.pending == 0
    assert s.possible / s.total < 0.08
    assert {f"R{i}" for i in range(11, 19)} <= {b.rule_id for b in bindings}  # chaque règle sert
