"""Plan d'import d'un fichier catalogue (V2d.1) : ce que UIE essaierait d'importer, sans rien écrire.

    décisions validées de l'import + cibles COURANTES de l'instance
        → build_import_plan → ImportPlan (colonnes importées, sort de chaque colonne)
    fichier (lu une fois) → normalize_rows → NormalizedRow (valeurs prêtes, problèmes par ligne)
        → summarize → ImportPreview (agrégats pour l'écran)

PUR : aucune session, aucun réseau, aucun LLM, aucune analyse sémantique. Le preview (V2d.1) et l'import réel
(V2d.2) partagent cette préparation. Aucun nom de système : entités, champs, types, sélections et caractère
obligatoire viennent des bindings de l'instance (via les cibles d'import).

Validations déterministes seulement : nombres, booléens, dates (mêmes reconnaissances que la
détection de type), valeurs d'une sélection, adresse d'image `http(s)://`. Une valeur invalide
n'est pas écrite (le reste de la ligne l'est). Seul un champ OBLIGATOIRE SANS VALEUR IMPLICITE
vide rend la ligne non importable : obligatoire dans le système ET texte (une référence, une
sélection, un nombre ou un booléen obligatoires ont une valeur par défaut côté système). Une clé
`required_for_catalog` du vocabulaire vide n'est qu'un avertissement.
Références (catégorie, unité…), tags et images restent des données préparées : jamais résolus, créés, lus ni
téléchargés ici.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from uie_core.domains.semantics import SemanticSpec
from uie_core.imports.matching import IDENTITY_KEYS, ColumnStatus, ImportTarget, ReviewReason
from uie_core.imports.profile import _DATE, _DEC, _INT, ColumnType

TEXT_TYPES = {"string", "text", "json"}
SAMPLE_ROWS = 5  # numéros de ligne montrés par problème


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ColumnOutcome(StrEnum):
    IMPORTED = "IMPORTED"
    IGNORED = "IGNORED"  # ignorée par le fournisseur
    NO_TARGET = "NO_TARGET"  # aucune cible dans le système (sans correspondance, ou sens connu sans champ)
    EMPTY = "EMPTY"  # aucune valeur dans le fichier
    # seule cause d'indisponibilité : la cible validée n'existe plus dans le système (décision ou
    # analyse changée). Le type DÉTECTÉ de la colonne (estimation sur toute la colonne) n'annule jamais
    # une décision validée : les valeurs sont vérifiées ligne par ligne (`normalize_rows`).
    TARGET_GONE = "TARGET_GONE"


class IssueCode(StrEnum):
    MISSING_REQUIRED = "MISSING_REQUIRED"  # bloquant : la ligne n'est pas importable
    MISSING_RECOMMENDED = "MISSING_RECOMMENDED"
    INVALID_NUMBER = "INVALID_NUMBER"
    INVALID_BOOLEAN = "INVALID_BOOLEAN"
    INVALID_DATE = "INVALID_DATE"
    INVALID_CHOICE = "INVALID_CHOICE"
    INVALID_URL = "INVALID_URL"
    NO_COLUMN_FOR_REQUIRED = "NO_COLUMN_FOR_REQUIRED"  # plan : aucune ligne importable


BLOCKING = {IssueCode.MISSING_REQUIRED, IssueCode.NO_COLUMN_FOR_REQUIRED}
# valeur PRÉSENTE mais invalide (≠ valeur manquante)
INVALID = {
    IssueCode.INVALID_NUMBER,
    IssueCode.INVALID_BOOLEAN,
    IssueCode.INVALID_DATE,
    IssueCode.INVALID_CHOICE,
    IssueCode.INVALID_URL,
}


class PlannedColumn(_Model):
    position: int
    source_name: str
    target_id: str  # « groupe:clé »
    label: str  # libellé métier
    group: str  # product | supplier
    entity: str  # technique : V2d.2 seulement, jamais exposé
    field: str
    data_type: str
    kind: str  # value | label | labels | link | media
    required: bool  # obligatoire sans valeur implicite (voir la docstring du module)
    recommended: bool  # `required_for_catalog` du vocabulaire
    selection: list[list[str]] | None = None
    relation_entity: str | None = None  # entité d'une référence / collection (V2d.2)
    identity: bool = False  # identité de mise à jour (V2d.2, `IDENTITY_KEYS`)


class ColumnFate(_Model):
    position: int
    source_name: str
    outcome: ColumnOutcome
    label: str | None = None  # libellé de la cible (colonne importée)


class PlanIssue(_Model):
    code: IssueCode
    target_id: str
    label: str


class ImportPlan(_Model):
    import_id: uuid.UUID
    instance_id: uuid.UUID
    file_sha256: str
    columns: list[PlannedColumn]
    fates: list[ColumnFate]  # une par colonne du fichier, dans l'ordre
    issues: list[PlanIssue] = []

    @property
    def checksum(self) -> str:
        """Même import + mêmes décisions + mêmes cibles + même fichier → même plan."""
        raw = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# valeurs préparées (non résolues)
# --------------------------------------------------------------------------- #
class Ref(_Model):
    """Référence donnée par son libellé (catégorie, unité, fournisseur…) : résolue à l'import réel."""

    kind: Literal["ref"] = "ref"
    label: str


class Labels(_Model):
    """Collection donnée par libellés (tags) : résolue à l'import réel."""

    kind: Literal["labels"] = "labels"
    labels: list[str]


class MediaUrl(_Model):
    """Image donnée par son adresse : jamais téléchargée ici."""

    kind: Literal["media_url"] = "media_url"
    url: str


Value = str | float | int | bool | Ref | Labels | MediaUrl


class RowIssue(_Model):
    position: int | None  # colonne source (None : problème du plan)
    target_id: str
    code: IssueCode


class NormalizedRow(_Model):
    source_row: int  # n° de ligne de produit dans le fichier (1 = première ligne de données)
    records: dict[str, dict[str, Value]]  # groupe → champ technique → valeur préparée (cellule vide : absent)
    issues: list[RowIssue] = []

    @property
    def importable(self) -> bool:
        return not any(i.code in BLOCKING for i in self.issues)


# --------------------------------------------------------------------------- #
# plan
# --------------------------------------------------------------------------- #
class ValidatedColumn(_Model):
    """Décision validée d'une colonne (lue sur l'import, jamais recalculée)."""

    position: int
    source_name: str
    detected_type: ColumnType
    status: ColumnStatus
    reason: str | None = None
    ignored: bool
    target_id: str | None = None


def build_import_plan(
    *,
    import_id: uuid.UUID,
    instance_id: uuid.UUID,
    file_sha256: str,
    decisions: Sequence[ValidatedColumn],
    targets: Sequence[ImportTarget],
    spec: SemanticSpec,
) -> ImportPlan:
    by_id = {t.id: t for t in targets}
    columns: list[PlannedColumn] = []
    fates: list[ColumnFate] = []
    for d in sorted(decisions, key=lambda d: d.position):
        if d.status == ColumnStatus.EMPTY:
            fates.append(
                ColumnFate(position=d.position, source_name=d.source_name, outcome=ColumnOutcome.EMPTY)
            )
            continue
        if d.ignored:
            no_target = d.status == ColumnStatus.UNMATCHED or d.reason == ReviewReason.NOT_IN_SYSTEM
            outcome = ColumnOutcome.NO_TARGET if no_target else ColumnOutcome.IGNORED
            fates.append(ColumnFate(position=d.position, source_name=d.source_name, outcome=outcome))
            continue
        target = by_id.get(d.target_id or "")
        if d.status != ColumnStatus.MATCHED or target is None:
            fates.append(
                ColumnFate(position=d.position, source_name=d.source_name, outcome=ColumnOutcome.TARGET_GONE)
            )
            continue
        label = _label(target, spec)
        columns.append(_planned(d, target, label, spec))
        fates.append(
            ColumnFate(position=d.position, source_name=d.source_name, outcome=ColumnOutcome.IMPORTED)
        )
    issues = [
        PlanIssue(code=IssueCode.NO_COLUMN_FOR_REQUIRED, target_id=t.id, label=_label(t, spec))
        for t in targets
        if t.group == "product" and _required(t) and not any(c.target_id == t.id for c in columns)
    ]
    return ImportPlan(
        import_id=import_id,
        instance_id=instance_id,
        file_sha256=file_sha256,
        columns=columns,
        fates=fates,
        issues=issues,
    )


def _required(t: ImportTarget) -> bool:
    """Obligatoire SANS valeur implicite : obligatoire dans le système et texte."""
    return t.required and t.data_type in TEXT_TYPES and t.kind == "value"


def _label(t: ImportTarget, spec: SemanticSpec) -> str:
    entry = spec.vocabulary.get(t.semantic_key)
    return entry.label if entry else t.label


def _planned(d: ValidatedColumn, t: ImportTarget, label: str, spec: SemanticSpec) -> PlannedColumn:
    entry = spec.vocabulary.get(t.semantic_key)
    return PlannedColumn(
        position=d.position,
        source_name=d.source_name,
        target_id=t.id,
        label=label,
        group=t.group,
        entity=t.entity,
        field=t.field,
        data_type=t.data_type,
        kind=t.kind,
        required=_required(t),
        recommended=bool(entry and entry.required_for_catalog),
        selection=t.selection,
        relation_entity=t.relation,
        identity=t.semantic_key in IDENTITY_KEYS,
    )


# --------------------------------------------------------------------------- #
# lignes
# --------------------------------------------------------------------------- #
def normalize_rows(rows: Iterable[Sequence[Any]], plan: ImportPlan) -> list[NormalizedRow]:
    """Lignes du fichier (valeurs par position de colonne) → lignes préparées. Déterministe."""
    blocking_plan = [
        RowIssue(position=None, target_id=i.target_id, code=i.code) for i in plan.issues if i.code in BLOCKING
    ]
    out: list[NormalizedRow] = []
    for n, row in enumerate(rows, start=1):
        records: dict[str, dict[str, Value]] = {}
        issues = list(blocking_plan)
        for col in plan.columns:
            raw = row[col.position] if col.position < len(row) else None
            if _empty(raw):
                if col.required:
                    issues.append(
                        RowIssue(
                            position=col.position, target_id=col.target_id, code=IssueCode.MISSING_REQUIRED
                        )
                    )
                elif col.recommended:
                    issues.append(
                        RowIssue(
                            position=col.position, target_id=col.target_id, code=IssueCode.MISSING_RECOMMENDED
                        )
                    )
                continue
            value, code = _convert(raw, col)
            if code is not None:
                issues.append(RowIssue(position=col.position, target_id=col.target_id, code=code))
                continue
            records.setdefault(col.group, {})[col.field] = value
        out.append(NormalizedRow(source_row=n, records=records, issues=issues))
    return out


def _empty(raw: Any) -> bool:
    return raw is None or (isinstance(raw, str) and not raw.strip())


_URL = re.compile(r"^https?://\S+$", re.IGNORECASE)
_TRUE, _FALSE = {"true", "yes", "y", "1"}, {"false", "no", "n", "0"}


def _convert(raw: Any, col: PlannedColumn) -> tuple[Value | None, IssueCode | None]:  # noqa: PLR0911
    text = raw.strip() if isinstance(raw, str) else raw
    if col.kind == "media":
        url = str(text)
        return (MediaUrl(url=url), None) if _URL.match(url) else (None, IssueCode.INVALID_URL)
    if col.kind == "labels":
        labels = list(dict.fromkeys(p.strip() for p in str(text).split(",") if p.strip()))
        return Labels(labels=labels), None
    if col.kind in ("label", "link") or col.data_type == "reference":
        return Ref(label=str(text)), None
    if col.selection:
        return _choice(str(text), col.selection)
    if col.data_type in ("integer", "decimal"):
        number = _number(text)
        if number is None or (col.data_type == "integer" and number != int(number)):
            return None, IssueCode.INVALID_NUMBER
        return (int(number) if col.data_type == "integer" else number), None
    if col.data_type == "boolean":
        if isinstance(text, bool):
            return text, None
        low = str(text).lower()
        if low in _TRUE or low in _FALSE:
            return low in _TRUE, None
        return None, IssueCode.INVALID_BOOLEAN
    if col.data_type in ("date", "datetime"):
        if isinstance(text, datetime | date):
            return text.isoformat(), None
        return (str(text), None) if _DATE.match(str(text)) else (None, IssueCode.INVALID_DATE)
    return str(text), None


def _choice(text: str, selection: list[list[str]]) -> tuple[Value | None, IssueCode | None]:
    low = text.casefold()
    for key, label in selection:
        if low in (str(key).casefold(), str(label).casefold()):
            return str(key), None
    return None, IssueCode.INVALID_CHOICE


def _number(value: Any) -> float | None:
    """Même reconnaissance que la détection de type : « 19.90 », « 19,90 », « 1 299,50 », « 1,299.50 »."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    text = str(value).strip()
    if not (_INT.match(text) or _DEC.match(text)):
        return None
    compact = text.replace(" ", "")
    if "," in compact and "." in compact:  # le dernier séparateur est le séparateur décimal
        decimal = "," if compact.rfind(",") > compact.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        compact = compact.replace(thousands, "").replace(decimal, ".")
    elif compact.count(",") > 1 or compact.count(".") > 1:  # plusieurs groupes : séparateurs de milliers
        compact = compact.replace(",", "").replace(".", "")
    else:
        compact = compact.replace(",", ".")  # une seule virgule : séparateur décimal
    try:
        return float(compact)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# résumé
# --------------------------------------------------------------------------- #
class DataItem(_Model):
    target_id: str
    label: str
    group: str
    kind: str
    rows_present: int  # cellules non vides dans le fichier (valides ou non)
    rows_valid: int  # valeurs qui seraient écrites (présentes ET valides)
    distinct_values: int | None = None  # références et tags (valeurs valides)


class IssueSummary(_Model):
    code: IssueCode
    target_id: str
    label: str
    count: int
    rows: list[int]  # au plus SAMPLE_ROWS numéros de ligne
    blocking: bool


class ImportPreview(_Model):
    plan_checksum: str
    rows: int
    importable_rows: int
    rows_with_issues: int
    data: list[DataItem]
    issues: list[IssueSummary]
    fates: list[ColumnFate]


def summarize(plan: ImportPlan, rows: Sequence[NormalizedRow]) -> ImportPreview:
    data: list[DataItem] = []
    invalid: dict[int, int] = {}  # colonne → lignes où la valeur présente est invalide
    for r in rows:
        for i in r.issues:
            if i.position is not None and i.code in INVALID:
                invalid[i.position] = invalid.get(i.position, 0) + 1
    for col in plan.columns:
        values = [r.records.get(col.group, {}).get(col.field) for r in rows]
        present = [v for v in values if v is not None]  # valeurs valides (écrites)
        distinct = None
        if col.kind == "labels":
            distinct = len({lab.casefold() for v in present if isinstance(v, Labels) for lab in v.labels})
        elif col.kind in ("label", "link") or col.data_type == "reference":
            distinct = len({v.label.casefold() for v in present if isinstance(v, Ref)})
        data.append(
            DataItem(
                target_id=col.target_id,
                label=col.label,
                group=col.group,
                kind=col.kind,
                rows_present=len(present) + invalid.get(col.position, 0),
                rows_valid=len(present),
                distinct_values=distinct,
            )
        )
    labels = {c.target_id: c.label for c in plan.columns} | {i.target_id: i.label for i in plan.issues}
    grouped: dict[tuple[IssueCode, str], list[int]] = {}
    for r in rows:
        for i in r.issues:
            grouped.setdefault((i.code, i.target_id), []).append(r.source_row)
    for i in plan.issues:  # un problème du plan est montré même sans ligne
        grouped.setdefault((i.code, i.target_id), [])
    issues = [
        IssueSummary(
            code=code,
            target_id=tid,
            label=labels.get(tid, tid),
            count=len(lines),
            rows=lines[:SAMPLE_ROWS],
            blocking=code in BLOCKING,
        )
        for (code, tid), lines in sorted(grouped.items(), key=lambda kv: (kv[0][0] not in BLOCKING, kv[0]))
    ]
    return ImportPreview(
        plan_checksum=plan.checksum,
        rows=len(rows),
        importable_rows=sum(1 for r in rows if r.importable),
        rows_with_issues=sum(1 for r in rows if r.issues),
        data=data,
        issues=issues,
        fates=plan.fates,
    )
