"""`uie demo prepare` : remise à zéro de la démo puis export des fichiers déclarés, par le MÊME chemin
que la page « Prepare data for DATEV » (comptes réservés, pièces journalisées, registre des factures
ouvertes), avec des réponses déclarées ; une question sans réponse déclarée arrête tout, jamais devinée.
Fichiers INVENTÉS ; specs DATEV locales (module sauté si absentes)."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import yaml
from test_wizard_datev_api import CUSTOMERS, INVOICES, pytestmark, world  # noqa: F401 - fixture partagée
from typer.testing import CliRunner
from uie_cli import demo as cli_demo
from uie_cli.main import app as cli_app
from uie_core.exports.demo import file_accounts
from uie_core.exports.open_items import OpenItemRegister
from uie_core.identity import Organization
from uie_core.wizard.demo import DemoPlan, prepare_demo

SETTINGS = {
    "setting:advisor_number": "1234567",
    "setting:client_number": "10001",
    "setting:lock_bookings": "0",
}


def _files(tmp_path: Path, answers: dict[str, str]) -> Path:
    (tmp_path / "lists").mkdir()
    (tmp_path / "lists" / "Kundenliste.csv").write_bytes(CUSTOMERS.encode("cp1252"))
    (tmp_path / "Rechnungen.csv").write_bytes(INVOICES.encode("cp1252"))
    plan = {
        "answers": answers,
        "files": [
            {"file": "lists/Kundenliste.csv", "target": "datev/debitoren-kreditoren/5"},
            {"file": "Rechnungen.csv", "target": "datev/buchungsstapel/13"},
        ],
    }
    path = tmp_path / "demo_answers.yaml"
    path.write_text(yaml.safe_dump(plan), encoding="utf-8")
    return path


def _use_test_session(monkeypatch, session) -> None:  # noqa: ANN001
    @contextmanager
    def test_session():
        yield session

    monkeypatch.setattr(cli_demo, "session_scope", test_session)


def test_demo_prepare_exports_the_declared_files_and_fills_the_open_items_register(
    world,  # noqa: F811
    monkeypatch,
    tmp_path,
):
    session = world["session"]
    _use_test_session(monkeypatch, session)
    path = _files(tmp_path, {**SETTINGS, "accounting:supply_kind": "GOODS"})
    out = tmp_path / "out"
    res = CliRunner().invoke(
        cli_app, ["demo", "prepare", "--org", "acme", "--answers", str(path), "--out", str(out), "--yes"]
    )
    assert res.exit_code == 0, res.output
    text = " ".join(res.output.split())
    assert "Kundenliste.csv → DATEV Debitoren/Kreditoren 5" in text and "new account numbers reserved" in text
    assert "Rechnungen.csv → DATEV Buchungsstapel 13 (Invoices) : 6 invoices in the file" in text
    assert "accounting:supply_kind = GOODS" in text
    org = session.query(Organization).filter_by(slug="acme").one()
    register = OpenItemRegister(session, org.id, "default", "datev", "buchungsstapel").items()
    assert {i.number for i in register} == {"RE-1", "RE-2", "RE-3", "RE-4", "RE-7", "RE-8"}
    assert file_accounts(session, org.id, "default") >= 6
    assert len(list(out.iterdir())) == 2  # les deux fichiers DATEV préparés

    # même commande une seconde fois : remise à zéro d'abord, donc les mêmes exports (rien en double)
    again = CliRunner().invoke(cli_app, ["demo", "prepare", "--org", "acme", "--answers", str(path), "--yes"])
    assert again.exit_code == 0, again.output
    assert len(OpenItemRegister(session, org.id, "default", "datev", "buchungsstapel").items()) == 6


def test_a_question_without_a_declared_answer_stops_the_demo_preparation(world, monkeypatch, tmp_path):  # noqa: F811
    _use_test_session(monkeypatch, world["session"])
    path = _files(tmp_path, SETTINGS)  # « goods or services? » sans réponse déclarée
    res = CliRunner().invoke(cli_app, ["demo", "prepare", "--org", "acme", "--answers", str(path), "--yes"])
    assert res.exit_code == 1
    text = " ".join(res.output.split())
    assert "Arrêt, rien n'est enregistré. Rechnungen.csv: question" in text
    assert "add `accounting:supply_kind` (options: GOODS, SERVICES, skip)" in text


def test_the_summary_counts_the_written_records(world, tmp_path):  # noqa: F811
    """Le résumé d'un téléchargement donne le NOMBRE d'enregistrements écrits (il était remplacé par
    l'ensemble des pièces écrites, dans le registre des factures ouvertes)."""
    session = world["session"]
    path = _files(tmp_path, {**SETTINGS, "accounting:supply_kind": "GOODS"})
    plan = DemoPlan.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    data = {f.file: (tmp_path / f.file).read_bytes() for f in plan.files}
    ctx = cli_demo._tenant(session, "acme", None)
    results = prepare_demo(cli_demo._wizard(session, ctx), plan, data)
    assert [r.summary.rows for r in results] == [8, 6]  # 8 clients, 6 factures
    assert all(isinstance(r.summary.rows, int) for r in results)
