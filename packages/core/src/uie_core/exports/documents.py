"""Idempotence d'export au niveau du DOCUMENT (générique : tout domaine, tout format cible).

Un document source déjà écrit dans un fichier généré ne doit pas repartir par accident. Pour chaque
document exportable, le registre compare l'empreinte de son contenu cible ACTUEL à celle de son dernier
export :

    aucun export précédent              → NEW                    (exporté)
    même empreinte                      → ALREADY_EXPORTED       (non réexporté par défaut)
    empreinte différente                → CHANGED_SINCE_EXPORT   (bloqué tant qu'un humain ne l'autorise pas)

Identité d'un document = organisation + système source + domaine + format cible + référence source
OPAQUE : ni le numéro lisible, ni un autre système, ni un autre format ne peuvent entrer en collision.
Empreinte = contenu cible canonique fourni par l'appelant (une liste d'« unités » : champ → texte tel
qu'il serait écrit) ; rien de volatil (horodatage, nom de fichier, identifiant de génération) n'y entre.
L'historique est immuable : chaque export ajoute une ligne, jamais de mise à jour. Aucun nom de système
ni de format dans ce module.

Envoi par API : la portée inclut l'INSTANCE cible (`target_instance_id`) — deux comptes du même système
cible ont chacun leur historique. Un fichier n'a pas d'instance cible (NULL) : son comportement est inchangé.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from uie_core.exports.records import ExportDocumentRecord, ExportDocumentReleaseRecord, ExportRunRecord


class DocumentExportState(StrEnum):
    NEW = "NEW"
    ALREADY_EXPORTED = "ALREADY_EXPORTED"
    CHANGED_SINCE_EXPORT = "CHANGED_SINCE_EXPORT"


class RecordOrigin(StrEnum):
    EXPORTED = "EXPORTED"  # premier export
    RELEASED = "RELEASED"  # changement autorisé par un humain
    RE_EXPORTED = "RE_EXPORTED"  # doublon volontaire (document inchangé)
    UPDATED = "UPDATED"  # envoi par API : mise à jour décidée par un humain (contenu modifié ou rapproché)
    ADOPTED = "ADOPTED"  # fichier généré avant le suivi, prouvé identique


@dataclass(frozen=True)
class DocumentScope:
    """Portée d'un registre : deux portées différentes ne se voient jamais."""

    organization_id: uuid.UUID
    instance_id: uuid.UUID | None  # None : source FICHIER (références préfixées, aucune instance)
    domain: str
    target_system: str
    target_product: str
    target_instance_id: uuid.UUID | None = None  # envoi par API ; None = fichier (aucune instance cible)
    client_key: str | None = None  # dossier (mandant) d'une source fichier ; None : source connectée


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DocumentContent(_Model):
    """Contenu cible canonique d'un document : une unité par enregistrement cible, champ → texte écrit."""

    source_ref: str
    number: str | None = None
    units: list[dict[str, str]]

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.units)

    @property
    def field_digests(self) -> list[dict[str, str]]:
        return [{k: _digest(v) for k, v in sorted(u.items())} for u in self.units]


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def fingerprint(units: list[dict[str, str]]) -> str:
    """Empreinte déterministe : JSON canonique (clés triées, ordre des unités conservé), SHA-256."""
    canonical = json.dumps([sorted(u.items()) for u in units], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class FieldChange(_Model):
    unit: int  # rang de l'enregistrement cible (1..n)
    field: str  # champ du format
    change: str  # CHANGED | ADDED | REMOVED


class DocumentClassification(_Model):
    source_ref: str
    state: DocumentExportState
    fingerprint: str
    released: bool = False  # CHANGED autorisé pour l'empreinte actuelle
    previous_record_id: uuid.UUID | None = None
    previous_exported_at: datetime | None = None
    previous_origin: str | None = None
    previous_run_id: uuid.UUID | None = None
    previous_units: int | None = None
    changes: list[FieldChange] = []  # CHANGED : champs différents (jamais les valeurs)
    legacy_exported_at: datetime | None = None  # présent dans un fichier d'AVANT le suivi (indice seulement)
    legacy_run_id: uuid.UUID | None = None


class ExportHistoryError(Exception):
    """Décision impossible sur l'historique des exports (→ 409 / 400 selon l'appelant)."""


class DocumentExportLedger:
    def __init__(self, session: Session) -> None:
        self.s = session

    def _where(self, scope: DocumentScope) -> list:
        r = ExportDocumentRecord
        return [
            r.organization_id == scope.organization_id,
            r.instance_id == scope.instance_id,
            r.domain == scope.domain,
            r.target_system == scope.target_system,
            r.target_product == scope.target_product,
            r.target_instance_id.is_(None)
            if scope.target_instance_id is None
            else r.target_instance_id == scope.target_instance_id,
            r.client_key.is_(None) if scope.client_key is None else r.client_key == scope.client_key,
        ]

    def latest(self, scope: DocumentScope, refs: list[str]) -> dict[str, ExportDocumentRecord]:
        """Dernier export de chaque document (le plus récent)."""
        if not refs:
            return {}
        rows = self.s.scalars(
            select(ExportDocumentRecord)
            .where(*self._where(scope), ExportDocumentRecord.source_ref.in_(refs))
            .order_by(ExportDocumentRecord.created_at, ExportDocumentRecord.id)
        )
        return {r.source_ref: r for r in rows}

    def classify(
        self,
        scope: DocumentScope,
        documents: list[DocumentContent],
        legacy: dict[str, tuple[uuid.UUID, datetime]] | None = None,
    ) -> dict[str, DocumentClassification]:
        """État de chaque document. `legacy` : référence → (génération, date) d'un fichier généré avant le
        suivi, simple indice affiché (le document reste NEW tant qu'il n'est pas adopté explicitement)."""
        latest = self.latest(scope, [d.source_ref for d in documents])
        released = self._released([r.id for r in latest.values()])
        out = {}
        for d in documents:
            fp = d.fingerprint
            prev = latest.get(d.source_ref)
            if prev is None:
                hint = (legacy or {}).get(d.source_ref)
                out[d.source_ref] = DocumentClassification(
                    source_ref=d.source_ref,
                    state=DocumentExportState.NEW,
                    fingerprint=fp,
                    legacy_run_id=hint[0] if hint else None,
                    legacy_exported_at=hint[1] if hint else None,
                )
                continue
            same = prev.fingerprint == fp
            out[d.source_ref] = DocumentClassification(
                source_ref=d.source_ref,
                state=DocumentExportState.ALREADY_EXPORTED
                if same
                else DocumentExportState.CHANGED_SINCE_EXPORT,
                fingerprint=fp,
                released=not same and (prev.id, fp) in released,
                previous_record_id=prev.id,
                previous_exported_at=prev.created_at,
                previous_origin=prev.origin,
                previous_run_id=prev.export_run_id,
                previous_units=prev.units,
                changes=[] if same else _changes(prev.field_digests, d.field_digests),
            )
        return out

    def _released(self, record_ids: list[uuid.UUID]) -> set[tuple[uuid.UUID, str]]:
        if not record_ids:
            return set()
        rows = self.s.scalars(
            select(ExportDocumentReleaseRecord).where(
                ExportDocumentReleaseRecord.previous_record_id.in_(record_ids)
            )
        )
        return {(r.previous_record_id, r.fingerprint) for r in rows}

    def release(
        self,
        scope: DocumentScope,
        document: DocumentContent,
        *,
        decided_by: uuid.UUID | None,
        note: str | None = None,
    ) -> ExportDocumentReleaseRecord:
        """Autorise explicitement le nouvel export d'un document MODIFIÉ, pour son contenu actuel."""
        c = self.classify(scope, [document])[document.source_ref]
        if c.state != DocumentExportState.CHANGED_SINCE_EXPORT or c.previous_record_id is None:
            raise ExportHistoryError("Only a document changed since its export can be released")
        existing = self.s.scalar(
            select(ExportDocumentReleaseRecord).where(
                ExportDocumentReleaseRecord.previous_record_id == c.previous_record_id,
                ExportDocumentReleaseRecord.fingerprint == c.fingerprint,
            )
        )
        if existing is not None:
            return existing
        row = ExportDocumentReleaseRecord(
            organization_id=scope.organization_id,
            previous_record_id=c.previous_record_id,
            fingerprint=c.fingerprint,
            decided_by=decided_by,
            note=(note or "")[:300] or None,
        )
        self.s.add(row)
        self.s.flush()
        return row

    def revoke(self, scope: DocumentScope, document: DocumentContent) -> bool:
        """Retire une autorisation non encore utilisée : le document reste bloqué."""
        c = self.classify(scope, [document])[document.source_ref]
        if not c.released or c.previous_record_id is None:
            return False
        row = self.s.scalar(
            select(ExportDocumentReleaseRecord).where(
                ExportDocumentReleaseRecord.previous_record_id == c.previous_record_id,
                ExportDocumentReleaseRecord.fingerprint == c.fingerprint,
            )
        )
        if row is None:
            return False
        self.s.delete(row)
        self.s.flush()
        return True

    def record(
        self,
        scope: DocumentScope,
        run: ExportRunRecord,
        documents: list[tuple[DocumentContent, RecordOrigin, uuid.UUID | None]],
        *,
        scope_ref: str | None = None,
        decided_by: uuid.UUID | None = None,
    ) -> list[ExportDocumentRecord]:
        """Ajoute une ligne par document écrit dans le fichier de `run` (jamais de mise à jour)."""
        rows = []
        for content, origin, previous in documents:
            row = ExportDocumentRecord(
                organization_id=scope.organization_id,
                instance_id=scope.instance_id,
                domain=scope.domain,
                target_system=scope.target_system,
                target_product=scope.target_product,
                scope_ref=scope_ref,
                client_key=scope.client_key,
                source_ref=content.source_ref,
                source_number=content.number,
                fingerprint=content.fingerprint,
                field_digests=content.field_digests,
                units=len(content.units),
                origin=origin.value,
                previous_record_id=previous,
                export_run_id=run.id,
                decided_by=decided_by,
            )
            self.s.add(row)
            rows.append(row)
        self.s.flush()
        return rows

    def record_sent(
        self,
        scope: DocumentScope,
        *,
        send_item_id: uuid.UUID,
        source_ref: str,
        fingerprint: str,
        field_digests: list[dict[str, str]],
        units: int,
        origin: RecordOrigin,
        previous: uuid.UUID | None,
        decided_by: uuid.UUID | None = None,
    ) -> ExportDocumentRecord:
        """Ligne d'un document ENVOYÉ par API (preuve : l'élément d'envoi) ; jamais de mise à jour."""
        if scope.target_instance_id is None:
            raise ExportHistoryError("An API send needs its target instance")
        row = ExportDocumentRecord(
            organization_id=scope.organization_id,
            instance_id=scope.instance_id,
            domain=scope.domain,
            target_system=scope.target_system,
            target_product=scope.target_product,
            target_instance_id=scope.target_instance_id,
            source_ref=source_ref,
            fingerprint=fingerprint,
            field_digests=field_digests,
            units=units,
            origin=origin.value,
            previous_record_id=previous,
            send_item_id=send_item_id,
            decided_by=decided_by,
        )
        self.s.add(row)
        self.s.flush()
        return row

    def recorded_runs(self, run_ids: list[uuid.UUID]) -> set[uuid.UUID]:
        """Générations qui ont des lignes de documents (les autres datent d'avant le suivi)."""
        if not run_ids:
            return set()
        return set(
            self.s.scalars(
                select(ExportDocumentRecord.export_run_id)
                .where(ExportDocumentRecord.export_run_id.in_(run_ids))
                .distinct()
            )
        )


def _changes(previous: list[dict[str, str]], current: list[dict[str, str]]) -> list[FieldChange]:
    """Champs différents entre l'export précédent et le contenu actuel (par empreinte de valeur)."""
    out: list[FieldChange] = []
    for n in range(max(len(previous), len(current))):
        before = previous[n] if n < len(previous) else {}
        after = current[n] if n < len(current) else {}
        for field in sorted(set(before) | set(after)):
            if field not in before:
                out.append(FieldChange(unit=n + 1, field=field, change="ADDED"))
            elif field not in after:
                out.append(FieldChange(unit=n + 1, field=field, change="REMOVED"))
            elif before[field] != after[field]:
                out.append(FieldChange(unit=n + 1, field=field, change="CHANGED"))
    return out
