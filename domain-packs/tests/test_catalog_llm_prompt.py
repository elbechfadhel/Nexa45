"""Prompt LLM du domaine PRODUCT (V1c) : donnée versionnée du domain pack, pas une chaîne dans le code."""

from __future__ import annotations

import json
from pathlib import Path

import uie_domain_packs  # noqa: F401 — enregistre les packs et le prompt découverts
import yaml
from uie_connectors.odoo import OdooSchemaLoader
from uie_core.domains import DomainKey, DomainPackRegistry
from uie_domain_packs.catalog.yaml_pack import LLM_DIR

ROOT = Path(__file__).resolve().parents[2]
PROMPT_FILE = ROOT / "domain-packs" / "catalog" / LLM_DIR / "product_semantic_proposal.yaml"


def test_prompt_is_registered_from_the_pack_file():
    prompt = DomainPackRegistry.prompt(DomainKey.PRODUCT)
    raw = yaml.safe_load(PROMPT_FILE.read_text("utf-8"))
    assert (prompt.version, prompt.system) == (raw["version"], raw["system"])
    assert "NO_CONFIDENT_MATCH" in prompt.system and len(prompt.checksum) == 64
    # les dossiers llm/ et targets/ ne sont pas des packs
    assert DomainPackRegistry.available() == ["ACCOUNTING/odoo", "PARTNER/odoo", "PRODUCT/odoo"]


def test_few_shots_are_invented_never_a_measured_system():
    """Des exemples tirés d'une instance mesurée biaiseraient la mesure de précision de V1c."""
    prompt = DomainPackRegistry.prompt(DomainKey.PRODUCT)
    assert prompt.few_shots
    text = json.dumps(prompt.few_shots)
    for shot in prompt.few_shots:
        assert shot["input"]["field"]["entity"].startswith("shop."), shot
        assert shot["answer"]["outcome"] in {"MATCH", "NO_CONFIDENT_MATCH"}
        assert set(shot["answer"]) <= {"outcome", "semantic_key", "confidence", "justification", "reason"}
    for measured in ("product.", "uom.", "supplierinfo", "discount", "partner_ref", "is_used_on_products"):
        assert measured not in text, measured


def test_few_shot_keys_belong_to_the_vocabulary():
    prompt = DomainPackRegistry.prompt(DomainKey.PRODUCT)
    keys = {k.key for k in DomainPackRegistry.vocabulary(DomainKey.PRODUCT).keys}
    for shot in prompt.few_shots:
        assert set(shot["input"]["vocabulary"]) <= keys
        assert set(shot["input"].get("keys_already_used_on_this_entity", {})) <= keys
        if shot["answer"]["outcome"] == "MATCH":
            assert shot["answer"]["semantic_key"] in shot["input"]["vocabulary"]


def test_field_help_reaches_the_domain():
    pack = DomainPackRegistry.resolve(DomainKey.PRODUCT, "odoo", "17")
    resolved = pack.resolve(OdooSchemaLoader({"json": ROOT / "docs/dumps/odoo17_ref.json"}).build_snapshot())
    helps = {f.path: f.help for f in resolved.fields if f.present}
    assert sum(1 for h in helps.values() if h) > 50


# --------------------------------------------------------------------------- #
# V2c.3 : prompt des colonnes d'import
# --------------------------------------------------------------------------- #
IMPORT_PROMPT_FILE = ROOT / "domain-packs" / "catalog" / LLM_DIR / "import_column_mapping.yaml"
BENCHMARK_FILES = [
    ROOT / "captures" / "liquidweb-woocommerce-product-sample-data.csv",
    ROOT / "captures" / "Shopify Product CSV.csv",
]


def test_import_prompt_is_registered_next_to_the_field_prompt():
    from uie_core.imports import IMPORT_PROMPT

    imp = DomainPackRegistry.prompt(DomainKey.PRODUCT, IMPORT_PROMPT)
    raw = yaml.safe_load(IMPORT_PROMPT_FILE.read_text("utf-8"))
    assert (imp.version, imp.system) == (raw["version"], raw["system"])
    assert imp.checksum != DomainPackRegistry.prompt(DomainKey.PRODUCT).checksum
    assert "NOT_RELEVANT" in imp.system and "UNKNOWN" in imp.system


def test_import_few_shots_are_invented_and_use_vocabulary_keys():
    """Aucun en-tête du fichier de benchmark (sinon la mesure de V2c.3 serait biaisée)."""
    from uie_core.imports import IMPORT_PROMPT

    imp = DomainPackRegistry.prompt(DomainKey.PRODUCT, IMPORT_PROMPT)
    keys = {k.key for k in DomainPackRegistry.vocabulary(DomainKey.PRODUCT).keys}
    measured: set[str] = set()
    for f in BENCHMARK_FILES:
        if f.exists():
            measured |= {h.strip().lower() for h in f.read_text("utf-8-sig").splitlines()[0].split(",")}
    for shot in imp.few_shots:
        assert set(shot["input"]["targets"]) <= keys
        for col in shot["input"]["columns"]:
            assert col["header"].lower() not in measured, col["header"]
        for answer in shot["answer"]["columns"]:
            assert answer["decision"] in {"TARGET", "NOT_RELEVANT", "UNKNOWN"}
            if answer["decision"] == "TARGET":
                assert answer["semantic_key"] in shot["input"]["targets"]


def test_import_prompt_never_treats_a_missing_target_as_not_relevant():
    """V2c.5 : NOT_RELEVANT = donnée propre à la source ; vraie donnée produit sans cible offerte = UNKNOWN
    (sinon « Ignore all AI not relevant » l'apprendrait comme inutile)."""
    from uie_core.imports import IMPORT_PROMPT

    imp = DomainPackRegistry.prompt(DomainKey.PRODUCT, IMPORT_PROMPT)
    text = " ".join(imp.system.split())
    assert imp.version == "1.1.0"
    assert "none of the targets can hold" not in text
    assert "NEVER only because no target is offered" in text
    unknown = text.split("- UNKNOWN:", 1)[1]
    assert "real product or catalog data" in unknown and "variant option" in unknown
