"""Fichier du client → structure cible proposée par le Wizard : correspondances des colonnes, fichier préparé.

    fichier .xlsx / .csv ──► profil (lecteur des imports, INGEST seulement) ──► correspondances :
        1. nom exact (en-tête = nom ou libellé d'un champ cible, normalisé) : certain, sauf si le vocabulaire
           du domaine est certain d'un autre sens (le champ du même nom n'est alors pas retenu) ;
        2. LLM pour les autres colonnes non vides (contrat `propose_columns` des imports, exemples MASQUÉS) :
           ≥ 0,90 → correspondance de l'IA, 0,60-0,90 → à confirmer, sinon non reconnue ;
    ──► décisions de l'utilisateur ──► fichier préparé : colonnes de la structure cible, valeurs du
        client COPIÉES telles quelles (aucune conversion, aucune valeur inventée), feuilles « Mapping » et
        « Read me ».

Sans état : le fichier n'est jamais stocké (le client le renvoie pour l'aperçu et le téléchargement). Rien
n'est envoyé à un système. Un fichier préparé n'est jamais présenté comme prêt à importer : seule la structure
vérifiée par le Registry est dite vérifiée, et le client doit la confronter au modèle officiel de la cible."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import unicodedata
from collections import Counter
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

import polars as pl
import xlsxwriter
from pydantic import BaseModel, ConfigDict

from uie_core.domains.pack import ImportDefault
from uie_core.domains.semantics import SemanticSpec
from uie_core.imports import CandidateColumn, ColumnProposalBatch, TargetChoice, mask_example, read_table
from uie_core.imports.matching import header_key, rule_key
from uie_core.imports.proposer import ColumnDecision
from uie_core.wizard.missing import set_by_target
from uie_core.wizard.model import TargetField, TargetProposal, Verification
from uie_core.wizard.quality import check_barcodes, is_barcode, missing_prices
from uie_core.wizard.text import count, number, say
from uie_core.wizard.units import is_package, unit_code
from uie_core.wizard.values import (
    EXAMPLES,
    WITHOUT_PRICES,
    ColumnCheck,
    ExcludedRow,
    ValueIssue,
    ValueQuestion,
    ValueTransform,
    _clip,
    check_column,
    currency_question,
    detect_currency,
    is_money,
    merge_transforms,
    rows_of,
    unwrap,
)

# types numériques de la structure : une valeur « nombre simple » y est écrite comme nombre dans l'Excel
NUMERIC_TYPES = frozenset({"number", "decimal", "float", "monetary", "integer"})
_PLAIN_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")
log = logging.getLogger("uie.wizard")

_SCIENTIFIC = re.compile(r"^-?\d+(\.\d+)?[eE][+-]?\d+$")
AI_MATCH = 0.90  # au-dessus : correspondance de l'IA (affichée comme telle, modifiable)
AI_SUGGEST = 0.60  # entre les deux : à confirmer par l'utilisateur
PREVIEW_ROWS = 5
SHOWN_EXAMPLES = 3


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ColumnStatus(StrEnum):
    MATCHED = "matched"  # même nom qu'un champ cible (certain)
    AI_MATCH = "ai_match"  # proposée par l'IA avec une confiance élevée
    TO_CONFIRM = "to_confirm"  # proposition incertaine : l'utilisateur confirme ou corrige
    UNMATCHED = "unmatched"  # aucune correspondance : non importée sauf choix de l'utilisateur
    EMPTY = "empty"  # colonne vide dans ce fichier
    PREVIOUS = "previous"  # choix déjà retenu pour cet en-tête (structure précédente), réappliqué


class PreviousChoice(_Model):
    """Choix retenu pour une colonne lors d'une analyse précédente (même fichier, autre structure) : en-tête →
    champ cible, ou None (« Not imported » choisi). Réappliqué tant que le champ existe dans la structure."""

    header: str
    target: str | None = None


class ColumnMapping(_Model):
    position: int
    header: str
    filled: int
    examples: list[str]  # valeurs du fichier du client (affichées à lui seul, jamais envoyées telles quelles)
    status: ColumnStatus
    target: str | None = None  # nom du champ cible proposé
    confidence: int | None = None  # pourcentage (IA)
    probably_not_needed: bool = (
        False  # l'IA pense que la colonne ne sert pas à la cible (jamais ignorée d'office)
    )


class FileMapping(_Model):
    file_name: str
    rows: int
    columns: list[ColumnMapping]
    # l'IA n'a pas pu analyser les colonnes (délai, coupure…) : seules les correspondances par nom sont là ;
    # jamais présenté comme « aucune correspondance » (l'utilisateur relance l'analyse)
    ai_unavailable: bool = False

    def count(self, *statuses: ColumnStatus) -> int:
        return sum(1 for c in self.columns if c.status in statuses)


class ColumnHints(_Model):
    """Ce que Nexa45 sait déjà d'un en-tête, sans LLM : vocabulaire du domaine de la structure (champ de la
    structure → clé sémantique déclarée par le domain pack) et mémoire apprise de l'organisation (en-tête
    normalisé → clé sémantique choisie ; en-têtes jugés « pas utiles » pour ce système)."""

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    spec: SemanticSpec
    field_keys: dict[str, str]
    learned: dict[str, str] = {}
    not_relevant: frozenset[str] = frozenset()


class RecordSheet(_Model):
    """Enregistrements LIÉS à créer avant les lignes (ex. les catégories d'articles) : entité liée, champ du
    nom et champ du parent (hiérarchie) lus dans le domain pack ; « id » = identifiant externe,
    « <champ>/id » = lien par identifiant externe (convention d'import des fichiers de la cible)."""

    field: str  # champ des lignes qui désigne l'enregistrement lié (ex. la catégorie de l'article)
    label: str
    entity: str
    name_field: str
    parent_field: str | None = None


class RecordIdentity(_Model):
    """Identifiant externe STABLE de chaque ligne du fichier préparé (colonne « id », même convention que les
    feuilles « (import first) ») : fondé sur le champ identifiant de la structure (clé « sku » du domain pack)
    quand la ligne en a un, sinon sur le nom. Réimporter le même fichier met à jour les enregistrements au
    lieu de les créer en double."""

    entity: str
    key_field: str | None = None
    name_field: str | None = None


class CreatedRecords(_Model):
    """Feuille à importer AVANT les lignes : un enregistrement par valeur (et par niveau d'un chemin)."""

    field: str
    label: str
    entity: str
    headers: list[str]
    rows: list[list[str]]


class UnitSettings(_Model):
    """Unités de la cible (domain pack) : nom de chaque unité canonique, champs qui désignent une unité
    (références), unité des champs chiffrés."""

    unit_names: dict[str, str] = {}
    unit_fields: list[str] = []
    field_units: dict[str, str] = {}


UNITS_AS_PIECE, UNITS_KEEP = "unit", "keep"


def units_key(field: str) -> str:
    return f"units:{field}"


# choix d'une référence absente de la cible : la créer (feuille à part) ou laisser son défaut à la cible
RECORDS_CREATE, RECORDS_DEFAULT = "create", "default"
PATH_SPLIT = re.compile(r"\s+[/>|]\s+|\s*>\s*")


def records_key(field: str) -> str:
    return f"records:{field}"


class MappingDecision(_Model):
    position: int
    target: str | None  # None : colonne non importée


class ValueConversion(_Model):
    """Valeur convertie (unité) : la valeur SOURCE est conservée à côté de la valeur écrite."""

    row: int  # numéro de ligne dans le fichier du client (1 = première ligne de données)
    column: str
    field: str
    source: str
    written: str


class AlreadyExported(_Model):
    """Pièces déjà exportées par un fichier précédent, à une date (jamais réécrites)."""

    date: str  # AAAA-MM-JJ ; vide : date inconnue
    count: int


class PreparedFile(_Model):
    rows: int
    headers: list[str]  # champs cibles, dans l'ordre de la structure
    preview: list[list[str]]  # premières lignes du fichier préparé
    mapped: int  # champs cibles alimentés par une colonne du client
    missing_required: list[str]  # champs obligatoires encore sans valeur sur au moins une ligne (noms)
    # obligatoires CALCULÉS par la cible d'après d'autres champs (Registry) : {field, label, source}
    set_by_target: list[dict[str, str]] = []
    verification: Verification
    data_type: str = ""  # type de données choisi dans le parcours (réponse), sinon le titre de la structure
    # « prepared » (fichier préparé) ≠ « ready » (prêt à importer : tous les contrôles réellement passés)
    status: str = "prepared"  # to_complete | prepared | ready
    checks: list[dict[str, Any]] = []  # contrôles effectués : {label, ok}
    completed: list[str] = []  # champs complétés par l'utilisateur (« Complete missing information »)
    # valeurs remplies d'office d'après les réponses du parcours (valeurs admises du Registry, vérifiées)
    auto_filled: list[dict[str, str]] = []
    warnings: list[str] = []  # valeurs saisies non vérifiables : avertissements CONSERVÉS
    # colonnes du fichier AVEC des valeurs qui ne vont dans aucun champ : jamais perdues en silence
    not_imported: list[str] = []
    source_rows: int = 0  # lignes du fichier du client (rows = lignes écrites)
    value_issues: list[ValueIssue] = []  # valeurs NON écrites, groupées par sorte, comptées
    value_transforms: list[ValueTransform] = []  # transformations déterministes appliquées (avant → après)
    value_conversions: list[ValueConversion] = []  # chaque conversion d'unité, valeur source conservée
    value_questions: list[ValueQuestion] = []  # décisions sur les valeurs (par colonne et par sorte)
    currency_column: str | None = None  # colonne de devises du fichier, si un montant est mappé
    currency: str | None = None  # devise du système cible choisie par l'utilisateur
    excluded_rows: list[ExcludedRow] = []  # lignes non importées (autre devise, doublon), listées
    # pièces déjà exportées, comptées par date de l'export précédent (sur la liste COMPLÈTE) ; `nothing_new` :
    # rien de neuf à écrire, tout ce qui pouvait l'être l'a déjà été
    already_exported: list[AlreadyExported] = []
    nothing_new: bool = False
    # enregistrements liés à créer AVANT les lignes (choix « créer » de l'utilisateur), une feuille chacun
    created_records: list[CreatedRecords] = []
    # références laissées à la valeur par défaut de la cible (choix de l'utilisateur) : libellés
    left_to_target: list[str] = []
    # plus de la moitié des lignes sans leurs montants (autre devise) : message en haut de la synthèse
    currency_notice: CurrencyNotice | None = None
    # « sheet » : fichier préparé pour l'import de la cible ; « format » : fichier du FORMAT de la cible
    # (ex. DATEV EXTF), produit par son binding
    delivery: str = "sheet"
    notices: list[str] = []  # constats du format cible (enregistrements écrits, comptes réservés, période)


class CurrencyNotice(_Model):
    """Plus de la moitié des lignes ont leurs montants dans une autre devise que celle de la cible : message
    bien visible en haut de la synthèse (« Your system uses EUR, your file uses USD: … »)."""

    target: str  # devise de la cible (société du système connecté, ou choisie par l'utilisateur)
    file: list[str]  # devises de ces lignes dans le fichier, la plus fréquente d'abord
    rows: int  # lignes préparées SANS leurs montants
    fields: list[str]  # libellés des montants non écrits (prix de vente, coût…)
    from_system: bool = False  # devise lue dans le système connecté choisi comme cible
    # ce que l'import de la cible écrira à la place (valeur par défaut, cellule vide) : phrase, ou ""
    note: str = ""


class MappingError(ValueError):
    """Décisions de correspondance invalides (→ 422)."""


class WizardColumnsRequest(_Model):
    request_ref: str  # opaque : ni organisation, ni fichier
    data_type: str
    system: str
    version: str
    columns: list[CandidateColumn]
    targets: list[TargetChoice]


@runtime_checkable
class WizardColumnProposer(Protocol):
    """Propose une correspondance pour chaque colonne (même contrat de réponse que les imports)."""

    def wizard_columns(self, request: WizardColumnsRequest) -> ColumnProposalBatch: ...


_SPACES = re.compile(r"[^a-z0-9]+")


def _norm(text: str) -> str:
    return _SPACES.sub(" ", text.casefold()).strip()


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _targets(proposal: TargetProposal) -> list[TargetChoice]:
    return [
        TargetChoice(
            key=f.name,
            label=f.label,
            description=(f.description or f.label)[:300],
            group="target",
            data_type=f.type,
        )
        for f in proposal.fields
    ]


def map_columns(
    proposal: TargetProposal,
    data: bytes,
    file_name: str,
    *,
    context: dict[str, str],
    proposer: WizardColumnProposer | None,
    previous: list[PreviousChoice] | None = None,
    hints: ColumnHints | None = None,
) -> FileMapping:
    """Correspondances proposées pour chaque colonne du fichier : choix précédent pour le même en-tête (la
    structure a changé, le fichier non), nom exact, mémoire de l'organisation et vocabulaire du domaine
    (`hints`), puis IA pour les SEULES colonnes restées sans correspondance."""
    _, df = read_table(data, file_name)
    fields = {f.name for f in proposal.fields}
    by_name: dict[str, str] = {}
    for f in proposal.fields:
        by_name.setdefault(_norm(f.name), f.name)
        by_name.setdefault(_norm(f.label), f.name)
    # un choix précédent n'est repris que si son champ existe encore (sinon : analyse normale)
    kept: dict[str, str | None] = {}
    for c in previous or []:
        if c.target is None or c.target in fields:
            kept.setdefault(_norm(c.header), c.target)
    taken: set[str] = {t for t in kept.values() if t}
    columns: list[ColumnMapping] = []
    pending: list[tuple[int, str, pl.Series]] = []
    for pos, name in enumerate(df.columns):
        header = str(name).strip()
        col = df.get_column(name)
        values = [_cell(v) for v in col.drop_nulls().to_list() if _cell(v).strip()]
        examples = list(dict.fromkeys(values))[:SHOWN_EXAMPLES]
        target = by_name.get(_norm(header))
        if target and hints is not None and _other_meaning(header, target, hints):
            target = None  # le vocabulaire donne un autre sens que le champ du même nom : règles, puis IA
        if not values:
            columns.append(
                ColumnMapping(position=pos, header=header, filled=0, examples=[], status=ColumnStatus.EMPTY)
            )
        elif _norm(header) in kept:
            columns.append(
                ColumnMapping(
                    position=pos,
                    header=header,
                    filled=len(values),
                    examples=examples,
                    status=ColumnStatus.PREVIOUS,
                    target=kept.pop(_norm(header)),  # un en-tête en double ne reprend le choix qu'une fois
                )
            )
        elif target and target not in taken:
            taken.add(target)
            columns.append(
                ColumnMapping(
                    position=pos,
                    header=header,
                    filled=len(values),
                    examples=examples,
                    status=ColumnStatus.MATCHED,
                    target=target,
                )
            )
        else:
            columns.append(
                ColumnMapping(
                    position=pos,
                    header=header,
                    filled=len(values),
                    examples=examples,
                    status=ColumnStatus.UNMATCHED,
                )
            )
            pending.append((pos, header, col))
    weak: set[int] = set()
    if pending and hints is not None:
        columns, pending, weak = _with_rules(columns, pending, hints, fields, taken)
    if pending and proposer is not None:
        columns, ai_unavailable = _with_ai(
            columns, pending, df.height, proposal, context, proposer, taken, weak
        )
    else:
        ai_unavailable = False
    return FileMapping(
        file_name=file_name[:200], rows=df.height, columns=columns, ai_unavailable=ai_unavailable
    )


def _other_meaning(header: str, target: str, hints: ColumnHints) -> bool:
    """Le nom exact d'un champ cède au vocabulaire du domaine quand celui-ci est CERTAIN que l'en-tête a un
    autre sens que celui déclaré pour ce champ (ex. « description » d'un fichier = texte pour les clients,
    alors que le champ du même nom de la cible est une note interne)."""
    declared = hints.field_keys.get(target)
    if declared is None:
        return False
    meaning = rule_key(header, hints.spec, [e.key for e in hints.spec.vocabulary.keys])
    return meaning is not None and meaning[1] >= AI_MATCH and meaning[0] != declared


def _with_rules(
    columns: list[ColumnMapping],
    pending: list[tuple[int, str, pl.Series]],
    hints: ColumnHints,
    fields: set[str],
    taken: set[str],
) -> tuple[list[ColumnMapping], list[tuple[int, str, pl.Series]], set[int]]:
    """Sans LLM : mémoire de l'organisation (correspondance déjà choisie → reprise ; « pas utile » pour ce
    système → signalée, jamais ignorée d'office), puis vocabulaire du domaine : ≥ 0,90 → correspondance, sans
    LLM ; 0,60 à 0,90 → « à confirmer » mais ENVOYÉE au LLM quand même (comme les imports, V2c.3), la règle
    ne restant qu'en repli. Une cible reçoit au plus une colonne, la plus sûre la garde. Une colonne dont le
    sens est CERTAIN mais qu'aucun champ de la structure ne porte reste « non importée » sans appel (le LLM ne
    choisit que parmi ces champs). Rend (colonnes, colonnes à envoyer au LLM, positions en repli)."""
    to_field: dict[str, str] = {}
    for name, key in hints.field_keys.items():
        if name in fields and name not in taken:
            to_field.setdefault(key, name)
    keys = list(to_field)
    own = set(hints.field_keys.values())
    every = [e.key for e in hints.spec.vocabulary.keys]
    found: dict[int, tuple[str, float, ColumnStatus]] = {}
    not_needed: set[int] = set()
    known_elsewhere: set[int] = set()
    for pos, header, _ in pending:
        hk = header_key(header)
        learned = hints.learned.get(hk)
        if learned in to_field:
            found[pos] = (to_field[learned], 1.0, ColumnStatus.PREVIOUS)
        elif hk in hints.not_relevant:
            not_needed.add(pos)
        elif keys and (rule := rule_key(header, hints.spec, keys)) and rule[1] >= AI_SUGGEST:
            status = ColumnStatus.MATCHED if rule[1] >= AI_MATCH else ColumnStatus.TO_CONFIRM
            found[pos] = (to_field[rule[0]], rule[1], status)
        elif (
            (meaning := rule_key(header, hints.spec, every))
            and meaning[1] >= AI_MATCH
            and meaning[0] not in own
        ):
            known_elsewhere.add(pos)  # sens certain, aucun champ de la structure ne l'a : rien à demander
    winners: dict[int, tuple[str, float, ColumnStatus]] = {}
    sure = {pos: x for pos, x in found.items() if x[2] != ColumnStatus.TO_CONFIRM}
    for pos, (target, confidence, status) in sorted(sure.items(), key=lambda x: (-x[1][1], x[0])):
        if target not in taken:
            taken.add(target)
            winners[pos] = (target, confidence, status)
    weak: dict[int, tuple[str, float, ColumnStatus]] = {}  # repli : la cible n'est PAS réservée
    for pos, (target, confidence, status) in sorted(found.items(), key=lambda x: (-x[1][1], x[0])):
        if pos not in sure and target not in taken and target not in {w[0] for w in weak.values()}:
            weak[pos] = (target, confidence, status)
    out = []
    for c in columns:
        if c.position in winners or c.position in weak:
            target, confidence, status = winners.get(c.position) or weak[c.position]
            pct = round(confidence * 100) if status == ColumnStatus.TO_CONFIRM else None
            out.append(c.model_copy(update={"status": status, "target": target, "confidence": pct}))
        elif c.position in not_needed:
            out.append(c.model_copy(update={"probably_not_needed": True}))
        else:
            out.append(c)
    rest = [x for x in pending if x[0] not in winners and x[0] not in not_needed | known_elsewhere]
    return out, rest, set(weak)


def _detected_type(col: pl.Series) -> str:
    """Type lu sur les VALEURS (aide l'IA : une colonne de nombres n'est pas un texte libre)."""
    values = [_plain(v) for v in (unwrap(_cell(x))[0] for x in col.drop_nulls().to_list()) if v]
    if values and all(_PLAIN_NUMBER.match(v) for v in values):
        return "decimal" if any("." in v for v in values) else "integer"
    return "text"


def _plain(value: str) -> str:
    """Nombre en notation scientifique → décimal (même valeur) ; toute autre valeur inchangée."""
    if _SCIENTIFIC.match(value):
        return format(float(value), "f").rstrip("0").rstrip(".")
    return value


def _with_ai(
    columns: list[ColumnMapping],
    pending: list[tuple[int, str, pl.Series]],
    height: int,
    proposal: TargetProposal,
    context: dict[str, str],
    proposer: WizardColumnProposer,
    taken: set[str],
    weak: set[int] | None = None,
) -> tuple[list[ColumnMapping], bool]:
    candidates = [
        CandidateColumn(
            position=pos,
            header=header,
            detected_type=_detected_type(col),
            fill_ratio=round(col.drop_nulls().len() / max(height, 1), 2),
            examples=[
                mask_example(_cell(v)) for v in col.drop_nulls().unique(maintain_order=True).to_list()[:3]
            ],
        )
        for pos, header, col in pending
    ]
    targets = _targets(proposal)
    ref = hashlib.sha256(json.dumps([c.header for c in candidates]).encode()).hexdigest()[:16]
    batch = proposer.wizard_columns(
        WizardColumnsRequest(
            request_ref=ref,
            data_type=context.get("data_type", ""),
            system=context.get("system", ""),
            version=context.get("version", ""),
            columns=candidates,
            targets=targets,
        )
    )
    if batch.error is not None:
        # cause réelle (timeout, HTTP 429...) dans le journal de l'API, jamais côté fournisseur
        log.warning("wizard columns: AI unavailable for %d columns (%s)", len(candidates), batch.error)
        return columns, True  # rien d'inventé : les colonnes restent à analyser, l'échec est dit
    by_pos = {p.position: p for p in batch.proposals}
    # la plus sûre garde une cible disputée (jamais l'ordre des colonnes)
    order = sorted(by_pos.values(), key=lambda p: -p.confidence)
    winners: dict[int, str] = {}
    for p in order:
        key = p.semantic_key
        if p.decision == ColumnDecision.TARGET and key and key not in taken and p.confidence >= AI_SUGGEST:
            winners[p.position] = key
            taken.add(key)
    out = []
    for c in columns:
        p = by_pos.get(c.position)
        fallback = c.position in (weak or set())
        if fallback and c.position not in winners and c.target in winners.values():
            # la cible de la règle faible revient à une colonne que l'IA juge plus sûre
            out.append(
                c.model_copy(update={"status": ColumnStatus.UNMATCHED, "target": None, "confidence": None})
            )
            continue
        if fallback and c.position in winners:
            c = c.model_copy(update={"status": ColumnStatus.UNMATCHED, "target": None, "confidence": None})
        if c.status != ColumnStatus.UNMATCHED or p is None:
            out.append(c)
            continue
        pct = round(p.confidence * 100)
        if c.position in winners:
            status = ColumnStatus.AI_MATCH if p.confidence >= AI_MATCH else ColumnStatus.TO_CONFIRM
            out.append(
                c.model_copy(update={"status": status, "target": winners[c.position], "confidence": pct})
            )
        elif p.decision == ColumnDecision.NOT_RELEVANT and p.confidence >= AI_MATCH:
            out.append(c.model_copy(update={"probably_not_needed": True, "confidence": pct}))
        else:
            out.append(c)
    return out, False


def check_decisions(
    proposal: TargetProposal, mapping_columns: int, decisions: list[MappingDecision]
) -> dict[int, str]:
    """Décisions de l'utilisateur → colonne → champ cible (un champ par colonne au plus, jamais deux fois)."""
    fields = {f.name for f in proposal.fields}
    out: dict[int, str] = {}
    for d in decisions:
        if not 0 <= d.position < mapping_columns:
            raise MappingError(f"Unknown column {d.position + 1}")
        if d.target is None:
            continue
        if d.target not in fields:
            raise MappingError(f"Unknown target field {d.target}")
        if d.target in out.values():
            raise MappingError(f"The target field {d.target} is chosen for two columns")
        out[d.position] = d.target
    return out


def prepare(  # noqa: C901
    proposal: TargetProposal,
    data: bytes,
    file_name: str,
    decisions: list[MappingDecision],
    fills: dict[str, list[str]] | None = None,
    completed: list[str] | None = None,
    warnings: list[str] | None = None,
    choices: dict[str, str] | None = None,
    records: dict[str, RecordSheet] | None = None,
    units: UnitSettings | None = None,
    currency_from_system: bool = False,
    keep_duplicates: bool = False,
    noun: str = "rows",
    import_defaults: dict[str, ImportDefault] | None = None,
    target_label: str = "The target",
) -> tuple[PreparedFile, list[list[str]], list[tuple[str, str]]]:
    """Lignes du fichier préparé : une colonne par champ cible, valeurs du client passées par le moteur de
    valeurs (`values.check_column` : transformations déterministes, questions par colonne, anomalies
    groupées ; jamais de valeur devinée) ; + les correspondances retenues (colonne → champ cible).

    Montants : la devise de chaque ligne vient de la colonne de devises du fichier ou de la valeur elle-même
    (« 12 EUR », « €12 ») ; la devise du système cible est une question (`choices["currency"]`) : une ligne
    dans une autre devise est GARDÉE avec toutes ses autres valeurs, ses montants ne sont pas écrits (listés
    avec leur devise d'origine) ; rien n'est converti. Réponse « sans les prix » : aucun montant écrit.
    Toutes les lignes sont rendues (mêmes rangs que le fichier du client) : `kept_rows` donne celles du
    fichier préparé."""
    choices = choices or {}
    _, df = read_table(data, file_name)
    chosen = check_decisions(proposal, df.width, decisions)
    by_field = {f.name: f for f in proposal.fields}
    names = [str(n).strip() for n in df.columns]
    cells = [[_cell(v) for v in df.get_column(n).to_list()] for n in df.columns]
    headers = [f.name for f in proposal.fields]
    checks: dict[str, ColumnCheck] = {}
    pos_of = {target: pos for pos, target in chosen.items()}
    # créer les enregistrements liés : une liste (« ["A", "B"] ») se lit comme un chemin (hiérarchie)
    for pos, target in chosen.items():
        if choices.get(records_key(target)) == RECORDS_CREATE:
            choices = {f"list:{pos}": "path", **choices}
    for pos, target in sorted(chosen.items()):
        unit = units.field_units.get(target) if units else None
        checks[target] = check_column(
            by_field[target], names[pos], pos, cells[pos], choices, target_unit=unit
        )
    series = {t: c.values for t, c in checks.items()}
    issues = [i for c in checks.values() for i in c.issues]
    questions = [q for c in checks.values() for q in c.questions]
    transforms = [t for c in checks.values() for t in c.transforms]
    conversions = [
        ValueConversion(row=i + 1, column=names[pos_of[t]], field=t, source=src, written=out)
        for t, c in checks.items()
        for i, src, out in c.conversions
    ]
    # montants : devise de chaque ligne (colonne de devises, sinon la valeur elle-même) ; jamais convertie
    found_currency = detect_currency(names, cells)
    money = [t for t in series if is_money(by_field[t])]
    # lignes identiques : écrites une fois ; un relevé bancaire décide lui-même de ses doublons
    # (référence de la banque, décision « Export both »)
    excluded: list[ExcludedRow] = [] if keep_duplicates else duplicate_rows(cells, sorted(chosen))
    currency: str | None = None
    notice: CurrencyNotice | None = None
    row_currency: list[str | None] = [None] * df.height
    if found_currency:
        row_currency = [unwrap(v)[0].upper() or None for v in cells[found_currency.position]]
    for t in money:
        conflicts: list[str] = []
        conflict_rows: list[int] = []
        for i, mark in enumerate(checks[t].currencies):
            if not mark:
                continue
            if row_currency[i] and row_currency[i] != mark:
                conflicts.append(cells[pos_of[t]][i])
                conflict_rows.append(i)
                series[t][i] = ""
            else:
                row_currency[i] = mark
        if conflicts:
            issues.append(
                ValueIssue(
                    field=t,
                    column=names[pos_of[t]],
                    kind="currency_conflict",
                    count=len(conflicts),
                    examples=conflicts[:3],
                    message=say(
                        "{n} [amount|amounts] of {label} [names|name] another currency than the currency "
                        "column: not written.",
                        len(conflicts),
                        label=by_field[t].label,
                    ),
                    rows=rows_of(conflict_rows),
                    fix="Make the amount and the currency column of these rows agree in your file.",
                )
            )
    with_amount = [i for i in range(df.height) if any(series[t][i] for t in money)]
    codes = Counter(row_currency[i] for i in with_amount if row_currency[i])
    if money and with_amount and codes:
        header = found_currency.header if found_currency else None
        q = currency_question(codes, header or "", choices, noun)
        questions.append(q)
        currency = q.answer
        if currency is None:
            for t in money:
                waiting = [i for i, v in enumerate(series[t]) if v]
                n = len(waiting)
                if n:
                    issues.append(
                        ValueIssue(
                            field=t,
                            column=names[pos_of[t]],
                            kind="currency",
                            count=n,
                            examples=list(codes)[:3],
                            message=say(
                                "{n} [amount|amounts] of {label}: waiting for the currency of your target "
                                "system.",
                                n,
                                label=by_field[t].label,
                            ),
                            rows=rows_of(waiting),
                            fix="Answer the question about the currency above.",
                        )
                    )
                series[t] = [""] * df.height
            transforms = [x for x in transforms if x.field not in money]  # rien d'écrit : rien de transformé
        elif currency == WITHOUT_PRICES:
            for t in money:
                left_out = [i for i, v in enumerate(series[t]) if v]
                if left_out:
                    issues.append(
                        ValueIssue(
                            field=t,
                            column=names[pos_of[t]],
                            kind="currency_left_out",
                            count=len(left_out),
                            examples=[c for c, _ in codes.most_common(3)],
                            message=say(
                                "{n} [amount|amounts] of {label} not written: you chose to import the {noun} "
                                "without their prices.",
                                len(left_out),
                                label=by_field[t].label,
                                noun=noun,
                            ),
                            rows=rows_of(left_out),
                            fix="Set these prices in the target system after the import.",
                        )
                    )
                series[t] = [""] * df.height
            transforms = [x for x in transforms if x.field not in money]
            currency = None
        else:
            # autre devise (ou aucune) : la ligne est GARDÉE, seuls ses montants ne sont pas écrits
            duplicates = {e.row - 1 for e in excluded}
            other = [i for i in with_amount if row_currency[i] != currency and i not in duplicates]
            touched: list[str] = []
            for t in money:
                left_out = [i for i in other if series[t][i]]
                if not left_out:
                    continue
                touched.append(by_field[t].label)
                found = Counter(row_currency[i] or "no currency" for i in left_out)
                listed = (
                    next(iter(found))
                    if len(found) == 1
                    else ", ".join(f"{c} ({n})" for c, n in found.most_common())
                )
                issues.append(
                    ValueIssue(
                        field=t,
                        column=names[pos_of[t]],
                        kind="currency_other",
                        count=len(left_out),
                        examples=[cells[pos_of[t]][i] for i in left_out[:EXAMPLES]],
                        message=say(
                            "{n} [amount|amounts] of {label} in {listed}, not {target}: not written, never "
                            "converted.",
                            len(left_out),
                            label=by_field[t].label,
                            target=currency,
                            listed=listed,
                        ),
                        rows=rows_of(left_out),
                        fix=f"Set these prices in {currency} in the target system, or write them in "
                        f"{currency} in your file.",
                    )
                )
                for i in left_out:
                    series[t][i] = ""
            kept = df.height - len(duplicates)
            if other and len(other) * 2 > kept:
                notice = CurrencyNotice(
                    target=currency,
                    file=[c for c, _ in Counter(row_currency[i] or "" for i in other).most_common() if c],
                    rows=len(other),
                    fields=touched,
                    from_system=currency_from_system,
                )
    # valeurs complétées : seulement là où le fichier du client n'a rien (jamais écrasé)
    for name, column in (fills or {}).items():
        own = series.get(name, [""] * df.height)
        series[name] = [own[i] or (column[i] if i < len(column) else "") for i in range(df.height)]
    out = {e.row - 1 for e in excluded}
    # références absentes de la cible : créées (feuille à importer d'abord) ou laissées au défaut de la cible
    created: list[CreatedRecords] = []
    left: list[str] = []
    defaulted: set[str] = set()
    for key, choice in choices.items():
        name = key.removeprefix("records:")
        if name == key or name not in by_field:
            continue
        if (
            choice == RECORDS_DEFAULT
        ):  # toute référence : rien d'écrit, la cible applique sa valeur par défaut
            series[name] = [""] * df.height
            left.append(by_field[name].label)
            defaulted.add(name)
        elif choice == RECORDS_CREATE and name in series and name in (records or {}):
            paths = [
                [p for p in PATH_SPLIT.split(v) if p] if v and i not in out else []
                for i, v in enumerate(series[name])
            ]
            series[name] = [" / ".join(p) for p in paths]  # chemin complet : nom dans la cible
            created.append(records_sheet((records or {})[name], [p for p in paths if p]))
    # valeurs d'unités écrites avec le nom de la cible (« Stück » → Units) ; inconnues : signalées
    for name in units.unit_fields if units else []:
        if name in series and name not in defaulted:
            series[name], transform, issue = translate_units(
                by_field[name],
                names[pos_of[name]] if name in pos_of else "",
                series[name],
                units,
                out,
                choices.get(units_key(name)),
            )
            transforms += [transform] if transform else []
            issues += [issue] if issue else []
    # contrôles avant l'import (valeurs écrites) : codes-barres non écrits, prix manquants signalés
    for t in [t for t in series if is_barcode(by_field[t])]:
        column = names[pos_of[t]] if t in pos_of else ""
        series[t], found = check_barcodes(by_field[t], column, series[t], out)
        issues += found
    written = [i for i in range(df.height) if i not in out]
    given = {t: {i for i, v in enumerate(cells[pos]) if unwrap(v)[0]} for t, pos in pos_of.items()}
    issues += missing_prices(
        proposal.fields, series, {t: names[pos] for t, pos in pos_of.items()}, written, given
    )
    rows = [[series[h][i] if h in series else "" for h in headers] for i in range(df.height)]
    kept = [r for i, r in enumerate(rows) if i not in out]
    # montants laissés vides : ce que l'import de la cible écrira à la place, DIT (jamais écrit par Nexa45)
    notes = {
        t: target_note(target_label, by_field[t].label, noun, d, any(r[headers.index(t)] for r in kept))
        for t, d in (import_defaults or {}).items()
        if t in by_field and is_money(by_field[t])
    }
    issues = [
        i.model_copy(update={"message": f"{i.message} {notes[i.field]}"})
        if notes.get(i.field) and i.kind in EMPTY_AMOUNT_KINDS
        else i
        for i in issues
    ]
    if notice is not None:
        labels = {by_field[t].label: t for t in money}
        said = [notes[labels[x]] for x in notice.fields if notes.get(labels.get(x, ""))]
        notice = notice.model_copy(update={"note": " ".join(said)})
    # calculé par la cible d'après d'autres champs (Registry) : jamais demandé, jamais rempli ici
    by_target = {f.name: set_by_target(f, proposal.fields) for f in proposal.fields}
    missing = [
        f.name
        for f in proposal.fields
        if f.required
        and not by_target[f.name]
        and f.name not in defaulted  # laissé à la valeur par défaut de la cible : choix de l'utilisateur
        and (f.name not in series or not all(r[headers.index(f.name)] for r in kept))
    ]
    summary = PreparedFile(
        rows=len(kept),
        source_rows=df.height,
        headers=headers,
        preview=kept[:PREVIEW_ROWS],
        mapped=len(chosen),
        missing_required=missing,
        set_by_target=[
            {"field": name, "label": by_field[name].label, "source": ", ".join(d.label for d in deps)}
            for name, deps in by_target.items()
            if deps and not any(series.get(name, []))
        ],
        verification=proposal.verification,
        completed=list(completed or []),
        warnings=list(warnings or []),
        not_imported=[
            names[pos] for pos in range(df.width) if pos not in chosen and any(v.strip() for v in cells[pos])
        ],
        value_issues=issues,
        value_transforms=merge_transforms(transforms),
        value_conversions=[c for c in conversions if c.row - 1 not in out],
        value_questions=questions,
        currency_column=found_currency.header if found_currency and money else None,
        currency=currency,
        excluded_rows=excluded,
        created_records=created,
        left_to_target=left,
        currency_notice=notice,
    )
    pairs = [(names[pos], target) for pos, target in sorted(chosen.items())]
    return summary, rows, pairs


# montants laissés vides (autre devise, choix « sans les prix », prix absent du fichier)
EMPTY_AMOUNT_KINDS = frozenset({"currency_other", "currency_left_out", "missing_price"})


def target_note(target: str, label: str, noun: str, d: ImportDefault, column_written: bool) -> str:
    """Ce que l'import de fichier de la cible écrira dans un montant laissé vide : sa valeur par défaut si la
    colonne n'est pas dans le fichier préparé (toutes les valeurs vides), sinon la valeur d'une cellule vide.
    Dit à l'utilisateur, jamais écrit par Nexa45 ; "" : inconnu (pack)."""
    if column_written:
        return f"{target} imports an empty {label} as {d.empty}." if d.empty else ""
    return f"{target} will use its default {label}, {d.absent}, for these {noun}." if d.absent else ""


def duplicate_rows(cells: list[list[str]], positions: list[int]) -> list[ExcludedRow]:
    """Lignes identiques à une précédente sur TOUTES les colonnes importées (doublon exact, ou différence
    seulement dans une colonne non importée, ex. une remarque) : la première est importée, les suivantes sont
    listées, jamais fusionnées ni devinées."""
    first: dict[tuple[str, ...], int] = {}
    out: list[ExcludedRow] = []
    height = len(cells[0]) if cells else 0
    for i in range(height):
        key = tuple(unwrap(cells[pos][i])[0] for pos in positions)
        if not any(key):
            continue
        if key in first:
            out.append(
                ExcludedRow(
                    row=i + 1,
                    reason=f"Same data as row {first[key] + 1} of your file: imported once (duplicate row).",
                    kind="duplicate",
                )
            )
        else:
            first[key] = i
    return out


def _slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", ascii_text.lower()).strip("_") or "x"


def translate_units(  # noqa: PLR0913
    f: TargetField,
    column: str,
    values: list[str],
    units: UnitSettings,
    skip: set[int],
    package_choice: str | None,
) -> tuple[list[str], ValueTransform | None, ValueIssue | None]:
    """Valeurs d'une référence d'unité → nom de l'unité dans la cible (vocabulaire des unités + domain pack) ;
    unité d'emballage → pièce seulement si l'utilisateur l'a choisi ; une valeur inconnue de la cible est
    gardée et signalée (la cible la refuserait), jamais remplacée en silence."""
    known = set(units.unit_names.values())
    out, pairs, unknown = list(values), [], []
    for i, v in enumerate(values):
        if not v or i in skip:
            continue
        code = unit_code(v)
        target = units.unit_names.get(code) if code else None
        if target is None and is_package(v) and package_choice == UNITS_AS_PIECE:
            target = units.unit_names.get("unit")
        if target is None:
            if v not in known:
                unknown.append(i)
            continue
        if target != v:
            pairs.append([v, target])
        out[i] = target
    transform = (
        ValueTransform(
            field=f.name,
            column=column,
            kind="unit_name",
            label="Unit written with the name used by the target",
            count=len(pairs),
            examples=[[_clip(a), b] for a, b in list({p[0]: p for p in pairs}.values())[:EXAMPLES]],
        )
        if pairs
        else None
    )
    issue = None
    if unknown:
        seen = list(dict.fromkeys(values[i] for i in unknown))
        issue = ValueIssue(
            field=f.name,
            column=column,
            kind="unit_unknown",
            count=len(unknown),
            examples=[_clip(x) for x in seen[:EXAMPLES]],
            message=say(
                "{n} [row uses a unit|rows use units] unknown to the target ({units}): the import would "
                "refuse [it|them].",
                len(unknown),
                units=", ".join(seen[:5]),
            ),
            rows=rows_of(unknown),
            fix="Count them as pieces (question about units), or write a unit the target knows "
            f"({', '.join(list(dict.fromkeys(units.unit_names.values()))[:4])}…).",
            severity="to_check",
        )
    return out, transform, issue


def _xmlid_prefix(entity: str) -> str:
    return f"__import__.nexa45_{_slug(entity)}_"


XMLID_PART = 60  # au-delà : début du texte + empreinte (un identifiant reste lisible et court)


def _xmlid_part(text: str) -> str:
    slug = _slug(text)
    if len(slug) <= XMLID_PART:
        return slug
    return f"{slug[:XMLID_PART].rstrip('_')}_{hashlib.sha1(text.encode()).hexdigest()[:8]}"


def record_ids(identity: RecordIdentity, headers: list[str], rows: list[list[str]]) -> list[str]:
    """Un identifiant externe par ligne, dérivé de SES valeurs (jamais du rang seul, jamais d'un identifiant
    du système) : référence interne, sinon nom, sinon rang. Deux références différentes que le texte réduit
    rendrait identiques (« A-1 », « A 1 ») sont départagées par une empreinte de la valeur ; une même valeur
    sur plusieurs lignes reçoit _2, _3… dans l'ordre du fichier (stable pour le même fichier)."""
    prefix = _xmlid_prefix(identity.entity)
    col = {h: i for i, h in enumerate(headers)}

    def value(row: list[str], field: str | None) -> str:
        i = col.get(field) if field else None
        return row[i].strip() if i is not None and i < len(row) else ""

    raws: list[tuple[str, str]] = []
    for n, row in enumerate(rows, start=1):
        if key := value(row, identity.key_field):
            raws.append(("", key))
        elif name := value(row, identity.name_field):
            raws.append(("name_", name))
        else:
            raws.append(("row_", str(n)))
    sources: dict[str, set[str]] = {}
    for kind, raw in raws:
        sources.setdefault(kind + _xmlid_part(raw), set()).add(raw)
    out: list[str] = []
    seen: Counter[str] = Counter()
    for kind, raw in raws:
        base = kind + _xmlid_part(raw)
        if len(sources[base]) > 1:
            base = f"{base}_{hashlib.sha1(raw.encode()).hexdigest()[:8]}"
        seen[base] += 1
        out.append(prefix + base + (f"_{seen[base]}" if seen[base] > 1 else ""))
    return out


def records_sheet(spec: RecordSheet, paths: list[list[str]]) -> CreatedRecords:
    """Un enregistrement par niveau de chaque chemin, parents d'abord, identifiants externes stables
    (dérivés du chemin, jamais d'un identifiant du système)."""
    ids: dict[tuple[str, ...], str] = {}
    used: set[str] = set()
    rows: list[list[str]] = []
    prefix = _xmlid_prefix(spec.entity)
    for path in paths:
        for depth in range(1, len(path) + 1):
            key = tuple(path[:depth])
            if key in ids:
                continue
            base = prefix + _slug("_".join(key))
            xmlid, n = base, 2
            while xmlid in used:
                xmlid, n = f"{base}_{n}", n + 1
            used.add(xmlid)
            ids[key] = xmlid
            parent = ids.get(key[:-1], "") if depth > 1 else ""
            rows.append([xmlid, key[-1], parent] if spec.parent_field else [xmlid, key[-1]])
    headers = ["id", spec.name_field] + ([f"{spec.parent_field}/id"] if spec.parent_field else [])
    return CreatedRecords(field=spec.field, label=spec.label, entity=spec.entity, headers=headers, rows=rows)


def kept_rows(summary: PreparedFile, rows: list[list[str]]) -> list[list[str]]:
    """Lignes écrites dans le fichier préparé (sans les lignes listées comme non importées)."""
    out = {e.row - 1 for e in summary.excluded_rows}
    return [r for i, r in enumerate(rows) if i not in out]


PREPARED_LABEL = {
    "to_complete": "Not ready: required fields are still to complete",
    "prepared": "Prepared file, not yet ready for import: see the checks below and the sheet “Problems”",
    "ready": "Ready for import: every check below passed",
}

PREPARED_STATUS = {
    Verification.VERIFIED: "Target structure: verified schema. Check the values before any import.",
    Verification.PARTIAL: "Target structure: partly verified schema. Check it against the official import "
    "template of the target system before any import.",
    Verification.AI: "Target structure: AI proposal, not a verified import schema. Check it against the "
    "official import template of the target system before any import.",
}


def render_prepared(
    proposal: TargetProposal,
    summary: PreparedFile,
    rows: list[list[str]],
    mapping: list[tuple[str, str]],
    now: datetime | None = None,
    identity: RecordIdentity | None = None,
) -> bytes:
    """Fichier préparé : « Data » (données du client), « Mapping » (colonne → champ) et « Read me ». Avec
    `identity`, la première colonne « id » donne à chaque ligne un identifiant externe stable."""
    now = now or datetime.now(UTC)
    rows = kept_rows(summary, rows)
    ids = record_ids(identity, summary.headers, rows) if identity else []
    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(
        buf, {"in_memory": True, "strings_to_numbers": False, "strings_to_formulas": False}
    )
    bold = wb.add_format({"bold": True, "bg_color": "#E8EFFB", "border": 1})
    wrap = wb.add_format({"text_wrap": True, "valign": "top"})
    data = wb.add_worksheet("Data")
    # une colonne entièrement vide n'est PAS écrite : l'import de fichier de la cible lirait « vide » (ex. une
    # case à cocher décochée) au lieu d'appliquer sa valeur par défaut ; elle est listée dans « Read me »
    kept = [i for i in range(len(summary.headers)) if any(i < len(r) and r[i] for r in rows)]
    numeric = {f.name for f in proposal.fields if f.type.casefold() in NUMERIC_TYPES}
    shift = 1 if ids else 0
    if ids:
        data.write_string(0, 0, ID_COLUMN, bold)
        data.set_column(0, 0, 40)
        for r, xmlid in enumerate(ids, start=1):
            data.write_string(r, 0, xmlid)
    for col, i in enumerate(kept, start=shift):
        h = summary.headers[i]
        data.write_string(0, col, h, bold)
        data.set_column(col, col, max(12, min(40, len(h) + 4)))
    for r, values in enumerate(rows, start=1):
        for col, i in enumerate(kept, start=shift):
            value = values[i] if i < len(values) else ""
            if not value:
                continue
            if summary.headers[i] in numeric and _PLAIN_NUMBER.match(value):
                # nombre écrit comme NOMBRE (même valeur) ; tout autre format reste tel quel, en texte
                data.write_number(r, col, float(value) if "." in value else int(value))
            else:
                data.write_string(r, col, value)
    data.freeze_panes(1, 0)
    empty = [summary.headers[i] for i in range(len(summary.headers)) if i not in kept]
    sheet = wb.add_worksheet("Mapping")
    for col, h in enumerate(["Your column", "Target field"]):
        sheet.write_string(0, col, h, bold)
    for r, (src, dst) in enumerate(mapping, start=1):
        sheet.write_string(r, 0, src)
        sheet.write_string(r, 1, dst)
    sheet.set_column(0, 1, 36)
    about = wb.add_worksheet("Read me")
    about.set_column(0, 0, 26)
    about.set_column(1, 1, 90)
    lines = [
        ("Structure", proposal.title),
        ("Status", PREPARED_STATUS[proposal.verification]),
        (
            "Your data",
            f"{number(summary.rows)} of {count(summary.source_rows or summary.rows, 'row')} from your file; "
            "values are copied without conversion (only their format is corrected, see below).",
        ),
        (
            "Mapped fields",
            f"{summary.mapped} of {len(summary.headers)} target fields have a column of your file.",
        ),
        (
            "Record identifiers",
            f"Column “{ID_COLUMN}”: one stable identifier per row, from "
            f"{identity_source(proposal, identity)}. Importing this file again updates these records "
            "instead of creating them twice."
            if identity
            else "None",
        ),
        ("Import status", PREPARED_LABEL.get(summary.status, PREPARED_LABEL["prepared"])),
        *((f"Check: {c['label']}", "Yes" if c["ok"] else "No") for c in summary.checks),
        ("To complete", ", ".join(summary.missing_required) or "None"),
        (
            "Set by the target system",
            " | ".join(f"{x['label']}: from {x['source']}" for x in summary.set_by_target) or "None",
        ),
        (
            "Filled from your answers",
            ", ".join(f"{a['field']} = {a['value']}" for a in summary.auto_filled) or "None",
        ),
        ("Completed by you", ", ".join(summary.completed) or "None"),
        (
            "Not imported from your file",
            (", ".join(summary.not_imported) + ": these columns have values but go to no target field.")
            if summary.not_imported
            else "None",
        ),
        (
            "Left out (no value)",
            (", ".join(empty) + ": left out so the target applies its own default values.")
            if empty
            else "None",
        ),
        (
            "Values not written",
            " | ".join(i.message for i in summary.value_issues if i.severity == "not_written") or "None",
        ),
        (
            "To check before import",
            " | ".join(i.message for i in summary.value_issues if i.severity != "not_written") or "None",
        ),
        (
            "Problems",
            "Rows and how to fix each problem: see the sheet “Problems”." if summary.value_issues else "None",
        ),
        (
            "Transformations",
            " | ".join(f"{t.label} ({t.column}): {t.count}" for t in summary.value_transforms) or "None",
        ),
        (
            "Your decisions",
            " | ".join(f"{q.title}: {q.answer or 'not decided'}" for q in summary.value_questions) or "None",
        ),
        (
            "Currency",
            f"Amounts kept in {summary.currency}; never converted."
            if summary.currency
            else (
                "Not chosen: amounts are not written."
                if any(q.kind == "currency" for q in summary.value_questions)
                else "No currency in your file."
            ),
        ),
        (
            "Converted values",
            f"{count(len(summary.value_conversions), 'value')} converted to the unit you confirmed, with "
            "exact factors: see the sheet “Converted values” (source value kept next to the written value)."
            if summary.value_conversions
            else "None",
        ),
        (
            "Rows not imported",
            f"{count(len(summary.excluded_rows), 'row')}: see the sheet “Rows not imported”."
            if summary.excluded_rows
            else "None",
        ),
        (
            "Import first",
            " | ".join(
                f"{c.label}: {count(len(c.rows), 'record')} to create, sheet “{import_first_sheet(c)}”, to "
                f"import into {c.entity} BEFORE the sheet “Data”"
                for c in summary.created_records
            )
            or "Nothing",
        ),
        (
            "Target default",
            ", ".join(summary.left_to_target)
            + ": left empty, the target applies its default value (your choice)."
            if summary.left_to_target
            else "None",
        ),
        ("Warnings", " | ".join(summary.warnings) or "None"),
        ("Generated", now.strftime("%Y-%m-%d %H:%M UTC") + " by the Nexa45 AI Migration Wizard"),
    ]
    for r, (k, v) in enumerate(lines):
        about.write_string(r, 0, k, bold)
        about.write_string(r, 1, v, wrap)
    if summary.value_issues:
        problems = wb.add_worksheet("Problems")
        heads = ["Type", "Target field", "Your column", "Problem", "Rows in your file", "How to fix"]
        for col, h in enumerate(heads):
            problems.write_string(0, col, h, bold)
        for r, i in enumerate(summary.value_issues, start=1):
            shown = ", ".join(str(n) for n in i.rows)
            more = i.count - len(i.rows) if i.rows else 0
            cells = [
                "Not written" if i.severity == "not_written" else "To check",
                i.field,
                i.column,
                i.message,
                shown + (f" (+{more} more)" if more > 0 else ""),
                i.fix,
            ]
            for col, v in enumerate(cells):
                problems.write_string(r, col, v, wrap)
        problems.set_column(0, 2, 18)
        problems.set_column(3, 3, 60)
        problems.set_column(4, 4, 30)
        problems.set_column(5, 5, 60)
        problems.freeze_panes(1, 0)
    if summary.value_conversions:
        conv = wb.add_worksheet("Converted values")
        for col, h in enumerate(
            ["Row in your file", "Your column", "Target field", "Your value", "Written value"]
        ):
            conv.write_string(0, col, h, bold)
        for r, c in enumerate(summary.value_conversions, start=1):
            conv.write_number(r, 0, c.row)
            conv.write_string(r, 1, c.column)
            conv.write_string(r, 2, c.field)
            conv.write_string(r, 3, c.source)
            conv.write_string(r, 4, c.written)
        conv.set_column(0, 4, 22)
    for c in summary.created_records:
        sheet = wb.add_worksheet(import_first_sheet(c))
        for col, h in enumerate(c.headers):
            sheet.write_string(0, col, h, bold)
        for r, row in enumerate(c.rows, start=1):
            for col, v in enumerate(row):
                sheet.write_string(r, col, v)
        sheet.set_column(0, len(c.headers) - 1, 40)
    if summary.excluded_rows:
        left = wb.add_worksheet("Rows not imported")
        for col, h in enumerate(["Row in your file", "Reason"]):
            left.write_string(0, col, h, bold)
        for r, e in enumerate(summary.excluded_rows, start=1):
            left.write_number(r, 0, e.row)
            left.write_string(r, 1, e.reason)
        left.set_column(0, 0, 18)
        left.set_column(1, 1, 80)
    wb.close()
    return buf.getvalue()


ID_COLUMN = "id"  # identifiant externe, même en-tête que les feuilles « (import first) »


def identity_source(proposal: TargetProposal, identity: RecordIdentity) -> str:
    label = {f.name: f.label for f in proposal.fields}
    key = label.get(identity.key_field or "")
    name = label.get(identity.name_field or "")
    if key and name:
        return f"{key} (otherwise {name})"
    return key or name or "the row"


def import_first_sheet(c: CreatedRecords) -> str:
    """Nom de feuille Excel (31 caractères au plus, sans caractères interdits)."""
    return re.sub(r"[\[\]:*?/\\]", " ", f"{c.label} (import first)")[:31]


def prepared_filename(proposal: TargetProposal) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", proposal.title).strip("_")[:80] or "target_structure"
    return f"{slug}_prepared.xlsx"


__all__ = [
    "AI_MATCH",
    "AI_SUGGEST",
    "ColumnMapping",
    "ColumnStatus",
    "FileMapping",
    "MappingDecision",
    "MappingError",
    "PreparedFile",
    "PreviousChoice",
    "RecordIdentity",
    "ValueConversion",
    "WizardColumnProposer",
    "WizardColumnsRequest",
    "check_decisions",
    "kept_rows",
    "map_columns",
    "prepare",
    "prepared_filename",
    "record_ids",
    "render_prepared",
]
