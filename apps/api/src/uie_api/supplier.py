"""Presenters de la Supplier API (V1d, `/api/*`) : DTO PROPRES au supplier, jamais ceux de l'Admin.

Mêmes services Core, contexte tenant, vocabulaire métier. Aucune donnée technique n'en sort : ni snapshot,
checksum, baseline, diff, classification d'origine (STANDARD / CUSTOM / THIRD_PARTY), binding brut,
rule_id, version de règles / vocabulaire / prompt, fournisseur ni modèle LLM, endpoint ni configuration de
connexion. Un test parcourt toutes les réponses Supplier à la recherche de ces clés.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from uie_core.catalog import CatalogProduct, Money
from uie_core.catalog.records import CatalogProductRecord, CatalogSyncRecord
from uie_core.catalog.service import ProductPage
from uie_core.domains import (
    AiEnrichmentResult,
    AiStatus,
    BindingView,
    CatalogAnalysisSummary,
    DecisionSource,
    EntityRole,
    MeaningStatus,
    Vocabulary,
)
from uie_core.domains.model import DomainKey
from uie_core.domains.pack import DomainPackRegistry
from uie_core.domains.semantics import OTHER_KEY
from uie_core.instances import ConnectionCheck, ConnectionField, ConnectionFieldRole
from uie_core.mapping import TargetBindingRegistry
from uie_core.registry import models as m
from uie_core.sources import SourceBindingRegistry


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


# libellés métier (anglais : langue de l'interface)
TYPE_LABEL = {
    "string": "Text",
    "text": "Long text",
    "integer": "Whole number",
    "decimal": "Number",
    "boolean": "Yes / No",
    "date": "Date",
    "datetime": "Date and time",
    "enum": "Choice list",
    "binary": "File / image",
    "json": "Structured data",
    "reference": "Link",
    "collection": "List",
}
STRUCTURE_LABEL = {
    EntityRole.PRIMARY: "Products",
    EntityRole.VARIANT: "Variants",
    EntityRole.CATEGORY: "Categories",
    EntityRole.ATTRIBUTE: "Attributes",
    EntityRole.MEDIA: "Images",
}
ACCOUNTING_STRUCTURE_LABEL = {
    EntityRole.DOCUMENT: "Invoices and credit notes",
    EntityRole.DOCUMENT_LINE: "Document lines",
    EntityRole.LEDGER_ACCOUNT: "Chart of accounts",
    EntityRole.TAX: "Taxes",
}
PARTNER_STRUCTURE_LABEL = {
    EntityRole.PRIMARY: "Partners",
    EntityRole.BANK_ACCOUNT: "Bank accounts",
    EntityRole.COUNTRY: "Countries",
}
SOURCE = {
    DecisionSource.STANDARD: "rules",
    DecisionSource.RULE: "rules",
    DecisionSource.LLM: "ai",
    DecisionSource.HUMAN: "you",
}

ConnectionState = Literal["connected", "failed", "not_tested"]
CatalogState = Literal["awaiting_setup", "not_analyzed", "questions_open", "complete"]


# --------------------------------------------------------------------------- #
# Entrées
# --------------------------------------------------------------------------- #
class ConfirmAnswerIn(_In):
    meaning: str | None = Field(None, description="Clé du vocabulaire ; absente = accepter la suggestion")
    note: str | None = None


class ChangeAnswerIn(_In):
    meaning: str = Field(description=f"Clé du vocabulaire, ou '{OTHER_KEY}' (autre / je ne sais pas)")
    note: str | None = None


class NoteAnswerIn(_In):
    note: str | None = None


# --------------------------------------------------------------------------- #
# Systèmes (instances du tenant)
# --------------------------------------------------------------------------- #
class ConnectionStatusOut(_Out):
    status: ConnectionState
    message: str | None
    tested_at: datetime | None


class CatalogStatusOut(_Out):
    status: CatalogState
    questions: int | None
    analyzed_at: datetime | None


class FactOut(_Out):
    """Fait affichable d'un système (libellé, valeur) : jamais un secret ni un identifiant de connexion."""

    label: str
    value: str


class SupplierSystemOut(_Out):
    id: uuid.UUID
    name: str
    system: str  # « Odoo 17 »
    system_code: str  # « odoo » (icône, nom du système)
    environment: str
    server_url: str | None  # adresse saisie par le fournisseur (jamais d'identifiant de connexion)
    database: str | None
    connection: ConnectionStatusOut
    catalog: CatalogStatusOut
    # ce que le système sait faire ICI, d'après ses connecteurs et ses packs (aucun système n'est « source »
    # ou « cible » par nature) : domaines qu'il peut FOURNIR, modèles qu'il peut RECEVOIR par son API
    provides: list[str] = []  # « product », « partner », « accounting »
    receives: list[str] = []  # « partner »
    # domaines dont le sens est DÉCLARÉ par un binding de source (schéma fixe) : prêts sans analyse
    declared: list[str] = []
    details: list[FactOut] = []  # faits sûrs du dernier test réussi (ex. société du compte)


# Causes d'échec de connexion → phrase simple (jamais le message technique du connecteur)
CONNECTION_MESSAGES = {
    "UNREACHABLE": "We could not reach the system. Check the connection details and that it is online.",
    "AUTH_FAILED": "The credentials were not accepted. Check them and try again.",
    "ACCESS_DENIED": "The credentials are valid, but they do not allow this access. Check their permissions.",
    "RATE_LIMITED": "The system is receiving too many requests right now. Please try again in a moment.",
    "DATABASE_NOT_FOUND": "This database was not found on the server.",
    "INCOMPLETE": "Some connection details are missing.",
    "UNEXPECTED": "The connection could not be completed. Check the details and try again.",
}


def connection_message(kind: str | None) -> str:
    return CONNECTION_MESSAGES.get(kind or "", CONNECTION_MESSAGES["UNEXPECTED"])


def system_label(i: m.Instance) -> str:
    ver = i.interface.version
    return f"{ver.product.system.name} {ver.version}"


def connection_status(i: m.Instance) -> ConnectionStatusOut:
    conn = i.connections[0] if i.connections else None
    if conn is None or conn.last_test_status is None:
        return ConnectionStatusOut(status="not_tested", message=None, tested_at=None)
    ok = conn.last_test_status == "OK"
    return ConnectionStatusOut(
        status="connected" if ok else "failed",
        message=None if ok else connection_message((conn.last_test_info or {}).get("error_kind")),
        tested_at=conn.last_test_at,
    )


def catalog_status(i: m.Instance, summary: CatalogAnalysisSummary | None) -> CatalogStatusOut:
    if summary is None or not summary.enriched:
        # sans schéma découvert (opération d'administration), l'analyse n'est pas encore possible
        state: CatalogState = "not_analyzed" if i.snapshots else "awaiting_setup"
        return CatalogStatusOut(
            status=state, questions=None, analyzed_at=summary.analyzed_at if summary else None
        )
    return CatalogStatusOut(
        status="complete" if summary.complete else "questions_open",
        questions=summary.pending,
        analyzed_at=summary.analyzed_at,
    )


# domaine du Core → nom du modèle côté fournisseur
PROVIDED = {DomainKey.PRODUCT: "product", DomainKey.PARTNER: "partner", DomainKey.ACCOUNTING: "accounting"}


def system_declared(code: str) -> list[str]:
    """Domaines dont le sens est déclaré par un binding de source à schéma fixe : rien à analyser."""
    keys = [DomainKey(d) for d in SourceBindingRegistry.domains_for(code)]
    return sorted(PROVIDED[k] for k in keys if k in PROVIDED)


def system_provides(code: str) -> list[str]:
    """Domaines que ce système sait fournir : un domain pack (analyse) ou un binding de source (schéma fixe)
    existe pour lui (jamais un nom de système)."""
    analyzed = [PROVIDED[d] for d in DomainPackRegistry.domains_for(code) if d in PROVIDED]
    return analyzed + [d for d in system_declared(code) if d not in analyzed]


def system_receives(code: str) -> list[str]:
    """Modèles que ce système sait recevoir par son API : un binding cible `delivery: api` existe."""
    models = {
        b.source.model
        for model in PROVIDED.values()
        for b in TargetBindingRegistry.for_model(model, delivery="api")
        if b.target.system == code
    }
    return sorted(models)


def supplier_system(
    i: m.Instance,
    summary: CatalogAnalysisSummary | None,
    facts: list[tuple[str, str]] | None = None,
    *,
    own_address: bool = True,
) -> SupplierSystemOut:
    """`own_address` faux : l'adresse appartient au connecteur (API publique), elle n'est pas une donnée du
    fournisseur et n'est pas affichée."""
    code = i.interface.version.product.system.code
    return SupplierSystemOut(
        id=i.id,
        name=i.name,
        system=system_label(i),
        system_code=code,
        environment=i.environment,
        server_url=i.connections[0].endpoint if i.connections and own_address else None,
        database=_database(i),
        connection=connection_status(i),
        catalog=catalog_status(i, summary),
        provides=system_provides(code),
        receives=system_receives(code),
        declared=system_declared(code),
        details=[FactOut(label=label, value=value) for label, value in facts or []],
    )


def _database(i: m.Instance) -> str | None:
    config = i.connections[0].config if i.connections else {}
    value = config.get("db") or config.get("database")
    return str(value) if value else None


# --------------------------------------------------------------------------- #
# Connecter un système : formulaire DÉCLARÉ par le connecteur (aucun champ propre à un système ici)
# --------------------------------------------------------------------------- #
class CheckConnectionIn(_In):
    system: str = Field(min_length=1, max_length=80)
    # valeurs du formulaire du connecteur, par nom de champ ; toutes traitées comme des secrets à l'entrée
    # (jamais renvoyées, jamais dans un message de validation)
    fields: dict[str, SecretStr] = Field(description="Write-only: never returned")

    @field_validator("fields")
    @classmethod
    def _sizes(cls, value: dict[str, SecretStr]) -> dict[str, SecretStr]:
        if len(value) > 20 or any(len(k) > 80 or len(v.get_secret_value()) > 4000 for k, v in value.items()):
            raise ValueError("too many or too long connection values")
        return value


class ConnectSystemIn(CheckConnectionIn):
    name: str = Field(min_length=1, max_length=200, description="Name of the system in your workspace")


FieldKind = Literal["text", "url", "secret"]
FIELD_KIND: dict[ConnectionFieldRole, FieldKind] = {
    ConnectionFieldRole.ENDPOINT: "url",
    ConnectionFieldRole.CONFIG: "text",
    ConnectionFieldRole.SECRET: "secret",
}


class ConnectorFieldOut(_Out):
    """Champ du formulaire de connexion, tel que le connecteur le déclare."""

    name: str
    label: str
    kind: FieldKind  # « secret » : saisie masquée, jamais réaffichée
    required: bool
    help: str | None
    placeholder: str | None

    @classmethod
    def of(cls, f: ConnectionField) -> ConnectorFieldOut:
        return cls(
            name=f.name,
            label=f.label,
            kind=FIELD_KIND[f.role],
            required=f.required,
            help=f.help,
            placeholder=f.placeholder,
        )


class ConnectorOut(_Out):
    """Système connectable : versions prises en charge (Registry) et formulaire de connexion (connecteur)."""

    system: str  # « odoo »
    name: str  # « Odoo »
    versions: list[str]
    fields: list[ConnectorFieldOut]


class SystemRemovedOut(_Out):
    name: str
    removed: bool


class ConnectionCheckOut(_Out):
    ok: bool
    detected: str | None  # « Odoo 17 »
    supported: bool
    message: str | None
    details: list[FactOut] = []  # faits sûrs du système joint (ex. société du compte), si le test a réussi

    @classmethod
    def of(cls, check: ConnectionCheck, facts: list[tuple[str, str]] | None = None) -> ConnectionCheckOut:
        detected = f"{check.system_name or check.system.title()} {check.version}" if check.version else None
        if not check.result.ok:
            message = connection_message(check.result.error_kind)
        elif not check.supported:
            message = f"{detected or 'This version'} is not supported yet."
        else:
            message = None
        return cls(
            ok=check.result.ok,
            detected=detected,
            supported=check.supported,
            message=message,
            details=[FactOut(label=label, value=value) for label, value in facts or []],
        )


# --------------------------------------------------------------------------- #
# Catalogue
# --------------------------------------------------------------------------- #
class StructureItemOut(_Out):
    label: str
    present: bool


class CatalogOut(_Out):
    status: CatalogState
    analyzed_at: datetime | None
    total: int
    identified: int
    excluded_automatically: int
    excluded_by_you: int
    possible: int
    questions: int
    answers: int
    complete: bool
    structure: list[StructureItemOut]

    @classmethod
    def of(
        cls, i: m.Instance, s: CatalogAnalysisSummary | None, labels: dict[EntityRole, str] | None = None
    ) -> CatalogOut:
        state = catalog_status(i, s).status
        if s is None:
            return cls(
                status=state,
                analyzed_at=None,
                total=0,
                identified=0,
                excluded_automatically=0,
                excluded_by_you=0,
                possible=0,
                questions=0,
                answers=0,
                complete=False,
                structure=[],
            )
        return cls(
            status=state,
            analyzed_at=s.analyzed_at,
            total=s.total,
            identified=s.identified,
            excluded_automatically=s.excluded_auto,
            excluded_by_you=s.excluded_by_supplier,
            possible=s.possible,
            questions=s.pending,
            answers=s.supplier_decisions,
            complete=s.complete,
            structure=[
                StructureItemOut(
                    label=(labels or STRUCTURE_LABEL).get(c.role, c.role.value.title()), present=c.present
                )
                for c in s.structure
            ],
        )


class MeaningOut(_Out):
    key: str
    label: str


class SuggestionOut(_Out):
    meaning: MeaningOut
    confidence: float | None
    source: Literal["rules", "ai", "you"]
    justification: str | None
    low_confidence: bool


class CatalogFieldOut(_Out):
    """Champ tel que le supplier le voit. `entity` / `field` servent d'identifiants dans les URL d'action."""

    entity: str
    field: str
    label: str
    description: str | None
    type: str  # « Link to product.category », « Text »…
    required: bool


class QuestionOut(_Out):
    field: CatalogFieldOut
    suggestion: SuggestionOut | None
    reason: str | None  # pourquoi la question est posée (texte lisible)
    no_suggestion_reason: str | None  # l'IA s'est abstenue : pourquoi
    changed_since_answer: bool  # le champ a changé depuis votre réponse : question reposée
    previous_answer: MeaningOut | None
    reopened: bool


class AnswerOut(_Out):
    field: CatalogFieldOut
    answer: Literal["confirmed", "ignored", "other"]
    meaning: MeaningOut | None
    note: str | None
    answered_at: datetime


def _field(v: BindingView) -> CatalogFieldOut:
    f = v.field
    base = TYPE_LABEL.get(f.data_type or "", f.native_type or "Unknown")
    return CatalogFieldOut(
        entity=v.entity_technical_name,
        field=v.field_technical_name,
        label=f.label or v.field_technical_name,
        description=f.help,
        type=f"{base} to {f.relation_target}" if f.relation_target else base,
        required=f.required,
    )


def _meaning(key: str | None, vocab: Vocabulary, label: str | None = None) -> MeaningOut | None:
    if not key or key == OTHER_KEY:
        return None
    entry = vocab.get(key)
    return MeaningOut(key=key, label=label or (entry.label if entry else key))


def question(v: BindingView, vocab: Vocabulary) -> QuestionOut:
    meaning = _meaning(v.semantic_key, vocab, v.semantic_label)
    prev = v.previous or {}
    stale = v.meaning_status == MeaningStatus.STALE
    reopened = v.source == DecisionSource.HUMAN and not stale
    return QuestionOut(
        field=_field(v),
        suggestion=SuggestionOut(
            meaning=meaning,
            confidence=v.confidence,
            source=SOURCE[v.source],
            justification=v.justification,
            low_confidence=v.low_confidence,
        )
        if meaning
        else None,
        reason=v.reason,
        no_suggestion_reason=v.note_ai if meaning is None else None,
        changed_since_answer=stale,
        previous_answer=_meaning(prev.get("semantic_key"), vocab) if (stale or reopened) else None,
        reopened=reopened,
    )


def answer(v: BindingView, vocab: Vocabulary) -> AnswerOut:
    kind: Literal["confirmed", "ignored", "other"] = (
        "confirmed"
        if v.meaning_status == MeaningStatus.CONFIRMED
        else "ignored"
        if v.meaning_status == MeaningStatus.IGNORED
        else "other"
    )
    return AnswerOut(
        field=_field(v),
        answer=kind,
        meaning=_meaning(v.semantic_key, vocab, v.semantic_label) if kind == "confirmed" else None,
        note=v.note,
        answered_at=v.confirmed_at or v.updated_at,
    )


# --------------------------------------------------------------------------- #
# Vocabulaire (sélecteur « Change ») et Ask AI
# --------------------------------------------------------------------------- #
class SupplierVocabularyEntryOut(_Out):
    key: str
    label: str
    description: str


class SupplierVocabularyGroupOut(_Out):
    group: str
    entries: list[SupplierVocabularyEntryOut]


class SupplierVocabularyOut(_Out):
    groups: list[SupplierVocabularyGroupOut]
    other: MeaningOut  # « autre / je ne sais pas »

    @classmethod
    def of(cls, v: Vocabulary) -> SupplierVocabularyOut:
        groups: dict[str, list[SupplierVocabularyEntryOut]] = {}
        for k in v.keys:
            groups.setdefault(k.group.value.replace("_", " ").title(), []).append(
                SupplierVocabularyEntryOut(key=k.key, label=k.label, description=k.description)
            )
        return cls(
            groups=[SupplierVocabularyGroupOut(group=g, entries=e) for g, e in groups.items()],
            other=MeaningOut(key=OTHER_KEY, label="Something else / I don't know"),
        )


class AiAvailabilityOut(_Out):
    available: bool
    fields_to_clarify: int  # champs que « Ask AI » examinerait maintenant
    ai_suggestions: int  # questions ouvertes portant une suggestion de l'IA


class AskAiOut(_Out):
    examined: int
    suggestions: int
    no_suggestion: int
    failed: int


def ai_availability(s: AiStatus) -> AiAvailabilityOut:
    return AiAvailabilityOut(
        available=s.configured,
        fields_to_clarify=s.candidates if s.configured else 0,
        ai_suggestions=s.ai_proposed,
    )


def ask_ai(r: AiEnrichmentResult) -> AskAiOut:
    return AskAiOut(
        examined=r.targets,
        suggestions=r.proposed + r.low_confidence,
        no_suggestion=r.no_confident_match,
        failed=r.errors,
    )


# --------------------------------------------------------------------------- #
# Catalogue réel (V2a) : produits canoniques, jamais d'identifiant ni de nom du système source
# --------------------------------------------------------------------------- #
ProductStatus = Literal["active", "archived"]


def _status(active: bool | None) -> ProductStatus:
    return "archived" if active is False else "active"


class PriceOut(_Out):
    amount: float
    currency: str | None


class CatalogSyncOut(_Out):
    status: Literal["running", "succeeded", "failed"]
    started_at: datetime
    finished_at: datetime | None
    products: int
    variants: int
    categories: int
    images: int
    partial: bool  # plafond atteint : seule une partie du catalogue a été lue
    message: str | None

    @classmethod
    def of(cls, r: CatalogSyncRecord) -> CatalogSyncOut:
        return cls(
            status=r.status.lower(),  # type: ignore[arg-type]
            started_at=r.started_at,
            finished_at=r.finished_at,
            products=r.products,
            variants=r.variants,
            categories=r.categories,
            images=r.images,
            partial=r.truncated,
            message=r.error,
        )


class ProductListItem(_Out):
    id: uuid.UUID
    system_id: uuid.UUID
    name: str | None
    sku: str | None
    category: str | None
    price: PriceOut | None
    status: ProductStatus
    has_image: bool
    variant_count: int

    @classmethod
    def of(cls, r: CatalogProductRecord) -> ProductListItem:
        return cls(
            id=r.id,
            system_id=r.instance_id,
            name=r.name,
            sku=r.sku,
            category=r.category,
            price=PriceOut(amount=r.list_price, currency=r.currency) if r.list_price is not None else None,
            status=_status(r.active),
            has_image=r.has_image,
            variant_count=r.variant_count,
        )


class ProductPageOut(_Out):
    total: int
    page: int
    page_size: int
    items: list[ProductListItem]

    @classmethod
    def of(cls, p: ProductPage) -> ProductPageOut:
        return cls(
            total=p.total, page=p.page, page_size=p.page_size, items=[ProductListItem.of(r) for r in p.items]
        )


class VariantAttributeOut(_Out):
    name: str | None
    value: str


class VariantOut(_Out):
    id: uuid.UUID
    label: str  # « Black · Standard » (attributs) sinon SKU
    sku: str | None
    barcode: str | None
    price: PriceOut | None
    status: ProductStatus
    weight: float | None
    attributes: list[VariantAttributeOut]
    has_image: bool


class ProductAttributeOut(_Out):
    name: str
    values: list[str]


class SupplierOfferOut(_Out):
    supplier: str | None
    supplier_reference: str | None
    price: PriceOut | None
    min_quantity: float | None
    lead_time_days: float | None


class ExtraAttributeOut(_Out):
    label: str
    value: str | int | float | bool | None


class ProductDetailOut(_Out):
    id: uuid.UUID
    system_id: uuid.UUID
    name: str | None
    sku: str | None
    barcode: str | None
    category: str | None
    price: PriceOut | None
    description: str | None
    weight: float | None
    volume: float | None
    unit: str | None
    product_type: str | None
    status: ProductStatus
    has_image: bool
    variants: list[VariantOut]
    attributes: list[ProductAttributeOut]
    supplier_offers: list[SupplierOfferOut]
    additional_information: list[ExtraAttributeOut]
    synced_at: datetime

    @classmethod
    def of(cls, r: CatalogProductRecord, p: CatalogProduct) -> ProductDetailOut:
        def price(m: Money | None) -> PriceOut | None:
            return PriceOut(amount=m.amount, currency=m.currency) if m else None

        return cls(
            id=r.id,
            system_id=r.instance_id,
            name=p.name,
            sku=p.sku,
            barcode=p.barcode,
            category=p.category,
            price=price(p.price),
            description=p.description,
            weight=p.weight,
            volume=p.volume,
            unit=p.unit,
            product_type=p.product_type,
            status=_status(p.active),
            has_image=p.has_image,
            variants=[
                VariantOut(
                    id=v.id,
                    label=" · ".join(a.value for a in v.attributes) or v.sku or v.name or "Variant",
                    sku=v.sku,
                    barcode=v.barcode,
                    price=price(v.price),
                    status=_status(v.active),
                    weight=v.weight,
                    attributes=[VariantAttributeOut(name=a.name, value=a.value) for a in v.attributes],
                    has_image=v.has_image,
                )
                for v in p.variants
            ],
            attributes=[ProductAttributeOut(name=a.name, values=a.values) for a in p.attributes],
            supplier_offers=[
                SupplierOfferOut(
                    supplier=o.supplier,
                    supplier_reference=o.supplier_reference,
                    price=price(o.price),
                    min_quantity=o.min_quantity,
                    lead_time_days=o.lead_time_days,
                )
                for o in p.supplier_offers
            ],
            additional_information=[
                ExtraAttributeOut(
                    label=x.label,
                    value=x.value if isinstance(x.value, str | int | float | bool) else str(x.value),
                )
                for x in p.extra
            ],
            synced_at=r.updated_at,
        )
