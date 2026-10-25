"""Doublons de pièces comptables ENTRE SOURCES (étape 1, sans migration) dans la préparation DATEV existante.

Source préparée : faux serveur Lexware (pièces INVENTÉES). « Autre source » : une deuxième instance de la même
organisation (même faux serveur : mêmes pièces), ou une lecture simulée. Lignes de registre par pièce, liens
d'envoi et fichiers d'avant le suivi sont posés directement (état d'exports PASSÉS) : le registre, les
historiques et la cible DATEV ne sont jamais modifiés par la protection. Aucun fichier, aucun export."""

# ruff: noqa: F811  (fixtures importées et utilisées en paramètre)
from __future__ import annotations

import uuid
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from test_accounting_category_mapping import BS, DATEV, SPEC, cat  # noqa: F401  (fixture)
from test_accounting_lexware_source import JANUARY, acc  # noqa: F401  (fixture)
from uie_connectors.lexware.testing import FAKE_KEY
from uie_core.accounting.duplicates import RULE, CrossSourceGuard
from uie_core.delivery.records import TargetResourceLink
from uie_core.exports import ExportDocumentRecord, ExportRunRecord
from uie_core.mapping import IssueLevel, RecordStatus

pytestmark = pytest.mark.skipif(not SPEC.exists(), reason="spec DATEV locale absente (spec_builder)")


@pytest.fixture
def dup(cat):  # noqa: ANN001, ANN201
    """Deuxième source de la même organisation (« origin »), lisible par le même faux serveur."""
    origin, _ = cat["reader"].instances.connect_values("origin", "lexware", {"api_key": SecretStr(FAKE_KEY)})
    return cat | {"origin": origin}


def preview(w):  # noqa: ANN001, ANN201
    return {d.number: d for d in w["svc"].preview(w["inst"].id, "single", JANUARY, DATEV, BS).documents}


def rule_issues(doc) -> list[Any]:  # noqa: ANN001
    return [i for i in doc.issues if i.rule_id == RULE]


def exported(w, instance_id, ref: str, number: str | None = None) -> None:  # noqa: ANN001
    """Ligne de registre d'un export DATEV PASSÉ (état existant, posé pour le test), avec son fichier."""
    run = legacy(w, instance_id, [ref])  # fichier généré...
    w["session"].add(
        ExportDocumentRecord(
            organization_id=w["inst"].organization_id,
            instance_id=instance_id,
            domain="accounting",
            target_system=DATEV,
            target_product=BS,
            scope_ref="single",
            source_ref=ref,
            source_number=number,
            fingerprint="0" * 64,
            field_digests=[],
            units=1,
            origin="EXPORTED",
            export_run_id=run.id,  # ... et suivi par pièce (donc pas un fichier « legacy »)
        )
    )
    w["session"].flush()


def linked(w, source_id, source_ref: str, target_id, target_ref: str) -> None:  # noqa: ANN001
    """Lien d'envoi PASSÉ : la pièce `source_ref` de `source_id` a été créée comme `target_ref`."""
    w["session"].add(
        TargetResourceLink(
            organization_id=w["inst"].organization_id,
            source_instance_id=source_id,
            target_instance_id=target_id,
            domain="accounting",
            resource="vouchers",
            source_ref=source_ref,
            target_id=target_ref,
            target_version=1,
            origin="SENT",
        )
    )
    w["session"].flush()


def legacy(w, instance_id, refs: list[str]) -> ExportRunRecord:  # noqa: ANN001
    """Fichier généré (sans ligne de registre : AVANT le suivi par pièce), comme la phase 5 l'enregistrait."""
    own = w["svc"].profiles.get(DATEV, BS, w["inst"].organization_id)
    run = ExportRunRecord(
        organization_id=w["inst"].organization_id,
        profile_id=own.id,
        instance_id=instance_id,
        target_label="DATEV Buchungsstapel (EXTF) · version 13",
        binding_version="1.1.0",
        encoding="cp1252",
        records=len(refs),
        file_sha256="0" * 64,
        file_size=1,
        period="2026-01",
        details={"documents": [{"ref": r} for r in refs]},
    )
    w["session"].add(run)
    w["session"].flush()
    return run


# --------------------------------------------------------------------------- #
def test_without_another_source_nothing_changes(dup):
    assert all(rule_issues(d) == [] for d in preview(dup).values())


def test_certain_lineage_already_exported_elsewhere_is_blocked(dup):
    linked(dup, dup["origin"].id, "odoo-77", dup["inst"].id, "v-1")  # v-1 a été créée depuis « origin »
    exported(dup, dup["origin"].id, "odoo-77", "RE/2026/00077")
    docs = preview(dup)
    issues = rule_issues(docs["NR-v-1"])
    assert [(i.level, i.message) for i in issues] == [
        (
            IssueLevel.BLOCKING,
            "Same document as RE/2026/00077 in origin, already exported from there: not exported twice",
        )
    ]
    assert docs["NR-v-1"].target_status == RecordStatus.BLOCKED
    assert rule_issues(docs["NR-v-2"]) == []  # sans filiation ni correspondance : rien


def test_certain_lineage_not_exported_yet_is_a_warning_in_both_directions(dup):
    linked(dup, dup["inst"].id, "v-2", dup["origin"].id, "lex-2")  # v-2 a été ENVOYÉE vers « origin »
    issues = rule_issues(preview(dup)["NR-v-2"])
    assert [(i.level, i.message) for i in issues] == [
        (IssueLevel.WARNING, "Same document as origin: export it from one system only")
    ]


def test_strong_business_match_with_a_document_exported_from_another_source_is_blocked(dup):
    exported(
        dup, dup["origin"].id, "v-1", "NR-v-1"
    )  # « origin » a exporté la MÊME pièce (relue à la demande)
    docs = preview(dup)
    assert [i.message for i in rule_issues(docs["NR-v-1"])] == [
        "Already exported from origin as NR-v-1 (same number, amount and date): not exported twice"
    ]
    assert rule_issues(docs["NR-v-2"]) == []  # autre pièce : jamais bloquée


def _guard(dup, read):  # noqa: ANN001, ANN202
    docs = {
        i.document.source_ref: i.document
        for i in dup["reader"].read(dup["inst"].id, "single", JANUARY).items
        if i.document is not None
    }
    return docs, CrossSourceGuard(dup["session"], read).check(
        organization_id=dup["inst"].organization_id,
        instance_id=dup["inst"].id,
        domain="accounting",
        target_system=DATEV,
        target_product=BS,
        period=JANUARY,
        documents=docs,
    )


def test_same_values_and_partner_with_another_number_is_a_possible_duplicate_never_by_name_alone(dup):
    exported(dup, dup["origin"].id, "x-1", "OTHER-1")
    base = {
        d.source_ref: d
        for d in [
            i.document for i in dup["reader"].read(dup["inst"].id, "single", JANUARY).items if i.document
        ]
    }
    other = base["v-1"].model_copy(update={"number": "OTHER-1", "source_ref": "x-1"})
    _, out = _guard(dup, lambda iid, company, period: [("x-1", other)])
    assert [i.message for i in out["v-1"]] == [
        "Possible duplicate of OTHER-1 exported from origin (same partner, amount and date, "
        "different number): not exported until this is clarified"
    ]
    # même partenaire mais montant différent : jamais rapproché
    changed = other.model_copy(update={"total_amount": other.total_amount + 1})
    _, out = _guard(dup, lambda iid, company, period: [("x-1", changed)])
    assert "v-1" not in out


def test_unreadable_other_source_blocks_the_check(dup):
    exported(dup, dup["origin"].id, "x-1", "OTHER-1")

    def fail(iid, company, period):  # noqa: ANN001, ANN202
        raise RuntimeError("unreachable")

    docs, out = _guard(dup, fail)
    assert set(out) == set(docs)
    assert {i.message for issues in out.values() for i in issues} == {
        "Duplicate check not possible: origin could not be read"
    }


def test_legacy_file_of_another_source_blocks_the_whole_period(dup):
    run = legacy(dup, dup["origin"].id, ["odoo-1", "odoo-2"])
    when = run.created_at.strftime("%Y-%m-%d")
    docs = preview(dup)
    for d in docs.values():
        if d.target_status is None:
            continue  # brouillons / annulées : jamais préparées
        assert [i.message for i in rule_issues(d)] == [
            f"A file for this period was generated from origin on {when} before document tracking "
            "(import unknown): adopt it in origin first"
        ]
        assert d.target_status == RecordStatus.BLOCKED


def test_legacy_file_of_the_same_source_blocks_only_its_documents(dup):
    legacy(dup, dup["inst"].id, ["v-1"])
    docs = preview(dup)
    assert (
        len(rule_issues(docs["NR-v-1"])) == 1
        and "adopt that file first" in rule_issues(docs["NR-v-1"])[0].message
    )
    assert rule_issues(docs["NR-v-2"]) == []


def test_the_guard_writes_nothing_and_never_changes_history(dup):
    exported(dup, dup["origin"].id, "v-1", "NR-v-1")
    legacy(dup, dup["origin"].id, ["odoo-1"])
    session = dup["session"]
    tables = (ExportDocumentRecord, ExportRunRecord, TargetResourceLink)
    before = [session.scalar(select(func.count()).select_from(t)) for t in tables]
    preview(dup)
    assert [session.scalar(select(func.count()).select_from(t)) for t in tables] == before
    assert dup["server"].writes == []


def test_guard_is_system_free():
    from pathlib import Path

    py = (
        Path(__file__).resolve().parents[2]
        / "packages"
        / "core"
        / "src"
        / "uie_core"
        / "accounting"
        / "duplicates.py"
    )
    low = py.read_text(encoding="utf-8").lower()
    assert not any(w in low for w in ("lexware", "odoo", "datev", "voucher"))
    assert uuid  # (import utilisé pour les fixtures)
