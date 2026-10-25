"""AI Migration Wizard (Core) : Registry d'abord, IA ensuite ; validation des réponses ; vérification
de la structure cible par le Registry ; cache ; Excel d'exemple. Faux proposer (aucun appel réel)."""

from __future__ import annotations

import io
from typing import Any

import polars as pl
import pytest
from uie_core.domains import ProposalMeta
from uie_core.identity import SystemContext
from uie_core.registry import (
    DataType,
    EntityDef,
    FieldDef,
    ModuleDef,
    RelationDef,
    RelationType,
    SchemaSnapshotModel,
)
from uie_core.registry.service import RegistryService
from uie_core.wizard import (
    ChoiceSource,
    ItemStatus,
    MigrationWizardProposer,
    MigrationWizardService,
    Verification,
    WizardAnswer,
    WizardError,
    WizardRaw,
    WizardRequest,
    WizardUnavailable,
    clear_cache,
    render_excel,
)


class FakeProposer:
    """Réponses scriptées ; garde chaque requête (preuve de ce qui est envoyé, du nombre d'appels)."""

    cache_identity = "fake:1"

    def __init__(self, questions: list[Any], target: Any = None, error: str | None = None) -> None:
        self.questions = list(questions)
        self.target = target
        self.error = error
        self.requests: list[tuple[str, WizardRequest]] = []

    def _raw(self, data: Any) -> WizardRaw:
        meta = ProposalMeta(provider="fake", model="fake", input_tokens=10, output_tokens=5)
        return WizardRaw(data=data, meta=meta, error=self.error)

    def wizard_question(self, request: WizardRequest) -> WizardRaw:
        self.requests.append(("question", request))
        return self._raw(self.questions.pop(0) if self.questions else {"done": True, "remaining": 0})

    def wizard_target(self, request: WizardRequest) -> WizardRaw:
        self.requests.append(("target", request))
        return self._raw(self.target)


def _q(topic: str, *labels: str, remaining: int = 3) -> dict[str, Any]:
    return {
        "done": False,
        "remaining": remaining,
        "question": {
            "id": topic,
            "topic": topic,
            "title": f"Which {topic}?",
            "help": None,
            "allow_other": True,
            "choices": [{"value": lb.lower(), "label": lb, "description": None} for lb in labels],
        },
    }


def _answer(topic: str, value: str, label: str, source: ChoiceSource = ChoiceSource.AI) -> WizardAnswer:
    return WizardAnswer(
        question_id=topic, topic=topic, question=f"Which {topic}?", value=value, label=label, source=source
    )


def _target(entity: str | None, *names: str, rows: int = 5) -> dict[str, Any]:
    return {
        "title": "Demo structure",
        "summary": "A proposal.",
        "entity": entity,
        "template_name": None,
        "configuration": [{"label": "Key", "value": "One row per record"}],
        "fields": [
            {
                "name": n,
                "label": n.title(),
                "type": "text",
                "length": None,
                "required": False,
                "example": f"ex-{n}",
                "description": None,
            }
            for n in names
        ],
        "sample_rows": [[f"{n}-{i}" for n in names] for i in range(rows)],
        "notes": ["Check the template."],
    }


@pytest.fixture
def registry(session):  # noqa: ANN001, ANN201
    clear_cache()
    snap = SchemaSnapshotModel(
        system="demo",
        product="demo",
        version="17",
        interface="orm",
        interface_type="orm",
        source={"loader": "test"},
        metadata={"system_name": "Demo ERP"},
        modules=[ModuleDef(technical_name="base")],
        entities=[
            EntityDef(
                technical_name="res.partner",
                module="base",
                fields=[
                    FieldDef(technical_name="name", label="Name", data_type=DataType.STRING, required=True),
                    FieldDef(technical_name="email", label="Email", data_type=DataType.STRING),
                ],
            )
        ],
    )
    RegistryService(session, SystemContext("test")).publish_snapshot(snap)
    yield session
    clear_cache()


def _svc(session, proposer):  # noqa: ANN001, ANN202
    return MigrationWizardService(session, SystemContext("test"), proposer)


def test_the_fake_follows_the_contract():
    assert isinstance(FakeProposer([]), MigrationWizardProposer)


def test_first_question_comes_from_the_registry_without_any_llm_call(registry):
    RegistryService(registry, SystemContext("test")).register_known_system(
        "acme_books", "Acme Books", category="accounting", description="Cloud accounting software"
    )
    fake = FakeProposer([])
    step = _svc(registry, fake).next([])
    assert fake.requests == []  # le Registry sait quels systèmes existent : aucun appel
    assert step.question is not None and step.question.allow_other
    choices = [(c.label, c.value, c.source, c.group) for c in step.question.choices]
    # schéma publié d'abord (vérifié), puis système seulement connu (aucun schéma supposé)
    assert choices == [
        ("Demo ERP", "registry:demo", ChoiceSource.REGISTRY, "ERP"),
        ("Acme Books", "known:acme_books", ChoiceSource.KNOWN, "Accounting"),
    ]
    assert step.question.choices[1].description == "Cloud accounting software"


def test_a_known_system_never_gets_registry_versions_nor_a_verified_status(registry):
    RegistryService(registry, SystemContext("test")).register_known_system(
        "acme_books", "Acme Books", category="accounting"
    )
    answers = [_answer("target_system", "known:acme_books", "Acme Books", ChoiceSource.KNOWN)]
    fake = FakeProposer([_q("version", "2024", "2025")], target=_target(None, "NAME"))
    svc = _svc(registry, fake)
    step = svc.next(answers)
    assert step.question is not None
    # versions proposées par l'IA, jamais vérifiées
    assert {c.source for c in step.question.choices} == {ChoiceSource.AI}
    request = fake.requests[0][1]
    assert request.target is not None and request.target.has_schema is False and request.target.versions == []
    p = svc.propose(answers)
    assert [c.status for c in p.configuration[:1]] == [ItemStatus.KNOWN_SYSTEM]
    assert p.verification == Verification.AI and not any(f.verified for f in p.fields)


def test_registering_a_known_system_never_downgrades_a_system_with_a_schema(registry):
    reg = RegistryService(registry, SystemContext("test"))
    reg.register_known_system("demo", "Renamed", category="erp")
    assert next(s for s in reg.list_systems() if s.code == "demo").name == "Demo ERP"


def test_llm_target_question_when_the_registry_is_empty(session):
    clear_cache()
    q = _q("target_system", "Big Suite", *[f"Suite {i}" for i in range(20)], remaining=5)
    q["question"]["choices"][0]["group"] = "ERP"
    step = _svc(session, FakeProposer([q])).next([])
    assert step.question is not None
    assert len(step.question.choices) == 16 and step.question.choices[0].group == "ERP"
    clear_cache()


def test_versions_of_a_registry_system_come_from_the_registry(registry):
    fake = FakeProposer([_q("version", "18", "99")])
    answers = [_answer("target_system", "registry:demo", "Demo ERP", ChoiceSource.REGISTRY)]
    step = _svc(registry, fake).next(answers)
    assert step.question is not None
    assert [(c.label, c.value, c.source) for c in step.question.choices] == [
        ("17", "registry:demo:demo:17", ChoiceSource.REGISTRY)
    ]
    assert fake.requests[0][1].target is not None


def test_proposal_is_verified_field_by_field_against_the_registry(registry):
    answers = [
        _answer("target_system", "registry:demo", "Demo ERP", ChoiceSource.REGISTRY),
        _answer("version", "registry:demo:demo:17", "17", ChoiceSource.REGISTRY),
    ]
    target = _target("res.partner", "name", "vat_code", "email")
    target["fields"][0]["required"] = True  # la structure ET le schéma : obligatoire
    target["fields"][2]["required"] = True  # la structure seule (le schéma dit facultatif) : pas obligatoire
    fake = FakeProposer([], target=target)
    p = _svc(registry, fake).propose(answers)
    assert p.entity_verified and p.verification == Verification.PARTIAL
    assert not p.fields[2].required
    name, vat, _ = p.fields
    assert name.verified and name.required and name.label == "Name"  # le Registry fait foi
    assert not vat.verified
    assert [c.verified for c in p.configuration[:2]] == [True, True]
    assert [c.status for c in p.configuration[:2]] == [ItemStatus.VERIFIED_SCHEMA] * 2
    assert p.configuration[2].label == "Key" and not p.configuration[2].verified
    assert p.registry_reference and "demo" in p.registry_reference
    # aucune mention du Registry ni de l'IA dans les notes montrées au fournisseur
    assert not any("Registry" in n or "AI" in n for n in p.notes)


def test_a_target_unknown_to_the_registry_stays_an_ai_proposal(registry):
    answers = [_answer("target_system", "big_suite", "Big Suite"), _answer("version", "2608", "2608")]
    fake = FakeProposer([], target=_target("Product", "PRODUCT", "PRODUCT_TYPE", rows=3))
    p = _svc(registry, fake).propose(answers)
    assert p.verification == Verification.AI and not p.entity_verified
    assert not any(f.verified for f in p.fields) and not any(c.verified for c in p.configuration)
    assert {c.status for c in p.configuration} == {ItemStatus.AI_PROPOSAL}
    assert "official import template" in p.notes[0] and "AI" not in p.notes[0]
    assert len(p.rows) == 3 and any("Only 3 sample" in n for n in p.notes)


def test_client_cannot_claim_a_verified_status(registry):
    answers = [_answer("target_system", "big_suite", "Big Suite")]
    fake = FakeProposer([], target=_target(None, "A"))
    svc = _svc(registry, fake)
    p = svc.propose(answers)
    forged = p.model_copy(
        update={
            "fields": [f.model_copy(update={"verified": True}) for f in p.fields],
            "verification": Verification.VERIFIED,
        }
    )
    again = svc.verify(answers, forged)
    assert again.verification == Verification.AI and not again.fields[0].verified
    # une valeur « registry: » d'un système sans schéma, ou une source déclarée par le client, ne vérifie rien
    fake_registry = [_answer("target_system", "registry:big_suite", "Big Suite", ChoiceSource.REGISTRY)]
    assert svc.verify(fake_registry, p).configuration[0].status == ItemStatus.AI_PROPOSAL


def test_same_answers_reuse_the_previous_answer_without_a_new_call(registry):
    fake = FakeProposer([_q("edition", "Cloud", "On premise"), _q("edition", "X", "Y")])
    answers = [_answer("target_system", "big_suite", "Big Suite")]
    first = _svc(registry, fake).next(answers)
    second = _svc(registry, fake).next(answers)  # retour arrière puis Continue : même question, aucun appel
    assert first == second and len(fake.requests) == 1


def test_invalid_or_failed_answers_are_never_shown(registry):
    first = [_answer("target_system", "s", "S")]  # la 1re question vient du Registry : on teste la suivante
    with pytest.raises(WizardError):
        _svc(
            registry,
            FakeProposer([{"done": False, "remaining": 1, "question": {"title": "x", "choices": []}}]),
        ).next(first)
    with pytest.raises(WizardError, match="could not answer"):
        _svc(registry, FakeProposer([_q("x", "A")], error="timeout")).next(first)
    with pytest.raises(WizardError):
        _svc(registry, FakeProposer([], target={"title": "t", "fields": []})).propose(
            [_answer("target_system", "s", "S")]
        )
    with pytest.raises(WizardUnavailable):
        _svc(registry, None).next(first)


def test_done_ends_the_questions(registry):
    step = _svc(registry, FakeProposer([{"done": True, "remaining": 0, "question": None}])).next(
        [_answer("target_system", "s", "S")]
    )
    assert step.done and step.question is None


def test_excel_has_data_fields_and_read_me(registry):
    fake = FakeProposer([], target=_target("Product", "PRODUCT", "PRODUCT_TYPE"))
    p = _svc(registry, fake).propose([_answer("target_system", "big_suite", "Big Suite")])
    content = render_excel(p)
    data = pl.read_excel(io.BytesIO(content), sheet_name="Data")
    assert data.columns == ["PRODUCT", "PRODUCT_TYPE"] and data.height == 5
    fields = pl.read_excel(io.BytesIO(content), sheet_name="Fields")
    assert set(fields["Status"].to_list()) == {"AI proposal"}
    about = pl.read_excel(io.BytesIO(content), sheet_name="Read me", has_header=False)
    texts = [str(v) for col in about.columns for v in about[col].to_list()]
    assert any("fictitious" in t for t in texts)
    assert "AI proposal: not a verified import schema" in texts
    assert not any(t.startswith("Verified schema") for t in texts)


# --------------------------------------------------------------------------- #
# Mode simple / expert ; fichier du client : correspondances et fichier préparé
# --------------------------------------------------------------------------- #
from uie_core.imports import ColumnProposal, ColumnProposalBatch  # noqa: E402
from uie_core.imports.proposer import ColumnDecision  # noqa: E402
from uie_core.wizard import ColumnStatus, MappingDecision, MappingError, WizardColumnsRequest  # noqa: E402

CSV = (
    b"Product number,Designation,Contact e-mail,Weight kg,Shop theme,Empty\n"
    b"P-1,Oak tray,anna@example.com,1.5,dark,\n"
    b"P-2,Birch crate,bob@example.com,2,light,\n"
)


class FakeColumns(FakeProposer):
    """Proposer de questions + correspondance des colonnes (réponses scriptées)."""

    def __init__(self, *args: Any, proposals: list[ColumnProposal] | None = None, **kw: Any) -> None:
        super().__init__(*args, **kw)
        self.proposals = proposals or []
        self.column_requests: list[WizardColumnsRequest] = []

    def wizard_columns(self, request: WizardColumnsRequest) -> ColumnProposalBatch:
        self.column_requests.append(request)
        return ColumnProposalBatch(proposals=self.proposals, meta=ProposalMeta(provider="fake", model="fake"))


def _structure(registry, fake):  # noqa: ANN001, ANN202
    answers = [_answer("target_system", "big_suite", "Big Suite")]
    target = _target("Product", "PRODUCT", "DESCRIPTION", "WEIGHT")
    target["fields"][1]["label"] = "Designation"
    target["fields"][0]["required"] = True
    fake.target = target
    return answers, _svc(registry, fake).propose(answers)


def test_mode_reaches_the_llm_and_keeps_the_answers(registry):
    fake = FakeProposer([_q("version", "A", "B"), _q("version", "A", "B")])
    answers = [_answer("target_system", "s", "S")]
    _svc(registry, fake).next(answers, "expert")
    _svc(registry, fake).next(answers, "simple")
    modes = [r.mode for _, r in fake.requests]
    assert modes == ["expert", "simple"] and all(r.answers == answers for _, r in fake.requests)


def test_file_columns_are_mapped_by_name_then_by_ai_with_masked_examples(registry):
    fake = FakeColumns(
        [],
        proposals=[
            ColumnProposal(
                position=0, decision=ColumnDecision.TARGET, semantic_key="PRODUCT", confidence=0.97
            ),
            ColumnProposal(position=2, decision=ColumnDecision.UNKNOWN, confidence=0.2),
            ColumnProposal(position=3, decision=ColumnDecision.TARGET, semantic_key="WEIGHT", confidence=0.7),
            ColumnProposal(position=4, decision=ColumnDecision.NOT_RELEVANT, confidence=0.95),
        ],
    )
    answers, proposal = _structure(registry, fake)
    svc = _svc(registry, fake)
    p, m = svc.map_file(answers, proposal, CSV, "data.csv")
    by_header = {c.header: c for c in m.columns}
    assert m.rows == 2
    assert by_header["Designation"].status == ColumnStatus.MATCHED  # même nom qu'un champ : certain
    assert (by_header["Product number"].status, by_header["Product number"].target) == (
        ColumnStatus.AI_MATCH,
        "PRODUCT",
    )
    assert (by_header["Weight kg"].status, by_header["Weight kg"].confidence) == (ColumnStatus.TO_CONFIRM, 70)
    assert by_header["Contact e-mail"].status == ColumnStatus.UNMATCHED
    assert by_header["Shop theme"].probably_not_needed and by_header["Shop theme"].target is None
    assert by_header["Empty"].status == ColumnStatus.EMPTY
    # ce qui part au LLM : colonnes non résolues, exemples MASQUÉS, cibles = champs de la structure
    sent = fake.column_requests[0]
    assert "Designation" not in [c.header for c in sent.columns]
    assert not any("anna@example.com" in e for c in sent.columns for e in c.examples)
    assert {t.key for t in sent.targets} == {"PRODUCT", "DESCRIPTION", "WEIGHT"}
    # même fichier, même structure : réponse reprise, aucun nouvel appel
    svc.map_file(answers, proposal, CSV, "data.csv")
    assert len(fake.column_requests) == 1


def test_prepared_file_copies_the_values_and_checks_the_decisions(registry):
    fake = FakeColumns([])
    answers, proposal = _structure(registry, fake)
    svc = _svc(registry, fake)
    decisions = [
        MappingDecision(position=0, target="PRODUCT"),
        MappingDecision(position=1, target="DESCRIPTION"),
    ]
    summary, content, name = svc.prepare_file(answers, proposal, CSV, "data.csv", decisions)
    assert summary.headers == ["PRODUCT", "DESCRIPTION", "WEIGHT"] and summary.mapped == 2
    assert summary.preview[0] == [
        "P-1",
        "Oak tray",
        "",
    ]  # valeur copiée telle quelle, champ sans colonne vide
    assert summary.missing_required == []
    assert name.endswith("_prepared.xlsx") and fake.column_requests == []  # aucun appel au LLM
    data = pl.read_excel(io.BytesIO(content), sheet_name="Data")
    # WEIGHT entièrement vide : retirée (la cible appliquera sa valeur par défaut)
    assert data.columns == ["PRODUCT", "DESCRIPTION"] and data.height == 2
    about = pl.read_excel(io.BytesIO(content), sheet_name="Read me", has_header=False)
    texts = [str(v) for col in about.columns for v in about[col].to_list()]
    assert any("not a verified import schema" in t for t in texts)
    assert not any(t.startswith("Ready for import") for t in texts)  # jamais « prêt » sans tous les contrôles
    missing, _, _ = svc.prepare_file(answers, proposal, CSV, "data.csv", decisions[1:])
    assert missing.missing_required == ["PRODUCT"]
    with pytest.raises(MappingError):
        svc.prepare_file(answers, proposal, CSV, "data.csv", [MappingDecision(position=0, target="NOPE")])
    with pytest.raises(MappingError):
        svc.prepare_file(
            answers,
            proposal,
            CSV,
            "data.csv",
            [MappingDecision(position=0, target="PRODUCT"), MappingDecision(position=1, target="PRODUCT")],
        )


# --------------------------------------------------------------------------- #
# « Complete missing information »
# --------------------------------------------------------------------------- #
from uie_core.wizard import Fill, FillError, MissingKind  # noqa: E402

SHOP_CSV = b"name,default_code\nOak tray,P-1\nBirch crate,P-2\n"


class FakeMissing(FakeColumns):
    def __init__(self, *args: Any, missing: Any = None, **kw: Any) -> None:
        super().__init__(*args, **kw)
        self.missing = missing
        self.missing_requests: list[Any] = []

    def wizard_missing(self, request: Any) -> WizardRaw:
        self.missing_requests.append(request)
        return self._raw(self.missing)


@pytest.fixture
def shop(registry):  # noqa: ANN001, ANN201
    snap = SchemaSnapshotModel(
        system="shop",
        product="shop",
        version="18",
        interface="orm",
        interface_type="orm",
        source={"loader": "test"},
        metadata={"system_name": "Shop ERP"},
        modules=[ModuleDef(technical_name="base")],
        entities=[
            EntityDef(
                technical_name="product.template",
                module="base",
                fields=[
                    FieldDef(technical_name="name", label="Name", data_type=DataType.STRING, required=True),
                    FieldDef(technical_name="default_code", label="Reference", data_type=DataType.STRING),
                    FieldDef(
                        technical_name="type",
                        label="Product Type",
                        data_type=DataType.ENUM,
                        required=True,
                        metadata={
                            "selection": [
                                {"value": "consu", "label": "Goods"},
                                {"value": "service", "label": "Service"},
                            ]
                        },
                    ),
                    FieldDef(
                        technical_name="uom_id", label="Unit", data_type=DataType.REFERENCE, required=True
                    ),
                ],
            )
        ],
    )
    RegistryService(registry, SystemContext("test")).publish_snapshot(snap)
    answers = [
        _answer("target_system", "registry:shop", "Shop ERP", ChoiceSource.REGISTRY),
        _answer("version", "registry:shop:shop:18", "18", ChoiceSource.REGISTRY),
    ]
    target = _target("product.template", "name", "default_code", "type", "uom_id/id", "care_label", rows=2)
    target["fields"][3]["label"] = "Unit of Measure External ID"
    for i in (0, 2, 3):  # name, type, uom_id/id : obligatoires pour la structure d'import (et le schéma)
        target["fields"][i]["required"] = True
    target["fields"][4]["required"] = True  # champ inconnu du Registry : formulation par l'IA
    target["fields"][4]["length"] = 20  # longueur annoncée : une suggestion plus longue est écartée
    return registry, answers, target


def test_missing_fields_use_the_registry_first_and_never_suggest_identifiers(shop):
    session, answers, target = shop
    fake = FakeMissing(
        [],
        target=target,
        missing={
            "fields": [
                {
                    "name": "care_label",
                    "title": "Which care instructions apply?",
                    "help": None,
                    "choices": [
                        {"value": "Wipe clean", "label": "Wipe clean", "description": None},
                        {"value": "x" * 300, "label": "too long", "description": None},
                    ],
                }
            ]
        },
    )
    svc = _svc(session, fake)
    proposal = svc.propose(answers)
    by = {f.name: f for f in proposal.fields}
    assert [a.value for a in by["type"].allowed] == ["consu", "service"] and by["type"].verified
    assert by["uom_id/id"].reference and by["uom_id/id"].required and not by["uom_id/id"].verified
    decisions = [
        MappingDecision(position=0, target="name"),
        MappingDecision(position=1, target="default_code"),
    ]
    info = svc.missing_info(answers, proposal, SHOP_CSV, "shop.csv", decisions)
    q = {x.field: x for x in info.questions}
    assert set(q) == {"type", "uom_id/id", "care_label"}
    assert q["type"].kind == MissingKind.CHOICE and all(c.verified for c in q["type"].choices)
    assert [c.label for c in q["type"].choices] == ["Goods", "Service"] and not q["type"].free_text
    assert (
        q["uom_id/id"].kind == MissingKind.REFERENCE and q["uom_id/id"].choices == []
    )  # jamais d'identifiant
    assert q["uom_id/id"].warning
    assert [c.value for c in q["care_label"].choices] == ["Wipe clean"]  # suggestion invalide écartée
    assert not any(c.verified for c in q["care_label"].choices)
    # le LLM reçoit les champs à compléter (types, valeurs admises) et les réponses données, jamais le fichier
    sent = fake.missing_requests[0]
    assert {f.name for f in sent.fields} == {"type", "uom_id/id", "care_label"}
    assert next(f for f in sent.fields if f.name == "type").allowed[0] == {"value": "consu", "label": "Goods"}
    assert [a.answer for a in sent.answers] == ["Shop ERP", "18"]
    assert [r.label for r in info.rows] == ["1. Oak tray", "2. Birch crate"]


def test_completed_values_are_validated_confirmed_and_written(shop):
    session, answers, target = shop
    fake = FakeMissing([], target=target, missing={"fields": []})
    svc = _svc(session, fake)
    proposal = svc.propose(answers)
    decisions = [
        MappingDecision(position=0, target="name"),
        MappingDecision(position=1, target="default_code"),
    ]
    fills = [
        Fill(field="type", common="consu"),  # valeur admise du Registry : aucune confirmation
        Fill(field="uom_id/id", per_row={0: "unit_a", 1: "unit_b"}, confirmed=True),
        Fill(field="care_label", common="Wipe clean", confirmed=True),
    ]
    summary, content, _ = svc.prepare_file(answers, proposal, SHOP_CSV, "shop.csv", decisions, fills)
    assert summary.missing_required == [] and summary.completed == ["type", "uom_id/id", "care_label"]
    assert len(summary.warnings) == 2 and all("not verified" in w for w in summary.warnings)
    data = pl.read_excel(io.BytesIO(content), sheet_name="Data")
    assert data["type"].to_list() == ["consu", "consu"] and data["uom_id/id"].to_list() == [
        "unit_a",
        "unit_b",
    ]
    about = pl.read_excel(io.BytesIO(content), sheet_name="Read me", has_header=False)
    texts = [str(v) for col in about.columns for v in about[col].to_list()]
    assert any("not verified against the target" in t for t in texts)  # avertissement conservé
    assert not any(t.startswith("Ready for import") for t in texts)  # jamais « prêt » sans tous les contrôles
    with pytest.raises(FillError, match="allowed"):
        svc.prepare_file(
            answers, proposal, SHOP_CSV, "shop.csv", decisions, [Fill(field="type", common="combo")]
        )
    with pytest.raises(FillError, match="confirm"):
        svc.prepare_file(
            answers, proposal, SHOP_CSV, "shop.csv", decisions, [Fill(field="uom_id/id", common="u")]
        )
    with pytest.raises(FillError, match="row"):
        svc.prepare_file(
            answers, proposal, SHOP_CSV, "shop.csv", decisions, [Fill(field="type", per_row={5: "consu"})]
        )


# --------------------------------------------------------------------------- #
# Guided Resolution : réponses déduites, mots simples, valeurs du système connecté
# --------------------------------------------------------------------------- #
from uie_core.wizard import ReferenceOption  # noqa: E402


class FakeFinder:
    """Système connecté simulé : options réelles d'une référence (lecture seule)."""

    def __init__(self, options: dict[str, list[ReferenceOption]]) -> None:
        self.data = options
        self.calls: list[tuple[str, str, str, str | None]] = []

    def options(
        self, system: str, version: str, entity: str, attribute: str | None, instance_id: Any = None
    ) -> list[ReferenceOption]:
        self.calls.append((system, version, entity, attribute))
        return self.data.get(entity, [])


def _shop_with_storable(shop):  # noqa: ANN001, ANN202
    session, answers, target = shop
    RegistryService(session, SystemContext("test")).publish_snapshot(
        SchemaSnapshotModel(
            system="shop",
            product="shop",
            version="18",
            interface="orm",
            interface_type="orm",
            source={"loader": "test", "rev": 2},
            metadata={"system_name": "Shop ERP"},
            modules=[ModuleDef(technical_name="base")],
            entities=[
                EntityDef(
                    technical_name="product.template",
                    module="base",
                    fields=[
                        FieldDef(
                            technical_name="name", label="Name", data_type=DataType.STRING, required=True
                        ),
                        FieldDef(technical_name="default_code", label="Reference", data_type=DataType.STRING),
                        FieldDef(
                            technical_name="type",
                            label="Product Type",
                            data_type=DataType.ENUM,
                            required=True,
                            metadata={
                                "selection": [
                                    {"value": "consu", "label": "Goods"},
                                    {"value": "service", "label": "Service"},
                                ]
                            },
                        ),
                        FieldDef(
                            technical_name="uom_id", label="Unit", data_type=DataType.REFERENCE, required=True
                        ),
                        FieldDef(
                            technical_name="is_storable", label="Track Inventory", data_type=DataType.BOOLEAN
                        ),
                    ],
                    relations=[
                        RelationDef(
                            source_field="uom_id",
                            target_entity="uom.uom",
                            relation_type=RelationType.MANY_TO_ONE,
                        )
                    ],
                )
            ],
        )
    )
    target["fields"].append(
        {
            "name": "is_storable",
            "label": "Track Inventory",
            "type": "boolean",
            "length": None,
            "required": False,
            "example": "true",
            "description": None,
        }
    )
    answers = [*answers, _answer("stock", "yes", "Yes, I track stock quantities")]
    return session, answers, target


def _item(name, title, suggested=None, explanation=None, hints=None):  # noqa: ANN001, ANN202
    return {
        "name": name,
        "title": title,
        "help": None,
        "suggested_value": suggested,
        "explanation": explanation,
        "choices": [],
        "business_choices": hints or [],
    }


def test_answers_already_given_are_reused_and_checked_against_the_registry(shop):
    session, answers, target = _shop_with_storable(shop)
    fake = FakeMissing(
        [],
        target=target,
        missing={
            "fields": [
                _item(
                    "type", "What kind of products are they?", "consu", "You track stock, so they are goods."
                ),
                _item("is_storable", "Do you track stock?", "true", "You said you track stock quantities."),
                # une référence n'est jamais devinée, même si l'IA propose un identifiant
                _item(
                    "uom_id/id",
                    "How do you sell your products?",
                    "uom.unit",
                    None,
                    ["Piece", "Kilogram", "Litre"],
                ),
                _item("care_label", "Care instructions?"),
            ]
        },
    )
    svc = _svc(session, fake)
    proposal = svc.propose(answers)
    decisions = [
        MappingDecision(position=0, target="name"),
        MappingDecision(position=1, target="default_code"),
    ]
    info = svc.missing_info(answers, proposal, SHOP_CSV, "shop.csv", decisions)
    q = {x.field: x for x in info.questions}
    assert (q["type"].suggested, q["type"].title) == ("consu", "What kind of products are they?")
    assert q["type"].explanation == "You track stock, so they are goods."
    # facultatif, mais déduit des réponses : proposé (marqué facultatif)
    assert q["is_storable"].optional and q["is_storable"].suggested == "true"
    unit = q["uom_id/id"]
    assert unit.suggested is None and unit.choices == [] and unit.allow_unknown
    assert unit.hints == ["Piece", "Kilogram", "Litre"] and unit.title == "How do you sell your products?"
    # réponse métier ou « je ne sais pas » : rien d'écrit, le champ reste signalé
    summary, _, _ = svc.prepare_file(
        answers,
        proposal,
        SHOP_CSV,
        "shop.csv",
        decisions,
        [
            Fill(field="type", common="consu"),
            Fill(field="uom_id/id", hint="Piece"),
            Fill(field="care_label", unknown=True),
        ],
    )
    assert "uom_id/id" in summary.missing_required and "care_label" in summary.missing_required
    assert any("Piece" in w and "could not be looked up" in w for w in summary.warnings)
    assert any("not known yet" in w for w in summary.warnings)


def test_a_suggestion_outside_the_allowed_values_of_this_version_is_ignored(shop):
    session, answers, target = shop
    # valeur d'une AUTRE version : jamais appliquée
    fake = FakeMissing([], target=target, missing={"fields": [_item("type", "t", "product", "x")]})
    svc = _svc(session, fake)
    proposal = svc.propose(answers)
    info = svc.missing_info(
        answers, proposal, SHOP_CSV, "shop.csv", [MappingDecision(position=0, target="name")]
    )
    assert next(x for x in info.questions if x.field == "type").suggested is None


def test_values_of_a_connected_system_are_offered_and_need_no_confirmation(shop):
    session, answers, target = _shop_with_storable(shop)
    finder = FakeFinder({"uom.uom": [ReferenceOption(value="uom.product_uom_unit", label="Units")]})
    fake = FakeMissing([], target=target, missing={"fields": []})
    svc = MigrationWizardService(session, SystemContext("test"), fake, references=finder)  # type: ignore[arg-type]
    proposal = svc.propose(answers)
    decisions = [
        MappingDecision(position=0, target="name"),
        MappingDecision(position=1, target="default_code"),
    ]
    info = svc.missing_info(answers, proposal, SHOP_CSV, "shop.csv", decisions)
    unit = next(x for x in info.questions if x.field == "uom_id/id")
    assert unit.from_system
    assert [(c.value, c.label, c.verified) for c in unit.choices] == [("uom.product_uom_unit", "Units", True)]
    assert finder.calls[0] == ("shop", "18", "uom.uom", "id")  # entité liée lue dans le Registry
    summary, _, _ = svc.prepare_file(
        answers,
        proposal,
        SHOP_CSV,
        "shop.csv",
        decisions,
        [Fill(field="type", common="consu"), Fill(field="uom_id/id", common="uom.product_uom_unit")],
    )
    assert "uom_id/id" in summary.completed and summary.warnings == []  # lu dans le système : vérifié


# --------------------------------------------------------------------------- #
# Champs obligatoires non résolus : réponse gardée, résolue par le système connecté, sinon « à compléter »
# --------------------------------------------------------------------------- #
class FakeConnectedFinder(FakeFinder):
    def __init__(self, options: dict[str, list[ReferenceOption]], connected: bool) -> None:
        super().__init__(options)
        self.is_connected = connected

    def connected(self, system: str, version: str, instance_id: Any = None) -> bool:
        return self.is_connected


def _unresolved(shop, finder):  # noqa: ANN001, ANN202
    session, answers, target = _shop_with_storable(shop)
    fake = FakeMissing([], target=target, missing={"fields": []})
    svc = MigrationWizardService(session, SystemContext("test"), fake, references=finder)  # type: ignore[arg-type]
    proposal = svc.propose(answers)
    decisions = [
        MappingDecision(position=0, target="name"),
        MappingDecision(position=1, target="default_code"),
    ]
    return svc, answers, proposal, decisions


def test_a_business_answer_is_kept_and_stays_to_complete_without_a_connected_system(shop):
    svc, answers, proposal, decisions = _unresolved(shop, FakeConnectedFinder({}, connected=False))
    previous = [Fill(field="type", common="consu"), Fill(field="uom_id/id", hint="Box")]
    info = svc.missing_info(answers, proposal, SHOP_CSV, "shop.csv", decisions, fills=previous)
    unit = next(x for x in info.questions if x.field == "uom_id/id")
    assert unit.previous_hint == "Box" and unit.suggested is None  # gardée, jamais traduite en identifiant
    assert "connect your target system" in (unit.resolution or "") and "exact value" in (
        unit.resolution or ""
    )
    assert "ID" not in (unit.resolution or "")
    assert "type" not in {x.field for x in info.questions}  # réponse déjà validée : jamais redemandée
    summary, content, _ = svc.prepare_file(answers, proposal, SHOP_CSV, "shop.csv", decisions, previous)
    assert "uom_id/id" in summary.missing_required and summary.completed == ["type"]
    about = pl.read_excel(io.BytesIO(content), sheet_name="Read me", has_header=False)
    texts = [str(v) for col in about.columns for v in about[col].to_list()]
    assert "Not ready: required fields are still to complete" in texts


def test_a_business_answer_is_resolved_by_the_connected_system_when_one_value_has_its_name(shop):
    finder = FakeConnectedFinder(
        {
            "uom.uom": [
                ReferenceOption(value="u.box", label="Boxes"),
                ReferenceOption(value="u.kg", label="kg"),
            ]
        },
        connected=True,
    )
    svc, answers, proposal, decisions = _unresolved(shop, finder)
    previous = [Fill(field="type", common="consu"), Fill(field="uom_id/id", hint="Box")]
    info = svc.missing_info(answers, proposal, SHOP_CSV, "shop.csv", decisions, fills=previous)
    unit = next(x for x in info.questions if x.field == "uom_id/id")
    assert (unit.previous_hint, unit.suggested) == ("Box", "u.box")  # même nom (pluriel simple) : proposée
    assert unit.from_system and "Boxes" in (unit.explanation or "")
    resolved = [Fill(field="type", common="consu"), Fill(field="uom_id/id", common="u.box")]
    summary, _, _ = svc.prepare_file(answers, proposal, SHOP_CSV, "shop.csv", decisions, resolved)
    # lue dans le système : vérifiée, plus « à compléter », aucun avertissement
    assert "uom_id/id" not in summary.missing_required and summary.warnings == []


def test_without_a_matching_value_the_real_values_are_listed_never_guessed(shop):
    finder = FakeConnectedFinder({"uom.uom": [ReferenceOption(value="u.kg", label="kg")]}, connected=True)
    svc, answers, proposal, decisions = _unresolved(shop, finder)
    info = svc.missing_info(
        answers, proposal, SHOP_CSV, "shop.csv", decisions, fills=[Fill(field="uom_id/id", hint="Box")]
    )
    unit = next(x for x in info.questions if x.field == "uom_id/id")
    assert unit.suggested is None and [c.value for c in unit.choices] == ["u.kg"]
    assert "Box" in (unit.resolution or "")
    unknown = svc.missing_info(
        answers, proposal, SHOP_CSV, "shop.csv", decisions, fills=[Fill(field="uom_id/id", unknown=True)]
    )
    assert next(x for x in unknown.questions if x.field == "uom_id/id").previous_unknown


def test_an_entered_value_is_checked_in_the_connected_system_when_possible(shop):
    finder = FakeConnectedFinder({"uom.uom": [ReferenceOption(value="u.kg", label="kg")]}, connected=True)
    svc, answers, proposal, decisions = _unresolved(shop, finder)
    with pytest.raises(FillError, match="not found in your connected system"):
        svc.prepare_file(
            answers,
            proposal,
            SHOP_CSV,
            "shop.csv",
            decisions,
            [Fill(field="uom_id/id", common="u.typo", confirmed=True)],
        )
    ok, _, _ = svc.prepare_file(
        answers, proposal, SHOP_CSV, "shop.csv", decisions, [Fill(field="uom_id/id", common="u.kg")]
    )
    assert "uom_id/id" in ok.completed and ok.warnings == []


def test_the_type_of_data_shown_is_the_one_chosen_in_the_wizard(shop):
    from uie_core.wizard.service import data_type_of

    session, answers, target = shop
    proposal = _svc(session, FakeProposer([], target=target)).propose(answers)
    pick = _answer("object_subtype", "fert", "Finished goods")  # un SOUS-type n'est jamais le type de données
    assert data_type_of([*answers, pick], proposal) == proposal.title
    chosen = [*answers, pick, _answer("data_type", "products", "Products")]
    assert data_type_of(chosen, proposal) == "Products"
    assert (
        data_type_of([*answers, _answer("business_object", "customers", "Customers")], proposal)
        == "Customers"
    )
    summary, _, _ = _svc(session, FakeProposer([], target=target)).prepare_file(
        chosen, proposal, SHOP_CSV, "shop.csv", [MappingDecision(position=0, target="name")]
    )
    assert summary.data_type == "Products"


# --------------------------------------------------------------------------- #
# Fichier seul → import de fichier natif de la cible : références par NOM, « préparé » ≠ « prêt à importer »
# --------------------------------------------------------------------------- #
def _native(shop, finder=None):  # noqa: ANN001, ANN202
    """Import de fichier natif : colonnes = noms exacts du schéma, unité désignée par son NOM."""
    session, answers, target = _shop_with_storable(shop)
    target = _target("product.template", "name", "default_code", "type", "uom_id", rows=2)
    for i in (0, 2, 3):
        target["fields"][i]["required"] = True
    fake = FakeMissing(
        [],
        target=target,
        missing={
            "fields": [
                _item("type", "What kind of products?", "consu", "You sell goods only."),
                _item(
                    "uom_id",
                    "How do you sell your products?",
                    "Units",
                    "The standard unit for pieces.",
                    ["Piece"],
                ),
            ]
        },
    )
    svc = MigrationWizardService(session, SystemContext("test"), fake, references=finder)  # type: ignore[arg-type]
    proposal = svc.propose(answers)
    decisions = [
        MappingDecision(position=0, target="name"),
        MappingDecision(position=1, target="default_code"),
    ]
    return svc, answers, proposal, decisions, fake


def test_a_reference_by_name_gets_a_standard_name_never_an_identifier(shop):
    svc, answers, proposal, decisions, fake = _native(shop, FakeConnectedFinder({}, connected=False))
    info = svc.missing_info(
        answers, proposal, SHOP_CSV, "shop.csv", decisions, fills=[Fill(field="uom_id", hint="Piece")]
    )
    unit = next(x for x in info.questions if x.field == "uom_id")
    assert (
        unit.suggested == "Units" and not unit.from_system and unit.choices == []
    )  # nom proposé, à confirmer
    assert "suggests “Units”" in (unit.resolution or "") and unit.previous_hint == "Piece"
    assert next(f for f in fake.missing_requests[0].fields if f.name == "uom_id").reference_by == "name"
    # le produit : type déduit des réponses (valeur admise du Registry)
    assert next(x for x in info.questions if x.field == "type").suggested == "consu"


def test_prepared_is_not_ready_until_every_check_really_passed(shop):
    svc, answers, proposal, decisions, _ = _native(shop, FakeConnectedFinder({}, connected=False))
    assert proposal.verification == Verification.VERIFIED  # colonnes toutes connues du schéma publié
    missing, _, _ = svc.prepare_file(answers, proposal, SHOP_CSV, "shop.csv", decisions)
    assert missing.status == "to_complete"
    fills = [Fill(field="type", common="consu"), Fill(field="uom_id", common="Units", confirmed=True)]
    prepared, content, _ = svc.prepare_file(answers, proposal, SHOP_CSV, "shop.csv", decisions, fills)
    assert prepared.status == "prepared" and prepared.missing_required == []
    assert {c["label"]: c["ok"] for c in prepared.checks}["Every value you entered could be checked"] is False
    about = pl.read_excel(io.BytesIO(content), sheet_name="Read me", has_header=False)
    texts = [str(v) for col in about.columns for v in about[col].to_list()]
    assert any(t.startswith("Prepared file, not yet ready for import") for t in texts)


def test_ready_to_import_only_when_the_connected_system_confirms_the_linked_values(shop):
    finder = FakeConnectedFinder({"uom.uom": [ReferenceOption(value="Units", label="Units")]}, connected=True)
    svc, answers, proposal, decisions, _ = _native(shop, finder)
    fills = [Fill(field="type", common="consu"), Fill(field="uom_id", common="Units")]
    ready, content, _ = svc.prepare_file(answers, proposal, SHOP_CSV, "shop.csv", decisions, fills)
    assert ready.status == "ready" and all(c["ok"] for c in ready.checks) and ready.warnings == []
    about = pl.read_excel(io.BytesIO(content), sheet_name="Read me", has_header=False)
    assert "Ready for import: every check below passed" in [str(v) for v in about.to_series(1).to_list()]


UNIT_CSV = b"name,default_code,unit\nOak tray,P-1,Units\nBirch crate,P-2,units\n"
LINKED = "Categories, units and other linked values found in your"


def _with_unit_column(decisions):  # noqa: ANN001, ANN202
    return [*decisions, MappingDecision(position=2, target="uom_id")]


def test_an_unknown_linked_value_is_listed_with_its_rows_and_a_close_existing_value(shop):
    finder = FakeConnectedFinder({"uom.uom": [ReferenceOption(value="Units", label="Units")]}, connected=True)
    svc, answers, proposal, decisions, _ = _native(shop, finder)
    prepared, content, _ = svc.prepare_file(
        answers,
        proposal,
        UNIT_CSV,
        "shop.csv",
        _with_unit_column(decisions),
        [Fill(field="type", common="consu")],
    )
    (issue,) = [i for i in prepared.value_issues if i.field == "uom_id"]
    assert (issue.kind, issue.rows, issue.severity) == ("reference_unknown", [2], "to_check")
    assert "“units” → “Units”" in issue.fix  # proposé à l'utilisateur, jamais corrigé à sa place
    checks = {c["label"]: c["ok"] for c in prepared.checks}
    assert (
        prepared.status == "prepared" and next(v for k, v in checks.items() if k.startswith(LINKED)) is False
    )
    data = pl.read_excel(io.BytesIO(content), sheet_name="Data", infer_schema_length=0)
    assert data["uom_id"].to_list() == ["Units", "units"]  # gardée telle quelle : seul l'utilisateur corrige
    problems = pl.read_excel(io.BytesIO(content), sheet_name="Problems", infer_schema_length=0)
    assert problems["Rows in your file"].to_list() == ["2"] and problems["Type"].to_list() == ["To check"]


def test_linked_values_are_flagged_unverified_without_a_connected_system(shop):
    svc, answers, proposal, decisions, _ = _native(shop, FakeConnectedFinder({}, connected=False))
    prepared, _, _ = svc.prepare_file(
        answers,
        proposal,
        UNIT_CSV,
        "shop.csv",
        _with_unit_column(decisions),
        [Fill(field="type", common="consu")],
    )
    (issue,) = [i for i in prepared.value_issues if i.field == "uom_id"]
    assert (issue.kind, issue.rows, issue.count) == ("reference_unverified", [1, 2], 2)
    assert "Connect your" in issue.fix and prepared.status == "prepared"


def test_linked_values_all_found_in_the_connected_system_keep_the_file_ready(shop):
    options = [ReferenceOption(value="Units", label="Units"), ReferenceOption(value="units", label="units")]
    finder = FakeConnectedFinder({"uom.uom": options}, connected=True)
    svc, answers, proposal, decisions, _ = _native(shop, finder)
    ready, _, _ = svc.prepare_file(
        answers,
        proposal,
        UNIT_CSV,
        "shop.csv",
        _with_unit_column(decisions),
        [Fill(field="type", common="consu")],
    )
    assert ready.status == "ready" and ready.value_issues == []


def test_never_ready_while_a_mapped_value_does_not_fit_its_field(shop):
    """Même parcours que « ready », mais une liste JSON dans un champ à valeur unique : une question pour la
    colonne, valeur jamais écrite, jamais « Ready » ; le choix explicite (dernier élément) rend les contrôles
    verts."""
    finder = FakeConnectedFinder({"uom.uom": [ReferenceOption(value="Units", label="Units")]}, connected=True)
    svc, answers, proposal, decisions, _ = _native(shop, finder)
    csv = b'name,default_code\nOak tray,"[""Wood"",""P-1""]"\nBirch crate,P-2\n'
    fills = [Fill(field="type", common="consu"), Fill(field="uom_id", common="Units")]
    blocked, _, _ = svc.prepare_file(answers, proposal, csv, "shop.csv", decisions, fills)
    checks = {c["label"]: c["ok"] for c in blocked.checks}
    assert checks["Every decision about your values is made"] is False
    assert checks["Every value of your file fits its target field"] is False
    assert blocked.status == "prepared" and blocked.preview[0][1] == ""
    assert [q.key for q in blocked.value_questions] == ["list:1"]
    ready, _, _ = svc.prepare_file(answers, proposal, csv, "shop.csv", decisions, fills, {"list:1": "last"})
    assert ready.status == "ready" and ready.preview[0][1] == "P-1"
    assert [(t.kind, t.count) for t in ready.value_transforms] == [("list", 1)]  # montré, avant → après


def test_a_business_answer_gets_the_standard_name_of_the_target_only_for_a_reference_by_name(shop):
    session, answers, target = _shop_with_storable(shop)
    target = _target("product.template", "name", "default_code", "type", "uom_id", "uom_id/id", rows=2)
    for i in (0, 2, 3, 4):
        target["fields"][i]["required"] = True
    choices = [{"label": "Piece", "standard_name": "Units"}, {"label": "Pallet", "standard_name": None}]
    fake = FakeMissing(
        [],
        target=target,
        missing={
            "fields": [
                {**_item("uom_id", "How do you sell?"), "business_choices": choices},
                {**_item("uom_id/id", "Unit id?"), "business_choices": choices},
            ]
        },
    )
    svc = _svc(session, fake)
    proposal = svc.propose(answers)
    decisions = [MappingDecision(position=0, target="name")]
    info = svc.missing_info(answers, proposal, SHOP_CSV, "shop.csv", decisions)
    q = {x.field: x for x in info.questions}
    assert q["uom_id"].hints == ["Piece", "Pallet"] and q["uom_id"].hint_names == {"Piece": "Units"}
    assert q["uom_id/id"].hint_names == {}  # par identifiant : jamais de nom proposé
    again = svc.missing_info(
        answers, proposal, SHOP_CSV, "shop.csv", decisions, fills=[Fill(field="uom_id", hint="Piece")]
    )
    unit = next(x for x in again.questions if x.field == "uom_id")
    assert (unit.previous_hint, unit.suggested) == ("Piece", "Units") and "standard name" in (
        unit.explanation or ""
    )


def test_a_previous_answer_naming_exactly_one_allowed_value_is_deduced_without_llm():
    from uie_core.wizard.missing import deduce_choice
    from uie_core.wizard.model import AllowedValue, TargetField

    f = TargetField(
        name="type",
        label="Product Type",
        type="enum",
        required=True,
        allowed=[
            AllowedValue(value="consu", label="Goods"),
            AllowedValue(value="service", label="Service"),
            AllowedValue(value="combo", label="Combo"),
        ],
    )
    assert deduce_choice(f, [("target_system", "Odoo"), ("product_kind", "Goods only")]) == (
        "consu",
        "Goods",
        "Goods only",
    )
    assert deduce_choice(f, [("product_kind", "Services only")]) == ("service", "Service", "Services only")
    assert deduce_choice(f, [("product_kind", "Goods and services")]) is None  # ambigu : jamais deviné
    assert deduce_choice(f, [("a", "Goods only"), ("b", "Services only")]) is None  # contradictoire
    assert deduce_choice(f, [("a", "Goodsmith tools")]) is None  # mot entier seulement


def test_the_prepared_excel_suits_a_native_spreadsheet_import(shop):
    """Prix numérique écrit comme NOMBRE (même valeur), tout autre format gardé tel quel ; code-barres en
    texte ; colonnes entièrement vides retirées (la cible appliquera ses valeurs par défaut) et listées dans
    « Read me » ; une valeur appliquée d'office n'est jamais « complétée par vous »."""
    import re
    import zipfile

    session, answers, target = shop
    target = _target(
        "product.template", "name", "default_code", "type", "list_price", "barcode", "sale_ok", rows=2
    )
    target["fields"][3]["type"] = "decimal"
    target["fields"][4]["type"] = "text"
    target["fields"][5]["type"] = "boolean"
    svc = _svc(session, FakeProposer([], target=target))
    proposal = svc.propose(answers)
    csv = b"\n".join(
        [
            b"name,default_code,price,barcode",
            b"Oak tray,P-1,39.9,00123457",
            b'Birch crate,P-2,"39,9",00123464',
            b"",
        ]
    )
    decisions = [
        MappingDecision(position=0, target="name"),
        MappingDecision(position=1, target="default_code"),
        MappingDecision(position=2, target="list_price"),
        MappingDecision(position=3, target="barcode"),
    ]
    summary, content, _ = svc.prepare_file(
        answers, proposal, csv, "shop.csv", decisions, [Fill(field="type", common="consu", auto=True)]
    )
    assert summary.completed == [] and [a["field"] for a in summary.auto_filled] == ["type"]
    data = pl.read_excel(io.BytesIO(content), sheet_name="Data", infer_schema_length=0)
    assert data.columns == ["name", "default_code", "type", "list_price", "barcode"]  # sale_ok vide : retirée
    assert data["barcode"].to_list() == ["00123457", "00123464"]  # EAN-8 en texte : zéros conservés
    sheet = zipfile.ZipFile(io.BytesIO(content)).read("xl/worksheets/sheet1.xml").decode()
    cell = {m.group(1): m.group(2) for m in re.finditer(r'<c r="(D[23])"([^>]*)>', sheet)}
    # « 39,9 » : virgule décimale SANS ambiguïté (un seul chiffre après) → 39.9, écrit comme nombre, montré
    assert 't="s"' not in cell["D2"] and 't="s"' not in cell["D3"]
    assert data["list_price"].to_list() == ["39.9", "39.9"]
    assert summary.value_issues == []
    assert [(t.kind, t.examples) for t in summary.value_transforms] == [("separators", [["39,9", "39.9"]])]
    about = dict(pl.read_excel(io.BytesIO(content), sheet_name="Read me", has_header=False).rows())
    assert about["Completed by you"] == "None" and about["Filled from your answers"] == "type = Goods"
    assert about["Left out (no value)"].startswith("sale_ok:")


from uie_core.wizard import PreviousChoice, TargetField, TargetProposal  # noqa: E402
from uie_core.wizard.mapping import map_columns, prepare  # noqa: E402


class _RecordingColumns:
    """IA de correspondance simulée : s'abstient toujours, enregistre ce qui lui est envoyé."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def wizard_columns(self, request: WizardColumnsRequest) -> ColumnProposalBatch:
        self.sent += [(c.header, c.detected_type) for c in request.columns]
        return ColumnProposalBatch(proposals=[], meta=ProposalMeta(provider="fake", model="fake"))


def _fields_only(*names: str) -> TargetProposal:
    return TargetProposal(
        title="T",
        summary="",
        configuration=[],
        fields=[TargetField(name=n, label=n, type="decimal" if "price" in n else "text") for n in names],
        rows=[],
    )


def test_a_previous_choice_survives_a_regenerated_structure_and_is_never_lost_silently():
    """Choix précédent réappliqué par en-tête tant que son champ existe (sans IA) ; champ disparu → analyse
    normale ; type numérique lu sur les valeurs ; colonne avec valeurs sans champ → `not_imported`."""
    csv = b"Nom,Prix\nOak tray,39.9\nBirch crate,24.5\n"
    ai = _RecordingColumns()
    m = map_columns(
        _fields_only("name", "list_price", "standard_price"),
        csv,
        "f.csv",
        context={},
        proposer=ai,
        previous=[PreviousChoice(header="prix ", target="list_price")],
    )
    prix = m.columns[1]
    assert (prix.status, prix.target) == (ColumnStatus.PREVIOUS, "list_price") and ai.sent == [
        ("Nom", "text")
    ]
    # champ du choix précédent absent de la nouvelle structure : rien n'est forcé, l'IA est consultée
    ai = _RecordingColumns()
    m = map_columns(
        _fields_only("name", "sale_price"),
        csv,
        "f.csv",
        context={},
        proposer=ai,
        previous=[PreviousChoice(header="Prix", target="list_price")],
    )
    assert m.columns[1].status == ColumnStatus.UNMATCHED and ("Prix", "decimal") in ai.sent
    summary, _, _ = prepare(
        _fields_only("name", "sale_price"), csv, "f.csv", [MappingDecision(position=0, target="name")]
    )
    assert summary.not_imported == ["Prix"]


# --------------------------------------------------------------------------- #
# Questions prévisibles AVANT le fichier ; champ calculé par la cible (Registry) jamais demandé
# --------------------------------------------------------------------------- #
from uie_core.wizard.missing import set_by_target  # noqa: E402


def _with_purchase_unit(shop):  # noqa: ANN001, ANN202
    """Schéma où l'unité d'achat est CALCULÉE d'après l'unité de vente (`depends`, comme Odoo 18)."""
    session, answers, _ = shop
    unit = dict(data_type=DataType.REFERENCE, required=True)
    RegistryService(session, SystemContext("test")).publish_snapshot(
        SchemaSnapshotModel(
            system="shop",
            product="shop",
            version="18",
            interface="orm",
            interface_type="orm",
            source={"loader": "test", "rev": 3},
            metadata={"system_name": "Shop ERP"},
            modules=[ModuleDef(technical_name="base")],
            entities=[
                EntityDef(
                    technical_name="product.template",
                    module="base",
                    fields=[
                        FieldDef(
                            technical_name="name", label="Name", data_type=DataType.STRING, required=True
                        ),
                        FieldDef(
                            technical_name="type",
                            label="Product Type",
                            data_type=DataType.ENUM,
                            required=True,
                            metadata={"selection": [{"value": "consu", "label": "Goods"}]},
                        ),
                        FieldDef(technical_name="uom_id", label="Unit of Measure", **unit),
                        FieldDef(
                            technical_name="uom_po_id",
                            label="Purchase Unit",
                            metadata={"depends": ["uom_id", "uom_id.category_id"]},
                            **unit,
                        ),
                    ],
                    relations=[
                        RelationDef(
                            source_field=n, target_entity="uom.uom", relation_type=RelationType.MANY_TO_ONE
                        )
                        for n in ("uom_id", "uom_po_id")
                    ],
                )
            ],
        )
    )
    target = _target("product.template", "name", "type", "uom_id", "uom_po_id", rows=2)
    for f in target["fields"]:
        f["required"] = True
    fake = FakeMissing([], target=target, missing={"fields": []})
    return _svc(session, fake), answers, fake


def test_a_field_computed_by_the_target_is_never_asked_and_never_trusted_from_the_client(shop):
    svc, answers, _ = _with_purchase_unit(shop)
    proposal = svc.propose(answers)
    po = next(f for f in proposal.fields if f.name == "uom_po_id")
    assert po.computed_from == ["uom_id"]  # Registry : « uom_id.category_id » → « uom_id », une fois
    assert [d.name for d in set_by_target(po, proposal.fields)] == ["uom_id"]
    # une dépendance absente de la structure : rien n'est supposé, le champ reste à compléter
    assert set_by_target(po, [f for f in proposal.fields if f.name != "uom_id"]) == []
    # le client ne peut pas déclarer un champ « calculé » : recalculé d'après le Registry
    forged = proposal.model_copy(
        update={
            "fields": [
                f.model_copy(update={"computed_from": ["name"]}) if f.name == "type" else f
                for f in proposal.fields
            ]
        }
    )
    assert next(f for f in svc.verify(answers, forged).fields if f.name == "type").computed_from == []
    summary, _, _ = svc.prepare_file(
        answers, proposal, SHOP_CSV, "shop.csv", [MappingDecision(position=0, target="name")]
    )
    assert set(summary.missing_required) == {"type", "uom_id"}
    assert summary.set_by_target == [
        {"field": "uom_po_id", "label": "Purchase Unit", "source": "Unit of Measure"}
    ]


def test_predictable_questions_come_before_the_file_and_are_not_asked_again_after_it(shop):
    svc, answers, fake = _with_purchase_unit(shop)
    proposal = svc.propose(answers)
    # avant le fichier : valeurs admises et enregistrements de la cible ; ni le nom (propre à chaque ligne),
    # ni l'unité d'achat (calculée par la cible)
    early = svc.early_info(answers, proposal)
    assert [q.field for q in early.questions] == ["type", "uom_id"] and early.rows == []
    given = [Fill(field="type", common="consu"), Fill(field="uom_id", common="Units", confirmed=True)]
    assert svc.early_info(answers, proposal, fills=given).questions == []  # réponses gardées, sans doublon
    with pytest.raises(FillError, match="allowed"):
        svc.early_info(
            answers, proposal, fills=[Fill(field="type", common="combo")]
        )  # contrainte du Registry
    # après le fichier : rien de plus à demander, les réponses vont jusqu'au fichier préparé
    decisions = [MappingDecision(position=0, target="name")]
    after = svc.missing_info(answers, proposal, SHOP_CSV, "shop.csv", decisions, fills=given)
    assert after.questions == []
    summary, content, _ = svc.prepare_file(answers, proposal, SHOP_CSV, "shop.csv", decisions, given)
    assert summary.missing_required == []
    data = pl.read_excel(io.BytesIO(content), sheet_name="Data", infer_schema_length=0)
    assert data["type"].to_list() == ["consu", "consu"] and data["uom_id"].to_list() == ["Units", "Units"]
    assert "uom_po_id" not in data.columns  # jamais rempli par le Wizard : la cible le calcule


def test_a_file_column_wins_over_an_answer_given_before_the_file(shop):
    svc, answers, _ = _with_purchase_unit(shop)
    proposal = svc.propose(answers)
    csv = b"name,unit\nOak tray,Box\nBirch crate,\n"
    decisions = [MappingDecision(position=0, target="name"), MappingDecision(position=1, target="uom_id")]
    given = [Fill(field="type", common="consu"), Fill(field="uom_id", common="Units", confirmed=True)]
    _, content, _ = svc.prepare_file(answers, proposal, csv, "shop.csv", decisions, given)
    data = pl.read_excel(io.BytesIO(content), sheet_name="Data", infer_schema_length=0)
    assert data["uom_id"].to_list() == ["Box", "Units"]  # la réponse ne remplit que la cellule vide


# --------------------------------------------------------------------------- #
# Mode Simple « fichier d'abord »
# --------------------------------------------------------------------------- #
import uuid  # noqa: E402

from uie_core.wizard import InstanceTarget, QuickStart, WizardInputError, infer_data_type  # noqa: E402
from uie_core.wizard.lookup import InstanceChoice  # noqa: E402


def test_the_data_type_comes_from_the_headers_or_is_not_guessed():
    assert infer_data_type(["SKU", "Title", "Price", "Barcode", "Brand"]) == "Products"
    assert infer_data_type(["Name", "Email", "Phone", "City", "VAT", "Supplier number"]) == "Suppliers"
    assert infer_data_type(["Name", "Email", "Phone", "Street", "Customer id"]) == "Customers"
    assert infer_data_type(["Name", "Email", "Phone", "City"]) == "Customers & suppliers"
    assert infer_data_type(["col_a", "col_b", "Price"]) is None  # trop peu d'indices : le LLM ou une erreur
    assert infer_data_type(["Price", "SKU", "Email", "Phone"]) is None  # mélange : jamais tranché au hasard


def test_german_headers_name_the_data_type_without_the_llm():
    articles = [
        "Art.-Nr.", "Artikelbezeichnung", "Beschreibung", "Warengruppe", "Hersteller", "EAN", "Einheit",
        "Gewicht (kg)", "EK netto", "VK netto", "VK brutto", "MwSt", "Lieferant", "Bestand", "Aktiv",
        "Angelegt am", "Bemerkung",
    ]  # fmt: skip
    assert infer_data_type(articles) == "Products"  # « Lieferant » ne suffit pas
    assert infer_data_type(["Artikelnummer", "Bezeichnung", "Preis", "Einheit"]) == "Products"
    assert infer_data_type(["Lieferantennr.", "Firma", "Straße", "PLZ", "Ort", "Telefon"]) == "Suppliers"
    assert infer_data_type(["Kundennummer", "Firma", "Straße", "PLZ", "Ort"]) == "Customers"
    assert infer_data_type(["Firma", "Straße", "PLZ", "Ort", "Telefon"]) == "Customers & suppliers"


class FakeInstanceFinder(FakeConnectedFinder):
    def __init__(self, target: InstanceTarget) -> None:
        super().__init__({}, connected=target.connected)
        self.target = target
        self.asked: list[uuid.UUID] = []

    def instance_target(self, instance_id: uuid.UUID) -> InstanceTarget:
        self.asked.append(instance_id)
        return self.target


class FakeChoices(FakeInstanceFinder):
    def __init__(self, choices: list[InstanceChoice]) -> None:
        super().__init__(
            InstanceTarget(system="shop", product="shop", version="18", name="x", connected=True)
        )
        self.choices = choices

    def instance_choices(self) -> list[InstanceChoice]:
        return self.choices


def test_targets_list_your_systems_first_then_the_registry_then_known_systems(shop):
    session, _, _ = shop
    RegistryService(session, SystemContext("test")).register_known_system(
        "acme_erp", "Acme ERP", category="erp"
    )
    mine, old = uuid.uuid4(), uuid.uuid4()
    finder = FakeChoices(
        [
            InstanceChoice(
                id=mine, system="shop", product="shop", version="18", name="Main shop", connected=True
            ),
            # version absente du Registry : jamais proposée au démarrage par le fichier
            InstanceChoice(
                id=old, system="shop", product="shop", version="99", name="Old shop", connected=True
            ),
        ]
    )
    svc = MigrationWizardService(session, SystemContext("test"), None, references=finder)  # type: ignore[arg-type]
    targets = svc.quick_targets()
    assert (targets[0].group, targets[0].value) == ("instance", f"instance:{mine}")
    assert (targets[0].label, targets[0].detail, targets[0].connected) == ("Shop ERP 18", "Main shop", True)
    assert ("registry", "registry:shop:shop:18", "Shop ERP 18") in {
        (t.group, t.value, t.label) for t in targets
    }
    assert f"instance:{old}" not in {t.value for t in targets}
    assert [t.group for t in targets] == sorted(
        (t.group for t in targets), key=["instance", "registry", "known"].index
    )
    assert ("known", "known:acme_erp", "Acme ERP") in {(t.group, t.value, t.label) for t in targets}
    # sans lecteur d'instances (CLI, tests) : Registry et systèmes connus seulement
    alone = MigrationWizardService(session, SystemContext("test"), None).quick_targets()
    assert alone and all(t.group != "instance" for t in alone)


def test_a_connected_instance_gives_the_version_and_the_company_currency(shop):
    session, _, target = shop
    finder = FakeInstanceFinder(
        InstanceTarget(
            system="shop", product="shop", version="18", name="My shop", connected=True, currency="EUR"
        )
    )
    fake = FakeMissing([], target=target, missing={"fields": []})
    svc = MigrationWizardService(session, SystemContext("test"), fake, references=finder)  # type: ignore[arg-type]
    instance_id = uuid.uuid4()
    csv = b"name,sku,price,barcode\nOak tray,P-1,12,4006381333931\n"
    result = svc.quick_start(f"instance:{instance_id}", csv, "shop.csv")
    assert isinstance(result, QuickStart) and finder.asked == [instance_id]
    assert result.choices["currency"] == "EUR"  # devise de la société : décision déjà connue
    assert [a.value for a in result.answers[:2]] == ["registry:shop", "registry:shop:shop:18"]
    # l'instance choisie suit le parcours (valeurs réelles lues dans ELLE seule), libellé générique
    assert (result.answers[2].topic, result.answers[2].value) == (
        "connected_system",
        f"instance:{instance_id}",
    )
    assert result.target_label == "Shop ERP 18" and result.target and result.target.name == "My shop"
    # aucun domain pack pour ce système : la structure reste celle de l'IA (vérifiée par le Registry)
    assert result.structure_from == "ai" and fake.missing_requests == []  # questions sans LLM
    assert len(result.questions) <= 3
    with pytest.raises(WizardInputError):
        svc.quick_start("instance:not-a-uuid", csv, "shop.csv")


def test_targets_name_the_format_when_a_system_has_several(shop):
    """Système du Registry à plusieurs formats (produit ≠ système) : chaque cible porte son système et son
    format, pour une seule tuile du système dans le portail ; un système à un seul produit n'en a pas."""
    session, _, _ = shop
    registry = RegistryService(session, SystemContext("test"))
    for product, name, version in [
        ("journal", "Journal", "12"),
        ("journal", "Journal", "13"),
        ("people", "People", "5"),
    ]:
        registry.publish_snapshot(
            SchemaSnapshotModel(
                system="ledger",
                product=product,
                version=version,
                interface="file",
                interface_type="file",
                source={"loader": "test"},
                metadata={"system_name": "Ledger", "product_name": name},
                modules=[],
                entities=[
                    EntityDef(
                        technical_name="rows",
                        fields=[FieldDef(technical_name="a", data_type=DataType.STRING)],
                    )
                ],
            )
        )
    svc = MigrationWizardService(session, SystemContext("test"), None)  # type: ignore[arg-type]
    by_value = {t.value: t for t in svc.quick_targets()}
    journal = by_value["registry:ledger:journal:13"]
    assert (journal.system, journal.system_name, journal.format) == ("ledger", "Ledger", "Journal 13")
    assert by_value["registry:ledger:people:5"].format == "People 5"
    own = by_value["registry:shop:shop:18"]
    assert (own.system, own.format) == ("shop", None)
