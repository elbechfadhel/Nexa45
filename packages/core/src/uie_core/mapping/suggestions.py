"""Tables de comptes d'usage par CATÉGORIE d'une source sans plan comptable (ex. « Kostenart » d'un
journal de factures d'achat), pour un format cible et un plan comptable du dossier. DONNÉES versionnées
(domain packs), marquées « usage courant, à valider » : elles ne servent qu'à PRÉSÉLECTIONNER la réponse à une
question, jamais à décider seules. Une catégorie absente de la table n'est jamais devinée (la question reste
sans réponse).

Les opérations avec l'étranger (TVA : livraison intracommunautaire, exportation, autoliquidation) ont
leur propre table (`kind: vat_accounts`) : compte d'usage par sens, ZONE du partenaire (UE avec numéro de TVA
valide, hors UE) et NATURE (marchandise, service), pour des catégories `vat:<sens>:<zone>:<nature>:<taux>`.

Aucun nom de système ici : la table nomme sa cible (système, format) et son plan comptable."""

from __future__ import annotations

import hashlib
import re
from decimal import Decimal
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from uie_core.mapping.binding import TargetProductRef


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


KEY_MAX = 60  # au-delà : début du nom + empreinte stable (la référence tient dans une colonne de 120)


def category_key(name: str) -> str:
    """Forme comparable d'un nom de catégorie : minuscules, mots séparés par un tiret (« Telefon/Internet » →
    « telefon-internet ») ; un nom très long garde son début et une empreinte stable (jamais deux catégories
    confondues)."""
    key = "-".join(re.findall(r"[^\W_]+", name.casefold()))
    if len(key) <= KEY_MAX:
        return key
    return f"{key[: KEY_MAX - 9]}-{hashlib.sha256(key.encode()).hexdigest()[:8]}"


class CategoryAccount(_Spec):
    """Noms d'une catégorie (toutes les graphies courantes) et son compte d'usage à ce taux de TVA."""

    names: list[str] = Field(min_length=1)
    rate: Decimal
    account: str = Field(pattern=r"^\d+$")


class CategoryAccountTable(_Spec):
    kind: Literal["category_accounts"]
    table_version: str
    target: TargetProductRef
    chart: str  # plan comptable du dossier (valeur du paramètre du binding, ex. « 03 »)
    scope: Literal["SALE", "PURCHASE"]
    status: str  # ex. « usage courant, à valider par le Steuerberater » : affiché avec la présélection
    categories: list[CategoryAccount]
    # comptes AUTOMATIQUES : la cible calcule la taxe elle-même, aucune clé de taxe n'y est jamais écrite
    automatic_accounts: list[str] = []
    # taux → clé de taxe d'usage (présélection) ; un taux absent n'a pas de clé présélectionnée ; 0 % : aucune
    tax_keys: dict[str, str] = {}

    def tax_key_for(self, rate: Decimal) -> str | None:
        return self.tax_keys.get(f"{rate.normalize():f}")

    def account_for(self, name: str, rate: Decimal) -> str | None:
        """Compte d'usage de cette catégorie à ce taux ; None : inconnue (jamais devinée)."""
        key = category_key(name)
        for c in self.categories:
            if c.rate == rate and key in {category_key(n) for n in c.names}:
                return c.account
        return None


VAT_PREFIX = "vat"
Zone = Literal["EU", "NON_EU"]
Nature = Literal["GOODS", "SERVICES"]
Scope = Literal["SALE", "PURCHASE"]


def vat_category_ref(scope: str, zone: str, nature: str, rate: Decimal) -> str:
    """Référence de la catégorie d'une opération avec l'étranger (« vat:sale:eu:goods:0 ») : le taux en
    dernier, comme toute catégorie."""
    return f"{VAT_PREFIX}:{scope.lower()}:{zone.lower()}:{nature.lower()}:{rate.normalize():f}"


def parse_vat_ref(ref: str) -> tuple[str, str, str, Decimal] | None:
    """(sens, zone, nature, taux) d'une catégorie d'opération avec l'étranger ; None : autre catégorie."""
    parts = ref.split(":")
    if len(parts) != 5 or parts[0] != VAT_PREFIX:
        return None
    return parts[1].upper(), parts[2].upper(), parts[3].upper(), Decimal(parts[4])


class VatAccount(_Spec):
    scope: Scope
    zone: Zone
    supply_kind: Nature
    account: str = Field(pattern=r"^\d+$")


class VatAccountTable(_Spec):
    """Comptes d'usage des opérations avec l'étranger pour un format cible et un plan comptable."""

    kind: Literal["vat_accounts"]
    table_version: str
    target: TargetProductRef
    chart: str
    status: str  # « à valider par le Steuerberater » : affiché avec chaque présélection
    accounts: list[VatAccount]
    # comptes AUTOMATIQUES (taxe due et déductible calculées par la cible) : jamais de clé de taxe
    automatic_accounts: list[str] = []

    def account_for(self, scope: str, zone: str, nature: str) -> str | None:
        """Compte d'usage ; None : opération absente de la table (jamais devinée)."""
        return next(
            (a.account for a in self.accounts if (a.scope, a.zone, a.supply_kind) == (scope, zone, nature)),
            None,
        )


class VatAccountRegistry:
    """Tables des opérations avec l'étranger, par (système, format, plan comptable)."""

    _tables: ClassVar[dict[tuple[str, str, str], VatAccountTable]] = {}

    @classmethod
    def register(cls, table: VatAccountTable) -> VatAccountTable:
        cls._tables[(table.target.system, table.target.product, table.chart)] = table
        return table

    @classmethod
    def find(cls, system: str, product: str, chart: str | None) -> VatAccountTable | None:
        return cls._tables.get((system, product, chart)) if chart else None


def automatic_accounts(system: str, product: str, chart: str | None, scope: str) -> list[str]:
    """Comptes automatiques du plan comptable du dossier (catégories et opérations avec l'étranger)."""
    found = CategoryAccountRegistry.find(system, product, chart, scope)
    vat = VatAccountRegistry.find(system, product, chart)
    return list(
        dict.fromkeys(
            [*(found.automatic_accounts if found else []), *(vat.automatic_accounts if vat else [])]
        )
    )


class CategoryAccountRegistry:
    """Tables découvertes dans les domain packs, par (système, format, plan comptable, sens)."""

    _tables: ClassVar[dict[tuple[str, str, str, str], CategoryAccountTable]] = {}

    @classmethod
    def register(cls, table: CategoryAccountTable) -> CategoryAccountTable:
        cls._tables[(table.target.system, table.target.product, table.chart, table.scope)] = table
        return table

    @classmethod
    def find(cls, system: str, product: str, chart: str | None, scope: str) -> CategoryAccountTable | None:
        if not chart:
            return None
        return cls._tables.get((system, product, chart, scope))


__all__ = [
    "CategoryAccount",
    "CategoryAccountRegistry",
    "CategoryAccountTable",
    "VatAccount",
    "VatAccountRegistry",
    "VatAccountTable",
    "automatic_accounts",
    "category_key",
    "parse_vat_ref",
    "vat_category_ref",
]
