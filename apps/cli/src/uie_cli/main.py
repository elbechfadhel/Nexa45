"""`uie` — Admin CLI, premier entrypoint du Core.

uie registry systems
uie registry dump odoo --url http://localhost:8017 --db ref17 -u admin -p admin -o odoo17.json
uie registry import odoo --json odoo17.json
uie registry import odoo --url http://localhost:8017 --db ref17 -u admin -p admin
uie registry show odoo 17
uie registry show odoo 17 res.partner
uie registry fields odoo 17 --search price
uie registry compare odoo 17 odoo 18
uie db upgrade
uie api serve [--port 8000] [--reload]
uie api openapi -o apps/web/openapi.json
uie secret gen-key
uie instance add -n customer17 -s odoo -v 17 --url http://localhost:8027 --db customer17 -u admin
uie instance list | show <nom> | test <nom> | discover <nom> | diff <nom> [--details] | delete <nom>
uie product-domain show-standard --system odoo --version 17
uie product-domain analyze | show | fields | extensions | missing | history --instance <id|nom>
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table
from rich.tree import Tree
from uie_core.db import session_scope
from uie_core.identity import SystemContext
from uie_core.registry import (
    ChangeType,
    LoaderRegistry,
    NotFound,
    RegistryError,
    RegistryService,
    SchemaLoaderError,
    SnapshotRef,
)

# Sortie UTF-8 forcée : sous Windows, une sortie redirigée est en cp1252 et Rich
# échoue sur les caractères non ASCII (→, —, accents).
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

from uie_cli import demo, identity, instances, partners, product_domain  # noqa: E402

app = typer.Typer(help="Universal Integration / Interface Engine — Admin CLI", no_args_is_help=True)
registry = typer.Typer(help="System Knowledge Registry", no_args_is_help=True)
db = typer.Typer(help="Base de données du Registry (migrations)", no_args_is_help=True)
api = typer.Typer(help="API Admin FastAPI", no_args_is_help=True)
app.add_typer(registry, name="registry")
app.add_typer(db, name="db")
app.add_typer(api, name="api")
app.add_typer(instances.app, name="instance")
app.add_typer(instances.secret_app, name="secret")
app.add_typer(product_domain.app, name="product-domain")
app.add_typer(partners.partner_app, name="partner")
app.add_typer(partners.export_app, name="export")
app.add_typer(demo.app, name="demo")
app.add_typer(identity.app, name="admin")
app.add_typer(identity.org_app, name="org")
app.add_typer(identity.user_app, name="user")
console = Console()
err = Console(stderr=True, style="bold red")


def _loaders() -> None:
    import uie_connectors  # noqa: F401  — enregistre les loaders disponibles


def _fail(msg: str, code: int = 1) -> None:
    err.print(msg)
    raise typer.Exit(code)


# --------------------------------------------------------------------------- #
# db
# --------------------------------------------------------------------------- #
@db.command("upgrade")
def db_upgrade(revision: str = "head") -> None:
    """Applique les migrations Alembic (UIE_DATABASE_URL)."""
    core_dir = Path(__import__("uie_core").__file__).resolve().parents[2]
    r = subprocess.run([sys.executable, "-m", "alembic", "upgrade", revision], cwd=core_dir, env=os.environ)
    raise typer.Exit(r.returncode)


# --------------------------------------------------------------------------- #
# api
# --------------------------------------------------------------------------- #
@api.command("serve")
def api_serve(
    host: Annotated[str, typer.Option(help="Interface d'écoute")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port HTTP")] = 8000,
    reload: Annotated[bool, typer.Option("--reload", help="Rechargement auto (dev)")] = False,
) -> None:
    """Lance l'API Admin (uvicorn) : /docs pour la documentation OpenAPI."""
    import uvicorn

    uvicorn.run("uie_api.app:app", host=host, port=port, reload=reload)


@api.command("openapi")
def api_openapi(
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Fichier (défaut : stdout)")] = None,
) -> None:
    """Exporte le schéma OpenAPI (source du client TypeScript de apps/web)."""
    from uie_api.app import openapi_json

    text = openapi_json()
    if output is None:
        typer.echo(text)
    else:
        output.write_text(text, encoding="utf-8")
        console.print(f"[green]✓[/green] OpenAPI → {output}")


# --------------------------------------------------------------------------- #
# registry
# --------------------------------------------------------------------------- #
@registry.command("loaders")
def registry_loaders() -> None:
    """Schema Loaders disponibles."""
    _loaders()
    for code in LoaderRegistry.available():
        console.print(f"• {code}")


@registry.command("known-systems")
def registry_known_systems() -> None:
    """Importe le catalogue des systèmes CONNUS (identifiés, sans schéma) dans le Registry (idempotent)."""
    from uie_connectors.known_systems import import_known_systems

    with session_scope() as s:
        n = import_known_systems(s, SystemContext("cli"))
    console.print(f"[green]{n} systèmes connus[/green] (statut known : aucun schéma, aucune version)")


@registry.command("systems")
def registry_systems() -> None:
    """Arbre System → Product → Version → Interface → snapshots."""
    with session_scope() as s:
        systems = RegistryService(s, SystemContext("cli")).list_systems()
        if not systems:
            console.print("[dim]Registry vide. Lancer : uie registry import odoo ...[/dim]")
            return
        tree = Tree("[bold]System Knowledge Registry[/bold]")
        for sys_ in systems:
            n_sys = tree.add(f"[cyan]{sys_.code}[/cyan]  {sys_.name}")
            for prod in sys_.products:
                n_prod = n_sys.add(prod.code if prod.name == prod.code else f"{prod.code}  {prod.name}")
                for ver in sorted(prod.versions, key=lambda v: v.version):
                    ed = f" ({ver.edition})" if ver.edition else ""
                    n_ver = n_prod.add(f"[bold]{ver.version}[/bold]{ed}")
                    for itf in ver.interfaces:
                        snaps = sorted(itf.snapshots, key=lambda x: x.created_at)
                        label = escape(f"{itf.code} [{itf.type}]")
                        if snaps:
                            last = snaps[-1]
                            label += (
                                f"  → {len(snaps)} snapshot(s), dernier @{last.snapshot_version} "
                                f"({last.entity_count} entités, {last.field_count} champs, "
                                f"{last.created_at:%Y-%m-%d})"
                            )
                        n_ver.add(label)
        console.print(tree)


def _connection_options(url, db_, user, password, json_file, spec=None) -> dict:
    cfg: dict = {}
    if spec:
        cfg["spec"] = spec
    elif json_file:
        cfg["json"] = json_file
    else:
        cfg.update({"url": url, "db": db_, "username": user, "password": password})
    return cfg


UrlOpt = Annotated[str | None, typer.Option("--url", help="URL Odoo, ex. http://localhost:8017")]
DbOpt = Annotated[str | None, typer.Option("--db", help="Base Odoo")]
UserOpt = Annotated[str | None, typer.Option("--user", "-u")]
PasswordOpt = Annotated[str | None, typer.Option("--password", "-p", envvar="UIE_SOURCE_PASSWORD")]
JsonOpt = Annotated[Path | None, typer.Option("--json", help="Dump JSON (hors-ligne)")]
SpecOpt = Annotated[
    Path | None,
    typer.Option("--spec", help="Fichier de spécification (loaders à spec, ex. datev) ; dossier avec --all"),
]
ProductOpt = Annotated[
    str | None, typer.Option("--product", "-P", help="Produit du système (ex. datev : buchungsstapel)")
]


@registry.command("dump")
def registry_dump(
    system: str,
    url: UrlOpt = None,
    db_: DbOpt = None,
    user: UserOpt = None,
    password: PasswordOpt = None,
    output: Annotated[Path, typer.Option("--output", "-o")] = Path("dump.json"),
    indent: Annotated[
        int | None, typer.Option("--indent", help="Indentation JSON (défaut : compact, une ligne)")
    ] = None,
) -> None:
    """Exporte les métadonnées brutes d'une instance en JSON (reproductible, hors-ligne)."""
    _loaders()
    if system != "odoo":
        _fail("dump : seul 'odoo' est supporté pour l'instant")
    from uie_connectors.odoo import XmlRpcMetadataSource

    src = XmlRpcMetadataSource(url=url or "", db=db_ or "", username=user or "", password=password or "")
    try:
        data = src.dump()
    except Exception as exc:  # noqa: BLE001
        _fail(f"Connexion/introspection échouée : {exc}")
    separators = None if indent is not None else (",", ":")
    output.write_text(
        json.dumps(data, indent=indent, separators=separators, ensure_ascii=False), encoding="utf-8"
    )
    console.print(f"[green]✓[/green] {len(data['models'])} models, {len(data['fields'])} champs → {output}")


@registry.command("import")
def registry_import(
    system: str,
    url: UrlOpt = None,
    db_: DbOpt = None,
    user: UserOpt = None,
    password: PasswordOpt = None,
    json_file: JsonOpt = None,
    spec: SpecOpt = None,
    all_: Annotated[
        bool,
        typer.Option(
            "--all", help="Toutes les specs *.yaml du dossier --spec (défaut : var/<système>/specs)"
        ),
    ] = False,
    snapshot_version: Annotated[str | None, typer.Option("--snapshot-version")] = None,
    include_transient: Annotated[bool, typer.Option("--include-transient")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Extraire sans publier")] = False,
) -> None:
    """Extrait le schéma d'un système (loader) et le publie en snapshot immutable."""
    _loaders()
    if all_:
        folder = spec or Path("var") / system / "specs"
        specs = sorted(folder.rglob("*.yaml")) if folder.is_dir() else []
        if not specs:
            _fail(f"Aucune spec *.yaml dans {folder}")
    else:
        specs = [spec]
    try:
        loader_cls = LoaderRegistry.get(system)
    except SchemaLoaderError as exc:
        _fail(str(exc))
    for one in specs:
        try:
            cfg = _connection_options(url, db_, user, password, json_file, one)
            cfg["include_transient"] = include_transient
            loader = loader_cls(cfg)
            with console.status(f"Extraction du schéma {system}…"):
                snap = loader.build_snapshot()
        except (SchemaLoaderError, PermissionError, OSError) as exc:
            _fail(str(exc))
        _publish(snap, snapshot_version, dry_run)


def _publish(snap, snapshot_version: str | None, dry_run: bool) -> None:  # noqa: ANN001
    product = "" if snap.product == snap.system else f" {snap.product}"
    edition = f" ({snap.edition})" if snap.edition else ""
    console.print(
        f"Schéma extrait : [bold]{snap.system}{product} {snap.version}[/bold]{edition} "
        f"interface {snap.interface} — {len(snap.entities)} entités, {snap.field_count} champs, "
        f"{len(snap.modules)} modules"
    )
    if dry_run:
        console.print(f"[dim]--dry-run : non publié. checksum {snap.checksum()[:12]}[/dim]")
        return
    with session_scope() as s:
        svc = RegistryService(s, SystemContext("cli"))
        try:
            row = svc.publish_snapshot(snap, snapshot_version)
        except RegistryError as exc:
            _fail(str(exc))
        console.print(
            f"[green]✓[/green] snapshot [bold]@{row.snapshot_version}[/bold] "
            f"(checksum {row.checksum[:12]}) — {row.entity_count} entités, {row.field_count} champs"
        )


def _ref(
    system: str, version: str, snapshot_version: str | None = None, product: str | None = None
) -> SnapshotRef:
    return SnapshotRef(system=system, version=version, product=product, snapshot_version=snapshot_version)


@registry.command("show")
def registry_show(
    system: str,
    version: str,
    entity: Annotated[str | None, typer.Argument()] = None,
    search: Annotated[str | None, typer.Option("--search", "-s", help="Filtre sur le nom d'entité")] = None,
    snapshot_version: Annotated[str | None, typer.Option("--snapshot-version")] = None,
    product: ProductOpt = None,
) -> None:
    """Liste les entités d'une version, ou le détail (champs + relations) d'une entité."""
    with session_scope() as s:
        svc = RegistryService(s, SystemContext("cli"))
        try:
            snap = svc.resolve_snapshot(_ref(system, version, snapshot_version, product))
        except NotFound as exc:
            _fail(str(exc))
        head = svc.snapshot_label(snap)
        if entity is None:
            ents = svc.list_entities(snap.id, search)
            t = Table(title=f"{head} — {len(ents)} entités", show_lines=False)
            t.add_column("Entité", style="cyan")
            t.add_column("Libellé")
            t.add_column("Module")
            t.add_column("Kind")
            t.add_column("Table")
            t.add_column("Champs", justify="right")
            for e in ents:
                t.add_row(
                    e.technical_name,
                    e.display_name or "",
                    e.module.technical_name if e.module else "",
                    e.kind,
                    e.physical_name or "—",
                    str(len(e.fields)),
                )
            console.print(t)
            return
        try:
            e = svc.get_entity(snap.id, entity)
        except NotFound as exc:
            _fail(str(exc))
        console.print(
            f"[bold cyan]{e.technical_name}[/bold cyan]  {e.display_name or ''}  "
            f"[dim]module={e.module.technical_name if e.module else '—'} kind={e.kind} "
            f"table={e.physical_name or '—'}[/dim]"
        )
        t = Table(show_lines=False)
        for col in ("Champ", "Libellé", "Type", "Natif", "Req", "RO", "Stocké", "Calc", "Colonne", "Cible"):
            t.add_column(col, justify="center" if col in ("Req", "RO", "Stocké", "Calc") else "left")
        rels = {r.source_field_id: r for r in svc.list_relations(e.id)}
        for f in e.fields:
            r = rels.get(f.id)
            target = f"{r.target_entity_name} ({r.relation_type})" if r else ""
            if f.native_type == "selection" and f.metadata_.get("selection"):
                target = ", ".join(x["value"] for x in f.metadata_["selection"][:6])
            style = "dim" if f.metadata_.get("technical") else ("yellow" if f.metadata_.get("custom") else "")
            t.add_row(
                f"[{style}]{f.technical_name}[/{style}]" if style else f.technical_name,
                f.label or "",
                f.data_type,
                f.native_type or "",
                "●" if f.required else "",
                "●" if f.readonly else "",
                "●" if f.stored else "",
                "●" if f.computed else "",
                f.physical_column or "—",
                target,
            )
        console.print(t)


@registry.command("fields")
def registry_fields(
    system: str,
    version: str,
    search: Annotated[str, typer.Option("--search", "-s")],
    snapshot_version: Annotated[str | None, typer.Option("--snapshot-version")] = None,
    product: ProductOpt = None,
) -> None:
    """Recherche un champ par nom dans toute une version."""
    with session_scope() as s:
        svc = RegistryService(s, SystemContext("cli"))
        try:
            snap = svc.resolve_snapshot(_ref(system, version, snapshot_version, product))
        except NotFound as exc:
            _fail(str(exc))
        rows = svc.search_fields(snap.id, search)
        t = Table(title=f"{svc.snapshot_label(snap)} — champs contenant « {search} » ({len(rows)})")
        t.add_column("Entité", style="cyan")
        t.add_column("Champ")
        t.add_column("Libellé")
        t.add_column("Type")
        t.add_column("Natif")
        for ent, f in rows:
            t.add_row(ent, f.technical_name, f.label or "", f.data_type, f.native_type or "")
        console.print(t)


@registry.command("compare")
def registry_compare(
    system_a: str,
    version_a: str,
    system_b: str,
    version_b: str,
    entity: Annotated[str | None, typer.Option("--entity", "-e", help="Limiter à une entité")] = None,
    all_: Annotated[bool, typer.Option("--all", help="Inclure UNCHANGED")] = False,
    product: Annotated[
        str | None,
        typer.Option(
            "--product", "-P", help="Produit des deux côtés (ex. datev 12 datev 13 -P buchungsstapel)"
        ),
    ] = None,
) -> None:
    """Version Compare : ADDED / REMOVED / CHANGED entre deux versions (ex. odoo 17 odoo 18)."""
    with session_scope() as s:
        svc = RegistryService(s, SystemContext("cli"))
        try:
            res = svc.compare(
                _ref(system_a, version_a, product=product),
                _ref(system_b, version_b, product=product),
                include_unchanged=all_,
            )
        except NotFound as exc:
            _fail(str(exc))
    if entity:
        res = res.for_entity(entity)
    counts = res.counts()
    console.print(
        f"[bold]{res.source}[/bold] → [bold]{res.target}[/bold]"
        + (f" [dim](entité {entity})[/dim]" if entity else "")
        + "   "
        f"[green]+{counts['ADDED']}[/green]  [red]-{counts['REMOVED']}[/red]  "
        f"[yellow]~{counts['CHANGED']}[/yellow]" + (f"  ={counts['UNCHANGED']}" if all_ else "")
    )
    colors = {
        ChangeType.ADDED: "green",
        ChangeType.REMOVED: "red",
        ChangeType.CHANGED: "yellow",
        ChangeType.UNCHANGED: "dim",
    }
    sign = {
        ChangeType.ADDED: "+",
        ChangeType.REMOVED: "-",
        ChangeType.CHANGED: "~",
        ChangeType.UNCHANGED: "=",
    }
    for c in res.changes:
        # escape : les chemins de relation « entité[champ->cible] » seraient lus comme du balisage Rich
        path = escape(c.path)
        if c.object_type == "entity":
            det = f"{c.details.get('fields', c.details.get('changes', ''))}"
            col = colors[c.change_type]
            console.print(f"[{col}]{sign[c.change_type]} {path}[/{col}]  [dim]{escape(det)}[/dim]")
        else:
            det = ", ".join(
                f"{k}: {v['from']} → {v['to']}" if isinstance(v, dict) and "from" in v else f"{k}={v}"
                for k, v in c.details.items()
            )
            col = colors[c.change_type]
            console.print(f"    [{col}]{sign[c.change_type]} {path}[/{col}]  [dim]{escape(det)}[/dim]")


if __name__ == "__main__":
    app()
