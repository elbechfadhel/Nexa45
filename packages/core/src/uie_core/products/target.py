"""Préparation de produits pour une cible PAR API (une requête par unité vendable) — APERÇU seulement.

    n'importe quelle source ──► Product CDM ──► unités vendables ──► binding cible (données) résolu contre le
        Registry ──► requête canonique validée ──► READY / WARNING / BLOCKED + état d'export ──► aperçu

Générique dans les deux sens : la SOURCE n'est connue que par le Product CDM, la CIBLE par un binding
enregistré (`delivery: api`) et le contrat du Registry. Aucun nom de système ici.

Ce qui ne se déduit jamais et se DÉCIDE explicitement (table `export_value_mappings`, portée organisation ×
format cible × instance source) :
- la base des prix de la source (nets ou bruts) : réglage `PRICE_BASIS`, sans valeur par défaut ;
- le taux de chaque taxe de vente de la source : correspondance `TAX_RATE`, valeur parmi celles admises par le
  Registry, jamais suggérée. Produit sans taxe, avec plusieurs taxes ou une taxe sans décision : BLOQUÉ.

`prepare_products` est PURE (ni base, ni réseau). L'APERÇU n'appelle jamais la cible, ne déchiffre pas sa clé
et n'enregistre rien ; seules les décisions explicites (réglage, taux) sont écrites.

ENVOI (phase 1b) : mécanisme générique `DeliveryService`, comme les partenaires — sélection humaine ET
confirmation explicite, création (POST) ou mise à jour avec contrôle de version (PUT) d'après le lien
enregistré, doublon possible (même numéro d'article) → décision humaine, jamais de fusion ; D11 (déjà envoyé
et inchangé = jamais renvoyé) ; issue inconnue = jamais retentée d'office."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session
from uie_cdm import ProductItem

from uie_core.delivery.contract import (
    RecordSender,
    RecordSenderError,
    RecordSenderResolver,
    RemoteRecord,
    SendError,
)
from uie_core.delivery.records import TargetResourceLink, TargetSendItem, TargetSendRun
from uie_core.delivery.service import DeliveryCandidate, DeliveryScope, DeliveryService
from uie_core.exports.documents import DocumentContent, DocumentExportLedger, DocumentExportState
from uie_core.exports.records import ExportValueMappingRecord
from uie_core.identity.context import AdminContext, TenantContext
from uie_core.mapping import (
    IssueLevel,
    LookupKind,
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
    TargetRecord,
    ValueFormat,
    map_record,
    resolve_binding,
)
from uie_core.products.service import ProductError, ProductReadService
from uie_core.registry import models as m
from uie_core.registry.service import NotFound, RegistryService

SOURCE_MODEL = "product"
DOMAIN = "product"
DELIVERY = "api"
ENCODING = "utf-8"  # une requête d'API n'a pas de contrainte d'encodage de fichier
PRICE_BASIS = "PRICE_BASIS"  # sorte du réglage dans export_value_mappings
PRICE_BASIS_REF = "price"
BASES = ("NET", "GROSS")
# valeurs fournies à la vue du CDM (`target.<nom>`) : lues par le binding, jamais par le Core
NET, GROSS = "net_price", "gross_price"


class ProductTargetError(ProductError):
    """Préparation impossible ou décision invalide (→ 400)."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PreparedValue(_Model):
    field: str  # champ du contrat cible
    label: str  # libellé du Registry
    value: Any


class PreparedProduct(_Model):
    source_ref: str  # référence OPAQUE de l'unité vendable dans SA source
    title: str
    sku: str | None
    kind: str
    status: RecordStatus
    issues: list[TargetIssue]
    values: list[PreparedValue]
    request: dict[str, Any] | None  # requête canonique (None : bloqué)
    content: DocumentContent  # contenu cible pour l'empreinte (champ → texte), sans rien de volatil


class _Blocks:
    """Problèmes BLOQUANTS d'une unité posés par la préparation (et les champs qu'ils couvrent)."""

    def __init__(self, fields: dict[str, Any]) -> None:
        self.fields = fields
        self.issues: list[TargetIssue] = []
        self.flagged: set[str] = set()

    def __call__(self, message: str, field: str | None = None) -> None:
        label = self.fields[field].label if field in self.fields else None
        self.issues.append(TargetIssue(level=IssueLevel.BLOCKING, field=field, label=label, message=message))
        if field:
            self.flagged.add(field)


def _rule_wins(issues: list[TargetIssue]) -> list[TargetIssue]:
    """Une règle du binding sur un champ remplace le message générique du même champ (« … is required »)."""
    ruled = {i.field for i in issues if i.rule_id and i.field}
    return [i for i in issues if i.rule_id or i.field not in ruled]


def prepare_products(
    items: list[ProductItem],
    resolved: ResolvedBinding,
    builder: RecordRequestBuilder,
    *,
    price_basis: str | None,
    tax_rates: dict[str, Decimal],
    fmt: ValueFormat | None = None,
) -> list[PreparedProduct]:
    """Unités vendables (Product CDM) → requêtes canoniques de la cible. PURE : mêmes unités, mêmes décisions
    et même contrat = même résultat, quelle que soit la source qui a rempli le CDM."""
    fmt = fmt or PlainValueFormat()
    b = resolved.binding
    tax = b.lookup(LookupKind.TAX_RATE)
    fields = {f.technical_name: f for f in resolved.fields}
    out = []
    for item in items:
        block = _Blocks(fields)  # décisions manquantes et limites de la cible : messages propres
        supplied: dict[str, Any] = {}
        if item.price is not None:
            cur = item.price.currency
            if cur is None:
                block("The currency of the price is unknown in the source")
            elif b.currencies and cur not in b.currencies:
                accepted = ", ".join(b.currencies)
                block(f"{b.target.display_name} only accepts prices in {accepted} (this price is in {cur})")
        if price_basis is None:
            block("Choose whether the prices of the source are net or gross (Settings)")
            block.flagged |= {f for f in fields if f.startswith("price.") and (tax is None or f != tax.field)}
        else:
            supplied["price_basis"] = price_basis
            if item.price is not None:
                supplied[NET if price_basis == "NET" else GROSS] = item.price.amount
        if tax is not None:
            labels = ", ".join(f"« {t.label or t.ref} »" for t in item.sales_taxes)
            if not item.sales_taxes:
                block(
                    "The source gives no sales tax for this product: its tax rate cannot be chosen", tax.field
                )
            elif len(item.sales_taxes) > 1:
                block(
                    f"The source gives several sales taxes ({labels}): only one tax rate can be sent",
                    tax.field,
                )
            elif (rate := tax_rates.get(item.sales_taxes[0].ref)) is None:
                block(f"Choose the {tax.label.lower()} of {labels} (Tax rates)", tax.field)
            else:
                supplied["tax_rate"] = rate
        rec = map_record(
            item.mapping_view(), resolved, fmt, ENCODING, source_ref=item.source_ref, target_values=supplied
        )
        issues = block.issues + _rule_wins([i for i in rec.issues if i.field not in block.flagged])
        status = TargetRecord(source_ref=item.source_ref, values=rec.values, issues=issues).status
        ordered = [f for f in resolved.fields if f.technical_name in rec.values]
        out.append(
            PreparedProduct(
                source_ref=item.source_ref,
                title=item.title,
                sku=item.sku,
                kind=item.kind.value,
                status=status,
                issues=issues,
                values=[
                    PreparedValue(
                        field=f.technical_name,
                        label=f.label or f.technical_name,
                        value=rec.values[f.technical_name],
                    )
                    for f in ordered
                ],
                request=None
                if status == RecordStatus.BLOCKED
                else builder.build(resolved.fields, rec.values),
                content=DocumentContent(
                    source_ref=item.source_ref,
                    number=None,
                    units=[{k: fmt.format_cell(fields[k], v) for k, v in sorted(rec.values.items())}],
                ),
            )
        )
    return out


class TargetOption(_Model):
    """Système connecté de l'organisation qui peut recevoir des produits par son API."""

    instance_id: uuid.UUID
    name: str
    system: str
    target: str  # nom du binding (ce que les produits deviennent dans la cible)
    connected: bool


class TaxRateDecision(_Model):
    """Taxe de vente de la source et le taux décidé pour la cible (None : à décider)."""

    ref: str
    label: str | None
    products: int  # unités vendables qui portent cette taxe (seule)
    rate: str | None


class PreviewProduct(_Model):
    prepared: PreparedProduct
    export_state: DocumentExportState
    changed_fields: list[str] = []
    source_issues: list[str] = []
    linked: bool = False  # lié à un article de la cible : un envoi le met à jour
    linked_by_match: bool = False  # lien posé par un rapprochement humain (pas par un envoi)
    outcome_unknown: bool = False  # un envoi précédent a une issue inconnue non résolue

    @property
    def sendable(self) -> bool:
        return (
            self.prepared.status != RecordStatus.BLOCKED
            and self.export_state != DocumentExportState.ALREADY_EXPORTED
            and not self.outcome_unknown
        )


class ProductTargetPreview(_Model):
    source_name: str
    source_system: str
    target_name: str
    target_system: str
    target: str
    contract: str  # snapshot du Registry qui a validé les requêtes
    price_basis: str | None
    bases: list[str]  # valeurs admises par la cible pour la base des prix (Registry)
    rates: list[str]  # taux admis par la cible (Registry)
    taxes: list[TaxRateDecision]
    products_read: int
    archived: int  # unités archivées dans la source : jamais préparées
    truncated: bool
    records: list[PreviewProduct]

    def count(self, status: RecordStatus) -> int:
        return sum(1 for r in self.records if r.prepared.status == status)

    def count_state(self, state: DocumentExportState) -> int:
        return sum(1 for r in self.records if r.export_state == state)


def _system(inst: m.Instance) -> tuple[m.System, m.SystemProduct, m.SystemVersion]:
    ver = inst.interface.version
    return ver.product.system, ver.product, ver


class SendView(_Model):
    """Un envoi et ses éléments (aucune donnée métier stockée : titres lus dans la source à l'affichage)."""

    run: Any
    items: list[Any]
    names: dict[str, str]  # référence source → titre actuel (lecture de la source), affichage seulement
    labels: dict[str, str] = {}  # champ du contrat cible → libellé du Registry (messages d'erreur)


@dataclass(frozen=True)
class _Prepared:
    source: m.Instance
    target: m.Instance
    resolved: ResolvedBinding
    scope: DeliveryScope
    items: list[ProductItem]
    archived: int
    prepared: list[PreparedProduct]
    basis: str | None
    rate_rows: dict[str, ExportValueMappingRecord]
    read_issues: dict[str, list[str]]  # référence du PRODUIT → messages de lecture
    products_read: int
    truncated: bool


class ProductTargetService:
    def __init__(
        self,
        session: Session,
        products: ProductReadService,
        senders: RecordSenderResolver | None = None,
        commit: Callable[[], None] | None = None,
    ) -> None:
        self.s = session
        self.ctx = products.ctx
        self.products = products
        self.instances = products.instances
        self.registry = RegistryService(session, self.ctx)
        self.ledger = DocumentExportLedger(session)
        self.user = self.ctx.user_id if isinstance(self.ctx, TenantContext | AdminContext) else None
        self.senders = senders or RecordSenderResolver()
        self.delivery = DeliveryService(session, user_id=self.user, commit=commit)

    # ------------------------------------------------------------------ #
    def _binding(self, inst: m.Instance) -> TargetBinding | None:
        system, product, _ = _system(inst)
        try:
            binding = TargetBindingRegistry.get(SOURCE_MODEL, system.code, product.code)
        except TargetBindingError:
            return None
        return binding if binding.delivery == DELIVERY else None

    def targets(self, source_id: uuid.UUID) -> list[TargetOption]:
        """Systèmes de la MÊME organisation que la source qui peuvent recevoir des produits."""
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
        target = self.instances.get(target_id)
        binding = self._binding(target)
        if binding is None:
            raise NotFound(f"Le système {target.name} ne reçoit pas de produits")
        system, _, version = _system(target)
        try:
            resolved = resolve_binding(self.registry, binding, version.version)
            builder = RecordRequestBuilderRegistry.get(system.code, target.interface.code)
        except (TargetBindingError, RecordFileWriterError) as exc:
            raise ProductTargetError(str(exc)) from None
        return target, resolved, builder

    def _pair(
        self, source_id: uuid.UUID, target_id: uuid.UUID
    ) -> tuple[m.Instance, m.Instance, ResolvedBinding, RecordRequestBuilder]:
        """Source et cible de la MÊME organisation (contrôles d'appartenance AVANT toute lecture)."""
        source = self.instances.get(source_id)
        target, resolved, builder = self.resolve(target_id)
        if source.id == target.id:
            raise ProductTargetError("Choose two different systems")
        if source.organization_id != target.organization_id:
            raise NotFound(f"Instance {target_id} introuvable")
        return source, target, resolved, builder

    def _scope(self, source: m.Instance, target: m.Instance, resolved: ResolvedBinding) -> DeliveryScope:
        t = resolved.binding.target
        return DeliveryScope(
            organization_id=source.organization_id,
            source_instance_id=source.id,
            target_instance_id=target.id,
            domain=DOMAIN,
            target_system=t.system,
            target_product=t.product,
            resource=t.entity,
        )

    # ------------------------------------------------------------------ #
    # Décisions explicites : base des prix, taux des taxes
    # ------------------------------------------------------------------ #
    def _rows(self, source: m.Instance, b: TargetBinding, kind: str) -> dict[str, ExportValueMappingRecord]:
        rows = self.s.scalars(
            select(ExportValueMappingRecord).where(
                ExportValueMappingRecord.organization_id == source.organization_id,
                ExportValueMappingRecord.target_system == b.target.system,
                ExportValueMappingRecord.target_product == b.target.product,
                ExportValueMappingRecord.instance_id == source.id,
                ExportValueMappingRecord.kind == kind,
            )
        )
        return {r.source_ref: r for r in rows}

    def _decide(
        self, source: m.Instance, b: TargetBinding, kind: str, ref: str, value: str, label: str | None
    ) -> ExportValueMappingRecord:
        row = self._rows(source, b, kind).get(ref)
        if row is None:
            row = ExportValueMappingRecord(
                organization_id=source.organization_id,
                target_system=b.target.system,
                target_product=b.target.product,
                instance_id=source.id,
                kind=kind,
                source_ref=ref,
            )
            self.s.add(row)
        row.target_value = value
        row.origin = "MANUAL"
        row.suggestion_origin = None
        row.source_name = (label or "")[:200] or None
        row.decided_by = self.user
        self.s.flush()
        return row

    def _clear(self, source: m.Instance, b: TargetBinding, kind: str, ref: str) -> bool:
        row = self._rows(source, b, kind).get(ref)
        if row is None:
            return False
        self.s.delete(row)
        self.s.flush()
        return True

    @staticmethod
    def _allowed(resolved: ResolvedBinding, field: str | None) -> list[str]:
        """Valeurs admises par le contrat de la cible pour un champ (Registry), dans l'ordre du contrat."""
        if field is None:
            return []
        allowed = resolved.field(field).metadata.get("enum")
        return [str(v) for v in allowed] if isinstance(allowed, list) else []

    def _basis_field(self, resolved: ResolvedBinding) -> str | None:
        return next((f.field for f in resolved.binding.fields if f.source == "target.price_basis"), None)

    def set_price_basis(
        self, source_id: uuid.UUID, target_id: uuid.UUID, basis: str
    ) -> ExportValueMappingRecord:
        source, _, resolved, _ = self._pair(source_id, target_id)
        value = basis.upper()
        allowed = [v for v in BASES if v in self._allowed(resolved, self._basis_field(resolved))]
        if value not in allowed:
            raise ProductTargetError(f"The price basis must be one of: {', '.join(allowed) or 'none'}")
        return self._decide(source, resolved.binding, PRICE_BASIS, PRICE_BASIS_REF, value, None)

    def clear_price_basis(self, source_id: uuid.UUID, target_id: uuid.UUID) -> bool:
        source, _, resolved, _ = self._pair(source_id, target_id)
        return self._clear(source, resolved.binding, PRICE_BASIS, PRICE_BASIS_REF)

    def set_tax_rate(
        self, source_id: uuid.UUID, target_id: uuid.UUID, tax_ref: str, rate: str, *, label: str | None = None
    ) -> ExportValueMappingRecord:
        """Correspondance EXPLICITE taxe de vente source → taux admis par la cible (contrôlé par le
        Registry)."""
        source, _, resolved, _ = self._pair(source_id, target_id)
        if not tax_ref or len(tax_ref) > 120:
            raise ProductTargetError("Unknown tax")
        lookup = resolved.binding.lookup(LookupKind.TAX_RATE)
        if lookup is None:
            raise ProductTargetError("This target does not take a tax rate")
        allowed = self._allowed(resolved, lookup.field)
        value = _rate_text(rate)
        if value is None or value not in allowed:
            raise ProductTargetError(f"The tax rate must be one of: {', '.join(allowed)}")
        return self._decide(source, resolved.binding, LookupKind.TAX_RATE.value, tax_ref, value, label)

    def clear_tax_rate(self, source_id: uuid.UUID, target_id: uuid.UUID, tax_ref: str) -> bool:
        source, _, resolved, _ = self._pair(source_id, target_id)
        return self._clear(source, resolved.binding, LookupKind.TAX_RATE.value, tax_ref)

    # ------------------------------------------------------------------ #
    def _prepare(self, source_id: uuid.UUID, target_id: uuid.UUID) -> _Prepared:
        """Lecture du Product CDM de la source et préparation avec les décisions enregistrées (même chemin
        pour l'aperçu et l'envoi : ce qui est envoyé est exactement ce qui a été montré)."""
        source, target, resolved, builder = self._pair(source_id, target_id)
        b = resolved.binding
        result = self.products.read(source.id)
        items = [i for p in result.products for i in p.items()]
        archived = sum(1 for i in items if i.active is False)
        items = [i for i in items if i.active is not False]
        basis_row = self._rows(source, b, PRICE_BASIS).get(PRICE_BASIS_REF)
        rate_rows = self._rows(source, b, LookupKind.TAX_RATE.value)
        rates = {ref: Decimal(r.target_value) for ref, r in rate_rows.items() if r.target_value is not None}
        basis = basis_row.target_value if basis_row else None
        prepared = prepare_products(items, resolved, builder, price_basis=basis, tax_rates=rates)
        read_issues: dict[str, list[str]] = {}
        for issue in result.issues:
            read_issues.setdefault(issue.source_ref, []).append(issue.message)
        return _Prepared(
            source=source,
            target=target,
            resolved=resolved,
            scope=self._scope(source, target, resolved),
            items=items,
            archived=archived,
            prepared=prepared,
            basis=basis,
            rate_rows=rate_rows,
            read_issues=read_issues,
            products_read=len(result.products),
            truncated=result.truncated,
        )

    def preview(self, source_id: uuid.UUID, target_id: uuid.UUID) -> ProductTargetPreview:
        """Aperçu SANS EFFET : lit le Product CDM de la source, prépare les requêtes avec les décisions
        enregistrées, classe chaque unité d'après l'historique de CETTE cible. N'appelle jamais la cible."""
        x = self._prepare(source_id, target_id)
        resolved, b = x.resolved, x.resolved.binding
        product_of = {i.source_ref: i.product_ref for i in x.items}
        classes = self.ledger.classify(x.scope.documents, [p.content for p in x.prepared])
        links = self.delivery.links(x.scope)
        unknown = self.delivery.open_unknown(x.scope)
        labels = {f.technical_name: f.label or f.technical_name for f in resolved.fields}
        lookup = b.lookup(LookupKind.TAX_RATE)
        return ProductTargetPreview(
            source_name=x.source.name,
            source_system=_system(x.source)[0].name,
            target_name=x.target.name,
            target_system=_system(x.target)[0].name,
            target=b.name,
            contract=resolved.snapshot_label,
            price_basis=x.basis,
            bases=[v for v in BASES if v in self._allowed(resolved, self._basis_field(resolved))],
            rates=self._allowed(resolved, lookup.field if lookup else None),
            taxes=_taxes(x.items, x.rate_rows),
            products_read=x.products_read,
            archived=x.archived,
            truncated=x.truncated,
            records=[
                PreviewProduct(
                    prepared=p,
                    export_state=classes[p.source_ref].state,
                    changed_fields=sorted(
                        {labels.get(c.field, c.field) for c in classes[p.source_ref].changes}
                    ),
                    source_issues=x.read_issues.get(product_of[p.source_ref], []),
                    linked=p.source_ref in links,
                    linked_by_match=p.source_ref in links and links[p.source_ref].origin == "MATCHED",
                    outcome_unknown=p.source_ref in unknown,
                )
                for p in x.prepared
            ],
        )

    # ------------------------------------------------------------------ #
    # Envoi (phase 1b) : toujours une sélection humaine et une confirmation explicite
    # ------------------------------------------------------------------ #
    def _candidates(
        self, source_id: uuid.UUID, target_id: uuid.UUID
    ) -> tuple[_Prepared, list[DeliveryCandidate]]:
        x = self._prepare(source_id, target_id)
        candidates = [
            DeliveryCandidate(
                source_ref=p.source_ref,
                blocked=p.status == RecordStatus.BLOCKED,
                request=p.request,
                content=p.content,
            )
            for p in x.prepared
        ]
        return x, candidates

    def _sender_factory(self, target: m.Instance) -> Callable[[], RecordSender]:
        system, _, _ = _system(target)
        interface = target.interface.code
        if not self.senders.supports(system.code, interface):
            raise ProductTargetError(f"Sending to {system.name} is not available")

        def factory() -> RecordSender:
            _, ctx = self.instances.connection_context(target.id)  # secret déchiffré au dernier moment
            try:
                return self.senders.create(system.code, interface, ctx)
            except (RecordSenderError, SendError) as exc:
                raise ProductTargetError(str(exc)) from None

        return factory

    def _view(self, run: TargetSendRun, x: _Prepared) -> SendView:
        labels = {f.technical_name: f.label or f.technical_name for f in x.resolved.fields}
        names = {p.source_ref: p.title for p in x.prepared}
        return SendView(run=run, items=self.delivery.items(run), names=names, labels=labels)

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
        """Envoie les articles CHOISIS (jamais bloqués, jamais inchangés). `confirm` explicite exigé ; même
        `request_key` = même envoi, jamais rejoué."""
        if not confirm:
            raise ProductTargetError("Confirm the send first")
        x, candidates = self._candidates(source_id, target_id)
        run = self.delivery.send(
            x.scope,
            candidates,
            refs=refs,
            request_key=request_key,
            create_new=set(create_new or []),
            target_label=f"{x.resolved.binding.name} ({x.resolved.snapshot_label})",
            sender_factory=self._sender_factory(x.target),
        )
        return self._view(run, x)

    def resume(self, source_id: uuid.UUID, target_id: uuid.UUID, run_id: uuid.UUID) -> SendView:
        x, candidates = self._candidates(source_id, target_id)
        return self._view(
            self.delivery.resume(x.scope, run_id, candidates, self._sender_factory(x.target)), x
        )

    def runs(self, source_id: uuid.UUID, target_id: uuid.UUID) -> list[TargetSendRun]:
        source, target, resolved, _ = self._pair(source_id, target_id)
        return self.delivery.runs(self._scope(source, target, resolved))

    def run(self, source_id: uuid.UUID, target_id: uuid.UUID, run_id: uuid.UUID) -> SendView:
        x = self._prepare(source_id, target_id)
        return self._view(self.delivery.run(x.scope, run_id), x)

    def matches(self, source_id: uuid.UUID, target_id: uuid.UUID, source_ref: str) -> list[RemoteRecord]:
        """Articles EXISTANTS de la cible qui pourraient être cette unité (lecture seule)."""
        x, candidates = self._candidates(source_id, target_id)
        c = next((y for y in candidates if y.source_ref == source_ref), None)
        if c is None or c.request is None:
            raise ProductTargetError("This record cannot be matched (unknown or blocked)")
        with self._sender_factory(x.target)() as sender:
            try:
                found = sender.find_candidates(x.scope.resource, c.request)
            except SendError as exc:
                raise ProductTargetError(
                    f"The target system could not be searched ({exc.kind.value})"
                ) from None
        taken = self.delivery.linked_target_ids(x.scope)  # déjà lié à une autre unité : jamais proposé
        return [f for f in found if f.id not in taken]

    def link(
        self, source_id: uuid.UUID, target_id: uuid.UUID, source_ref: str, remote_id: str
    ) -> TargetResourceLink:
        x, candidates = self._candidates(source_id, target_id)
        if source_ref not in {c.source_ref for c in candidates}:
            raise ProductTargetError("Unknown record")
        return self.delivery.link(x.scope, source_ref, remote_id, self._sender_factory(x.target))

    def unlink(self, source_id: uuid.UUID, target_id: uuid.UUID, source_ref: str) -> None:
        source, target, resolved, _ = self._pair(source_id, target_id)
        self.delivery.unlink(self._scope(source, target, resolved), source_ref)

    def resolve_unknown(
        self,
        source_id: uuid.UUID,
        target_id: uuid.UUID,
        item_id: uuid.UUID,
        *,
        created: bool,
        remote_id: str | None,
    ) -> TargetSendItem:
        source, target, resolved, _ = self._pair(source_id, target_id)
        return self.delivery.resolve_unknown(
            self._scope(source, target, resolved),
            item_id,
            created=created,
            target_id=remote_id,
            sender_factory=self._sender_factory(target),
        )


def _rate_text(rate: str) -> str | None:
    """« 19 », « 19.0 », « 19,0 » → « 19 » (forme des valeurs admises du contrat) ; illisible → None."""
    try:
        d = Decimal(str(rate).strip().replace(",", "."))
    except InvalidOperation:
        return None
    return str(d.quantize(Decimal(1))) if d == d.to_integral_value() else str(d.normalize())


def _taxes(items: list[ProductItem], rows: dict[str, ExportValueMappingRecord]) -> list[TaxRateDecision]:
    """Taxes de vente rencontrées (seules sur une unité : les seules qu'un taux traduit), avec la décision."""
    counts: dict[str, int] = {}
    labels: dict[str, str | None] = {}
    for i in items:
        if len(i.sales_taxes) == 1:
            t = i.sales_taxes[0]
            counts[t.ref] = counts.get(t.ref, 0) + 1
            labels[t.ref] = t.label
    return [
        TaxRateDecision(
            ref=ref,
            label=labels[ref],
            products=n,
            rate=rows[ref].target_value if ref in rows else None,
        )
        for ref, n in sorted(counts.items(), key=lambda kv: ((labels[kv[0]] or ""), kv[0]))
    ]
