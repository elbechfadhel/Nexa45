"""ACCOUNTING phase 5 : écritures PRÉPARÉES (phase 4) → fichier DATEV Buchungsstapel EXTF (aperçu =
téléchargement).

Même montage que la phase 4 (schéma réel d'accounting17, pièces inventées, spec v13 réelle du Registry —
sauté si elle est absente). Le fichier est RELU octet par octet : en-tête, ordre et nombre des colonnes
du Registry, lignes, encodage, umlauts. La sérialisation ne refait aucune décision comptable : elle est
aussi testée sur une préparation construite à la main, sans aucune pièce comptable.
"""

# ruff: noqa: F811  (fixtures importées du module de préparation et utilisées en paramètre)
from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from test_accounting_datev_preparation import (  # noqa: F401  (fixtures)
    BS,
    DATEV,
    _by_ref,
    _ready_world,
    _shared_profile,
    prep,
    real_spec,
    world,
)
from test_accounting_read import COMPANY, JANUARY
from uie_cdm import LedgerAccountRef
from uie_connectors.odoo.discovery import READ_ONLY_METHODS
from uie_core.accounting import (
    AccountingTargetNotReady,
    ExportChanged,
    ExportInvalid,
    Side,
)
from uie_core.accounting import target as target_module
from uie_core.accounting.target import PreparedBooking, PreparedDocument, TargetPreparation
from uie_core.exports import ExportAccountAssignmentRecord, ExportRunRecord, ExportValueMappingRecord
from uie_core.mapping import LookupKind, RecordStatus

FILE = (COMPANY, JANUARY, DATEV, BS, "13")
EOL = b"\r\n"


def _file_world(prep, **settings):  # noqa: ANN001, ANN003, ANN202
    """Phase 4 prête (comptes, taxes « aucune clé » de test) + réglages de fichier."""
    _ready_world(prep)
    svc, iid = prep["target"], prep["inst"].id
    svc.save_settings(iid, DATEV, BS, {"lock_bookings": "0"} | settings)
    return svc, iid


def _lines(content: bytes, encoding: str = "cp1252") -> list[list[str]]:
    text = content.decode(encoding)
    assert text.endswith("\r\n") and "\n" not in text.replace("\r\n", "")  # CRLF partout, y compris la fin
    return [line.split(";") for line in text.split("\r\n")[:-1]]


def _cells(row) -> dict[str, str]:  # noqa: ANN001
    return {c.field: c.value for c in row.cells}


# --------------------------------------------------------------------------- #
@real_spec
def test_file_settings_are_required_only_for_the_file(prep):
    preview, _ = _ready_world(prep)
    svc, iid = prep["target"], prep["inst"].id
    # la préparation (phase 4) reste possible et inchangée sans réglage de fichier
    assert (preview.count(RecordStatus.READY), preview.count(RecordStatus.WARNING)) == (8, 2)
    settings = svc.settings(iid, DATEV, BS)
    assert settings.ready and settings.missing_for_file == ["Lock the bookings in DATEV (Festschreibung)"]
    with pytest.raises(AccountingTargetNotReady, match="Required to create the file"):
        svc.file_preview(iid, *FILE)
    with pytest.raises(AccountingTargetNotReady):
        svc.generate(iid, *FILE)
    # un réglage saisi ne retire pas les autres (WKZ conservé)
    after = svc.save_settings(iid, DATEV, BS, {"lock_bookings": "1"})
    assert {v.name: v.value for v in after.values}["currency"] == "EUR" and not after.missing_for_file


@real_spec
def test_header_comes_from_the_registry_settings_and_export_context(prep):
    svc, iid = _file_world(prep)
    plan = svc.file_preview(iid, *FILE)
    assert {c.field: c.value for c in plan.header} == {
        "001": '"EXTF"',
        "002": "700",
        "003": "21",
        "004": '"Buchungsstapel"',
        "005": "13",
        "006": next(c.value for c in plan.header if c.field == "006"),  # horodatage : volatile
        "008": '"RE"',
        "011": "1001",
        "012": "1",
        "013": "20260101",
        "014": "4",
        "015": "20260101",  # Datum von / bis = période exportée
        "016": "20260131",
        "017": '"Buchungen 01/2026"',
        "019": "1",
        "020": "0",
        "021": "0",  # Festschreibung : choix explicite du profil
        "022": '"EUR"',
        "027": '"03"',
    }
    assert plan.volatile_header_fields == ["006"]
    assert (plan.format_version, plan.snapshot_label, plan.encoding) == (
        "13",
        "datev buchungsstapel 13 @1.0.0",
        "cp1252",
    )


@real_spec
def test_only_ready_and_warning_documents_are_written(prep):
    svc, iid = _file_world(prep)
    preview = svc.preview(iid, *FILE)
    plan = svc.file_preview(iid, *FILE)
    docs = _by_ref(preview)
    written = {r.document_number for r in plan.rows}
    expected = {
        d.number for d in preview.documents if d.target_status in (RecordStatus.READY, RecordStatus.WARNING)
    }
    assert written == expected and len(expected) == 10
    assert (
        plan.row_count
        == sum(len(d.bookings) for d in preview.documents if d.number in expected)
        == len(plan.rows)
    )
    assert (plan.source_documents, plan.exported_documents, plan.excluded_documents) == (13, 10, 3)
    assert (plan.warning_documents, plan.blocked_documents, plan.not_prepared_documents) == (2, 1, 2)
    # politique WARNING : techniquement valide → écrite, et signalée
    warning_rows = {r.document_number for r in plan.rows if r.status == RecordStatus.WARNING}
    assert warning_rows == {docs["UIE-ACC-G"].number, docs["UIE-ACC-H"].number}
    # jamais écrites, avec leur raison
    excluded = {e.reference: e for e in plan.excluded}
    assert set(excluded) == {"UIE-ACC-J", "UIE-ACC-K", "UIE-ACC-L"}
    assert excluded["UIE-ACC-J"].target_status is None and "Draft" in excluded["UIE-ACC-J"].reasons[0]
    assert excluded["UIE-ACC-K"].target_status is None and "Cancelled" in excluded["UIE-ACC-K"].reasons[0]
    assert excluded["UIE-ACC-L"].target_status == RecordStatus.BLOCKED
    assert "A line has several taxes" in " ".join(excluded["UIE-ACC-L"].reasons)


@real_spec
def test_golden_re_2026_00002_is_exactly_two_rows_from_the_prepared_bookings(prep):
    svc, iid = _file_world(prep)
    prepared = _by_ref(svc.preview(iid, *FILE))["UIE-ACC-B"]
    plan = svc.file_preview(iid, *FILE)
    rows = [r for r in plan.rows if r.document_number == "RE/2026/00002"]
    assert [r.booking_index for r in rows] == [1, 2]
    # les valeurs écrites SONT celles des écritures préparées (aucune re-dérivation)
    assert [_cells(r) for r in rows] == [{c.field: c.value for c in bk.cells} for bk in prepared.bookings]
    account = prepared.bookings[0].partner_account
    assert [_cells(r) for r in rows] == [
        {
            "001": "654,50",
            "002": '"S"',
            "003": '"EUR"',
            "007": account,
            "008": "8400",
            "010": "1501",
            "011": '"RE/2026/00002"',
            "014": '"Lena Hoffmann, RE/2026/00002"',
        },
        {
            "001": "85,39",
            "002": '"S"',
            "003": '"EUR"',
            "007": account,
            "008": "8300",
            "010": "1501",
            "011": '"RE/2026/00002"',
            "014": '"Lena Hoffmann, RE/2026/00002"',
        },
    ]
    assert [r.source_lines for r in rows] == [[0, 1], [2]]  # provenance : lignes de la pièce


@real_spec
def test_generated_bytes_are_a_valid_extf_file_in_registry_order(prep, session):  # noqa: ANN001
    svc, iid = _file_world(prep)
    plan = svc.file_preview(iid, *FILE)
    file = svc.generate(iid, *FILE, expected_digest=plan.content_digest)
    lines = _lines(file.content)
    header, columns, data = lines[0], lines[1], lines[2:]
    assert header[:5] == ['"EXTF"', "700", "21", '"Buchungsstapel"', "13"] and len(header) == 31
    # colonnes : celles du Registry (snapshot v13), dans son ordre, sans liste recopiée ici
    resolved = svc._target(DATEV, BS, "13").resolved
    assert columns == [f.label for f in resolved.fields] and len(columns) == plan.column_count == 125
    assert len(data) == plan.row_count and all(len(row) == 125 for row in data)
    order = [f.technical_name for f in resolved.fields]
    by_pos = [dict(zip(order, row, strict=True)) for row in data]
    # chaque ligne du fichier = la ligne de l'aperçu, cellule par cellule ; le reste est vide
    for written, row in zip(by_pos, plan.rows, strict=True):
        cells = _cells(row)
        for pos, value in written.items():
            assert value == cells.get(pos, '""' if value == '""' else ""), (row.document_number, pos)
    golden = [r for r in by_pos if r["011"] == '"RE/2026/00002"']
    assert [(r["001"], r["002"], r["008"], r["010"]) for r in golden] == [
        ("654,50", '"S"', "8400", "1501"),
        ("85,39", '"S"', "8300", "1501"),
    ]
    # sens conservés (phase 4) : avoir client H, facture fournisseur H, avoir fournisseur S
    side = {r["011"].strip('"'): r["002"] for r in by_pos}
    assert (side["RRE/2026/00001"], side["RECHN/2026/01/0001"], side["RRECHN/2026/01/0001"]) == (
        '"H"',
        '"H"',
        '"S"',
    )
    text = file.content.decode("cp1252")
    assert "Bäckerei Sonnenschein GmbH" in text  # umlauts en CP1252
    assert not {"UIE-ACC-J", "UIE-ACC-K", "RE/2026/00007"} & {r["011"].strip('"') for r in by_pos}
    assert all("." not in r["001"] and "," in r["001"] for r in by_pos)  # virgule décimale, jamais de point
    assert file.filename.startswith(
        "DATEV_buchungsstapel_GO45-Demo-GmbH_2026-01_"
    ) and file.filename.endswith(".csv")
    assert file.media_type == "text/csv" and file.run.file_size == len(file.content) == plan.size


@real_spec
def test_preview_is_idempotent_and_download_is_the_previewed_content(prep, session):  # noqa: ANN001
    svc, iid = _file_world(prep)

    def state() -> tuple[int, int, int]:
        return tuple(  # type: ignore[return-value]
            session.scalar(select(func.count()).select_from(t))
            for t in (ExportRunRecord, ExportAccountAssignmentRecord, ExportValueMappingRecord)
        )

    before = state()
    first, second = svc.file_preview(iid, *FILE), svc.file_preview(iid, *FILE)
    assert state() == before  # aucun journal, aucun compte attribué, aucune décision créée
    assert first.content_digest == second.content_digest and first.rows == second.rows
    assert (first.previous_exports, first.last_export_at) == (0, None)

    a = svc.generate(iid, *FILE, expected_digest=first.content_digest)
    # D11 : un second téléchargement NORMAL ne réexporte rien (les pièces sont déjà exportées)
    with pytest.raises(ExportInvalid, match="No new document to export"):
        svc.generate(iid, *FILE)
    # doublon VOLONTAIRE (confirmé) : mêmes enregistrements, à l'octet près hors ligne d'en-tête
    b = svc.generate(iid, *FILE, include_exported=True, confirm_duplicates=True)
    assert a.content.split(EOL, 1)[1] == b.content.split(EOL, 1)[1]
    assert _lines(a.content)[0][:5] == _lines(b.content)[0][:5]
    runs = list(session.scalars(select(ExportRunRecord).where(ExportRunRecord.period == "2026-01")))
    assert len(runs) == 2 and state()[1:] == before[1:]
    again = svc.file_preview(iid, *FILE, include_exported=True)
    assert again.previous_exports == 2
    assert again.content_digest == first.content_digest  # transformation inchangée


@real_spec
def test_download_is_refused_when_the_data_changed_since_the_preview(prep, session):  # noqa: ANN001
    svc, iid = _file_world(prep)
    plan = svc.file_preview(iid, *FILE)
    mapping = next(m for m in svc.preview(iid, *FILE).mappings if m.source_code == "8300")
    svc.clear(iid, DATEV, BS, LookupKind.ACCOUNT, mapping.source_ref)  # décision retirée après l'aperçu
    with pytest.raises(ExportChanged, match="preview the file again"):
        svc.generate(iid, *FILE, expected_digest=plan.content_digest)
    assert session.scalar(select(func.count()).select_from(ExportRunRecord)) == 0
    assert (
        svc.file_preview(iid, *FILE).exported_documents == plan.exported_documents - 1
    )  # B n'est plus écrite


@real_spec
def test_nothing_exportable_is_an_explicit_error(prep):
    _shared_profile(prep)
    svc, iid = prep["target"], prep["inst"].id
    svc.save_settings(iid, DATEV, BS, {"currency": "EUR", "lock_bookings": "0"})
    with pytest.raises(ExportInvalid, match="No document can be exported"):
        svc.file_preview(iid, *FILE)  # aucune décision : tout est bloqué


@real_spec
def test_history_keeps_an_audit_without_amounts(prep):
    svc, iid = _file_world(prep)
    plan = svc.file_preview(iid, *FILE)
    file = svc.generate(iid, *FILE, expected_digest=plan.content_digest)
    run = file.run
    assert (run.period, run.records, run.warnings, run.blocked, run.encoding) == (
        "2026-01",
        plan.row_count,
        2,
        1,
        "cp1252",
    )
    assert run.instance_id == iid and run.target_snapshot_id is not None and run.binding_version == "1.4.0"
    assert run.file_sha256 == hashlib.sha256(file.content).hexdigest()
    d = run.details
    assert (d["company"], d["format_version"], d["source_documents"], d["exported_documents"]) == (
        "GO45 Demo GmbH",
        "13",
        13,
        10,
    )
    assert (d["excluded_documents"], d["not_prepared_documents"], d["rows"]) == (3, 2, plan.row_count)
    golden = next(x for x in d["documents"] if x["number"] == "RE/2026/00002")
    assert len(golden["rows"]) == 2  # provenance : pièce → rangs de ses lignes dans le fichier
    assert "654" not in str(d) and "Lena" not in str(d)  # ni montant ni nom de partenaire
    assert [(r.id, tracked) for r, tracked in svc.runs(iid, DATEV, BS)] == [(run.id, True)]


@real_spec
def test_utf8_with_bom_is_supported_when_chosen(prep):
    svc, iid = _file_world(prep, encoding="utf-8-sig")
    plan = svc.file_preview(iid, *FILE)
    file = svc.generate(iid, *FILE, expected_digest=plan.content_digest)
    assert file.content.startswith(b"\xef\xbb\xbf") and plan.encoding == "utf-8-sig"
    assert "Bäckerei Sonnenschein GmbH" in file.content.decode("utf-8-sig")


@real_spec
def test_serialization_never_writes_to_the_source(prep):
    svc, iid = _file_world(prep)
    svc.generate(iid, *FILE)
    assert prep["fake"].methods_called <= READ_ONLY_METHODS | {"authenticate", "version"}


# --------------------------------------------------------------------------- #
# Frontière : le sérialiseur ne consomme QUE des écritures préparées
# --------------------------------------------------------------------------- #
def _hand_made(values: dict, status: RecordStatus = RecordStatus.READY) -> TargetPreparation:
    booking = PreparedBooking(
        index=1,
        amount=Decimal("119.00"),
        partner_side=Side.DEBIT,
        net_amount=Decimal("-100"),
        gross_amount=Decimal("-119"),
        counter_account=LedgerAccountRef(source_ref="a", code="8400"),
        tax=None,
        partner_account="10000",
        counter_target="8400",
        tax_target=None,
        tax_no_value=False,
        source_lines=[],
        values=values,
        cells=[],
        status=status,
        issues=[],
    )
    doc = PreparedDocument(
        source_ref="1",
        number="INV-1",
        reference=None,
        document_type="CUSTOMER_INVOICE",
        source_status="POSTED",
        eligible=True,
        target_status=status,
        partner_name="Müller & Söhne GmbH",
        accounting_date=date(2026, 1, 15),
        currency="EUR",
        bookings=[booking],
    )
    return TargetPreparation(
        target="x",
        system=DATEV,
        product=BS,
        format_version="13",
        snapshot_label="s",
        binding_version="1.1.0",
        period="2026-01",
        company_name="Test GmbH",
        settings=[],
        mappings=[],
        documents=[doc],
        truncated=False,
    )


VALUES = {
    "001": Decimal("119.00"),
    "002": "S",
    "003": "EUR",
    "007": "10000",
    "008": "8400",
    "010": "1501",
    "011": "INV-1",
    "014": "Müller & Söhne GmbH, INV-1",
}


def _serialize(prep, values: dict, status: RecordStatus = RecordStatus.READY):  # noqa: ANN001, ANN202
    svc, iid = prep["target"], prep["inst"].id
    if svc.profiles.get(DATEV, "debitoren-kreditoren", prep["inst"].organization_id) is None:
        _shared_profile(prep)
    settings = svc.save_settings(iid, DATEV, BS, {"currency": "EUR", "lock_bookings": "0"})
    target = svc._target(DATEV, BS, "13")
    now = datetime(2026, 2, 1, 9, 30, 0, 123000, tzinfo=UTC)
    return target_module._serialize(target, _hand_made(values, status), settings, JANUARY, now)


@real_spec
def test_serializer_consumes_prepared_bookings_not_accounting_documents(prep):
    plan, content = _serialize(
        prep, VALUES
    )  # aucune pièce comptable, aucune correspondance, aucun partenaire
    lines = _lines(content)
    assert lines[0][5] == "20260201093000123"  # « Erzeugt am » : seule métadonnée volatile
    row = lines[2]
    assert (row[0], row[1], row[2], row[6], row[7], row[9], row[10], row[13]) == (
        "119,00",
        '"S"',
        '"EUR"',
        "10000",
        "8400",
        "1501",
        '"INV-1"',
        '"Müller & Söhne GmbH, INV-1"',
    )
    assert plan.row_count == 1 and content.decode("cp1252").count("Müller & Söhne") == 1
    again, same = _serialize(prep, VALUES)
    assert (again.content_digest, same) == (plan.content_digest, content)  # déterministe, au bit près


@real_spec
@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"008": "84AB"}, "Gegenkonto"),  # valeur « prête » refusée par le Registry : jamais omise
        ({"001": Decimal("119.005")}, "has more than 2 decimals"),
        ({"011": "INV 1"}, "Belegfeld 1"),
        ({"014": "Zażółć gęślą jaźń"}, "cannot be written in cp1252"),
    ],
)
def test_a_ready_booking_refused_by_the_registry_stops_the_file(prep, change, message):  # noqa: ANN001
    with pytest.raises(ExportInvalid, match=message):
        _serialize(prep, VALUES | change)


@real_spec
def test_a_missing_required_value_stops_the_file(prep):
    with pytest.raises(ExportInvalid, match="is required and has no value"):
        _serialize(prep, {k: v for k, v in VALUES.items() if k != "007"})


@real_spec
def test_blocked_documents_are_never_serialized_even_if_they_carry_values(prep):
    with pytest.raises(ExportInvalid, match="No document can be exported"):
        _serialize(prep, VALUES, RecordStatus.BLOCKED)
