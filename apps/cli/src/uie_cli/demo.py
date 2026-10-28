"""`uie demo prepare` : prépare la démonstration « Prepare data for DATEV » d'une organisation.

    uie demo prepare --org <slug> --answers temp/demo_answers.yaml [--dir <dossier des fichiers>]
                     [--out <dossier>] [--user <e-mail>] [--yes]

1. remet à zéro l'état de démo du dossier (comme `uie export reset-demo`, réglages gardés) ;
2. exporte les fichiers du fichier de réponses, dans l'ordre, par le MÊME chemin que la page (Core
   `uie_core.wizard.demo`) : numéros de compte réservés, pièces journalisées, registre des factures ouvertes ;
3. une question sans réponse déclarée : arrêt, message clair, RIEN n'est enregistré (une seule transaction) ;
4. résumé : fichiers exportés, enregistrements et écritures, comptes réservés.

Agit comme un membre de l'organisation (le propriétaire, ou `--user`) : mêmes réglages, mêmes correspondances
mémorisées que la page. Aucun LLM, aucun envoi : les fichiers DATEV sont écrits dans `--out` s'il est donné.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Annotated

import typer
import yaml
from pydantic import ValidationError
from rich.console import Console
from rich.markup import escape
from sqlalchemy import select
from sqlalchemy.orm import Session
from uie_core.accounting import AccountingReadService, AccountingTargetService
from uie_core.catalog import CatalogReaderResolver
from uie_core.db import session_scope
from uie_core.domains import (
    AccountingDomainService,
    DomainPackResolver,
    PartnerDomainService,
    SemanticEnrichmentService,
)
from uie_core.exports import ExportProfileService, PartnerExportService
from uie_core.exports.demo import file_accounts, reset_file_exports
from uie_core.exports.open_items import OpenItemRegister
from uie_core.exports.service import DEFAULT_CLIENT
from uie_core.identity import (
    MembershipRole,
    Organization,
    OrganizationMembership,
    SystemContext,
    TenantContext,
    User,
)
from uie_core.imports import ImportFileError
from uie_core.instances import InstanceService, ProviderResolver, default_secret_store
from uie_core.partners import PartnerReadService
from uie_core.sources import RecordSourceResolver
from uie_core.wizard import MigrationWizardService, ReferenceFinder, WizardError, WizardInputError
from uie_core.wizard.demo import DemoPlan, DemoQuestionUnanswered, DemoTargetUnknown, prepare_demo

app = typer.Typer(help="Démonstrations : préparation de l'état d'un compte de démo", no_args_is_help=True)
console = Console()
err = Console(stderr=True, style="bold red")


def _fail(msg: str) -> None:
    err.print(escape(msg))
    raise typer.Exit(1)


def _tenant(s: Session, slug: str, email: str | None) -> TenantContext:
    org = s.scalar(select(Organization).where(Organization.slug == slug))
    if org is None:
        _fail(f"Organisation '{slug}' introuvable")
    q = (
        select(OrganizationMembership, User)
        .join(User, User.id == OrganizationMembership.user_id)
        .where(OrganizationMembership.organization_id == org.id)
        .order_by(OrganizationMembership.role != "OWNER", OrganizationMembership.created_at)
    )
    if email:
        q = q.where(User.email == email.strip().lower())
    hit = s.execute(q).first()
    if hit is None:
        _fail(f"Aucun membre {email or ''} dans l'organisation '{slug}'".replace("  ", " "))
    member, user = hit
    return TenantContext(user.id, org.id, MembershipRole(member.role))


def _wizard(s: Session, ctx: TenantContext) -> MigrationWizardService:
    """Même composition que l'API (`uie_api.deps.get_tenant_migration_wizard`), sans LLM."""
    import uie_connectors  # noqa: F401 — loaders, lecteurs, écrivains
    import uie_domain_packs  # noqa: F401 — packs, tableaux de source et bindings cibles

    packs, readers, sources = DomainPackResolver(), CatalogReaderResolver(), RecordSourceResolver()
    instances = InstanceService(s, ctx, default_secret_store(), ProviderResolver())
    partners = PartnerReadService(
        s,
        ctx,
        instances,
        SemanticEnrichmentService(s, ctx, packs, domains=PartnerDomainService(s, ctx, packs)),
        readers,
        sources=sources,
    )
    accounting = AccountingReadService(
        s,
        ctx,
        instances,
        SemanticEnrichmentService(s, ctx, packs, domains=AccountingDomainService(s, ctx, packs)),
        readers,
        sources=sources,
    )
    exports = SimpleNamespace(
        partners=PartnerExportService(s, ctx, partners),
        accounting=AccountingTargetService(s, ctx, accounting),
        profiles=ExportProfileService(s, ctx),
    )
    return MigrationWizardService(s, ctx, None, references=ReferenceFinder(instances), exports=exports)


@app.command("prepare")
def demo_prepare(
    org: Annotated[str, typer.Option("--org", help="Slug de l'organisation (compte de démo)")],
    answers: Annotated[Path, typer.Option("--answers", help="Fichier de réponses (YAML : answers, files)")],
    files_dir: Annotated[
        Path | None,
        typer.Option("--dir", help="Dossier des fichiers (défaut : celui du fichier de réponses)"),
    ] = None,
    out: Annotated[Path | None, typer.Option("--out", help="Dossier où écrire les fichiers préparés")] = None,
    user: Annotated[
        str | None, typer.Option("--user", help="E-mail du membre (défaut : propriétaire)")
    ] = None,
    yes: Annotated[bool, typer.Option("--yes", help="Sans confirmation")] = False,
) -> None:
    """Remet la démo à zéro puis exporte les fichiers déclarés, comme la page « Prepare data for DATEV »."""
    try:
        plan = DemoPlan.model_validate(yaml.safe_load(answers.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        _fail(f"Fichier de réponses illisible ({answers}) : {exc}")
    base = files_dir or answers.parent
    data: dict[str, bytes] = {}
    for spec in plan.files:
        path = base / spec.file
        if not path.is_file():
            _fail(f"Fichier introuvable : {path}")
        data[spec.file] = path.read_bytes()
    if not yes and not typer.confirm(
        f"Remettre à zéro la démo de {org} (réglages gardés) puis exporter {len(plan.files)} fichier(s) ?"
    ):
        raise typer.Exit(1)
    with session_scope() as s:
        ctx = _tenant(s, org, user)
        reset = reset_file_exports(s, SystemContext("cli"), ctx.organization_id, DEFAULT_CLIENT)
        try:
            results = prepare_demo(_wizard(s, ctx), plan, data)
        except (
            DemoQuestionUnanswered,
            DemoTargetUnknown,
            WizardInputError,
            WizardError,
            ImportFileError,
        ) as exc:
            # une seule transaction : la remise à zéro et les exports précédents sont annulés
            _fail(f"Arrêt, rien n'est enregistré. {exc}")
        accounts = file_accounts(s, ctx.organization_id, DEFAULT_CLIENT)
        register = sum(
            len(OpenItemRegister(s, ctx.organization_id, DEFAULT_CLIENT, system, product).items())
            for system, product in {tuple(f.target.split("/")[:2]) for f in plan.files}
        )
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
        for r in results:
            (out / r.filename).write_bytes(r.content)
    console.print(
        f"Remise à zéro : {reset.accounts} compte(s), {reset.documents} pièce(s), "
        f"{reset.open_items} facture(s) du registre retirés ; réglages gardés."
    )
    console.print(f"Démo préparée — {escape(org)}")
    for r in results:
        console.print(
            f"✓ {escape(r.file)} → {escape(r.target_label)} ({escape(r.data_type)}) : "
            + escape(" ".join(r.summary.notices) or f"{r.summary.rows} written.")
        )
        if r.answered:
            console.print("    réponses : " + escape(", ".join(f"{k} = {v}" for k, v in r.answered.items())))
    console.print(
        f"✓ {len(results)} fichier(s) exporté(s) ; {accounts} numéro(s) de compte réservé(s) ; "
        f"{register} facture(s) au registre des factures ouvertes"
        + (f" ; fichiers dans {out}" if out else "")
    )
