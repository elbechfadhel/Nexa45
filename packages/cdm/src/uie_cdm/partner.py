"""Partner CDM 1.0.0 : un partenaire commercial (client, fournisseur), indépendant de tout système.

Ni notion Odoo (rangs, res.partner), ni notion DATEV (compte de personne, Adressattyp) : un système source le
REMPLIT, une cible le TRADUIT. Le numéro de compte d'un format comptable n'est pas un attribut du partenaire :
c'est une donnée de la cible (attribuée à l'export).

`source_ref` est un identifiant opaque de la source (relecture, attribution stable d'un compte) : jamais
affiché ni écrit dans un fichier cible.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PARTNER_MODEL_VERSION = "1.1.0"  # 1.1.0 : contact.contact_person (personne à contacter)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PartnerKind(StrEnum):
    ORGANIZATION = "ORGANIZATION"
    PERSON = "PERSON"
    UNKNOWN = "UNKNOWN"


class PartnerRole(StrEnum):
    CUSTOMER = "CUSTOMER"
    SUPPLIER = "SUPPLIER"


class AddressUsage(StrEnum):
    MAIN = "MAIN"
    INVOICE = "INVOICE"
    DELIVERY = "DELIVERY"
    POSTAL = "POSTAL"


class IdentifierScheme(StrEnum):
    CUSTOMER_NUMBER = "customer_number"  # numéro de client chez l'émetteur
    SUPPLIER_NUMBER = "supplier_number"  # numéro de fournisseur chez l'émetteur
    PARTNER_REFERENCE = "partner_reference"  # référence interne libre
    COMPANY_REGISTRY = "company_registry"  # registre du commerce


class PersonName(_Model):
    """Nom d'une personne. `first_name` / `last_name` seulement si la SOURCE les sépare : jamais déduits
    d'un nom complet."""

    full_name: str = Field(min_length=1)
    first_name: str | None = None
    last_name: str | None = None


class Address(_Model):
    usage: AddressUsage = AddressUsage.MAIN
    street: str | None = None
    street2: str | None = None
    po_box: str | None = None
    postal_code: str | None = None
    city: str | None = None
    region: str | None = None
    country_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")  # ISO 3166-1 alpha-2

    def is_empty(self) -> bool:
        return not any(
            (self.street, self.street2, self.po_box, self.postal_code, self.city, self.country_code)
        )


class Identifier(_Model):
    scheme: IdentifierScheme
    value: str = Field(min_length=1)


class TaxInfo(_Model):
    vat_id: str | None = None  # numéro de TVA intracommunautaire COMPLET, préfixe pays compris
    tax_number: str | None = None  # numéro fiscal national


class ContactInfo(_Model):
    contact_person: str | None = None  # 1.1.0 : personne à contacter chez le partenaire (nom tel qu'écrit)
    email: str | None = None
    phone: str | None = None
    mobile: str | None = None
    fax: str | None = None
    website: str | None = None


_IBAN = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{10,30}$")


def is_valid_iban(value: str) -> bool:
    """Forme + clé de contrôle ISO 13616 (mod 97)."""
    iban = re.sub(r"\s+", "", value).upper()
    if not _IBAN.match(iban):
        return False
    digits = "".join(str(int(c, 36)) for c in iban[4:] + iban[:4])
    return int(digits) % 97 == 1


class BankAccount(_Model):
    iban: str | None = None  # normalisé (sans espaces, majuscules) et valide, sinon dans account_number
    account_number: str | None = None
    bic: str | None = None
    bank_name: str | None = None
    holder_name: str | None = None
    is_main: bool = False

    @field_validator("iban")
    @classmethod
    def _iban(cls, value: str | None) -> str | None:
        if value is None:
            return None
        iban = re.sub(r"\s+", "", value).upper()
        if not is_valid_iban(iban):
            raise ValueError("IBAN invalide")
        return iban


class Partner(_Model):
    model_version: str = PARTNER_MODEL_VERSION
    source_ref: str = Field(min_length=1)
    kind: PartnerKind
    name: str = Field(min_length=1)
    organization_name: str | None = None
    person: PersonName | None = None
    short_name: str | None = None
    roles: list[PartnerRole] = []
    identifiers: list[Identifier] = []
    addresses: list[Address] = []
    tax: TaxInfo = TaxInfo()
    contact: ContactInfo = ContactInfo()
    bank_accounts: list[BankAccount] = []
    language: str | None = Field(default=None, pattern=r"^[a-z]{2}$")  # ISO 639-1
    active: bool = True

    @model_validator(mode="after")
    def _consistency(self) -> Partner:
        if len(set(self.roles)) != len(self.roles):
            raise ValueError("rôle en double")
        if self.kind == PartnerKind.ORGANIZATION and self.person is not None:
            raise ValueError("une organisation n'a pas de nom de personne")
        if self.kind == PartnerKind.PERSON and self.organization_name is not None:
            raise ValueError("une personne n'a pas de nom d'organisation")
        if sum(1 for b in self.bank_accounts if b.is_main) > 1:
            raise ValueError("un seul compte bancaire principal")
        return self

    def identifier(self, scheme: IdentifierScheme) -> str | None:
        return next((i.value for i in self.identifiers if i.scheme == scheme), None)

    def address(self, usage: AddressUsage = AddressUsage.MAIN) -> Address | None:
        return next((a for a in self.addresses if a.usage == usage), None)

    def mapping_view(self, role: PartnerRole | None = None) -> dict[str, Any]:
        """Vue plate du partenaire pour un moteur de mapping : chemins stables (`name`, `person.last_name`,
        `address.MAIN.city`, `identifiers.customer_number`, `tax.vat_id`, `contact.email`, `role`,
        `roles.CUSTOMER`…) ; les
        comptes bancaires restent une liste (`bank_accounts`). Valeurs absentes = clé absente."""
        view: dict[str, Any] = {
            "kind": self.kind.value,
            "name": self.name,
            "organization_name": self.organization_name,
            "short_name": self.short_name,
            "language": self.language,
            "active": self.active,
            "role": role.value if role else None,
            # tous les rôles du partenaire (une cible à UN enregistrement par partenaire les lit ainsi)
            **{f"roles.{r.value}": True for r in self.roles},
            "tax.vat_id": self.tax.vat_id,
            "tax.tax_number": self.tax.tax_number,
        }
        if self.person:
            view |= {f"person.{k}": v for k, v in self.person.model_dump().items()}
        view |= {f"contact.{k}": v for k, v in self.contact.model_dump().items()}
        view |= {f"identifiers.{i.scheme.value}": i.value for i in self.identifiers}
        for a in self.addresses:
            view |= {f"address.{a.usage.value}.{k}": v for k, v in a.model_dump(exclude={"usage"}).items()}
        view["bank_accounts"] = [b.model_dump() for b in self.bank_accounts]
        return {k: v for k, v in view.items() if v is not None and v != []}


def partner_json_schema() -> dict:
    """Contrat publié (JSON Schema) du Partner CDM."""
    schema = Partner.model_json_schema()
    schema["$id"] = f"https://go45.io/uie/cdm/partner-{PARTNER_MODEL_VERSION}.json"
    schema["title"] = "Partner"
    return schema


if __name__ == "__main__":  # régénère le contrat publié : uv run python -m uie_cdm.partner
    import json
    from pathlib import Path

    out = Path(__file__).resolve().parents[2] / "schemas" / f"partner-{PARTNER_MODEL_VERSION}.json"
    out.write_text(json.dumps(partner_json_schema(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(out)
