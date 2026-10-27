"""Mode Simple « fichier d'abord » : type de données déduit des en-têtes (règles), structure cible construite
depuis le Registry (domain pack résolu sur le snapshot), réponse unique avec au plus 3 questions bloquantes.

Fonctions pures ; aucun nom de système : l'entité principale et ses champs métier viennent du domain pack
(rôle PRIMARY, champs déclarés), leurs types, obligations et valeurs admises du snapshot du Registry."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict

from uie_core.domains.model import DomainKey, EntityRole, FieldGroup, ResolvedDomain
from uie_core.registry.schema import DataType, SchemaSnapshotModel
from uie_core.wizard.lookup import InstanceTarget
from uie_core.wizard.mapping import (
    RECORDS_CREATE,
    RECORDS_DEFAULT,
    UNITS_AS_PIECE,
    UNITS_KEEP,
    FileMapping,
    MappingDecision,
    RecordSheet,
    records_key,
    units_key,
)
from uie_core.wizard.missing import Fill, MissingChoice, MissingKind, MissingQuestion
from uie_core.wizard.model import TargetField, TargetProposal, WizardAnswer
from uie_core.wizard.text import count, plural
from uie_core.wizard.values import ISO_4217, WITHOUT_PRICES

MAX_BLOCKING = 3  # questions bloquantes rendues par le démarrage rapide (les autres restent comptées)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------- #
# Type de données d'après les en-têtes (règles, aucune valeur lue)
# --------------------------------------------------------------------------- #
PRODUCT_WORDS = frozenset(
    """sku price prices barcode ean upc gtin asin isbn mpn product products title brand weight stock inventory
    category categories variant variants manufacturer model dimensions rating reviews
    artikel artikelnummer artikelnr artnr artikelbezeichnung bezeichnung produkt produkte warengruppe
    kategorie hersteller marke einheit mengeneinheit gewicht preis vk ek verkaufspreis einkaufspreis
    listenpreis bestand lagerbestand mwst""".split()
)
PARTNER_WORDS = frozenset(
    """email mail phone mobile fax street address city zip postcode postal country vat customer customers
    client clients supplier suppliers vendor vendors contact firstname lastname iban bic website
    kunde kunden kundennummer kundennr lieferant lieferanten lieferantennummer lieferantennr firma
    ansprechpartner vorname nachname strasse straße hausnummer plz ort land telefon telefax handy
    ustid ustidnr steuernummer""".split()
)
SUPPLIER_WORDS = frozenset(
    """supplier suppliers vendor vendors creditor kreditor lieferant lieferanten lieferantennummer
    lieferantennr""".split()
)
CUSTOMER_WORDS = frozenset(
    """customer customers client clients debtor debitor kunde kunden kundennummer kundennr""".split()
)
# factures / écritures (journal de factures de sortie, export d'un logiciel de facturation)
INVOICE_WORDS = frozenset(
    """invoice invoices invoiceno invoicenumber net gross total amount vat tax taxrate credit creditnote due
    rechnung rechnungen rechnungsnummer rechnungsnr renr redatum rechnungsdatum rechnungsbetrag beleg
    belegnr belegnummer belegdatum belegfeld netto nettobetrag brutto bruttobetrag ust mwst umsatzsteuer
    steuersatz gesamtbetrag betrag gutschrift faellig fälligkeit
    rechnungseingang eingangsrechnung eingangsrechnungen eingangsdatum kostenart kostenarten aufwand
    aufwandsart""".split()
)
# livre de caisse : recettes, dépenses, solde de la caisse (« cashin » : « Cash in » collé)
CASH_WORDS = frozenset(
    """einnahme einnahmen ausgabe ausgaben kassenbestand kassenstand kassensaldo kassenbuch bareinnahme
    barausgabe cashin cashout recette recettes depense depenses encaissement decaissement""".split()
)
# relevé bancaire (export d'une banque) : motif de paiement, compte, contrepartie, dates de la banque
BANK_WORDS = frozenset(
    """verwendungszweck auftragskonto valutadatum valuta buchungstag zahlungspflichtiger beguenstigter
    begünstigter mandatsreferenz glaeubiger gläubiger sammlerreferenz wertstellung""".split()
)
# types de données des journaux de factures : ventes (factures de sortie) et achats (factures d'achat) ;
# livre de caisse (une ligne = une recette ou une dépense, sans client ni fournisseur) ; relevé bancaire
INVOICES, PURCHASE_INVOICES, CASH_BOOK = "Invoices", "Purchase invoices", "Cash book"
BANK_STATEMENT = "Bank statement"
MIN_SCORE = 3  # en-têtes reconnus au minimum, et au moins deux fois plus que l'autre sorte

# type de données affiché → domaine du domain pack qui donne la structure
DATA_TYPES: dict[str, DomainKey] = {
    "Products": DomainKey.PRODUCT,
    "Customers": DomainKey.PARTNER,
    "Suppliers": DomainKey.PARTNER,
    "Customers & suppliers": DomainKey.PARTNER,
}
_WORD = re.compile(r"[a-z0-9]+")


def _words(header: str) -> set[str]:
    """Mots de l'en-tête, plus l'en-tête collé sans ponctuation (« Art.-Nr. » → « artnr », « USt-IdNr » →
    « ustidnr »)."""
    words = _WORD.findall(header.casefold())
    return {*words, "".join(words)}


def infer_data_type(headers: list[str], options: list[str] | None = None) -> str | None:
    """Type de données d'après les en-têtes, parmi `options` (types que la cible reçoit ; None : tous) ;
    None quand les règles ne tranchent pas (le LLM peut alors être consulté, parmi les mêmes types)."""
    words = [_words(h) for h in headers]
    # en-têtes d'un export bancaire (au moins deux) : un relevé bancaire, avant tout le reste
    if sum(1 for w in words if w & BANK_WORDS) >= 2:
        return BANK_STATEMENT if options is None or BANK_STATEMENT in options else None
    # recettes ET dépenses (au moins deux en-têtes de caisse) : un livre de caisse, avant les factures
    if sum(1 for w in words if w & CASH_WORDS) >= 2:
        return CASH_BOOK if options is None or CASH_BOOK in options else None
    product = sum(1 for w in words if w & PRODUCT_WORDS)
    partner = sum(1 for w in words if w & PARTNER_WORDS)
    invoice = sum(1 for w in words if w & INVOICE_WORDS)
    if invoice >= MIN_SCORE and invoice >= 2 * max(product, partner):
        # journal d'ACHAT : le fournisseur est nommé (Lieferant, Kreditor…) et aucun client ne l'est
        every = set().union(*words) if words else set()
        kind = PURCHASE_INVOICES if every & SUPPLIER_WORDS and not every & CUSTOMER_WORDS else INVOICES
        return kind if options is None or kind in options else None
    if product >= MIN_SCORE and product >= 2 * partner:
        return "Products"
    if partner >= MIN_SCORE and partner >= 2 * product:
        every = set().union(*words) if words else set()
        if every & SUPPLIER_WORDS and not every & CUSTOMER_WORDS:
            return "Suppliers"
        if every & CUSTOMER_WORDS and not every & SUPPLIER_WORDS:
            return "Customers"
        return "Customers & suppliers"
    return None


def data_type_mismatch(headers: list[str], options: list[str], target: str) -> str | None:
    """Le fichier ressemble NETTEMENT à un type que la cible ne reçoit pas (liste de clients envoyée à un
    journal de factures...) : message pour l'utilisateur, jamais un type forcé ; None sinon. Les types de
    partenaires entre eux ne sont pas un désaccord (le rôle se précise ensuite)."""
    looks = infer_data_type(headers)
    if looks is None or looks in options:
        return None
    partners = {k for k, d in DATA_TYPES.items() if d == DomainKey.PARTNER}
    if looks in partners and partners & set(options):
        return None
    takes = " or ".join(o.lower() for o in options)
    return (
        f"Your file looks like a list of {looks.lower()}, but {target} receives {takes}. Choose another "
        f"target for this file, or confirm below what your file holds."
    )


def target_for_data_type(looks: str | None, receives: dict[str, list[str]]) -> str | None:
    """Cible choisie d'après le type de données du fichier, parmi les cibles proposées (valeur → types
    reçus) : la SEULE qui reçoit ce type (les types de partenaires entre eux comptent comme un même type) ;
    None si le type n'est pas sûr, ou si aucune ou plusieurs cibles le reçoivent (rien de présélectionné)."""
    if looks is None:
        return None
    partners = {k for k, d in DATA_TYPES.items() if d == DomainKey.PARTNER}
    family = partners if looks in partners else {looks}
    found = [value for value, types in receives.items() if family & set(types)]
    return found[0] if len(found) == 1 else None


def data_type_options(domains: list[DomainKey]) -> list[str]:
    """Types de données qu'une cible sait recevoir (domain packs du système) ; tous si aucun pack."""
    return [label for label, d in DATA_TYPES.items() if not domains or d in domains]


# --------------------------------------------------------------------------- #
# Structure cible depuis le Registry
# --------------------------------------------------------------------------- #
SKIPPED_GROUPS = frozenset({FieldGroup.MEDIA, FieldGroup.TECHNICAL})
FILE_TYPES = frozenset(
    {
        DataType.STRING,
        DataType.TEXT,
        DataType.INTEGER,
        DataType.DECIMAL,
        DataType.BOOLEAN,
        DataType.DATE,
        DataType.DATETIME,
        DataType.ENUM,
        DataType.REFERENCE,
    }
)


def registry_structure(
    resolved: ResolvedDomain, model: SchemaSnapshotModel, data_type: str, target_label: str
) -> TargetProposal | None:
    """Structure d'import construite SANS LLM : entité PRIMARY du domain pack, ses champs déclarés présents
    dans le snapshot (hors médias, champs techniques, collections, binaires et champs en LECTURE SEULE : rien
    à importer), groupés comme le pack, obligatoires en tête ; type, libellé et obligation lus dans le
    Registry (`verify` y ajoute les valeurs admises et les liens). None : pas d'entité principale présente
    (le parcours garde alors la proposition de l'IA)."""
    primary = next((e for e in resolved.entities if e.role == EntityRole.PRIMARY and e.present), None)
    if primary is None:
        return None
    entity = next((e for e in model.entities if e.technical_name == primary.technical_name), None)
    if entity is None:
        return None
    by_name = {f.technical_name: f for f in entity.fields}
    order = {g: i for i, g in enumerate(FieldGroup)}
    chosen = []
    for d in resolved.fields:
        f = by_name.get(d.field_technical_name)
        if d.entity_technical_name != entity.technical_name or not d.listed or not d.present or f is None:
            continue
        if d.group in SKIPPED_GROUPS or f.data_type not in FILE_TYPES or f.readonly:
            continue
        chosen.append((order.get(d.group, len(order)), not f.required, f))
    fields = [
        TargetField(
            name=f.technical_name,
            label=(f.label or f.technical_name)[:200],
            type=f.data_type.value,
            required=bool(f.required),
            description=f.help[:400] if f.help else None,
        )
        for _, _, f in sorted(chosen, key=lambda x: (x[0], x[1]))
    ]
    if not fields:
        return None
    return TargetProposal(
        title=f"{data_type} for {target_label}"[:200],
        summary=f"Business fields of {data_type.lower()} read from the verified {target_label} schema of the "
        "Nexa45 Registry, for the target's own spreadsheet import.",
        entity=entity.technical_name,
        configuration=[],
        fields=fields,
        rows=[],
    )


# --------------------------------------------------------------------------- #
# Réponse du démarrage rapide
# --------------------------------------------------------------------------- #
class QuickMapping(_Model):
    """Correspondance d'une CATÉGORIE de la source vers un compte (une ligne d'un tableau) : catégorie, taux,
    décision de la clé de taxe de ce taux (partagée par les catégories du même taux) et comptes automatiques
    du plan comptable (aucune clé de taxe n'y est écrite)."""

    category: str
    rate: str
    tax_decision: str | None = None
    automatic_accounts: list[str] = []


class QuickQuestion(_Model):
    """Question bloquante avec une valeur PRÉSÉLECTIONNÉE (à confirmer, jamais appliquée seule) et son
    origine : déduite des réponses, lue dans le système connecté, ou première valeur admise du Registry."""

    question: MissingQuestion
    default: str | None = None
    # your_answers | your_system | registry_first_value | your_file | usual_default
    default_from: str | None = None
    # décision sur les VALEURS (clé de `choices`, ex. « currency ») ; None : valeur d'un champ (`fills`)
    decision: str | None = None
    # correspondance de catégorie : affichée en tableau (catégorie, taux, compte, clé)
    mapping: QuickMapping | None = None
    # réglage : ligne de résumé (« Customer account = {value} + customer number ») ; None : nom + valeur
    summary: str | None = None


class QuickStart(_Model):
    target_label: str
    target: InstanceTarget | None = None  # instance de l'organisation choisie comme cible
    data_type: str
    data_type_from: str  # rules | ai
    structure_from: str  # registry | ai
    answers: list[WizardAnswer]  # réponses équivalentes, pour continuer avec les étapes existantes
    proposal: TargetProposal
    mapping: FileMapping
    decisions: list[MappingDecision]  # correspondances retenues sans intervention
    choices: dict[str, str] = {}  # décisions de valeurs déjà connues (devise de la société)
    questions: list[QuickQuestion]  # au plus MAX_BLOCKING, chacune avec sa réponse présélectionnée
    # APPLIQUÉES avec leur réponse présélectionnée (listées, modifiables) : valeurs de champs
    # présélectionnables (jamais demandées) et décisions au-delà de MAX_BLOCKING ; leurs réponses sont déjà
    # dans `choices` (décisions sur les valeurs) et `fills` (champs)
    applied: list[QuickQuestion] = []
    fills: list[Fill] = []
    more_questions: int = 0  # questions au-delà de MAX_BLOCKING SANS réponse présélectionnée


class TargetOption(_Model):
    """Cible proposée au démarrage par le fichier : `value` est passée telle quelle à `quick_start`."""

    value: str
    label: str  # système et version (« Odoo 18 »)
    group: str  # "instance" (système de l'organisation), "registry" (schéma publié), "known" (sans schéma)
    detail: str | None = None  # nom de l'instance
    connected: bool = False  # instance : dernière connexion réussie
    # Registry : système (une tuile par système qui a plusieurs FORMATS) et format + version, si le produit
    # du Registry n'est pas le système lui-même (« Buchungsstapel 13 ») ; None sinon (« Odoo 18 »)
    system: str | None = None
    system_name: str | None = None
    format: str | None = None
    # produit (format) et version du Registry ; `latest` : version la plus récente de ce format
    product: str | None = None
    product_name: str | None = None
    version: str | None = None
    latest: bool = False
    # types de données que la cible sait recevoir (texte de vérification, choix quand le type est inconnu)
    receives: list[str] = []


# clés du vocabulaire du domaine (pas des noms de système) : champ catégorie des lignes, nom et parent de la
# catégorie
CATEGORY_KEY, CATEGORY_NAME_KEY, PARENT_CATEGORY_KEY = "category", "category_name", "parent_category"


def records_question(spec: RecordSheet, target: str) -> QuickQuestion:
    """Une question pour toutes les valeurs du fichier qu'aucun système connecté ne confirme : les créer
    (présélectionné : feuille à importer AVANT les articles) ou laisser la valeur par défaut de la cible."""
    return QuickQuestion(
        question=MissingQuestion(
            field=spec.field,
            label=spec.label,
            kind=MissingKind.CHOICE,
            title=f"Your file has its own {spec.label.lower()} values: what should {target} do with them?",
            help="They may not exist yet in the target system. Creating them adds a sheet to import first "
            "(with their hierarchy when your values are paths like “A / B”).",
            choices=[
                MissingChoice(value=RECORDS_CREATE, label=f"Create the missing {spec.label.lower()} values"),
                MissingChoice(
                    value=RECORDS_DEFAULT,
                    label=f"Use the default {spec.label.lower()} of {target} for every row",
                ),
            ],
            free_text=False,
        ),
        default=RECORDS_CREATE,
        default_from="usual_default",
        decision=records_key(spec.field),
    )


def base_unit_first(q: MissingQuestion, unit: str | None) -> MissingQuestion:
    """Unités lues dans le système connecté : l'unité de base de la cible (« Units » d'après le domain pack)
    en tête, donc présélectionnée et NOMMÉE, au lieu de la première valeur lue."""
    first = [c for c in q.choices if unit and c.label.casefold() == unit.casefold()]
    if not first:
        return q
    return q.model_copy(update={"choices": first + [c for c in q.choices if c not in first]})


def with_created_count(q: QuickQuestion, n: int) -> QuickQuestion:
    """Option « créer » de la question des valeurs liées, avec le NOMBRE d'enregistrements que le fichier
    préparé créera d'abord (niveaux d'une hiérarchie compris) : « Create 1,643 missing product
    categories »."""
    label = q.question.label.lower()
    choices = [
        c.model_copy(update={"label": f"Create {count(n, f'missing {label}', f'missing {plural(label)}')}"})
        if c.value == RECORDS_CREATE
        else c
        for c in q.question.choices
    ]
    return q.model_copy(update={"question": q.question.model_copy(update={"choices": choices})})


# champs dont la cible a une valeur par défaut à proposer quand le fichier n'en dit rien (jamais un texte, un
# nombre ou une date propres à chaque ligne)
DEFAULTABLE_KINDS = frozenset({MissingKind.REFERENCE, MissingKind.CHOICE, MissingKind.BOOLEAN})


def target_default_question(q: MissingQuestion, target: str) -> QuickQuestion:
    """Champ obligatoire que le fichier ne donne pas et dont aucune valeur ne se présélectionne : la valeur
    par défaut de la cible est proposée (présélectionnée, rien n'est inventé) ; une autre valeur peut être
    saisie."""
    return QuickQuestion(
        question=q.model_copy(
            update={
                "title": f"Your file has no {q.label.lower()}: which one should {target} use?",
                "help": f"Leaving it to {target} applies its usual default value; you can also type a value.",
                "choices": [
                    MissingChoice(value=RECORDS_DEFAULT, label=f"The default {q.label.lower()} of {target}"),
                    *q.choices,
                ],
            }
        ),
        default=RECORDS_DEFAULT,
        default_from="usual_default",
        decision=records_key(q.field),
    )


UNIT_KEYS = frozenset({"uom", "uom_purchase"})  # clés du vocabulaire : champs qui désignent une unité


def units_question(field: str, label: str, found: list[str], piece: str) -> QuickQuestion:
    """Unités d'emballage du fichier (Set, Packung…) inconnues de la cible : les compter comme une pièce
    (présélectionné) ou les garder (à créer d'abord dans la cible)."""
    listed = ", ".join(found[:6]) + ("…" if len(found) > 6 else "")
    return QuickQuestion(
        question=MissingQuestion(
            field=field,
            label=label,
            kind=MissingKind.CHOICE,
            title=f"Your file sells some products by the {listed}: how should they be counted?",
            help=f"The target has no such units. Each {found[0]} can be sold as one piece ({piece}).",
            choices=[
                MissingChoice(value=UNITS_AS_PIECE, label=f"Count each one as one piece ({piece})"),
                MissingChoice(
                    value=UNITS_KEEP, label="Keep them: I will create these units in the target first"
                ),
            ],
            free_text=False,
        ),
        default=UNITS_AS_PIECE,
        default_from="usual_default",
        decision=units_key(field),
    )


USUAL_CURRENCIES = ["EUR", "USD", "GBP", "CHF"]
AMBIGUOUS_DEFAULT = {"$": "USD", "¥": "JPY", "kr": "SEK"}  # présélection seulement, à confirmer


def currency_question(codes: dict[str, int], noun: str = "rows") -> QuickQuestion:
    """Devise des prix du fichier, à confirmer : la plus fréquente du fichier (signe ambigu → sa devise la
    plus courante), sinon EUR. Seules les devises PRÉSENTES dans le fichier sont proposées, plus « Other
    value » (saisie libre, contrôlée) ; les devises usuelles seulement quand le fichier n'en cite aucune.
    Réponse rangée dans `choices["currency"]`."""
    ranked = sorted(codes.items(), key=lambda x: -x[1])
    found = [c for c in dict.fromkeys(AMBIGUOUS_DEFAULT.get(c, c) for c, _ in ranked) if c in ISO_4217]
    default = found[0] if found else "EUR"
    options = found or USUAL_CURRENCIES
    listed = ", ".join(f"{c} ({n})" for c, n in ranked)
    return QuickQuestion(
        question=MissingQuestion(
            field="currency",
            label="Currency",
            kind=MissingKind.CHOICE,
            title="Which currency are your prices in?",
            help=(f"Currencies seen in your file: {listed}. " if ranked else "Your file does not say. ")
            + "Rows in another currency keep their other values; their prices are left empty. Nothing is "
            "converted.",
            choices=[MissingChoice(value=c, label=c, verified=True) for c in options]
            + [MissingChoice(value=WITHOUT_PRICES, label=f"Import the {noun} without their prices")],
            free_text=True,  # « Other value » : une autre devise (code ISO 4217, contrôlé à l'aperçu)
        ),
        default=default,
        default_from="your_file" if ranked else "usual_default",
        decision="currency",
    )


def with_default(q: MissingQuestion) -> QuickQuestion:
    """Valeur présélectionnée : d'abord une déduction (réponses, système connecté), sinon la première valeur
    admise du Registry ; aucune pour une référence sans système connecté (jamais devinée)."""
    if q.suggested:
        return QuickQuestion(question=q, default=q.suggested, default_from="your_answers")
    if q.from_system and q.choices:
        return QuickQuestion(question=q, default=q.choices[0].value, default_from="your_system")
    verified = [c for c in q.choices if c.verified]
    if verified:
        return QuickQuestion(question=q, default=verified[0].value, default_from="registry_first_value")
    return QuickQuestion(question=q)
