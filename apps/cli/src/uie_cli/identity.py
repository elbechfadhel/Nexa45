"""`uie admin ...`, `uie org ...`, `uie user ...` : administration de la plateforme GO45 (V1d).

Mêmes services Core que l'API Admin (OrganizationService), contexte SystemContext("cli").
Mot de passe d'un utilisateur : jamais en argument (historique shell, liste des processus) ; variable
UIE_USER_PASSWORD, `--password-stdin`, ou saisie masquée avec confirmation.

`uie admin bootstrap` crée l'administrateur plateforme initial, une seule fois (idempotent) :
email = --email ou UIE_INITIAL_ADMIN_EMAIL (défaut admin@go45.local) ; mot de passe =
UIE_INITIAL_ADMIN_PASSWORD ou saisie masquée, jamais en argument. La migration de la base ne crée aucun
compte.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

import typer
from pydantic import SecretStr
from rich.console import Console
from rich.markup import escape
from rich.table import Table
from uie_core.db import session_scope
from uie_core.identity import (
    MembershipRole,
    PasswordError,
    PlatformRole,
    SystemContext,
    bootstrap_platform_admin,
)
from uie_core.identity.organizations import OrganizationService
from uie_core.registry import RegistryError

app = typer.Typer(help="Administration de la plateforme GO45 (comptes)", no_args_is_help=True)
org_app = typer.Typer(help="Organisations (suppliers) : liste, création, détail", no_args_is_help=True)
user_app = typer.Typer(help="Utilisateurs : création, ajout à une organisation, liste", no_args_is_help=True)
console = Console()
err = Console(stderr=True, style="bold red")

DEFAULT_ADMIN_EMAIL = "admin@go45.local"
USER_PASSWORD_ENV = "UIE_USER_PASSWORD"


def _fail(msg: str) -> None:
    err.print(escape(msg))
    raise typer.Exit(1)


@contextmanager
def organization_service() -> Iterator[OrganizationService]:
    try:
        with session_scope() as s:
            yield OrganizationService(s, SystemContext("cli"))
    except (RegistryError, PasswordError) as exc:
        _fail(str(exc))


def _read_password(from_stdin: bool) -> SecretStr:
    if env := os.environ.get(USER_PASSWORD_ENV):
        return SecretStr(env)
    if from_stdin:
        return SecretStr(sys.stdin.readline().rstrip("\r\n"))
    if not console.is_terminal:
        _fail(f"{USER_PASSWORD_ENV} ou --password-stdin requis (aucune saisie possible hors terminal)")
    return SecretStr(typer.prompt("Mot de passe initial", hide_input=True, confirmation_prompt=True))


@app.command("bootstrap")
def bootstrap(
    email: Annotated[
        str | None, typer.Option("--email", help="Défaut : UIE_INITIAL_ADMIN_EMAIL ou admin@go45.local")
    ] = None,
    display_name: Annotated[str, typer.Option("--name")] = "GO45 Administrator",
) -> None:
    """Crée l'administrateur plateforme initial s'il n'existe pas (idempotent, jamais de doublon)."""
    address = email or os.environ.get("UIE_INITIAL_ADMIN_EMAIL") or DEFAULT_ADMIN_EMAIL
    raw = os.environ.get("UIE_INITIAL_ADMIN_PASSWORD")
    if not raw:
        if not console.is_terminal:
            err.print("UIE_INITIAL_ADMIN_PASSWORD manquant (aucune saisie possible hors terminal)")
            raise typer.Exit(1)
        raw = typer.prompt("Mot de passe de l'administrateur", hide_input=True, confirmation_prompt=True)
    try:
        with session_scope() as s:
            result = bootstrap_platform_admin(s, address, SecretStr(raw), display_name)
    except PasswordError as exc:
        err.print(str(exc))
        raise typer.Exit(1) from None
    if result.created:
        console.print(f"[green]✓ Administrateur plateforme créé[/green] {result.email} ({result.user_id})")
    elif result.platform_role == "ADMIN":
        console.print(f"[dim]Déjà présent[/dim] {result.email} : rien n'a été modifié")
    else:
        console.print(
            f"[yellow]Un utilisateur {result.email} existe déjà sans rôle ADMIN[/yellow] : rien n'a été "
            "modifié (promotion non automatique)"
        )


# --------------------------------------------------------------------------- #
# org
# --------------------------------------------------------------------------- #
@org_app.command("list")
def org_list() -> None:
    """Organisations, avec leurs utilisateurs et instances comptés."""
    with organization_service() as svc:
        t = Table("Nom", "Slug", "Statut", "Utilisateurs", "Instances", "Créée", "Id")
        for o in svc.list():
            t.add_row(
                o.name,
                o.slug,
                o.status,
                str(o.user_count),
                str(o.instance_count),
                f"{o.created_at:%Y-%m-%d}",
                str(o.id),
            )
        console.print(t)


@org_app.command("create")
def org_create(
    name: Annotated[str, typer.Option("--name", "-n")],
    slug: Annotated[str | None, typer.Option("--slug", help="Dérivé du nom si absent")] = None,
) -> None:
    """Crée une organisation (supplier)."""
    with organization_service() as svc:
        o = svc.create(name, slug)
        console.print(
            f"[green]✓[/green] organisation [bold]{escape(o.name)}[/bold] ({o.slug}) créée : {o.id}"
        )


@org_app.command("show")
def org_show(ref: Annotated[str, typer.Argument(help="Slug ou id de l'organisation")]) -> None:
    """Détail : utilisateurs et rôles (instances : uie instance list)."""
    with organization_service() as svc:
        o = svc.resolve(ref)
        console.print(
            f"[bold cyan]{escape(o.name)}[/bold cyan]  {o.slug}  statut={o.status}  "
            f"instances={o.instance_count}  [dim]{o.id}[/dim]"
        )
        members = svc.list_members(o.id)
        if not members:
            console.print(
                "[dim]Aucun utilisateur. Ajouter : uie user create --org ... | uie user add-member[/dim]"
            )
            return
        t = Table("Email", "Nom", "Rôle", "Statut", "Depuis")
        for mv in members:
            t.add_row(mv.email, mv.display_name, mv.role.value, mv.status, f"{mv.joined_at:%Y-%m-%d}")
        console.print(t)


# --------------------------------------------------------------------------- #
# user
# --------------------------------------------------------------------------- #
@user_app.command("create")
def user_create(
    email: Annotated[str, typer.Option("--email", "-e")],
    name: Annotated[str | None, typer.Option("--name", "-n")] = None,
    org: Annotated[str | None, typer.Option("--org", help="Slug ou id : ajoute aussi le membership")] = None,
    role: Annotated[MembershipRole, typer.Option("--role", help="Rôle dans --org")] = MembershipRole.MEMBER,
    admin: Annotated[bool, typer.Option("--admin", help="Administrateur plateforme (sans --org)")] = False,
    password_stdin: Annotated[
        bool, typer.Option("--password-stdin", help="Lire le mot de passe sur stdin")
    ] = False,
) -> None:
    """Crée un utilisateur avec son mot de passe initial (jamais affiché ni passé en argument)."""
    if admin and org:
        _fail("--admin et --org sont exclusifs : un administrateur plateforme n'a pas besoin de membership")
    password = _read_password(password_stdin)
    with organization_service() as svc:
        if org:
            o = svc.resolve(org)
            added = svc.add_member(o.id, email, display_name=name, password=password, role=role)
            if not added.user_created:
                _fail(f"Un utilisateur {added.member.email} existe déjà : uie user add-member")
            console.print(
                f"[green]✓[/green] utilisateur {added.member.email} créé, {role.value} de {escape(o.name)} "
                f"[dim]{added.member.user_id}[/dim]"
            )
            return
        u = svc.create_user(
            email,
            password,
            display_name=name,
            platform_role=PlatformRole.ADMIN if admin else PlatformRole.NONE,
        )
        kind = "administrateur plateforme" if admin else "utilisateur (sans organisation)"
        console.print(f"[green]✓[/green] {kind} {u.email} créé [dim]{u.user_id}[/dim]")


@user_app.command("add-member")
def user_add_member(
    email: Annotated[str, typer.Option("--email", "-e")],
    org: Annotated[str, typer.Option("--org", help="Slug ou id de l'organisation")],
    role: Annotated[MembershipRole, typer.Option("--role")] = MembershipRole.MEMBER,
) -> None:
    """Ajoute un utilisateur EXISTANT à une organisation (son mot de passe n'est pas modifié)."""
    with organization_service() as svc:
        o = svc.resolve(org)
        added = svc.add_member(o.id, email, role=role)  # inconnu → erreur (aucun mot de passe fourni)
        console.print(f"[green]✓[/green] {added.member.email} ajouté à {escape(o.name)} ({role.value})")


@user_app.command("list")
def user_list(
    org: Annotated[str | None, typer.Option("--org", help="Slug ou id : seulement ses membres")] = None,
) -> None:
    """Utilisateurs, rôle plateforme et organisations (aucun hash affiché)."""
    with organization_service() as svc:
        org_id = svc.resolve(org).id if org else None
        t = Table("Email", "Nom", "Rôle plateforme", "Statut", "Organisations", "Id")
        for u in svc.list_users(org_id):
            t.add_row(
                u.email,
                u.display_name,
                u.platform_role.value,
                u.status,
                ", ".join(u.organizations) or "—",
                str(u.user_id),
            )
        console.print(t)
