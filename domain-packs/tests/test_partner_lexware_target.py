"""Phase 1a Lexware Office — cible générique PARTNER → contact, SANS AUCUNE SOURCE PARTICULIÈRE.

Ces tests construisent le Partner CDM à la main, ou le font fournir par un système IMAGINAIRE (« acme-erp »).
Aucun connecteur de source n'y est importé : ils prouvent que la préparation d'un contact ne dépend que du
Partner CDM, du binding (données) et du contrat du Registry. Si remplacer la source demandait de toucher à la
cible, ces tests ne pourraient pas exister.

Hors ligne : contrat minimal du connecteur, faux serveur pour la connexion ; rien n'est envoyé."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import uie_domain_packs  # noqa: F401  (enregistre les bindings cibles)
import yaml
from uie_cdm import (
    Address,
    BankAccount,
    ContactInfo,
    Identifier,
    IdentifierScheme,
    Partner,
    PartnerKind,
    PartnerRole,
    PersonName,
    TaxInfo,
)
from uie_connectors.lexware import LexwareSchemaLoader
from uie_connectors.lexware.spec import DEFAULT_CONTRACT
from uie_core.mapping import (
    RecordRequestBuilderRegistry,
    RecordStatus,
    ResolvedBinding,
    TargetBindingRegistry,
)
from uie_core.partners import (
    prepare_partners,
)

ROOT = Path(__file__).resolve().parents[2]
BINDING_FILE = ROOT / "domain-packs" / "partner" / "targets" / "lexware_contacts.yaml"
LEXWARE_SRC = ROOT / "packages" / "connectors" / "src" / "uie_connectors" / "lexware"


# --------------------------------------------------------------------------- #
# Partner CDM synthétique : aucune source
# --------------------------------------------------------------------------- #
def company(**over: Any) -> Partner:
    data: dict[str, Any] = {
        "source_ref": "SAP-BP/0000104711",  # une référence qui n'a la forme d'aucun système particulier
        "kind": PartnerKind.ORGANIZATION,
        "name": "Nordwind Handel AG",
        "organization_name": "Nordwind Handel AG",
        "roles": [PartnerRole.CUSTOMER],
        "addresses": [
            Address(
                street="Hafenstr. 12",
                street2="Kontor 3",
                postal_code="20095",
                city="Hamburg",
                country_code="DE",
            )
        ],
        "tax": TaxInfo(vat_id="DE123456789", tax_number="27/123/45678"),
        "contact": ContactInfo(
            email="info@nordwind.example", phone="+49 40 1234", mobile="+49 170 1234", fax="+49 40 1235"
        ),
    }
    return Partner(**{**data, **over})


def person(**over: Any) -> Partner:
    data: dict[str, Any] = {
        "source_ref": "customers.csv:row-7",
        "kind": PartnerKind.PERSON,
        "name": "Lena Vogel",
        "person": PersonName(full_name="Lena Vogel", first_name="Lena", last_name="Vogel"),
        "roles": [PartnerRole.CUSTOMER],
    }
    return Partner(**{**data, **over})


def resolved_from(spec: Path | None = None) -> ResolvedBinding:
    """Binding résolu contre le contrat, SANS base de données : la préparation est pure."""
    snap = LexwareSchemaLoader({"spec": spec} if spec else None).build_snapshot()
    binding = TargetBindingRegistry.get("partner", "lexware", "public-api")
    return ResolvedBinding(
        binding=binding,
        snapshot_id=uuid.uuid4(),
        snapshot_label="lexware public-api v1 @test",
        format_version=snap.version,
        metadata=dict(snap.metadata),
        fields=list(snap.entity("contacts").fields),
        header_fields=[],
    )


def prepare(*partners: Partner, spec: Path | None = None):  # noqa: ANN201
    builder = RecordRequestBuilderRegistry.get("lexware", "rest")
    return prepare_partners(list(partners), resolved_from(spec), builder)


# --------------------------------------------------------------------------- #
# D. Préparation
# --------------------------------------------------------------------------- #
def test_binding_is_an_api_target_resolved_against_the_registry_contract():
    binding = TargetBindingRegistry.get("partner", "lexware", "public-api")
    assert (binding.delivery, binding.records, binding.source.model) == ("api", "single", "partner")
    assert binding.target.versions == ["v1"] and binding.target.entity == "contacts"
    # une cible par API n'apparaît jamais parmi les formats de fichier, et inversement
    assert binding not in TargetBindingRegistry.for_model("partner")
    assert TargetBindingRegistry.for_model("partner", delivery="api") == [binding]
    resolved = resolved_from()  # toute position du binding existe dans le contrat
    assert binding.positions() <= {f.technical_name for f in resolved.fields}
    # le binding n'écrit que des champs que le contrat permet d'envoyer
    writable = {
        f.technical_name for f in resolved.fields if not f.readonly and not f.metadata.get("protocol")
    }
    assert {f.field for f in binding.fields} <= writable


def test_synthetic_company_becomes_a_contact_request_without_any_source():
    (p,) = prepare(company())
    assert p.status == RecordStatus.READY and p.issues == []
    assert p.request == {
        "roles": {"customer": {}},
        "company": {
            "name": "Nordwind Handel AG",
            "vatRegistrationId": "DE123456789",
            "taxNumber": "27/123/45678",
        },
        "addresses": {
            "billing": [
                {
                    "street": "Hafenstr. 12",
                    "supplement": "Kontor 3",
                    "zip": "20095",
                    "city": "Hamburg",
                    "countryCode": "DE",
                }
            ]
        },
        "emailAddresses": {"business": ["info@nordwind.example"]},
        "phoneNumbers": {"business": ["+49 40 1234"], "mobile": ["+49 170 1234"], "fax": ["+49 40 1235"]},
    }
    # la référence source est conservée telle quelle, sans être interprétée
    assert p.source_ref == "SAP-BP/0000104711" and p.kind == "ORGANIZATION" and p.roles == ["CUSTOMER"]
    # aucune donnée attribuée par la cible ni champ technique d'échange dans la requête
    assert "person" not in p.request and "version" not in p.request and "id" not in p.request
    assert "number" not in json.dumps(p.request)


def test_person_customer_supplier_and_both_roles():
    lena, supplier, both = prepare(
        person(),
        company(source_ref="V-1", roles=[PartnerRole.SUPPLIER]),
        company(source_ref="B-1", roles=[PartnerRole.CUSTOMER, PartnerRole.SUPPLIER]),
    )
    assert lena.status == RecordStatus.READY
    assert lena.request == {"roles": {"customer": {}}, "person": {"firstName": "Lena", "lastName": "Vogel"}}
    assert supplier.request["roles"] == {
        "vendor": {}
    }  # le rôle fournisseur du CDM = « vendor » dans le contrat
    assert both.request["roles"] == {"customer": {}, "vendor": {}}  # UN contact, deux rôles
    assert both.status == RecordStatus.READY


def test_a_person_without_a_separate_last_name_is_blocked_never_guessed():
    full_only = person(person=PersonName(full_name="Lena Vogel"))
    no_person = person(source_ref="p2", person=None)
    first_only = person(source_ref="p3", person=PersonName(full_name="Lena Vogel", first_name="Lena"))
    for p in prepare(full_only, no_person, first_only):
        assert p.status == RecordStatus.BLOCKED and p.request is None
        messages = [i.message for i in p.issues]
        assert messages == [
            "Lexware Office requires a last name, and the source does not provide one separately from the "
            "full name"
        ]
        # le nom complet n'est jamais glissé dans le nom de famille
        assert not any(v.field == "person.lastName" for v in p.values)


def test_unknown_kind_and_missing_role_are_blocked_with_a_reason():
    (unknown,) = prepare(company(kind=PartnerKind.UNKNOWN, organization_name=None))
    assert unknown.status == RecordStatus.BLOCKED
    assert [i.message for i in unknown.issues] == [
        "Lexware Office needs to know whether this partner is a company or a person"
    ]
    (no_role,) = prepare(company(roles=[]))
    assert no_role.status == RecordStatus.BLOCKED
    assert [i.message for i in no_role.issues] == [
        "Lexware Office needs at least one role (customer or supplier)"
    ]


def test_country_is_required_as_soon_as_an_address_is_sent_and_is_never_invented():
    (p,) = prepare(company(addresses=[Address(street="Hafenstr. 12", city="Hamburg")]))
    assert p.status == RecordStatus.BLOCKED and p.request is None
    assert [i.message for i in p.issues] == ["Billing country is required and has no value"]
    assert not any(v.field == "addresses.billing.countryCode" for v in p.values)  # jamais « DE » par défaut
    # sans adresse du tout, rien n'est exigé : l'obligation vient du contrat (groupe de l'adresse)
    (bare,) = prepare(company(addresses=[]))
    assert bare.status == RecordStatus.READY and "addresses" not in bare.request


def test_data_the_target_cannot_receive_is_left_out_with_a_warning_not_silently():
    rich = company(
        bank_accounts=[BankAccount(iban="DE89370400440532013000", is_main=True)],
        contact=ContactInfo(email="info@nordwind.example", website="https://nordwind.example"),
        identifiers=[
            Identifier(scheme=IdentifierScheme.CUSTOMER_NUMBER, value="4711"),
            Identifier(scheme=IdentifierScheme.SUPPLIER_NUMBER, value="70011"),
        ],
    )
    (p,) = prepare(rich)
    assert p.status == RecordStatus.WARNING and p.request is not None
    assert sorted(i.message for i in p.issues) == [
        "Bank accounts cannot be sent to Lexware Office: left out",
        "Customer number is not sent: Lexware Office assigns its own number",
        "Supplier number is not sent: Lexware Office assigns its own number",
        "Website cannot be sent to Lexware Office: left out",
    ]
    sent = json.dumps(p.request)
    assert (
        "DE8937" not in sent
        and "4711" not in sent
        and "70011" not in sent
        and "nordwind.example/" not in sent
    )
    # une personne ne peut pas porter de numéro de TVA dans le contrat : écarté et dit
    (lena,) = prepare(person(tax=TaxInfo(vat_id="DE999999999")))
    assert lena.status == RecordStatus.WARNING
    assert [i.message for i in lena.issues] == ["VAT ID can only be sent for a company"]
    assert "DE999999999" not in json.dumps(lena.request)


def test_shipping_address_and_only_one_address_per_kind():
    (p,) = prepare(
        company(
            addresses=[
                Address(street="Hafenstr. 12", city="Hamburg", country_code="DE"),
                Address(
                    usage="DELIVERY", street="Lager 4", postal_code="21107", city="Hamburg", country_code="DE"
                ),
                Address(usage="POSTAL", po_box="1234", city="Hamburg", country_code="DE"),
            ]
        )
    )
    assert p.status == RecordStatus.READY
    assert len(p.request["addresses"]["billing"]) == 1 and len(p.request["addresses"]["shipping"]) == 1
    assert p.request["addresses"]["shipping"][0] == {
        "street": "Lager 4",
        "zip": "21107",
        "city": "Hamburg",
        "countryCode": "DE",
    }


def test_registry_limits_are_enforced_without_truncation(tmp_path):
    """Limites du CONTRAT : une valeur trop longue ou hors liste n'est jamais coupée ni corrigée."""
    data = yaml.safe_load(DEFAULT_CONTRACT.read_text(encoding="utf-8"))
    for f in data["resources"][1]["fields"]:
        if f["path"] == "company.name":
            f["length"] = 10  # champ d'identité
        if f["path"] == "addresses.billing[0].city":
            f["length"] = 5  # champ facultatif
        if f["path"] == "addresses.billing[0].countryCode":
            f["type"], f["enum"] = "enum", ["DE", "FR"]
    spec = tmp_path / "contract.yaml"
    spec.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    (too_long,) = prepare(company(), spec=spec)
    assert too_long.status == RecordStatus.BLOCKED and too_long.request is None
    assert "Company name is longer than 10 characters" in [i.message for i in too_long.issues]
    assert "Billing city is longer than 5 characters: left out" in [i.message for i in too_long.issues]
    assert not any(v.field == "company.name" for v in too_long.values)  # ni coupée ni envoyée

    short = company(name="Nord AG", organization_name="Nord AG")
    (city_out,) = prepare(short, spec=spec)
    # champ facultatif trop long : écarté avec un avertissement, le reste de l'adresse part (pays présent)
    assert city_out.status == RecordStatus.WARNING
    assert [i.message for i in city_out.issues] == ["Billing city is longer than 5 characters: left out"]
    assert "city" not in city_out.request["addresses"]["billing"][0]

    ok = company(
        name="Nord AG", organization_name="Nord AG", addresses=[Address(city="Kiel", country_code="DE")]
    )
    (fine,) = prepare(ok, spec=spec)
    assert fine.status == RecordStatus.READY and fine.request["addresses"]["billing"][0]["city"] == "Kiel"

    swiss = company(
        name="Nord AG", organization_name="Nord AG", addresses=[Address(city="Bern", country_code="CH")]
    )
    (bad_enum,) = prepare(swiss, spec=spec)
    # pays hors liste : écarté, donc l'adresse n'a plus de pays → bloqué (jamais une adresse sans pays)
    assert bad_enum.status == RecordStatus.BLOCKED and bad_enum.request is None
    assert [i.message for i in bad_enum.issues] == [
        "Billing country is not one of the values accepted by the target: left out",
        "Billing country is required and has no value",
    ]
