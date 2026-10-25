"""Semantic Enrichment (V1b) générique : moteur de règles pur + SemanticEnrichmentService sur le système
imaginaire « shop » (aucune dépendance Odoo, aucun LLM, aucun appel réseau)."""

from __future__ import annotations

import ast
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from test_domains_core import (
    PASSWORD,
    ShopPack,
    ShopPackResolver,
    ShopProvider,
    ShopResolver,
    _f,
    _snap,
    _standard,
)
from uie_core.domains import (
    OTHER_KEY,
    DecisionSource,
    DomainEntityDef,
    DomainFieldDef,
    DomainKey,
    EntityRole,
    FieldGroup,
    FieldOrigin,
    MatchKind,
    MeaningStatus,
    ProductDomainService,
    ProtectionSpec,
    Relevance,
    RelevanceRule,
    SemanticEnrichmentService,
    SemanticError,
    SemanticSpec,
    Vocabulary,
    VocabularyEntry,
    classify,
)
from uie_core.identity import GO45_DEV_ORG_ID, SystemContext
from uie_core.instances import FernetSecretStore, InstanceCreate, InstanceService
from uie_core.registry import DataType, FieldDef, NotFound, RegistryService
from uie_core.registry import models as m

DOMAINS_SRC = Path(__file__).resolve().parents[1] / "src" / "uie_core" / "domains"


# --------------------------------------------------------------------------- #
# Vocabulaire et règles du système imaginaire
# --------------------------------------------------------------------------- #
def _key(key: str, group: FieldGroup, data_type: DataType, **kw: object) -> VocabularyEntry:
    return VocabularyEntry(
        key=key,
        group=group,
        label=kw.pop("label", key.replace("_", " ").capitalize()),
        description=f"{key} (test)",
        data_type=data_type,
        cardinality=kw.pop("cardinality", "single"),
        required_for_catalog=False,
        **kw,
    )


VOCABULARY = Vocabulary(
    domain_key=DomainKey.PRODUCT,
    version="t1",
    keys=[
        _key("sku", FieldGroup.IDENTITY, DataType.STRING, label="Item code", aliases=["sku", "item code"]),
        _key("internal_reference", FieldGroup.IDENTITY, DataType.STRING, aliases=["code"]),
        _key(
            "name", FieldGroup.IDENTITY, DataType.STRING, aliases=["title"], entity_roles=[EntityRole.PRIMARY]
        ),
        _key("list_price", FieldGroup.COMMERCIAL, DataType.DECIMAL, aliases=["price", "sales price"]),
        _key("weight", FieldGroup.LOGISTICS, DataType.DECIMAL, aliases=["weight"]),
        _key(
            "category",
            FieldGroup.CLASSIFICATION,
            DataType.REFERENCE,
            aliases=["category"],
            relation_target_roles=[EntityRole.CATEGORY],
        ),
        _key(
            "supplier_reference",
            FieldGroup.SUPPLIER,
            DataType.STRING,
            label="Supplier product code",
            patterns=[r"^zz_?supplier(_?ref)?$"],
        ),
        _key("description_internal", FieldGroup.DESCRIPTION, DataType.TEXT, label="Internal description"),
        _key("main_image", FieldGroup.MEDIA, DataType.BINARY),
    ],
)

RULES = [
    RelevanceRule.model_validate(r)
    for r in [
        {
            "id": "R01",
            "when": {"any_of": [{"technical": True}, {"name_patterns": ["^id$"]}]},
            "relevance": "NOT_RELEVANT",
            "reason": "technical",
        },
        {
            "id": "R02",
            "when": {"listed": True, "group_not_in": ["other"]},
            "relevance": "RELEVANT",
            "reason": "listed",
        },
        {"id": "R03", "when": {"is_media": True}, "relevance": "RELEVANT", "reason": "image"},
        {
            "id": "R04",
            "when": {"relation_outside_scope": True},
            "relevance": "NOT_RELEVANT",
            "reason": "link to {target}",
        },
        {
            "id": "R05",
            "when": {"modules_all_in": ["rating"]},
            "relevance": "NOT_RELEVANT",
            "reason": "module {modules}",
        },
        {
            "id": "R08",
            "when": {"min_match_confidence": 0.9},
            "relevance": "RELEVANT",
            "reason": "vocabulary ({key})",
        },
        {
            "id": "R09",
            "when": {"origins": ["CUSTOM"], "no_match": True},
            "relevance": "POSSIBLE",
            "reason": "custom",
        },
        {"id": "R10", "relevance": "POSSIBLE", "reason": "default"},
    ]
]


def _spec(field_keys: dict[str, str] | None = None, rules: list[RelevanceRule] | None = None) -> SemanticSpec:
    return SemanticSpec(
        domain_key=DomainKey.PRODUCT,
        version="1.0.0+t1",
        vocabulary=VOCABULARY,
        rules=rules or RULES,
        field_keys=field_keys or {"article.title": "name"},
        structure_checks=[EntityRole.PRIMARY, EntityRole.VARIANT, EntityRole.CATEGORY, EntityRole.MEDIA],
    )


ENTITIES = [
    DomainEntityDef(technical_name="article", role=EntityRole.PRIMARY, present=True),
    DomainEntityDef(technical_name="article_variant", role=EntityRole.VARIANT, present=True),
    DomainEntityDef(technical_name="shelf", role=EntityRole.CATEGORY, present=True),
]


def fd(
    name: str, data_type: DataType = DataType.STRING, *, entity: str = "article", **kw: object
) -> DomainFieldDef:
    return DomainFieldDef(
        entity_technical_name=entity,
        field_technical_name=name,
        group=kw.pop("group", FieldGroup.OTHER),
        relevance=kw.pop("relevance", Relevance.POSSIBLE),
        label=kw.pop("label", name.replace("_", " ").title()),
        data_type=data_type.value,
        **kw,
    )


def _by_path(fields: list[DomainFieldDef], spec: SemanticSpec | None = None) -> dict:
    return {d.path: d for d in classify(fields, ENTITIES, spec or _spec())}


# --------------------------------------------------------------------------- #
# 1-7. Moteur de règles (pur, sans base)
# --------------------------------------------------------------------------- #
def test_rule_order_first_match_wins_and_rule_id_is_traced():
    d = _by_path(
        [
            fd("id", DataType.INTEGER, technical=True, listed=True, group=FieldGroup.IDENTITY),
            fd("title", listed=True, group=FieldGroup.IDENTITY),
            fd("price", DataType.DECIMAL),
            fd("misc"),
        ]
    )
    # technique ET listé : R01 passe avant R02 (première règle qui correspond)
    assert (d["article.id"].rule_id, d["article.id"].relevance) == ("R01", Relevance.NOT_RELEVANT)
    assert d["article.id"].status == MeaningStatus.EXCLUDED and d["article.id"].semantic_key is None
    assert (d["article.title"].rule_id, d["article.title"].relevance) == ("R02", Relevance.RELEVANT)
    assert (d["article.price"].rule_id, d["article.price"].reason) == ("R08", "vocabulary (list_price)")
    assert (d["article.misc"].rule_id, d["article.misc"].relevance) == ("R10", Relevance.POSSIBLE)


def test_relation_outside_scope_is_not_relevant_with_reason():
    d = _by_path(
        [
            fd("invoice_id", DataType.REFERENCE, relation_target="invoice"),
            fd("category", DataType.REFERENCE, relation_target="shelf"),
        ]
    )
    out = d["article.invoice_id"]
    assert (out.relevance, out.rule_id, out.reason) == (Relevance.NOT_RELEVANT, "R04", "link to invoice")
    assert d["article.category"].relevance == Relevance.RELEVANT  # cible du domaine (rôle CATEGORY)


def test_non_catalog_module_is_not_relevant():
    d = _by_path(
        [
            fd("ext_rating", DataType.DECIMAL, modules=["rating"], origin=FieldOrigin.THIRD_PARTY),
            fd("ext_mixed", DataType.DECIMAL, modules=["rating", "shop"], origin=FieldOrigin.THIRD_PARTY),
        ]
    )
    assert (d["article.ext_rating"].rule_id, d["article.ext_rating"].reason) == ("R05", "module rating")
    assert d["article.ext_mixed"].relevance != Relevance.NOT_RELEVANT  # un module du catalogue suffit


def test_pack_alias_wins_over_generic_alias():
    field = fd("code")  # alias générique exact de internal_reference (0.95)
    assert _by_path([field])["article.code"].semantic_key == "internal_reference"
    d = _by_path([field], _spec({"article.code": "sku"}))["article.code"]
    assert (d.semantic_key, d.confidence, d.match, d.source) == (
        "sku",
        0.98,
        MatchKind.PACK,
        DecisionSource.STANDARD,
    )


def test_incompatible_type_or_target_role_cancels_the_proposal():
    d = _by_path(
        [
            fd("weight"),  # alias exact, mais texte au lieu d'un nombre
            fd("category", DataType.REFERENCE, relation_target="article_variant"),  # cible VARIANT ≠ CATEGORY
            fd("title", entity="article_variant"),  # clé réservée aux entités PRIMARY
        ]
    )
    assert d["article.weight"].semantic_key is None
    assert d["article.category"].semantic_key is None
    assert d["article_variant.title"].semantic_key is None


def test_unicity_conflict_on_single_key_and_taken_key():
    d = _by_path([fd("sku"), fd("item_code"), fd("item_code_old")])
    # deux alias exacts (0.95) sur la clé single « sku » : le premier par nom garde KNOWN, l'autre est posé
    assert (d["article.item_code"].status, d["article.item_code"].semantic_key) == (
        MeaningStatus.KNOWN,
        "sku",
    )
    loser = d["article.sku"]
    assert (loser.status, loser.pending) == (MeaningStatus.PROPOSED, True)
    assert loser.reason == "Conflicts with article.item_code (sku)"
    # proposition faible (tokens) sur une clé déjà tenue avec certitude : abandonnée, pas de question
    weak = d["article.item_code_old"]
    assert (weak.semantic_key, weak.status, weak.pending) == (None, MeaningStatus.UNRESOLVED, False)


def test_thresholds_known_proposed_unresolved():
    d = _by_path(
        [
            fd("zz_supplier_ref"),  # motif 0.90, standard → KNOWN
            fd("sales_price_net", DataType.DECIMAL),  # tokens 2/3 → 0.63, standard → PROPOSED (question)
            fd("internal_notes", DataType.TEXT, label="Internal description"),  # libellé du vocabulaire 0.85
            fd("zz_supplier", origin=FieldOrigin.CUSTOM),  # motif 0.90 mais CUSTOM → PROPOSED (question)
            fd("zz_color", origin=FieldOrigin.CUSTOM),  # CUSTOM sans proposition → UNRESOLVED (question)
            fd("colour"),  # standard sans proposition → UNRESOLVED, pas de question
        ]
    )

    def st(path: str) -> tuple:
        x = d[path]
        return x.status, x.semantic_key, x.confidence, x.pending

    assert st("article.zz_supplier_ref") == (MeaningStatus.KNOWN, "supplier_reference", 0.90, False)
    assert st("article.sales_price_net") == (MeaningStatus.PROPOSED, "list_price", 0.63, True)
    assert d["article.sales_price_net"].match == MatchKind.TOKENS
    assert st("article.internal_notes") == (MeaningStatus.PROPOSED, "description_internal", 0.85, True)
    assert st("article.zz_supplier") == (MeaningStatus.PROPOSED, "supplier_reference", 0.90, True)
    assert st("article.zz_color") == (MeaningStatus.UNRESOLVED, None, None, True)
    assert d["article.zz_color"].rule_id == "R09"
    assert st("article.colour") == (MeaningStatus.UNRESOLVED, None, None, False)


def test_related_copy_of_a_custom_field_follows_its_source():
    d = _by_path(
        [
            fd("zz_supplier", origin=FieldOrigin.CUSTOM),
            fd("article_id", DataType.REFERENCE, entity="article_variant", relation_target="article"),
            fd(
                "zz_supplier",
                entity="article_variant",
                origin=FieldOrigin.CUSTOM,
                related="article_id.zz_supplier",
            ),
        ]
    )
    copy = d["article_variant.zz_supplier"]
    assert (copy.mirror_of, copy.rule_id, copy.pending) == ("article.zz_supplier", "MIRROR", False)
    assert (copy.status, copy.semantic_key) == (MeaningStatus.PROPOSED, "supplier_reference")
    assert d["article.zz_supplier"].pending is True  # une seule question pour une seule donnée


# --------------------------------------------------------------------------- #
# V1b.1 : prédicats back_reference / related_copy / entity_roles, follow_source, protection
# --------------------------------------------------------------------------- #
def _rule(rule_id: str, when: dict, **outcome: object) -> RelevanceRule:
    return RelevanceRule.model_validate(
        {"id": rule_id, "when": when, "reason": f"{rule_id} {{inverse}}", **outcome}
    )


def _with(*extra: RelevanceRule) -> SemanticSpec:
    return _spec(rules=[*RULES[:-1], *extra, RULES[-1]])


def test_back_reference_one2many_inverse_and_many2many_shared_table():
    fields = [
        # one2many dont l'inverse est un lien vers l'entité courante : la relation est portée par la cible
        fd("variant_ids", DataType.COLLECTION, relation_target="article_variant", inverse_field="article_id"),
        fd("article_id", DataType.REFERENCE, entity="article_variant", relation_target="article"),
        # many2many : même table des deux côtés
        fd("shelf_ids", DataType.COLLECTION, relation_target="shelf", relation_table="article_shelf_rel"),
        fd(
            "article_ids",
            DataType.COLLECTION,
            entity="shelf",
            relation_target="article",
            relation_table="article_shelf_rel",
        ),
        # négatifs : many2many réflexif (seul champ de sa table), one2many dont l'inverse pointe ailleurs
        fd(
            "similar_ids",
            DataType.COLLECTION,
            relation_target="article",
            relation_table="article_similar_rel",
        ),
        fd(
            "sibling_ids",
            DataType.COLLECTION,
            entity="article_variant",
            relation_target="article_variant",
            inverse_field="article_id",
        ),
    ]
    d = _by_path(fields, _with(_rule("RB", {"back_reference": True}, relevance="NOT_RELEVANT")))
    assert d["article.variant_ids"].rule_id == "RB"
    assert d["article.variant_ids"].reason == "RB article_variant.article_id"
    assert d["article.shelf_ids"].reason == "RB shelf.article_ids"
    assert d["shelf.article_ids"].reason == "RB article.shelf_ids"  # vrai des deux côtés d'une many2many
    assert d["article.similar_ids"].rule_id != "RB"
    assert d["article_variant.sibling_ids"].rule_id != "RB"
    assert d["article_variant.article_id"].rule_id != "RB"  # un lien simple n'est jamais une back-référence


def test_entity_roles_condition():
    spec = _with(
        _rule("RC", {"entity_roles": ["CATEGORY"], "name_patterns": ["^property_"]}, relevance="NOT_RELEVANT")
    )
    d = _by_path([fd("property_default"), fd("property_default", entity="shelf")], spec)
    assert (d["shelf.property_default"].rule_id, d["article.property_default"].rule_id) == ("RC", "R10")


def test_follow_source_rule_gives_the_final_decision_of_the_source_even_through_a_chain():
    fields = [
        fd("shelf_id", DataType.REFERENCE, relation_target="shelf"),
        fd("title", entity="shelf", listed=True, group=FieldGroup.CLASSIFICATION),
        fd("shelf_title", related="shelf_id.title"),  # copie d'un champ de shelf
        fd("article_id", DataType.REFERENCE, entity="article_variant", relation_target="article"),
        fd("shelf_title", entity="article_variant", related="article_id.shelf_title"),  # copie d'une copie
        fd("id", entity="shelf"),
        fd("shelf_ref", related="shelf_id.id"),  # copie d'un champ exclu
    ]
    spec = _with(_rule("RF", {"related_copy": True}, follow_source=True))
    spec = spec.model_copy(
        update={"rules": [spec.rules[0], spec.rules[-2], *spec.rules[1:-2], spec.rules[-1]]}
    )
    d = _by_path(fields, spec)
    for path in ("article.shelf_title", "article_variant.shelf_title"):
        assert (d[path].rule_id, d[path].relevance, d[path].status) == (
            "RF",
            Relevance.RELEVANT,
            MeaningStatus.KNOWN,
        )
    assert d["article_variant.shelf_title"].mirror_of == "article.shelf_title"
    assert (d["article.shelf_ref"].relevance, d["article.shelf_ref"].status) == (
        Relevance.NOT_RELEVANT,
        MeaningStatus.EXCLUDED,
    )
    # sans règle follow_source, une copie standard garde son propre classement
    assert _by_path(fields)["article.shelf_title"].rule_id == "R10"


def test_protected_field_is_never_excluded_nor_attached_by_a_rule():
    fields = [
        fd("shelf_id", DataType.REFERENCE, relation_target="shelf"),
        fd("id", entity="shelf"),
        fd("shelf_ref", related="shelf_id.id"),
        fd("warehouse_id", DataType.REFERENCE, relation_target="warehouse"),
    ]
    spec = _with(_rule("RF", {"related_copy": True}, follow_source=True)).model_copy(
        update={"protection": ProtectionSpec(fields=["article.shelf_ref", "article.warehouse_id"])}
    )
    d = _by_path(fields, spec)
    assert d["article.warehouse_id"].rule_id == "R10"  # R04 sautée
    assert d["article.shelf_ref"].rule_id == "R10" and d["article.shelf_ref"].mirror_of is None


def test_a_rule_has_exactly_one_outcome():
    with pytest.raises(ValueError, match="exactement un"):
        RelevanceRule.model_validate({"id": "X", "reason": "x"})
    with pytest.raises(ValueError, match="exactement un"):
        RelevanceRule.model_validate(
            {"id": "X", "reason": "x", "relevance": "POSSIBLE", "follow_source": True}
        )


# --------------------------------------------------------------------------- #
# Service (base de données) : instance « shop » dont le schéma évolue
# --------------------------------------------------------------------------- #
class ScriptedProvider(ShopProvider):
    """Chaque découverte rend `self.schema` (modifiable par le test)."""

    def __init__(self) -> None:
        super().__init__()
        self.schema = _instance(zz_type=DataType.STRING)

    def discover_schema(self, ctx):  # noqa: ANN001, ANN201
        self.round += 1
        return self.schema


def _instance(*, zz_type: DataType, with_zz: bool = True, extra: tuple = ()) -> object:
    art, variant, invoice = _standard()
    fields = [f for f in art.fields if f.technical_name != "price"]
    fields += [_f("ext_rating", DataType.DECIMAL, modules=["rating"]), _f("zz_color")]
    if with_zz:
        native = {DataType.STRING: "char", DataType.INTEGER: "integer"}[zz_type]
        fields.append(
            FieldDef(technical_name="zz_supplier", label="Zz Supplier", data_type=zz_type, native_type=native)
        )
    fields += list(extra)
    return _snap([art.model_copy(update={"fields": fields}), variant, invoice])


class SemanticShopPack(ShopPack):
    def __init__(self) -> None:
        self.spec = _spec()

    def semantics(self) -> SemanticSpec:
        return self.spec


@pytest.fixture
def world(session):
    RegistryService(session, SystemContext("test")).publish_snapshot(_snap(_standard()))
    pack = SemanticShopPack()
    provider = ScriptedProvider()
    instances = InstanceService(
        session,
        SystemContext("test"),
        FernetSecretStore(FernetSecretStore.generate_key()),
        ShopResolver(provider),
    )
    inst = instances.create(
        InstanceCreate(
            organization_id=GO45_DEV_ORG_ID,
            name="shop-semantic",
            system="shop",
            version="1",
            endpoint="https://shop.invalid",
            password=SecretStr(PASSWORD),
        )
    )
    instances.discover_schema(inst.id)
    domains = ProductDomainService(session, SystemContext("test"), ShopPackResolver(pack))
    return {
        "inst": inst,
        "instances": instances,
        "provider": provider,
        "pack": pack,
        "domains": domains,
        "svc": SemanticEnrichmentService(
            session, SystemContext("test"), ShopPackResolver(pack), domains=domains
        ),
    }


def _row(session, path: str) -> m.FieldSemanticBinding:
    entity, field = path.rsplit(".", 1)
    return session.scalar(
        select(m.FieldSemanticBinding).where(
            m.FieldSemanticBinding.entity_technical_name == entity,
            m.FieldSemanticBinding.field_technical_name == field,
        )
    )


def _pending(svc, inst) -> list[str]:
    return [q.path for q in svc.get_pending_questions(inst.id)]


def test_analyze_enriches_and_summary_is_consistent(world, session):
    w = world
    out = w["domains"].analyze(w["inst"].id)
    assert out.enrichment is not None and out.enrichment.created == len(
        [f for f in out.stored.snapshot.fields]
    )
    # le domain snapshot référence la version exacte des règles
    assert out.stored.snapshot.semantic_rules_version == "1.0.0+t1"
    assert out.stored.snapshot.semantic_rules_checksum == w["pack"].spec.checksum
    assert _pending(w["svc"], w["inst"]) == [
        "article.zz_supplier",
        "article.zz_color",
    ]  # confiance décroissante
    s = w["svc"].summary(w["inst"].id)
    assert s.pending + s.excluded_auto + s.excluded_by_supplier + s.identified + s.possible == s.total
    assert s.excluded_auto_by_rule == {"R01": 1, "R05": 1}
    assert [(c.role, c.present) for c in s.structure] == [
        (EntityRole.PRIMARY, True),
        (EntityRole.VARIANT, True),
        (EntityRole.CATEGORY, False),  # shelf absente du système
        (EntityRole.MEDIA, True),
    ]
    assert not s.complete
    # lire ne recalcule rien ; une 2e analyse ne change rien
    again = w["domains"].analyze(w["inst"].id).enrichment
    assert (again.created, again.updated, again.unchanged) == (0, 0, out.enrichment.created)


def test_human_decisions_persist_and_are_journaled(world, session):
    w = world
    svc, inst = w["svc"], w["inst"]
    w["domains"].analyze(inst.id)
    b = svc.confirm(inst.id, "article", "zz_supplier")  # accepte la proposition
    assert (b.meaning_status, b.semantic_key, b.source, b.relevance) == (
        MeaningStatus.CONFIRMED,
        "supplier_reference",
        DecisionSource.HUMAN,
        Relevance.RELEVANT,
    )
    assert b.semantic_label == "Supplier product code" and b.confirmed_at is not None
    b = svc.change(inst.id, "article", "zz_color", OTHER_KEY, note="colour code of the vendor")
    assert (b.meaning_status, b.semantic_key, b.note, b.pending) == (
        MeaningStatus.UNRESOLVED,
        None,
        "colour code of the vendor",
        False,
    )
    b = svc.change(inst.id, "article", "zz_color", "internal_reference")
    assert (b.meaning_status, b.semantic_key, b.confidence) == (
        MeaningStatus.CONFIRMED,
        "internal_reference",
        None,
    )
    b = svc.ignore(inst.id, "article", "notes")
    assert (b.meaning_status, b.relevance) == (MeaningStatus.IGNORED, Relevance.NOT_RELEVANT)
    # contestation d'une exclusion automatique : redevient une question, décision humaine
    b = svc.include(inst.id, "article", "ext_rating")
    assert (b.meaning_status, b.relevance, b.source, b.pending) == (
        MeaningStatus.UNRESOLVED,
        Relevance.POSSIBLE,
        DecisionSource.HUMAN,
        True,
    )
    assert _pending(svc, inst) == ["article.ext_rating"]
    session.flush()
    actions = [e.action for e in _row(session, "article.zz_color").events]
    assert actions == ["ENRICH", "CHANGE", "CHANGE"]
    assert [(e.from_status, e.to_status) for e in _row(session, "article.ext_rating").events] == [
        (None, "EXCLUDED"),
        ("EXCLUDED", "UNRESOLVED"),
    ]
    s = svc.summary(inst.id)
    assert (s.excluded_by_supplier, s.pending) == (1, 1)


def test_supplier_answers_are_listed_and_can_be_reopened(world, session):
    w = world
    svc, inst = w["svc"], w["inst"]
    w["domains"].analyze(inst.id)
    assert svc.list_decisions(inst.id) == []
    svc.change(inst.id, "article", "zz_supplier", "internal_reference")  # erreur de saisie
    svc.ignore(inst.id, "article", "zz_color")
    assert [b.path for b in svc.list_decisions(inst.id)] == ["article.zz_color", "article.zz_supplier"]
    assert svc.summary(inst.id).supplier_decisions == 2

    # rouvrir : redevient une question, la clé confirmée reste pré-sélectionnée
    b = svc.reopen(inst.id, "article", "zz_supplier", note="wrong key")
    assert (b.meaning_status, b.semantic_key, b.pending, b.source, b.relevance) == (
        MeaningStatus.PROPOSED,
        "internal_reference",
        True,
        DecisionSource.HUMAN,
        Relevance.POSSIBLE,
    )
    assert b.previous == {
        "meaning_status": "CONFIRMED",
        "semantic_key": "internal_reference",
        "relevance": "RELEVANT",
        "pending": False,
    }
    # une réponse IGNORED rouverte reprend la proposition des règles (ici aucune → UNRESOLVED)
    b = svc.reopen(inst.id, "article", "zz_color")
    assert (b.meaning_status, b.semantic_key, b.pending, b.previous["meaning_status"]) == (
        MeaningStatus.UNRESOLVED,
        None,
        True,
        "IGNORED",
    )
    assert svc.list_decisions(inst.id) == []
    assert set(_pending(svc, inst)) == {"article.zz_supplier", "article.zz_color"}
    with pytest.raises(SemanticError, match="aucune réponse"):
        svc.reopen(inst.id, "article", "zz_color")  # déjà une question
    with pytest.raises(SemanticError, match="aucune réponse"):
        svc.reopen(inst.id, "article", "notes")  # décision de règle

    # une nouvelle analyse ne referme pas la question rouverte
    assert w["domains"].analyze(inst.id).enrichment.pending == 2
    b = svc.confirm(inst.id, "article", "zz_supplier", "supplier_reference")
    assert (b.meaning_status, b.pending, b.previous) == (MeaningStatus.CONFIRMED, False, None)
    session.flush()
    assert [e.action for e in _row(session, "article.zz_supplier").events] == [
        "ENRICH",
        "CHANGE",
        "REOPEN",
        "CONFIRM",
    ]


def test_unknown_key_is_refused_other_is_accepted(world):
    w = world
    w["domains"].analyze(w["inst"].id)
    with pytest.raises(SemanticError, match="inconnue"):
        w["svc"].change(w["inst"].id, "article", "zz_color", "free_text_meaning")
    with pytest.raises(SemanticError, match="aucune proposition"):
        w["svc"].confirm(w["inst"].id, "article", "zz_color")  # rien à accepter
    with pytest.raises(NotFound):
        w["svc"].confirm(w["inst"].id, "article", "does_not_exist", "sku")
    assert (
        w["svc"].change(w["inst"].id, "article", "zz_color", OTHER_KEY).meaning_status
        == MeaningStatus.UNRESOLVED
    )


def test_new_snapshot_compatible_signature_reapplies_human_decision(world):
    w = world
    svc, inst = w["svc"], w["inst"]
    w["domains"].analyze(inst.id)
    svc.confirm(inst.id, "article", "zz_supplier")
    svc.change(inst.id, "article", "zz_color", "internal_reference")
    w["instances"].discover_schema(inst.id)  # même schéma → nouveau snapshot d'instance
    e = w["domains"].analyze(inst.id).enrichment
    assert e.instance_schema_snapshot_sequence == 2 and (e.reapplied, e.stale, e.pending) == (2, 0, 0)
    assert _pending(svc, inst) == [] and svc.summary(inst.id).complete


def test_changed_signature_makes_the_question_stale(world, session):
    w = world
    svc, inst = w["svc"], w["inst"]
    w["domains"].analyze(inst.id)
    svc.confirm(inst.id, "article", "zz_supplier")
    w["provider"].schema = _instance(zz_type=DataType.INTEGER)  # type natif modifié
    w["instances"].discover_schema(inst.id)
    e = w["domains"].analyze(inst.id).enrichment
    assert e.stale == 1
    q = next(q for q in svc.get_pending_questions(inst.id) if q.path == "article.zz_supplier")
    assert q.meaning_status == MeaningStatus.STALE
    assert q.semantic_key == "supplier_reference"  # ancienne réponse pré-sélectionnée
    assert q.previous["meaning_status"] == "CONFIRMED"
    assert q.previous["technical_signature"]["data_type"] == "string"
    assert [ev.action for ev in _row(session, "article.zz_supplier").events][-1] == "STALE"
    svc.confirm(inst.id, "article", "zz_supplier", "internal_reference")
    assert "article.zz_supplier" not in _pending(svc, inst)


def test_vanished_field_is_orphaned_and_kept(world, session):
    w = world
    svc, inst = w["svc"], w["inst"]
    w["domains"].analyze(inst.id)
    svc.confirm(inst.id, "article", "zz_supplier")
    w["provider"].schema = _instance(zz_type=DataType.STRING, with_zz=False)
    w["instances"].discover_schema(inst.id)
    assert w["domains"].analyze(inst.id).enrichment.orphaned == 1
    row = _row(session, "article.zz_supplier")
    assert (row.meaning_status, row.semantic_key, row.previous["meaning_status"]) == (
        "ORPHANED",
        "supplier_reference",
        "CONFIRMED",
    )
    assert "article.zz_supplier" not in {b.path for b in svc.list_bindings(inst.id)}  # invisible
    assert svc.summary(inst.id).orphaned == 1
    with pytest.raises(NotFound):
        svc.confirm(inst.id, "article", "zz_supplier")
    # le champ réapparaît : la décision humaine revient telle quelle
    w["provider"].schema = _instance(zz_type=DataType.STRING)
    w["instances"].discover_schema(inst.id)
    w["domains"].analyze(inst.id)
    assert _row(session, "article.zz_supplier").meaning_status == "CONFIRMED"


def test_rule_binding_is_recomputed_human_binding_never_overwritten(world, session):
    w = world
    svc, inst, pack = w["svc"], w["inst"], w["pack"]
    w["domains"].analyze(inst.id)
    svc.change(inst.id, "article", "zz_supplier", "internal_reference")  # HUMAN
    assert _row(session, "article.notes").meaning_status == "UNRESOLVED"  # RULE : aucune proposition
    # nouvelle version du pack : « notes » déclaré par le pack, zz_supplier déclaré autrement
    pack.version_ = "1.1.0"
    pack.spec = _spec(
        {"article.title": "name", "article.notes": "description_internal", "article.zz_supplier": "sku"}
    )
    e = w["domains"].analyze(inst.id).enrichment
    assert e.updated >= 1 and e.reapplied == 1
    notes = _row(session, "article.notes")
    assert (notes.meaning_status, notes.semantic_key, notes.source) == (
        "KNOWN",
        "description_internal",
        "STANDARD",
    )
    zz = _row(session, "article.zz_supplier")
    assert (zz.semantic_key, zz.source, zz.meaning_status) == ("internal_reference", "HUMAN", "CONFIRMED")


# --------------------------------------------------------------------------- #
# 14. Architecture : aucun LLM, aucun appel réseau
# --------------------------------------------------------------------------- #
def test_no_llm_and_no_network_during_enrichment(world, monkeypatch):
    forbidden = ("uie_ai", "packages.ai", "openai", "anthropic", "httpx", "requests", "urllib", "socket")
    for py in DOMAINS_SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import | ast.ImportFrom):
                mods = [a.name for a in node.names] + [getattr(node, "module", None) or ""]
                assert not any(x.startswith(forbidden) for x in mods), f"{py}: {mods}"
    code = (
        "import sys, uie_core.domains; print(sorted(m for m in sys.modules if m.split('.')[0] == 'uie_ai'))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "[]"

    def no_network(*a, **kw):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("appel réseau pendant l'enrichissement")

    w = world
    stored = w["domains"].build_instance_domain(w["inst"].id)
    calls = w["provider"].round
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    assert w["svc"].enrich(stored).created > 0
    assert w["provider"].round == calls  # l'instance n'est jamais relue
