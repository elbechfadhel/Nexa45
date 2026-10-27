"""Source FICHIER (tableau d'un client : .xlsx, .csv) d'un domaine : DONNÉES qui disent quelles colonnes un
tel fichier peut apporter et ce qu'elles veulent dire.

Un fichier n'a ni schéma publié ni découverte : son sens vient de ses en-têtes. Le tableau de source d'un
domaine déclare les clés que le constructeur du CDM sait lire (`customer_number`, `gross_amount`…), leurs noms
courants (alias, toutes langues) et les listes de référence utiles à la construction (formes juridiques, noms
de pays, motifs des codes postaux et des numéros de TVA, taux de TVA admis). Il est indépendant des
vocabulaires des systèmes (aucune analyse sémantique ne change) et ne nomme aucun système. Les correspondances
de colonnes réutilisent le moteur des imports et du Wizard (`SemanticSpec`)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from uie_core.domains.model import DomainKey, FieldGroup
from uie_core.domains.semantics import SemanticSpec, Vocabulary, VocabularyEntry
from uie_core.registry.schema import DataType


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TableColumn(_Model):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    label: str
    description: str = ""
    data_type: DataType = DataType.STRING
    group: FieldGroup = FieldGroup.IDENTITY
    required: bool = False  # sans elle, aucun enregistrement ne peut être construit
    aliases: list[str] = []
    # types de données (libellés du tableau) qui ont cette colonne ; vide : tous
    data_types: list[str] = []
    # valeurs admises : valeur → graphies courantes (« Ware », « Lieferung » → goods) ; une autre valeur
    # n'est jamais devinée
    values: dict[str, list[str]] = {}

    def value_of(self, text: str | None) -> str | None:
        """Valeur admise d'une cellule (casse et espaces ignorés) ; None : vide ou inconnue."""
        found = " ".join((text or "").split()).casefold()
        if not found:
            return None
        for value, names in self.values.items():
            if found in {" ".join(n.split()).casefold() for n in names}:
                return value
        return None


class TaxRates(_Model):
    """Taux de TVA admis par pays à partir d'une date (« 16 % » en 2026 n'est plus un taux allemand)."""

    country: str = Field(pattern=r"^[A-Z]{2}$")
    since: date
    rates: list[Decimal]


class BankCategory(_Model):
    """Mouvement bancaire HORS factures reconnu par des mots du motif ou de la contrepartie (jamais par le
    montant) : frais, dépôt d'espèces, impôt… `per_counterparty` : son compte dépend de la contrepartie (un
    loyer par bailleur) ; `never_booked` : jamais écrit, toujours avec ce motif (versement des cartes)."""

    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    label: str
    words: list[str] = Field(min_length=1)
    per_counterparty: bool = False
    never_booked: str | None = None


class FileTableSpec(_Model):
    table_version: str
    domain: DomainKey
    # types de données (libellés du Wizard) que ce tableau décrit, ex. « Customers & suppliers »
    data_types: list[str]
    columns: list[TableColumn]
    # mots qui, en fin de nom, désignent une personne morale (GmbH, AG, SARL…) : jamais un nom de personne
    legal_forms: list[str] = []
    # nom de pays (toutes langues, ou code) → code ISO 3166-1 alpha-2
    countries: dict[str, str] = {}
    # pays → motif du code postal (« 01067 » en Allemagne, « 1060 » en Autriche)
    postal_codes: dict[str, str] = {}
    # pays → motif du numéro de TVA intracommunautaire (préfixe compris, sans espaces)
    vat_ids: dict[str, str] = {}
    # pays membres de l'UE (ISO) : seul un partenaire d'un autre pays membre a une USt-IdNr ; liste explicite,
    # jamais déduite des motifs
    eu_members: list[str] = []
    tax_rates: list[TaxRates] = []
    # texte d'une ligne d'OUVERTURE d'un livre de caisse (« Anfangsbestand ») : pas une écriture, son solde
    # est le point de départ du contrôle du solde
    opening_words: list[str] = []
    # mots d'un PAIEMENT PAR CARTE dans le texte d'une ligne de caisse (« EC-Karte ») : pas de l'argent
    # liquide ; lus dans le texte de l'écriture seulement, jamais dans une remarque libre
    card_words: list[str] = []
    # relevé bancaire : mouvements hors factures (par mots) ; mots d'un ESCOMPTE (Skonto) dans le motif d'un
    # paiement : jamais rapproché d'office
    bank_categories: list[BankCategory] = []
    discount_words: list[str] = []
    # pays du fichier quand aucun n'est donné (adresse sans pays, taux de TVA) ; None : jamais supposé
    home_country: str | None = None

    @model_validator(mode="after")
    def _unique(self) -> FileTableSpec:
        keys = [c.key for c in self.columns]
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        if dupes:
            raise ValueError(f"colonne en double : {dupes}")
        return self

    def column(self, key: str) -> TableColumn | None:
        return next((c for c in self.columns if c.key == key), None)

    def columns_for(self, data_type: str | None) -> list[TableColumn]:
        """Colonnes qu'un fichier de ce type peut apporter (toutes si `data_type` est None)."""
        return [c for c in self.columns if data_type is None or not c.data_types or data_type in c.data_types]

    def semantic_spec(self, keys: set[str] | None = None) -> SemanticSpec:
        """Vocabulaire des colonnes au format du moteur de correspondance (alias exacts, libellés, mots) ;
        `keys` : seulement ces colonnes (celles du type de données du fichier : un même alias, « Kategorie »,
        peut désigner une colonne différente selon le type)."""
        vocabulary = Vocabulary(
            domain_key=self.domain,
            version=f"table-{self.table_version}",
            keys=[
                VocabularyEntry(
                    key=c.key,
                    group=c.group,
                    label=c.label,
                    description=c.description or c.label,
                    data_type=c.data_type,
                    cardinality="single",
                    required_for_catalog=c.required,
                    aliases=c.aliases,
                )
                for c in self.columns
                if keys is None or c.key in keys
            ],
        )
        return SemanticSpec(
            domain_key=self.domain, version=vocabulary.version, vocabulary=vocabulary, rules=[]
        )

    def country_code(self, value: str | None) -> str | None:
        """« Deutschland », « D », « de » → DE ; inconnu → None (jamais deviné)."""
        if not value:
            return None
        text = " ".join(value.split()).casefold()
        for name, code in self.countries.items():
            if name.casefold() == text:
                return code
        return None

    def rates(self, country: str, when: date) -> list[Decimal] | None:
        """Taux de TVA admis dans ce pays à cette date ; None : inconnus (rien n'est vérifié)."""
        found = [t for t in self.tax_rates if t.country == country and t.since <= when]
        return max(found, key=lambda t: t.since).rates if found else None


class FileTableRegistry:
    """Tableaux de source fichier, un par domaine (découverts dans les domain packs)."""

    _tables: ClassVar[dict[DomainKey, FileTableSpec]] = {}

    @classmethod
    def register(cls, spec: FileTableSpec) -> FileTableSpec:
        cls._tables[spec.domain] = spec
        return spec

    @classmethod
    def get(cls, domain: DomainKey) -> FileTableSpec | None:
        return cls._tables.get(domain)

    @classmethod
    def for_data_type(cls, data_type: str) -> FileTableSpec | None:
        return next((t for t in cls._tables.values() if data_type in t.data_types), None)

    @classmethod
    def all(cls) -> list[FileTableSpec]:
        return list(cls._tables.values())


__all__ = ["BankCategory", "FileTableRegistry", "FileTableSpec", "TableColumn", "TaxRates"]
