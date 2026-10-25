"""Valeurs réelles d'un système connecté (`ReferenceFinder`) : l'instance CHOISIE comme cible est la seule
lue ; sans choix, une instance injoignable n'empêche pas de lire la suivante."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

from uie_core.wizard.lookup import ReferenceFinder, ReferenceLookup, ReferenceOption


def _instance(name: str, version: str = "17", ok: bool = True) -> Any:
    ver = SimpleNamespace(
        version=version, product=SimpleNamespace(code="odoo", system=SimpleNamespace(code="odoo"))
    )
    conn = SimpleNamespace(last_test_status="OK" if ok else "FAILED")
    return SimpleNamespace(
        id=uuid.uuid4(), name=name, interface=SimpleNamespace(version=ver), connections=[conn]
    )


class FakeInstances:
    """Instances de l'organisation : `get` d'une instance inconnue (autre tenant) lève, comme le service."""

    def __init__(self, *instances: Any) -> None:
        self.by_id = {i.id: i for i in instances}

    def list(self) -> list[Any]:
        return list(self.by_id.values())

    def get(self, instance_id: uuid.UUID) -> Any:
        if instance_id not in self.by_id:
            raise LookupError("not found")
        return self.by_id[instance_id]

    def connection_context(self, instance_id: uuid.UUID) -> tuple[None, str]:
        return None, self.by_id[instance_id].name


class Units(ReferenceLookup):
    system_code = "odoo"
    VALUES = {"customer17": ["Stück"], "accounting17": ["Units", "Dozens"]}
    down = {"customer17"}  # instance injoignable (conteneur arrêté)

    def options(self, ctx: Any, entity: str, attribute: str | None, limit: int = 50) -> list[ReferenceOption]:
        if ctx in self.down:
            raise OSError("connection refused")
        return [ReferenceOption(value=v, label=v) for v in self.VALUES[ctx]]


class Lookups:
    @staticmethod
    def get(system: str) -> ReferenceLookup | None:
        return Units() if system == "odoo" else None


def test_only_the_instance_chosen_as_target_is_read() -> None:
    customer, accounting = _instance("customer17"), _instance("accounting17")
    finder = ReferenceFinder(FakeInstances(customer, accounting), Lookups)  # type: ignore[arg-type]
    read = finder.options("odoo", "17", "uom.uom", None, instance_id=accounting.id)
    assert [o.label for o in read or []] == ["Units", "Dozens"]
    # une autre instance (autre organisation, supprimée) : rien n'est lu, jamais une instance au hasard
    assert finder.options("odoo", "17", "uom.uom", None, instance_id=uuid.uuid4()) is None
    assert not finder.connected("odoo", "17", instance_id=uuid.uuid4())
    # mauvaise version pour l'instance choisie : rien
    assert finder.options("odoo", "18", "uom.uom", None, instance_id=accounting.id) is None


def test_without_a_choice_an_unreachable_instance_does_not_hide_the_next_one() -> None:
    customer, accounting = _instance("customer17"), _instance("accounting17")
    finder = ReferenceFinder(FakeInstances(customer, accounting), Lookups)  # type: ignore[arg-type]
    assert [o.label for o in finder.options("odoo", "17", "uom.uom", None) or []] == ["Units", "Dozens"]
    failed = _instance("accounting17", ok=False)
    only_down = ReferenceFinder(FakeInstances(customer, failed), Lookups)  # type: ignore[arg-type]
    assert only_down.options("odoo", "17", "uom.uom", None) is None
