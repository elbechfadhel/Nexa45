"""Générateur des specs DATEV (D1) : sources officielles téléchargées LOCALEMENT → `uie-datev-format/1`.

Sources (jamais versionnées : licence de redistribution non établie) :
    portail      contenu public du DATEV Developer Portal (JSON de « DATEV Format ») : tableaux de colonnes
                 (numéro, en-tête, expression régulière) de la version courante et de la ligne d'en-tête
    programme    archive du programme de contrôle DATEV : définitions de format XML par format ET version
                 (libellé, longueur, décimales, type, obligatoire, identifiant du champ)
    exemples     archive des fichiers d'exemple officiels (en-têtes de colonnes réels, version courante)

Les définitions XML sont choisies par leur CONTENU (nom exact du format + version), jamais par leur nom de
fichier.
Libellé canonique = celui du XML (seule source disponible pour TOUTES les versions : Compare reste cohérent) ;
libellés du portail et des exemples gardés en alias. Contrôles croisés (rapport) : nombres de colonnes XML /
portail / exemple, libellés, versions successives « append-only », version du fichier d'exemple.

Usage :
    uv run python -m uie_connectors.datev.spec_builder --portal <datev-format.json> \
        --pruefprogramm <Datev_Format_Pruefprogramm.zip> --samples <Musterdaten.zip> \
        --out var/datev/specs/extf_700 [--report var/datev/specs/report.json]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from uie_connectors.datev.loader import header_type_of
from uie_connectors.datev.spec import DatevField, DatevFormatSpec


@dataclass(frozen=True)
class Target:
    """Un format visé par D1 (codes et versions décidés par l'utilisateur)."""

    code: str  # code produit
    entity: str  # entité du format dans le snapshot
    xml_name: str  # nom exact du format dans les définitions XML
    portal_title: str  # titre de la page du portail (localisation allemande)
    sample: str | None  # fichier d'exemple officiel (version courante)
    versions: tuple[str, ...]


TARGETS = (
    Target(
        "debitoren-kreditoren",
        "debitoren_kreditoren",
        "Debitoren/Kreditoren",
        "Debitoren/Kreditoren",
        "EXTF_DebKred_Stamm.csv",
        ("5",),
    ),
    Target(
        "buchungsstapel",
        "buchungsstapel",
        "Buchungsstapel",
        "Buchungsstapel",
        "EXTF_Buchungsstapel.csv",
        ("10", "11", "12", "13"),
    ),
)


# --------------------------------------------------------------------------- #
# lecture des sources
# --------------------------------------------------------------------------- #
def _tag(block: str, tag: str) -> str | None:
    m = re.search(rf"<{tag}>(.*?)</{tag}>", block, re.S)
    return html.unescape(m.group(1)).strip() if m else None


def read_definitions(archive: Path) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """(nom du format, version) → définitions `Format_*.xml` {fichier, sha256, champs} (doublons gardés)."""
    out: dict[tuple[str, str], list[dict[str, Any]]] = {}
    with zipfile.ZipFile(archive) as z:
        for name in z.namelist():
            base = name.rsplit("/", 1)[-1]
            if not (base.startswith("Format_") and base.endswith(".xml")):
                continue
            raw = z.read(name)
            text = raw.decode("utf-8-sig", errors="replace")
            fmt = re.search(r"<Format>(.*?)</Format>", text, re.S)
            if not fmt:
                continue
            key = (_tag(fmt.group(1), "Name") or "", _tag(fmt.group(1), "Version") or "")
            fields = []
            for block in re.findall(r"<Field>(.*?)</Field>", text, re.S):
                fields.append(
                    {
                        "ordinal": int(_tag(block, "OrdinalNumber") or 0),
                        "label": _tag(block, "Label") or "",
                        "length": _int(_tag(block, "Length")),
                        "decimals": _int(_tag(block, "DecimalPlaces")),
                        "type": _tag(block, "FormatType") or "",
                        "necessary": _tag(block, "Necessary") == "1",
                        "importable": _bool(_tag(block, "Importable")),
                        "exportable": _bool(_tag(block, "Exportable")),
                        "field_id": _tag(block, "BaseFieldId") or _tag(block, "FieldId"),
                        "label_alias": _tag(block, "LabelAlias") or None,
                    }
                )
            fields.sort(key=lambda f: f["ordinal"])
            out.setdefault(key, []).append(
                {"file": base, "sha256": hashlib.sha256(raw).hexdigest(), "fields": fields}
            )
    return out


def _int(value: str | None) -> int | None:
    return int(value) if value and value.lstrip("-").isdigit() else None


def _bool(value: str | None) -> bool | None:
    return None if value is None else value == "1"


def read_portal(path: Path) -> dict[str, list[dict[str, str]]]:
    """Titre de page (allemand) → lignes {position, heading, pattern} des tableaux de colonnes."""
    data = json.loads(path.read_text(encoding="utf-8"))
    pages: dict[str, list[dict[str, str]]] = {}

    def walk(items: list[dict[str, Any]]) -> None:
        for p in items:
            rows = []
            for tr in re.findall(r"<tr.*?</tr>", p.get("body") or "", re.S):
                cells = [
                    html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c))).strip()
                    # découpe sur les balises OUVRANTES : le portail oublie parfois des </td>
                    for c in re.split(r"<t[hd](?:\s[^>]*)?>", tr)[1:]
                ]
                if len(cells) >= 3 and cells[0].isdigit():
                    rows.append({"position": cells[0], "heading": cells[1], "pattern": cells[2]})
            if rows:
                pages[p["title"]] = rows
            walk(p.get("fileFormatPages") or [])

    walk(((data.get("localization") or {}).get("fileFormatPages")) or [])
    return pages


def read_sample(archive: Path, name: str) -> dict[str, Any]:
    with zipfile.ZipFile(archive) as z:
        raw = z.read(name)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")
    lines = text.splitlines()
    header = next(csv.reader([lines[0]], delimiter=";"))
    columns = next(csv.reader([lines[1]], delimiter=";"))
    return {"header": header, "columns": columns, "sha256": hashlib.sha256(raw).hexdigest()}


# --------------------------------------------------------------------------- #
# construction
# --------------------------------------------------------------------------- #
def build(
    portal: Path,
    pruefprogramm: Path,
    samples: Path,
    out: Path,
    targets: tuple[Target, ...] = TARGETS,
) -> dict[str, Any]:
    definitions = read_definitions(pruefprogramm)
    pages = read_portal(portal)
    retrieved = datetime.now(UTC).date().isoformat()
    header_rows = pages.get("Header") or []
    header_version = _header_version(header_rows)
    header = [
        DatevField(
            position=int(r["position"]),
            label=r["heading"],
            datev_type=header_type_of(r["pattern"]),
            pattern=r["pattern"] or None,
        )
        for r in header_rows
    ]
    report: dict[str, Any] = {"generated_at": retrieved, "header_fields": len(header), "formats": []}
    for target in targets:
        portal_rows = pages.get(target.portal_title) or []
        sample = read_sample(samples, target.sample) if target.sample else None
        current = max(target.versions, key=int)
        previous: list[dict[str, Any]] | None = None
        for version in target.versions:
            found = definitions.get((target.xml_name, version), [])
            if len(found) != 1:  # absente, ou plusieurs définitions : ne jamais choisir au hasard
                files = ", ".join(d["file"] for d in found) or "aucune"
                raise SystemExit(f"{target.xml_name} v{version} : une définition attendue, trouvé : {files}")
            definition = found[0]
            fields = definition["fields"]
            is_current = version == current
            spec = DatevFormatSpec(
                format=target.code,
                format_name=target.xml_name,
                data_category=_category(sample, header_rows) if sample else 0,
                format_version=version,
                header_version=header_version,
                entity=target.entity,
                header=header,
                fields=[_field(f, portal_rows, sample, is_current) for f in fields],
                source={
                    "definition": {"file": definition["file"], "sha256": definition["sha256"]},
                    "pruefprogramm": {"file": pruefprogramm.name, "sha256": _sha256(pruefprogramm)},
                    "portal": {"file": portal.name, "retrieved": retrieved} if is_current else None,
                    "sample": {"file": target.sample, "sha256": sample["sha256"]}
                    if is_current and sample
                    else None,
                },
            )
            spec.dump(out / spec.file_name)
            report["formats"].append(
                _checks(target, version, fields, portal_rows, sample, is_current, previous)
            )
            previous = fields
    return report


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _header_version(rows: list[dict[str, str]]) -> str:
    """« Versionsnummer » de l'en-tête : la seule valeur permise par son motif officiel (ex. ^(700)$)."""
    row = next((r for r in rows if r["position"] == "2"), None)
    m = re.fullmatch(r"\^\(?(\d+)\)?\$", (row or {}).get("pattern", ""))
    if not m:
        raise SystemExit("Version d'en-tête introuvable dans le tableau « Header » du portail")
    return m.group(1)


def _category(sample: dict[str, Any], header_rows: list[dict[str, str]]) -> int:
    """Catégorie de données lue dans l'en-tête du fichier d'exemple officiel (champ 3)."""
    return int(sample["header"][2])


def _field(
    f: dict[str, Any], portal_rows: list[dict[str, str]], sample: dict[str, Any] | None, current: bool
) -> DatevField:
    position = f["ordinal"] + 1
    aliases: list[str] = [f["label_alias"]] if f.get("label_alias") and f["label_alias"] != f["label"] else []
    pattern = None
    if current:
        row = next((r for r in portal_rows if int(r["position"]) == position), None)
        if row:
            pattern = row["pattern"] or None
            if row["heading"] != f["label"]:
                aliases.append(row["heading"])
        if sample and position <= len(sample["columns"]):
            col = sample["columns"][position - 1]
            if col != f["label"] and col not in aliases:
                aliases.append(col)
    return DatevField(
        position=position,
        label=f["label"],
        datev_type=f["type"],
        length=f["length"],
        decimals=f["decimals"],
        required=f["necessary"],
        importable=f["importable"],
        exportable=f["exportable"],
        pattern=pattern,
        source_field_id=f["field_id"],
        aliases=aliases,
    )


def _checks(
    target: Target,
    version: str,
    fields: list[dict[str, Any]],
    portal_rows: list[dict[str, str]],
    sample: dict[str, Any] | None,
    current: bool,
    previous: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    labels = [f["label"] for f in fields]
    check: dict[str, Any] = {"format": target.code, "version": version, "fields": len(fields)}
    if current:
        check["portal_fields"] = len(portal_rows)
        check["portal_label_differences"] = sum(
            1
            for r in portal_rows
            if int(r["position"]) <= len(labels) and r["heading"] != labels[int(r["position"]) - 1]
        )
        if sample:
            check["sample_fields"] = len(sample["columns"])
            check["sample_format_version"] = sample["header"][4] if len(sample["header"]) > 4 else None
            check["sample_label_differences"] = [
                {"position": i + 1, "definition": a, "sample": b}
                for i, (a, b) in enumerate(zip(labels, sample["columns"], strict=False))
                if a != b
            ]
    if previous is not None:
        old = {f["ordinal"] + 1: f for f in previous}
        new = {f["ordinal"] + 1: f for f in fields}
        check["append_only"] = all(p in new for p in old) and sorted(set(new) - set(old)) == list(
            range(len(old) + 1, len(new) + 1)
        )
        check["added"] = [{"position": p, "label": new[p]["label"]} for p in sorted(set(new) - set(old))]
        check["changed"] = [
            {
                "position": p,
                "differences": {
                    k: [old[p][k], new[p][k]]
                    for k in ("label", "length", "decimals", "type", "necessary")
                    if old[p][k] != new[p][k]
                },
            }
            for p in sorted(set(old) & set(new))
            if any(old[p][k] != new[p][k] for k in ("label", "length", "decimals", "type", "necessary"))
        ]
    return check


def main() -> None:
    ap = argparse.ArgumentParser(description="Génère les specs DATEV locales depuis les sources officielles")
    ap.add_argument("--portal", type=Path, required=True)
    ap.add_argument("--pruefprogramm", type=Path, required=True)
    ap.add_argument("--samples", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("var/datev/specs/extf_700"))
    ap.add_argument("--report", type=Path, default=Path("var/datev/specs/report.json"))
    args = ap.parse_args()
    report = build(args.portal, args.pruefprogramm, args.samples, args.out)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    for f in report["formats"]:
        print(
            f"{f['format']} v{f['version']} : {f['fields']} champs"
            + (f" | ajoutés {[a['position'] for a in f['added']]}" if "added" in f else "")
            + (f" | modifiés {[c['position'] for c in f['changed']]}" if "changed" in f else "")
        )
    print(f"Specs : {args.out}  ·  rapport : {args.report}")


if __name__ == "__main__":
    main()
