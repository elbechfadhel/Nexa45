"""Remise à zéro de la démo : comptes réservés et pièces journalisées d'une source FICHIER d'une organisation.

Pour répéter une démonstration (liste de clients puis factures vers un format de fichier) : retire les numéros
de compte réservés (références « file: »), les fichiers générés depuis un fichier et l'historique de leurs
pièces, pour un dossier (mandant) ou tous. Rien d'une source CONNECTÉE n'est touché (ses comptes, ses fichiers
et son historique ont une instance, ou une référence sans « file: ») ; les réglages du dossier sont gardés
sauf demande explicite. Réservé à la plateforme (CLI) : jamais une route du portail."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from uie_core.exports.records import (
    ExportAccountAssignmentRecord,
    ExportDocumentRecord,
    ExportDocumentReleaseRecord,
    ExportProfileRecord,
    ExportRunRecord,
)
from uie_core.identity.context import AdminContext, Context, PermissionDenied, SystemContext, ensure_context

FILE_PREFIX = "file:"


@dataclass(frozen=True)
class DemoReset:
    accounts: int  # numéros de compte réservés retirés
    documents: int  # pièces journalisées retirées
    runs: int  # fichiers générés retirés du journal
    profiles: int  # réglages retirés (seulement sur demande)


def reset_file_exports(
    s: Session,
    ctx: Context,
    organization_id: uuid.UUID,
    client_key: str | None = None,
    *,
    with_settings: bool = False,
) -> DemoReset:
    """État d'export d'une source FICHIER de l'organisation, retiré (`client_key` : un dossier ; None :
    tous)."""
    ctx = ensure_context(ctx)
    if not isinstance(ctx, SystemContext | AdminContext):
        raise PermissionDenied("Resetting the demo is reserved to the platform (CLI)")
    profiles = select(ExportProfileRecord.id).where(ExportProfileRecord.organization_id == organization_id)
    if client_key is not None:
        profiles = profiles.where(ExportProfileRecord.client_key == client_key)
    profile_ids = list(s.scalars(profiles))

    assignments = select(ExportAccountAssignmentRecord).where(
        ExportAccountAssignmentRecord.profile_id.in_(profile_ids),
        ExportAccountAssignmentRecord.instance_id.is_(None),
        ExportAccountAssignmentRecord.source_ref.like(f"{FILE_PREFIX}%"),
    )
    accounts = list(s.scalars(assignments))
    records = select(ExportDocumentRecord).where(
        ExportDocumentRecord.organization_id == organization_id,
        ExportDocumentRecord.instance_id.is_(None),
        ExportDocumentRecord.source_ref.like(f"{FILE_PREFIX}%"),
    )
    if client_key is not None:
        records = records.where(ExportDocumentRecord.client_key == client_key)
    documents = list(s.scalars(records))
    # fichiers générés depuis une source fichier : marqués « file », ou cités par ses comptes / ses pièces
    run_ids = {a.export_run_id for a in accounts if a.export_run_id} | {
        d.export_run_id for d in documents if d.export_run_id
    }
    run_ids |= set(
        s.scalars(
            select(ExportRunRecord.id).where(
                ExportRunRecord.profile_id.in_(profile_ids),
                ExportRunRecord.instance_id.is_(None),
                ExportRunRecord.details["source"].astext == "file",
            )
        )
    )
    doc_ids = [d.id for d in documents]
    if doc_ids:
        s.execute(
            delete(ExportDocumentReleaseRecord).where(
                ExportDocumentReleaseRecord.previous_record_id.in_(doc_ids)
            )
        )
        s.execute(delete(ExportDocumentRecord).where(ExportDocumentRecord.id.in_(doc_ids)))
    if accounts:
        s.execute(
            delete(ExportAccountAssignmentRecord).where(
                ExportAccountAssignmentRecord.id.in_([a.id for a in accounts])
            )
        )
    if run_ids:
        s.execute(delete(ExportRunRecord).where(ExportRunRecord.id.in_(run_ids)))
    removed_profiles = 0
    if with_settings and profile_ids:
        s.flush()
        # un dossier encore utilisé par une source CONNECTÉE (comptes, fichiers) est gardé, réglages vidés :
        # le supprimer emporterait son historique (CASCADE) ; sinon il est retiré
        used = set(
            s.scalars(
                select(ExportAccountAssignmentRecord.profile_id).where(
                    ExportAccountAssignmentRecord.profile_id.in_(profile_ids)
                )
            )
        ) | set(
            s.scalars(select(ExportRunRecord.profile_id).where(ExportRunRecord.profile_id.in_(profile_ids)))
        )
        for profile in s.scalars(select(ExportProfileRecord).where(ExportProfileRecord.id.in_(profile_ids))):
            if profile.id in used:
                profile.values = {}
            else:
                s.delete(profile)
            removed_profiles += 1
    s.flush()
    return DemoReset(
        accounts=len(accounts), documents=len(doc_ids), runs=len(run_ids), profiles=removed_profiles
    )


__all__ = ["DemoReset", "reset_file_exports"]
