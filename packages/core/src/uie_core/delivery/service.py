"""Envoi CONTRÔLÉ d'enregistrements préparés vers un système cible par API (générique).

    sélection humaine + confirmation ──► envoi (request_key) ──► un élément par enregistrement
        PENDING commité ──► rapprochement (création seulement) ──► attempted_at commité ──► écriture
        ──► SENT (lien + ligne D11) | FAILED | UNKNOWN | NEEDS_MATCH | SKIPPED

Règles :
- jamais d'envoi d'un enregistrement BLOCKED, ni d'un enregistrement inchangé depuis son dernier envoi
  (D11 ALREADY_EXPORTED), ni d'un enregistrement dont un envoi précédent a une issue INCONNUE non résolue ;
- un lien existant (envoi réussi ou rapprochement humain) → mise à jour, sinon création ;
- avant une création, la cible est interrogée (lecture) : un enregistrement existant possible → NEEDS_MATCH,
  rien n'est créé tant qu'un humain n'a pas choisi (rapprocher, ou créer quand même) — jamais de fusion ;
- issue inconnue (délai dépassé, passerelle, réseau) → UNKNOWN, jamais retentée d'office ;
- clé refusée / permission manquante / limite de débit → l'envoi s'arrête, les suivants restent non tentés
  (FAILED NOT_ATTEMPTED : sûrs à renvoyer) ;
- reprise d'un envoi interrompu : un élément commencé (`attempted_at`) devient UNKNOWN, un élément jamais
  commencé est envoyé.

Aucun nom de système, de source ni de domaine ici : la portée et les requêtes viennent de l'appelant."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from uie_core.db.base import utcnow
from uie_core.delivery.contract import (
    STOPPING_KINDS,
    RecordSender,
    RemoteRecord,
    SendError,
    SendErrorKind,
)
from uie_core.delivery.records import TargetResourceLink, TargetSendItem, TargetSendRun
from uie_core.exports.documents import (
    DocumentContent,
    DocumentExportLedger,
    DocumentExportState,
    DocumentScope,
    RecordOrigin,
)
from uie_core.registry.service import NotFound, RegistryError


class DeliveryError(RegistryError):
    """Demande d'envoi invalide (→ 400)."""


class DeliveryConflict(Exception):
    """Demande incompatible avec l'état actuel (envoi en cours, clé réutilisée, lien existant) (→ 409)."""


class ItemStatus(StrEnum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    SKIPPED = "SKIPPED"
    NEEDS_MATCH = "NEEDS_MATCH"


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"  # tout ce qui était à envoyer l'a été
    PARTIAL = "PARTIAL"  # une partie seulement
    NOT_SENT = "NOT_SENT"  # rien n'a été écrit


@dataclass(frozen=True)
class DeliveryScope:
    organization_id: uuid.UUID
    source_instance_id: uuid.UUID
    target_instance_id: uuid.UUID
    domain: str
    target_system: str
    target_product: str
    resource: str  # ressource du contrat cible (entité du Registry)

    @property
    def documents(self) -> DocumentScope:
        return DocumentScope(
            organization_id=self.organization_id,
            instance_id=self.source_instance_id,
            domain=self.domain,
            target_system=self.target_system,
            target_product=self.target_product,
            target_instance_id=self.target_instance_id,
        )


class DeliveryCandidate(BaseModel):
    """Enregistrement préparé par l'appelant (domaine) : requête validée + contenu D11."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_ref: str
    blocked: bool
    request: dict[str, Any] | None
    content: DocumentContent
    # refus décidé par l'appelant (règle de son domaine, ex. « modifié depuis l'envoi : accord requis ») :
    # l'enregistrement est SKIPPED avec ce code, jamais envoyé
    skip_reason: str | None = None


class LinkState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    target_id: str
    origin: str


class DeliveryService:
    def __init__(
        self,
        session: Session,
        *,
        user_id: uuid.UUID | None = None,
        commit: Callable[[], None] | None = None,
    ) -> None:
        self.s = session
        self.user_id = user_id
        # commit PAR ÉLÉMENT (API : session.commit) ; tests : flush (transaction annulée en fin de test)
        self.commit = commit or session.flush
        self.ledger = DocumentExportLedger(session)

    # ------------------------------------------------------------------ #
    # Lecture de l'état (sans effet)
    # ------------------------------------------------------------------ #
    def links(self, scope: DeliveryScope, refs: list[str] | None = None) -> dict[str, TargetResourceLink]:
        q = select(TargetResourceLink).where(*self._link_where(scope))
        if refs is not None:
            q = q.where(TargetResourceLink.source_ref.in_(refs))
        return {r.source_ref: r for r in self.s.scalars(q)}

    def open_unknown(self, scope: DeliveryScope) -> set[str]:
        """Références dont un envoi a une issue INCONNUE non résolue (aucun nouvel envoi possible)."""
        rows = self.s.scalars(
            select(TargetSendItem.source_ref)
            .join(TargetSendRun, TargetSendRun.id == TargetSendItem.run_id)
            .where(
                *self._run_where(scope),
                TargetSendItem.status == ItemStatus.UNKNOWN.value,
                TargetSendItem.resolution.is_(None),
            )
        )
        return set(rows)

    def find_run(self, scope: DeliveryScope, request_key: str) -> TargetSendRun | None:
        """Envoi déjà demandé avec cette clé pour cette cible (rejeu, double clic), sinon None."""
        return self.s.scalar(
            select(TargetSendRun).where(
                TargetSendRun.target_instance_id == scope.target_instance_id,
                TargetSendRun.request_key == request_key,
            )
        )

    def runs(self, scope: DeliveryScope, limit: int = 20) -> list[TargetSendRun]:
        return list(
            self.s.scalars(
                select(TargetSendRun)
                .where(*self._run_where(scope))
                .order_by(TargetSendRun.created_at.desc())
                .limit(limit)
            )
        )

    def run(self, scope: DeliveryScope, run_id: uuid.UUID) -> TargetSendRun:
        row = self.s.get(TargetSendRun, run_id)
        if (
            row is None
            or row.source_instance_id != scope.source_instance_id
            or (row.target_instance_id != scope.target_instance_id or row.domain != scope.domain)
        ):
            raise NotFound(f"Envoi {run_id} introuvable")
        return row

    def items(self, run: TargetSendRun) -> list[TargetSendItem]:
        return list(
            self.s.scalars(
                select(TargetSendItem)
                .where(TargetSendItem.run_id == run.id)
                .order_by(TargetSendItem.position)
            )
        )

    # ------------------------------------------------------------------ #
    # Envoi
    # ------------------------------------------------------------------ #
    def send(
        self,
        scope: DeliveryScope,
        candidates: list[DeliveryCandidate],
        *,
        refs: list[str],
        request_key: str,
        create_new: set[str],
        target_label: str,
        sender_factory: Callable[[], RecordSender],
    ) -> TargetSendRun:
        existing = self.s.scalar(
            select(TargetSendRun).where(
                TargetSendRun.target_instance_id == scope.target_instance_id,
                TargetSendRun.request_key == request_key,
            )
        )
        if existing is not None:  # même clé = même envoi : jamais rejoué
            if existing.source_instance_id != scope.source_instance_id or existing.domain != scope.domain:
                raise DeliveryConflict("This request key was already used for another send")
            return existing
        if self._running(scope) is not None:
            raise DeliveryConflict("A send to this system is already in progress")
        selected = list(dict.fromkeys(refs))
        if not selected:
            raise DeliveryError("Select at least one record to send")
        by_ref = {c.source_ref: c for c in candidates}
        unknown_refs = [r for r in selected if r not in by_ref]
        if unknown_refs:
            raise DeliveryError(f"Unknown record(s): {', '.join(unknown_refs[:5])}")

        classes = self.ledger.classify(scope.documents, [by_ref[r].content for r in selected])
        links = self.links(scope, selected)
        unknown = self.open_unknown(scope)
        run = TargetSendRun(
            organization_id=scope.organization_id,
            source_instance_id=scope.source_instance_id,
            target_instance_id=scope.target_instance_id,
            domain=scope.domain,
            resource=scope.resource,
            target_label=target_label[:200],
            request_key=request_key,
            status=RunStatus.RUNNING.value,
            created_by=self.user_id,
        )
        self.s.add(run)
        self.s.flush()
        for pos, ref in enumerate(selected, start=1):
            c = by_ref[ref]
            state = classes[ref].state
            reason = None
            if c.blocked or c.request is None:
                reason = "BLOCKED"
            elif c.skip_reason:
                reason = c.skip_reason[:40]
            elif state == DocumentExportState.ALREADY_EXPORTED:
                reason = "UNCHANGED"
            elif ref in unknown:
                reason = "OUTCOME_UNKNOWN"
            self.s.add(
                TargetSendItem(
                    run_id=run.id,
                    position=pos,
                    source_ref=ref,
                    operation="NONE" if reason else ("UPDATE" if ref in links else "CREATE"),
                    create_new=ref in create_new,
                    status=ItemStatus.SKIPPED.value if reason else ItemStatus.PENDING.value,
                    reason=reason,
                    fingerprint=c.content.fingerprint,
                    field_digests=c.content.field_digests,
                    finished_at=utcnow() if reason else None,
                )
            )
        self.s.flush()
        self.commit()  # l'envoi et ses éléments existent AVANT tout appel au système cible
        return self._execute(scope, run, by_ref, sender_factory)

    def resume(
        self,
        scope: DeliveryScope,
        run_id: uuid.UUID,
        candidates: list[DeliveryCandidate],
        sender_factory: Callable[[], RecordSender],
    ) -> TargetSendRun:
        """Reprise d'un envoi interrompu : un élément commencé devient UNKNOWN (jamais réécrit), un élément
        jamais commencé est envoyé avec la requête préparée ACTUELLE (même contenu exigé, sinon ignoré)."""
        run = self.run(scope, run_id)
        if run.status != RunStatus.RUNNING.value:
            return run
        by_ref = {c.source_ref: c for c in candidates}
        for item in self.items(run):
            if item.status != ItemStatus.PENDING.value:
                continue
            if item.attempted_at is not None:
                self._finish(item, ItemStatus.UNKNOWN, "INTERRUPTED")
            else:
                c = by_ref.get(item.source_ref)
                if c is None or c.blocked or c.content.fingerprint != item.fingerprint:
                    self._finish(item, ItemStatus.SKIPPED, "CHANGED_BEFORE_RESUME")
        self.commit()
        return self._execute(scope, run, by_ref, sender_factory)

    def _execute(
        self,
        scope: DeliveryScope,
        run: TargetSendRun,
        by_ref: dict[str, DeliveryCandidate],
        sender_factory: Callable[[], RecordSender],
    ) -> TargetSendRun:
        pending = [i for i in self.items(run) if i.status == ItemStatus.PENDING.value]
        if pending:
            with sender_factory() as sender:
                stop: SendErrorKind | None = None
                for item in pending:
                    if stop is not None:
                        self._finish(item, ItemStatus.FAILED, "NOT_ATTEMPTED")
                        self.commit()
                        continue
                    stop = self._send_one(scope, sender, item, by_ref[item.source_ref])
                    self.commit()
        statuses = {i.status for i in self.items(run)}
        sent = ItemStatus.SENT.value in statuses
        problems = statuses & {
            ItemStatus.FAILED.value,
            ItemStatus.UNKNOWN.value,
            ItemStatus.NEEDS_MATCH.value,
        }
        if not problems:
            run.status = RunStatus.COMPLETED.value
        else:
            run.status = (RunStatus.PARTIAL if sent else RunStatus.NOT_SENT).value
        run.finished_at = utcnow()
        self.s.flush()
        self.commit()
        return run

    def _send_one(
        self, scope: DeliveryScope, sender: RecordSender, item: TargetSendItem, c: DeliveryCandidate
    ) -> SendErrorKind | None:
        """Un élément ; renvoie le type d'erreur qui ARRÊTE l'envoi, sinon None."""
        assert c.request is not None
        link = self.links(scope, [item.source_ref]).get(item.source_ref)
        item.operation = "UPDATE" if link else "CREATE"
        if link is None and not item.create_new:
            try:
                found = sender.find_candidates(scope.resource, c.request)
            except SendError as exc:
                self._fail(item, exc, status=ItemStatus.FAILED, reason="MATCH_CHECK_FAILED")
                return exc.kind if exc.kind in STOPPING_KINDS else None
            taken = self.linked_target_ids(scope)
            found = [f for f in found if f.id not in taken]  # déjà lié à un autre enregistrement
            if found:
                item.candidates = [f.id for f in found]
                self._finish(item, ItemStatus.NEEDS_MATCH, "POSSIBLE_DUPLICATE")
                return None
        item.attempted_at = utcnow()
        self.s.flush()
        self.commit()  # « commencé » enregistré AVANT l'écriture : une coupure donne UNKNOWN, pas un doublon
        try:
            if link is None:
                remote = sender.create(scope.resource, c.request)
            else:
                remote = sender.update(scope.resource, link.target_id, link.target_version, c.request)
        except SendError as exc:
            unknown = exc.kind == SendErrorKind.UNKNOWN_OUTCOME
            self._fail(
                item, exc, status=ItemStatus.UNKNOWN if unknown else ItemStatus.FAILED, reason=exc.kind
            )
            return exc.kind if exc.kind in STOPPING_KINDS else None
        except Exception:  # noqa: BLE001 — erreur inattendue APRÈS le début : issue inconnue, on s'arrête
            self._finish(item, ItemStatus.UNKNOWN, "UNEXPECTED")
            return SendErrorKind.UNKNOWN_OUTCOME
        self._sent(scope, item, remote, link, c.content, origin="SENT")
        return None

    def _sent(
        self,
        scope: DeliveryScope,
        item: TargetSendItem,
        remote: RemoteRecord,
        link: TargetResourceLink | None,
        content: DocumentContent | None,
        *,
        origin: str,
    ) -> None:
        item.target_id = remote.id
        item.target_version = remote.version
        if link is None:
            link = TargetResourceLink(
                organization_id=scope.organization_id,
                source_instance_id=scope.source_instance_id,
                target_instance_id=scope.target_instance_id,
                domain=scope.domain,
                resource=scope.resource,
                source_ref=item.source_ref,
                target_id=remote.id,
                target_version=remote.version,
                origin=origin,
                decided_by=self.user_id,
            )
            self.s.add(link)
        else:
            link.target_version = remote.version
            link.updated_at = utcnow()
        previous = self.ledger.latest(scope.documents, [item.source_ref]).get(item.source_ref)
        self.ledger.record_sent(
            scope.documents,
            send_item_id=item.id,
            source_ref=item.source_ref,
            fingerprint=item.fingerprint or (content.fingerprint if content else ""),
            field_digests=item.field_digests or [],
            units=1,
            origin=RecordOrigin.UPDATED if item.operation == "UPDATE" else RecordOrigin.EXPORTED,
            previous=previous.id if previous else None,
            decided_by=self.user_id,
        )
        self._finish(item, ItemStatus.SENT, None)

    # ------------------------------------------------------------------ #
    # Décisions humaines
    # ------------------------------------------------------------------ #
    def link(
        self,
        scope: DeliveryScope,
        source_ref: str,
        target_id: str,
        sender_factory: Callable[[], RecordSender],
    ) -> TargetResourceLink:
        """Rapprochement MANUEL : cet enregistrement source EST cet enregistrement cible (vérifié en lecture).
        L'envoi suivant le met à jour au lieu d'en créer un autre. Jamais de fusion."""
        if source_ref in self.links(scope, [source_ref]):
            raise DeliveryConflict("This record is already linked")
        if target_id in self.linked_target_ids(scope):
            raise DeliveryConflict("This target record is already linked to another record")
        if source_ref in self.open_unknown(scope):
            raise DeliveryConflict("Resolve the unknown outcome of the previous send first")
        with sender_factory() as sender:
            try:
                remote = sender.get(scope.resource, target_id)
            except SendError as exc:
                raise DeliveryError(f"The target record could not be read ({exc.kind.value})") from None
        row = TargetResourceLink(
            organization_id=scope.organization_id,
            source_instance_id=scope.source_instance_id,
            target_instance_id=scope.target_instance_id,
            domain=scope.domain,
            resource=scope.resource,
            source_ref=source_ref,
            target_id=remote.id,
            target_version=remote.version,
            origin="MATCHED",
            decided_by=self.user_id,
        )
        self.s.add(row)
        self.s.flush()
        return row

    def unlink(self, scope: DeliveryScope, source_ref: str) -> None:
        """Retire un rapprochement MANUEL encore jamais utilisé par un envoi (correction d'une erreur)."""
        link = self.links(scope, [source_ref]).get(source_ref)
        if link is None:
            raise NotFound("No link for this record")
        if link.origin != "MATCHED" or self.ledger.latest(scope.documents, [source_ref]):
            raise DeliveryConflict("Only a manual link that was never used by a send can be removed")
        self.s.delete(link)
        self.s.flush()

    def resolve_unknown(
        self,
        scope: DeliveryScope,
        item_id: uuid.UUID,
        *,
        created: bool,
        target_id: str | None,
        sender_factory: Callable[[], RecordSender],
    ) -> TargetSendItem:
        """Un humain a vérifié dans la cible : enregistrement créé (lien + D11) ou non (renvoyable)."""
        item = self.s.get(TargetSendItem, item_id)
        if item is None:
            raise NotFound(f"Élément {item_id} introuvable")
        self.run(scope, item.run_id)  # même portée, sinon NotFound
        if item.status != ItemStatus.UNKNOWN.value or item.resolution is not None:
            raise DeliveryConflict("Only an unresolved unknown outcome can be resolved")
        if created:
            if not target_id:
                raise DeliveryError("Choose the record that was created in the target system")
            if target_id in self.linked_target_ids(scope):
                raise DeliveryConflict("This target record is already linked to another record")
            with sender_factory() as sender:
                try:
                    remote = sender.get(scope.resource, target_id)
                except SendError as exc:
                    raise DeliveryError(f"The target record could not be read ({exc.kind.value})") from None
            link = self.links(scope, [item.source_ref]).get(item.source_ref)
            if link is not None and link.target_id != remote.id:
                raise DeliveryConflict("This record is already linked to another target record")
            reason = item.reason
            self._sent(scope, item, remote, link, None, origin="SENT")
            item.status, item.reason = ItemStatus.UNKNOWN.value, reason  # l'issue inconnue reste visible
            item.resolution = "CREATED"
        else:
            item.resolution = "NOT_CREATED"
        item.resolved_by = self.user_id
        item.resolved_at = utcnow()
        self.s.flush()
        return item

    # ------------------------------------------------------------------ #
    def _running(self, scope: DeliveryScope) -> TargetSendRun | None:
        return self.s.scalar(
            select(TargetSendRun).where(
                *self._run_where(scope), TargetSendRun.status == RunStatus.RUNNING.value
            )
        )

    def linked_target_ids(self, scope: DeliveryScope) -> set[str]:
        return set(self.s.scalars(select(TargetResourceLink.target_id).where(*self._link_where(scope))))

    @staticmethod
    def _link_where(scope: DeliveryScope) -> list:
        r = TargetResourceLink
        return [
            r.source_instance_id == scope.source_instance_id,
            r.target_instance_id == scope.target_instance_id,
            r.domain == scope.domain,
        ]

    @staticmethod
    def _run_where(scope: DeliveryScope) -> list:
        r = TargetSendRun
        return [
            r.source_instance_id == scope.source_instance_id,
            r.target_instance_id == scope.target_instance_id,
            r.domain == scope.domain,
        ]

    def _finish(self, item: TargetSendItem, status: ItemStatus, reason: str | None) -> None:
        item.status = status.value
        item.reason = reason
        item.finished_at = utcnow()
        self.s.flush()

    def _fail(self, item: TargetSendItem, exc: SendError, *, status: ItemStatus, reason: str) -> None:
        item.error_status = exc.status
        item.error_trace_id = (exc.trace_id or "")[:120] or None
        item.error_fields = list(exc.fields) or None
        self._finish(item, status, reason)
