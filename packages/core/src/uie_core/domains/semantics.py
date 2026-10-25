"""Semantic Enrichment par règles (Product Domain V1b) : contrats et moteur DÉTERMINISTE.

Aucun LLM, aucun appel réseau : uniquement les métadonnées du domaine effectif, le vocabulaire contrôlé
et des règles ordonnées. Tout ce qui est propre à un système (alias, modules hors catalogue, motifs de noms)
est de la DONNÉE fournie par le domain pack (`DomainPack.semantics()`) ; ce module est générique.

    Registry            → WHAT EXISTS?
    Product Domain      → WHAT BELONGS TO THE CATALOG?
    Semantic Enrichment → WHAT DOES IT MEAN?          ← ici (règles + décisions humaines)

Pour chaque champ présent du domaine :
1. proposition d'une semantic_key (au plus une) : clé déclarée par le pack (0.98), alias exact (0.95),
   motif (0.90), libellé du vocabulaire (0.85), recouvrement de tokens (0.60-0.80) ; un type incompatible
   ou une relation vers une entité du mauvais rôle annule la proposition ;
2. pertinence : règles du pack évaluées dans l'ordre, la première qui correspond gagne (rule_id tracé) ;
   un champ PROTÉGÉ (liste de sûreté du pack) n'est jamais exclu par une règle : seul un humain l'ignore ;
3. statut : KNOWN / PROPOSED / UNRESOLVED / EXCLUDED, puis unicité des clés `single` par entité et
   rattachement des copies `related` d'un champ du domaine (mirror).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from functools import cached_property
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from uie_core.domains.model import (
    DomainEntityDef,
    DomainFieldDef,
    DomainKey,
    EntityRole,
    FieldGroup,
    FieldOrigin,
    Relevance,
)
from uie_core.registry.schema import DataType

OTHER_KEY = "other"  # réponse humaine « autre / je ne sais pas » : jamais une semantic_key
MIRROR_RULE = "MIRROR"


class MeaningStatus(StrEnum):
    KNOWN = "KNOWN"  # proposition sûre sur un champ standard : aucune question
    PROPOSED = "PROPOSED"  # proposition à confirmer (confiance moyenne, extension, conflit)
    UNRESOLVED = "UNRESOLVED"  # aucune proposition
    EXCLUDED = "EXCLUDED"  # hors catalogue par une règle (NOT_RELEVANT), contestable (include)
    CONFIRMED = "CONFIRMED"  # décision humaine
    REJECTED = "REJECTED"  # réservé (non produit en V1b)
    IGNORED = "IGNORED"  # exclu par le supplier
    STALE = "STALE"  # décision humaine dont la signature technique a changé : question reposée
    ORPHANED = "ORPHANED"  # champ disparu de l'instance : décision conservée, invisible


HUMAN_STATUSES = frozenset({MeaningStatus.CONFIRMED, MeaningStatus.REJECTED, MeaningStatus.IGNORED})


class DecisionSource(StrEnum):
    STANDARD = "STANDARD"  # connaissance déclarée par le domain pack (champ listé, clé du pack)
    RULE = "RULE"  # règle générique (alias, motif, libellé, tokens, règle de pertinence)
    LLM = "LLM"  # proposition d'un LLM (V1c) : toujours PROPOSED, jamais KNOWN ni une exclusion
    HUMAN = "HUMAN"  # réponse du supplier : jamais écrasée par une règle


class MatchKind(StrEnum):
    PACK = "pack"
    ALIAS = "alias"
    PATTERN = "pattern"
    LABEL = "label"
    TOKENS = "tokens"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, json_schema_serialization_defaults_required=True)


# --------------------------------------------------------------------------- #
# Vocabulaire contrôlé
# --------------------------------------------------------------------------- #
class VocabularyEntry(_Model):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    group: FieldGroup
    label: str
    description: str
    data_type: DataType
    cardinality: Literal["single", "multi"]
    required_for_catalog: bool
    aliases: list[str] = []  # noms techniques / libellés connus (casse et séparateurs normalisés)
    patterns: list[str] = []  # regex sur le nom technique
    expected_types: list[DataType] = []  # vide = data_type seul
    relation_target_roles: list[EntityRole] = []  # références : rôle attendu de l'entité cible
    entity_roles: list[EntityRole] = []  # rôles des entités où la clé a un sens (vide = toutes)

    @field_validator("patterns")
    @classmethod
    def _regex(cls, value: list[str]) -> list[str]:
        for p in value:
            re.compile(p)
        return value

    @field_validator("key")
    @classmethod
    def _not_other(cls, value: str) -> str:
        if value == OTHER_KEY:
            raise ValueError(f"'{OTHER_KEY}' est réservé (réponse « autre »)")
        return value

    @property
    def types(self) -> list[DataType]:
        return self.expected_types or [self.data_type]


class Vocabulary(_Model):
    domain_key: DomainKey
    version: str
    keys: list[VocabularyEntry]

    @model_validator(mode="after")
    def _unique(self) -> Vocabulary:
        names = [k.key for k in self.keys]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"semantic_key en double : {dupes}")
        return self

    def get(self, key: str) -> VocabularyEntry | None:
        return next((k for k in self.keys if k.key == key), None)


# --------------------------------------------------------------------------- #
# Règles de pertinence (données du pack)
# --------------------------------------------------------------------------- #
class RuleCondition(_Model):
    """Conditions combinées par ET ; une condition absente (None / liste vide) ne filtre pas."""

    technical: bool | None = None
    listed: bool | None = None
    is_media: bool | None = None
    group_in: list[FieldGroup] = []
    group_not_in: list[FieldGroup] = []
    origins: list[FieldOrigin] = []
    native_types: list[str] = []
    data_types: list[DataType] = []
    stored: bool | None = None
    computed: bool | None = None
    name_patterns: list[str] = []  # au moins un motif doit correspondre au nom technique
    modules_all_in: list[str] = []  # le champ a des modules et TOUS sont dans la liste
    relation_outside_scope: bool | None = None  # relation vers une entité sans rôle dans le domaine
    # collection vers une entité du domaine dont la relation est portée par l'autre côté : one2many dont
    # l'inverse est un lien vers l'entité courante, ou many2many dont la table est celle d'un champ de la
    # cible pointant vers l'entité courante
    back_reference: bool | None = None
    related_copy: bool | None = (
        None  # copie related (« lien.champ ») d'un champ d'une autre entité du domaine
    )
    entity_roles: list[EntityRole] = []  # rôle de l'entité du champ dans le domaine
    min_match_confidence: float | None = None  # proposition de semantic_key au moins aussi sûre
    no_match: bool | None = None  # aucune proposition de semantic_key
    any_of: list[RuleCondition] = []  # au moins une de ces conditions (OU)

    @field_validator("name_patterns")
    @classmethod
    def _regex(cls, value: list[str]) -> list[str]:
        for p in value:
            re.compile(p)
        return value


class RelevanceRule(_Model):
    """Une règle décide une pertinence, OU (follow_source) fait suivre à une copie related la décision
    finale de son champ d'origine (mêmes données : jamais deux décisions indépendantes)."""

    id: str
    description: str = ""
    when: RuleCondition = RuleCondition()
    relevance: Relevance | None = None
    follow_source: bool = False
    # raison lisible ; variables : {field}, {entity}, {target}, {modules}, {key}, {inverse}, {source}
    reason: str

    @field_validator("relevance")
    @classmethod
    def _decided(cls, value: Relevance | None) -> Relevance | None:
        if value == Relevance.UNRESOLVED:
            raise ValueError("une règle décide : RELEVANT, POSSIBLE ou NOT_RELEVANT")
        return value

    @model_validator(mode="after")
    def _one_outcome(self) -> RelevanceRule:
        if self.follow_source == (self.relevance is not None):
            raise ValueError(f"règle {self.id} : relevance OU follow_source (exactement un des deux)")
        return self


class ProtectionSpec(_Model):
    """Liste de sûreté du pack : champs du catalogue par nature, jamais NOT_RELEVANT par une règle.

    Ce n'est pas une règle (aucun effet sur la pertinence positive) : une règle d'exclusion qui correspond
    à un champ protégé est simplement sautée, la suivante décide. Seul un humain peut l'ignorer."""

    fields: list[str] = []  # « entité.champ »
    origins: list[FieldOrigin] = []  # ex. CUSTOM : toute extension custom est protégée
    media: bool = False  # tout champ média détecté

    def covers(self, f: DomainFieldDef) -> bool:
        return f.path in self.fields or f.origin in self.origins or (self.media and f.is_media)


class SemanticSpec(_Model):
    """Tout ce que le moteur sait d'un domaine pour un système : vocabulaire, règles, clés du pack."""

    domain_key: DomainKey
    version: str  # version du pack + version du vocabulaire (provenance des décisions)
    vocabulary: Vocabulary
    rules: list[RelevanceRule]
    field_keys: dict[str, str] = {}  # « entité.champ » → semantic_key déclarée par le pack
    known_threshold: float = 0.90
    structure_checks: list[EntityRole] = []
    protection: ProtectionSpec = ProtectionSpec()

    @model_validator(mode="after")
    def _consistency(self) -> SemanticSpec:
        ids = [r.id for r in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("rule_id en double")
        unknown = sorted({k for k in self.field_keys.values() if self.vocabulary.get(k) is None})
        if unknown:
            raise ValueError(f"clés du pack absentes du vocabulaire : {unknown}")
        return self

    @cached_property
    def checksum(self) -> str:
        raw = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Résultat du moteur
# --------------------------------------------------------------------------- #
class KeyProposal(_Model):
    semantic_key: str
    confidence: float
    match: MatchKind


class FieldDecision(_Model):
    entity_technical_name: str
    field_technical_name: str
    relevance: Relevance
    rule_id: str
    reason: str
    semantic_key: str | None = None
    confidence: float | None = None
    match: MatchKind | None = None
    status: MeaningStatus
    source: DecisionSource
    pending: bool = False  # question ouverte pour le supplier
    mirror_of: str | None = None  # copie related d'un champ du domaine : suit sa décision

    @property
    def path(self) -> str:
        return f"{self.entity_technical_name}.{self.field_technical_name}"


# --------------------------------------------------------------------------- #
# Proposition de semantic_key
# --------------------------------------------------------------------------- #
_SEP = re.compile(r"[\s_\-./]+")
_STOP = frozenset({"the", "of", "a", "an", "id", "ids", "on", "for", "to", "and"})


def normalize(text: str) -> str:
    return _SEP.sub(" ", text.strip().lower()).strip()


def tokens(text: str) -> frozenset[str]:
    return frozenset(t for t in normalize(text).split() if len(t) > 1 and t not in _STOP)


def _overlap(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / max(len(a), len(b))


class _Matcher:
    def __init__(self, spec: SemanticSpec, roles: dict[str, EntityRole]) -> None:
        self.spec = spec
        self.roles = roles  # entité du domaine (présente) → rôle
        self.aliases = {k.key: {normalize(a) for a in [k.key, *k.aliases]} for k in spec.vocabulary.keys}
        self.patterns = {k.key: [re.compile(p) for p in k.patterns] for k in spec.vocabulary.keys}
        self.token_sets = {
            k.key: [tokens(x) for x in [k.key, k.label, *k.aliases]] for k in spec.vocabulary.keys
        }

    def compatible(self, entry: VocabularyEntry, f: DomainFieldDef, *, declared: bool = False) -> bool:
        """Type compatible, cible de relation du bon rôle ; entité du bon rôle sauf clé déclarée par le
        pack (son auteur sait où la clé a un sens : ex. système sans entité fournisseur dédiée)."""
        if (f.data_type or DataType.UNKNOWN.value) not in {t.value for t in entry.types}:
            return False
        roles = entry.entity_roles
        if roles and not declared and self.roles.get(f.entity_technical_name) not in roles:
            return False
        if entry.relation_target_roles:
            role = self.roles.get(f.relation_target or "")
            return role in entry.relation_target_roles
        return True

    def score(self, entry: VocabularyEntry, name: str, label: str | None) -> tuple[float, MatchKind] | None:
        """Correspondance d'un nom (et d'un libellé) avec une clé : alias, motif, libellé, tokens."""
        n, lab = normalize(name), normalize(label or "")
        if n in self.aliases[entry.key] or (lab and lab in self.aliases[entry.key]):
            return (0.95, MatchKind.ALIAS)
        if any(p.search(name) for p in self.patterns[entry.key]):
            return (0.90, MatchKind.PATTERN)
        if lab and lab == normalize(entry.label):
            return (0.85, MatchKind.LABEL)
        name_tokens, label_tokens = tokens(name), tokens(label or "")
        ov = max(
            (max(_overlap(name_tokens, t), _overlap(label_tokens, t)) for t in self.token_sets[entry.key]),
            default=0.0,
        )
        # recouvrement partiel : 0.6 → 0.60, 1.0 (mêmes tokens, ordre différent) → 0.80
        return (round(0.60 + 0.20 * (ov - 0.6) / 0.4, 2), MatchKind.TOKENS) if ov >= 0.6 else None

    def propose(self, f: DomainFieldDef) -> KeyProposal | None:
        best: KeyProposal | None = None
        pack_key = self.spec.field_keys.get(f.path)
        for entry in self.spec.vocabulary.keys:
            if not self.compatible(entry, f, declared=entry.key == pack_key):
                continue
            if entry.key == pack_key:
                found: tuple[float, MatchKind] | None = (0.98, MatchKind.PACK)
            else:
                found = self.score(entry, f.field_technical_name, f.label)
            if found and (best is None or found[0] > best.confidence):
                best = KeyProposal(semantic_key=entry.key, confidence=found[0], match=found[1])
        return best


# --------------------------------------------------------------------------- #
# Pertinence
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class _Context:
    """Ce que le moteur sait d'un champ au-delà de ses propres métadonnées (calculé par classify)."""

    proposal: KeyProposal | None
    outside: bool  # relation vers une entité sans rôle dans le domaine
    role: EntityRole | None  # rôle de l'entité du champ
    back_ref: str | None = None  # « cible.champ » qui porte la relation (back_reference)
    source: str | None = None  # champ d'origine d'une copie related (related_copy)


def _matches(c: RuleCondition, f: DomainFieldDef, ctx: _Context) -> bool:
    checks: list[bool] = []
    if c.technical is not None:
        checks.append(f.technical == c.technical)
    if c.listed is not None:
        checks.append(f.listed == c.listed)
    if c.is_media is not None:
        checks.append(f.is_media == c.is_media)
    if c.group_in:
        checks.append(f.group in c.group_in)
    if c.group_not_in:
        checks.append(f.group not in c.group_not_in)
    if c.origins:
        checks.append(f.origin in c.origins)
    if c.native_types:
        checks.append((f.native_type or "") in c.native_types)
    if c.data_types:
        checks.append((f.data_type or "") in {t.value for t in c.data_types})
    if c.stored is not None:
        checks.append(f.stored == c.stored)
    if c.computed is not None:
        checks.append(f.computed == c.computed)
    if c.name_patterns:
        checks.append(any(re.search(p, f.field_technical_name) for p in c.name_patterns))
    if c.modules_all_in:
        checks.append(bool(f.modules) and set(f.modules) <= set(c.modules_all_in))
    if c.relation_outside_scope is not None:
        checks.append(ctx.outside == c.relation_outside_scope)
    if c.back_reference is not None:
        checks.append((ctx.back_ref is not None) == c.back_reference)
    if c.related_copy is not None:
        checks.append((ctx.source is not None) == c.related_copy)
    if c.entity_roles:
        checks.append(ctx.role in c.entity_roles)
    if c.min_match_confidence is not None:
        checks.append(ctx.proposal is not None and ctx.proposal.confidence >= c.min_match_confidence)
    if c.no_match is not None:
        checks.append((ctx.proposal is None) == c.no_match)
    if c.any_of:
        checks.append(any(_matches(sub, f, ctx) for sub in c.any_of))
    return all(checks)


def _reason(rule: RelevanceRule, f: DomainFieldDef, ctx: _Context) -> str:
    values = {
        "field": f.path,
        "entity": f.entity_technical_name,
        "target": f.relation_target or "",
        "modules": ", ".join(f.modules),
        "key": ctx.proposal.semantic_key if ctx.proposal else "",
        "inverse": ctx.back_ref or "",
        "source": ctx.source or "",
    }
    return rule.reason.format_map(values)


# --------------------------------------------------------------------------- #
# Moteur
# --------------------------------------------------------------------------- #
def classify(
    fields: list[DomainFieldDef], entities: list[DomainEntityDef], spec: SemanticSpec
) -> list[FieldDecision]:
    """Décisions de règle pour les champs PRÉSENTS du domaine (déterministe, sans état)."""
    roles = {e.technical_name: e.role for e in entities if e.present}
    matcher = _Matcher(spec, roles)
    present = [f for f in fields if f.present]
    copies = related_copies(present, roles)
    back_refs = back_references(present, roles)
    decisions: dict[str, FieldDecision] = {}
    for f in present:
        ctx = _Context(
            proposal=matcher.propose(f),
            outside=bool(f.relation_target) and f.relation_target not in roles,
            role=roles.get(f.entity_technical_name),
            back_ref=back_refs.get(f.path),
            source=copies.get(f.path),
        )
        decisions[f.path] = decide(f, spec, ctx)
    _enforce_unicity(decisions, present, spec, copies)
    _attach_mirrors(decisions, present, copies)
    return [decisions[f.path] for f in present]


def related_copies(fields: list[DomainFieldDef], roles: dict[str, EntityRole]) -> dict[str, str]:
    """Champs qui ne sont qu'une copie `related` (« lien.champ ») d'un champ d'une AUTRE entité du
    domaine (ex. champ hérité par délégation) → chemin du champ d'origine."""
    by_path = {f.path: f for f in fields}
    out: dict[str, str] = {}
    for f in fields:
        if not f.related or f.related.count(".") != 1:
            continue
        link, target_field = f.related.split(".")
        link_def = by_path.get(f"{f.entity_technical_name}.{link}")
        if link_def is None or link_def.relation_target not in roles:
            continue
        source = f"{link_def.relation_target}.{target_field}"
        if source != f.path and source in by_path:
            out[f.path] = source
    return out


def back_references(fields: list[DomainFieldDef], roles: dict[str, EntityRole]) -> dict[str, str]:
    """Collections vers une entité du domaine dont la relation est portée par l'AUTRE côté → champ qui la
    porte : one2many dont l'inverse est un lien de la cible vers l'entité courante ; many2many dont la
    table de relation est celle d'un autre champ de la cible pointant vers l'entité courante."""
    by_path = {f.path: f for f in fields}
    by_table: dict[tuple[str, str], list[DomainFieldDef]] = {}
    for f in fields:
        if f.relation_table:
            by_table.setdefault((f.entity_technical_name, f.relation_table), []).append(f)
    out: dict[str, str] = {}
    for f in fields:
        target = f.relation_target
        if f.data_type != DataType.COLLECTION.value or target not in roles:
            continue
        if f.inverse_field:
            carriers = [by_path.get(f"{target}.{f.inverse_field}")]
        else:
            carriers = list(by_table.get((target, f.relation_table or ""), []))
        for g in carriers:
            if g is not None and g.path != f.path and g.relation_target == f.entity_technical_name:
                out[f.path] = g.path
                break
    return out


def propose_key(f: DomainFieldDef, entities: list[DomainEntityDef], spec: SemanticSpec) -> KeyProposal | None:
    """Proposition seule (contestation d'une exclusion : la pertinence est alors décidée par l'humain)."""
    return _Matcher(spec, {e.technical_name: e.role for e in entities if e.present}).propose(f)


def propose_for_name(
    name: str, label: str | None, spec: SemanticSpec, keys: list[str] | None = None
) -> KeyProposal | None:
    """Même correspondance que pour un champ (alias, motif, libellé, tokens), sur un nom SANS métadonnées de
    schéma (ex. en-tête de colonne d'un fichier) : aucune clé du pack, la compatibilité de type est
    décidée par l'appelant. `keys` limite (et ordonne, à confiance égale) les clés candidates."""
    matcher = _Matcher(spec, {})
    entries = [e for k in keys if (e := spec.vocabulary.get(k))] if keys is not None else spec.vocabulary.keys
    best: KeyProposal | None = None
    for entry in entries:
        found = matcher.score(entry, name, label)
        if found and (best is None or found[0] > best.confidence):
            best = KeyProposal(semantic_key=entry.key, confidence=found[0], match=found[1])
    return best


def compatible_keys(
    f: DomainFieldDef, entities: list[DomainEntityDef], spec: SemanticSpec
) -> list[VocabularyEntry]:
    """Clés du vocabulaire proposables pour ce champ (type, rôle de l'entité et de la cible compatibles) :
    le seul choix offert au LLM (V1c)."""
    matcher = _Matcher(spec, {e.technical_name: e.role for e in entities if e.present})
    return [entry for entry in spec.vocabulary.keys if matcher.compatible(entry, f)]


def _applies(rule: RelevanceRule, f: DomainFieldDef, ctx: _Context, protected: bool) -> bool:
    # un champ protégé n'est ni exclu par une règle, ni rattaché à la décision d'un autre champ
    if protected and (rule.relevance == Relevance.NOT_RELEVANT or rule.follow_source):
        return False
    if rule.follow_source and ctx.source is None:
        return False
    return _matches(rule.when, f, ctx)


def decide(f: DomainFieldDef, spec: SemanticSpec, ctx: _Context) -> FieldDecision:
    proposal = ctx.proposal
    protected = spec.protection.covers(f)
    rule = next((r for r in spec.rules if _applies(r, f, ctx, protected)), None)
    if rule is not None and rule.follow_source:
        # provisoire : _attach_mirrors lui donne la décision finale de son champ d'origine
        return FieldDecision(
            entity_technical_name=f.entity_technical_name,
            field_technical_name=f.field_technical_name,
            relevance=Relevance.POSSIBLE,
            rule_id=rule.id,
            reason=_reason(rule, f, ctx),
            status=MeaningStatus.UNRESOLVED,
            source=DecisionSource.RULE,
            mirror_of=ctx.source,
        )
    if rule is None:  # le pack doit finir par une règle par défaut ; sinon on ne devine rien
        relevance, rule_id, reason = Relevance.POSSIBLE, "DEFAULT", "No rule applies"
    else:
        relevance, rule_id, reason = rule.relevance or Relevance.POSSIBLE, rule.id, _reason(rule, f, ctx)

    listed_source = DecisionSource.STANDARD if f.listed else DecisionSource.RULE
    if relevance == Relevance.NOT_RELEVANT:
        return FieldDecision(
            entity_technical_name=f.entity_technical_name,
            field_technical_name=f.field_technical_name,
            relevance=relevance,
            rule_id=rule_id,
            reason=reason,
            status=MeaningStatus.EXCLUDED,
            source=DecisionSource.RULE,
        )
    if proposal is not None:
        known = f.origin == FieldOrigin.STANDARD and proposal.confidence >= spec.known_threshold
        status = MeaningStatus.KNOWN if known else MeaningStatus.PROPOSED
        source = DecisionSource.STANDARD if proposal.match == MatchKind.PACK else DecisionSource.RULE
    elif relevance == Relevance.RELEVANT:
        # champ du catalogue reconnu par le pack (listé, média) sans clé du vocabulaire : pas de question
        status, source = MeaningStatus.KNOWN, listed_source
    else:
        status, source = MeaningStatus.UNRESOLVED, DecisionSource.RULE
    pending = status == MeaningStatus.PROPOSED or (
        status == MeaningStatus.UNRESOLVED and f.origin != FieldOrigin.STANDARD
    )
    return FieldDecision(
        entity_technical_name=f.entity_technical_name,
        field_technical_name=f.field_technical_name,
        relevance=relevance,
        rule_id=rule_id,
        reason=reason,
        semantic_key=proposal.semantic_key if proposal else None,
        confidence=proposal.confidence if proposal else None,
        match=proposal.match if proposal else None,
        status=status,
        source=source,
        pending=pending,
    )


def _enforce_unicity(
    decisions: dict[str, FieldDecision],
    fields: list[DomainFieldDef],
    spec: SemanticSpec,
    copies: dict[str, str],
) -> None:
    """Clés `single` : au plus un champ par entité.

    - deux champs KNOWN sur la même clé : le plus sûr garde KNOWN, les autres passent en PROPOSED (question)
      avec la raison du conflit ;
    - une proposition faible (sous le seuil KNOWN) sur une clé déjà tenue avec certitude dans l'entité est
      abandonnée : la clé est prise, ce n'est pas une ambiguïté à poser au supplier.
    Une copie related d'un autre champ du domaine ne compte pas (même donnée que son champ d'origine).
    """
    by_path = {f.path: f for f in fields}
    groups: dict[tuple[str, str], list[FieldDecision]] = {}
    for d in decisions.values():
        entry = spec.vocabulary.get(d.semantic_key) if d.semantic_key else None
        if entry is None or entry.cardinality != "single" or d.path in copies:
            continue
        groups.setdefault((d.entity_technical_name, entry.key), []).append(d)
    for items in groups.values():
        known = [d for d in items if d.status == MeaningStatus.KNOWN]
        if not known:
            continue
        ranked = sorted(
            known, key=lambda d: (-(d.confidence or 0), not by_path[d.path].listed, d.field_technical_name)
        )
        winner = ranked[0]
        for d in ranked[1:]:
            decisions[d.path] = d.model_copy(
                update={
                    "status": MeaningStatus.PROPOSED,
                    "pending": True,
                    "reason": f"Conflicts with {winner.path} ({winner.semantic_key})",
                }
            )
        for d in items:
            if d.status == MeaningStatus.KNOWN or (d.confidence or 0) >= spec.known_threshold:
                continue
            decisions[d.path] = _without_key(d, by_path[d.path])


def _attach_mirrors(
    decisions: dict[str, FieldDecision], fields: list[DomainFieldDef], copies: dict[str, str]
) -> None:
    """Une copie related d'un champ d'une autre entité du domaine suit la décision FINALE de ce champ :
    une seule décision pour une seule donnée. Toujours pour une extension (rule MIRROR) ; pour un champ
    standard, seulement si une règle follow_source l'a désigné (son rule_id et sa raison sont gardés).
    Les chaînes (copie d'une copie) sont résolues depuis leur origine."""
    by_path = {f.path: f for f in fields}
    followers = {
        path: source
        for path, source in copies.items()
        if path in decisions
        and source in decisions
        and (by_path[path].origin != FieldOrigin.STANDARD or decisions[path].mirror_of is not None)
    }
    done: set[str] = set()

    def resolve(path: str, stack: frozenset[str]) -> None:
        if path in done or path not in followers or path in stack:
            return
        source = followers[path]
        resolve(source, stack | {path})
        own = decisions[path]
        if own.mirror_of is not None:  # désigné par une règle follow_source
            decisions[path] = mirror(own, decisions[source], rule_id=own.rule_id, reason=own.reason)
        else:
            decisions[path] = mirror(own, decisions[source])
        done.add(path)

    for path in followers:
        resolve(path, frozenset())


def _without_key(d: FieldDecision, f: DomainFieldDef) -> FieldDecision:
    """Proposition retirée : le champ reste avec sa pertinence, sans clé."""
    if d.relevance == Relevance.RELEVANT:
        status = MeaningStatus.KNOWN
    else:
        status = MeaningStatus.UNRESOLVED
    return d.model_copy(
        update={
            "semantic_key": None,
            "confidence": None,
            "match": None,
            "status": status,
            "pending": status == MeaningStatus.UNRESOLVED and f.origin != FieldOrigin.STANDARD,
        }
    )


def mirror(
    own: FieldDecision, source: FieldDecision, *, rule_id: str = MIRROR_RULE, reason: str | None = None
) -> FieldDecision:
    return own.model_copy(
        update={
            "relevance": source.relevance,
            "rule_id": rule_id,
            "reason": reason or f"Same data as {source.path} (related field): follows its decision",
            "semantic_key": source.semantic_key,
            "confidence": source.confidence,
            "match": source.match,
            "status": source.status,
            "source": DecisionSource.RULE,
            "pending": False,
            "mirror_of": source.path,
        }
    )


def technical_signature(f: DomainFieldDef) -> dict[str, Any]:
    """Ce qui, s'il change, rend une décision humaine caduque (question reposée)."""
    return {
        "native_type": f.native_type,
        "data_type": f.data_type,
        "relation_target": f.relation_target,
        "selection": selection_values(f.selection),
        "required": f.required,
        "stored": f.stored,
    }


def selection_values(selection: list[Any] | None) -> list[str] | None:
    if selection is None:
        return None
    out: list[str] = []
    for item in selection:
        if isinstance(item, dict):
            out.append(str(item.get("value")))
        elif isinstance(item, list | tuple) and item:
            out.append(str(item[0]))
        else:
            out.append(str(item))
    return sorted(out)


def signature_compatible(old: dict[str, Any], new: dict[str, Any]) -> bool:
    """Même type natif (à défaut : même type logique), même cible de relation, et les valeurs de sélection
    anciennes toujours présentes."""
    if (old.get("native_type") or old.get("data_type")) != (new.get("native_type") or new.get("data_type")):
        return False
    if old.get("relation_target") != new.get("relation_target"):
        return False
    before, after = old.get("selection"), new.get("selection")
    if before is None:
        return True
    return after is not None and set(before) <= set(after)
