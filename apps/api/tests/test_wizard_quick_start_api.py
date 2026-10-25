"""Mode Simple « fichier d'abord » par la Supplier API : cible Odoo 18 du Registry RÉEL (dump), fichier de
1 000 produits du test de non-régression. Les appels au LLM sont comptés par `RecordingProposer` (même
enveloppe que l'enregistrement des réponses réelles) : au plus UN appel (les colonnes non reconnues par leur
nom)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr
from test_lexware_partner_api import _client
from test_supplier_api import PW
from test_wizard_1000_products import _catalog
from uie_ai import RecordingProposer
from uie_api import deps
from uie_api.app import create_app
from uie_connectors.odoo import OdooSchemaLoader
from uie_core.domains import ProposalMeta
from uie_core.identity import Organization, OrganizationMembership, SystemContext, User, hash_password
from uie_core.imports import ColumnProposal, ColumnProposalBatch
from uie_core.imports.proposer import ColumnDecision
from uie_core.registry import RegistryService
from uie_core.wizard import WizardRaw, clear_cache

DUMPS = Path(__file__).resolve().parents[3] / "docs" / "dumps"
KEYS = {"title": "name", "final_price": "list_price", "upc": "barcode", "description": "description_sale"}


class FakeLLM:
    """LLM simulé : correspondances des colonnes, type de données (si demandé) ; le reste lève (jamais
    appelé)."""

    provider = "fake"
    model = "fake"
    cache_identity = "fake-quick"

    def __init__(self, data_type: str | None = "Products") -> None:
        self.data_type = data_type
        self.sent: list[list[str]] = []  # en-têtes envoyés au LLM, par appel

    def wizard_columns(self, request: Any) -> ColumnProposalBatch:
        self.sent.append([c.header for c in request.columns])
        keys = {t.key for t in request.targets}
        return ColumnProposalBatch(
            proposals=[
                ColumnProposal(
                    position=c.position,
                    decision=ColumnDecision.TARGET,
                    semantic_key=KEYS[c.header],
                    confidence=0.95,
                )
                for c in request.columns
                if KEYS.get(c.header) in keys
            ],
            meta=ProposalMeta(provider="fake", model="fake"),
        )

    def wizard_data_type(self, request: Any) -> WizardRaw:
        return WizardRaw(
            data={"data_type": self.data_type, "confidence": 0.9},
            meta=ProposalMeta(provider="fake", model="fake"),
        )

    def wizard_question(self, request: Any) -> WizardRaw:
        raise AssertionError("no question in the file-first flow")

    def wizard_target(self, request: Any) -> WizardRaw:
        raise AssertionError("the structure comes from the Registry")

    def wizard_missing(self, request: Any) -> WizardRaw:
        raise AssertionError("blocking questions come from rules, the Registry and the connected system")


@pytest.fixture
def world(session, tmp_path):  # noqa: ANN001, ANN201
    clear_cache()
    RegistryService(session, SystemContext("test")).publish_snapshot(
        OdooSchemaLoader({"json": DUMPS / "odoo18_ref.json"}).build_snapshot()
    )
    org = Organization(name="Acme", slug="acme")
    session.add(org)
    session.flush()
    user = User(email="alice@acme.io", display_name="Alice", password_hash=hash_password(SecretStr(PW)))
    session.add(user)
    session.flush()
    session.add(OrganizationMembership(organization_id=org.id, user_id=user.id, role="OWNER"))
    session.flush()
    llm = FakeLLM()
    recording = RecordingProposer(llm, tmp_path / "wizard_calls.json")  # type: ignore[arg-type]
    app = create_app()
    app.dependency_overrides[deps.get_session] = lambda: session
    app.dependency_overrides[deps.get_wizard_proposer] = lambda: recording
    yield {"app": app, "llm": llm, "recording": recording}
    clear_cache()


def _start(client: Any, target: str, content: bytes, name: str = "catalog.csv") -> Any:
    return client.post(
        "/api/wizard/quick-start",
        files={"file": (name, content, "text/csv")},
        data={"payload": json.dumps({"target": target})},
    )


def test_file_first_with_an_odoo18_target_of_the_registry_makes_at_most_one_llm_call(world):
    alice = _client(world["app"], "alice@acme.io")
    res = _start(alice, "registry:odoo:odoo:18", _catalog(1000))
    assert res.status_code == 200, res.text
    body = res.json()
    # au plus UN appel au LLM : les colonnes que leur nom ne suffit pas à placer
    assert world["recording"].wizard_calls == ["columns"]
    assert (body["data_type"], body["data_type_from"], body["structure_from"]) == (
        "Products",
        "rules",
        "registry",
    )
    assert body["target_label"] == "Odoo 18" and body["proposal"]["verification"] == "verified"
    assert body["proposal"]["entity"] == "product.template"
    names = [f["name"] for f in body["proposal"]["fields"]]
    assert {"name", "list_price", "barcode", "type", "uom_id"} <= set(names)
    assert "product_variant_count" not in names  # lecture seule : rien à importer
    assert [a["source"] for a in body["answers"]] == ["registry", "registry", "file"]
    decided = {d["target"] for d in body["decisions"]}
    # « description » : description de vente (vocabulaire), jamais le champ du même nom (note interne)
    assert {"name", "list_price", "barcode", "description_sale"} <= decided and "description" not in decided
    # au plus 3 questions bloquantes ; l'unité d'achat est calculée par Odoo (jamais demandée)
    questions = {q["question"]["field"]: q for q in body["questions"]}
    assert len(questions) <= 3 and "uom_po_id" not in questions
    assert all(q["default"] for q in body["questions"])  # chacune avec sa réponse présélectionnée
    # valeurs de champs présélectionnables : appliquées et listées, jamais demandées
    applied = {q["question"]["field"]: q for q in body["applied"]}
    assert "type" not in questions and "uom_id" not in questions
    assert (applied["type"]["default"], applied["type"]["default_from"]) == ("consu", "registry_first_value")
    assert {"field": "type", "common": "consu", "confirmed": True}.items() <= next(
        f for f in body["fills"] if f["field"] == "type"
    ).items()
    # sans système connecté : aucune unité inventée, la valeur par défaut de la cible est appliquée
    unit = applied["uom_id"]
    assert (unit["default"], unit["decision"]) == ("default", "records:uom_id")
    assert body["choices"]["records:uom_id"] == "default"
    saved = json.loads(Path(world["recording"].path).read_text(encoding="utf-8"))
    assert [c["kind"] for c in saved["wizard_calls"]] == ["columns"]

    # la suite du parcours reprend la réponse telle quelle (aperçu existant)
    proposal = {
        k: body["proposal"][k] for k in ("title", "summary", "entity", "template_name", "configuration")
    }
    proposal |= {
        "rows": [],
        "notes": [],
        "fields": [
            {k: v for k, v in f.items() if k not in ("verified", "allowed", "reference")}
            for f in body["proposal"]["fields"]
        ],
    }
    payload = {
        "answers": body["answers"],
        "proposal": proposal,
        "decisions": body["decisions"],
        "fills": body["fills"],
        "choices": body["choices"] | {"currency": "USD"},
    }
    preview = alice.post(
        "/api/wizard/file/preview",
        files={"file": ("catalog.csv", _catalog(1000), "text/csv")},
        data={"payload": json.dumps(payload)},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["rows"] == 1000
    assert world["recording"].wizard_calls == ["columns"]  # même fichier : réponse reprise du cache
    # cache PERSISTANT : sans le cache du processus (redémarrage, autre processus), aucun nouvel appel
    clear_cache()
    again = _start(alice, "registry:odoo:odoo:18", _catalog(1000))
    assert again.status_code == 200 and again.json()["decisions"] == body["decisions"]
    assert world["recording"].wizard_calls == ["columns"]


def test_the_llm_names_the_data_type_only_when_the_rules_cannot(world):
    alice = _client(world["app"], "alice@acme.io")
    vague = b"col_a,col_b,col_c\nx,1,y\nz,2,w\n"
    res = _start(alice, "registry:odoo:odoo:18", vague, "vague.csv")
    assert res.status_code == 200, res.text
    assert (res.json()["data_type"], res.json()["data_type_from"]) == ("Products", "ai")
    assert world["recording"].wizard_calls[0] == "data_type"
    world["llm"].data_type = None  # le LLM s'abstient : jamais un type deviné
    clear_cache()
    refused = _start(alice, "registry:odoo:odoo:18", b"col_d,col_e\n1,2\n", "other.csv")
    detail = refused.json()["detail"]
    assert refused.status_code == 422 and "kind of data" in detail["message"]
    # l'utilisateur choisit alors le type parmi ceux que la cible reçoit (point 11) : jamais un arrêt sans
    # issue
    assert detail["code"] == "data_type_unknown" and "Products" in detail["data_types"]


def test_an_unknown_target_is_refused(world):
    alice = _client(world["app"], "alice@acme.io")
    assert _start(alice, "registry:odoo:odoo:99", _catalog(5)).status_code == 422
    assert _start(alice, "something-else", _catalog(5)).status_code == 422
    assert world["recording"].wizard_calls == []


def test_only_columns_without_any_known_match_are_sent_to_the_llm(world, session):
    """Mémoire de l'organisation (imports précédents) et vocabulaire du domaine AVANT le LLM."""
    from uie_core.imports.matching import header_key
    from uie_core.imports.records import LearnedColumnMapping
    from uie_core.registry.models import System

    alice = _client(world["app"], "alice@acme.io")
    org = session.query(Organization).filter_by(slug="acme").one()
    odoo = session.query(System).filter_by(code="odoo").one()
    session.add_all(
        [
            # « title » déjà choisi pour le nom ; « seller_name » jugé pas utile pour Odoo
            LearnedColumnMapping(
                organization_id=org.id, header_key=header_key("title"), decision="MAPPED", semantic_key="name"
            ),
            LearnedColumnMapping(
                organization_id=org.id,
                header_key=header_key("seller_name"),
                decision="NOT_RELEVANT",
                system_id=odoo.id,
            ),
        ]
    )
    session.flush()
    res = _start(alice, "registry:odoo:odoo:18", _catalog(1000))
    assert res.status_code == 200, res.text
    columns = {c["header"]: c for c in res.json()["mapping"]["columns"]}
    assert (columns["title"]["target"], columns["title"]["status"]) == ("name", "previous")  # mémoire
    assert columns["seller_name"]["probably_not_needed"] and columns["seller_name"]["target"] is None
    assert (columns["upc"]["target"], columns["categories"]["target"]) == (
        "barcode",
        "categ_id",
    )  # vocabulaire
    (sent,) = world["llm"].sent
    # ni la mémoire, ni le vocabulaire, ni un sens certain sans champ (« currency ») ne partent au LLM
    assert not {"title", "seller_name", "upc", "categories", "description", "currency"} & set(sent)
    # 55 colonnes − 1 vide − 2 par la mémoire − 6 par le vocabulaire 1.6.0 (upc, categories, final_price,
    # item_weight, asin, description) − 1 sens sans champ
    assert len(sent) == 45


def test_a_description_column_goes_to_the_sales_description_never_to_the_internal_note(world):
    """« description » d'un fichier = texte destiné aux clients : description de vente (vocabulaire), jamais
    le champ « description » d'Odoo (note interne) malgré le même nom ; sans appel au LLM pour elle."""
    alice = _client(world["app"], "alice@acme.io")
    for header in ("description", "Description", "Product description"):
        content = f"Product name,SKU,{header}\nOak tray,NX-1,A solid oak tray.\n".encode()
        res = _start(alice, "registry:odoo:odoo:18", content)
        assert res.status_code == 200, res.text
        column = next(c for c in res.json()["mapping"]["columns"] if c["header"] == header)
        assert column["target"] == "description_sale", (header, column)
        assert column["status"] == "matched"
    assert not any(h.casefold().endswith("description") for sent in world["llm"].sent for h in sent)


def test_explicit_choices_are_remembered_and_not_sent_to_the_llm_again(world, session):
    """L'utilisateur corrige une colonne et en écarte une autre (confirmations explicites) : sa mémoire les
    reprend au passage suivant du même fichier, sans les envoyer au LLM. Une confirmation que le vocabulaire
    donne déjà seul n'est pas mémorisée."""
    from uie_core.imports.matching import header_key
    from uie_core.imports.records import LearnedColumnMapping

    alice = _client(world["app"], "alice@acme.io")
    content = _catalog(1000)
    first = _start(alice, "registry:odoo:odoo:18", content)
    assert first.status_code == 200, first.text
    body = first.json()
    position = {c["header"]: c["position"] for c in body["mapping"]["columns"]}
    assert {"features", "seller_name"} <= set(world["llm"].sent[0])
    # corrections explicites : features → description d'achat ; seller_name non importée ; upc confirmé
    chosen = {"features": "description_purchase", "seller_name": None, "upc": "barcode"}
    decisions = [d for d in body["decisions"] if d["position"] not in {position[h] for h in chosen}]
    decisions += [{"position": position[h], "target": t} for h, t in chosen.items()]
    proposal = {
        k: body["proposal"][k] for k in ("title", "summary", "entity", "template_name", "configuration")
    }
    proposal |= {
        "rows": [],
        "notes": [],
        "fields": [
            {k: v for k, v in f.items() if k not in ("verified", "allowed", "reference")}
            for f in body["proposal"]["fields"]
        ],
    }
    payload = {
        "answers": body["answers"],
        "proposal": proposal,
        "decisions": decisions,
        "choices": {"currency": "USD"},
        "confirmed": [position[h] for h in chosen],
    }
    preview = alice.post(
        "/api/wizard/file/preview",
        files={"file": ("catalog.csv", content, "text/csv")},
        data={"payload": json.dumps(payload)},
    )
    assert preview.status_code == 200, preview.text
    memory = {
        r.header_key: (r.decision, r.semantic_key)
        for r in session.query(LearnedColumnMapping).filter_by(
            organization_id=session.query(Organization).filter_by(slug="acme").one().id
        )
    }
    assert memory == {
        header_key("features"): ("MAPPED", "description_purchase"),
        header_key("seller_name"): ("NOT_RELEVANT", None),
    }  # upc → barcode : déjà donné par le vocabulaire, rien à mémoriser

    clear_cache()
    second = _start(alice, "registry:odoo:odoo:18", content)
    assert second.status_code == 200, second.text
    columns = {c["header"]: c for c in second.json()["mapping"]["columns"]}
    assert (columns["features"]["status"], columns["features"]["target"]) == (
        "previous",
        "description_purchase",
    )
    assert columns["seller_name"]["probably_not_needed"] and columns["seller_name"]["target"] is None
    assert not {"features", "seller_name"} & set(world["llm"].sent[-1])  # plus envoyées au LLM
    assert len(world["llm"].sent[-1]) == len(world["llm"].sent[0]) - 2


def _questions(body: dict[str, Any]) -> list[tuple[str, Any, Any, Any]]:
    return [
        (q["question"]["field"], q["default"], q["default_from"], q["decision"]) for q in body["questions"]
    ]


def test_the_currency_comes_from_the_file_or_is_asked_first_with_eur_preselected(world):
    alice = _client(world["app"], "alice@acme.io")
    header = "Product name,SKU,Sales price,EAN"
    # « € » dans une colonne NON importée suffit : EUR déduit, aucune question de devise
    euro = f"{header},Gross price\nOak tray,NX-1,39.90,4006381333931,47.48 €\nPine box,NX-2,18.75,,22.31 €\n"
    body = _start(alice, "registry:odoo:odoo:18", euro.encode(), "euro.csv").json()
    assert body["choices"]["currency"] == "EUR" and "currency" not in {q[0] for q in _questions(body)}
    # aucun indice : la question vient en premier, EUR présélectionné (à confirmer)
    plain = f"{header}\nOak tray,NX-1,39.90,4006381333931\n"
    body = _start(alice, "registry:odoo:odoo:18", plain.encode(), "plain.csv").json()
    assert "currency" not in body["choices"]
    assert _questions(body)[0] == ("currency", "EUR", "usual_default", "currency")
    # plusieurs devises dans la colonne de devises : la plus fréquente présélectionnée
    mixed = f"{header},Currency\nA,NX-1,1,,USD\nB,NX-2,2,,USD\nC,NX-3,3,,INR\n"
    body = _start(alice, "registry:odoo:odoo:18", mixed.encode(), "mixed.csv").json()
    assert _questions(body)[0] == ("currency", "USD", "your_file", "currency")
    # seules les devises du fichier sont proposées, plus « Other value » (saisie libre)
    assert [c["value"] for c in body["questions"][0]["question"]["choices"]] == [
        "USD",
        "INR",
        "without_prices",
    ]
    assert (
        body["questions"][0]["question"]["choices"][-1]["label"] == "Import the products without their prices"
    )
    assert body["questions"][0]["question"]["free_text"] is True
    # une seule devise dans la colonne : déduite ; les 1 000 prix sont alors bien écrits
    body = _start(alice, "registry:odoo:odoo:18", _catalog(1000)).json()
    assert body["choices"]["currency"] == "USD"
    assert len(body["questions"]) <= 3


def test_categories_of_the_file_get_one_question_with_create_preselected(world):
    alice = _client(world["app"], "alice@acme.io")
    rows = [
        "Product name;SKU;Sales price;Category",
        "Drill;D-1;10,00 €;Werkzeug",
        "Hose;H-1;20,00 €;Garten / Schlauch",
    ]
    csv = "\n".join(rows) + "\n"
    body = _start(alice, "registry:odoo:odoo:18", csv.encode("cp1252"), "liste.csv").json()
    asked = {q["decision"]: q for q in body["questions"] if q["decision"]}
    question = asked["records:categ_id"]
    assert question["default"] == "create" and question["question"]["field"] == "categ_id"
    assert [c["value"] for c in question["question"]["choices"]] == ["create", "default"]
    # le nombre de catégories que le fichier créera d'abord (Werkzeug, Garten, Schlauch), dans l'option
    assert question["question"]["choices"][0]["label"] == "Create 3 missing product categories"
    assert body["choices"]["currency"] == "EUR"  # « € » dans les prix : déduit, aucune question
    # aperçu : feuille des catégories à importer d'abord (ordre d'import expliqué par le portail)
    choices = body["choices"] | {q["decision"]: q["default"] for q in body["questions"] if q["decision"]}
    preview = alice.post(
        "/api/wizard/file/preview",
        files={"file": ("liste.csv", csv.encode("cp1252"), "text/csv")},
        data={"payload": json.dumps(_payload(body, choices))},
    )
    assert preview.status_code == 200, preview.text
    created = preview.json()["created_records"]
    assert [(c["field"]["name"], c["count"]) for c in created] == [
        ("categ_id", 3)
    ]  # Werkzeug, Garten, Schlauch
    assert created[0]["sheet"].endswith("(import first)")
    assert preview.json()["left_to_target"] == ["Unit of Measure"]  # unité absente : celle de la cible


def _payload(body: dict[str, Any], choices: dict[str, str]) -> dict[str, Any]:
    """Suite du parcours : réponses, structure et décisions rendues par /quick-start, renvoyées telles
    quelles."""
    proposal = {
        k: body["proposal"][k] for k in ("title", "summary", "entity", "template_name", "configuration")
    }
    proposal |= {
        "rows": [],
        "notes": [],
        "fields": [
            {k: v for k, v in f.items() if k not in ("verified", "allowed", "reference", "computed_from")}
            for f in body["proposal"]["fields"]
        ],
    }
    return {
        "answers": body["answers"],
        "proposal": proposal,
        "decisions": body["decisions"],
        "fills": body["fills"],
        "choices": choices,
    }


def test_the_targets_come_from_your_systems_then_the_registry_without_any_llm_call(world):
    alice = _client(world["app"], "alice@acme.io")
    res = alice.get("/api/wizard/targets")
    assert res.status_code == 200, res.text
    targets = res.json()
    # aucun système connecté pour Acme : les versions du Registry, chacune démarrable telle quelle
    assert {"value": "registry:odoo:odoo:18", "label": "Odoo 18", "group": "registry"}.items() <= next(
        t for t in targets if t["value"] == "registry:odoo:odoo:18"
    ).items()
    assert all(t["group"] != "instance" for t in targets)
    assert world["recording"].wizard_calls == []
    assert _start(alice, targets[0]["value"], _catalog(5)).status_code == 200


class ConnectedFinder:
    """Système connecté simulé (même système et version) : une seule catégorie et une seule unité existent."""

    VALUES = {"product.category": ["All"], "uom.uom": ["Units"]}

    def connected(self, system: str, version: str, instance_id: Any = None) -> bool:
        return True

    def all_options(
        self, system: str, version: str, entity: str, attribute: str | None, instance_id: Any = None
    ) -> list[Any]:
        from uie_core.wizard import ReferenceOption

        return [ReferenceOption(value=v, label=v) for v in self.VALUES.get(entity, [])]

    options = all_options


def test_categories_created_first_are_never_reported_unknown_in_the_connected_system(world, session):
    from uie_core.identity import SystemContext as Ctx
    from uie_core.wizard import MappingDecision, MigrationWizardService

    csv = "Product name;SKU;Sales price;Category\nDrill;D-1;10,00 €;Werkzeug\nHose;H-1;20,00 €;Garten\n"
    data = csv.encode("cp1252")
    svc = MigrationWizardService(session, Ctx("test"), FakeLLM(), references=ConnectedFinder())  # type: ignore[arg-type]
    q = svc.quick_start("registry:odoo:odoo:18", data, "liste.csv")
    asked = {x.decision: x for x in q.questions if x.decision}
    assert asked["records:categ_id"].default == "create"  # Werkzeug, Garten : absentes du système connecté
    decisions = [MappingDecision(position=d.position, target=d.target) for d in q.decisions]

    def unknown(choice: str) -> list[str]:
        choices = {**q.choices, "records:categ_id": choice}
        summary, _, _ = svc.prepare_file(
            q.answers, q.proposal, data, "liste.csv", decisions, list(q.fills), choices
        )
        return [i.field for i in summary.value_issues if i.kind == "reference_unknown"]

    # créées par la feuille « (import first) » avant les articles : jamais « refusées par la cible »
    assert "categ_id" not in unknown("create")
    # laissées à la valeur par défaut de la cible : aucune valeur écrite, rien à vérifier
    assert "categ_id" not in unknown("default")


class ChosenInstanceFinder:
    """Deux systèmes Odoo 18 connectés : seul celui choisi comme cible a des unités (« Dozens » lue AVANT
    « Units »). Enregistre l'instance demandée à chaque lecture."""

    CHOSEN = uuid.uuid4()

    def __init__(self) -> None:
        self.read: list[Any] = []

    def instance_target(self, instance_id: uuid.UUID) -> Any:
        from uie_core.wizard import InstanceTarget

        return InstanceTarget(
            system="odoo", product="odoo", version="18", name="accounting", connected=True, currency="EUR"
        )

    def connected(self, system: str, version: str, instance_id: uuid.UUID | None = None) -> bool:
        return True

    def all_options(
        self, system: str, version: str, entity: str, attribute: str | None, instance_id: Any = None
    ) -> list[Any]:
        from uie_core.wizard import ReferenceOption

        self.read.append(instance_id)
        values = {"uom.uom": ["Dozens", "Units"], "product.category": ["All"]}
        own = values.get(entity, []) if instance_id == self.CHOSEN else []
        return [ReferenceOption(value=v, label=v) for v in own]

    options = all_options


def test_the_default_unit_is_read_in_the_connected_system_chosen_as_target(world, session):
    """Cible = un système connecté : ses valeurs réelles sont lues dans CETTE instance (jamais une autre du
    même système et de la même version) ; l'unité de base de la cible (« Units ») est présélectionnée et
    nommée. L'instance n'est jamais transmise au LLM (réponse à libellé générique)."""
    from uie_core.identity import SystemContext as Ctx
    from uie_core.wizard import MigrationWizardService

    csv = "Product name;SKU;Sales price;Category\nDrill;D-1;10,00 €;All\n"
    finder = ChosenInstanceFinder()
    llm = FakeLLM()
    svc = MigrationWizardService(session, Ctx("test"), llm, references=finder)  # type: ignore[arg-type]
    q = svc.quick_start(f"instance:{finder.CHOSEN}", csv.encode("cp1252"), "liste.csv")
    uom = next(a for a in q.applied if a.question.field == "uom_id")
    assert (uom.default, uom.default_from) == ("Units", "your_system")
    assert [c.label for c in uom.question.choices][:2] == ["Units", "Dozens"]
    assert finder.read and set(finder.read) == {finder.CHOSEN}  # seule l'instance choisie est lue
    chosen = [a for a in q.answers if a.topic == "connected_system"]
    assert [(a.value, a.label) for a in chosen] == [(f"instance:{finder.CHOSEN}", "Your connected system")]
