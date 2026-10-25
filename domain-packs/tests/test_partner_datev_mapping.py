"""Partner CDM → DATEV Debitoren/Kreditoren v5 : le binding YAML sur la spec RÉELLE du Registry (locale), avec
des partenaires INVENTÉS. Sauté si la spec locale n'est pas générée (spec_builder, sources non versionnées).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import uie_connectors  # noqa: F401
import uie_domain_packs  # noqa: F401
from uie_cdm import (
    Address,
    BankAccount,
    Identifier,
    IdentifierScheme,
    Partner,
    PartnerKind,
    PartnerRole,
    PersonName,
    TaxInfo,
)
from uie_connectors.datev import DatevSchemaLoader
from uie_core.identity import SystemContext
from uie_core.mapping import (
    RecordFileWriterRegistry,
    RecordStatus,
    TargetBindingRegistry,
    map_record,
    resolve_binding,
)
from uie_core.registry import RegistryService

ROOT = Path(__file__).resolve().parents[2]
REAL_SPEC = ROOT / "var" / "datev" / "specs" / "extf_700" / "debitoren-kreditoren_v5.yaml"
pytestmark = pytest.mark.skipif(not REAL_SPEC.exists(), reason="spec DATEV locale absente (spec_builder)")
IBAN = "DE89370400440532013000"


@pytest.fixture
def resolved(session):  # noqa: ANN001, ANN201
    registry = RegistryService(session, SystemContext("test"))
    registry.publish_snapshot(DatevSchemaLoader({"spec": REAL_SPEC}).build_snapshot())
    return resolve_binding(registry, TargetBindingRegistry.get("partner", "datev", "debitoren-kreditoren"))


def _map(resolved, partner: Partner, role: PartnerRole, account: str = "10000"):  # noqa: ANN001, ANN202
    writer = RecordFileWriterRegistry.get("datev", "extf")
    return map_record(
        partner.mapping_view(role),
        resolved,
        writer,
        "cp1252",
        source_ref=partner.source_ref,
        target_values={"account_number": account},
    )


def _company(**kw) -> Partner:  # noqa: ANN003
    base = {
        "source_ref": "10",
        "kind": PartnerKind.ORGANIZATION,
        "name": "Holzbau Beispiel GmbH",
        "organization_name": "Holzbau Beispiel GmbH",
        "roles": [PartnerRole.CUSTOMER],
        "addresses": [
            Address(street="Hauptstraße 1", postal_code="80331", city="München", country_code="DE")
        ],
    }
    return Partner(**(base | kw))


def test_company_customer(resolved):
    p = _company(
        tax=TaxInfo(vat_id="DE123456789"),
        language="de",
        identifiers=[Identifier(scheme=IdentifierScheme.CUSTOMER_NUMBER, value="10042")],
        bank_accounts=[BankAccount(iban=IBAN, bic="BEISDEMMXXX", bank_name="Beispielbank", is_main=True)],
    )
    rec = _map(resolved, p, PartnerRole.CUSTOMER)
    assert rec.status == RecordStatus.READY, rec.issues
    assert {k: rec.values[k] for k in ("001", "002", "007", "009", "010", "016", "018", "019", "020")} == {
        "001": "10000",
        "002": "Holzbau Beispiel GmbH",
        "007": "2",
        "009": "DE",
        "010": "123456789",
        "016": "Hauptstraße 1",
        "018": "80331",
        "019": "München",
        "020": "DE",
    }
    assert (rec.values["099"], rec.values["101"]) == ("10042", "1")
    assert (rec.values["042"], rec.values["045"], rec.values["047"], rec.values["049"]) == (
        "Beispielbank",
        IBAN,
        "BEISDEMMXXX",
        "1",
    )
    assert "004" not in rec.values  # jamais les champs de nom d'un autre type d'adressat


def test_person_full_name_in_name_field_never_split(resolved):
    p = Partner(
        source_ref="11",
        kind=PartnerKind.PERSON,
        name="Anna Beispiel",
        person=PersonName(full_name="Anna Beispiel"),
        roles=[PartnerRole.CUSTOMER],
        addresses=[Address(country_code="DE")],
        language="en",
    )
    rec = _map(resolved, p, PartnerRole.CUSTOMER)
    assert (rec.values["004"], rec.values["007"], rec.values["101"]) == ("Anna Beispiel", "1", "5")
    assert "005" not in rec.values and "002" not in rec.values


def test_supplier_number_only_on_the_supplier_record(resolved):
    p = _company(
        roles=[PartnerRole.CUSTOMER, PartnerRole.SUPPLIER],
        identifiers=[
            Identifier(scheme=IdentifierScheme.CUSTOMER_NUMBER, value="K1"),
            Identifier(scheme=IdentifierScheme.SUPPLIER_NUMBER, value="L1"),
        ],
    )
    assert _map(resolved, p, PartnerRole.CUSTOMER).values["099"] == "K1"
    assert _map(resolved, p, PartnerRole.SUPPLIER, "70000").values["099"] == "L1"


def test_warnings_and_blocking(resolved):
    foreign = _company(
        addresses=[Address(street="Kade 4", postal_code="1012 AB", city="Amsterdam", country_code="NL")],
        language="nl",
        bank_accounts=[BankAccount(account_number="123456", is_main=True)],
    )
    rec = _map(resolved, foreign, PartnerRole.SUPPLIER, "70000")
    assert rec.status == RecordStatus.WARNING
    assert {i.message for i in rec.issues} == {
        "Postleitzahl does not have the format expected by the target: left out",
        "Sprache value « nl » is not supported by the target",
        "1 bank accounts not written: IBAN missing",
    }

    no_country = _company(addresses=[Address(street="Waldweg 1", postal_code="24103", city="Kiel")])
    rec = _map(resolved, no_country, PartnerRole.SUPPLIER, "70001")
    assert rec.status == RecordStatus.WARNING and "020" not in rec.values  # jamais « DE » inventé
    assert [i.rule_id for i in rec.issues] == ["D02"]

    long_name = _company(organization_name="X" * 60, name="X" * 60)
    rec = _map(resolved, long_name, PartnerRole.CUSTOMER)
    assert rec.status == RecordStatus.BLOCKED
    assert rec.issues[0].message == "Name (Adressattyp Unternehmen) is longer than 50 characters"
