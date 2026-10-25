"""Catalogue des use cases de la console (source de vérité des pages Overview) : ses tests `node --test` sont
exécutés ici pour que `uv run pytest` les couvre (identifiants, audiences, statuts, routes existantes,
couverture de chaque page de l'application, filtrage admin / fournisseur, démarrage selon le statut)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "apps" / "web"
TEST = WEB / "src" / "lib" / "use-cases" / "catalog.test.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node absent")
def test_use_case_catalog_node_suite():
    run = subprocess.run(
        ["node", "--test", str(TEST)], cwd=WEB, capture_output=True, text=True, encoding="utf-8", timeout=120
    )
    assert run.returncode == 0, run.stdout[-4000:] + run.stderr[-2000:]
    assert "# fail 0" in run.stdout


ACCOUNTING_PRESENTATION = WEB / "src" / "lib" / "accounting" / "presentation.test.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node absent")
def test_accounting_presentation_node_suite():
    """Vue Débit / Crédit des montants signés du CDM, solde, titres de ligne, libellé « Eligible »
    (phase 3.1)."""
    run = subprocess.run(
        ["node", "--test", str(ACCOUNTING_PRESENTATION)],
        cwd=WEB,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert run.returncode == 0, run.stdout[-4000:] + run.stderr[-2000:]
    assert "# fail 0" in run.stdout


ACCOUNTING_PREPARATION = WEB / "src" / "lib" / "accounting" / "preparation.test.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node absent")
def test_accounting_preparation_node_suite():
    """Préparation DATEV (phase 4) : statuts cibles, suggestion ≠ décision, cellules telles qu'écrites."""
    run = subprocess.run(
        ["node", "--test", str(ACCOUNTING_PREPARATION)],
        cwd=WEB,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert run.returncode == 0, run.stdout[-4000:] + run.stderr[-2000:]
    assert "# fail 0" in run.stdout
