"""Moteur de mapping générique sur un format INVENTÉ (système « demo »), sans aucun format réel : résolution
contre le Registry, sources, transformations, emplacements répétés, validation par métadonnées, en-tête."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from uie_core.identity import SystemContext
from uie_core.mapping import (
    IssueLevel,
    RecordFileWriter,
    RecordStatus,
    TargetBinding,
    TargetBindingError,
    check_value,
    header_values,
    map_record,
    resolve_binding,
)
from uie_core.registry import DataType, EntityDef, FieldDef, RegistryService, SchemaSnapshotModel


def _f(pos: int, label: str, kind: str, **meta: Any) -> FieldDef:
    data_type = DataType.INTEGER if kind == "num" else DataType.STRING
    required = meta.pop("required", False)
    return FieldDef(
        technical_name=f"{pos:02d}",
        label=label,
        data_type=data_type,
        native_type=kind,
        required=required,
        metadata={"position": pos, **meta},
    )


def _snapshot() -> SchemaSnapshotModel:
    return SchemaSnapshotModel(
        system="demo",
        product="contacts",
        version="2",
        interface="file",
        interface_type="file",
        metadata={"product_name": "Demo contacts", "data_category": 9},
        entities=[
            EntityDef(
                technical_name="row",
                fields=[
                    _f(1, "Number", "num", required=True, length=5, pattern=r"^\d{5}$"),
                    _f(2, "Company", "text", length=10),
                    _f(3, "Person", "text", length=10),
                    _f(4, "Type", "text", length=1),
                    _f(5, "Country", "text", length=2, pattern=r'^"[A-Z]{2}"$'),
                    _f(6, "VAT prefix", "text", length=2),
                    _f(7, "Zip", "text", length=5, pattern=r'^"\d{0,5}"$'),
                    _f(8, "IBAN 1", "text", length=34),
                    _f(9, "Main 1", "text", length=1),
                    _f(10, "IBAN 2", "text", length=34),
                    _f(11, "Main 2", "text", length=1),
                    _f(12, "Lang", "num", length=1),
                ],
            ),
            EntityDef(
                technical_name="head",
                fields=[
                    _f(1, "Mark", "text"),
                    _f(2, "Category", "num"),
                    _f(3, "Created", "num"),
                    _f(4, "Start", "num", pattern=r"^20\d{6}$"),
                ],
            ),
        ],
    )


BINDING = TargetBinding.model_validate(
    {
        "binding_version": "1.0.0",
        "name": "Demo contacts",
        "source": {"model": "partner", "version": "1"},
        "target": {
            "system": "demo",
            "product": "contacts",
            "versions": ["1", "2"],
            "interface": "file",
            "entity": "row",
            "header_entity": "head",
        },
        "records": "per_role",
        "fields": [
            {"field": "01", "source": "target.account_number", "identity": True},
            {
                "field": "02",
                "source": "organization_name",
                "when": {"kind": ["ORGANIZATION"]},
                "identity": True,
            },
            {
                "field": "03",
                "source": "person.last_name",
                "fallback": "person.full_name",
                "when": {"kind": ["PERSON"]},
            },
            {"field": "04", "source": "kind", "map": {"PERSON": "P", "ORGANIZATION": "O"}},
            {"field": "05", "source": "address.MAIN.country_code"},
            {"field": "06", "source": "tax.vat_id", "extract": {"regex": "^([A-Z]{2})(.+)$"}},
            {"field": "07", "source": "address.MAIN.postal_code"},
            {"field": "12", "source": "language", "map": {"de": "1"}},
        ],
        "repeat": [
            {
                "source": "bank_accounts",
                "label": "bank accounts",
                "require": "iban",
                "require_label": "IBAN",
                "slots": [{"iban": "08", "is_main": "09"}, {"iban": "10", "is_main": "11"}],
                "map": {"is_main": {"true": "1", "false": "0"}},
            }
        ],
        "rules": [
            {
                "id": "R1",
                "kind": "required_unless",
                "field": "05",
                "source": "address.MAIN.country_code",
                "equals": "DE",
                "message": "Country missing",
            }
        ],
        "header": {
            "01": {"constant": "DEMO"},
            "02": {"snapshot": "data_category"},
            "03": {"generated": "timestamp_ms"},
            "04": {"profile": "start"},
        },
        "profile": [{"name": "start", "label": "Start", "type": "date", "header_field": "04"}],
    }
)


class DemoWriter(RecordFileWriter):
    system_code = "demo"
    interface_code = "file"

    def format_cell(self, field: FieldDef, value: Any) -> str:
        if value is None:
            return '""' if field.native_type == "text" else ""
        return f'"{value}"' if field.native_type == "text" else str(value)

    def render(self, header_fields, header_values, fields, rows, encoding):  # noqa: ANN001, ANN201
        lines = [";".join(self.format_cell(f, header_values.get(f.technical_name)) for f in header_fields)]
        lines += [";".join(self.format_cell(f, r.get(f.technical_name)) for f in fields) for r in rows]
        return "\n".join(lines).encode(encoding)


@pytest.fixture
def resolved(session):  # noqa: ANN001, ANN201
    registry = RegistryService(session, SystemContext("test"))
    registry.publish_snapshot(_snapshot())
    return resolve_binding(registry, BINDING)


def _org(**extra: Any) -> dict[str, Any]:
    return {"kind": "ORGANIZATION", "name": "Acme", "organization_name": "Acme", "role": "CUSTOMER", **extra}


def _map(resolved, view, account="10001", encoding="cp1252"):  # noqa: ANN001, ANN202
    return map_record(
        view, resolved, DemoWriter(), encoding, source_ref="1", target_values={"account_number": account}
    )


def test_resolution_picks_the_latest_version_in_the_registry(resolved):
    assert (resolved.format_version, resolved.snapshot_label) == ("2", "demo contacts 2 @1.0.0")
    assert [f.technical_name for f in resolved.fields][:3] == ["01", "02", "03"]
    assert resolved.field("01").label == "Number" and resolved.header_field("01").label == "Mark"


def test_unknown_position_is_refused(session):
    registry = RegistryService(session, SystemContext("test"))
    registry.publish_snapshot(_snapshot())
    data = BINDING.model_dump()
    data["fields"] = [*data["fields"], {"field": "99", "source": "name"}]
    with pytest.raises(TargetBindingError, match="99"):
        resolve_binding(registry, TargetBinding.model_validate(data))


def test_missing_format_is_refused(session):
    with pytest.raises(TargetBindingError):
        resolve_binding(RegistryService(session, SystemContext("test")), BINDING)


def test_ready_record(resolved):
    view = _org(
        **{"address.MAIN.country_code": "DE", "address.MAIN.postal_code": "80331", "tax.vat_id": "DE123"},
        language="de",
        bank_accounts=[{"iban": "DE89", "is_main": True}, {"iban": "AT61", "is_main": False}],
    )
    rec = _map(resolved, view)
    assert rec.status == RecordStatus.READY, rec.issues
    assert rec.values == {
        "01": "10001",
        "02": "Acme",
        "04": "O",
        "05": "DE",
        "06": "DE",
        "07": "80331",
        "12": "1",
        "08": "DE89",
        "09": "1",
        "10": "AT61",
        "11": "0",
    }


def test_person_uses_fallback_and_never_the_company_field(resolved):
    rec = _map(
        resolved, {"kind": "PERSON", "name": "Anna B", "person.full_name": "Anna B", "role": "CUSTOMER"}
    )
    assert rec.values["03"] == "Anna B" and "02" not in rec.values and rec.values["04"] == "P"


def test_invalid_optional_value_is_left_out_with_a_warning(resolved):
    rec = _map(
        resolved,
        _org(**{"address.MAIN.country_code": "DE", "address.MAIN.postal_code": "12 AB"}, language="nl"),
    )
    assert rec.status == RecordStatus.WARNING and "07" not in rec.values and "12" not in rec.values
    messages = {i.message for i in rec.issues}
    assert "Zip does not have the format expected by the target: left out" in messages
    assert "Lang value « nl » is not supported by the target" in messages


def test_identity_or_required_failures_block_and_nothing_is_truncated(resolved):
    long_name = _map(resolved, _org(organization_name="A" * 11, **{"address.MAIN.country_code": "DE"}))
    assert long_name.status == RecordStatus.BLOCKED and "02" not in long_name.values
    assert long_name.issues[0].message == "Company is longer than 10 characters"
    no_account = _map(resolved, _org(**{"address.MAIN.country_code": "DE"}), account=None)
    assert no_account.status == RecordStatus.BLOCKED
    assert [i.message for i in no_account.issues] == ["Number is required and has no value"]
    bad_account = _map(resolved, _org(**{"address.MAIN.country_code": "DE"}), account="12")
    assert bad_account.status == RecordStatus.BLOCKED  # motif du Registry : 5 chiffres


def test_encoding_is_checked(resolved):
    rec = _map(resolved, _org(organization_name="Łódź", **{"address.MAIN.country_code": "PL"}))
    assert rec.status == RecordStatus.BLOCKED
    assert rec.issues[0].message == "Company contains characters that cannot be written in cp1252"
    assert (
        _map(
            resolved, _org(organization_name="Łódź", **{"address.MAIN.country_code": "PL"}), encoding="utf-8"
        ).status
        == RecordStatus.READY
    )


def test_rule_and_repeat_warnings(resolved):
    rec = _map(
        resolved,
        _org(bank_accounts=[{"account_number": "1"}, {"iban": "A"}, {"iban": "B"}, {"iban": "C"}]),
    )
    messages = [i.message for i in rec.issues]
    assert "Country missing" in messages  # règle déclarée, jamais un pays inventé
    assert "1 bank accounts not written: IBAN missing" in messages
    assert "Only the first 2 of 3 bank accounts are written" in messages
    assert all(i.level == IssueLevel.WARNING for i in rec.issues)
    assert (rec.values["08"], rec.values["10"]) == ("A", "B")


def test_vat_without_country_prefix(resolved):
    rec = _map(resolved, _org(**{"tax.vat_id": "123", "address.MAIN.country_code": "DE"}))
    assert "06" not in rec.values and rec.issues[0].message == "VAT prefix does not have the expected form"


def test_header_values(resolved):
    now = datetime(2026, 10, 2, 14, 30, 15, 123456)
    assert header_values(resolved, {"start": "2026-01-01"}, now) == {
        "01": "DEMO",
        "02": 9,
        "03": "20261002143015123",
        "04": "20260101",
    }


@pytest.mark.parametrize(
    ("syntax", "value", "ok"),
    [
        ("ascii", "RE", True),
        ("ascii", "Ü", False),  # \w ASCII : lettres accentuées refusées
        ("ascii", "٣", False),  # chiffre non ASCII : refusé
        (None, "Ü", True),  # sans dialecte déclaré : comportement Python par défaut (inchangé)
    ],
)
def test_pattern_syntax_declared_by_the_registry(syntax, value, ok):  # noqa: ANN001
    meta: dict[str, Any] = {"pattern": r'^"\w{0,2}"$', **({"pattern_syntax": syntax} if syntax else {})}
    fd = FieldDef(technical_name="08", label="Origin", native_type="text", metadata=meta)
    assert (check_value(fd, value, DemoWriter(), "utf-8") is None) is ok
    digits = FieldDef(
        technical_name="09", label="Code", native_type="text", metadata={**meta, "pattern": r'^"\d{1,2}"$'}
    )
    assert (check_value(digits, "٣", DemoWriter(), "utf-8") is None) is (syntax is None)
