"""Tables de comptes d'usage par CATÉGORIE d'une source sans plan comptable (ex. « Kostenart » d'un
journal de factures d'achat), pour un format cible et un plan comptable du dossier. DONNÉES versionnées
(domain packs), marquées « usage courant, à valider » : elles ne servent qu'à PRÉSÉLECTIONNER la réponse à une
question, jamais à décider seules. Une catégorie absente de la table n'est jamais devinée (la question reste
sans réponse).

Aucun nom de système ici : la table nomme sa cible (système, format) et son plan comptable."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from uie_core.mapping.binding import TargetProductRef


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def category_key(name: str) -> str:
    """Forme comparable d'un nom de catégorie : minuscules, mots séparés par un tiret (« Telefon/Internet » →
    « telefon-internet »)."""
    return "-".join(re.findall(r"[^\W_]+", name.casefold()))


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


__all__ = ["CategoryAccount", "CategoryAccountRegistry", "CategoryAccountTable", "category_key"]
