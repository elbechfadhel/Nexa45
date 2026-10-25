"""Compatibilité colonnes du fichier ↔ catalogue du système cible (V2c.1), DÉTERMINISTE, sans LLM.

Réutilise ce que l'analyse du catalogue a établi, rien de plus :
- les CIBLES possibles = les clés PLATES importables du Product Domain (`IMPORT_KEYS`, V2c.4 : distinctes
  des clés d'affichage V2a), choisies par le même sélecteur que le plan de lecture (semantic keys KNOWN /
  CONFIRMED du produit et de l'offre fournisseur, extensions confirmées) ; une clé n'est une cible que si
  un vrai champ la porte ; un champ ignoré ou encore en question n'est jamais une cible. Image principale :
  depuis une URL (le téléchargement relève de l'import réel). Les variantes ne sont pas des cibles (tranche
  à part) ;
- la correspondance en-tête → semantic key = celle du Semantic Enrichment (alias, motif, libellé, tokens,
  `propose_for_name`) sur le vocabulaire contrôlé ;
- une règle propre aux en-têtes courts (« Price ») : tous les mots de l'en-tête figurent dans le libellé ou un
  alias d'une cible → suggestion faible (REVIEW), jamais MATCHED.

    MATCHED    confiance ≥ 0.90, cible présente sur le système, type de colonne compatible
    REVIEW     suggestion moins sûre, type incompatible, sens reconnu sans cible sur ce système, ou cible
               déjà prise par une colonne plus sûre
    UNMATCHED  aucun sens reconnu
    EMPTY      aucune valeur dans le fichier : rien à importer, jamais de cible

Mémoire apprise (V2c.2) : AVANT le vocabulaire, un en-tête déjà associé par le fournisseur (même organisation)
à une semantic_key est MATCHED (source LEARNED) si cette clé est une cible valide du système et que le type
convient ; sinon rien n'est appliqué et le pipeline normal continue. Clé de mémoire = en-tête nettoyé et
normalisé (`header_key`), sans pluriel ni rapprochement. V2c.2b : un en-tête que le fournisseur a jugé inutile
POUR CE SYSTÈME (NOT_RELEVANT) est ignoré d'office, sans cible ; il prime sur un mapping de l'organisation
(le plus spécifique gagne). Une colonne vide reste EMPTY : la mémoire ne s'applique qu'ensuite.

Fallback LLM (V2c.3, `ai_candidates` / `apply_ai_proposals`) : APRÈS tout le reste, seulement pour les
colonnes UNMATCHED et les suggestions faibles des règles (REVIEW LOW_CONFIDENCE). Il ne passe jamais devant
EMPTY, la mémoire ni les règles, ne contredit jamais une suggestion des règles et ne prend jamais une cible
déjà tenue :
    TARGET ≥ 0.95, type compatible, cible libre       → MATCHED (source AI)
    TARGET 0.60-0.95 (ou cible prise / type différent) → REVIEW AI_SUGGESTION (ou TARGET_TAKEN), suggestion
    NOT_RELEVANT ≥ 0.90                               → REVIEW AI_NOT_RELEVANT (jamais ignorée d'office)
    reste                                             → inchangé
Sur une suggestion faible des règles : seule la MÊME clé à ≥ 0.95 la confirme (MATCHED), sinon inchangée.

En-têtes normalisés de façon GÉNÉRIQUE avant la correspondance (aucune règle propre à un format d'export) :
précision entre parenthèses / crochets retirée (« Weight (lbs) » → « Weight »), ponctuation finale retirée
(« In stock? »), puis, seulement si rien ne correspond, pluriel simple du dernier mot (« Categories »).

Aucun nom de système : entités et champs viennent du plan de lecture.
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from uie_core.catalog.plan import READ_KEYS, CatalogReadPlan, EntityPlan, KeySpec, PlannedField
from uie_core.domains.model import EntityRole
from uie_core.domains.semantics import SemanticSpec, normalize, propose_for_name, tokens
from uie_core.imports.profile import ColumnProfile, ColumnType
from uie_core.imports.proposer import ColumnDecision, ColumnProposal

MATCHED_THRESHOLD = 0.90
# V2c.5b : une résolution AUTOMATIQUE du LLM exige 0.95 (plus que les règles, 0.90) ; 0.90-0.949 reste une
# suggestion à confirmer en un clic (une mauvaise décision automatique coûte plus qu'une confirmation)
AI_AUTO_THRESHOLD = 0.95
AI_SUGGEST_THRESHOLD = 0.60  # plancher d'une suggestion (V1c)
AI_NOT_RELEVANT_THRESHOLD = 0.90  # « Probably not needed » (toujours à confirmer, jamais automatique)
CONTAINED_CONFIDENCE = 0.70  # en-tête court dont tous les mots sont dans le libellé d'une cible

# Rôles importables et préfixe (stable) de l'identifiant de cible ; ordre = préférence à confiance égale
TARGET_ROLES: dict[EntityRole, str] = {EntityRole.PRIMARY: "product", EntityRole.SUPPLIER_INFO: "supplier"}
GROUP_LABELS = {"product": "Product", "supplier": "Supplier offer"}
_NOT_IMPORTABLE = {"product_template"}  # lien interne (le rattachement au produit)

# Clés plates importables par rôle (V2c.4) : l'affichage V2a (`READ_KEYS`) + ce qu'un fichier peut renseigner
# d'un produit sans structure (pas de variantes, pas de one2many). Une clé sans champ réel sur le système
# n'est jamais une cible.
IMPORT_KEYS: dict[EntityRole, dict[str, KeySpec]] = {
    EntityRole.PRIMARY: {
        **READ_KEYS[EntityRole.PRIMARY],
        "tags": KeySpec("labels"),
        "cost_price": KeySpec("value"),
        "can_be_sold": KeySpec("value"),
        "can_be_purchased": KeySpec("value"),
        "description_purchase": KeySpec("value"),
        "description_internal": KeySpec("value"),
        "manufacturer_reference": KeySpec("value"),
        "internal_reference": KeySpec("value"),
    },
    EntityRole.SUPPLIER_INFO: dict(READ_KEYS[EntityRole.SUPPLIER_INFO]),
}
# Identité de mise à jour à l'import réel (V2d.2) : une clé sémantique, jamais un champ ni le nom
# (deux produits peuvent légitimement porter le même nom)
IDENTITY_KEYS = frozenset({"sku"})


class ColumnStatus(StrEnum):
    MATCHED = "MATCHED"
    REVIEW = "REVIEW"
    UNMATCHED = "UNMATCHED"
    EMPTY = "EMPTY"


class MatchSource(StrEnum):
    AUTO = "AUTO"  # vocabulaire / règles
    LEARNED = "LEARNED"  # décision passée du fournisseur (mémoire de l'organisation)
    AI = "AI"  # fallback LLM (V2c.3) : jamais écrit dans la mémoire


class ReviewReason(StrEnum):
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    TYPE_MISMATCH = "TYPE_MISMATCH"
    NOT_IN_SYSTEM = "NOT_IN_SYSTEM"
    TARGET_TAKEN = "TARGET_TAKEN"
    AI_SUGGESTION = "AI_SUGGESTION"  # suggestion du LLM à confirmer
    AI_NOT_RELEVANT = "AI_NOT_RELEVANT"  # « Probably not needed » selon le LLM, à confirmer


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ImportTarget(_Model):
    """Champ du catalogue du système cible qui peut recevoir une colonne."""

    id: str  # « <groupe>:<semantic_key> » : opaque pour le fournisseur, jamais un nom technique
    group: str  # product | supplier
    semantic_key: str
    label: str  # libellé métier (vocabulaire)
    entity: str  # technique : jamais exposé au fournisseur
    field: str
    data_type: str
    kind: str  # value | label | labels | link | media
    required: bool = False  # obligatoire dans le système (preview d'import, V2d.1)
    relation: str | None = None  # entité visée par une référence / collection (import réel, V2d.2)
    selection: list[list[str]] | None = None  # valeurs permises [clé, libellé] d'une sélection


class ColumnMatch(_Model):
    position: int
    semantic_key: str | None = None
    target_id: str | None = None
    confidence: float | None = None
    status: ColumnStatus
    reason: ReviewReason | None = None
    source: MatchSource = MatchSource.AUTO
    ignored: bool = False  # NOT_RELEVANT appris pour ce système (V2c.2b)


def import_targets(plan: CatalogReadPlan, spec: SemanticSpec) -> list[ImportTarget]:
    """Cibles du plan de lecture, dans l'ordre du vocabulaire (produit, puis offre fournisseur)."""
    order = {k.key: i for i, k in enumerate(spec.vocabulary.keys)}
    out: list[ImportTarget] = []
    for role, group in TARGET_ROLES.items():
        ep: EntityPlan | None = plan.primary if role == EntityRole.PRIMARY else plan.supplier_info
        if ep is None:
            continue
        specs = IMPORT_KEYS[role]
        planned: list[tuple[PlannedField, str]] = [
            (f, specs[k].kind) for k, f in ep.fields.items() if k not in _NOT_IMPORTABLE and k in specs
        ]
        if ep.media is not None and "main_image" in specs:  # image principale, depuis une URL
            planned.append((PlannedField(key="main_image", field=ep.media.main, data_type="binary"), "media"))
        planned += [(f, "value") for f in ep.extras if f.key not in ep.fields]
        for f, kind in sorted(planned, key=lambda p: order.get(p[0].key, len(order))):
            entry = spec.vocabulary.get(f.key)
            out.append(
                ImportTarget(
                    id=f"{group}:{f.key}",
                    group=group,
                    semantic_key=f.key,
                    label=entry.label if entry else f.key,
                    entity=ep.entity,
                    field=f.field,
                    data_type=f.data_type,
                    kind=kind,
                    required=f.required,
                    selection=f.selection,
                    relation=f.target,
                )
            )
    return out


def column_fits(column: ColumnType, target: ImportTarget) -> bool:
    """Le contenu de la colonne peut-il alimenter la cible ? Une référence se donne par son libellé (texte) ;
    sa résolution en enregistrement relève de l'import (V2c.2)."""
    if column == ColumnType.EMPTY:
        return True
    if target.kind in ("label", "labels", "link", "media") or target.data_type in ("reference", "enum"):
        return column == ColumnType.TEXT  # libellé(s) d'une référence, ou URL d'une image
    return {
        "string": True,
        "text": True,
        "json": True,
        "integer": column == ColumnType.INTEGER,
        "decimal": column in (ColumnType.INTEGER, ColumnType.DECIMAL),
        "boolean": column == ColumnType.BOOLEAN,
        "date": column == ColumnType.DATE,
        "datetime": column == ColumnType.DATE,
    }.get(target.data_type, False)


def match_columns(
    columns: list[ColumnProfile],
    targets: list[ImportTarget],
    spec: SemanticSpec,
    learned: dict[str, str] | None = None,
    not_relevant: frozenset[str] = frozenset(),
) -> list[ColumnMatch]:
    """`learned` : mappings de l'organisation, `header_key` → semantic_key ; `not_relevant` : en-têtes
    (`header_key`) jugés inutiles par le fournisseur pour le système cible."""
    by_key = _by_key(targets)
    target_keys = list(by_key)
    proposals: list[ColumnMatch] = []
    for col in columns:
        hit = _ignored(col, not_relevant) or _learned(col, by_key, learned or {})
        proposals.append(hit or _match(col, by_key, target_keys, spec))
    return _one_column_per_target(proposals)


def ai_candidates(matches: list[ColumnMatch]) -> list[ColumnMatch]:
    """Colonnes laissées au LLM : sans correspondance, ou suggestion faible des règles."""
    return [
        m
        for m in matches
        if not m.ignored
        and (
            m.status == ColumnStatus.UNMATCHED
            or (m.status == ColumnStatus.REVIEW and m.reason == ReviewReason.LOW_CONFIDENCE)
        )
    ]


def apply_ai_proposals(
    matches: list[ColumnMatch],
    proposals: dict[int, ColumnProposal],
    targets: list[ImportTarget],
    columns: list[ColumnProfile],
) -> list[ColumnMatch]:
    """Fusion DÉTERMINISTE des réponses du LLM (voir la docstring du module). Ordre des colonnes conservé."""
    by_key = _by_key(targets)
    types = {c.position: c.detected_type for c in columns}
    eligible = {m.position for m in ai_candidates(matches)}
    taken = {m.target_id for m in matches if m.target_id and not m.ignored}
    out = {m.position: m for m in matches}
    ranked = sorted(
        (p for p in proposals.values() if p.position in eligible),
        key=lambda p: (-p.confidence, p.position),  # la plus sûre garde la cible, puis la première
    )
    for p in ranked:
        m = out[p.position]
        if m.status == ColumnStatus.REVIEW:  # suggestion des règles : le LLM ne peut que la confirmer
            if (
                p.decision == ColumnDecision.TARGET
                and p.semantic_key == m.semantic_key
                and p.confidence >= AI_AUTO_THRESHOLD
            ):
                out[p.position] = m.model_copy(
                    update={
                        "status": ColumnStatus.MATCHED,
                        "reason": None,
                        "confidence": p.confidence,
                        "source": MatchSource.AI,
                    }
                )
            continue
        if p.decision == ColumnDecision.NOT_RELEVANT and p.confidence >= AI_NOT_RELEVANT_THRESHOLD:
            out[p.position] = m.model_copy(
                update={
                    "status": ColumnStatus.REVIEW,
                    "reason": ReviewReason.AI_NOT_RELEVANT,
                    "confidence": p.confidence,
                    "source": MatchSource.AI,
                }
            )
            continue
        if p.decision != ColumnDecision.TARGET or p.confidence < AI_SUGGEST_THRESHOLD:
            continue
        candidates = by_key.get(p.semantic_key or "", [])
        if not candidates:
            continue  # déjà refusée par la validation du contrat ; défense en profondeur
        fitting = next((t for t in candidates if column_fits(types[p.position], t)), None)
        target = fitting or candidates[0]
        common = {"semantic_key": target.semantic_key, "confidence": p.confidence, "source": MatchSource.AI}
        if target.id in taken:
            out[p.position] = m.model_copy(
                update={
                    **common,
                    "target_id": None,
                    "status": ColumnStatus.REVIEW,
                    "reason": ReviewReason.TARGET_TAKEN,
                }
            )
            continue
        taken.add(target.id)
        auto = fitting is not None and p.confidence >= AI_AUTO_THRESHOLD
        out[p.position] = m.model_copy(
            update={
                **common,
                "target_id": target.id,
                "status": ColumnStatus.MATCHED if auto else ColumnStatus.REVIEW,
                "reason": None if auto else ReviewReason.AI_SUGGESTION,
            }
        )
    return [out[m.position] for m in matches]


def match_by_rules(col: ColumnProfile, targets: list[ImportTarget], spec: SemanticSpec) -> ColumnMatch:
    """Ce que le vocabulaire et les règles SEULS donnent pour une colonne (sans mémoire ni unicité)."""
    by_key = _by_key(targets)
    return _match(col, by_key, list(by_key), spec)


def rule_key(header: str, spec: SemanticSpec, keys: list[str]) -> tuple[str, float] | None:
    """Clé sémantique d'un en-tête par le vocabulaire et les règles SEULS (mêmes formes de l'en-tête, alias,
    motifs, puis mots contenus à 0,70), limitée à `keys` ; None : aucune. Réutilisée par l'assistant de
    migration avant tout appel au LLM."""
    variants = header_variants(header)
    found = _propose(variants, spec, keys)
    if found is not None:
        return found
    key = next((k for v in variants if (k := _contained(v, keys, spec))), None)
    return (key, CONTAINED_CONFIDENCE) if key else None


def header_key(header: str) -> str:
    """Clé de mémoire : « Article No. », « article no », « ARTICLE NO » → « article no »."""
    return normalize(header_variants(header)[0])


def _by_key(targets: list[ImportTarget]) -> dict[str, list[ImportTarget]]:
    by_key: dict[str, list[ImportTarget]] = {}
    for t in targets:
        by_key.setdefault(t.semantic_key, []).append(t)
    return by_key


def _ignored(col: ColumnProfile, not_relevant: frozenset[str]) -> ColumnMatch | None:
    if col.detected_type == ColumnType.EMPTY or header_key(col.name) not in not_relevant:
        return None
    # sans cible : n'entre pas dans l'unicité (ne retire rien à une autre colonne)
    return ColumnMatch(
        position=col.position, status=ColumnStatus.UNMATCHED, source=MatchSource.LEARNED, ignored=True
    )


def _learned(
    col: ColumnProfile, by_key: dict[str, list[ImportTarget]], learned: dict[str, str]
) -> ColumnMatch | None:
    if col.detected_type == ColumnType.EMPTY:
        return None
    key = learned.get(header_key(col.name))
    target = next((t for t in by_key.get(key or "", []) if column_fits(col.detected_type, t)), None)
    if target is None:
        return None  # clé absente de ce système ou type incompatible : pipeline normal
    return ColumnMatch(
        position=col.position,
        semantic_key=target.semantic_key,
        target_id=target.id,
        confidence=1.0,  # décision humaine passée : prioritaire sur une règle dans l'unicité
        status=ColumnStatus.MATCHED,
        source=MatchSource.LEARNED,
    )


_BRACKETS = re.compile(r"\s*[(\[{][^)\]}]*[)\]}]")
_TRAILING = re.compile(r"[\s?!:;.,*#]+$")


def header_variants(header: str) -> list[str]:
    """Formes de l'en-tête essayées dans l'ordre : nettoyée, au singulier (dernier mot), puis, pour un en-tête
    « Groupe / Champ », le dernier segment (V2c.4 : compréhension seulement, jamais une cible inventée)."""
    cleaned = _TRAILING.sub("", _BRACKETS.sub("", header)).strip() or header.strip()
    variants = [cleaned]
    _add_singular(variants, cleaned)
    if "/" in cleaned:
        last = cleaned.rsplit("/", 1)[-1].strip()
        if last and last != cleaned:
            variants.append(last)
            _add_singular(variants, last)
    return variants


def _add_singular(variants: list[str], text: str) -> None:
    words = text.split(" ")
    single = _singular(words[-1])
    if single != words[-1]:
        variants.append(" ".join([*words[:-1], single]))


def _singular(word: str) -> str:
    low = word.lower()
    if len(word) > 4 and low.endswith("ies"):
        return word[:-3] + ("Y" if word[-3:].isupper() else "y")
    if low.endswith("sses"):
        return word[:-2]
    if len(word) > 3 and low.endswith("s") and not low.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def _propose(variants: list[str], spec: SemanticSpec, keys: list[str] | None) -> tuple[str, float] | None:
    """Meilleure clé sur les formes de l'en-tête ; la première forme l'emporte à confiance égale."""
    best: tuple[str, float] | None = None
    for v in variants:
        found = propose_for_name(normalize(v).replace(" ", "_"), v, spec, keys)  # « supplier_ref » : motifs
        if found and (best is None or found.confidence > best[1]):
            best = (found.semantic_key, found.confidence)
    return best


def _match(
    col: ColumnProfile, by_key: dict[str, list[ImportTarget]], target_keys: list[str], spec: SemanticSpec
) -> ColumnMatch:
    if col.detected_type == ColumnType.EMPTY:
        return ColumnMatch(position=col.position, status=ColumnStatus.EMPTY)
    variants = header_variants(col.name)
    found = _propose(variants, spec, target_keys)
    if found is None:
        found_key = next((k for v in variants if (k := _contained(v, target_keys, spec))), None)
        confidence = CONTAINED_CONFIDENCE if found_key else None
    else:
        found_key, confidence = found
    if found_key is None:
        other = _propose(variants, spec, None)  # sens reconnu, mais sans cible sur ce système
        if other is not None:
            return ColumnMatch(
                position=col.position,
                semantic_key=other[0],
                confidence=other[1],
                status=ColumnStatus.REVIEW,
                reason=ReviewReason.NOT_IN_SYSTEM,
            )
        return ColumnMatch(position=col.position, status=ColumnStatus.UNMATCHED)
    candidates = by_key[found_key]
    target = next((t for t in candidates if column_fits(col.detected_type, t)), None)
    if target is None:
        return ColumnMatch(
            position=col.position,
            semantic_key=found_key,
            target_id=candidates[0].id,
            confidence=confidence,
            status=ColumnStatus.REVIEW,
            reason=ReviewReason.TYPE_MISMATCH,
        )
    sure = confidence is not None and confidence >= MATCHED_THRESHOLD
    return ColumnMatch(
        position=col.position,
        semantic_key=found_key,
        target_id=target.id,
        confidence=confidence,
        status=ColumnStatus.MATCHED if sure else ColumnStatus.REVIEW,
        reason=None if sure else ReviewReason.LOW_CONFIDENCE,
    )


def _contained(header: str, keys: list[str], spec: SemanticSpec) -> str | None:
    """Tous les mots de l'en-tête dans le libellé ou un alias d'une cible ; première cible dans l'ordre."""
    words = tokens(header)
    if not words:
        return None
    for key in keys:
        entry = spec.vocabulary.get(key)
        if entry and any(words <= tokens(x) for x in [entry.label, *entry.aliases]):
            return key
    return None


def _one_column_per_target(matches: list[ColumnMatch]) -> list[ColumnMatch]:
    """Une cible reçoit au plus une colonne : la plus sûre la garde (puis la première), les autres
    → REVIEW."""
    ranked = sorted(
        (m for m in matches if m.target_id and m.reason != ReviewReason.TYPE_MISMATCH),
        key=lambda m: (-(m.confidence or 0.0), m.position),
    )
    taken: set[str] = set()
    losers: set[int] = set()
    for m in ranked:
        if m.target_id in taken:
            losers.add(m.position)
        else:
            taken.add(m.target_id or "")
    return [
        m.model_copy(
            update={
                "target_id": None,
                "status": ColumnStatus.REVIEW,
                "reason": ReviewReason.TARGET_TAKEN,
                "source": MatchSource.AUTO,
            }
        )
        if m.position in losers
        else m
        for m in matches
    ]
