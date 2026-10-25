"""Lecture et profilage d'un fichier catalogue (V2c.1) : INGEST uniquement (Polars, cf. architecture).

On ne retient que la FORME du fichier : colonnes (nom, position, type détecté, nombre de valeurs) et nombre de
lignes. Aucune valeur de cellule n'est écrite en base : le fichier original reste dans le FileStore. Seule
exception, EN MÉMOIRE pendant l'analyse : quelques valeurs d'exemple par colonne (`examples`), que le fallback
LLM masque avant tout envoi (V2c.3) ; jamais persistées.
"""

from __future__ import annotations

import csv
import io
import re
import warnings
from enum import StrEnum
from pathlib import PurePath

import polars as pl
from pydantic import BaseModel, ConfigDict

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_ROWS = 20_000
MAX_COLUMNS = 200
EXTENSIONS = {".csv": "csv", ".xlsx": "xlsx"}
EXAMPLES = 3  # valeurs d'exemple distinctes par colonne (en mémoire seulement)
TYPE_SHARE = 0.95  # part minimale des valeurs non vides pour retenir un type (le reste = valeurs invalides)


class ImportFileError(ValueError):
    """Fichier illisible, vide, trop gros ou d'un format non pris en charge (message simple, anglais)."""


class ColumnType(StrEnum):
    TEXT = "text"
    INTEGER = "integer"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    DATE = "date"
    EMPTY = "empty"


class ColumnProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    position: int
    name: str
    detected_type: ColumnType
    filled: int  # cellules non vides
    invalid: int = 0  # valeurs minoritaires qui ne sont pas du type détecté (utile à l'import réel)
    examples: list[str] = []  # quelques valeurs distinctes (analyse seulement, jamais écrites en base)


class FileProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    file_format: str  # csv | xlsx
    row_count: int
    columns: list[ColumnProfile]


def file_format(filename: str) -> str:
    fmt = EXTENSIONS.get(PurePath(filename).suffix.lower())
    if fmt is None:
        raise ImportFileError("Please upload an .xlsx or .csv file.")
    return fmt


def read_table(data: bytes, filename: str) -> tuple[str, pl.DataFrame]:
    """Fichier → DataFrame (première feuille d'un .xlsx). La ligne d'en-têtes est DÉTECTÉE (ligne de titre ou
    lignes vides au-dessus sautées) ; les lignes entièrement vides et les lignes de total (« Summe »,
    « Total »…) sont retirées."""
    fmt = file_format(filename)
    if not data:
        raise ImportFileError("The file is empty.")
    if len(data) > MAX_FILE_BYTES:
        raise ImportFileError(f"The file is too large (maximum {MAX_FILE_BYTES // (1024 * 1024)} MB).")
    try:
        df = _read_csv(data) if fmt == "csv" else _read_xlsx(data)
    except ImportFileError:
        raise
    except Exception as exc:  # format corrompu : jamais le détail technique côté fournisseur
        raise ImportFileError("We couldn't read this file. Please check its format.") from exc
    df = df.filter(~pl.all_horizontal(pl.all().is_null()))
    df = _without_totals(df)
    if df.width == 0 or df.height == 0:
        raise ImportFileError("The file has no data rows.")
    if df.width > MAX_COLUMNS:
        raise ImportFileError(f"The file has too many columns (maximum {MAX_COLUMNS}).")
    if df.height > MAX_ROWS:
        raise ImportFileError(f"The file has too many rows (maximum {MAX_ROWS:,}).")
    return fmt, df


def profile_file(data: bytes, filename: str) -> FileProfile:
    fmt, df = read_table(data, filename)
    columns = []
    for i, name in enumerate(df.columns):
        col = df.get_column(name)
        detected, invalid = _detect(col)
        columns.append(
            ColumnProfile(
                position=i,
                name=str(name).strip(),
                detected_type=detected,
                filled=int(col.drop_nulls().len()),
                invalid=invalid,
                examples=_examples(col),
            )
        )
    return FileProfile(file_format=fmt, row_count=df.height, columns=columns)


def _read_xlsx(data: bytes) -> pl.DataFrame:
    with warnings.catch_warnings():  # avertissement interne Polars ↔ fastexcel (from_arrow), sans effet ici
        warnings.simplefilter("ignore", FutureWarning)
        df = pl.read_excel(io.BytesIO(data), engine="calamine")
        named = [c for c in df.columns if c.strip() and not c.startswith("__UNNAMED__")]
        if len(named) * 2 >= df.width or df.width < 2:
            return df  # en-têtes en ligne 1 (cas courant) : types lus par la feuille, inchangés
        # ligne de titre au-dessus des en-têtes : relecture sans en-tête, tout en texte
        raw = pl.read_excel(io.BytesIO(data), engine="calamine", has_header=False)
    grid = [[_text(v) for v in row] for row in raw.iter_rows()]
    return _from_grid(grid)


def _read_csv(data: bytes) -> pl.DataFrame:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")  # export Excel « CSV » classique sous Windows
    first = text.split("\n", 1)[0]
    try:
        sep = csv.Sniffer().sniff(first, delimiters=",;\t|").delimiter
    except csv.Error:
        sep = ","
    if "\n" in text:  # séparateur sur la ligne d'en-têtes plutôt que sur une éventuelle ligne de titre
        lines = [ln for ln in text.splitlines()[:HEADER_SCAN] if ln.strip()]
        best = max(lines, key=lambda ln: max(ln.count(d) for d in ",;\t|"), default=first)
        try:
            sep = csv.Sniffer().sniff(best, delimiters=",;\t|").delimiter
        except csv.Error:
            pass
    # tout en texte : le type est détecté ensuite (un code-barres garde ses zéros)
    raw = pl.read_csv(
        io.BytesIO(text.encode("utf-8")),
        separator=sep,
        has_header=False,
        infer_schema=False,
        truncate_ragged_lines=True,
    )
    return _from_grid([[_text(v) for v in row] for row in raw.iter_rows()])


HEADER_SCAN = 20  # lignes examinées pour trouver la ligne d'en-têtes
_TOTAL = re.compile(
    r"^(summe|gesamt|gesamtsumme|zwischensumme|insgesamt|total|totals|grand total|subtotal|sum)\s*:?$", re.I
)


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def header_row(grid: list[list[str]]) -> int:
    """Indice de la ligne d'en-têtes : la première (parmi les HEADER_SCAN premières) qui remplit au moins la
    moitié des colonnes du fichier ; une ligne de titre (une seule cellule) ou vide est sautée. Ligne 1 par
    défaut."""
    width = max((sum(1 for c in row if c) for row in grid[:HEADER_SCAN]), default=0)
    need = max(1 if width < 2 else 2, -(-width // 2))
    return next((i for i, row in enumerate(grid[:HEADER_SCAN]) if sum(1 for c in row if c) >= need), 0)


def _from_grid(grid: list[list[str]]) -> pl.DataFrame:
    """Grille de texte → DataFrame : en-têtes détectés, noms vides ou en double rendus uniques."""
    if not grid:
        return pl.DataFrame()
    start = header_row(grid)
    width = max(len(r) for r in grid)
    names: list[str] = []
    seen: dict[str, int] = {}
    for i, cell in enumerate((grid[start] + [""] * width)[:width]):
        name = cell or f"Column {i + 1}"
        seen[name] = seen.get(name, 0) + 1
        names.append(name if seen[name] == 1 else f"{name} ({seen[name]})")
    rows = [(r + [""] * width)[:width] for r in grid[start + 1 :]]
    data = {n: [row[i] or None for row in rows] for i, n in enumerate(names)}
    return pl.DataFrame(data, schema={n: pl.String for n in names})


def _without_totals(df: pl.DataFrame) -> pl.DataFrame:
    """Lignes de total retirées : première cellule remplie égale à « Summe », « Total: »… (la cellule
    entière : un article nommé « Total care kit » reste un article) et au moins une cellule vide (un total ne
    porte que quelques sommes)."""
    keep = []
    for row in df.iter_rows():
        cells = [_text(v) for v in row]
        filled = [c for c in cells if c]
        keep.append(not (filled and _TOTAL.match(filled[0]) and len(filled) < len(cells)))
    return df.filter(pl.Series(keep)) if not all(keep) else df


_INT = re.compile(r"^[+-]?\d+$")
_DEC = re.compile(r"^[+-]?(\d+([.,]\d+)?|\d{1,3}([ .,]\d{3})+([.,]\d+)?)$")
_BOOL = frozenset({"true", "false", "yes", "no", "y", "n"})
_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2}|\d{1,2}[./]\d{1,2}[./]\d{2,4})([ T]\d{1,2}:\d{2}(:\d{2})?)?$")


def _examples(col: pl.Series) -> list[str]:
    out: list[str] = []
    for v in col.drop_nulls().to_list():
        text = str(v).strip()[:200]
        if text and text not in out:
            out.append(text)
            if len(out) == EXAMPLES:
                break
    return out


def _detect(col: pl.Series) -> tuple[ColumnType, int]:
    """Type retenu = celui d'au moins 95 % des valeurs non vides ; les autres sont comptées comme invalides
    (une valeur mal saisie ne change pas le type de toute la colonne)."""
    values = col.drop_nulls()
    n = values.len()
    if n == 0:
        return ColumnType.EMPTY, 0
    dtype = values.dtype
    if dtype == pl.Boolean:
        return ColumnType.BOOLEAN, 0
    if dtype.is_integer():
        return ColumnType.INTEGER, 0
    if dtype.is_float():
        # .xlsx : un entier saisi dans Excel est lu en flottant
        whole = bool((values == values.round(0)).all())
        return (ColumnType.INTEGER if whole else ColumnType.DECIMAL), 0
    if dtype.is_temporal():
        return ColumnType.DATE, 0
    texts = [str(v).strip() for v in values.to_list()]
    ints = sum(1 for t in texts if _INT.match(t))
    numbers = sum(1 for t in texts if _INT.match(t) or _DEC.match(t))
    if numbers >= TYPE_SHARE * n:
        return (ColumnType.INTEGER if ints == numbers else ColumnType.DECIMAL), n - numbers
    for kind, ok in (
        (ColumnType.BOOLEAN, lambda t: t.lower() in _BOOL),
        (ColumnType.DATE, lambda t: bool(_DATE.match(t))),
    ):
        good = sum(1 for t in texts if ok(t))
        if good >= TYPE_SHARE * n:
            return kind, n - good
    return ColumnType.TEXT, 0
