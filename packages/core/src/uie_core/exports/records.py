"""Tables des exports vers un format cible (PARTNER → fichier).

Aucune donnée de partenaire (nom, adresse, banque…) n'est stockée : seulement des données TECHNIQUES.

`export_profiles`              paramètres d'export d'une organisation pour un format (valeurs validées par le
                               binding et les motifs du Registry), un profil par (organisation, format).
`export_account_assignments`   numéro attribué à (profil, système source, référence source opaque, rôle) ;
                               jamais réutilisé : unique par profil ; système supprimé → la ligne reste
                               (instance_id NULL) et garde son numéro réservé.
`export_runs`                  journal d'audit d'une génération : qui, quand, format, compteurs, empreinte
                               du fichier ; jamais son contenu.
`export_value_mappings`        correspondances de valeurs décidées (compte, taxe) pour un format et un
                               système source (ACCOUNTING → ex. Buchungsstapel).
`export_document_records`      historique IMMUABLE des documents écrits dans un fichier OU envoyés à un
                               système cible par API (D11) : `export_run_id` XOR `send_item_id`.
`export_document_releases`     autorisation explicite de réexporter un document modifié depuis son export.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from uie_core.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, utcnow

_UUID = UUID(as_uuid=True)


class ExportProfileRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Réglages d'un format cible pour UN dossier de l'organisation (`client_key`) : un cabinet a plusieurs
    mandants, chacun avec ses réglages (Beraternummer, Mandantennummer…), ses numéros de compte et son
    historique d'export. « default » : le dossier unique d'une organisation qui n'en a qu'un."""

    __tablename__ = "export_profiles"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "target_system",
            "target_product",
            "client_key",
            name="uq_export_profiles_client",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_model: Mapped[str] = mapped_column(String(50), nullable=False)  # ex. partner
    target_system: Mapped[str] = mapped_column(String(80), nullable=False)
    target_product: Mapped[str] = mapped_column(String(80), nullable=False)
    # dossier (mandant) de l'organisation : clé courte stable, choisie par l'organisation
    client_key: Mapped[str] = mapped_column(
        String(60), nullable=False, default="default", server_default="default"
    )
    values: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class ExportAccountAssignmentRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "export_account_assignments"
    __table_args__ = (
        UniqueConstraint("profile_id", "account_number"),
        UniqueConstraint("profile_id", "instance_id", "source_ref", "role"),
    )

    profile_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("export_profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    instance_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("instances.id", ondelete="SET NULL"), index=True
    )
    source_ref: Mapped[str] = mapped_column(String(120), nullable=False)  # identifiant opaque de la source
    role: Mapped[str] = mapped_column(String(30), nullable=False)
    account_number: Mapped[str] = mapped_column(String(30), nullable=False)
    origin: Mapped[str] = mapped_column(String(20), nullable=False)  # ASSIGNED | REUSED | OFFSET
    export_run_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("export_runs.id", ondelete="SET NULL")
    )
    # source FICHIER : état du numéro de TVA du partenaire au dernier export (valid | invalid | missing) et
    # son pays (ISO), pour justifier une livraison intracommunautaire à 0 % ; jamais le numéro lui-même
    vat_status: Mapped[str | None] = mapped_column(String(10))
    vat_country: Mapped[str | None] = mapped_column(String(2))
    # source FICHIER : nom et IBAN du partenaire de la liste exportée, pour reconnaître le payeur d'un relevé
    # bancaire (rapprochement) ; voir docs/PROTECTION_DES_DONNEES_REGISTRE_DES_FACTURES.md
    partner_name: Mapped[str | None] = mapped_column(String(200))
    iban: Mapped[str | None] = mapped_column(String(34))


class ExportRunRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "export_runs"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    profile_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("export_profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    instance_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("instances.id", ondelete="SET NULL"), index=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(_UUID)  # utilisateur (None : CLI / système)
    target_label: Mapped[str] = mapped_column(String(200), nullable=False)  # format + version (libellé)
    target_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("schema_snapshots.id", ondelete="SET NULL")
    )
    binding_version: Mapped[str] = mapped_column(String(30), nullable=False)
    read_plan_checksum: Mapped[str | None] = mapped_column(String(64))
    encoding: Mapped[str] = mapped_column(String(20), nullable=False)
    records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # lignes écrites
    warnings: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    blocked: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # non écrits
    new_accounts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    # export d'une période (ex. pièces comptables d'un mois) : clé de recherche + audit léger (société,
    # version du format, compteurs, numéros des pièces et rangs de leurs lignes) ; jamais de montant ni de
    # contenu du fichier
    period: Mapped[str | None] = mapped_column(String(7), index=True)  # AAAA-MM
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class ExportValueMappingRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Correspondance de VALEUR décidée explicitement pour un format cible : (organisation, format, système
    source, sorte, référence source opaque) → valeur cible. Seules les décisions sont stockées (une suggestion
    n'est jamais enregistrée comme un choix) ; `target_value` NULL = « aucune valeur » décidée (ex. aucune clé
    de taxe). Données de configuration, jamais des montants ni des données de partenaire. Source FICHIER :
    aucune instance, la décision vaut pour le dossier (`client_key`, mandant) de l'organisation."""

    __tablename__ = "export_value_mappings"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "target_system",
            "target_product",
            "instance_id",
            "kind",
            "source_ref",
            name="uq_export_value_mappings_scope",  # nom explicite : le nom généré dépasse 63 caractères
        ),
        # source fichier (sans instance) : une décision par dossier (mandant)
        Index(
            "uq_export_value_mappings_file_scope",
            "organization_id",
            "target_system",
            "target_product",
            "client_key",
            "kind",
            "source_ref",
            unique=True,
            postgresql_where=text("instance_id IS NULL"),
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_system: Mapped[str] = mapped_column(String(80), nullable=False)
    target_product: Mapped[str] = mapped_column(String(80), nullable=False)
    # NULL : source FICHIER (aucune instance)
    instance_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("instances.id", ondelete="CASCADE"), index=True
    )
    client_key: Mapped[str | None] = mapped_column(String(60))  # dossier (mandant) d'une source fichier
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # ACCOUNT | TAX | CATEGORY
    source_ref: Mapped[str] = mapped_column(String(120), nullable=False)  # identifiant opaque de la source
    source_code: Mapped[str | None] = mapped_column(String(120))  # libellé de la source au moment du choix
    source_name: Mapped[str | None] = mapped_column(String(200))
    target_value: Mapped[str | None] = mapped_column(String(60))  # NULL = « aucune valeur » (décision)
    origin: Mapped[str] = mapped_column(String(30), nullable=False)  # MANUAL | SUGGESTION_ACCEPTED
    suggestion_origin: Mapped[str | None] = mapped_column(String(30))  # ex. IDENTICAL_CODE (si acceptée)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(_UUID)  # utilisateur (None : CLI / système)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class ExportDocumentRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Fait d'audit IMMUABLE : un document source a été écrit dans un fichier généré (`export_run_id`), avec
    l'empreinte de son contenu cible à ce moment. Jamais modifié ni remplacé : un nouvel export du même
    document (après changement autorisé, ou doublon volontaire) ajoute une ligne. Identité = organisation +
    système source + domaine + format cible + référence source OPAQUE (jamais le numéro lisible seul).
    `field_digests` : empreinte de chaque valeur de chaque enregistrement cible (pour dire QUELS champs ont
    changé) ; aucune valeur, aucun montant n'est stocké."""

    __tablename__ = "export_document_records"
    __table_args__ = (
        # un document est prouvé exporté par UN fichier généré OU par UN envoi par API, jamais les deux
        CheckConstraint("num_nonnulls(export_run_id, send_item_id) = 1", name="export_or_send"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    instance_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("instances.id", ondelete="SET NULL"), index=True
    )
    domain: Mapped[str] = mapped_column(String(30), nullable=False)  # ex. accounting
    target_system: Mapped[str] = mapped_column(String(80), nullable=False)
    target_product: Mapped[str] = mapped_column(String(80), nullable=False)
    scope_ref: Mapped[str | None] = mapped_column(String(120))  # sous-ensemble de la source (ex. société)
    # dossier (mandant) cible de l'organisation pour une source FICHIER : deux mandants ne se voient jamais ;
    # NULL : historique d'une source connectée (portée inchangée)
    client_key: Mapped[str | None] = mapped_column(String(60), index=True)
    source_ref: Mapped[str] = mapped_column(String(120), nullable=False, index=True)  # référence opaque
    source_number: Mapped[str | None] = mapped_column(String(120))  # numéro lisible (affichage seulement)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    field_digests: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # enregistrements cible écrits
    # EXPORTED (premier export) | RELEASED (changement autorisé) | RE_EXPORTED (doublon volontaire) |
    # ADOPTED (fichier d'avant le suivi, prouvé identique)
    origin: Mapped[str] = mapped_column(String(20), nullable=False)
    previous_record_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID,
        # nom explicite : le nom généré dépasse 63 caractères
        ForeignKey(
            "export_document_records.id", ondelete="SET NULL", name="fk_export_document_records_previous"
        ),
    )
    export_run_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("export_runs.id", ondelete="CASCADE"), index=True
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(_UUID)  # utilisateur de la génération / adoption
    # Envoi par API (phase 1b) : instance CIBLE (NULL pour un fichier, qui n'a pas d'instance cible) et
    # élément d'envoi qui prouve l'écriture.
    target_instance_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("instances.id", ondelete="CASCADE"), index=True
    )
    send_item_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("target_send_items.id", ondelete="CASCADE"), index=True
    )


class ExportDocumentReleaseRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Décision HUMAINE explicite : un document modifié depuis son export peut être exporté de nouveau avec
    son contenu ACTUEL (`fingerprint`). Ne vaut que pour cette empreinte et ce précédent export : si le
    document change encore, il est de nouveau bloqué. L'historique n'est jamais modifié."""

    __tablename__ = "export_document_releases"
    __table_args__ = (
        UniqueConstraint("previous_record_id", "fingerprint", name="uq_export_document_releases"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    previous_record_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey(
            "export_document_records.id", ondelete="CASCADE", name="fk_export_document_releases_previous"
        ),
        nullable=False,
        index=True,
    )
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)  # contenu actuel autorisé
    decided_by: Mapped[uuid.UUID | None] = mapped_column(_UUID)
    note: Mapped[str | None] = mapped_column(String(300))


@event.listens_for(ExportDocumentRecord, "before_update")
def _export_document_records_are_immutable(mapper: Any, connection: Any, target: Any) -> None:  # noqa: ARG001
    raise ValueError(
        "export_document_records est un historique immuable : ajouter une ligne, jamais modifier"
    )


class ExportOpenItemRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Facture d'un dossier (source FICHIER) écrite dans un fichier : registre des factures OUVERTES (Offene
    Posten), seul moyen de rapprocher un paiement d'un relevé bancaire (montant ouvert, partenaire). Champs
    MINIMAUX (voir docs/PROTECTION_DES_DONNEES_REGISTRE_DES_FACTURES.md) ; supprimé avec le dossier (remise à
    zéro) ou l'organisation. Montant ouvert = `amount` − paiements rapprochés (table des règlements)."""

    __tablename__ = "export_open_items"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "client_key",
            "target_system",
            "target_product",
            "document_ref",
            name="uq_export_open_items_document",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_key: Mapped[str | None] = mapped_column(String(60), index=True)  # dossier (mandant)
    target_system: Mapped[str] = mapped_column(String(80), nullable=False)
    target_product: Mapped[str] = mapped_column(String(80), nullable=False)
    document_ref: Mapped[str] = mapped_column(String(120), nullable=False)  # référence opaque de la pièce
    number: Mapped[str] = mapped_column(String(120), nullable=False)  # numéro de la facture (lettrage)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # CUSTOMER | SUPPLIER
    partner_ref: Mapped[str] = mapped_column(String(120), nullable=False)  # référence du partenaire
    partner_name: Mapped[str | None] = mapped_column(String(200))  # nom écrit sur la facture
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)  # brut, signé (avoir < 0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    document_date: Mapped[date] = mapped_column(Date, nullable=False)
    export_run_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("export_runs.id", ondelete="SET NULL"), index=True
    )


class ExportOpenItemSettlementRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Paiement d'un relevé bancaire rapproché d'une facture du registre et écrit dans un fichier : le montant
    réglé (positif) diminue le montant ouvert. Un paiement ne règle une facture qu'une fois."""

    __tablename__ = "export_open_item_settlements"
    __table_args__ = (
        UniqueConstraint("open_item_id", "payment_ref", name="uq_export_open_item_settlements_payment"),
    )

    open_item_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("export_open_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    payment_ref: Mapped[str] = mapped_column(String(120), nullable=False)  # référence opaque du mouvement
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    settled_on: Mapped[date] = mapped_column(Date, nullable=False)
    export_run_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID, ForeignKey("export_runs.id", ondelete="SET NULL"), index=True
    )
