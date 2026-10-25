"""`uie instance ...` et `uie secret ...` : mêmes services Core que l'API (InstanceService).

Le mot de passe n'est jamais un argument de ligne de commande (historique shell, liste des
processus) : invite masquée par défaut, `--password-stdin`, ou variable UIE_INSTANCE_PASSWORD.
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
from sqlalchemy import select
from uie_core.db import session_scope
from uie_core.identity import GO45_DEV_ORG_SLUG, Organization, SystemContext
from uie_core.instances import (
    ConnectionFieldRole,
    DiscoveryError,
    FernetSecretStore,
    InstanceCreate,
    InstanceDiffSummary,
    InstanceService,
    ProviderResolver,
    SecretError,
    SecretReplacementFailed,
    default_secret_store,
)
from uie_core.registry import RegistryError
from uie_core.registry import models as m

PASSWORD_ENV = "UIE_INSTANCE_PASSWORD"

app = typer.Typer(
    help="Instances client : connexion, capabilities, découverte, Instance Diff", no_args_is_help=True
)
secret_app = typer.Typer(help="Secrets (clé UIE_SECRET_KEY)", no_args_is_help=True)
console = Console()
err = Console(stderr=True, style="bold red")

STATUS_STYLE = {"VERIFIED": "green", "DECLARED": "yellow", "UNAVAILABLE": "red", "UNKNOWN": "dim"}
ORIGIN_STYLE = {"STANDARD": "cyan", "CUSTOM": "yellow", "THIRD_PARTY": "magenta", "UNKNOWN": "dim"}


def _fail(msg: str, code: int = 1) -> None:
    err.print(escape(msg))
    raise typer.Exit(code)


@contextmanager
def instance_service() -> Iterator[InstanceService]:
    """Même construction que l'API (uie_api.deps.get_instance_service) : session + secrets + providers."""
    import uie_connectors  # noqa: F401 — enregistre les discovery providers

    try:
        with session_scope() as s:
            yield InstanceService(s, SystemContext("cli"), default_secret_store(), ProviderResolver())
    except (RegistryError, DiscoveryError, SecretError) as exc:
        _fail(str(exc))


def _read_password(from_stdin: bool) -> SecretStr:
    if env := os.environ.get(PASSWORD_ENV):
        return SecretStr(env)
    if from_stdin:
        return SecretStr(sys.stdin.readline().rstrip("\r\n"))
    return SecretStr(typer.prompt("Mot de passe / clé API", hide_input=True))


# --------------------------------------------------------------------------- #
# secret
# --------------------------------------------------------------------------- #
@secret_app.command("gen-key")
def secret_gen_key() -> None:
    """Génère une clé Fernet pour UIE_SECRET_KEY (à mettre dans .env, jamais dans git)."""
    typer.echo(FernetSecretStore.generate_key())


# --------------------------------------------------------------------------- #
# instance
# --------------------------------------------------------------------------- #
@app.command("add")
def instance_add(
    name: Annotated[str, typer.Option("--name", "-n")],
    system: Annotated[str, typer.Option("--system", "-s")],
    version: Annotated[str, typer.Option("--version", "-v")],
    url: Annotated[str, typer.Option("--url", help="Endpoint, ex. http://localhost:8027")],
    db_: Annotated[str | None, typer.Option("--db")] = None,
    user: Annotated[str | None, typer.Option("--user", "-u")] = None,
    environment: Annotated[str, typer.Option("--environment", "-e")] = "test",
    org: Annotated[
        str, typer.Option("--org", help="Slug de l'organisation propriétaire")
    ] = GO45_DEV_ORG_SLUG,
    product: Annotated[str | None, typer.Option("--product")] = None,
    interface: Annotated[str | None, typer.Option("--interface")] = None,
    password_stdin: Annotated[
        bool, typer.Option("--password-stdin", help="Lire le secret sur stdin")
    ] = False,
) -> None:
    """Crée une instance (le secret est chiffré, seule une référence est stockée)."""
    config = {k: v for k, v in {"db": db_, "username": user}.items() if v}
    with session_scope() as s:
        org_id = s.scalar(select(Organization.id).where(Organization.slug == org))
    if org_id is None:
        _fail(f"Organisation '{org}' introuvable")
    data = InstanceCreate(
        organization_id=org_id,
        name=name,
        system=system,
        version=version,
        product=product,
        interface=interface,
        environment=environment,
        endpoint=url,
        config=config,
        password=_read_password(password_stdin),
    )
    with instance_service() as svc:
        inst = svc.create(data)
        console.print(f"[green]✓[/green] instance [bold]{inst.name}[/bold] créée : {inst.id}")
        _print_capabilities(svc.get(inst.id))


@app.command("list")
def instance_list() -> None:
    """Liste les instances, leur état de connexion et leur dernière découverte."""
    with instance_service() as svc:
        rows = svc.list()
        if not rows:
            console.print("[dim]Aucune instance. Créer : uie instance add ...[/dim]")
            return
        t = Table(
            "Nom",
            "Organisation",
            "Système",
            "Interface",
            "Env.",
            "Connexion",
            "Dernier discovery",
            "VERIFIED",
            "Id",
        )
        for i in rows:
            ver = i.interface.version
            conn = i.connections[0] if i.connections else None
            last = max(i.snapshots, key=lambda s: s.sequence, default=None)
            verified = [c.capability for c in i.capabilities if c.status == "VERIFIED"]
            t.add_row(
                i.name,
                i.organization.slug,
                f"{ver.product.system.code} {ver.version}",
                i.interface.code,
                i.environment,
                _status(conn),
                f"#{last.sequence} {last.discovered_at:%Y-%m-%d %H:%M}" if last else "—",
                ", ".join(verified) or "—",
                str(i.id),
            )
        console.print(t)


@app.command("show")
def instance_show(ref: Annotated[str, typer.Argument(help="Id ou nom de l'instance")]) -> None:
    """Détail : connexion (sans secret), capabilities, historique des snapshots."""
    with instance_service() as svc:
        i = svc.resolve(ref)
        ver = i.interface.version
        conn = i.connections[0] if i.connections else None
        console.print(
            f"[bold cyan]{i.name}[/bold cyan]  {ver.product.system.code}/{ver.product.code} {ver.version} "
            f"interface {i.interface.code}  env={i.environment}  statut={i.status}  [dim]{i.id}[/dim]"
        )
        console.print(f"organisation {escape(i.organization.name)} ({i.organization.slug})")
        if conn:
            secret = "secret chiffré (secret_ref)" if conn.secret_ref else "aucun secret"
            console.print(f"connexion {conn.connector_type} {conn.endpoint} {conn.config} · {secret}")
            console.print(f"dernier test : {_status(conn)}")
            if conn.last_test_error:
                console.print(f"  [red]{escape(conn.last_test_error)}[/red]")
        _print_capabilities(i)
        _print_snapshots(i)


@app.command("test")
def instance_test(ref: Annotated[str, typer.Argument(help="Id ou nom de l'instance")]) -> None:
    """Teste réellement la connexion et vérifie les capabilities (lecture seule)."""
    with instance_service() as svc:
        i = svc.resolve(ref)
        res = svc.test_connection(i.id)
        if res.ok:
            console.print(f"[green]✓ connexion OK[/green] {res.server_info}")
        else:
            console.print(f"[red]✗ connexion KO[/red] {escape(res.error or '')}")
        _print_capabilities(svc.get(i.id))


@app.command("replace-key")
def instance_replace_key(
    ref: Annotated[str, typer.Argument(help="Id ou nom de l'instance")],
    key_stdin: Annotated[bool, typer.Option("--key-stdin", help="Lire la nouvelle valeur sur stdin")] = False,
    activate: Annotated[
        bool, typer.Option("--activate", help="Activer la nouvelle valeur (sinon : vérification seulement)")
    ] = False,
) -> None:
    """Remplace le secret (clé d'API, mot de passe) d'une instance EXISTANTE : même instance, mêmes liens et
    historique. Test réel (lecture) de la nouvelle valeur et contrôle du compte distant AVANT toute écriture ;
    échec → l'ancienne valeur reste active. Sans --activate : rien n'est enregistré. La valeur n'est jamais un
    argument : saisie masquée ou --key-stdin."""
    value = (
        sys.stdin.readline().strip()
        if key_stdin
        else typer.prompt("Nouvelle clé / mot de passe (saisie masquée)", hide_input=True)
    )
    with instance_service() as svc:
        i = svc.resolve(ref)
        field = next(f.name for f in svc.connection_fields(i.id) if f.role == ConnectionFieldRole.SECRET)
        try:
            res = svc.replace_secret(i.id, {field: SecretStr(value)}, activate=activate)
        except SecretReplacementFailed as exc:
            _fail(f"✗ {exc} — l'ancienne valeur reste active, rien n'a été enregistré")
        facts = ", ".join(f"{k} : {v}" for k, v in svc.connection_facts(i.id)) if activate else ""
        console.print(
            f"[green]✓ nouvelle valeur acceptée[/green] (compte distant vérifié : {res.account_checked})"
        )
        if res.activated:
            console.print(
                f"[green]✓ activée[/green] pour [bold]{escape(i.name)}[/bold] ({i.id}) {escape(facts)}"
            )
        else:
            console.print(
                "[yellow]vérification seulement : rien n'a été enregistré[/yellow] (relancer avec --activate)"
            )


@app.command("discover")
def instance_discover(ref: Annotated[str, typer.Argument(help="Id ou nom de l'instance")]) -> None:
    """Découvre le schéma réel → NOUVEAU snapshot d'instance immutable + diff vs standard."""
    with instance_service() as svc:
        i = svc.resolve(ref)
        with console.status(f"Découverte du schéma de {i.name}…"):
            snap = svc.discover_schema(i.id)
        console.print(
            f"[green]✓[/green] snapshot d'instance [bold]#{snap.sequence}[/bold] "
            f"(checksum {snap.checksum[:12]}) — "
            f"{snap.entity_count} entités, {snap.field_count} champs, {snap.relation_count} relations · "
            f"baseline {snap.metadata_.get('base_snapshot_label')} [dim]{snap.base_snapshot_id}[/dim]"
        )
        _print_summary(InstanceDiffSummary.model_validate(snap.diff_summary))


@app.command("diff")
def instance_diff(
    ref: Annotated[str, typer.Argument(help="Id ou nom de l'instance")],
    sequence: Annotated[
        int | None, typer.Option("--snapshot", help="N° de snapshot (dernier par défaut)")
    ] = None,
    details: Annotated[bool, typer.Option("--details", help="Lister tous les changements")] = False,
) -> None:
    """Instance Diff : standard (baseline exacte du snapshot) → instance."""
    with instance_service() as svc:
        i = svc.resolve(ref)
        r = svc.get_schema_diff(i.id, sequence)
        console.print(
            f"[bold]{r.instance_snapshot.metadata_.get('base_snapshot_label')}[/bold] → "
            f"[bold]{i.name} #{r.instance_snapshot.sequence}[/bold] "
            f"({r.instance_snapshot.discovered_at:%Y-%m-%d %H:%M}, "
            f"checksum {r.instance_snapshot.checksum[:12]})"
        )
        _print_summary(r.summary)
        if details:
            sign = {"ADDED": "+", "REMOVED": "-", "CHANGED": "~"}
            for c in r.changes:
                style = ORIGIN_STYLE.get(c.origin, "")
                console.print(
                    f"  {sign.get(c.change_type, '?')} [{style}]{c.origin:<11}[/{style}] {escape(c.path)}"
                    f"  [dim]{escape(c.reason or '')}[/dim]"
                )


@app.command("delete")
def instance_delete(
    ref: Annotated[str, typer.Argument(help="Id ou nom de l'instance")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Ne pas demander de confirmation")] = False,
) -> None:
    """Supprime l'instance, sa connexion, ses capabilities, ses snapshots, ses diffs et son secret.

    Le snapshot standard servant de baseline n'est jamais supprimé ni modifié.
    """
    with instance_service() as svc:
        i = svc.resolve(ref)
        if not yes:
            typer.confirm(
                f"Supprimer l'instance {i.name} et ses {len(i.snapshots)} snapshot(s) d'instance ?",
                abort=True,
            )
        d = svc.delete(i.id)
    # hors du bloc : la transaction est commitée, le secret retiré du SecretStore
    console.print(
        f"[green]✓[/green] instance [bold]{d.name}[/bold] supprimée — {d.connections} connexion(s), "
        f"{d.capabilities} capabilities, {d.snapshots} snapshot(s), {d.diffs} diff(s), "
        f"{d.secrets} secret(s) ; "
        f"snapshot(s) standard conservé(s) : {', '.join(str(x) for x in d.base_snapshot_ids) or '—'}"
    )


# --------------------------------------------------------------------------- #
# affichage
# --------------------------------------------------------------------------- #
def _status(conn: m.InstanceConnection | None) -> str:
    if conn is None or conn.last_test_at is None:
        return "jamais testée"
    style = "green" if conn.last_test_status == "OK" else "red"
    return f"[{style}]{conn.last_test_status}[/{style}] {conn.last_test_at:%Y-%m-%d %H:%M}"


def _print_capabilities(i: m.Instance) -> None:
    t = Table("Capability", "Statut", "Détail", title="Capabilities")
    for c in i.capabilities:
        style = STATUS_STYLE.get(c.status, "")
        t.add_row(c.capability, f"[{style}]{c.status}[/{style}]", escape(c.detail or ""))
    console.print(t)


def _print_snapshots(i: m.Instance) -> None:
    if not i.snapshots:
        console.print("[dim]Aucun snapshot d'instance : uie instance discover ...[/dim]")
        return
    t = Table(
        "#",
        "Découvert le",
        "Entités",
        "Champs",
        "Relations",
        "Checksum",
        "Baseline",
        title="Snapshots (immutables)",
    )
    for s in sorted(i.snapshots, key=lambda s: s.sequence):
        t.add_row(
            str(s.sequence),
            f"{s.discovered_at:%Y-%m-%d %H:%M:%S}",
            str(s.entity_count),
            str(s.field_count),
            str(s.relation_count),
            s.checksum[:12],
            s.metadata_.get("base_snapshot_label", str(s.base_snapshot_id)),
        )
    console.print(t)


def _print_summary(s: InstanceDiffSummary) -> None:
    t = Table("", "ADDED", "REMOVED", "CHANGED", title="Instance Diff")
    for label, c in (("entités", s.entities), ("champs", s.fields), ("relations", s.relations)):
        t.add_row(
            label, f"[green]+{c.added}[/green]", f"[red]-{c.removed}[/red]", f"[yellow]~{c.changed}[/yellow]"
        )
    console.print(t)
    console.print(
        "par origine : " + "  ".join(f"[{ORIGIN_STYLE[o]}]{o}[/] {n}" for o, n in s.by_origin.items())
    )

    def block(title: str, items: list, limit: int = 15) -> None:  # noqa: ANN001
        if not items:
            return
        console.print(f"[bold]{title}[/bold] ({len(items)})")
        for it in items[:limit]:
            extra = f" [dim]{escape(', '.join(it.modules))}[/dim]" if it.modules else ""
            console.print(f"  • {escape(it.path)}{extra}")
        if len(items) > limit:
            console.print(f"  [dim]… {len(items) - limit} de plus (uie instance diff --details)[/dim]")

    block("Entités custom", s.custom_entities)
    block("Champs custom", s.custom_fields)
    block("Entités tierces", s.third_party_entities)
    block("Champs tiers", s.third_party_fields)
    if s.third_party_modules:
        console.print(f"[bold]Modules hors baseline[/bold] : {', '.join(s.third_party_modules)}")
    if s.missing_standard_modules:
        console.print("[bold]Modules standard absents[/bold] :")
        for mm in s.missing_standard_modules:
            console.print(f"  • {mm.name} — {mm.evidence} ({mm.removed_elements} élément(s))")
    block("Éléments standard manquants (non expliqués)", s.missing_standard_elements)
    block("Risques de compatibilité (champs standard modifiés)", s.compatibility_risks)
