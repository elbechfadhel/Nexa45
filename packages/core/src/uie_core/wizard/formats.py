"""Cible FORMAT DE FICHIER du mode Simple (binding `delivery: file` : DATEV Debitoren/Kreditoren,
Buchungsstapel…).

Le fichier du client est une SOURCE comme une autre :

    colonnes ──► clés du tableau de source du domaine (noms, mémoire, vocabulaire du tableau, IA)
             ──► valeurs mises en forme (moteur de valeurs : dates, nombres, doublons)
             ──► CDM (Partner, Accounting 1.1 : `cdm_partners`, `cdm_accounting`)
             ──► binding cible EXISTANT (comptes, en-tête, validation par le Registry) ──► fichier du format
             ──► historique : numéros de compte réservés, pièces journalisées (aucun doublon au 2e export).

Aucune règle de format ici : tout vient du binding (données) et du Registry. Les réglages de l'en-tête
sont les paramètres du profil du binding : enregistrés dans le profil d'export de l'organisation au
téléchargement, repris ensuite (« from your settings »). Les numéros (Beraternummer, Mandantennummer) ne sont
jamais inventés ; les autres sont présélectionnés (exercice de l'année des données, longueur 4, plages
d'usage…)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from uie_cdm import PartnerRole

from uie_core.domains.model import DomainKey
from uie_core.imports.matching import rule_key
from uie_core.mapping import (
    LookupKind,
    ParamType,
    ProfileParam,
    TargetBinding,
    TargetBindingError,
    TargetBindingRegistry,
)
from uie_core.mapping.suggestions import CategoryAccountRegistry
from uie_core.sources.table import FileTableRegistry, FileTableSpec
from uie_core.wizard.missing import MissingChoice, MissingKind, MissingQuestion
from uie_core.wizard.model import TargetField, TargetProposal, Verification
from uie_core.wizard.quick import INVOICES, PURCHASE_INVOICES, QuickMapping, QuickQuestion
from uie_core.wizard.values import ValueQuestion

MODEL_DOMAIN = {"partner": DomainKey.PARTNER, "accounting": DomainKey.ACCOUNTING}
SETTING = "setting:"  # réponse à un paramètre du profil (clé de `choices`)
MAPPING = "map:"  # correspondance d'une catégorie / d'une taxe (clé de `choices`)
NO_VALUE = "none"  # « aucune valeur » décidée (ex. aucune clé de taxe)
# paramètres posés, dans l'ordre (les autres gardent la valeur du profil ou leur défaut, jamais demandés)
ASKED = (
    "advisor_number",
    "client_number",
    "fiscal_year_start",
    "account_length",
    "chart_of_accounts",
    "debtor_range",
    "creditor_range",
    "account_offset",
    "creditor_offset",
    "currency",
    "lock_bookings",
)
NEVER_INVENTED = frozenset({"advisor_number", "client_number"})


@dataclass(frozen=True)
class FormatTarget:
    binding: TargetBinding
    table: FileTableSpec
    version: str
    label: str  # « DATEV Debitoren/Kreditoren 5 »

    @property
    def domain(self) -> DomainKey:
        return self.table.domain

    @property
    def shared(self) -> TargetBinding | None:
        b = self.binding
        if b.inherit_profile is None:
            return None
        return TargetBindingRegistry.find(b.inherit_profile.target.system, b.inherit_profile.target.product)


def format_target(system: str, product: str, version: str, label: str) -> FormatTarget | None:
    """Binding fichier de ce format et de cette version dont le modèle source a un tableau de source fichier ;
    None : la cible reste une structure d'import (Registry ou IA)."""
    try:
        b = TargetBindingRegistry.find(system, product)
    except TargetBindingError:
        return None
    domain = MODEL_DOMAIN.get(b.source.model)
    table = FileTableRegistry.get(domain) if domain else None
    if b.delivery != "file" or version not in b.target.versions or table is None:
        return None
    return FormatTarget(binding=b, table=table, version=version, label=label)


def data_type_of_file(fmt: FormatTarget, headers: list[str]) -> str | None:
    """Type de données d'après les en-têtes, parmi ceux du tableau : toutes les colonnes OBLIGATOIRES du
    tableau trouvées (alias exacts, ≥ 0,90) ; pour des partenaires, le rôle vient des colonnes de numéro ;
    pour des factures, le sens aussi (numéro de client : ventes, numéro de fournisseur : achats ; ni l'un ni
    l'autre, ou les deux : non tranché)."""
    found = matched_keys(fmt.table, headers)
    if not {c.key for c in fmt.table.columns if c.required} <= found:
        return None
    if fmt.domain == DomainKey.ACCOUNTING and len(fmt.table.data_types) > 1:
        customer, supplier = "partner_number" in found, "supplier_number" in found
        label = INVOICES if customer and not supplier else None
        label = PURCHASE_INVOICES if supplier and not customer else label
        return label if label in fmt.table.data_types else None
    if fmt.domain == DomainKey.PARTNER:
        customer, supplier = "customer_number" in found, "supplier_number" in found
        label = (
            "Customers" if customer and not supplier else "Suppliers" if supplier and not customer else None
        )
        if label in fmt.table.data_types:
            return label
    return fmt.table.data_types[0]


def matched_keys(table: FileTableSpec, headers: list[str]) -> set[str]:
    spec, keys = table.semantic_spec(), [c.key for c in table.columns]
    out = set()
    for h in headers:
        hit = rule_key(h, spec, keys)
        if hit and hit[1] >= 0.90:
            out.add(hit[0])
    return out


def role_of(data_type: str) -> PartnerRole | None:
    return {"Customers": PartnerRole.CUSTOMER, "Suppliers": PartnerRole.SUPPLIER}.get(data_type)


def structure(fmt: FormatTarget, data_type: str) -> TargetProposal:
    """Structure = colonnes du tableau de source (ce que le fichier peut apporter), vérifiée par le
    binding."""
    return TargetProposal(
        title=f"{data_type} for {fmt.label}"[:200],
        summary=f"Your file is turned into a {fmt.label} file: the columns are read, checked and written by "
        "the format rules of the Nexa45 Registry.",
        entity=fmt.binding.target.entity,
        entity_verified=True,
        verification=Verification.VERIFIED,
        configuration=[],
        fields=[
            TargetField(
                name=c.key,
                label=c.label,
                type=c.data_type.value,
                required=c.required,
                description=c.description or None,
                verified=True,
            )
            for c in fmt.table.columns
        ],
        rows=[],
    )


# --------------------------------------------------------------------------- #
# Réglages de l'en-tête (paramètres du profil du binding)
# --------------------------------------------------------------------------- #
def params(fmt: FormatTarget) -> list[tuple[ProfileParam, bool]]:
    """(paramètre, partagé ?) : paramètres propres du binding, puis ceux hérités du format partagé."""
    out = [(p, False) for p in fmt.binding.profile]
    shared = fmt.shared
    if shared is not None and fmt.binding.inherit_profile:
        names = set(fmt.binding.inherit_profile.params)
        out += [(p, True) for p in shared.profile if p.name in names]
    return [(p, s) for p, s in out if p.name in ASKED]


def _usual(name: str, length: int, data_year: int) -> str | None:
    """Valeur d'usage présélectionnée (à confirmer, jamais pour un numéro du cabinet)."""
    first = 10**length
    return {
        "fiscal_year_start": f"{data_year:04d}-01-01",
        "account_length": "4",
        "chart_of_accounts": "03",
        "debtor_range": f"{first}-{7 * first - 1}",
        "creditor_range": f"{7 * first}-{10 * first - 1}",
        "account_offset": str(first),
        "creditor_offset": str(7 * first),
        "currency": "EUR",
    }.get(name)


def _shown(p: ProfileParam, value: Any) -> str | None:
    if value in (None, ""):
        return None
    if p.type == ParamType.RANGE and isinstance(value, dict):
        return f"{value.get('start')}-{value.get('end')}"
    return str(value)


def settings_questions(
    fmt: FormatTarget, saved: dict[str, Any], saved_shared: dict[str, Any], data_year: int
) -> list[QuickQuestion]:
    """Une question par réglage : valeur de VOS réglages (enregistrés) d'abord, sinon valeur d'usage
    présélectionnée ; Beraternummer et Mandantennummer : jamais de présélection inventée."""
    length = int(saved_shared.get("account_length") or saved.get("account_length") or 4)
    out = []
    for p, shared in params(fmt):
        own = _shown(p, (saved_shared if shared else saved).get(p.name))
        # comptes de personne : longueur des comptes généraux + 1 (10000-69999 pour une longueur 4)
        default = own or (None if p.name in NEVER_INVENTED else _usual(p.name, length, data_year))
        choices = [MissingChoice(value=c, label=c) for c in p.choices]
        if p.type == ParamType.CHOICE and p.name == "lock_bookings":
            choices = [
                MissingChoice(value="0", label="No: the bookings stay editable in DATEV"),
                MissingChoice(value="1", label="Yes: lock the bookings (Festschreibung)"),
            ]
            default = own  # choix explicite, jamais présélectionné sans vos réglages (D10)
        out.append(
            QuickQuestion(
                question=MissingQuestion(
                    field=SETTING + p.name,
                    label=p.label,
                    kind=MissingKind.DATE
                    if p.type == ParamType.DATE
                    else MissingKind.CHOICE
                    if choices
                    else MissingKind.TEXT,
                    title=p.label,
                    help=" ".join(x for x in (p.help, p.format_help) if x) or None,
                    choices=choices,
                    free_text=not choices,
                ),
                default=default,
                default_from=("your_settings" if own else "usual_default") if default else None,
                decision=SETTING + p.name,
            )
        )
    return out


def setting_values(fmt: FormatTarget, choices: dict[str, str]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Réponses des réglages → valeurs des profils (propre, partagé), typées comme le profil les attend."""
    own: dict[str, Any] = {}
    shared: dict[str, Any] = {}
    for p, is_shared in params(fmt):
        raw = (choices.get(SETTING + p.name) or "").strip()
        if not raw:
            continue
        value: Any = raw
        if p.type == ParamType.RANGE:
            a, _, b = raw.replace(" ", "").partition("-")
            value = {"start": a, "end": b} if a and b else raw
        elif p.type == ParamType.DATE:
            value = _date(raw)
        (shared if is_shared else own)[p.name] = value
    return own, shared


def _date(raw: str) -> str:
    """« 01.01.2026 » ou « 2026-01-01 » → ISO ; autre forme : gardée (le profil la refuse et le dit)."""
    parts = raw.replace("/", ".").split(".")
    if len(parts) == 3 and all(x.isdigit() for x in parts) and len(parts[2]) == 4:
        try:
            return date(int(parts[2]), int(parts[1]), int(parts[0])).isoformat()
        except ValueError:
            return raw
    return raw


# --------------------------------------------------------------------------- #
# Correspondances d'une source sans plan comptable (catégories, taxes)
# --------------------------------------------------------------------------- #
def mapping_questions(
    fmt: FormatTarget,
    categories: dict[str, str],
    rates: list[str],
    chart: str | None,
    choices: dict[str, str],
    scope: str = "SALE",
    saved: dict[str, str] | None = None,
) -> list[QuickQuestion]:
    """Compte de chaque catégorie (« ventes à 19 % », « Bürobedarf à 19 % ») et clé de taxe de chaque taux,
    selon le sens des pièces (`scope` : SALE | PURCHASE). Réponses PRÉSÉLECTIONNÉES : celles du dossier,
    mémorisées au téléchargement précédent (`saved` : décision → valeur), sinon celles d'usage du plan
    comptable du dossier (données du binding ; pour les achats, table des comptes par catégorie), à confirmer.
    Une catégorie inconnue n'est jamais devinée."""
    b = fmt.binding
    saved = saved or {}
    hint = b.suggestions.get(chart or "")
    table = CategoryAccountRegistry.find(b.target.system, b.target.product, chart, scope)
    lookup = b.lookup(LookupKind.CATEGORY)
    account = lookup.label if lookup else "Account"
    out = []
    for ref, name in sorted(categories.items()):
        decision = f"{MAPPING}CATEGORY:{ref}"
        rate_of = ref.rsplit(":", 1)[-1]  # « purchase:bürobedarf:19 », « sale:19 » : le taux en dernier
        usual = hint.categories.get(ref) if hint else None
        from_table = None
        if usual is None and table is not None and ref.count(":") == 2:
            _, key, rate = ref.split(":")
            usual = from_table = table.account_for(key, Decimal(rate))
        default = saved.get(decision) or usual
        if saved.get(decision):
            help_text = "Your account from a previous file, preselected."
        elif from_table and table is not None:
            help_text = f"Usual account of your chart of accounts ({table.status}), preselected."
        elif usual:
            help_text = (
                "Usual account of your chart of accounts, preselected: confirm it with your tax advisor."
            )
        else:
            help_text = (
                "Enter the account given by your tax advisor: an unknown category is never guessed."
                if scope == "PURCHASE"
                else "Enter the account given by your tax advisor."
            )
        out.append(
            QuickQuestion(
                question=MissingQuestion(
                    field=decision,
                    label=f"{account} for {name}",
                    kind=MissingKind.TEXT,
                    title=f"{account} for {name}",
                    help=help_text,
                    choices=[MissingChoice(value=default, label=default)] if default else [],
                    free_text=True,
                ),
                default=default,
                default_from=(
                    ("your_settings" if saved.get(decision) else "usual_default") if default else None
                ),
                decision=decision,
                mapping=QuickMapping(
                    category=name.removesuffix(f" at {rate_of} % VAT"),
                    rate=rate_of,
                    tax_decision=f"{MAPPING}TAX:rate:{scope}:{rate_of}",
                    automatic_accounts=list(table.automatic_accounts) if table else [],
                ),
            )
        )
    tax_lookup = b.lookup(LookupKind.TAX)
    tax = tax_lookup.label if tax_lookup else "Tax key"
    none = (tax_lookup.none_label if tax_lookup else None) or "No value"
    what = "purchases" if scope == "PURCHASE" else "sales"
    for rate in rates:
        ref = f"rate:{scope}:{rate}"
        usual = NO_VALUE if scope == "SALE" and hint and hint.no_tax_key else None
        tax_help = "The usual revenue accounts calculate the VAT themselves: no tax key."
        if scope == "PURCHASE" and table is not None:
            # achat : clé de Vorsteuer d'usage du taux ; 0 % : aucune taxe déductible, aucune clé
            usual = NO_VALUE if Decimal(rate) == 0 else table.tax_key_for(Decimal(rate))
            tax_help = (
                f"Usual input-tax key for this rate ({table.status}); automatic accounts "
                f"({', '.join(table.automatic_accounts) or 'none'}) never get a tax key."
                if Decimal(rate)
                else "No VAT on these purchases: no input tax, no tax key."
            )
        default = saved.get(f"{MAPPING}TAX:{ref}") or usual
        out.append(
            QuickQuestion(
                question=MissingQuestion(
                    field=f"{MAPPING}TAX:{ref}",
                    label=f"{tax} for {what} at {rate} % VAT",
                    kind=MissingKind.CHOICE,
                    title=f"{tax} for {what} at {rate} % VAT",
                    help=tax_help if default else "Enter the tax key given by your tax advisor.",
                    choices=[MissingChoice(value=NO_VALUE, label=none)],
                    free_text=True,
                ),
                default=default,
                default_from="usual_default" if default else None,
                decision=f"{MAPPING}TAX:{ref}",
            )
        )
    return out


def mapping_decisions(choices: dict[str, str], defaults: dict[str, str | None]) -> dict[tuple[str, str], Any]:
    """Réponses des correspondances → décisions (sorte, référence) au format attendu par la préparation."""
    out: dict[tuple[str, str], Any] = {}
    for key, value in choices.items():
        if not key.startswith(MAPPING) or not value:
            continue
        kind, _, ref = key[len(MAPPING) :].partition(":")
        target = None if value == NO_VALUE else value.strip()
        origin = "SUGGESTION_ACCEPTED" if defaults.get(key) == value else "MANUAL"
        out[(kind, ref)] = SimpleNamespace(target_value=target, origin=origin)
    return out


def from_value_question(q: ValueQuestion) -> QuickQuestion:
    """Décision du constructeur du CDM ou du moteur de valeurs → question du démarrage rapide."""
    return QuickQuestion(
        question=MissingQuestion(
            field=q.key,
            label=q.title,
            kind=MissingKind.CHOICE,
            title=q.title,
            help=q.help,
            choices=[MissingChoice(value=o.value, label=o.label) for o in q.options],
            free_text=False,
        ),
        default=q.answer or q.suggested,
        default_from="usual_default" if (q.answer or q.suggested) else None,
        decision=q.key,
    )


def blocked_reasons(issues: list[Any]) -> str:
    """Raisons bloquantes d'un enregistrement cible, en une phrase."""
    return "; ".join(dict.fromkeys(i.message for i in issues if i.level.value == "BLOCKING")) or "Blocked"


__all__ = [
    "FormatTarget",
    "data_type_of_file",
    "format_target",
    "from_value_question",
    "mapping_decisions",
    "mapping_questions",
    "matched_keys",
    "role_of",
    "setting_values",
    "settings_questions",
    "structure",
]
