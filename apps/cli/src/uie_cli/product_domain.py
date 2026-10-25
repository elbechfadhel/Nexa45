"""`uie product-domain ...` : mêmes services que l'API (Product Domain V1a / V1b).

L'analyse travaille uniquement sur des snapshots déjà persistés (jamais sur l'instance en direct) ; elle
enrichit ensuite le domaine par règles (Semantic Enrichment, sans LLM). Les réponses du supplier
(confirm / change / ignore / include) sont validées par le Core. `--instance` : id ou nom de l'instance.
V1c : `enrich-ai` (Ask AI) est la seule commande qui appelle un LLM ; `ai-status` n'appelle rien.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table
from sqlalchemy import select
from uie_core.db import session_scope
from uie_core.domains import (
    OTHER_KEY,
    AiEnrichmentResult,
    BindingView,
    CatalogAnalysisSummary,
    DomainFieldDef,
    DomainPackError,
    DomainPackResolver,
    DomainSummary,
    EnrichmentResult,
    FieldGroup,
    FieldOrigin,
    ProductDomainService,
    Relevance,
    SemanticEnrichmentService,
    SemanticProposer,
    StoredDomainSnapshot,
    summarize_domain,
)
from uie_core.identity import SystemContext
from uie_core.registry import NotFound, RegistryError
from uie_core.registry import models as m

app = typer.Typer(
    help="Domaine PRODUCT : définition standard, domaine effectif d'une instance", no_args_is_help=True
)
console = Console()
err = Console(stderr=True, style="bold red")

ORIGIN_STYLE = {"STANDARD": "cyan", "CUSTOM": "yellow", "THIRD_PARTY": "magenta", "UNKNOWN": "dim"}
RELEVANCE_STYLE = {"RELEVANT": "green", "POSSIBLE": "yellow", "NOT_RELEVANT": "dim", "UNRESOLVED": "magenta"}

InstanceOpt = Annotated[str, typer.Option("--instance", "-i", help="Id ou nom de l'instance")]
SnapshotOpt = Annotated[str | None, typer.Option("--snapshot", help="Id d'un domain snapshot précis")]


def _fail(msg: str, code: int = 1) -> None:
    err.print(escape(msg))
    raise typer.Exit(code)


@contextmanager
def domain_service() -> Iterator[ProductDomainService]:
    """Même construction que l'API (uie_api.deps.get_product_domain_service)."""
    import uie_domain_packs  # noqa: F401 — enregistre les domain packs

    try:
        with session_scope() as s:
            yield ProductDomainService(s, SystemContext("cli"), DomainPackResolver())
    except (RegistryError, DomainPackError) as exc:
        _fail(str(exc))


@contextmanager
def semantic_service(proposer: SemanticProposer | None = None) -> Iterator[SemanticEnrichmentService]:
    """Même construction que l'API (uie_api.deps.get_semantic_enrichment_service)."""
    import uie_domain_packs  # noqa: F401 — enregistre les domain packs
    from uie_ai import build_proposer

    try:
        with session_scope() as s:
            yield SemanticEnrichmentService(
                s, SystemContext("cli"), DomainPackResolver(), proposer=proposer or build_proposer()
            )
    except (RegistryError, DomainPackError) as exc:
        _fail(str(exc))


def _instance_id(svc: ProductDomainService | SemanticEnrichmentService, ref: str) -> uuid.UUID:
    try:
        return uuid.UUID(ref)
    except ValueError:
        inst = svc.s.scalar(select(m.Instance).where(m.Instance.name == ref))
        if inst is None:
            raise NotFound(f"Instance '{ref}' introuvable") from None
        return inst.id


def _snapshot_id(ref: str | None) -> uuid.UUID | None:
    return uuid.UUID(ref) if ref else None


# --------------------------------------------------------------------------- #
# commandes
# --------------------------------------------------------------------------- #
@app.command("show-standard")
def show_standard(
    system: Annotated[str, typer.Option("--system", "-s")],
    version: Annotated[str, typer.Option("--version", "-v")],
    snapshot_version: Annotated[str | None, typer.Option("--snapshot-version")] = None,
    fields: Annotated[
        bool, typer.Option("--fields", help="Lister aussi les champs listés et médias")
    ] = False,
) -> None:
    """Définition standard du domaine PRODUCT, résolue sur le snapshot standard de la version."""
    with domain_service() as svc:
        stored = svc.get_standard_definition(system, version, snapshot_version)
        d = stored.definition
        console.print(
            f"[bold]{d.domain_key.value}[/bold] {d.system} {d.version} · pack {d.pack} "
            f"@{d.definition_version} · checksum {d.checksum[:12]}\n"
            f"[dim]définition {stored.id} · snapshot standard {d.base_schema_snapshot_id}[/dim]"
        )
        _print_summary(summarize_domain(d.entities, d.fields))
        absent = [f.path for f in d.fields if not f.present]
        if absent:
            console.print(f"[dim]Déclarés mais absents de cette version : {', '.join(absent)}[/dim]")
        if fields:
            _print_fields(
                [f for f in d.fields if f.present and (f.listed or f.is_media)], "Champs listés et médias"
            )


@app.command("analyze")
def analyze(
    instance: InstanceOpt,
    instance_snapshot: Annotated[
        str | None,
        typer.Option("--instance-snapshot", help="Id d'un snapshot d'instance (dernier par défaut)"),
    ] = None,
) -> None:
    """Domaine effectif depuis le snapshot d'instance persisté (idempotent), puis Semantic Enrichment."""
    with domain_service() as svc:
        outcome = svc.analyze(_instance_id(svc, instance), _snapshot_id(instance_snapshot))
        stored = outcome.stored
        verb = "créé" if stored.created else "déjà analysé (snapshot existant retourné)"
        console.print(f"[green]✓[/green] domain snapshot {stored.id} {verb}")
        _print_header(stored)
        s = stored.snapshot
        _print_summary(summarize_domain(s.entities, s.fields, s.extensions, s.missing_standard))
        if outcome.enrichment:
            _print_enrichment(outcome.enrichment)


@app.command("show")
def show(instance: InstanceOpt, snapshot: SnapshotOpt = None) -> None:
    """Domaine effectif de l'instance (dernier analysé par défaut)."""
    with domain_service() as svc:
        stored = svc.get_instance_domain(_instance_id(svc, instance), _snapshot_id(snapshot))
        _print_header(stored)
        s = stored.snapshot
        _print_summary(summarize_domain(s.entities, s.fields, s.extensions, s.missing_standard))
        _print_fields(s.extensions, "Extensions (à qualifier en V1b)")


@app.command("fields")
def list_fields(
    instance: InstanceOpt,
    group: Annotated[FieldGroup | None, typer.Option("--group", "-g")] = None,
    origin: Annotated[FieldOrigin | None, typer.Option("--origin", "-o")] = None,
    relevance: Annotated[Relevance | None, typer.Option("--relevance", "-r")] = None,
    entity: Annotated[str | None, typer.Option("--entity", "-e")] = None,
    snapshot: SnapshotOpt = None,
) -> None:
    """Champs du domaine effectif, filtrables par groupe, origine, pertinence, entité."""
    with domain_service() as svc:
        rows = svc.list_domain_fields(
            _instance_id(svc, instance),
            _snapshot_id(snapshot),
            group=group,
            origin=origin,
            relevance=relevance,
            entity=entity,
        )
        _print_fields(rows, f"{len(rows)} champ(s)")


@app.command("extensions")
def extensions(instance: InstanceOpt, snapshot: SnapshotOpt = None) -> None:
    """Champs CUSTOM / THIRD_PARTY ajoutés sur les entités du domaine."""
    with domain_service() as svc:
        rows = svc.list_extensions(_instance_id(svc, instance), _snapshot_id(snapshot))
        _print_fields(rows, f"{len(rows)} extension(s)")


@app.command("missing")
def missing(instance: InstanceOpt, snapshot: SnapshotOpt = None) -> None:
    """Éléments du standard (baseline) absents de l'instance."""
    with domain_service() as svc:
        rows = svc.list_missing_standard(_instance_id(svc, instance), _snapshot_id(snapshot))
        if not rows:
            console.print("[green]Aucun élément standard du domaine ne manque dans l'instance.[/green]")
            return
        t = Table(title=f"{len(rows)} élément(s) standard absent(s)")
        for col in ("Type", "Élément", "Rôle / groupe", "Origine", "Raison"):
            t.add_column(col)
        for r in rows:
            path = r.entity_technical_name + (f".{r.field_technical_name}" if r.field_technical_name else "")
            kind = (r.role.value if r.role else "") or (r.group.value if r.group else "")
            t.add_row(r.object_type, path, kind, _origin(r.origin.value), escape(r.reason or ""))
        console.print(t)


@app.command("history")
def history(instance: InstanceOpt) -> None:
    """Domain snapshots (immutables) de l'instance, du plus récent au plus ancien."""
    with domain_service() as svc:
        rows = svc.list_instance_domains(_instance_id(svc, instance))
        t = Table(title="Historique du domaine PRODUCT")
        for col in ("Domain snapshot", "Snapshot d'instance", "Analysé le", "Champs", "Ext.", "Manquants"):
            t.add_column(col)
        for r in rows:
            s = r.snapshot
            t.add_row(
                str(r.id),
                f"#{r.instance_schema_snapshot_sequence}",
                f"{s.analyzed_at:%Y-%m-%d %H:%M}",
                str(len(s.fields)),
                str(len(s.extensions)),
                str(len(s.missing_standard)),
            )
        console.print(t)


# --------------------------------------------------------------------------- #
# affichage
# --------------------------------------------------------------------------- #
def _origin(value: str) -> str:
    return f"[{ORIGIN_STYLE.get(value, 'white')}]{value}[/]"


def _print_header(stored: StoredDomainSnapshot) -> None:
    s = stored.snapshot
    console.print(
        f"[bold]{s.domain_key.value}[/bold] · "
        f"snapshot d'instance #{stored.instance_schema_snapshot_sequence} "
        f"({s.instance_schema_snapshot_id}) · baseline {s.base_schema_snapshot_id}\n"
        f"[dim]définition @{s.definition_version} ({s.standard_definition_checksum[:12]}) · "
        f"analysé le {s.analyzed_at:%Y-%m-%d %H:%M} · checksum {s.checksum[:12]}[/dim]"
    )


def _print_summary(summary: DomainSummary) -> None:
    t = Table(title="Entités par rôle", show_header=True)
    t.add_column("Rôle")
    t.add_column("Entités")
    for role, names in summary.entities_by_role.items():
        t.add_row(role, ", ".join(names))
    if summary.absent_entities:
        t.add_row("[dim]absentes[/dim]", f"[dim]{', '.join(summary.absent_entities)}[/dim]")
    console.print(t)
    g = Table(title=f"{summary.field_count} champ(s) par groupe")
    g.add_column("Groupe")
    g.add_column("Champs", justify="right")
    for group, n in summary.fields_by_group.items():
        g.add_row(group, str(n))
    console.print(g)
    console.print(
        "Origine : "
        + " · ".join(f"{_origin(k)} {v}" for k, v in summary.fields_by_origin.items())
        + "\nPertinence : "
        + " · ".join(
            f"[{RELEVANCE_STYLE.get(k, 'white')}]{k}[/] {v}" for k, v in summary.fields_by_relevance.items()
        )
    )
    if summary.media:
        console.print("Médias : " + ", ".join(f"{m_.path} ({m_.media_role.value})" for m_ in summary.media))
    console.print(
        f"Extensions : {summary.extension_count} · éléments standard absents : {summary.missing_count}"
    )


def _print_fields(rows: list[DomainFieldDef], title: str) -> None:
    if not rows:
        console.print(f"[dim]{title} : aucun[/dim]")
        return
    t = Table(title=title)
    for col in ("Champ", "Libellé", "Type", "Groupe", "Origine", "Pertinence", "Modules / raison"):
        t.add_column(col)
    for f in rows:
        kind = f.native_type or f.data_type or ""
        if f.relation_target:
            kind += f" → {f.relation_target}"
        if f.media_role:
            kind += f" [media:{f.media_role.value}]"
        note = ", ".join(f.modules) + (f" · {f.origin_reason}" if f.origin_reason else "")
        t.add_row(
            f.path,
            escape(f.label or ""),
            escape(kind),
            f.group.value,
            _origin(f.origin.value),
            f"[{RELEVANCE_STYLE.get(f.relevance.value, 'white')}]{f.relevance.value}[/]",
            escape(note),
        )
    console.print(t)


# --------------------------------------------------------------------------- #
# Semantic Enrichment (V1b)
# --------------------------------------------------------------------------- #
EntityOpt = Annotated[str, typer.Option("--entity", "-e", help="Entité (nom technique)")]
FieldOpt = Annotated[str, typer.Option("--field", "-f", help="Champ (nom technique)")]
NoteOpt = Annotated[str | None, typer.Option("--note", help="Commentaire consigné avec la décision")]
STATUS_STYLE = {
    "KNOWN": "green",
    "CONFIRMED": "green",
    "PROPOSED": "yellow",
    "UNRESOLVED": "magenta",
    "STALE": "red",
    "EXCLUDED": "dim",
    "IGNORED": "dim",
}


@app.command("summary")
def summary(instance: InstanceOpt) -> None:
    """Product Catalog Analysis : compteurs, structure, questions restantes (état persisté)."""
    with semantic_service() as svc:
        _print_catalog_summary(svc.summary(_instance_id(svc, instance)))


@app.command("questions")
def questions(instance: InstanceOpt) -> None:
    """Questions ouvertes pour le supplier (extensions d'abord, puis confiance décroissante)."""
    with semantic_service() as svc:
        rows = svc.get_pending_questions(_instance_id(svc, instance))
        if not rows:
            console.print("[green]✓ Aucune question : analyse du catalogue complète[/green]")
            return
        _print_bindings(rows, f"{len(rows)} question(s)")


@app.command("decisions")
def decisions(instance: InstanceOpt) -> None:
    """Réponses données par le supplier, la plus récente d'abord (modifiables ou à rouvrir : reopen)."""
    with semantic_service() as svc:
        rows = svc.list_decisions(_instance_id(svc, instance))
        _print_bindings(rows, f"{len(rows)} réponse(s) du supplier")


@app.command("exclusions")
def exclusions(
    instance: InstanceOpt,
    extensions_only: Annotated[bool, typer.Option("--extensions", help="Seulement les extensions")] = False,
) -> None:
    """Champs exclus automatiquement par une règle, avec la raison (contestables : include)."""
    with semantic_service() as svc:
        rows = svc.list_exclusions(_instance_id(svc, instance), extensions_only=extensions_only)
        _print_bindings(rows, f"{len(rows)} exclusion(s) automatique(s)")


@app.command("confirm")
def confirm(
    instance: InstanceOpt,
    entity: EntityOpt,
    field: FieldOpt,
    key: Annotated[
        str | None, typer.Option("--key", "-k", help="Clé du vocabulaire (défaut : la proposition)")
    ] = None,
    note: NoteOpt = None,
) -> None:
    """Confirme la proposition (ou la clé donnée) → CONFIRMED."""
    with semantic_service() as svc:
        _print_decision(svc.confirm(_instance_id(svc, instance), entity, field, key, note))


@app.command("change")
def change(
    instance: InstanceOpt,
    entity: EntityOpt,
    field: FieldOpt,
    key: Annotated[str, typer.Option("--key", "-k", help=f"Clé du vocabulaire, ou '{OTHER_KEY}'")],
    note: NoteOpt = None,
) -> None:
    """Choisit une autre clé du vocabulaire → CONFIRMED ; « other » → UNRESOLVED avec la note."""
    with semantic_service() as svc:
        _print_decision(svc.change(_instance_id(svc, instance), entity, field, key, note))


@app.command("ignore")
def ignore(instance: InstanceOpt, entity: EntityOpt, field: FieldOpt, note: NoteOpt = None) -> None:
    """Exclut le champ du catalogue → IGNORED."""
    with semantic_service() as svc:
        _print_decision(svc.ignore(_instance_id(svc, instance), entity, field, note))


@app.command("include")
def include(instance: InstanceOpt, entity: EntityOpt, field: FieldOpt, note: NoteOpt = None) -> None:
    """Conteste une exclusion : le champ redevient une question."""
    with semantic_service() as svc:
        _print_decision(svc.include(_instance_id(svc, instance), entity, field, note))


@app.command("reopen")
def reopen(instance: InstanceOpt, entity: EntityOpt, field: FieldOpt, note: NoteOpt = None) -> None:
    """Revient sur une réponse du supplier (erreur) : le champ redevient une question."""
    with semantic_service() as svc:
        _print_decision(svc.reopen(_instance_id(svc, instance), entity, field, note))


@app.command("enrich-ai")
def enrich_ai(
    instance: InstanceOpt,
    field: Annotated[
        list[str] | None,
        typer.Option("--field", help="entité.champ (répétable) ; défaut : toutes les cibles"),
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", help="Ignorer le cache et la priorité des règles")
    ] = False,
    max_fields: Annotated[int, typer.Option("--max-fields", min=1, max=200)] = 50,
    provider: Annotated[
        str | None, typer.Option("--provider", help="Surcharge UIE_LLM_PROVIDER (anthropic, openai, replay)")
    ] = None,
    replay_file: Annotated[Path | None, typer.Option("--replay-file", help="Réponses enregistrées")] = None,
    record_replay: Annotated[
        Path | None, typer.Option("--record-replay", help="Enregistre les réponses brutes dans ce fichier")
    ] = None,
) -> None:
    """Ask AI : propose une clé du vocabulaire pour les champs encore ambigus (jamais une exclusion).

    Envoie au fournisseur les MÉTADONNÉES des champs (aucune valeur métier, aucun secret, aucun nom
    d'instance). Le résultat est toujours une question au supplier."""
    from dataclasses import replace

    from uie_ai import LlmSettings, RecordingProposer, build_proposer

    settings = LlmSettings.from_env()
    if provider:
        settings = replace(settings, provider=provider)
    if replay_file:
        settings = replace(settings, provider="replay", replay_file=str(replay_file))
    proposer: SemanticProposer = build_proposer(settings)
    if proposer.provider == "none":
        _fail("Aucun fournisseur LLM configuré (UIE_LLM_PROVIDER + clé, ou --replay-file)")
    if record_replay:
        proposer = RecordingProposer(proposer, record_replay)
    refs = []
    for ref in field or []:
        entity, _, name = ref.rpartition(".")
        if not entity:
            _fail(f"--field attend entité.champ : {ref}")
        refs.append((entity, name))
    with semantic_service(proposer) as svc:
        out = svc.enrich_with_llm(
            _instance_id(svc, instance), refs or None, force=force, max_fields=max_fields
        )
        _print_ai_result(out)


@app.command("ai-status")
def ai_status(instance: InstanceOpt) -> None:
    """Fournisseur LLM configuré, candidats, appels, tokens et coût (n'appelle aucun LLM)."""
    with semantic_service() as svc:
        st = svc.ai_status(_instance_id(svc, instance))
    model = f" · {st.model}" if st.model else ""
    console.print(f"[bold]AI[/bold] {st.provider}{model} · prompt {st.prompt_version or '—'}")
    console.print(
        f"candidats {st.candidates} · questions avec proposition IA {st.ai_proposed} · "
        f"sans suggestion {st.ai_unresolved}"
    )
    cost = f"{st.cost_usd:.4f} $" if st.cost_usd is not None else "—"
    console.print(
        f"appels {st.calls} · dernier {st.last_call_at or '—'} · tokens {st.input_tokens} + "
        f"{st.output_tokens} · coût estimé {cost}"
    )


def _print_ai_result(r: AiEnrichmentResult) -> None:
    console.print(
        f"[bold]Ask AI[/bold] {r.provider} · {r.model or '—'} · prompt {r.prompt_version} "
        f"({r.prompt_checksum[:12]}) · cibles {r.targets}"
    )
    console.print(
        f"[green]proposés {r.proposed}[/green] · [yellow]faible confiance {r.low_confidence}[/yellow] · "
        f"sans correspondance {r.no_confident_match} · [red]erreurs {r.errors}[/red] · ignorés {r.skipped}"
    )
    t = Table()
    for col in ("Champ", "Résultat", "Clé", "Confiance", "Raison / justification"):
        t.add_column(col)
    for o in r.fields:
        conf = f"{o.confidence:.0%}" if o.confidence is not None else ""
        t.add_row(o.path, o.outcome, o.semantic_key or "", conf, escape(o.reason or ""))
    console.print(t)


def _print_enrichment(e: EnrichmentResult) -> None:
    console.print(
        f"[bold]Semantic Enrichment[/bold] règles {e.rules_version} ({e.rules_checksum[:12]}) · "
        f"créés {e.created} · recalculés {e.updated} · inchangés {e.unchanged} · "
        f"décisions réappliquées {e.reapplied} · STALE {e.stale} · ORPHANED {e.orphaned} · "
        f"[yellow]questions {e.pending}[/yellow]"
    )


def _print_catalog_summary(c: CatalogAnalysisSummary) -> None:
    checks = "  ".join(
        f"[green]✓[/green] {x.role.value}" if x.present else f"[red]✗[/red] {x.role.value}"
        for x in c.structure
    )
    console.print(
        f"[bold]Product Catalog Analysis[/bold] · "
        f"snapshot d'instance #{c.instance_schema_snapshot_sequence} · règles {c.rules_version or '—'}"
    )
    console.print(checks)

    def pct(n: int) -> str:
        return f"{100 * n / c.total:.0f} %" if c.total else "—"

    t = Table(show_header=False)
    t.add_column(justify="right")
    t.add_column()
    t.add_row(
        str(c.identified),
        f"champs du catalogue identifiés ({pct(c.identified)}, dont {c.identified_with_key} avec une clé)",
    )
    t.add_row(
        str(c.excluded_auto),
        f"exclus automatiquement ({pct(c.excluded_auto)}) : "
        + ", ".join(f"{k} {v}" for k, v in c.excluded_auto_by_rule.items()),
    )
    t.add_row(str(c.excluded_by_supplier), "exclus par le supplier")
    t.add_row(str(c.possible), f"possibles, sans question ({pct(c.possible)})")
    t.add_row(f"[yellow]{c.pending}[/yellow]", "questions ouvertes")
    t.add_row(str(c.total), "total (hors champs disparus)")
    console.print(t)
    console.print(
        f"Extensions : {c.extensions} · exclues automatiquement {c.extensions_excluded_auto} · "
        f"en question {c.extensions_pending}"
    )
    console.print(f"Réponses du supplier : {c.supplier_decisions} (uie product-domain decisions)")
    if c.complete:
        console.print("[green]✓ Catalog analysis complete · 0 fields pending · Analysis saved[/green]")


def _print_decision(b: BindingView) -> None:
    style = STATUS_STYLE.get(b.meaning_status.value, "white")
    key = f"{b.semantic_key} ({b.semantic_label})" if b.semantic_key else "—"
    console.print(f"[{style}]{b.meaning_status.value}[/] {b.path} → {escape(key)} · {b.relevance.value}")


def _print_bindings(rows: list[BindingView], title: str) -> None:
    if not rows:
        console.print(f"[dim]{title}[/dim]")
        return
    t = Table(title=title)
    for col in ("Champ", "Libellé", "Type", "Origine", "Statut", "Proposition", "Règle", "Raison"):
        t.add_column(col)
    for b in rows:
        f = b.field
        kind = (f.native_type or f.data_type or "") + (f" → {f.relation_target}" if f.relation_target else "")
        prop = b.semantic_key or ""
        if b.semantic_key and b.confidence:
            prop += f" ({b.confidence:.0%})"
        style = STATUS_STYLE.get(b.meaning_status.value, "white")
        t.add_row(
            b.path,
            escape(f.label or ""),
            escape(kind),
            _origin(b.origin.value),
            f"[{style}]{b.meaning_status.value}[/]",
            escape(prop),
            b.rule_id or "",
            escape(b.reason or ""),
        )
    console.print(t)
