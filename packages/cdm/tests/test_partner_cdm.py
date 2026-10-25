"""Partner CDM : validation, cohérence et contrat JSON Schema publié (à jour)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from uie_cdm import (
    PARTNER_MODEL_VERSION,
    Address,
    BankAccount,
    Identifier,
    IdentifierScheme,
    Partner,
    PartnerKind,
    PartnerRole,
    PersonName,
    is_valid_iban,
    partner_json_schema,
)

SCHEMA = Path(__file__).resolve().parents[1] / "schemas" / f"partner-{PARTNER_MODEL_VERSION}.json"
IBAN = "DE89370400440532013000"  # IBAN d'exemple de la norme (structure publique), aucune donnée réelle


def _org(**kw) -> Partner:  # noqa: ANN003
    return Partner(source_ref="1", kind=PartnerKind.ORGANIZATION, name="Holzbau Beispiel GmbH", **kw)


def test_organization_with_roles_address_and_bank():
    p = _org(
        organization_name="Holzbau Beispiel GmbH",
        roles=[PartnerRole.CUSTOMER, PartnerRole.SUPPLIER],
        identifiers=[Identifier(scheme=IdentifierScheme.CUSTOMER_NUMBER, value="4711")],
        addresses=[Address(street="Hauptstraße 1", postal_code="80331", city="München", country_code="DE")],
        bank_accounts=[BankAccount(iban="de89 3704 0044 0532 0130 00", is_main=True)],
        language="de",
    )
    assert p.identifier(IdentifierScheme.CUSTOMER_NUMBER) == "4711"
    assert p.address().city == "München"
    assert p.bank_accounts[0].iban == IBAN
    assert p.model_version == PARTNER_MODEL_VERSION


def test_person_name_is_never_split():
    p = Partner(
        source_ref="2",
        kind=PartnerKind.PERSON,
        name="Anna Beispiel",
        person=PersonName(full_name="Anna Beispiel"),
    )
    assert (p.person.first_name, p.person.last_name) == (None, None)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"roles": [PartnerRole.CUSTOMER, PartnerRole.CUSTOMER]},
        {"person": PersonName(full_name="X")},
        {"bank_accounts": [{"is_main": True}, {"is_main": True}]},
        {"addresses": [{"country_code": "Germany"}]},
        {"language": "de_DE"},
        {"bank_accounts": [{"iban": "DE00370400440532013000"}]},
        {"unknown": 1},
    ],
)
def test_invalid_partners_are_refused(kwargs):  # noqa: ANN001
    with pytest.raises(ValidationError):
        _org(**kwargs)


def test_iban_check_digits():
    assert is_valid_iban(IBAN) and not is_valid_iban("DE89370400440532013001") and not is_valid_iban("1234")


def test_published_json_schema_is_up_to_date():
    """Contrat publié : regénérer avec `uv run python -m uie_cdm.partner` si le modèle change (SemVer)."""
    assert json.loads(SCHEMA.read_text(encoding="utf-8")) == partner_json_schema()
