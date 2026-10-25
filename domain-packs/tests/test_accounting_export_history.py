"""D11 — idempotence d'export au niveau de la PIÈCE (registre générique `uie_core.exports.documents`),
exercée par l'export comptable vers le format cible de test (spec v13 réelle du Registry : sauté si elle
est absente).

NEW → exportée ; ALREADY_EXPORTED → jamais réexportée par défaut ; CHANGED_SINCE_EXPORT → bloquée tant
qu'un humain ne l'autorise pas. L'historique est immuable ; l'aperçu n'écrit rien ; une génération qui
échoue n'écrit rien. Aucune donnée de la source n'est modifiée : les « changements » sont des décisions
de correspondance de test (annulées avec la transaction).
"""

# ruff: noqa: F811  (fixtures importées et utilisées en paramètre)
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from test_accounting_datev_file import FILE, _file_world
from test_accounting_datev_preparation import (  # noqa: F401  (fixtures)
    BS,
    DATEV,
    _by_ref,
    prep,
    real_spec,
    world,
)
from test_accounting_read import COMPANY, JANUARY
from test_catalog_customer17_v1b1 import PASSWORD
from uie_core.accounting import AccountingPeriod, AccountingTargetError, ExportInvalid
from uie_core.accounting import target as target_module
from uie_core.exports import (
    DocumentContent,
    DocumentExportLedger,
    DocumentExportState,
    DocumentScope,
    ExportDocumentRecord,
    ExportDocumentReleaseRecord,
    ExportRunRecord,
    RecordOrigin,
    fingerprint,
)
from uie_core.identity import GO45_DEV_ORG_ID, Organization
from uie_core.instances import InstanceCreate
from uie_core.mapping import LookupKind, RecordStatus

NEW, DONE, CHANGED = (
    DocumentExportState.NEW,
    DocumentExportState.ALREADY_EXPORTED,
    DocumentExportState.CHANGED_SINCE_EXPORT,
)
FEBRUARY = AccountingPeriod.parse("2026-02")


def _count(session, table) -> int:  # noqa: ANN001
    return session.scalar(select(func.count()).select_from(table))


def _records(session, number: str | None = None) -> list[ExportDocumentRecord]:  # noqa: ANN001
    q = select(ExportDocumentRecord).order_by(ExportDocumentRecord.created_at, ExportDocumentRecord.id)
    rows = list(session.scalars(q))
    return [r for r in rows if number is None or r.source_number == number]


def _states(plan) -> dict[str, DocumentExportState]:  # noqa: ANN001
    return {d.number: d.state for d in plan.documents}


def _remap(svc, iid, code: str, value: str) -> None:  # noqa: ANN001
    """Décision de correspondance de TEST : change la valeur cible préparée d'un compte (source intacte)."""
    m = next(x for x in svc.preview(iid, *FILE).mappings if x.source_code == code)
    svc.decide(iid, DATEV, BS, LookupKind.ACCOUNT, m.source_ref, value=value)


# --------------------------------------------------------------------------- #
# Registre générique (sans format ni pièce comptable)
# --------------------------------------------------------------------------- #
B_UNITS = [
    {"001": "654,50", "002": '"S"', "007": "10003", "008": "8400"},
    {"001": "85,39", "002": '"S"', "007": "10003", "008": "8300"},
]


def test_fingerprint_is_deterministic_and_ignores_key_order():
    same = [dict(reversed(list(u.items()))) for u in B_UNITS]
    assert (
        fingerprint(B_UNITS)
        == fingerprint(same)
        == DocumentContent(source_ref="2", units=B_UNITS).fingerprint
    )
    assert len(fingerprint(B_UNITS)) == 64


@pytest.mark.parametrize(
    "change",
    [
        lambda u: [u[0] | {"001": "654,51"}, u[1]],  # montant
        lambda u: [u[0] | {"008": "8401"}, u[1]],  # compte de contrepartie (correspondance)
        lambda u: [u[0] | {"002": '"H"'}, u[1]],  # sens
        lambda u: [u[0] | {"007": "10004"}, u[1]],  # compte du tiers
        lambda u: [u[0] | {"009": '"1234"'}, u[1]],  # clé de taxe ajoutée
        lambda u: [u[0]],  # une écriture en moins
        lambda u: [u[1], u[0]],  # ordre des écritures
    ],
)
def test_any_change_of_the_target_content_changes_the_fingerprint(change):  # noqa: ANN001
    assert fingerprint(change(B_UNITS)) != fingerprint(B_UNITS)


@real_spec
def test_scopes_never_collide(prep, session):  # noqa: ANN001
    """Même référence source et même numéro : un autre système, un autre format ou une autre organisation
    n'héritent jamais de l'historique."""
    svc, iid = _file_world(prep)
    svc.generate(iid, *FILE)
    ledger = DocumentExportLedger(session)
    content = DocumentContent(source_ref="2", number="RE/2026/00002", units=B_UNITS)
    mine = DocumentScope(GO45_DEV_ORG_ID, iid, "accounting", DATEV, BS)
    assert ledger.classify(mine, [content])["2"].state == CHANGED  # même pièce, contenu différent

    other_instance = prep["instances"].create(
        InstanceCreate(
            organization_id=GO45_DEV_ORG_ID,
            name="other-source",
            system="odoo",
            version="17",
            endpoint="http://other:8069",
            config={"db": "x", "username": "admin"},
            password=SecretStr(PASSWORD),
        )
    )
    org = Organization(name="Other Org", slug="other-org")
    session.add(org)
    session.flush()
    scopes = {
        "other source system": DocumentScope(GO45_DEV_ORG_ID, other_instance.id, "accounting", DATEV, BS),
        "other target format": DocumentScope(GO45_DEV_ORG_ID, iid, "accounting", DATEV, "another-format"),
        "other target system": DocumentScope(GO45_DEV_ORG_ID, iid, "accounting", "another-system", BS),
        "other domain": DocumentScope(GO45_DEV_ORG_ID, iid, "partner", DATEV, BS),
        "other organization": DocumentScope(org.id, iid, "accounting", DATEV, BS),
    }
    for name, scope in scopes.items():
        assert ledger.classify(scope, [content])["2"].state == NEW, name


@real_spec
def test_history_records_are_immutable(prep, session):  # noqa: ANN001
    svc, iid = _file_world(prep)
    svc.generate(iid, *FILE)
    record = _records(session, "RE/2026/00002")[0]
    record.fingerprint = "0" * 64
    with pytest.raises(ValueError, match="immuable"):
        session.flush()
    session.expire(record)


# --------------------------------------------------------------------------- #
# NEW → ALREADY_EXPORTED
# --------------------------------------------------------------------------- #
@real_spec
def test_first_preview_is_all_new_and_writes_nothing(prep, session):  # noqa: ANN001
    svc, iid = _file_world(prep)
    plan = svc.file_preview(iid, *FILE)
    assert (plan.new_documents, plan.already_exported_documents, plan.changed_documents) == (10, 0, 0)
    assert plan.exported_documents == 10 and all(d.included and d.state == NEW for d in plan.documents)
    assert {r.export_state for r in plan.rows} == {NEW}
    assert (_count(session, ExportDocumentRecord), _count(session, ExportRunRecord)) == (0, 0)
    # non-régression phase 5 : depuis un historique vierge, même contenu que sans historique
    target = svc._target(DATEV, BS, "13")
    settings = svc.settings(iid, DATEV, BS)
    plain, _ = target_module._serialize(target, svc.preview(iid, *FILE), settings, JANUARY, datetime.now(UTC))
    assert plan.content_digest == plain.content_digest and plan.row_count == plain.row_count


@real_spec
def test_generation_records_every_written_document_and_only_those(prep, session):  # noqa: ANN001
    svc, iid = _file_world(prep)
    preview = svc.preview(iid, *FILE)
    plan = svc.file_preview(iid, *FILE)
    file = svc.generate(iid, *FILE, expected_digest=plan.content_digest)
    records = _records(session)
    assert len(records) == 10 == plan.exported_documents
    assert {r.export_run_id for r in records} == {file.run.id}
    assert {r.origin for r in records} == {"EXPORTED"} and {r.scope_ref for r in records} == {COMPANY}
    assert {(r.domain, r.target_system, r.target_product, r.instance_id) for r in records} == {
        ("accounting", DATEV, BS, iid)
    }
    docs = _by_ref(preview)
    exported = {r.source_number for r in records}
    assert docs["UIE-ACC-G"].number in exported  # WARNING + NEW : exportée
    assert not {docs["UIE-ACC-L"].number, None} & exported  # bloquée, brouillon, annulée : aucune ligne
    assert len({r.source_ref for r in records}) == 10
    golden = _records(session, "RE/2026/00002")[0]
    assert (
        golden.units == 2
        and [sorted(u) for u in golden.field_digests]
        == [["001", "002", "003", "007", "008", "010", "011", "014"]] * 2
    )
    assert "654" not in str(golden.field_digests)  # empreintes de valeurs, jamais les valeurs
    d = file.run.details
    assert (d["new_documents"], d["already_exported_skipped"], d["changed_blocked"]) == (10, 0, 0)
    assert (d["released_documents"], d["duplicate_documents"], d["duplicate_override"]) == (0, 0, False)


@real_spec
def test_second_preview_has_nothing_new_and_normal_export_is_refused(prep, session):  # noqa: ANN001
    svc, iid = _file_world(prep)
    svc.generate(iid, *FILE)
    before = (_count(session, ExportDocumentRecord), _count(session, ExportRunRecord))
    plan = svc.file_preview(iid, *FILE)
    assert (plan.new_documents, plan.already_exported_documents, plan.changed_documents) == (0, 10, 0)
    assert (plan.exported_documents, plan.row_count, plan.size, plan.rows) == (0, 0, 0, [])
    assert all(d.state == DONE and not d.included and d.previous_exported_at for d in plan.documents)
    reasons = [e.reasons[0] for e in plan.excluded if e.number == "RE/2026/00002"]
    assert reasons and reasons[0].startswith("Already exported on ") and "not exported again" in reasons[0]
    with pytest.raises(ExportInvalid, match="No new document to export"):
        svc.generate(iid, *FILE)
    with pytest.raises(ExportInvalid, match="No new document to export"):
        svc.generate(iid, *FILE, expected_digest=plan.content_digest)
    assert (_count(session, ExportDocumentRecord), _count(session, ExportRunRecord)) == before


@real_spec
def test_a_document_that_becomes_exportable_later_is_new(prep, session):  # noqa: ANN001
    svc, iid = _file_world(prep)
    m8300 = next(x for x in svc.preview(iid, *FILE).mappings if x.source_code == "8300")
    svc.clear(iid, DATEV, BS, LookupKind.ACCOUNT, m8300.source_ref)  # B bloquée (compte non confirmé)
    first = svc.generate(iid, *FILE)
    assert first.run.details["exported_documents"] == 9 and not _records(session, "RE/2026/00002")
    svc.decide(iid, DATEV, BS, LookupKind.ACCOUNT, m8300.source_ref, value="8300")
    plan = svc.file_preview(iid, *FILE)
    assert (plan.new_documents, plan.already_exported_documents) == (1, 9)
    assert [d.number for d in plan.documents if d.included] == ["RE/2026/00002"] and plan.row_count == 2
    second = svc.generate(iid, *FILE, expected_digest=plan.content_digest)
    assert second.run.details["new_documents"] == 1 and second.run.details["already_exported_skipped"] == 9
    # une autre période : ses pièces sont nouvelles, l'historique de janvier ne les concerne pas
    february = svc.file_preview(iid, COMPANY, FEBRUARY, DATEV, BS, "13")
    assert (february.new_documents, february.already_exported_documents) == (1, 0)


# --------------------------------------------------------------------------- #
# CHANGED_SINCE_EXPORT, autorisation explicite, doublon volontaire
# --------------------------------------------------------------------------- #
@real_spec
def test_changed_document_is_blocked_and_history_is_untouched(prep, session):  # noqa: ANN001
    svc, iid = _file_world(prep)
    svc.generate(iid, *FILE)
    original = _records(session, "RE/2026/00002")[0]
    exported_fingerprint = original.fingerprint
    _remap(svc, iid, "8300", "8310")  # la valeur préparée de l'écriture 2 change (8300 → 8310)
    plan = svc.file_preview(iid, *FILE)
    states = _states(plan)
    assert states["RE/2026/00002"] == CHANGED and list(states.values()).count(DONE) == 9
    b = next(d for d in plan.documents if d.number == "RE/2026/00002")
    assert (b.included, b.released, b.target_status) == (
        False,
        False,
        RecordStatus.READY,
    )  # deux axes distincts
    assert [(c.booking, c.field, c.label, c.change) for c in b.changes] == [
        (2, "008", "Gegenkonto (ohne BU-Schlüssel)", "CHANGED")
    ]
    assert (b.bookings, b.previous_bookings) == (2, 2)
    reason = next(e.reasons[0] for e in plan.excluded if e.number == "RE/2026/00002")
    assert "changed since" in reason and "allow a new export first" in reason
    with pytest.raises(ExportInvalid, match="No new document to export"):
        svc.generate(iid, *FILE)  # jamais réexportée automatiquement
    session.refresh(original)
    assert original.fingerprint == exported_fingerprint and len(_records(session, "RE/2026/00002")) == 1


@real_spec
def test_explicit_release_allows_one_new_export_and_keeps_both_facts(prep, session):  # noqa: ANN001
    svc, iid = _file_world(prep)
    svc.generate(iid, *FILE)
    first = _records(session, "RE/2026/00002")[0]
    ref = first.source_ref
    with pytest.raises(AccountingTargetError, match="Only a document changed since its export"):
        svc.release_document(iid, COMPANY, JANUARY, DATEV, BS, ref)  # inchangée : rien à autoriser
    _remap(svc, iid, "8300", "8310")
    assert svc.release_document(iid, COMPANY, JANUARY, DATEV, BS, ref, note="corrected in DATEV")
    release = session.scalars(select(ExportDocumentReleaseRecord)).one()
    assert (release.previous_record_id, release.note) == (first.id, "corrected in DATEV")
    plan = svc.file_preview(iid, *FILE)
    b = next(d for d in plan.documents if d.number == "RE/2026/00002")
    assert (b.state, b.released, b.included) == (CHANGED, True, True)
    assert (plan.exported_documents, plan.row_count, plan.released_documents) == (1, 2, 1)
    file = svc.generate(iid, *FILE, expected_digest=plan.content_digest)
    history = _records(session, "RE/2026/00002")
    assert [(r.origin, r.previous_record_id) for r in history] == [("EXPORTED", None), ("RELEASED", first.id)]
    assert history[0].fingerprint != history[1].fingerprint and history[0].id == first.id
    assert file.run.details["released_documents"] == 1 and file.run.details["new_documents"] == 0
    assert _states(svc.file_preview(iid, *FILE))["RE/2026/00002"] == DONE
    # un NOUVEAU changement n'est pas couvert par l'ancienne autorisation
    _remap(svc, iid, "8300", "8320")
    again = next(d for d in svc.file_preview(iid, *FILE).documents if d.number == "RE/2026/00002")
    assert (again.state, again.released, again.included) == (CHANGED, False, False)


@real_spec
def test_release_can_be_withdrawn_before_it_is_used(prep, session):  # noqa: ANN001
    svc, iid = _file_world(prep)
    svc.generate(iid, *FILE)
    ref = _records(session, "RE/2026/00002")[0].source_ref
    _remap(svc, iid, "8300", "8310")
    svc.release_document(iid, COMPANY, JANUARY, DATEV, BS, ref)
    assert svc.release_document(iid, COMPANY, JANUARY, DATEV, BS, ref, revoke=True)
    b = next(d for d in svc.file_preview(iid, *FILE).documents if d.number == "RE/2026/00002")
    assert (b.state, b.released, b.included) == (CHANGED, False, False)
    assert _count(session, ExportDocumentReleaseRecord) == 0


@real_spec
def test_duplicate_export_needs_an_explicit_confirmation_and_is_audited(prep, session):  # noqa: ANN001
    svc, iid = _file_world(prep)
    svc.generate(iid, *FILE)
    with pytest.raises(AccountingTargetError, match="confirm it explicitly"):
        svc.generate(iid, *FILE, include_exported=True)
    plan = svc.file_preview(iid, *FILE, include_exported=True)
    assert (plan.exported_documents, plan.duplicate_documents, plan.include_exported) == (10, 10, True)
    assert all(d.duplicate and d.included and d.state == DONE for d in plan.documents)
    assert {r.export_state for r in plan.rows} == {DONE}
    file = svc.generate(iid, *FILE, include_exported=True, confirm_duplicates=True)
    d = file.run.details
    assert (d["duplicate_override"], d["duplicate_documents"], d["new_documents"]) == (True, 10, 0)
    history = _records(session, "RE/2026/00002")
    assert [r.origin for r in history] == ["EXPORTED", "RE_EXPORTED"] and history[
        1
    ].previous_record_id == history[0].id
    assert history[0].fingerprint == history[1].fingerprint
    assert svc.file_preview(iid, *FILE).exported_documents == 0  # par défaut : toujours rien de nouveau


# --------------------------------------------------------------------------- #
# Échecs : rien n'est marqué exporté
# --------------------------------------------------------------------------- #
@real_spec
def test_a_failed_generation_records_nothing(prep, session, monkeypatch):  # noqa: ANN001
    svc, iid = _file_world(prep)
    real = target_module.header_values

    def bad_header(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        return real(*args, **kwargs) | {"002": "999"}  # valeur d'en-tête refusée par le motif du Registry

    with monkeypatch.context() as m:  # refus du Registry au contrôle final
        m.setattr(target_module, "header_values", bad_header)
        with pytest.raises(ExportInvalid, match="File header, Versionsnummer"):
            svc.generate(iid, *FILE)
    with monkeypatch.context() as m:  # échec de l'écriture du fichier
        m.setattr(type(svc._target(DATEV, BS, "13").writer), "render", lambda *a, **k: 1 / 0)
        with pytest.raises(ZeroDivisionError):
            svc.generate(iid, *FILE)
    with pytest.raises(target_module.ExportChanged):  # contenu changé depuis l'aperçu
        svc.generate(iid, *FILE, expected_digest="0" * 64)
    assert (_count(session, ExportDocumentRecord), _count(session, ExportRunRecord)) == (0, 0)
    assert svc.file_preview(iid, *FILE).new_documents == 10  # toujours tout à exporter


# --------------------------------------------------------------------------- #
# Fichiers générés AVANT le suivi par pièce (phase 5)
# --------------------------------------------------------------------------- #
def _legacy_file(svc, iid):  # noqa: ANN001, ANN202
    """Contenu du fichier tel que la phase 5 l'aurait écrit (calculé AVANT tout fichier d'avant le suivi :
    ensuite, la période est bloquée)."""
    target = svc._target(DATEV, BS, "13")
    settings = svc.settings(iid, DATEV, BS)
    return target_module._serialize(target, svc.preview(iid, *FILE), settings, JANUARY, datetime.now(UTC))


def _legacy_run(svc, iid, session, digest: str | None = None, file=None) -> ExportRunRecord:  # noqa: ANN001
    """Génération telle que la phase 5 l'enregistrait : liste des pièces et empreinte du fichier, aucune ligne
    par pièce."""
    plan, content = file or _legacy_file(svc, iid)
    own = svc.profiles.get(DATEV, BS, GO45_DEV_ORG_ID)
    run = ExportRunRecord(
        organization_id=GO45_DEV_ORG_ID,
        profile_id=own.id,
        instance_id=iid,
        target_label=plan.target,
        binding_version=plan.binding_version,
        encoding=plan.encoding,
        records=plan.row_count,
        file_sha256="0" * 64,
        file_size=len(content),
        period="2026-01",
        details={
            "company_ref": COMPANY,
            "content_digest": digest or plan.content_digest,
            "documents": target_module._row_index(plan.rows),
        },
    )
    session.add(run)
    session.flush()
    return run


@real_spec
def test_legacy_file_blocks_its_documents_until_it_is_adopted_with_proof(prep, session):  # noqa: ANN001
    svc, iid = _file_world(prep)
    run = _legacy_run(svc, iid, session)
    # import du fichier inconnu : ses pièces sont BLOQUÉES (jamais réexportées) jusqu'à son adoption
    cited = {x["ref"] for x in run.details["documents"]}
    blocked = [d for d in svc.preview(iid, *FILE).documents if d.source_ref in cited]
    assert len(blocked) == 10 and all(d.target_status == RecordStatus.BLOCKED for d in blocked)
    assert all(
        any("before document tracking (import unknown)" in i.message for i in d.issues) for d in blocked
    )
    with pytest.raises(ExportInvalid):
        svc.file_preview(iid, *FILE)
    assert [(r.id, tracked) for r, tracked in svc.runs(iid, DATEV, BS)] == [(run.id, False)]
    assert svc.adopt_legacy_run(iid, DATEV, BS, run.id) == 10  # empreinte du fichier = contenu actuel
    records = _records(session)
    assert {r.origin for r in records} == {"ADOPTED"} and {r.export_run_id for r in records} == {run.id}
    after = svc.file_preview(iid, *FILE)
    assert (after.new_documents, after.already_exported_documents, after.exported_documents) == (0, 10, 0)
    assert all(d.legacy_run_id is None for d in after.documents)
    with pytest.raises(AccountingTargetError, match="already tracked"):
        svc.adopt_legacy_run(iid, DATEV, BS, run.id)


@real_spec
def test_legacy_file_is_not_adopted_when_identity_cannot_be_proven(prep, session):  # noqa: ANN001
    svc, iid = _file_world(prep)
    file = _legacy_file(svc, iid)
    run = _legacy_run(svc, iid, session, file=file)
    _remap(svc, iid, "8300", "8310")  # le contenu actuel n'est plus celui du fichier
    with pytest.raises(AccountingTargetError, match="not identical to that file"):
        svc.adopt_legacy_run(iid, DATEV, BS, run.id)
    other = _legacy_run(svc, iid, session, digest="f" * 64, file=file)
    with pytest.raises(AccountingTargetError, match="not identical to that file"):
        svc.adopt_legacy_run(iid, DATEV, BS, other.id)
    assert _count(session, ExportDocumentRecord) == 0  # aucune empreinte fabriquée
    with pytest.raises(target_module.NotFound):
        svc.adopt_legacy_run(iid, DATEV, BS, uuid.uuid4())


@real_spec
def test_fingerprint_is_stable_across_previews_and_ignores_volatile_data(prep):
    svc, iid = _file_world(prep)
    target = svc._target(DATEV, BS, "13")
    doc = _by_ref(svc.preview(iid, *FILE))["UIE-ACC-B"]
    again = _by_ref(svc.preview(iid, *FILE))["UIE-ACC-B"]
    content = target_module._content(target, doc)
    assert content.fingerprint == target_module._content(target, again).fingerprint
    assert content.units == [
        {
            "001": "654,50",
            "002": '"S"',
            "003": '"EUR"',
            "007": doc.bookings[0].partner_account,
            "008": "8400",
            "010": "1501",
            "011": '"RE/2026/00002"',
            "014": '"Lena Hoffmann, RE/2026/00002"',
        },
        {
            "001": "85,39",
            "002": '"S"',
            "003": '"EUR"',
            "007": doc.bookings[0].partner_account,
            "008": "8300",
            "010": "1501",
            "011": '"RE/2026/00002"',
            "014": '"Lena Hoffmann, RE/2026/00002"',
        },
    ]  # exactement les valeurs préparées : ni horodatage, ni nom de fichier, ni rang, ni identifiant
    # deux générations (horodatage et nom de fichier différents) enregistrent la même empreinte
    a = svc.generate(iid, *FILE)
    b = svc.generate(iid, *FILE, include_exported=True, confirm_duplicates=True)
    assert a.run.id != b.run.id
    assert RecordOrigin.EXPORTED.value == "EXPORTED"


@real_spec
def test_generation_refuses_a_period_covered_by_another_source_legacy_file(prep, session):  # noqa: ANN001
    """Doublons entre sources (étape 1) : le fichier d'une AUTRE source (ici un système supprimé), généré
    avant le suivi et jamais adopté, bloque toute la période ; la génération n'écrit rien."""
    svc, iid = _file_world(prep)
    run = _legacy_run(svc, iid, session)
    run.instance_id = None  # système source supprimé : son fichier reste dans l'historique
    session.flush()
    runs, records = _count(session, ExportRunRecord), _count(session, ExportDocumentRecord)
    with pytest.raises(ExportInvalid):
        svc.generate(iid, *FILE)
    assert (_count(session, ExportRunRecord), _count(session, ExportDocumentRecord)) == (runs, records)
    docs = [d for d in svc.preview(iid, *FILE).documents if d.target_status is not None]
    assert all(any("generated from a removed system" in i.message for i in d.issues) for d in docs)
