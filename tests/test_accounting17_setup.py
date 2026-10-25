"""Script de configuration d'accounting17 (docker/accounting17-setup.py) : références stables, uniques, refus
de toucher customer17 ; idempotence vérifiée contre l'Odoo de test réel seulement si UIE_TEST_ACCOUNTING17=1
(le script est relancé : aucune base, aucun partenaire, aucune pièce ne doit être recréé)."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "docker" / "accounting17-setup.py"


def _module():  # noqa: ANN202
    spec = importlib.util.spec_from_file_location("accounting17_setup", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_stable_unique_references_and_scenarios():
    mod = _module()
    refs = [d["ref"] for d in mod.DOCUMENTS]
    assert len(refs) == len(set(refs)) and all(r.startswith("UIE-ACC-") for r in refs)
    types = {d["type"] for d in mod.DOCUMENTS}
    assert types == {"out_invoice", "out_refund", "in_invoice", "in_refund"}
    states = {d.get("state", "posted") for d in mod.DOCUMENTS}
    assert states == {"posted", "draft", "cancel"}
    keys = {p["key"] for p in mod.PARTNERS}
    assert {d["partner"] for d in mod.DOCUMENTS} <= keys
    long_name = next(p for p in mod.PARTNERS if p["key"] == "longname")
    assert len(long_name["name"]) > 50  # refusé par Debitoren/Kreditoren : cas « partenaire sans compte »


def test_customer17_is_never_configured():
    with pytest.raises(SystemExit, match="customer17"):
        _module().main(["--db", "customer17"])


@pytest.mark.skipif(
    os.environ.get("UIE_TEST_ACCOUNTING17") != "1", reason="Odoo de test accounting17 non demandé"
)
def test_rerun_is_idempotent_against_the_real_test_instance():
    run = subprocess.run(
        [sys.executable, str(SCRIPT)], capture_output=True, text=True, encoding="utf-8", timeout=600
    )
    assert run.returncode == 0, run.stderr[-2000:]
    assert ": créé" not in run.stdout and ": création" not in run.stdout, run.stdout


def test_every_document_has_a_standard_payment_term():
    """Sans condition de paiement, Odoo 17 prend la date de CRÉATION de la pièce pour échéance (phase 3.1) :
    chaque pièce en reçoit une, standard (identifiant externe du module account), échéances variées."""
    mod = _module()
    terms = [d.get("payment_term", mod.DEFAULT_PAYMENT_TERM) for d in mod.DOCUMENTS]
    assert all(t.startswith("account_payment_term_") for t in terms)
    assert len(set(terms)) > 1
