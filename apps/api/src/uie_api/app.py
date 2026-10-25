"""Application FastAPI : entrypoint HTTP du Core (même Core que la CLI)."""

from __future__ import annotations

import json

import uie_connectors  # noqa: F401 — racine de composition : enregistre loaders et discovery providers
import uie_domain_packs  # noqa: F401 — racine de composition : enregistre les domain packs
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from uie_core.accounting import (
    AccountingNotReady,
    AccountingPlanError,
    AccountingTargetNotReady,
    ExportChanged,
    ExportInvalid,
    MappingInvalid,
)
from uie_core.catalog import CatalogPlanError, CatalogReadError
from uie_core.catalog.service import CatalogNotReady
from uie_core.delivery import DeliveryConflict
from uie_core.domains import DomainPackError
from uie_core.exports import ExportNotReady, ProfileInvalid
from uie_core.identity import AuthenticationError, PasswordError, PermissionDenied
from uie_core.imports import ImportConflict
from uie_core.instances import DiscoveryError, SecretError
from uie_core.partners import PartnerNotReady, PartnerPlanError
from uie_core.registry import NotFound, RegistryError
from uie_core.wizard import WizardError, WizardInputError, WizardUnavailable
from uie_core.wizard.templates import TemplateConflict

from uie_api.auth import RateLimited
from uie_api.routes import (
    accounting,
    auth,
    catalog,
    exports,
    imports,
    instances,
    organizations,
    partners,
    product_domains,
    registry,
    semantics,
    supplier,
    wizard,
)

# Console web Next.js en dev (apps/web)
CORS_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]


def create_app() -> FastAPI:
    app = FastAPI(
        title="UIE Admin API",
        version="0.4.0",
        description=(
            "API Admin du Universal Integration Engine — System Knowledge Registry (lecture) et "
            "instances client (création, test de connexion, découverte de schéma, Instance Diff), "
            "domaine PRODUCT (définition standard, domaine effectif d'une instance, Semantic Enrichment "
            "par règles et réponses du supplier)."
        ),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,  # V1d : cookie de session (origines explicites, jamais « * »)
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type"],
    )

    @app.exception_handler(AuthenticationError)
    def _unauthenticated(_: Request, exc: AuthenticationError) -> JSONResponse:
        return JSONResponse(status_code=401, content={"detail": str(exc)})

    @app.exception_handler(PermissionDenied)
    def _forbidden(_: Request, exc: PermissionDenied) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @app.exception_handler(RateLimited)
    def _rate_limited(_: Request, exc: RateLimited) -> JSONResponse:
        return JSONResponse(
            status_code=429, content={"detail": str(exc)}, headers={"Retry-After": str(exc.retry_after)}
        )

    @app.exception_handler(PasswordError)
    def _password_error(_: Request, exc: PasswordError) -> JSONResponse:
        # mot de passe trop court / email invalide : le message ne contient jamais le mot de passe
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(CatalogNotReady)
    def _catalog_not_ready(_: Request, exc: CatalogNotReady) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ExportChanged)
    async def export_changed(_: Request, exc: ExportChanged) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ExportInvalid)
    @app.exception_handler(MappingInvalid)
    async def mapping_invalid(_: Request, exc: ExportInvalid | MappingInvalid) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(AccountingTargetNotReady)
    @app.exception_handler(AccountingNotReady)
    @app.exception_handler(PartnerNotReady)
    @app.exception_handler(ExportNotReady)
    def _export_not_ready(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ProfileInvalid)
    def _profile_invalid(_: Request, exc: ProfileInvalid) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc), "errors": exc.errors})

    @app.exception_handler(AccountingPlanError)
    @app.exception_handler(PartnerPlanError)
    def _partner_plan(_: Request, exc: PartnerPlanError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(CatalogPlanError)
    def _catalog_plan(_: Request, exc: CatalogPlanError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(CatalogReadError)
    def _catalog_read(_: Request, exc: CatalogReadError) -> JSONResponse:
        # message déjà sans secret (connecteur)
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.exception_handler(TemplateConflict)
    @app.exception_handler(DeliveryConflict)
    @app.exception_handler(ImportConflict)
    def _import_conflict(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(WizardInputError)
    def _wizard_input(_: Request, exc: WizardInputError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(WizardUnavailable)
    def _wizard_unavailable(_: Request, exc: WizardUnavailable) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(WizardError)
    def _wizard_error(_: Request, exc: WizardError) -> JSONResponse:
        # réponse de l'IA absente ou invalide : jamais montrée telle quelle
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.exception_handler(NotFound)
    def _not_found(_: Request, exc: NotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(RegistryError)
    def _registry_error(_: Request, exc: RegistryError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(DomainPackError)
    def _domain_pack_error(_: Request, exc: DomainPackError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(DiscoveryError)
    def _discovery_error(_: Request, exc: DiscoveryError) -> JSONResponse:
        # message déjà passé par redact() dans InstanceService
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.exception_handler(SecretError)
    def _secret_error(_: Request, exc: SecretError) -> JSONResponse:
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    @app.exception_handler(RequestValidationError)
    def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Par défaut FastAPI renvoie l'`input` reçu : il pourrait contenir un mot de passe.
        errors = [{"loc": e.get("loc"), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": errors})

    app.include_router(auth.router)
    app.include_router(registry.router)
    app.include_router(instances.router)
    app.include_router(product_domains.router)
    app.include_router(semantics.router)
    app.include_router(organizations.router)
    app.include_router(supplier.router)
    app.include_router(catalog.router)
    app.include_router(catalog.admin_router)
    app.include_router(imports.router)
    app.include_router(partners.router)
    app.include_router(accounting.router)
    app.include_router(exports.router)
    app.include_router(wizard.router)
    return app


app = create_app()


def openapi_json() -> str:
    """Schéma OpenAPI sérialisé de façon stable (fichier commité apps/web/openapi.json)."""
    return json.dumps(app.openapi(), indent=2, ensure_ascii=False, sort_keys=True) + "\n"
