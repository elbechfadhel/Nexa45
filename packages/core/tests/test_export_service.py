"""Service d'export PARTNER sur un format INVENTÉ (système « demo ») : profil validé par le binding et le
Registry, numéros de compte (stables, jamais réutilisés, reprise optionnelle, plage pleine), aperçu sans
réservation, génération (fichier, journal sans contenu). Lecteur de partenaires simulé (le vrai est testé
ailleurs)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import func, inspect, select
from test_mapping_engine import DemoWriter, _snapshot
from uie_cdm import Address, Identifier, IdentifierScheme, Partner, PartnerKind, PartnerRole
from uie_core.exports import (
    ExportAccountAssignmentRecord,
    ExportNotReady,
    ExportProfileService,
    ExportRunRecord,
    PartnerExportService,
    ProfileInvalid,
)
from uie_core.identity import GO45_DEV_ORG_ID, SystemContext
from uie_core.mapping import RecordFileWriterRegistry, RecordStatus, TargetBinding, TargetBindingRegistry
from uie_core.partners import PartnerReadResult
from uie_core.registry import RegistryService
from uie_core.registry import models as m

CTX = SystemContext("test")
BINDING = TargetBinding.model_validate(
    {
        "binding_version": "1.0.0",
        "name": "Demo accounts",
        "source": {"model": "partner", "version": "1"},
        "target": {
            "system": "demo",
            "product": "contacts",
            "versions": ["2"],
            "interface": "file",
            "entity": "row",
            "header_entity": "head",
        },
        "records": "per_role",
        "account": {
            "field": "01",
            "length_from": "length",
            "plus": 1,
            "ranges": {"CUSTOMER": "debtors", "SUPPLIER": "creditors"},
            "reuse_from": {"CUSTOMER": "identifiers.customer_number"},
            "reuse_param": "reuse",
        },
        "fields": [
            {"field": "01", "source": "target.account_number", "identity": True},
            {"field": "02", "source": "organization_name", "identity": True},
            {"field": "05", "source": "address.MAIN.country_code"},
        ],
        "header": {"01": {"constant": "DEMO"}, "04": {"profile": "start"}},
        "profile": [
            {
                "name": "start",
                "label": "Start",
                "type": "date",
                "required": True,
                "header_field": "04",
                "format_help": "A date from 2000 on.",
            },
            {"name": "length", "label": "Length", "type": "integer", "required": True},
            {"name": "debtors", "label": "Debtors", "type": "range", "required": True},
            {"name": "creditors", "label": "Creditors", "type": "range", "required": True},
            {"name": "reuse", "label": "Reuse", "type": "boolean", "default": False},
        ],
        "encodings": ["cp1252"],
    }
)
PROFILE = {
    "start": "2026-01-01",
    "length": 4,
    "debtors": {"start": 10000, "end": 10002},
    "creditors": {"start": 70000, "end": 79999},
}


def _p(ref: str, name: str, *roles: PartnerRole, **kw: Any) -> Partner:
    return Partner(
        source_ref=ref,
        kind=PartnerKind.ORGANIZATION,
        name=name,
        organization_name=name,
        roles=list(roles),
        addresses=[Address(country_code="DE")],
        **kw,
    )


class FakeInstances:
    def __init__(self, inst_id: uuid.UUID) -> None:
        self.inst = type("Inst", (), {"id": inst_id, "organization_id": GO45_DEV_ORG_ID})()

    def get(self, instance_id: uuid.UUID) -> Any:
        assert instance_id == self.inst.id
        return self.inst


class FakePartners:
    """Lecteur simulé : rend une liste de Partner CDM, comme PartnerReadService.read."""

    def __init__(self, inst_id: uuid.UUID, partners: list[Partner]) -> None:
        self.ctx = CTX
        self.instances = FakeInstances(inst_id)
        self.partners = partners

    def read(self, instance_id: uuid.UUID) -> PartnerReadResult:
        return PartnerReadResult(plan_checksum="x" * 64, partners=self.partners, read=len(self.partners))


@pytest.fixture
def world(session):  # noqa: ANN001, ANN201
    snap = RegistryService(session, CTX).publish_snapshot(_snapshot())
    TargetBindingRegistry.register(BINDING)
    RecordFileWriterRegistry.register(DemoWriter)
    # instance réelle (FK des numéros et du journal), jamais connectée : la lecture est simulée
    inst = m.Instance(organization_id=GO45_DEV_ORG_ID, interface_id=snap.interface_id, name="demo-source")
    session.add(inst)
    session.flush()
    inst_id = inst.id
    reader = FakePartners(
        inst_id,
        [
            _p("1", "Acme", PartnerRole.CUSTOMER),
            _p("2", "Both", PartnerRole.CUSTOMER, PartnerRole.SUPPLIER),
            _p("3", "Nobody"),
            _p("4", "X" * 11, PartnerRole.CUSTOMER),  # nom trop long (10) : bloqué
        ],
    )
    yield {
        "session": session,
        "reader": reader,
        "inst": inst_id,
        "svc": PartnerExportService(session, CTX, reader),
    }
    # registres globaux : rien de « demo » ne doit rester pour les autres tests (ex. /api/exports/targets)
    TargetBindingRegistry._bindings.pop(("partner", "demo", "contacts"), None)
    RecordFileWriterRegistry._writers.pop(("demo", "file"), None)


def _save(session, values=PROFILE):  # noqa: ANN001, ANN202
    return ExportProfileService(session, CTX).save("demo", "contacts", values, GO45_DEV_ORG_ID)


def test_preview_needs_a_profile(world):
    with pytest.raises(ExportNotReady):
        world["svc"].preview("demo", "contacts", world["inst"])


@pytest.mark.parametrize(
    ("values", "errors"),
    [
        ({}, {"start", "length", "debtors", "creditors"}),
        ({**PROFILE, "start": "1999-01-01"}, {"start"}),  # motif du Registry de la position d'en-tête
        ({**PROFILE, "debtors": {"start": 1000, "end": 1999}}, {"debtors"}),  # 5 chiffres attendus
        ({**PROFILE, "creditors": {"start": 10001, "end": 79999}}, {"debtors"}),  # plages qui se chevauchent
        ({**PROFILE, "length": "four"}, {"length"}),
    ],
)
def test_profile_validation(session, world, values, errors):  # noqa: ANN001
    with pytest.raises(ProfileInvalid) as exc:
        _save(session, values)
    assert set(exc.value.errors) == errors


def test_preview_reserves_nothing(world, session):
    _save(session)
    p = world["svc"].preview("demo", "contacts", world["inst"])
    assert [(r.name, r.role, r.account_number, r.account_new, r.status) for r in p.records] == [
        ("Acme", "CUSTOMER", "10000", True, RecordStatus.READY),
        ("Both", "CUSTOMER", "10001", True, RecordStatus.READY),
        ("Both", "SUPPLIER", "70000", True, RecordStatus.READY),
        ("X" * 11, "CUSTOMER", "10002", True, RecordStatus.BLOCKED),
    ]
    assert [n.name for n in p.not_exported] == ["Nobody"]
    assert session.scalar(select(func.count()).select_from(ExportAccountAssignmentRecord)) == 0
    assert session.scalar(select(func.count()).select_from(ExportRunRecord)) == 0


def test_generate_reserves_written_records_only_and_numbers_are_stable(world, session):
    _save(session)
    f = world["svc"].generate("demo", "contacts", world["inst"])
    lines = f.content.decode("cp1252").split("\n")
    assert lines[0].startswith('"DEMO";;;20260101') and len(lines) == 4  # en-tête + 3 lignes écrites
    run = f.run
    assert (run.records, run.blocked, run.new_accounts) == (3, 1, 3)
    assert run.file_sha256 and run.file_size == len(f.content)
    # le journal ne garde aucun contenu : ni nom, ni valeur
    columns = {c.key for c in inspect(ExportRunRecord).mapper.column_attrs}
    assert not columns & {"content", "payload", "values", "name"}

    world["reader"].partners.append(_p("5", "Newcomer", PartnerRole.CUSTOMER))
    p = world["svc"].preview("demo", "contacts", world["inst"])
    numbers = {(r.name, r.role): (r.account_number, r.account_new) for r in p.records}
    assert numbers[("Acme", "CUSTOMER")] == ("10000", False)  # jamais changé
    # 10002 n'a pas été réservé (ligne bloquée) : il est proposé de nouveau, dans l'ordre des références ;
    # la plage est alors pleine pour le nouveau partenaire, bloqué avec une phrase claire
    assert numbers[("X" * 11, "CUSTOMER")] == ("10002", True)
    newcomer = next(r for r in p.records if r.name == "Newcomer")
    assert newcomer.account_number is None and newcomer.status == RecordStatus.BLOCKED
    assert newcomer.issues[0].message == "No free account number left in the range"


def test_reuse_of_the_source_number_when_enabled(world, session):
    world["reader"].partners[:] = [
        _p(
            "1",
            "Acme",
            PartnerRole.CUSTOMER,
            identifiers=[Identifier(scheme=IdentifierScheme.CUSTOMER_NUMBER, value="10002")],
        ),
        _p(
            "2",
            "Beta",
            PartnerRole.CUSTOMER,
            identifiers=[Identifier(scheme=IdentifierScheme.CUSTOMER_NUMBER, value="999")],
        ),
    ]
    _save(session, {**PROFILE, "reuse": True})
    p = world["svc"].preview("demo", "contacts", world["inst"])
    assert [(r.name, r.account_number) for r in p.records] == [("Acme", "10002"), ("Beta", "10000")]


def test_numbers_are_never_reused_after_the_system_is_gone(world, session):
    profile = _save(session)
    session.add(
        ExportAccountAssignmentRecord(
            profile_id=profile.id,
            instance_id=None,
            source_ref="old",
            role="CUSTOMER",
            account_number="10000",
            origin="ASSIGNED",
        )
    )
    session.flush()
    p = world["svc"].preview("demo", "contacts", world["inst"])
    assert p.records[0].account_number == "10001"


def test_runs_journal(world, session):
    _save(session)
    world["svc"].generate("demo", "contacts", world["inst"])
    [(run, name)] = world["svc"].runs(GO45_DEV_ORG_ID)
    assert (run.target_label, name) == ("Demo accounts · version 2", "demo-source")


def test_format_help_replaces_the_generic_reason(session, world):  # noqa: ANN001
    """Valeur refusée par le motif du Registry : le message reprend le format déclaré par le binding (le
    motif lui-même n'est jamais montré) ; sans format_help, la raison générique reste."""
    with pytest.raises(ProfileInvalid) as exc:
        _save(session, {**PROFILE, "start": "1999-01-01"})
    assert exc.value.errors == {"start": "Start: A date from 2000 on."}
