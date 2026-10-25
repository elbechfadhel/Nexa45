"""Domain pack catalog (domaine PRODUCT) : vocabulaire contrôlé + définitions par système (YAML)."""

from __future__ import annotations

from uie_domain_packs.catalog.spec import (
    DomainPackSpec,
    SemanticsSpec,
    VocabularyKey,
    VocabularySpec,
    load_pack_spec,
    load_vocabulary_spec,
)
from uie_domain_packs.catalog.yaml_pack import (
    VOCABULARY_FILE,
    YamlDomainPack,
    discover_catalog_packs,
    discover_domain_packs,
    load_vocabulary,
    packs_root,
)

__all__ = [
    "VOCABULARY_FILE",
    "DomainPackSpec",
    "SemanticsSpec",
    "VocabularyKey",
    "VocabularySpec",
    "YamlDomainPack",
    "discover_catalog_packs",
    "discover_domain_packs",
    "load_pack_spec",
    "load_vocabulary",
    "load_vocabulary_spec",
    "packs_root",
]
