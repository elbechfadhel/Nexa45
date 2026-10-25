"""Contextes d'exécution (V1d) : TOUT service du Core reçoit un contexte explicite, jamais implicite.

    TenantContext  { user_id, organization_id, membership_role }  construit par le backend depuis la
                   session ET un membership vérifié, jamais depuis un organization_id du client ; FILTRE
    AdminContext   { user_id }   administrateur plateforme GO45 (platform_role = ADMIN) ; voit tout
    SystemContext  { reason }    CLI, migrations, worker, tests ; tous tenants ; créé explicitement

Isolation : une ressource d'un autre tenant est traitée exactement comme une ressource inexistante (même
NotFound, même message). Connaissance plateforme (Registry standard, domain packs, vocabulaire, prompts) :
lisible par tous, modifiable seulement en Admin / System (PermissionDenied en TenantContext).
Aucun framework web ici (test d'architecture).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from uie_core.identity.models import MembershipRole


class PermissionDenied(Exception):  # noqa: N818 — nom du domaine (→ HTTP 403)
    """Action réservée à la plateforme (Admin / System) tentée depuis un contexte tenant."""


@dataclass(frozen=True, slots=True)
class TenantContext:
    user_id: uuid.UUID
    organization_id: uuid.UUID
    membership_role: MembershipRole


@dataclass(frozen=True, slots=True)
class AdminContext:
    user_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class SystemContext:
    reason: str

    def __post_init__(self) -> None:
        if not self.reason or not self.reason.strip():
            raise ValueError("SystemContext exige une raison explicite (ex. « cli », « worker », « test »)")


Context = TenantContext | AdminContext | SystemContext


def ensure_context(ctx: Any) -> Context:
    """Garde des constructeurs de service : un contexte valide est obligatoire (aucune valeur par défaut)."""
    if not isinstance(ctx, TenantContext | AdminContext | SystemContext):
        raise TypeError(
            f"Contexte obligatoire (TenantContext, AdminContext ou SystemContext), reçu : {ctx!r}"
        )
    return ctx


def tenant_organization(ctx: Context) -> uuid.UUID | None:
    """Organisation à laquelle limiter les données ; None = aucun filtre (Admin / System)."""
    return ctx.organization_id if isinstance(ctx, TenantContext) else None


def require_platform(ctx: Context, action: str) -> None:
    """Écriture de connaissance plateforme : interdite à un tenant."""
    if isinstance(ctx, TenantContext):
        raise PermissionDenied(f"Action réservée à l'administration de la plateforme : {action}")
