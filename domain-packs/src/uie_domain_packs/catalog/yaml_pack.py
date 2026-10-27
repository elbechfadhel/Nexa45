"""Adaptateur générique : un fichier YAML du domain pack catalog → un `DomainPack` enregistré.

Aucune connaissance d'un système concret dans ce code : le domaine, le système, les entités, les groupes
et les règles médias viennent du fichier. Découverte automatique de `<domaine>/<système>/*.yaml` (`catalog`
= PRODUCT, `partner` = PARTNER) ; le fichier
`<système>/protected_fields.yaml` (liste de sûreté, pas un pack) accompagne les packs du même dossier.

Résolution contre un snapshot (standard ou instance), pour chaque entité déclarée :
- absente du snapshot → entité et champs listés rendus present=False (jamais une erreur) ;
- présente → TOUS ses champs réels sont classés :
    règle media (type natif ou motif de nom)     → group media, rôle main / derivative / variant
    champ listé                                   → son groupe, relevance « listed »
    champ technique (metadata.technical)          → group technical
    autre champ                                   → group « unlisted », relevance POSSIBLE
  les champs listés absents du snapshot sont rendus present=False.
"""

from __future__ import annotations

import os
import re
from functools import cached_property
from pathlib import Path
from typing import ClassVar

from pydantic import ValidationError
from uie_core.domains import (
    DomainEntityDef,
    DomainFieldDef,
    DomainPack,
    DomainPackError,
    DomainPackRegistry,
    EntityRole,
    FieldGroup,
    ImportDefault,
    MediaRole,
    PromptSpec,
    ProtectionSpec,
    Relevance,
    ResolvedDomain,
    SemanticSpec,
    ValueMap,
    Vocabulary,
)
from uie_core.mapping import TargetBinding, TargetBindingRegistry
from uie_core.mapping.suggestions import (
    BankAccountRegistry,
    BankAccountTable,
    CashAccountRegistry,
    CashAccountTable,
    CategoryAccountRegistry,
    CategoryAccountTable,
    VatAccountRegistry,
    VatAccountTable,
)
from uie_core.registry import EntityDef, FieldDef, RelationDef, SchemaSnapshotModel
from uie_core.sources import SourceBinding, SourceBindingRegistry
from uie_core.sources.table import FileTableRegistry, FileTableSpec

from uie_domain_packs.catalog.spec import (
    DomainPackSpec,
    MediaSpec,
    load_llm_prompt_spec,
    load_pack_spec,
    load_protected_fields_spec,
    load_vocabulary_spec,
    read_yaml,
)

VOCABULARY_FILE = "vocabulary.yaml"
PROTECTED_FILE = "protected_fields.yaml"
LLM_DIR = "llm"  # prompts LLM par domaine (V1c) : pas des packs
TARGETS_DIR = "targets"  # bindings cibles CDM → format (PARTNER) : pas des packs
SOURCES_DIR = "sources"  # bindings de source à schéma fixe → semantic keys : pas des packs
FILES_DIR = "files"  # tableau de source FICHIER du domaine (colonnes d'un fichier client) : pas des packs


def packs_root() -> Path:
    """Dossier domain-packs/ (surchargeable par UIE_DOMAIN_PACKS_DIR)."""
    env = os.environ.get("UIE_DOMAIN_PACKS_DIR")
    return Path(env) if env else Path(__file__).resolve().parents[3]


class YamlDomainPack(DomainPack):
    """Pack défini par un fichier ; sous-classé dynamiquement par fichier découvert."""

    path: ClassVar[Path]
    relative_name: ClassVar[str]

    @cached_property
    def spec(self) -> DomainPackSpec:
        return load_pack_spec(self.path)

    @property
    def name(self) -> str:
        return self.relative_name

    @property
    def definition_version(self) -> str:
        return self.spec.definition_version

    def supports(self, version: str) -> bool:
        return not self.spec.versions or version in self.spec.versions

    def scope_entities(self) -> list[str]:
        return [e.technical_name for e in self.spec.entities]

    @cached_property
    def _semantics(self) -> SemanticSpec | None:
        spec = self.spec
        if spec.semantics is None:
            return None
        vocabulary = load_vocabulary(self.path.parents[1])
        protection = self._protection()
        try:
            return SemanticSpec(
                domain_key=spec.domain,
                version=f"{spec.definition_version}+vocabulary-{vocabulary.version}",
                vocabulary=vocabulary,
                rules=spec.semantics.rules,
                field_keys={f"{f.entity}.{f.field}": f.key for f in spec.fields if f.key},
                known_threshold=spec.semantics.known_threshold,
                structure_checks=spec.semantics.structure_checks,
                protection=protection,
            )
        except ValueError as exc:
            raise DomainPackError(f"{self.path} : règles sémantiques invalides\n{exc}") from None

    def _protection(self) -> ProtectionSpec:
        """Liste de sûreté du dossier du pack (même domaine, même système), sur les entités du scope."""
        path = self.path.parent / PROTECTED_FILE
        if not path.exists():
            return ProtectionSpec()
        prot = load_protected_fields_spec(path)
        if (prot.domain, prot.system) != (self.spec.domain, self.spec.system):
            return ProtectionSpec()
        unknown = sorted(set(prot.fields) - set(self.scope_entities()))
        if unknown:
            raise DomainPackError(f"{path} : entités hors du scope du pack {self.relative_name} : {unknown}")
        return ProtectionSpec(fields=prot.paths(), origins=prot.origins, media=prot.media)

    def semantics(self) -> SemanticSpec | None:
        return self._semantics

    def value_maps(self) -> list[ValueMap]:
        """Traductions de valeurs du pack, validées contre les valeurs canoniques du vocabulaire."""
        maps = self.spec.value_maps
        if not maps:
            return []
        canonical = load_vocabulary_spec(self.path.parents[1] / VOCABULARY_FILE).values
        out = []
        for vm in maps:
            allowed = set(canonical.get(vm.key, []))
            unknown = sorted(set(vm.values.values()) - allowed)
            if unknown:
                raise DomainPackError(
                    f"{self.path} : value_map {vm.entity}.{vm.field} : valeurs inconnues {unknown}"
                )
            out.append(ValueMap(**vm.model_dump()))
        return out

    def unit_names(self) -> dict[str, str]:
        return dict(self.spec.unit_names)

    def field_units(self) -> dict[str, str]:
        return dict(self.spec.field_units)

    def import_defaults(self) -> dict[str, ImportDefault]:
        return {k: ImportDefault(**v.model_dump()) for k, v in self.spec.import_defaults.items()}

    def media_size(self, field: str) -> tuple[str, int] | None:
        """Champ média de la règle du pack : famille = nom sans suffixe numérique, taille = suffixe."""
        if not any(re.match(p, field) for p in self.spec.media.detect.name_patterns):
            return None
        m = _SIZED.match(field)
        return (m.group(1), int(m.group(2))) if m else None

    def resolve(self, snapshot: SchemaSnapshotModel) -> ResolvedDomain:
        spec = self.spec
        d = spec.field_defaults
        listed: dict[str, dict[str, FieldGroup]] = {}
        for f in spec.fields:
            listed.setdefault(f.entity, {})[f.field] = f.group

        entities: list[DomainEntityDef] = []
        fields: list[DomainFieldDef] = []
        for decl in spec.entities:
            ent = snapshot.entity(decl.technical_name)
            declared = listed.get(decl.technical_name, {})
            entities.append(
                DomainEntityDef(
                    technical_name=decl.technical_name,
                    role=decl.role,
                    present=ent is not None,
                    display_name=ent.display_name if ent else None,
                    notes=None if ent else "déclarée par le domain pack, absente de ce snapshot",
                )
            )
            real = {f.technical_name: f for f in ent.fields} if ent else {}
            media = _media_roles(spec.media, decl.role, list(real.values()))
            relations = {r.source_field: r for r in ent.relations} if ent else {}
            for name, fdef in real.items():
                if name in media:
                    group, relevance = FieldGroup.MEDIA, d.listed_field_relevance
                elif name in declared:
                    group, relevance = declared[name], d.listed_field_relevance
                elif fdef.metadata.get("technical"):
                    group, relevance = d.technical_fields_group, d.technical_fields_relevance
                else:
                    group, relevance = d.unlisted_field_group, d.unlisted_standard_field_relevance
                fields.append(
                    _field(
                        ent, fdef, group, relevance, listed=name in declared, media_role=media.get(name)
                    ).model_copy(update=_relation(relations.get(name)))
                )
            for name, group in declared.items():
                if name not in real:
                    fields.append(
                        DomainFieldDef(
                            entity_technical_name=decl.technical_name,
                            field_technical_name=name,
                            present=False,
                            group=group,
                            relevance=d.listed_field_relevance,
                            listed=True,
                        )
                    )
        return ResolvedDomain(entities=entities, fields=fields)


def _field(
    ent: EntityDef,
    f: FieldDef,
    group: FieldGroup,
    relevance: Relevance,
    *,
    listed: bool,
    media_role: MediaRole | None,
) -> DomainFieldDef:
    meta = f.metadata
    selection = meta.get("selection")
    return DomainFieldDef(
        entity_technical_name=ent.technical_name,
        field_technical_name=f.technical_name,
        group=group,
        relevance=relevance,
        listed=listed,
        is_media=media_role is not None,
        media_role=media_role,
        label=f.label,
        help=f.help,
        data_type=f.data_type.value,
        native_type=f.native_type,
        required=f.required,
        readonly=f.readonly,
        stored=f.stored,
        computed=f.computed,
        technical=bool(meta.get("technical")),
        related=meta.get("related") or None,
        selection=list(selection) if isinstance(selection, list) else None,
        modules=list(meta.get("modules") or []),
    )


def _relation(rel: RelationDef | None) -> dict[str, str | None]:
    """Cible, inverse (one2many) et table (many2many) d'une relation du snapshot."""
    if rel is None:
        return {"relation_target": None}
    table = rel.metadata.get("relation_table")
    return {
        "relation_target": rel.target_entity,
        "inverse_field": rel.inverse_field,
        "relation_table": table if isinstance(table, str) else None,
    }


_RESOLUTION = re.compile(r"(\d+)$")
_SIZED = re.compile(r"^(.*?)(\d+)$")


def _media_roles(rule: MediaSpec, role: EntityRole, fields: list[FieldDef]) -> dict[str, MediaRole]:
    """Champs médias prouvés par le schéma. Entité de niveau variante : tout = variant ; sinon la plus
    haute résolution = main, les autres tailles = derivative."""
    patterns = [re.compile(p) for p in rule.detect.name_patterns]

    def is_media(f: FieldDef) -> bool:
        return (f.native_type or "") in rule.detect.native_types or any(
            p.match(f.technical_name) for p in patterns
        )

    found = [f for f in fields if is_media(f)]
    if not found:
        return {}
    if role in rule.variant_level_roles:
        return {f.technical_name: MediaRole.VARIANT for f in found}

    def resolution(f: FieldDef) -> int:
        m = _RESOLUTION.search(f.technical_name)
        return int(m.group(1)) if m else 0

    ranked = sorted(found, key=lambda f: (-resolution(f), f.technical_name))
    return {
        f.technical_name: (MediaRole.MAIN if i == 0 else MediaRole.DERIVATIVE) for i, f in enumerate(ranked)
    }


def load_target_binding(path: Path) -> TargetBinding:
    """Binding cible CDM → format (`<domaine>/targets/*.yaml`), validé au chargement. La confrontation au
    Registry (positions existantes) se fait à la résolution, quand le snapshot du format est disponible."""
    try:
        return TargetBinding.model_validate(read_yaml(path))
    except ValidationError as exc:
        raise DomainPackError(f"{path} : binding cible invalide\n{exc}") from None


def load_file_table(path: Path) -> FileTableSpec:
    """Tableau de source fichier d'un domaine (`<domaine>/files/*.yaml`), validé au chargement."""
    try:
        return FileTableSpec.model_validate(read_yaml(path))
    except ValidationError as exc:
        raise DomainPackError(f"{path} : tableau de source fichier invalide\n{exc}") from None


def load_category_accounts(path: Path) -> CategoryAccountTable:
    """Table de comptes d'usage par catégorie (`<domaine>/<système>/*.yaml`, `kind: category_accounts`) :
    présélections à valider, jamais un domain pack."""
    try:
        return CategoryAccountTable.model_validate(read_yaml(path))
    except ValidationError as exc:
        raise DomainPackError(f"{path} : table de comptes par catégorie invalide\n{exc}") from None


def load_vat_accounts(path: Path) -> VatAccountTable:
    """Table des comptes d'usage des opérations avec l'étranger (`kind: vat_accounts`) : présélections à
    valider par le conseiller fiscal, jamais un domain pack."""
    try:
        return VatAccountTable.model_validate(read_yaml(path))
    except ValidationError as exc:
        raise DomainPackError(f"{path} : table des comptes de TVA invalide\n{exc}") from None


def load_cash_accounts(path: Path) -> CashAccountTable:
    """Table des comptes d'usage d'un livre de caisse (`kind: cash_accounts`) : présélections à valider par
    le conseiller fiscal, jamais un domain pack."""
    try:
        return CashAccountTable.model_validate(read_yaml(path))
    except ValidationError as exc:
        raise DomainPackError(f"{path} : table des comptes de caisse invalide\n{exc}") from None


def load_bank_accounts(path: Path) -> BankAccountTable:
    """Table des comptes d'usage d'un relevé bancaire (`kind: bank_accounts`) : présélections à valider."""
    try:
        return BankAccountTable.model_validate(read_yaml(path))
    except ValidationError as exc:
        raise DomainPackError(f"{path} : table des comptes bancaires invalide\n{exc}") from None


def load_source_binding(path: Path) -> SourceBinding:
    """Binding de source à schéma fixe (`<domaine>/sources/*.yaml`), validé au chargement. La confrontation au
    contrat du Registry se fait à la lecture, quand le snapshot du système est disponible."""
    try:
        return SourceBinding.model_validate(read_yaml(path))
    except ValidationError as exc:
        raise DomainPackError(f"{path} : binding de source invalide\n{exc}") from None


def load_prompt(path: Path) -> PromptSpec:
    """Prompt LLM du domain pack au format du Core (son checksum entre dans la provenance)."""
    spec = load_llm_prompt_spec(path)
    return PromptSpec(version=spec.version, system=spec.system, few_shots=spec.few_shots)


def load_vocabulary(catalog_dir: Path | None = None) -> Vocabulary:
    """Vocabulaire contrôlé du domaine (catalog/vocabulary.yaml) au format du Core."""
    spec = load_vocabulary_spec((catalog_dir or packs_root() / "catalog") / VOCABULARY_FILE)
    return Vocabulary(domain_key=spec.domain, version=spec.vocabulary_version, keys=spec.keys)


def discover_catalog_packs(root: Path | None = None) -> list[type[YamlDomainPack]]:
    """Domaine PRODUCT : `catalog/`."""
    return discover_domain_packs("catalog", root)


def discover_domain_packs(domain_dir: str, root: Path | None = None) -> list[type[YamlDomainPack]]:
    """Enregistre un DomainPack par fichier <domaine>/<système>/*.yaml (hors vocabulaire, prompts LLM et
    bindings cibles), et le vocabulaire du domaine. Les règles sémantiques sont validées dès la découverte."""
    base = (root or packs_root()) / domain_dir
    registered: list[type[YamlDomainPack]] = []
    if (base / VOCABULARY_FILE).exists():
        vocabulary = load_vocabulary(base)
        DomainPackRegistry.register_vocabulary(vocabulary.domain_key, lambda: load_vocabulary(base))
    for path in sorted((base / TARGETS_DIR).glob("*.yaml")):
        TargetBindingRegistry.register(load_target_binding(path))  # invalide → erreur au démarrage
    for path in sorted((base / SOURCES_DIR).glob("*.yaml")):
        SourceBindingRegistry.register(load_source_binding(path))  # invalide → erreur au démarrage
    for path in sorted((base / FILES_DIR).glob("*.yaml")):
        FileTableRegistry.register(load_file_table(path))  # invalide → erreur au démarrage
    for path in sorted((base / LLM_DIR).glob("*.yaml")):
        prompt = load_llm_prompt_spec(path)  # fichier invalide → erreur au démarrage
        DomainPackRegistry.register_prompt(prompt.domain, lambda p=path: load_prompt(p), prompt.purpose)
    for path in sorted(base.glob("*/*.yaml")):
        if path.name == PROTECTED_FILE or path.parent.name in (LLM_DIR, TARGETS_DIR, SOURCES_DIR, FILES_DIR):
            continue
        raw = read_yaml(path)
        if isinstance(raw, dict) and raw.get("kind") == "category_accounts":
            CategoryAccountRegistry.register(load_category_accounts(path))  # invalide → erreur au démarrage
            continue
        if isinstance(raw, dict) and raw.get("kind") == "vat_accounts":
            VatAccountRegistry.register(load_vat_accounts(path))  # invalide → erreur au démarrage
            continue
        if isinstance(raw, dict) and raw.get("kind") == "cash_accounts":
            CashAccountRegistry.register(load_cash_accounts(path))  # invalide → erreur au démarrage
            continue
        if isinstance(raw, dict) and raw.get("kind") == "bank_accounts":
            BankAccountRegistry.register(load_bank_accounts(path))  # invalide → erreur au démarrage
            continue
        spec = load_pack_spec(path)
        rel = path.relative_to(base.parent).as_posix()
        cls = type(
            f"YamlDomainPack_{spec.domain.value}_{spec.system}",
            (YamlDomainPack,),
            {"domain_key": spec.domain, "system_code": spec.system, "path": path, "relative_name": rel},
        )
        cls().semantics()  # clés du pack inconnues du vocabulaire → erreur au démarrage, pas à l'analyse
        cls().value_maps()  # valeurs canoniques inconnues → erreur au démarrage
        DomainPackRegistry.register(cls)
        registered.append(cls)
    return registered
