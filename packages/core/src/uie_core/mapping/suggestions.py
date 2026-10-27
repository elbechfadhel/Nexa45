"""Tables de comptes d'usage par CATÉGORIE d'une source sans plan comptable (ex. « Kostenart » d'un
journal de factures d'achat), pour un format cible et un plan comptable du dossier. DONNÉES versionnées
(domain packs), marquées « usage courant, à valider » : elles ne servent qu'à PRÉSÉLECTIONNER la réponse à une
question, jamais à décider seules. Une catégorie absente de la table n'est jamais devinée (la question reste
sans réponse).

Les opérations avec l'étranger (TVA : livraison intracommunautaire, exportation, autoliquidation) ont
leur propre table (`kind: vat_accounts`) : compte d'usage par sens, ZONE du partenaire (UE avec numéro de TVA
valide, hors UE) et NATURE (marchandise, service), pour des catégories `vat:<sens>:<zone>:<nature>:<taux>`.

Un livre de caisse a la sienne (`kind: cash_accounts`) : compte de la caisse, compte de transit et compte de
chaque catégorie de caisse par taux (ou sans TVA), pour des catégories `cash:<catégorie>[:<taux>]`.

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


CASH_PREFIX = "cash"


def cash_category_ref(name: str, rate: Decimal | None) -> str:
    """Référence d'une catégorie de caisse : « cash:<catégorie>:<taux> », sans TVA « cash:<catégorie> »."""
    key = category_key(name)
    return f"{CASH_PREFIX}:{key}" if rate is None else f"{CASH_PREFIX}:{key}:{rate.normalize():f}"


def parse_cash_ref(ref: str) -> tuple[str, Decimal | None] | None:
    """(catégorie, taux ou None sans TVA) d'une catégorie de caisse ; None : autre catégorie."""
    parts = ref.split(":")
    if parts[0] != CASH_PREFIX or len(parts) not in (2, 3):
        return None
    return parts[1], Decimal(parts[2]) if len(parts) == 3 else None


class CashCategory(_Spec):
    """Noms d'une catégorie de caisse, son taux (ou `no_vat` : sans TVA) et son compte d'usage (absent :
    jamais présélectionné)."""

    names: list[str] = Field(min_length=1)
    rate: Decimal | None = None
    no_vat: bool = False
    account: str | None = Field(default=None, pattern=r"^\d+$")
    note: str | None = None  # pourquoi le compte n'est pas présélectionné (affiché avec la question)


class CashAccountTable(_Spec):
    """Comptes d'usage d'un livre de caisse pour un format cible et un plan comptable."""

    kind: Literal["cash_accounts"]
    table_version: str
    target: TargetProductRef
    chart: str
    status: str  # « à valider par le Steuerberater » : affiché avec chaque présélection
    cash_account: str = Field(pattern=r"^\d+$")
    transit_account: str | None = Field(default=None, pattern=r"^\d+$")
    categories: list[CashCategory]
    automatic_accounts: list[str] = []
    tax_keys: dict[str, str] = {}  # dépense : taux → clé de taxe d'usage

    def _find(self, name: str) -> list[CashCategory]:
        key = category_key(name)
        return [c for c in self.categories if key in {category_key(n) for n in c.names}]

    def account_for(self, name: str, rate: Decimal | None) -> str | None:
        """Compte d'usage de cette catégorie à ce taux (None : sans TVA) ; None : inconnu, jamais deviné."""
        for c in self._find(name):
            if (rate is None and c.no_vat) or (rate is not None and c.rate == rate):
                return c.account
        return None

    def note_for(self, name: str) -> str | None:
        """Note de la table sur cette catégorie (ex. compte selon la forme juridique)."""
        return next((c.note for c in self._find(name) if c.note), None)

    def without_vat(self, name: str) -> bool:
        """Catégorie déclarée SANS TVA (un taux vide est admis pour elle, et seulement pour elle)."""
        return any(c.no_vat for c in self._find(name))

    def tax_key_for(self, rate: Decimal) -> str | None:
        return self.tax_keys.get(f"{rate.normalize():f}")


class CashAccountRegistry:
    """Tables des livres de caisse, par (système, format, plan comptable)."""

    _tables: ClassVar[dict[tuple[str, str, str], CashAccountTable]] = {}

    @classmethod
    def register(cls, table: CashAccountTable) -> CashAccountTable:
        cls._tables[(table.target.system, table.target.product, table.chart)] = table
        return table

    @classmethod
    def find(cls, system: str, product: str, chart: str | None) -> CashAccountTable | None:
        return cls._tables.get((system, product, chart)) if chart else None


def automatic_accounts(
    system: str, product: str, chart: str | None, scope: str, cash: bool = False
) -> list[str]:
    """Comptes automatiques du plan comptable du dossier (catégories, opérations avec l'étranger ; ceux de la
    table de la caisse seulement pour une écriture de caisse : `cash`)."""
    found = CategoryAccountRegistry.find(system, product, chart, scope)
    vat = VatAccountRegistry.find(system, product, chart)
    found_cash = CashAccountRegistry.find(system, product, chart) if cash else None
    return list(
        dict.fromkeys(
            [
                *(found.automatic_accounts if found else []),
                *(vat.automatic_accounts if vat else []),
                *(found_cash.automatic_accounts if found_cash else []),
            ]
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
    "CashAccountRegistry",
    "CashAccountTable",
    "CashCategory",
    "CategoryAccount",
    "CategoryAccountRegistry",
    "CategoryAccountTable",
    "VatAccount",
    "VatAccountRegistry",
    "VatAccountTable",
    "automatic_accounts",
    "cash_category_ref",
    "category_key",
    "parse_cash_ref",
    "parse_vat_ref",
    "vat_category_ref",
]
