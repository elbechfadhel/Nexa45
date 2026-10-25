"""`uie partner ...` et `uie export ...` : mêmes services que l'API (domaine PARTNER, export vers un format).

    uie partner analyze | questions | confirm | ignore | list --instance <id|nom>
    uie export targets
    uie export profile show | set --target datev/debitoren-kreditoren --org <slug> [--set nom=valeur]*
    uie export preview  --target ... --instance <id|nom>
    uie export generate --target ... --instance <id|nom> -o fichier.csv
    uie export reset-demo --org <slug> [--client <dossier>] [--with-settings] [--yes]

Lecture seule vers le système source ; aucun envoi vers le système cible : le fichier est écrit localement.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table
from sqlalchemy import select
from sqlalchemy.orm import Session
from uie_core.catalog import CatalogReaderResolver
from uie_core.db import session_scope
from uie_core.domains import (
    DomainPackError,
    DomainPackResolver,
    PartnerDomainService,
    SemanticEnrichmentService,
)
from uie_core.exports import ExportProfileService, PartnerExportService, ProfileInvalid
from uie_core.exports.demo import reset_file_exports
from uie_core.identity import Organization, SystemContext
from uie_core.instances import InstanceService, ProviderResolver, default_secret_store
from uie_core.partners import PartnerReadService
from uie_core.registry import NotFound, RegistryError
from uie_core.registry import models as m

partner_app = typer.Typer(help="Domaine PARTNER : analyse et lecture des partenaires", no_args_is_help=True)
export_app = typer.Typer(help="Export des partenaires vers un format de fichier", no_args_is_help=True)
console = Console()
err = Console(stderr=True, style="bold red")
CTX = SystemContext("cli")

InstanceOpt = Annotated[str, typer.Option("--instance", "-i", help="Id ou nom de l'instance")]
TargetOpt = Annotated[str, typer.Option("--target", "-t", help="Format cible <système>/<produit>")]


def _fail(msg: str, code: int = 1) -> None:
    err.print(escape(msg))
    raise typer.Exit(code)


@dataclass
class _Services:
    s: Session
    instances: InstanceService
    enrichment: SemanticEnrichmentService
    partners: PartnerReadService
    exports: PartnerExportService
    profiles: ExportProfileService


@contextmanager
def services() -> Iterator[_Services]:
    import uie_connectors  # noqa: F401 — loaders, lecteurs, écrivains
    import uie_domain_packs  # noqa: F401 — packs et bindings cibles

    try:
        with session_scope() as s:
            packs = DomainPackResolver()
            instances = InstanceService(s, CTX, default_secret_store(), ProviderResolver())
            enrichment = SemanticEnrichmentService(s, CTX, packs, domains=PartnerDomainService(s, CTX, packs))
            partners = PartnerReadService(s, CTX, instances, enrichment, CatalogReaderResolver())
            yield _Services(
                s,
                instances,
                enrichment,
                partners,
                PartnerExportService(s, CTX, partners),
                ExportProfileService(s, CTX),
            )
    except ProfileInvalid as exc:
        for name, msg in exc.errors.items():
            err.print(f"{escape(name)} : {escape(msg)}")
        raise typer.Exit(1) from None
    except (RegistryError, DomainPackError) as exc:
        _fail(str(exc))


def _instance(svc: _Services, ref: str) -> m.Instance:
    try:
        return svc.instances.get(uuid.UUID(ref))
    except ValueError:
        inst = svc.s.scalar(select(m.Instance).where(m.Instance.name == ref))
        if inst is None:
            raise NotFound(f"Instance '{ref}' introuvable") from None
        return inst


def _target(ref: str) -> tuple[str, str]:
    system, sep, product = ref.partition("/")
    if not sep:
        _fail("--target attendu sous la forme <système>/<produit>, ex. datev/debitoren-kreditoren")
    return system, product


# --------------------------------------------------------------------------- #
# uie partner
# --------------------------------------------------------------------------- #
@partner_app.command("analyze")
def partner_analyze(instance: InstanceOpt) -> None:
    """Domaine PARTNER effectif du dernier snapshot d'instance + Semantic Enrichment (règles, sans LLM)."""
    with services() as svc:
        inst = _instance(svc, instance)
        svc.enrichment.domains.analyze(inst.id)
        s = svc.enrichment.summary(inst.id)
        console.print(
            f"{escape(inst.name)} : {s.total} champs, {s.identified} identifiés, {s.excluded_auto} exclus, "
            f"{s.pending} question(s) — "
            + ("[green]complete[/green]" if s.complete else "[yellow]à compléter[/yellow]")
        )


@partner_app.command("questions")
def partner_questions(instance: InstanceOpt) -> None:
    with services() as svc:
        inst = _instance(svc, instance)
        rows = svc.enrichment.get_pending_questions(inst.id)
        t = Table(title=f"Questions PARTNER — {escape(inst.name)} ({len(rows)})")
        for col in ("Entité", "Champ", "Libellé", "Proposition", "Raison"):
            t.add_column(col)
        for q in rows:
            t.add_row(
                q.entity_technical_name,
                q.field_technical_name,
                q.field.label or "",
                q.semantic_key or "",
                q.reason or "",
            )
        console.print(t)


@partner_app.command("confirm")
def partner_confirm(
    instance: InstanceOpt,
    entity: Annotated[str, typer.Option("--entity")],
    field: Annotated[str, typer.Option("--field")],
    key: Annotated[
        str | None, typer.Option("--key", help="Clé du vocabulaire PARTNER (défaut : la proposition)")
    ] = None,
) -> None:
    with services() as svc:
        v = svc.enrichment.confirm(_instance(svc, instance).id, entity, field, key)
        console.print(f"[green]✓[/green] {escape(v.path)} → {escape(v.semantic_key or '')}")


@partner_app.command("ignore")
def partner_ignore(
    instance: InstanceOpt,
    entity: Annotated[str, typer.Option("--entity")],
    field: Annotated[str, typer.Option("--field")],
) -> None:
    with services() as svc:
        v = svc.enrichment.ignore(_instance(svc, instance).id, entity, field)
        console.print(f"[green]✓[/green] {escape(v.path)} ignoré")


@partner_app.command("list")
def partner_list(instance: InstanceOpt) -> None:
    """Partenaires lus À LA DEMANDE dans le système source, en Partner CDM (rien n'est stocké)."""
    with services() as svc:
        result = svc.partners.read(_instance(svc, instance).id)
        t = Table(title=f"Partenaires ({len(result.partners)}, {result.skipped_children} contacts ignorés)")
        for col in ("Nom", "Type", "Rôles", "Pays", "TVA", "Banques"):
            t.add_column(col)
        for p in result.partners:
            a = p.address()
            t.add_row(
                escape(p.name),
                p.kind.value,
                ", ".join(r.value for r in p.roles) or "—",
                (a.country_code if a else None) or "—",
                p.tax.vat_id or "",
                str(len(p.bank_accounts)),
            )
        console.print(t)
        for i in result.issues:
            console.print(f"[yellow]![/yellow] {escape(i.name or i.source_ref)} : {escape(i.message)}")


# --------------------------------------------------------------------------- #
# uie export
# --------------------------------------------------------------------------- #
@export_app.command("targets")
def export_targets() -> None:
    with services() as svc:
        for b in svc.profiles.targets():
            console.print(
                f"• {b.target.system}/{b.target.product}  {escape(b.name)} ({', '.join(b.target.versions)})"
            )


def _org_id(svc: _Services, slug: str) -> uuid.UUID:
    org = svc.s.scalar(select(Organization).where(Organization.slug == slug))
    if org is None:
        _fail(f"Organisation '{slug}' introuvable")
    return org.id


@export_app.command("reset-demo")
def export_reset_demo(
    org: Annotated[str, typer.Option("--org", help="Slug de l'organisation (compte de démo)")],
    client: Annotated[
        str | None, typer.Option("--client", help="Dossier (mandant) ; sans option : tous les dossiers")
    ] = None,
    with_settings: Annotated[
        bool,
        typer.Option("--with-settings", help="Retirer aussi les réglages (Beraternummer, Mandantennummer…)"),
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Sans confirmation")] = False,
) -> None:
    """Remet la démo à zéro : comptes réservés et pièces journalisées d'une source FICHIER (une source
    connectée n'est jamais touchée). Pour répéter la démo liste de clients → factures vers DATEV."""
    with services() as svc:
        org_id = _org_id(svc, org)
        what = f"dossier « {client} »" if client else "tous les dossiers"
        if not yes and not typer.confirm(
            f"Retirer les comptes réservés et les pièces journalisées (source fichier) de {org}, {what} ?"
        ):
            raise typer.Exit(1)
        done = reset_file_exports(svc.s, CTX, org_id, client, with_settings=with_settings)
        console.print(
            f"✓ {done.accounts} compte(s) réservé(s), {done.documents} pièce(s) journalisée(s), "
            f"{done.runs} fichier(s) du journal retirés"
            + (f", {done.profiles} réglage(s) retiré(s)" if with_settings else " ; réglages gardés")
        )


@export_app.command("profile")
def export_profile(
    action: Annotated[str, typer.Argument(help="show | set")],
    target: TargetOpt,
    org: Annotated[str, typer.Option("--org", help="Slug de l'organisation")],
    set_: Annotated[
        list[str] | None, typer.Option("--set", help="nom=valeur ; plage : nom=début-fin")
    ] = None,
) -> None:
    system, product = _target(target)
    with services() as svc:
        org_id = _org_id(svc, org)
        t = svc.profiles.target(system, product)
        row = svc.profiles.get(system, product, org_id)
        if action == "set":
            values: dict[str, Any] = dict(row.values) if row else {}
            types = {p.name: p.type.value for p in t.binding.profile}
            for item in set_ or []:
                name, _, raw = item.partition("=")
                if types.get(name) == "range":
                    start, _, end = raw.partition("-")
                    values[name] = {"start": start, "end": end}
                elif types.get(name) == "boolean":
                    values[name] = raw.lower() in ("1", "true", "yes", "oui")
                else:
                    values[name] = raw
            row = svc.profiles.save(system, product, values, org_id)
        elif action != "show":
            _fail("action attendue : show | set")
        values = row.values if row else {}
        tab = Table(title=f"Profil d'export {escape(t.label)} — {org}")
        for col in ("Paramètre", "Libellé", "Valeur", "Requis"):
            tab.add_column(col)
        for p in t.binding.profile:
            tab.add_row(
                p.name, escape(p.label), escape(json.dumps(values.get(p.name))), "●" if p.required else ""
            )
        console.print(tab)


@export_app.command("preview")
def export_preview(target: TargetOpt, instance: InstanceOpt) -> None:
    """Aperçu de l'export : lecture seule, aucun numéro réservé."""
    system, product = _target(target)
    with services() as svc:
        p = svc.exports.preview(system, product, _instance(svc, instance).id)
        t = Table(title=f"{escape(p.target)} — {len(p.records)} enregistrement(s), encodage {p.encoding}")
        for col in ("Partenaire", "Rôle", "Compte", "Statut", "Problèmes"):
            t.add_column(col)
        for r in p.records:
            account = f"{r.account_number or '—'}{' (new)' if r.account_new else ''}"
            issues = "; ".join(i.message for i in r.issues)
            t.add_row(escape(r.name), r.role.lower(), account, r.status.value, escape(issues))
        console.print(t)
        for n in p.not_exported:
            console.print(f"[dim]non exporté : {escape(n.name or '')} — {escape(n.reason)}[/dim]")


@export_app.command("generate")
def export_generate(
    target: TargetOpt,
    instance: InstanceOpt,
    output: Annotated[Path, typer.Option("--output", "-o", help="Fichier à écrire (rien n'est envoyé)")],
) -> None:
    """Génère le fichier (numéros de compte réservés, journal d'audit) et l'écrit localement."""
    system, product = _target(target)
    with services() as svc:
        f = svc.exports.generate(system, product, _instance(svc, instance).id)
        output.write_bytes(f.content)
        r = f.run
        console.print(
            f"[green]✓[/green] {output} : {r.records} enregistrement(s), {r.warnings} avec avertissement, "
            f"{r.blocked} bloqué(s), {r.new_accounts} nouveau(x) compte(s) — sha256 {r.file_sha256[:12]}"
        )
