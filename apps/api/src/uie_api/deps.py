"""Dépendances FastAPI : une session SQLAlchemy par requête, un service Core par requête, construit avec
un contexte explicite (V1d) dérivé de la session authentifiée :

    /admin/*  → require_admin  → AdminContext  (platform_role = ADMIN ; sans session 401, sinon 403)
    /api/*    → require_tenant → TenantContext (organisation sélectionnée + membership revérifié)

Un même service Core, deux contextes : `Registry`, `Instances`, `ProductDomains`, `SemanticEnrichment`
(admin) et `Tenant*` (supplier). Aucune route ne construit de contexte elle-même.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from functools import lru_cache
from types import SimpleNamespace
from typing import Annotated, Any

from fastapi import Depends, Request
from sqlalchemy.orm import Session
from uie_ai import build_proposer
from uie_core.accounting import AccountingReadService, AccountingTargetService
from uie_core.accounting.api_target import AccountingApiTargetService
from uie_core.catalog import CatalogReaderResolver
from uie_core.catalog.service import DEFAULT_MAX_PRODUCTS, CatalogReadService, CatalogSyncService
from uie_core.db import session_scope
from uie_core.delivery import RecordSenderResolver
from uie_core.domains import (
    AccountingDomainService,
    DomainPackResolver,
    PartnerDomainService,
    ProductDomainService,
    SemanticEnrichmentService,
    SemanticProposer,
)
from uie_core.exports import ExportProfileService, PartnerExportService
from uie_core.identity import (
    AdminContext,
    Context,
    IdentityService,
    Principal,
    SystemContext,
    TenantContext,
)
from uie_core.identity.organizations import OrganizationService
from uie_core.imports import (
    CatalogImportService,
    CatalogWriterResolver,
    ColumnMappingProposer,
    FileStore,
    default_file_store,
)
from uie_core.imports.execution import CatalogImportExecutionService
from uie_core.instances import InstanceService, ProviderResolver, SecretStore, default_secret_store
from uie_core.partners import PartnerReadService, PartnerTargetService
from uie_core.products import ProductReadService, ProductTargetService
from uie_core.registry import RegistryService
from uie_core.sources import RecordSourceResolver
from uie_core.wizard import MigrationWizardProposer, MigrationWizardService, ReferenceFinder
from uie_core.wizard.templates import WizardTemplateService

from uie_api.auth import session_token, session_ttl


def get_session() -> Iterator[Session]:
    with session_scope() as s:
        yield s


DbSession = Annotated[Session, Depends(get_session)]


# --------------------------------------------------------------------------- #
# Authentification → contexte
# --------------------------------------------------------------------------- #
def get_identity_service(session: DbSession) -> IdentityService:
    # l'authentification a lieu avant tout contexte utilisateur : contexte système explicite
    return IdentityService(session, SystemContext("auth"), session_ttl())


Identity = Annotated[IdentityService, Depends(get_identity_service)]


def get_principal(request: Request, identity: Identity) -> Principal:
    return identity.resolve_session(session_token(request))  # AuthenticationError → 401


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


def require_admin(principal: CurrentPrincipal, identity: Identity) -> AdminContext:
    return identity.admin_context(principal)  # PermissionDenied → 403


def require_tenant(principal: CurrentPrincipal, identity: Identity) -> TenantContext:
    return identity.tenant_context(principal)  # PermissionDenied → 403


# --------------------------------------------------------------------------- #
# Dépendances sans contexte (surchargées dans les tests)
# --------------------------------------------------------------------------- #
def get_secret_store() -> SecretStore:
    return default_secret_store()


def get_provider_resolver() -> ProviderResolver:
    return ProviderResolver()


def get_domain_pack_resolver() -> DomainPackResolver:
    return DomainPackResolver()


def get_catalog_readers() -> CatalogReaderResolver:
    return CatalogReaderResolver()


def get_record_senders() -> RecordSenderResolver:
    """Expéditeurs vers les systèmes cibles par API (envoi d'articles) ; surchargeable dans les tests."""
    return RecordSenderResolver()


def get_record_sources() -> RecordSourceResolver:
    """Lecteurs des sources à schéma fixe (binding de source) ; surchargeable dans les tests."""
    return RecordSourceResolver()


def get_catalog_writers() -> CatalogWriterResolver:
    """Écrivains des systèmes cibles (V2d.2) ; surchargeable dans les tests."""
    return CatalogWriterResolver()


def get_file_store() -> FileStore:
    return default_file_store()


def catalog_sync_max() -> int:
    """Plafond de produits par synchronisation (V2a : synchrone, sans worker)."""
    raw = os.environ.get("UIE_CATALOG_SYNC_MAX", "").strip()
    return int(raw) if raw.isdigit() else DEFAULT_MAX_PRODUCTS


@lru_cache(maxsize=1)
def get_semantic_proposer() -> SemanticProposer:
    """Fournisseur LLM choisi d'après l'environnement (V1c) ; sans configuration : aucun LLM."""
    return build_proposer()


# --------------------------------------------------------------------------- #
# Services Core, construits avec le contexte donné par `ctx_dep`
# --------------------------------------------------------------------------- #
def _services(ctx_dep: Callable[..., Context]) -> dict[str, Any]:
    # contexte en valeur par défaut (dernier paramètre) : un alias Annotated local ne serait pas résolu
    # par FastAPI (annotations différées)
    ctx_param = Depends(ctx_dep)

    def registry(session: DbSession, ctx: Context = ctx_param) -> RegistryService:
        return RegistryService(session, ctx)

    def instances(
        session: DbSession,
        secrets: Annotated[SecretStore, Depends(get_secret_store)],
        providers: Annotated[ProviderResolver, Depends(get_provider_resolver)],
        ctx: Context = ctx_param,
    ) -> InstanceService:
        return InstanceService(session, ctx, secrets, providers)

    def product_domains(
        session: DbSession,
        packs: Annotated[DomainPackResolver, Depends(get_domain_pack_resolver)],
        ctx: Context = ctx_param,
    ) -> ProductDomainService:
        return ProductDomainService(session, ctx, packs)

    def semantic_enrichment(
        session: DbSession,
        packs: Annotated[DomainPackResolver, Depends(get_domain_pack_resolver)],
        proposer: Annotated[SemanticProposer, Depends(get_semantic_proposer)],
        ctx: Context = ctx_param,
    ) -> SemanticEnrichmentService:
        return SemanticEnrichmentService(session, ctx, packs, proposer=proposer)

    def catalog_sync(
        session: DbSession,
        secrets: Annotated[SecretStore, Depends(get_secret_store)],
        providers: Annotated[ProviderResolver, Depends(get_provider_resolver)],
        packs: Annotated[DomainPackResolver, Depends(get_domain_pack_resolver)],
        proposer: Annotated[SemanticProposer, Depends(get_semantic_proposer)],
        readers: Annotated[CatalogReaderResolver, Depends(get_catalog_readers)],
        ctx: Context = ctx_param,
    ) -> CatalogSyncService:
        return CatalogSyncService(
            session,
            ctx,
            InstanceService(session, ctx, secrets, providers),
            SemanticEnrichmentService(session, ctx, packs, proposer=proposer),
            readers,
            max_products=catalog_sync_max(),
        )

    def catalog_read(
        session: DbSession,
        secrets: Annotated[SecretStore, Depends(get_secret_store)],
        providers: Annotated[ProviderResolver, Depends(get_provider_resolver)],
        readers: Annotated[CatalogReaderResolver, Depends(get_catalog_readers)],
        ctx: Context = ctx_param,
    ) -> CatalogReadService:
        return CatalogReadService(session, ctx, InstanceService(session, ctx, secrets, providers), readers)

    def product_target(
        session: DbSession,
        secrets: Annotated[SecretStore, Depends(get_secret_store)],
        providers: Annotated[ProviderResolver, Depends(get_provider_resolver)],
        packs: Annotated[DomainPackResolver, Depends(get_domain_pack_resolver)],
        readers: Annotated[CatalogReaderResolver, Depends(get_catalog_readers)],
        senders: Annotated[RecordSenderResolver, Depends(get_record_senders)],
        ctx: Context = ctx_param,
    ) -> ProductTargetService:
        # aperçu de produits pour une cible par API : lecture à la demande vers le Product CDM (rien stocké)
        catalog = CatalogSyncService(
            session,
            ctx,
            InstanceService(session, ctx, secrets, providers),
            SemanticEnrichmentService(session, ctx, packs),
            readers,
        )
        return ProductTargetService(
            session,
            ProductReadService(session, catalog, max_products=catalog_sync_max()),
            senders=senders,
            commit=session.commit,  # envoi : chaque élément commité avant et après l'appel à la cible
        )

    def partner_enrichment(
        session: DbSession,
        packs: Annotated[DomainPackResolver, Depends(get_domain_pack_resolver)],
        proposer: Annotated[SemanticProposer, Depends(get_semantic_proposer)],
        ctx: Context = ctx_param,
    ) -> SemanticEnrichmentService:
        # domaine PARTNER : même moteur, pas de prompt LLM enregistré → aucun appel
        domains = PartnerDomainService(session, ctx, packs)
        return SemanticEnrichmentService(session, ctx, packs, domains=domains, proposer=proposer)

    def partner_export(
        session: DbSession,
        secrets: Annotated[SecretStore, Depends(get_secret_store)],
        providers: Annotated[ProviderResolver, Depends(get_provider_resolver)],
        packs: Annotated[DomainPackResolver, Depends(get_domain_pack_resolver)],
        readers: Annotated[CatalogReaderResolver, Depends(get_catalog_readers)],
        sources: Annotated[RecordSourceResolver, Depends(get_record_sources)],
        ctx: Context = ctx_param,
    ) -> PartnerExportService:
        domains = PartnerDomainService(session, ctx, packs)
        partners = PartnerReadService(
            session,
            ctx,
            InstanceService(session, ctx, secrets, providers),
            SemanticEnrichmentService(session, ctx, packs, domains=domains),
            readers,
            sources=sources,
        )
        return PartnerExportService(session, ctx, partners)

    def partner_target(
        session: DbSession,
        secrets: Annotated[SecretStore, Depends(get_secret_store)],
        providers: Annotated[ProviderResolver, Depends(get_provider_resolver)],
        packs: Annotated[DomainPackResolver, Depends(get_domain_pack_resolver)],
        readers: Annotated[CatalogReaderResolver, Depends(get_catalog_readers)],
        sources: Annotated[RecordSourceResolver, Depends(get_record_sources)],
        ctx: Context = ctx_param,
    ) -> PartnerTargetService:
        domains = PartnerDomainService(session, ctx, packs)
        partners = PartnerReadService(
            session,
            ctx,
            InstanceService(session, ctx, secrets, providers),
            SemanticEnrichmentService(session, ctx, packs, domains=domains),
            readers,
            sources=sources,
        )
        # commit PAR ÉLÉMENT : une écriture dans la cible est enregistrée avant la suivante (reprise sûre)
        return PartnerTargetService(session, ctx, partners, commit=session.commit)

    def export_profiles(session: DbSession, ctx: Context = ctx_param) -> ExportProfileService:
        return ExportProfileService(session, ctx)

    def accounting_enrichment(
        session: DbSession,
        packs: Annotated[DomainPackResolver, Depends(get_domain_pack_resolver)],
        proposer: Annotated[SemanticProposer, Depends(get_semantic_proposer)],
        ctx: Context = ctx_param,
    ) -> SemanticEnrichmentService:
        # domaine ACCOUNTING : même moteur, pas de prompt LLM enregistré → aucun appel
        domains = AccountingDomainService(session, ctx, packs)
        return SemanticEnrichmentService(session, ctx, packs, domains=domains, proposer=proposer)

    def accounting_read(
        session: DbSession,
        secrets: Annotated[SecretStore, Depends(get_secret_store)],
        providers: Annotated[ProviderResolver, Depends(get_provider_resolver)],
        packs: Annotated[DomainPackResolver, Depends(get_domain_pack_resolver)],
        readers: Annotated[CatalogReaderResolver, Depends(get_catalog_readers)],
        sources: Annotated[RecordSourceResolver, Depends(get_record_sources)],
        ctx: Context = ctx_param,
    ) -> AccountingReadService:
        domains = AccountingDomainService(session, ctx, packs)
        return AccountingReadService(
            session,
            ctx,
            InstanceService(session, ctx, secrets, providers),
            SemanticEnrichmentService(session, ctx, packs, domains=domains),
            readers,
            sources=sources,
        )

    def accounting_target(
        session: DbSession,
        secrets: Annotated[SecretStore, Depends(get_secret_store)],
        providers: Annotated[ProviderResolver, Depends(get_provider_resolver)],
        packs: Annotated[DomainPackResolver, Depends(get_domain_pack_resolver)],
        readers: Annotated[CatalogReaderResolver, Depends(get_catalog_readers)],
        sources: Annotated[RecordSourceResolver, Depends(get_record_sources)],
        ctx: Context = ctx_param,
    ) -> AccountingTargetService:
        # préparation d'un format cible (aperçu, aucun fichier) : lecture comptable + binding cible
        domains = AccountingDomainService(session, ctx, packs)
        reader = AccountingReadService(
            session,
            ctx,
            InstanceService(session, ctx, secrets, providers),
            SemanticEnrichmentService(session, ctx, packs, domains=domains),
            readers,
            sources=sources,
        )
        return AccountingTargetService(session, ctx, reader)

    def accounting_api_target(
        session: DbSession,
        secrets: Annotated[SecretStore, Depends(get_secret_store)],
        providers: Annotated[ProviderResolver, Depends(get_provider_resolver)],
        packs: Annotated[DomainPackResolver, Depends(get_domain_pack_resolver)],
        readers: Annotated[CatalogReaderResolver, Depends(get_catalog_readers)],
        sources: Annotated[RecordSourceResolver, Depends(get_record_sources)],
        ctx: Context = ctx_param,
    ) -> AccountingApiTargetService:
        # préparation de pièces pour un système cible relié par son API (aperçu, lecture seule)
        domains = AccountingDomainService(session, ctx, packs)
        reader = AccountingReadService(
            session,
            ctx,
            InstanceService(session, ctx, secrets, providers),
            SemanticEnrichmentService(session, ctx, packs, domains=domains),
            readers,
            sources=sources,
        )
        # commit PAR ÉLÉMENT : une écriture dans la cible est enregistrée avant la suivante
        return AccountingApiTargetService(session, ctx, reader, commit=session.commit)

    return {
        "accounting_api_target": accounting_api_target,
        "accounting_enrichment": accounting_enrichment,
        "accounting_read": accounting_read,
        "accounting_target": accounting_target,
        "partner_enrichment": partner_enrichment,
        "partner_export": partner_export,
        "partner_target": partner_target,
        "product_target": product_target,
        "export_profiles": export_profiles,
        "catalog_sync": catalog_sync,
        "catalog_read": catalog_read,
        "registry": registry,
        "instances": instances,
        "product_domains": product_domains,
        "semantic_enrichment": semantic_enrichment,
    }


_admin = _services(require_admin)
_tenant = _services(require_tenant)


def get_organization_service(
    session: DbSession, ctx: Annotated[AdminContext, Depends(require_admin)]
) -> OrganizationService:
    return OrganizationService(session, ctx)


# Admin (/admin/*)
Registry = Annotated[RegistryService, Depends(_admin["registry"])]
Instances = Annotated[InstanceService, Depends(_admin["instances"])]
ProductDomains = Annotated[ProductDomainService, Depends(_admin["product_domains"])]
SemanticEnrichment = Annotated[SemanticEnrichmentService, Depends(_admin["semantic_enrichment"])]
Organizations = Annotated[OrganizationService, Depends(get_organization_service)]
CatalogSync = Annotated[CatalogSyncService, Depends(_admin["catalog_sync"])]

# Supplier (/api/*)
TenantInstances = Annotated[InstanceService, Depends(_tenant["instances"])]
TenantProductDomains = Annotated[ProductDomainService, Depends(_tenant["product_domains"])]
TenantSemanticEnrichment = Annotated[SemanticEnrichmentService, Depends(_tenant["semantic_enrichment"])]
TenantCatalogSync = Annotated[CatalogSyncService, Depends(_tenant["catalog_sync"])]
TenantCatalog = Annotated[CatalogReadService, Depends(_tenant["catalog_read"])]
TenantPartnerEnrichment = Annotated[SemanticEnrichmentService, Depends(_tenant["partner_enrichment"])]
TenantPartnerExport = Annotated[PartnerExportService, Depends(_tenant["partner_export"])]
TenantExportProfiles = Annotated[ExportProfileService, Depends(_tenant["export_profiles"])]
TenantPartnerTarget = Annotated[PartnerTargetService, Depends(_tenant["partner_target"])]
TenantProductTarget = Annotated[ProductTargetService, Depends(_tenant["product_target"])]
TenantAccountingEnrichment = Annotated[SemanticEnrichmentService, Depends(_tenant["accounting_enrichment"])]
TenantAccountingRead = Annotated[AccountingReadService, Depends(_tenant["accounting_read"])]
TenantAccountingTarget = Annotated[AccountingTargetService, Depends(_tenant["accounting_target"])]
TenantAccountingApiTarget = Annotated[AccountingApiTargetService, Depends(_tenant["accounting_api_target"])]


def get_tenant_catalog_imports(
    session: DbSession,
    catalog: TenantCatalogSync,
    files: Annotated[FileStore, Depends(get_file_store)],
    proposer: Annotated[SemanticProposer, Depends(get_semantic_proposer)],
) -> CatalogImportService:
    # même fournisseur LLM que V1c (fallback des colonnes d'import, V2c.3) ; sans configuration : aucun appel
    columns = proposer if isinstance(proposer, ColumnMappingProposer) else None
    return CatalogImportService(session, catalog, files, columns)


TenantCatalogImports = Annotated[CatalogImportService, Depends(get_tenant_catalog_imports)]


def get_tenant_import_execution(
    session: DbSession,
    imports: TenantCatalogImports,
    writers: Annotated[CatalogWriterResolver, Depends(get_catalog_writers)],
) -> CatalogImportExecutionService:
    # commit PAR LIGNE : une ligne écrite dans le système est enregistrée avant la suivante (reprise sûre)
    return CatalogImportExecutionService(imports, writers, commit=session.commit)


TenantImportExecution = Annotated[CatalogImportExecutionService, Depends(get_tenant_import_execution)]


def get_wizard_proposer() -> MigrationWizardProposer | None:
    """Même fournisseur LLM que V1c, s'il sait conduire l'assistant de migration ; sinon aucun appel."""
    proposer = get_semantic_proposer()
    return proposer if isinstance(proposer, MigrationWizardProposer) else None


def get_tenant_migration_wizard(
    session: DbSession,
    proposer: Annotated[MigrationWizardProposer | None, Depends(get_wizard_proposer)],
    ctx: Annotated[TenantContext, Depends(require_tenant)],
    instances: TenantInstances,
    partner_export: TenantPartnerExport,
    accounting_target: TenantAccountingTarget,
) -> MigrationWizardService:
    # valeurs réelles (catégories, unités…) lues dans un système connecté de l'organisation, lecture seule ;
    # formats de fichier (DATEV…) : profils, numéros de compte et historique des exports existants
    exports = SimpleNamespace(
        partners=partner_export,
        accounting=accounting_target,
        profiles=ExportProfileService(session, ctx),
    )
    return MigrationWizardService(
        session, ctx, proposer, references=ReferenceFinder(instances), exports=exports
    )


TenantMigrationWizard = Annotated[MigrationWizardService, Depends(get_tenant_migration_wizard)]


def get_tenant_wizard_templates(
    session: DbSession, ctx: Annotated[TenantContext, Depends(require_tenant)]
) -> WizardTemplateService:
    return WizardTemplateService(session, ctx)


TenantWizardTemplates = Annotated[WizardTemplateService, Depends(get_tenant_wizard_templates)]
