"""Doublons de pièces comptables ENTRE SOURCES pour un même format cible (étape 1, sans migration).

Une même facture peut arriver par plusieurs sources (ex. saisie dans un système, transmise à un autre, puis
lue dans les deux). Le registre d'export par pièce est par source : ce module le complète SANS le modifier,
en n'ajoutant que des blocages à la préparation (aperçu et fichier). Aucun nom de système.

Ordre des preuves :
1. FILIATION certaine : un lien d'envoi relie la pièce à une pièce d'une autre source (dans un sens ou dans
   l'autre). Déjà exportée vers ce format depuis l'autre source → BLOQUÉE ; pas encore → avertissement
   (« une seule source »).
2. Comparaison MÉTIER FORTE avec les pièces DÉJÀ exportées vers ce format depuis une autre source (relues à la
   demande) : même type, devise, montant TTC, TVA, date ET un numéro commun (numéro ou référence) → BLOQUÉE ;
   mêmes valeurs et même partenaire mais aucun numéro commun → doublon POSSIBLE, BLOQUÉ. Jamais le nom seul.
3. Fichiers d'AVANT le suivi par pièce (« legacy », import dans le système cible inconnu) couvrant la
   période : pièces citées par le fichier (même source) ou toute la période (autre source) → BLOQUÉES, avec
   la marche à suivre (adopter le fichier depuis sa source).
Une source qu'on ne peut pas relire → vérification impossible → candidates bloquées (jamais un doublon
silencieux)."""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session
from uie_cdm import AccountingDocument

from uie_core.delivery.records import TargetResourceLink
from uie_core.exports.documents import DocumentExportLedger
from uie_core.exports.records import ExportDocumentRecord, ExportProfileRecord, ExportRunRecord
from uie_core.mapping import IssueLevel, TargetIssue
from uie_core.registry import models as m

RULE = "CROSS_SOURCE_DOCUMENT"


def _numbers(doc: AccountingDocument) -> set[str]:
    """Numéros connus d'une pièce, normalisés (alphanumérique, majuscules) : numéro et référence externe."""
    out = set()
    for v in (doc.number, doc.reference):
        n = re.sub(r"[^0-9A-Z]", "", (v or "").upper())
        if len(n) >= 3:
            out.add(n)
    return out


def _name(v: str) -> str:
    return re.sub(r"\s+", " ", v.casefold()).strip()


@dataclass(frozen=True)
class BusinessKey:
    """Identité métier d'une pièce, indépendante de la source (jamais un nom seul)."""

    document_type: str
    currency: str
    total: Decimal
    tax: Decimal
    day: date
    numbers: frozenset[str]
    partner: str

    @classmethod
    def of(cls, doc: AccountingDocument) -> BusinessKey:
        return cls(
            document_type=doc.document_type.value,
            currency=doc.currency,
            total=doc.total_amount,
            tax=doc.tax_amount,
            day=doc.document_date or doc.accounting_date,
            numbers=frozenset(_numbers(doc)),
            partner=_name(doc.partner.name) if doc.partner else "",
        )

    def same_values(self, other: BusinessKey) -> bool:
        return (self.document_type, self.currency, self.total, self.tax, self.day) == (
            other.document_type,
            other.currency,
            other.total,
            other.tax,
            other.day,
        )


@dataclass(frozen=True)
class Exported:
    """Pièce déjà exportée vers le format depuis une autre source."""

    source: str  # nom du système source (affichage)
    number: str | None
    key: BusinessKey | None


def _blocking(message: str) -> TargetIssue:
    return TargetIssue(level=IssueLevel.BLOCKING, message=message, rule_id=RULE)


class CrossSourceGuard:
    """Problèmes « entre sources » de chaque pièce d'une préparation : {référence source → problèmes}."""

    def __init__(
        self,
        session: Session,
        read: Callable[[uuid.UUID, str, object], list[tuple[str, AccountingDocument]]],
    ) -> None:
        # `read(instance, company, period)` → [(référence, pièce)] d'une AUTRE source (lecture à la demande)
        self.s = session
        self.read = read
        self.ledger = DocumentExportLedger(session)

    def check(
        self,
        *,
        organization_id: uuid.UUID,
        instance_id: uuid.UUID,
        domain: str,
        target_system: str,
        target_product: str,
        period: object,
        documents: dict[str, AccountingDocument],
    ) -> dict[str, list[TargetIssue]]:
        if not documents:
            return {}
        names = {
            i.id: i.name
            for i in self.s.scalars(select(m.Instance).where(m.Instance.organization_id == organization_id))
        }
        exported = self._exported(organization_id, domain, target_system, target_product)
        out: dict[str, list[TargetIssue]] = {}

        # 1. filiation certaine (liens d'envoi, dans les deux sens)
        related = self._related(organization_id, domain, instance_id, set(documents))
        for ref, others in related.items():
            for other_inst, other_ref in others:
                hit = exported.get((other_inst, other_ref))
                who = names.get(other_inst, "another system")
                if hit is not None:
                    out.setdefault(ref, []).append(
                        _blocking(
                            f"Same document as {hit.number or other_ref} in {who}, already exported from "
                            "there: not exported twice"
                        )
                    )
                else:
                    out.setdefault(ref, []).append(
                        TargetIssue(
                            level=IssueLevel.WARNING,
                            message=f"Same document as {who}: export it from one system only",
                            rule_id=RULE,
                        )
                    )

        # 2. comparaison métier forte avec les pièces exportées depuis les AUTRES sources
        others = {i for (i, _) in exported if i != instance_id}
        known: list[Exported] = []
        unreadable: list[str] = []
        for other in sorted(others, key=str):
            refs = {r for (i, r) in exported if i == other}
            companies = {self._company(other, target_system, target_product, r) for r in refs} - {None}
            try:
                for company in sorted(companies):
                    for r, doc in self.read(other, company, period):
                        if r in refs:
                            known.append(
                                Exported(names.get(other, "another system"), doc.number, BusinessKey.of(doc))
                            )
            except Exception:  # noqa: BLE001 — source illisible : vérification impossible, signalée
                unreadable.append(names.get(other, "another system"))
        for ref, doc in documents.items():
            if any(i.level == IssueLevel.BLOCKING for i in out.get(ref, [])):
                continue
            key = BusinessKey.of(doc)
            strong = next(
                (e for e in known if e.key and e.key.same_values(key) and e.key.numbers & key.numbers), None
            )
            possible = next(
                (e for e in known if e.key and e.key.same_values(key) and e.key.partner == key.partner),
                None,
            )
            if strong:
                out.setdefault(ref, []).append(
                    _blocking(
                        f"Already exported from {strong.source} as {strong.number} (same number, amount and "
                        "date): not exported twice"
                    )
                )
            elif possible:
                out.setdefault(ref, []).append(
                    _blocking(
                        f"Possible duplicate of {possible.number} exported from {possible.source} "
                        "(same partner, amount and date, different number): not exported until this is "
                        "clarified"
                    )
                )
            elif unreadable:
                out.setdefault(ref, []).append(
                    _blocking(f"Duplicate check not possible: {', '.join(unreadable)} could not be read")
                )

        # 3. fichiers d'avant le suivi par pièce (import inconnu) couvrant la période
        for run, cited in self._legacy(organization_id, target_system, target_product, str(period)):
            when = run.created_at.strftime("%Y-%m-%d")
            if run.instance_id == instance_id:
                message = (
                    f"Already in a file generated on {when} before document tracking (import unknown): "
                    "adopt that file first"
                )
                targets = [r for r in documents if r in cited]
            else:
                who = names.get(run.instance_id, "another system") if run.instance_id else "a removed system"
                message = (
                    f"A file for this period was generated from {who} on {when} before document tracking "
                    f"(import unknown): adopt it in {who} first"
                )
                targets = list(documents)
            for ref in targets:
                out.setdefault(ref, []).append(_blocking(message))
        return out

    # ------------------------------------------------------------------ #
    def _exported(
        self, organization_id: uuid.UUID, domain: str, system: str, product: str
    ) -> dict[tuple[uuid.UUID, str], Exported]:
        """Pièces déjà exportées vers ce format (registre par pièce), toutes sources de l'organisation."""
        rows = self.s.scalars(
            select(ExportDocumentRecord).where(
                ExportDocumentRecord.organization_id == organization_id,
                ExportDocumentRecord.domain == domain,
                ExportDocumentRecord.target_system == system,
                ExportDocumentRecord.target_product == product,
                ExportDocumentRecord.instance_id.is_not(None),
            )
        )
        return {(r.instance_id, r.source_ref): Exported("", r.source_number, None) for r in rows}

    def _company(self, instance_id: uuid.UUID, system: str, product: str, ref: str) -> str | None:
        return self.s.scalar(
            select(ExportDocumentRecord.scope_ref).where(
                ExportDocumentRecord.instance_id == instance_id,
                ExportDocumentRecord.target_system == system,
                ExportDocumentRecord.target_product == product,
                ExportDocumentRecord.source_ref == ref,
            )
        )

    def _related(
        self, organization_id: uuid.UUID, domain: str, instance_id: uuid.UUID, refs: set[str]
    ) -> dict[str, list[tuple[uuid.UUID, str]]]:
        """Pièces d'AUTRES sources reliées par un lien d'envoi : envoyées vers cette source, ou envoyées
        depuis elle (la copie est alors une pièce de l'autre système)."""
        out: dict[str, list[tuple[uuid.UUID, str]]] = {}
        incoming = self.s.scalars(
            select(TargetResourceLink).where(
                TargetResourceLink.organization_id == organization_id,
                TargetResourceLink.domain == domain,
                TargetResourceLink.target_instance_id == instance_id,
                TargetResourceLink.target_id.in_(refs),
            )
        )
        for link in incoming:
            out.setdefault(link.target_id, []).append((link.source_instance_id, link.source_ref))
        outgoing = self.s.scalars(
            select(TargetResourceLink).where(
                TargetResourceLink.organization_id == organization_id,
                TargetResourceLink.domain == domain,
                TargetResourceLink.source_instance_id == instance_id,
                TargetResourceLink.source_ref.in_(refs),
            )
        )
        for link in outgoing:
            out.setdefault(link.source_ref, []).append((link.target_instance_id, link.target_id))
        return out

    def _legacy(
        self, organization_id: uuid.UUID, system: str, product: str, period: str
    ) -> list[tuple[ExportRunRecord, set[str]]]:
        """Fichiers de ce format pour la période, générés AVANT le suivi par pièce (aucune ligne de registre)
        et non adoptés : leur import dans le système cible est inconnu."""
        runs = list(
            self.s.scalars(
                select(ExportRunRecord)
                .join(ExportProfileRecord, ExportProfileRecord.id == ExportRunRecord.profile_id)
                .where(
                    ExportRunRecord.organization_id == organization_id,
                    ExportRunRecord.period == period,
                    ExportProfileRecord.target_system == system,
                    ExportProfileRecord.target_product == product,
                )
                .order_by(ExportRunRecord.created_at)
            )
        )
        tracked = self.ledger.recorded_runs([r.id for r in runs])
        return [
            (r, {x["ref"] for x in (r.details or {}).get("documents") or [] if x.get("ref")})
            for r in runs
            if r.id not in tracked
        ]
