"""Plusieurs dossiers (mandants) par organisation pour un format de fichier : réglages, historique des pièces
et numéros de compte propres à chaque dossier ; remise à zéro de la démo réservée à la plateforme."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import uie_domain_packs  # noqa: F401 - bindings cibles
from uie_connectors.datev import DatevSchemaLoader
from uie_core.exports import ExportProfileService
from uie_core.exports.demo import reset_file_exports
from uie_core.exports.documents import DocumentContent, DocumentExportLedger, DocumentScope, RecordOrigin
from uie_core.exports.records import ExportRunRecord
from uie_core.identity import Organization, SystemContext
from uie_core.identity.context import MembershipRole, PermissionDenied, TenantContext
from uie_core.registry import RegistryService

SPEC = Path(__file__).resolve().parents[2] / "var" / "datev" / "specs" / "extf_700"
pytestmark = pytest.mark.skipif(
    not (SPEC / "debitoren-kreditoren_v5.yaml").exists(), reason="spec DATEV locale absente"
)
VALUES = {
    "advisor_number": 1234567,
    "fiscal_year_start": "2026-01-01",
    "account_length": 4,
    "debtor_range": {"start": 10000, "end": 69999},
    "creditor_range": {"start": 70000, "end": 99999},
}


def test_two_clients_of_one_organization_never_share_settings_or_history(session):  # noqa: ANN001
    RegistryService(session, SystemContext("test")).publish_snapshot(
        DatevSchemaLoader({"spec": SPEC / "debitoren-kreditoren_v5.yaml"}).build_snapshot()
    )
    org = Organization(name="Kanzlei", slug="kanzlei")
    session.add(org)
    session.flush()
    profiles = ExportProfileService(session, SystemContext("test"))
    a = profiles.save("datev", "debitoren-kreditoren", VALUES | {"client_number": 10001}, org.id, "mandant-a")
    b = profiles.save("datev", "debitoren-kreditoren", VALUES | {"client_number": 20002}, org.id, "mandant-b")
    assert a.id != b.id  # un jeu de réglages par mandant
    assert profiles.get("datev", "debitoren-kreditoren", org.id, "mandant-b").values["client_number"] == 20002
    assert profiles.get("datev", "debitoren-kreditoren", org.id) is None  # « default » : pas encore créé

    ledger = DocumentExportLedger(session)
    scope = {
        "organization_id": org.id,
        "instance_id": None,
        "domain": "accounting",
        "target_system": "datev",
        "target_product": "buchungsstapel",
    }
    run = ExportRunRecord(
        organization_id=org.id,
        profile_id=a.id,
        instance_id=None,
        target_label="t",
        target_snapshot_id=None,
        binding_version="1",
        read_plan_checksum="file",
        encoding="cp1252",
        records=1,
        warnings=0,
        blocked=0,
        new_accounts=0,
        file_sha256="0" * 64,
        file_size=1,
        details={"source": "file"},
    )
    session.add(run)
    session.flush()
    doc = DocumentContent(source_ref="file:RE-1", number="RE-1", units=[{"001": "1,00"}])
    ledger.record(DocumentScope(**scope, client_key="mandant-a"), run, [(doc, RecordOrigin.EXPORTED, None)])
    seen_a = ledger.classify(DocumentScope(**scope, client_key="mandant-a"), [doc], {})
    seen_b = ledger.classify(DocumentScope(**scope, client_key="mandant-b"), [doc], {})
    assert seen_a["file:RE-1"].state.value == "ALREADY_EXPORTED"
    assert seen_b["file:RE-1"].state.value == "NEW"  # même numéro chez un autre mandant : jamais confondu

    tenant = TenantContext(user_id=uuid.uuid4(), organization_id=org.id, membership_role=MembershipRole.OWNER)
    with pytest.raises(PermissionDenied):  # réservé à la plateforme (CLI), jamais au portail
        reset_file_exports(session, tenant, org.id)
    done = reset_file_exports(session, SystemContext("test"), org.id, "mandant-b")
    assert done.documents == 0  # un dossier seulement : celui de l'autre mandant reste intact
    done = reset_file_exports(session, SystemContext("test"), org.id, "mandant-a")
    assert (done.documents, done.runs) == (1, 1)
    assert (
        ledger.classify(DocumentScope(**scope, client_key="mandant-a"), [doc], {})["file:RE-1"].state.value
        == "NEW"
    )


def test_reset_with_settings_never_takes_the_history_of_a_connected_source(session):  # noqa: ANN001
    """Réglages remis à zéro pour la démo (Beraternummer, Mandantennummer... à saisir de nouveau) ; un dossier
    encore utilisé par un système CONNECTÉ garde son historique (sa suppression l'emporterait en CASCADE)."""
    RegistryService(session, SystemContext("test")).publish_snapshot(
        DatevSchemaLoader({"spec": SPEC / "debitoren-kreditoren_v5.yaml"}).build_snapshot()
    )
    org = Organization(name="Kanzlei", slug="kanzlei")
    session.add(org)
    session.flush()
    profiles = ExportProfileService(session, SystemContext("test"))
    used = profiles.save("datev", "debitoren-kreditoren", VALUES | {"client_number": 1}, org.id, "mandant-a")
    profiles.save("datev", "debitoren-kreditoren", VALUES | {"client_number": 2}, org.id, "mandant-b")
    run = (
        ExportRunRecord(  # fichier d'un système connecté (instance renseignée en réalité ; ici sans « file »)
            organization_id=org.id,
            profile_id=used.id,
            instance_id=None,
            target_label="t",
            target_snapshot_id=None,
            binding_version="1",
            read_plan_checksum="odoo",
            encoding="cp1252",
            records=1,
            warnings=0,
            blocked=0,
            new_accounts=0,
            file_sha256="0" * 64,
            file_size=1,
            details={},
        )
    )
    session.add(run)
    session.flush()
    done = reset_file_exports(session, SystemContext("test"), org.id, with_settings=True)
    assert (done.runs, done.profiles) == (0, 2)
    kept = profiles.get("datev", "debitoren-kreditoren", org.id, "mandant-a")
    assert kept is not None and kept.values == {}  # réglages à saisir de nouveau, historique gardé
    assert session.get(ExportRunRecord, run.id) is not None
    assert profiles.get("datev", "debitoren-kreditoren", org.id, "mandant-b") is None  # inutilisé : retiré
