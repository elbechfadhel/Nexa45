"""Préparation et ENVOI de partenaires vers une cible PAR API (une requête par partenaire).

    n'importe quelle source ──► Partner CDM ──► binding cible (données) résolu contre le Registry
        ──► requête canonique validée ──► READY / WARNING / BLOCKED + état d'export ──► aperçu

Générique dans les deux sens : la SOURCE n'est connue que par le Partner CDM (jamais par son système), la
CIBLE par un binding enregistré (`delivery: api`) et le contrat du Registry. Aucun nom de système ici.

`prepare_partners` est PURE (ni base, ni réseau, ni instance) : c'est elle qui prouve que la préparation ne
dépend d'aucune source. `PartnerTargetService` ne fait que relier une instance source (lecture du CDM) à une
instance cible (son système désigne le binding et le contrat). L'APERÇU n'envoie rien et n'enregistre rien.

ENVOI (phase 1b) : confié au service générique `uie_core.delivery` (liens source ↔ cible, envois suivis,
historique D11 PAR INSTANCE CIBLE) ; ce module ne fait que lui fournir les requêtes préparées et la portée.
Toujours une sélection humaine + une confirmation explicite ; jamais un enregistrement bloqué."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session
from uie_cdm import Partner

# sous-modules (pas le paquet) : `uie_core.delivery` charge `uie_core.exports`, qui charge ce module
from uie_core.delivery.contract import (
    RecordSender,
    RecordSenderError,
    RecordSenderResolver,
    RemoteRecord,
    SendError,
)
from uie_core.delivery.records import TargetResourceLink, TargetSendItem, TargetSendRun
from uie_core.delivery.service import DeliveryCandidate, DeliveryScope, DeliveryService
from uie_core.exports.documents import (
    DocumentContent,
    DocumentExportLedger,
    DocumentExportState,
)
from uie_core.identity.context import AdminContext, Context, TenantContext, ensure_context
from uie_core.mapping import (
    PlainValueFormat,
    RecordFileWriterError,
    RecordRequestBuilder,
    RecordRequestBuilderRegistry,
    RecordStatus,
    ResolvedBinding,
    TargetBinding,
    TargetBindingError,
    TargetBindingRegistry,
    TargetIssue,
    ValueFormat,
    map_record,
    resolve_binding,
)
from uie_core.partners.service import PartnerError, PartnerReadService
from uie_core.registry import models as m
from uie_core.registry.service import NotFound, RegistryService

SOURCE_MODEL = "partner"
DOMAIN = "partner"
DELIVERY = "api"
ENCODING = "utf-8"  # une requête d'API n'a pas de contrainte d'encodage de fichier


class PartnerTargetError(PartnerError):
    """Préparation impossible (→ 400)."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PreparedValue(_Model):
    field: str  # champ du contrat cible
    label: str  # libellé du Registry
    value: Any


class PreparedPartner(_Model):
    source_ref: str  # référence OPAQUE de l'enregistrement dans SA source (jamais supposée d'un système)
    name: str
    kind: str
    roles: list[str]
    status: RecordStatus
    issues: list[TargetIssue]
    values: list[PreparedValue]
    request: dict[str, Any] | None  # requête canonique (None : enregistrement bloqué)
    content: DocumentContent  # contenu cible pour l'empreinte (champ → texte), sans rien de volatil


def prepare_partners(
    partners: list[Partner],
    resolved: ResolvedBinding,
    builder: RecordRequestBuilder,
    fmt: ValueFormat | None = None,
) -> list[PreparedPartner]:
    """Partner CDM → requêtes canoniques de la cible. PURE : mêmes partenaires + même contrat = même
    résultat, quelle que soit la source qui a rempli le CDM."""
    fmt = fmt or PlainValueFormat()
    out = []
    for p in partners:
        rec = map_record(p.mapping_view(), resolved, fmt, ENCODING, source_ref=p.source_ref)
        fields = {f.technical_name: f for f in resolved.fields}
        ordered = [f for f in resolved.fields if f.technical_name in rec.values]
        blocked = rec.status == RecordStatus.BLOCKED
        out.append(
            PreparedPartner(
                source_ref=p.source_ref,
                name=p.name,
                kind=p.kind.value,
                roles=[r.value for r in p.roles],
                status=rec.status,
                issues=list(rec.issues),
                values=[
                    PreparedValue(
                        field=f.technical_name,
                        label=f.label or f.technical_name,
                        value=rec.values[f.technical_name],
                    )
                    for f in ordered
                ],
                request=None if blocked else builder.build(resolved.fields, rec.values),
                content=DocumentContent(
                    source_ref=p.source_ref,
                    number=None,
                    units=[{k: fmt.format_cell(fields[k], v) for k, v in sorted(rec.values.items())}],
                ),
            )
        )
    return out


class TargetOption(_Model):
    """Système connecté de l'organisation qui peut recevoir des partenaires par son API."""

    instance_id: uuid.UUID
    name: str
    system: str  # nom du système (Registry)
    target: str  # nom du binding (ce que les partenaires deviennent dans la cible)
    connected: bool


class PreviewPartner(_Model):
    prepared: PreparedPartner
    export_state: DocumentExportState
    changed_fields: list[str] = []  # libellés des champs modifiés depuis le dernier envoi
    source_issues: list[str] = []  # problèmes de lecture de la source pour ce partenaire
    linked: bool = False  # lié à un enregistrement de la cible : un envoi le met à jour
    linked_by_match: bool = False  # lien posé par un rapprochement humain (pas par un envoi)
    outcome_unknown: bool = False  # un envoi précédent a une issue inconnue non résolue

    @property
    def sendable(self) -> bool:
        return (
            self.prepared.status != RecordStatus.BLOCKED
            and self.export_state != DocumentExportState.ALREADY_EXPORTED
            and not self.outcome_unknown
        )


class PartnerTargetPreview(_Model):
    source_name: str
    source_system: str
    target_name: str
    target_system: str
    target: str  # nom du binding
    contract: str  # snapshot du Registry qui a validé les requêtes
    partners_read: int
    truncated: bool
    records: list[PreviewPartner]

    def count(self, status: RecordStatus) -> int:
        return sum(1 for r in self.records if r.prepared.status == status)

    def count_state(self, state: DocumentExportState) -> int:
        return sum(1 for r in self.records if r.export_state == state)


def _system(inst: m.Instance) -> tuple[m.System, m.SystemProduct, m.SystemVersion]:
    ver = inst.interface.version
    return ver.product.system, ver.product, ver


class SendView(_Model):
    """Un envoi et ses éléments (aucune donnée métier stockée : les noms viennent de la lecture actuelle)."""

    run: Any
    items: list[Any]
    names: dict[str, str]  # référence source → nom actuel (lecture de la source), affichage seulement
    labels: dict[str, str] = {}  # champ du contrat cible → libellé du Registry (messages d'erreur)


class PartnerTargetService:
    def __init__(
        self,
        session: Session,
        ctx: Context,
        partners: PartnerReadService,
        senders: RecordSenderResolver | None = None,
        commit: Callable[[], None] | None = None,
    ) -> None:
        self.s = session
        self.ctx = ensure_context(ctx)
        if partners.ctx != self.ctx:
            raise ValueError("Les services de préparation doivent partager le même contexte")
        self.partners = partners
        self.instances = partners.instances
        self.registry = RegistryService(session, self.ctx)
        self.ledger = DocumentExportLedger(session)
        self.senders = senders or RecordSenderResolver()
        user = self.ctx.user_id if isinstance(self.ctx, TenantContext | AdminContext) else None
        self.delivery = DeliveryService(session, user_id=user, commit=commit)

    # ------------------------------------------------------------------ #
    def _binding(self, inst: m.Instance) -> TargetBinding | None:
        system, product, _ = _system(inst)
        try:
            binding = TargetBindingRegistry.get(SOURCE_MODEL, system.code, product.code)
        except TargetBindingError:
            return None
        return binding if binding.delivery == DELIVERY else None

    def targets(self, source_id: uuid.UUID) -> list[TargetOption]:
        """Systèmes de la MÊME organisation que la source qui peuvent recevoir des partenaires."""
        source = self.instances.get(source_id)  # contrôle d'appartenance
        out = []
        for inst in self.instances.list():
            if inst.id == source.id or inst.organization_id != source.organization_id:
                continue
            binding = self._binding(inst)
            if binding is None:
                continue
            conn = inst.connections[0] if inst.connections else None
            out.append(
                TargetOption(
                    instance_id=inst.id,
                    name=inst.name,
                    system=_system(inst)[0].name,
                    target=binding.name,
                    connected=bool(conn and conn.last_test_status == "OK"),
                )
            )
        return out

    def resolve(self, target_id: uuid.UUID) -> tuple[m.Instance, ResolvedBinding, RecordRequestBuilder]:
        """Instance cible, binding résolu contre le contrat de SA version, constructeur de requête."""
        target = self.instances.get(target_id)
        binding = self._binding(target)
        if binding is None:
            raise NotFound(f"Le système {target.name} ne reçoit pas de partenaires")
        system, _, version = _system(target)
        try:
            resolved = resolve_binding(self.registry, binding, version.version)
            builder = RecordRequestBuilderRegistry.get(system.code, target.interface.code)
        except (TargetBindingError, RecordFileWriterError) as exc:
            raise PartnerTargetError(str(exc)) from None
        return target, resolved, builder

    def _pair(
        self, source_id: uuid.UUID, target_id: uuid.UUID
    ) -> tuple[m.Instance, m.Instance, ResolvedBinding, RecordRequestBuilder, DeliveryScope]:
        """Source et cible de la MÊME organisation (contrôles d'appartenance AVANT toute lecture)."""
        source = self.instances.get(source_id)
        target, resolved, builder = self.resolve(target_id)
        if source.id == target.id:
            raise PartnerTargetError("Choose two different systems")
        if source.organization_id != target.organization_id:
            raise NotFound(f"Instance {target_id} introuvable")
        t = resolved.binding.target
        scope = DeliveryScope(
            organization_id=source.organization_id,
            source_instance_id=source.id,
            target_instance_id=target.id,
            domain=DOMAIN,
            target_system=t.system,
            target_product=t.product,
            resource=t.entity,
        )
        return source, target, resolved, builder, scope

    def preview(self, source_id: uuid.UUID, target_id: uuid.UUID) -> PartnerTargetPreview:
        """Aperçu SANS EFFET : lit le Partner CDM de la source, prépare les requêtes, classe chaque
        partenaire d'après l'historique de CETTE cible. N'appelle jamais la cible, n'enregistre rien."""
        source, target, resolved, builder, scope = self._pair(source_id, target_id)
        result = self.partners.read(source.id)
        prepared = prepare_partners(result.partners, resolved, builder)
        classes = self.ledger.classify(scope.documents, [p.content for p in prepared])
        links = self.delivery.links(scope)
        unknown = self.delivery.open_unknown(scope)
        labels = {f.technical_name: f.label or f.technical_name for f in resolved.fields}
        by_ref: dict[str, list[str]] = {}
        for issue in result.issues:
            by_ref.setdefault(issue.source_ref, []).append(issue.message)
        records = [
            PreviewPartner(
                prepared=p,
                export_state=classes[p.source_ref].state,
                changed_fields=sorted({labels.get(c.field, c.field) for c in classes[p.source_ref].changes}),
                source_issues=by_ref.get(p.source_ref, []),
                linked=p.source_ref in links,
                linked_by_match=p.source_ref in links and links[p.source_ref].origin == "MATCHED",
                outcome_unknown=p.source_ref in unknown,
            )
            for p in prepared
        ]
        return PartnerTargetPreview(
            source_name=source.name,
            source_system=_system(source)[0].name,
            target_name=target.name,
            target_system=_system(target)[0].name,
            target=resolved.binding.name,
            contract=resolved.snapshot_label,
            partners_read=len(result.partners),
            truncated=result.truncated,
            records=records,
        )

    # ------------------------------------------------------------------ #
    # Envoi (phase 1b) : toujours une sélection humaine et une confirmation explicite
    # ------------------------------------------------------------------ #
    def _candidates(
        self, source_id: uuid.UUID, target_id: uuid.UUID
    ) -> tuple[DeliveryScope, ResolvedBinding, m.Instance, list[DeliveryCandidate], dict[str, str]]:
        source, target, resolved, builder, scope = self._pair(source_id, target_id)
        result = self.partners.read(source.id)
        prepared = prepare_partners(result.partners, resolved, builder)
        candidates = [
            DeliveryCandidate(
                source_ref=p.source_ref,
                blocked=p.status == RecordStatus.BLOCKED,
                request=p.request,
                content=p.content,
            )
            for p in prepared
        ]
        return scope, resolved, target, candidates, {p.source_ref: p.name for p in prepared}

    def _sender_factory(self, target: m.Instance) -> Callable[[], RecordSender]:
        system, _, _ = _system(target)
        interface = target.interface.code
        if not self.senders.supports(system.code, interface):
            raise PartnerTargetError(f"Sending to {system.name} is not available")

        def factory() -> RecordSender:
            _, ctx = self.instances.connection_context(target.id)  # secret déchiffré au dernier moment
            try:
                return self.senders.create(system.code, interface, ctx)
            except (RecordSenderError, SendError) as exc:
                raise PartnerTargetError(str(exc)) from None

        return factory

    def send(
        self,
        source_id: uuid.UUID,
        target_id: uuid.UUID,
        *,
        refs: list[str],
        request_key: str,
        confirm: bool,
        create_new: list[str] | None = None,
    ) -> SendView:
        """Envoie les partenaires CHOISIS (jamais bloqués, jamais inchangés). `confirm` explicite exigé ;
        même `request_key` = même envoi, jamais rejoué."""
        if not confirm:
            raise PartnerTargetError("Confirm the send first")
        scope, resolved, target, candidates, names = self._candidates(source_id, target_id)
        run = self.delivery.send(
            scope,
            candidates,
            refs=refs,
            request_key=request_key,
            create_new=set(create_new or []),
            target_label=f"{resolved.binding.name} ({resolved.snapshot_label})",
            sender_factory=self._sender_factory(target),
        )
        return self._view(run, names, resolved)

    def resume(self, source_id: uuid.UUID, target_id: uuid.UUID, run_id: uuid.UUID) -> SendView:
        scope, resolved, target, candidates, names = self._candidates(source_id, target_id)
        run = self.delivery.resume(scope, run_id, candidates, self._sender_factory(target))
        return self._view(run, names, resolved)

    def runs(self, source_id: uuid.UUID, target_id: uuid.UUID) -> list[TargetSendRun]:
        *_, scope = self._pair(source_id, target_id)
        return self.delivery.runs(scope)

    def run(self, source_id: uuid.UUID, target_id: uuid.UUID, run_id: uuid.UUID) -> SendView:
        scope, resolved, _, _, names = self._candidates(source_id, target_id)
        return self._view(self.delivery.run(scope, run_id), names, resolved)

    def matches(self, source_id: uuid.UUID, target_id: uuid.UUID, source_ref: str) -> list[RemoteRecord]:
        """Enregistrements EXISTANTS de la cible qui pourraient être ce partenaire (lecture seule)."""
        scope, _, target, candidates, _ = self._candidates(source_id, target_id)
        c = next((x for x in candidates if x.source_ref == source_ref), None)
        if c is None or c.request is None:
            raise PartnerTargetError("This record cannot be matched (unknown or blocked)")
        with self._sender_factory(target)() as sender:
            try:
                found = sender.find_candidates(scope.resource, c.request)
            except SendError as exc:
                raise PartnerTargetError(
                    f"The target system could not be searched ({exc.kind.value})"
                ) from None
        taken = self.delivery.linked_target_ids(scope)  # déjà lié à un autre partenaire : jamais proposé
        return [f for f in found if f.id not in taken]

    def link(
        self, source_id: uuid.UUID, target_id: uuid.UUID, source_ref: str, remote_id: str
    ) -> TargetResourceLink:
        scope, _, target, candidates, _ = self._candidates(source_id, target_id)
        if source_ref not in {c.source_ref for c in candidates}:
            raise PartnerTargetError("Unknown record")
        return self.delivery.link(scope, source_ref, remote_id, self._sender_factory(target))

    def unlink(self, source_id: uuid.UUID, target_id: uuid.UUID, source_ref: str) -> None:
        *_, scope = self._pair(source_id, target_id)
        self.delivery.unlink(scope, source_ref)

    def resolve_unknown(
        self,
        source_id: uuid.UUID,
        target_id: uuid.UUID,
        item_id: uuid.UUID,
        *,
        created: bool,
        remote_id: str | None,
    ) -> TargetSendItem:
        source, target, _, _, scope = self._pair(source_id, target_id)
        return self.delivery.resolve_unknown(
            scope, item_id, created=created, target_id=remote_id, sender_factory=self._sender_factory(target)
        )

    def _view(self, run: TargetSendRun, names: dict[str, str], resolved: ResolvedBinding) -> SendView:
        labels = {f.technical_name: f.label or f.technical_name for f in resolved.fields}
        return SendView(run=run, items=self.delivery.items(run), names=names, labels=labels)
