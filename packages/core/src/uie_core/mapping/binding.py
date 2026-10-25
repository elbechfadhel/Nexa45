"""Binding cible : comment un modèle canonique (CDM) se traduit dans un format cible décrit par le Registry.

Le binding est une DONNÉE (YAML d'un domain pack, validée ici) ; il ne contient que :
- l'adresse du format dans le Registry (système, produit, versions, interface, entités) ;
- des POSITIONS de ce format (`technical_name` du Registry, ex. « 016 ») et la source CDM de chacune ;
- un petit ensemble FERMÉ de transformations génériques (`map`, `regex`, `when`, `fallback`, emplacements
  répétés), des règles déclarées (`required_unless`), l'origine des valeurs d'en-tête et les paramètres du
  profil d'export.
Libellé, type, longueur, décimales, obligatoire et motif viennent TOUJOURS du Registry (résolution), jamais du
binding. Aucun nom de système ni de format dans ce module.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TargetRef(_Spec):
    system: str
    product: str
    versions: list[str]
    interface: str
    entity: str  # entité des enregistrements (une ligne = un enregistrement)
    header_entity: str | None = None  # entité de la ligne d'en-tête du fichier, si le format en a une
    display: str | None = None  # nom du système cible dans les messages (donnée du binding)

    @property
    def display_name(self) -> str:
        return self.display or self.system.upper()


class SourceRef(_Spec):
    model: str  # ex. « partner »
    version: str  # version majeure du modèle CDM


class When(_Spec):
    """Conditions sur la vue du CDM (ET) : valeur de `kind` et / ou de `role` parmi celles listées."""

    kind: list[str] = []
    role: list[str] = []

    def matches(self, view: dict[str, Any]) -> bool:
        return (not self.kind or view.get("kind") in self.kind) and (
            not self.role or view.get("role") in self.role
        )


class Extract(_Spec):
    """Partie d'une valeur : groupe `group` de `regex` (pas de correspondance → valeur invalide)."""

    regex: str
    group: int = 1

    @field_validator("regex")
    @classmethod
    def _compile(cls, value: str) -> str:
        re.compile(value)
        return value


class FieldMapping(_Spec):
    field: str  # position du format (technical_name du Registry)
    source: str | None = None  # chemin de la vue CDM (ex. address.MAIN.city) ou target.<valeur fournie>
    constant: str | None = None
    fallback: str | None = None  # autre chemin si la source est vide
    when: When | None = None
    extract: Extract | None = None
    map: dict[str, str] | None = None  # valeur source → valeur cible ; valeur absente de la table = omise
    date_format: str | None = None  # date source → texte (strftime), ex. « %d%m » quand le format le déclare
    identity: bool = False  # sans elle l'enregistrement n'est pas utilisable (invalide → bloqué)

    @model_validator(mode="after")
    def _one_source(self) -> FieldMapping:
        if (self.source is None) == (self.constant is None):
            raise ValueError(f"{self.field} : « source » OU « constant »")
        return self


class RepeatMapping(_Spec):
    """Liste de la vue (ex. comptes bancaires) écrite dans des emplacements répétés du format."""

    source: str
    label: str  # libellé affiché (anglais), ex. « bank accounts »
    require: str | None = None  # attribut indispensable : un élément sans lui n'est pas écrit (averti)
    require_label: str | None = None  # libellé de cet attribut dans l'avertissement (ex. IBAN)
    slots: list[dict[str, str]] = Field(min_length=1)  # un dict par emplacement : attribut → position
    map: dict[str, dict[str, str]] = {}  # attribut → table de valeurs (ex. is_main true → 1)


class RuleKind(StrEnum):
    REQUIRED_UNLESS = "required_unless"


class TargetRule(_Spec):
    id: str
    kind: RuleKind
    field: str
    source: str | None = None
    equals: str | None = None
    when: When | None = None  # la règle ne vaut que pour ces enregistrements (ex. une sorte de partenaire)
    level: Literal["BLOCKING", "WARNING"] = "WARNING"
    message: str  # phrase simple (anglais)


class OmittedSource(_Spec):
    """Donnée du modèle canonique que la cible ne sait pas recevoir : écartée et SIGNALÉE (jamais en
    silence) quand elle a une valeur."""

    source: str  # chemin de la vue CDM
    label: str  # libellé affiché (anglais), ex. « Bank accounts »
    reason: str | None = None  # phrase simple ; défaut : « cannot be sent to <cible> »
    when: When | None = None


class HeaderValue(_Spec):
    constant: str | None = None
    snapshot: Literal["header_version", "data_category", "product_name", "format_version"] | None = None
    profile: str | None = None
    generated: Literal["timestamp_ms"] | None = None
    context: str | None = None  # valeur fournie par l'export (ex. period_start) ; date → `date_format`
    template: str | None = None  # texte composé de valeurs du contexte, ex. « {period_month}/{period_year} »
    date_format: str | None = None  # format d'une date du contexte (strftime), défaut %Y%m%d

    @model_validator(mode="after")
    def _one(self) -> HeaderValue:
        origins = (self.constant, self.snapshot, self.profile, self.generated, self.context, self.template)
        if sum(v is not None for v in origins) != 1:
            raise ValueError("une seule origine par valeur d'en-tête")
        return self


class ParamType(StrEnum):
    TEXT = "text"
    INTEGER = "integer"
    DATE = "date"  # AAAA-MM-JJ dans le profil
    RANGE = "range"  # {start, end} entiers
    CHOICE = "choice"
    BOOLEAN = "boolean"


class ProfileParam(_Spec):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    label: str
    help: str | None = None
    # format attendu, en phrase simple (ex. « Two digits, e.g. 03 ») : affiché comme aide et repris dans le
    # message d'erreur si la valeur ne respecte pas le motif du Registry (motif jamais montré)
    format_help: str | None = None
    type: ParamType = ParamType.TEXT
    required: bool = False
    header_field: str | None = None  # valeur écrite dans l'en-tête : contrôlée par le motif du Registry
    header_format: str | None = None  # date → format d'en-tête (strftime)
    choices: list[str] = []
    default: Any = None
    # indispensable seulement pour CRÉER le fichier (ex. un choix d'en-tête) : un aperçu des enregistrements
    # reste possible sans lui ; `required` reste réservé aux paramètres sans lesquels rien n'est possible
    required_for_file: bool = False


class AccountSpec(_Spec):
    """Identifiant de l'enregistrement dans la cible, attribué par plage et par rôle (numéro de compte)."""

    field: str
    length_from: str  # paramètre du profil donnant la longueur de référence
    plus: int = 0  # longueur des identifiants = paramètre + plus
    ranges: dict[str, str]  # rôle → paramètre RANGE du profil
    reuse_from: dict[str, str] = {}  # rôle → chemin de la vue (numéro de la source, si autorisé)
    reuse_param: str | None = None  # paramètre BOOLEAN qui autorise la reprise
    # paramètre INTEGER : numéro = ce nombre + numéro de la source (« 10000 + 1001 » → 11001), s'il tient dans
    # la plage du rôle et n'est pas déjà attribué ; sinon le premier libre de la plage
    offset_param: str | None = None


class TargetProductRef(_Spec):
    """Autre format du MÊME système cible (ex. le fichier des comptes de personne)."""

    system: str
    product: str


class InheritedProfile(_Spec):
    """Paramètres partagés lus dans le profil d'un autre format (une seule saisie pour un même dossier
    cible) ; ils ne sont jamais recopiés dans ce profil."""

    target: TargetProductRef
    params: list[str] = Field(min_length=1)


class BookingModel(StrEnum):
    # une écriture par (pièce, compte de contrepartie, taxe) : montant TTC, compte de tiers en face
    GROSS_BY_ACCOUNT_AND_TAX = "gross_by_account_and_tax"


class BookingSpec(_Spec):
    """Enregistrements par ÉCRITURE dérivée d'une pièce comptable (`records: per_booking`)."""

    model: BookingModel


class PartnerAccountSpec(_Spec):
    """Compte du tiers de la pièce : numéro attribué par l'export d'un autre format (jamais attribué ici)."""

    field: str
    target: TargetProductRef  # format dont le profil a attribué les numéros (même organisation)
    length_from: str  # paramètre (hérité) donnant la longueur de référence
    plus: int = 0


class LookupKind(StrEnum):
    ACCOUNT = "ACCOUNT"  # compte de contrepartie source → compte cible
    TAX = "TAX"  # taxe source (ou taux d'une source sans taxes) → clé de taxe cible
    CATEGORY = "CATEGORY"  # catégorie d'une source sans plan comptable → compte cible (jamais suggéré)
    # taxe de vente de la source → taux de taxe admis par la cible (valeurs du Registry ; jamais suggéré)
    TAX_RATE = "TAX_RATE"


class LookupSpec(_Spec):
    """Correspondance de VALEURS source → cible, décidée explicitement (table de correspondance)."""

    kind: LookupKind
    field: str  # position où la valeur cible est écrite (son motif et sa longueur la valident)
    label: str  # libellé affiché (anglais), ex. « G/L account »
    length_from: str | None = None  # paramètre (hérité) : longueur exacte attendue
    allow_none: bool = False  # « aucune valeur » est une décision valide (rien n'est écrit)
    none_label: str | None = None  # libellé de cette décision (ex. « No tax key »)
    # Cible par API : valeurs possibles = OPTIONS lues dans le système cible (ressource du contrat),
    # et une option a une sorte attendue selon le rôle du partenaire de la pièce (ex. recette / dépense).
    options_resource: str | None = None
    option_kind_by_role: dict[str, str] = {}


class ItemsSpec(_Spec):
    """Éléments RÉPÉTÉS d'un enregistrement par pièce (`records: per_document`) : un élément par écriture du
    modèle `booking`, placé dans la liste `path` de la requête (champs `path.<champ>` du Registry)."""

    path: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    fields: list[FieldMapping] = Field(min_length=1)


class ChartSuggestion(_Spec):
    """Réponses PRÉSÉLECTIONNÉES (jamais appliquées sans la réponse de l'utilisateur) pour une source sans
    plan comptable, selon le plan comptable du dossier cible : catégorie de la source (« sale:19 ») → compte
    d'usage ; `no_tax_key` : ces comptes calculent la taxe eux-mêmes (aucune clé de taxe)."""

    categories: dict[str, str] = {}
    no_tax_key: bool = False


class TargetBinding(_Spec):
    binding_version: str
    name: str  # libellé affiché (anglais)
    target: TargetRef
    source: SourceRef
    records: Literal["per_role", "single", "per_booking", "per_document"] = "single"
    # livraison : fichier rendu à l'utilisateur, ou requêtes envoyées à l'API du système cible
    delivery: Literal["file", "api"] = "file"
    omitted: list[OmittedSource] = []
    account: AccountSpec | None = None
    booking: BookingSpec | None = None
    partner_account: PartnerAccountSpec | None = None
    lookups: list[LookupSpec] = []
    inherit_profile: InheritedProfile | None = None
    fields: list[FieldMapping]
    repeat: list[RepeatMapping] = []
    rules: list[TargetRule] = []
    header: dict[str, HeaderValue] = {}
    profile: list[ProfileParam] = []
    encodings: list[str] = ["utf-8"]
    items: ItemsSpec | None = None  # `records: per_document` : éléments répétés (un par écriture)
    currencies: list[str] = []  # devises acceptées par la cible (vide : toutes)
    # paramètre du profil qui nomme le plan comptable, et réponses présélectionnées par plan comptable
    chart_param: str | None = None
    suggestions: dict[str, ChartSuggestion] = {}

    @model_validator(mode="after")
    def _consistency(self) -> TargetBinding:
        params = {p.name: p for p in self.profile}
        known = set(params) | set(self.inherit_profile.params if self.inherit_profile else [])
        for v in self.header.values():
            if v.profile and v.profile not in known:
                raise ValueError(f"paramètre de profil inconnu : {v.profile}")
        if self.account:
            missing = {self.account.length_from, *self.account.ranges.values()} - set(params)
            if self.account.reuse_param:
                missing |= {self.account.reuse_param} - set(params)
            if missing:
                raise ValueError(f"paramètres de profil inconnus : {sorted(missing)}")
        if (self.records in ("per_booking", "per_document")) != (self.booking is not None):
            raise ValueError("« records: per_booking | per_document » et « booking » vont ensemble")
        if (self.records == "per_document") != (self.items is not None):
            raise ValueError("« records: per_document » et « items » vont ensemble")
        lengths = [lk.length_from for lk in self.lookups if lk.length_from]
        if self.partner_account:
            lengths.append(self.partner_account.length_from)
        if unknown := sorted(set(lengths) - known):
            raise ValueError(f"paramètres de profil inconnus : {unknown}")
        kinds = [lk.kind for lk in self.lookups]
        if len(kinds) != len(set(kinds)):
            raise ValueError("une seule correspondance par sorte (ACCOUNT, TAX)")
        return self

    def positions(self) -> set[str]:
        """Toutes les positions citées (enregistrement) : elles doivent exister dans le Registry."""
        out = {f.field for f in self.fields} | {r.field for r in self.rules}
        out |= {pos for rep in self.repeat for slot in rep.slots for pos in slot.values()}
        if self.account:
            out.add(self.account.field)
        if self.partner_account:
            out.add(self.partner_account.field)
        out |= {lk.field for lk in self.lookups}
        if self.items:
            out |= {f.field for f in self.items.fields}
        return out

    def lookup(self, kind: LookupKind) -> LookupSpec | None:
        found = next((lk for lk in self.lookups if lk.kind == kind), None)
        if found is None and kind == LookupKind.CATEGORY:
            # une catégorie de la source se traduit dans le MÊME champ qu'un compte de contrepartie
            return self.lookup(LookupKind.ACCOUNT)
        return found


class TargetBindingError(Exception):
    """Binding invalide, introuvable, ou incohérent avec le Registry."""


class TargetBindingRegistry:
    """Bindings enregistrés par les domain packs (racines de composition), par (modèle source, système)."""

    _bindings: ClassVar[dict[tuple[str, str, str], TargetBinding]] = {}

    @classmethod
    def register(cls, binding: TargetBinding) -> None:
        cls._bindings[(binding.source.model, binding.target.system, binding.target.product)] = binding

    @classmethod
    def get(cls, model: str, system: str, product: str) -> TargetBinding:
        try:
            return cls._bindings[(model, system, product)]
        except KeyError:
            raise TargetBindingError(f"Aucun binding {model} → {system}/{product}") from None

    @classmethod
    def for_model(cls, model: str, delivery: str = "file") -> list[TargetBinding]:
        """Bindings d'un modèle source pour un mode de livraison (défaut : les formats de fichier)."""
        return [b for (m, _, _), b in sorted(cls._bindings.items()) if m == model and b.delivery == delivery]

    @classmethod
    def find(cls, system: str, product: str) -> TargetBinding:
        """Binding d'un format, quel que soit le modèle source (un format = un binding)."""
        for (_, s, p), b in sorted(cls._bindings.items()):
            if (s, p) == (system, product):
                return b
        raise TargetBindingError(f"Aucun binding vers {system}/{product}")
