"""`uie admin bootstrap` (V1d) : idempotent, mot de passe jamais en argument ni affiché."""

from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import func, select
from typer.testing import CliRunner
from uie_cli import identity as cli_identity
from uie_cli.main import app as cli_app
from uie_core.identity import User

PASSWORD = "B00tstrap-Admin-Pass!"


def _use_test_session(monkeypatch, session) -> None:  # noqa: ANN001
    @contextmanager
    def test_session():
        yield session

    monkeypatch.setattr(cli_identity, "session_scope", test_session)


def test_bootstrap_creates_once_then_does_nothing(monkeypatch, session):
    _use_test_session(monkeypatch, session)
    monkeypatch.setenv("UIE_INITIAL_ADMIN_EMAIL", "Root@GO45.local")
    monkeypatch.setenv("UIE_INITIAL_ADMIN_PASSWORD", PASSWORD)
    runner = CliRunner()
    first = runner.invoke(cli_app, ["admin", "bootstrap"])
    assert first.exit_code == 0, first.output
    assert "créé" in first.output and "root@go45.local" in first.output and PASSWORD not in first.output
    second = runner.invoke(cli_app, ["admin", "bootstrap"])
    assert second.exit_code == 0 and "Déjà présent" in second.output
    assert session.scalar(select(func.count()).select_from(User)) == 1


def test_bootstrap_without_password_fails_explicitly(monkeypatch, session):
    _use_test_session(monkeypatch, session)
    monkeypatch.delenv("UIE_INITIAL_ADMIN_PASSWORD", raising=False)
    res = CliRunner().invoke(cli_app, ["admin", "bootstrap"])
    assert res.exit_code == 1 and "UIE_INITIAL_ADMIN_PASSWORD" in res.output
    assert session.scalar(select(func.count()).select_from(User)) == 0


# --------------------------------------------------------------------------- #
# uie org / uie user
# --------------------------------------------------------------------------- #
USER_PW = "Supplier-Initial-Pass-3"


def test_org_and_user_commands(monkeypatch, session):
    _use_test_session(monkeypatch, session)
    monkeypatch.setenv("COLUMNS", "220")  # tableaux Rich non tronqués
    runner = CliRunner()

    def run(*args: str, **kw):  # noqa: ANN003, ANN202
        res = runner.invoke(cli_app, list(args), **kw)
        assert USER_PW not in res.output and "argon2" not in res.output
        return res

    r = run("org", "create", "--name", "Acme Tools GmbH")
    assert r.exit_code == 0 and "acme-tools-gmbh" in r.output, r.output
    assert run("org", "create", "--name", "Acme Tools GmbH").exit_code == 1  # slug déjà pris
    r = run("org", "list")
    assert "acme-tools-gmbh" in r.output and "go45-development" in r.output

    # sans mot de passe fourni et hors terminal : refus explicite, rien n'est créé
    monkeypatch.delenv("UIE_USER_PASSWORD", raising=False)
    r = run("user", "create", "--email", "anna@acme.io", "--org", "acme-tools-gmbh")
    assert r.exit_code == 1 and "UIE_USER_PASSWORD" in r.output
    assert session.scalar(select(func.count()).select_from(User)) == 0

    r = run(
        "user",
        "create",
        "-e",
        "Anna@Acme.io",
        "-n",
        "Anna",
        "--org",
        "acme-tools-gmbh",
        "--role",
        "OWNER",
        "--password-stdin",
        input=USER_PW + "\n",
    )
    assert r.exit_code == 0 and "anna@acme.io" in r.output and "OWNER" in r.output, r.output
    monkeypatch.setenv("UIE_USER_PASSWORD", USER_PW)
    assert run("user", "create", "-e", "anna@acme.io", "--org", "acme-tools-gmbh").exit_code == 1  # doublon
    assert run("user", "create", "-e", "ops@go45.local", "--admin").exit_code == 0
    assert run("user", "create", "-e", "x@y.io", "--admin", "--org", "acme-tools-gmbh").exit_code == 1

    assert run("user", "add-member", "-e", "nobody@acme.io", "--org", "acme-tools-gmbh").exit_code == 1
    assert run("user", "add-member", "-e", "ops@go45.local", "--org", "acme-tools-gmbh").exit_code == 0
    assert run("org", "show", "missing-org").exit_code == 1

    r = run("org", "show", "acme-tools-gmbh")
    assert "anna@acme.io" in r.output and "ops@go45.local" in r.output
    r = run("user", "list")
    assert "ADMIN" in r.output and "acme-tools-gmbh" in r.output
    anna = session.scalar(select(User).where(User.email == "anna@acme.io"))
    assert anna.display_name == "Anna" and anna.platform_role == "NONE"
    assert anna.password_hash.startswith("$argon2id$")
