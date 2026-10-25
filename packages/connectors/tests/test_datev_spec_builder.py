"""Générateur de specs DATEV sur des mini-sources INVENTÉES, construites dans le test.

Même forme que les sources officielles (JSON du portail, archive de définitions XML, archive de fichiers
d'exemple) ; aucun fichier DATEV.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from uie_connectors.datev import DatevFormatSpec, DatevSchemaLoader
from uie_connectors.datev.spec_builder import Target, build, read_definitions, read_portal

TARGET = Target("demo-journal", "demo_journal", "Demo Journal", "Demo Journal", "EXTF_Demo.csv", ("1", "2"))


def _xml(name: str, version: str, fields: list[tuple[str, str, int, int, int]]) -> str:
    body = "".join(
        f"<Field><FieldId>{i + 1}</FieldId><OrdinalNumber>{i}</OrdinalNumber><Label>{label}</Label>"
        f"<LabelAlias>{alias}</LabelAlias><Importable>1</Importable><Exportable>0</Exportable>"
        f"<Length>{length}</Length><DecimalPlaces>{dec}</DecimalPlaces><FormatType>{kind}</FormatType>"
        f"<Necessary>{req}</Necessary><BaseFieldId>{900 + i}</BaseFieldId></Field>"
        for i, (label, kind, length, dec, req, *rest) in enumerate(fields)
        for alias in [rest[0] if rest else ""]
    )
    head = f"<Format><Name>{name}</Name><Version>{version}</Version></Format>"
    return f"﻿<FormatDescription>{head}{body}</FormatDescription>"


V1 = [("Betrag", "Betrag", 8, 2, 1), ("Konto", "Konto", 6, 0, 1, "Kontonr")]
V2 = [*V1, ("Datum", "Datum", 4, 0, 0)]


def _table(rows: list[tuple[str, str, str]], *, unclosed: int | None = None) -> str:
    out = ["<table><tr><th>#</th><th>Überschrift</th><th>Ausdruck</th></tr>"]
    for pos, heading, pattern in rows:
        close = "" if unclosed == int(pos) else "</td>"  # le portail oublie parfois un </td>
        out.append(f"<tr><td>{pos}</td><td>{heading}{close}<td>{pattern}</td><td>…</td></tr>")
    return "".join(out) + "</table>"


@pytest.fixture
def sources(tmp_path: Path) -> dict[str, Path]:
    header = [
        ("1", "Kennung", '^["](DEMO)["]$'),
        ("2", "Kopfversion", "^(900)$"),
        ("3", "Kategorie", "^(91)$"),
    ]
    fmt = [("1", "Umsatz", r"^\d{1,8}\,\d{2}$"), ("2", "Konto", r"^(\d{1,6})$"), ("3", "Datum", "")]
    portal = {
        "localization": {
            "fileFormatPages": [
                {
                    "title": "Formatbeschreibung",
                    "body": "",
                    "fileFormatPages": [
                        {"title": "Header", "body": _table(header)},
                        {"title": "Demo Journal", "body": _table(fmt, unclosed=2)},
                    ],
                }
            ]
        }
    }
    p = tmp_path / "portal.json"
    p.write_text(json.dumps(portal), encoding="utf-8")
    pp = tmp_path / "pruef.zip"
    with zipfile.ZipFile(pp, "w") as z:
        z.writestr("Formate/Format_Demo_Journal_1.xml", _xml("Demo Journal", "1", V1))
        z.writestr("Formate/Format_Demo_Journal.xml", _xml("Demo Journal", "2", V2))
        z.writestr("Formate/Format_Autre.xml", _xml("Autre", "1", V1))
        z.writestr("Formate/Format_Autre_copie.xml", _xml("Autre", "1", V1))  # doublon hors périmètre
        z.writestr("Formate/Liste.xml", _xml("Demo Journal", "9", V1))  # pas une définition Format_*
    ms = tmp_path / "samples.zip"
    with zipfile.ZipFile(ms, "w") as z:
        z.writestr(
            "EXTF_Demo.csv",
            '"DEMO";900;91;"Demo Journal";2\r\nBetrag;Konto Nr;Datum\r\n1,00;100;0101\r\n'.encode("cp1252"),
        )
    return {"portal": p, "pruef": pp, "samples": ms, "out": tmp_path / "specs"}


def _build(src: dict[str, Path]) -> dict:
    return build(src["portal"], src["pruef"], src["samples"], src["out"], targets=(TARGET,))


def test_definitions_selected_by_content(sources):
    defs = read_definitions(sources["pruef"])
    assert [d["file"] for d in defs[("Demo Journal", "2")]] == ["Format_Demo_Journal.xml"]
    assert ("Demo Journal", "9") not in defs
    assert len(defs[("Autre", "1")]) == 2


def test_portal_rows_survive_missing_closing_tag(sources):
    rows = read_portal(sources["portal"])["Demo Journal"]
    assert [(r["position"], r["heading"]) for r in rows] == [("1", "Umsatz"), ("2", "Konto"), ("3", "Datum")]
    assert rows[1]["pattern"] == r"^(\d{1,6})$"


def test_build_specs_and_report(sources):
    report = _build(sources)
    files = sorted(p.name for p in sources["out"].iterdir())
    assert files == ["demo-journal_v1.yaml", "demo-journal_v2.yaml"]

    v2 = DatevFormatSpec.load(sources["out"] / "demo-journal_v2.yaml")
    assert (v2.format_version, v2.header_version, v2.data_category) == ("2", "900", 91)
    assert [(h.position, h.label, h.datev_type) for h in v2.header] == [
        (1, "Kennung", "Text"),
        (2, "Kopfversion", "Zahl"),
        (3, "Kategorie", "Zahl"),
    ]
    f = v2.fields
    assert (f[0].label, f[0].length, f[0].decimals, f[0].required, f[0].exportable) == (
        "Betrag",
        8,
        2,
        True,
        False,
    )
    assert f[0].aliases == ["Umsatz"]  # libellé du portail
    assert f[1].aliases == ["Kontonr", "Konto Nr"]  # LabelAlias du XML, puis en-tête du fichier d'exemple
    assert f[1].pattern == r"^(\d{1,6})$" and f[1].source_field_id == "901"
    assert f[2].pattern is None  # motif vide dans le portail
    assert v2.source["definition"]["file"] == "Format_Demo_Journal.xml"
    assert v2.source["sample"]["file"] == "EXTF_Demo.csv"

    v1 = DatevFormatSpec.load(sources["out"] / "demo-journal_v1.yaml")
    assert [x.pattern for x in v1.fields] == [None, None]  # motifs : version courante seulement
    assert v1.source["portal"] is None and v1.source["sample"] is None

    current = report["formats"][1]
    assert current["append_only"] is True
    assert current["added"] == [{"position": 3, "label": "Datum"}]
    assert current["portal_fields"] == 3 and current["portal_label_differences"] == 1
    assert current["sample_format_version"] == "2"
    assert current["sample_label_differences"] == [
        {"position": 2, "definition": "Konto", "sample": "Konto Nr"}
    ]

    snap = DatevSchemaLoader({"spec": sources["out"] / "demo-journal_v2.yaml"}).build_snapshot()
    assert (snap.product, snap.version, len(snap.entities[1].fields)) == ("demo-journal", "2", 3)


def test_insertion_breaks_append_only(sources):
    inserted = [V1[0], ("Neu", "Text", 5, 0, 0), V1[1]]
    pp = sources["pruef"].with_name("pruef2.zip")
    with zipfile.ZipFile(pp, "w") as z:
        z.writestr("Format_Demo_Journal_1.xml", _xml("Demo Journal", "1", V1))
        z.writestr("Format_Demo_Journal.xml", _xml("Demo Journal", "2", inserted))
    report = build(sources["portal"], pp, sources["samples"], sources["out"], targets=(TARGET,))
    current = report["formats"][1]
    assert current["append_only"] is True  # positions conservées…
    assert current["changed"][0]["position"] == 2  # …mais la colonne 2 a changé de sens : signalé


def test_ambiguous_or_missing_definition_is_refused(sources):
    pp = sources["pruef"].with_name("pruef3.zip")
    with zipfile.ZipFile(pp, "w") as z:
        z.writestr("Format_Demo_Journal_1.xml", _xml("Demo Journal", "1", V1))
        z.writestr("Format_Demo_Journal_1b.xml", _xml("Demo Journal", "1", V1))
    with pytest.raises(SystemExit, match="une définition attendue"):
        build(sources["portal"], pp, sources["samples"], sources["out"], targets=(TARGET,))
