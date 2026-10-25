"""`uie registry import --spec / --all` et `--product` (loaders à spec, ex. DATEV ; fixtures inventées)."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest
from typer.testing import CliRunner
from uie_cli import main as cli_main
from uie_cli.main import app as cli_app

FIX = Path(__file__).resolve().parents[3] / "packages" / "connectors" / "tests" / "fixtures" / "datev"


def _use_test_session(monkeypatch, session) -> None:  # noqa: ANN001
    @contextmanager
    def test_session():
        yield session

    monkeypatch.setattr(cli_main, "session_scope", test_session)


def test_import_specs_then_show_fields_and_compare_by_product(monkeypatch, session):
    _use_test_session(monkeypatch, session)
    monkeypatch.setenv("COLUMNS", "220")
    runner = CliRunner()

    one = runner.invoke(cli_app, ["registry", "import", "datev", "--spec", str(FIX / "demo-stamm_v1.yaml")])
    assert one.exit_code == 0, one.output
    assert "datev demo-stamm 1" in one.output and "@1.0.0" in one.output

    every = runner.invoke(cli_app, ["registry", "import", "datev", "--all", "--spec", str(FIX)])
    assert every.exit_code == 0, every.output
    assert every.output.count("✓ snapshot") == 3  # demo-stamm v1 déjà publié : même snapshot rendu

    show = runner.invoke(cli_app, ["registry", "show", "datev", "1", "demo_stamm", "-P", "demo-stamm"])
    assert show.exit_code == 0, show.output
    assert "001" in show.output and "Nummer" in show.output

    fields = runner.invoke(
        cli_app, ["registry", "fields", "datev", "2", "-s", "Betrag", "-P", "demo-journal"]
    )
    assert fields.exit_code == 0 and "datev demo-journal 2 @1.0.0" in fields.output

    cmp = runner.invoke(cli_app, ["registry", "compare", "datev", "1", "datev", "2", "-P", "demo-journal"])
    assert cmp.exit_code == 0, cmp.output
    assert "datev demo-journal 1 @1.0.0 → datev demo-journal 2 @1.0.0" in cmp.output
    assert "+ demo_journal.005" in cmp.output and "length: 8 → 10" in cmp.output


def test_import_all_without_specs_fails(tmp_path):
    res = CliRunner().invoke(cli_app, ["registry", "import", "datev", "--all", "--spec", str(tmp_path)])
    assert res.exit_code == 1 and "Aucune spec" in res.output


REAL_SPEC = (
    Path(__file__).resolve().parents[3]
    / "var"
    / "datev"
    / "specs"
    / "extf_700"
    / "debitoren-kreditoren_v5.yaml"
)


@pytest.mark.skipif(not REAL_SPEC.exists(), reason="spec DATEV locale absente (spec_builder)")
def test_export_profile_cli(monkeypatch, session):
    from uie_cli import partners as cli_partners
    from uie_core.identity import Organization

    @contextmanager
    def test_session():
        yield session

    monkeypatch.setattr(cli_main, "session_scope", test_session)
    monkeypatch.setattr(cli_partners, "session_scope", test_session)
    monkeypatch.setenv("COLUMNS", "220")
    session.add(Organization(name="Acme", slug="acme"))
    session.flush()
    runner = CliRunner()
    assert runner.invoke(cli_app, ["registry", "import", "datev", "--spec", str(REAL_SPEC)]).exit_code == 0
    targets = runner.invoke(cli_app, ["export", "targets"])
    assert "datev/debitoren-kreditoren" in targets.output
    base = ["export", "profile", "set", "-t", "datev/debitoren-kreditoren", "--org", "acme"]
    bad = runner.invoke(cli_app, [*base, "--set", "account_length=9"])
    assert bad.exit_code == 1 and "account_length" in bad.output
    values = ["advisor_number=1001", "client_number=1", "fiscal_year_start=2026-01-01", "account_length=4"]
    values += ["debtor_range=10000-69999", "creditor_range=70000-99999"]
    ok = runner.invoke(cli_app, [*base, *[x for v in values for x in ("--set", v)]])
    assert ok.exit_code == 0, ok.output
    assert '"start": 10000' in ok.output and "1001" in ok.output
