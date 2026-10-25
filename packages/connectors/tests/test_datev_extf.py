"""Écrivain EXTF : forme des cellules d'après le type natif et le motif du Registry, fichier complet.

Colonnes INVENTÉES pour les règles d'écriture ; vérification sur la spec RÉELLE Debitoren/Kreditoren v5 et sur
l'en-tête du fichier d'exemple officiel seulement s'ils sont présents sur le poste (non versionnés).
"""

from __future__ import annotations

import zipfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
import uie_connectors  # noqa: F401  (enregistre loaders et écrivains)
import uie_domain_packs  # noqa: F401  (enregistre le binding PARTNER → DATEV)
from uie_connectors.datev import DatevSchemaLoader, ExtfWriter
from uie_core.identity import SystemContext
from uie_core.mapping import RecordFileWriterRegistry, TargetBindingRegistry, header_values, resolve_binding
from uie_core.registry import DataType, FieldDef, RegistryService

ROOT = Path(__file__).resolve().parents[3]
REAL_SPEC = ROOT / "var" / "datev" / "specs" / "extf_700" / "debitoren-kreditoren_v5.yaml"
SAMPLES = ROOT / "temp" / "datev_portal" / "musterdaten.zip"
W = ExtfWriter()


def _fd(native: str, pattern: str | None = None, decimals: int | None = None) -> FieldDef:
    meta = {k: v for k, v in {"pattern": pattern, "decimals": decimals}.items() if v is not None}
    return FieldDef(
        technical_name="001", label="Demo", data_type=DataType.STRING, native_type=native, metadata=meta
    )


def test_writer_is_registered():
    assert isinstance(RecordFileWriterRegistry.get("datev", "extf"), ExtfWriter)


@pytest.mark.parametrize(
    ("field", "value", "cell"),
    [
        (_fd("Text", '^("(.){0,30}")$'), "Holz & Co", '"Holz & Co"'),
        (_fd("Text", '^("(.){0,30}")$'), 'Firma "Nord"', '"Firma ""Nord"""'),
        (_fd("Text", '^("(.){0,30}")$'), None, '""'),
        (_fd("Text", "^[]$"), None, ""),  # champ d'en-tête réservé : motif sans guillemets
        (_fd("Text"), "x", '"x"'),
        (_fd("Konto", r"^(\d{1,9})$"), "10000", "10000"),
        (_fd("Zahl"), None, ""),
        (_fd("Zahl"), 5, "5"),
        (_fd("Zahl", decimals=2), "5.5", "5,50"),
        (_fd("Betrag", decimals=2), Decimal("1234.5"), "1234,50"),
        (_fd("Datum"), date(2026, 1, 31), "31012026"),
        (_fd("Datum"), datetime(2026, 1, 31, 10, 0), "31012026"),
    ],
)
def test_cells(field, value, cell):  # noqa: ANN001
    assert W.format_cell(field, value) == cell


def test_render_crlf_and_encoding():
    head = [_fd("Text", '^["](EXTF)["]$')]
    cols = [
        FieldDef(technical_name="001", label="Konto", native_type="Konto"),
        FieldDef(technical_name="002", label="Name", native_type="Text"),
    ]
    data = W.render(head, {"001": "EXTF"}, cols, [{"001": "10000", "002": "Müller"}], "cp1252")
    assert data == '"EXTF"\r\nKonto;Name\r\n10000;"Müller"\r\n'.encode("cp1252")
    assert W.render([], {}, cols, [], "utf-8-sig").startswith(b"\xef\xbb\xbf")
    assert not W.encodable("Łódź", "cp1252") and W.encodable("Łódź", "utf-8-sig")


# --------------------------------------------------------------------------- #
# spec réelle (locale) : binding résolu, en-tête conforme à l'exemple officiel
# --------------------------------------------------------------------------- #
needs_real = pytest.mark.skipif(not REAL_SPEC.exists(), reason="spec DATEV locale absente (spec_builder)")


@pytest.fixture
def resolved(session):  # noqa: ANN001, ANN201
    registry = RegistryService(session, SystemContext("test"))
    registry.publish_snapshot(DatevSchemaLoader({"spec": REAL_SPEC}).build_snapshot())
    return resolve_binding(registry, TargetBindingRegistry.get("partner", "datev", "debitoren-kreditoren"))


@needs_real
def test_binding_positions_exist_in_the_registry(resolved):
    assert resolved.snapshot_label == "datev debitoren-kreditoren 5 @1.0.0"
    assert len(resolved.fields) == 254 and len(resolved.header_fields) == 31
    assert resolved.field("001").required  # Konto : seul champ obligatoire de la v5


@needs_real
@pytest.mark.skipif(not SAMPLES.exists(), reason="fichiers d'exemple DATEV locaux absents")
def test_header_line_has_the_shape_of_the_official_sample(resolved):
    raw = zipfile.ZipFile(SAMPLES).read("EXTF_DebKred_Stamm.csv").decode("cp1252")
    sample = raw.split("\r\n")[0].split(";")
    profile = {  # valeurs lues dans l'exemple officiel (rien de recopié dans le dépôt)
        "advisor_number": sample[10],
        "client_number": sample[11],
        "fiscal_year_start": f"{sample[12][:4]}-{sample[12][4:6]}-{sample[12][6:]}",
        "account_length": sample[13],
        "chart_of_accounts": sample[26].strip('"'),
        "origin_code": sample[7].strip('"'),
    }
    now = datetime.strptime(sample[5][:14], "%Y%m%d%H%M%S").replace(microsecond=int(sample[5][14:]) * 1000)
    ours = W.render(resolved.header_fields, header_values(resolved, profile, now), [], [], "cp1252")
    assert ours.decode("cp1252").split("\r\n")[0].split(";") == sample
