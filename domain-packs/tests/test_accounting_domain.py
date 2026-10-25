"""Domaine ACCOUNTING (Odoo) : même moteur que PRODUCT / PARTNER, packs `accounting/`, sur les schémas RÉELS.

Analyse seulement (définition standard, domaine effectif, Semantic Enrichment) : aucune lecture de pièce,
aucun export. Les VALEURS Odoo (move_type, state, display_type…) sont traduites par le pack (`value_maps`),
validées ici contre les sélections du Registry et contre les énumérations de l'Accounting CDM."""

from __future__ import annotations

from pathlib import Path

import pytest
import uie_domain_packs  # noqa: F401 — enregistre les packs découverts
from test_partner_domain import CTX, DUMPS, _instance
from uie_cdm import DocumentStatus, DocumentType, LineKind, TaxComputation, TaxScope
from uie_connectors.odoo import OdooSchemaLoader
from uie_core.domains import (
    AccountingDomainService,
    DomainKey,
    DomainPackRegistry,
    DomainPackResolver,
    EntityRole,
    SemanticEnrichmentService,
)
from uie_domain_packs.catalog.spec import load_vocabulary_spec

ROOT = Path(__file__).resolve().parents[2]
VOCABULARY = ROOT / "domain-packs" / "accounting" / "vocabulary.yaml"


def _accounting(session, inst):  # noqa: ANN001, ANN202
    domains = AccountingDomainService(session, CTX)
    outcome = domains.analyze(inst.id)
    return outcome, SemanticEnrichmentService(session, CTX, domains=domains)


CDM_ENUMS = {
    "document_type": DocumentType,
    "document_status": DocumentStatus,
    "line_kind": LineKind,
    "tax_computation": TaxComputation,
    "tax_usage": TaxScope,
}
# valeurs de sélection Odoo NON prises en charge en V1 (signalées à la lecture, jamais devinées)
_LINES = "account.move.line.display_type"
_TAX = "account.tax.amount_type"
_V17_LINES = {"cogs", "discount", "epd"}
UNSUPPORTED = {
    "17": {_LINES: _V17_LINES, _TAX: {"division"}},
    "18": {_LINES: _V17_LINES, _TAX: {"division"}},
    "19": {
        _LINES: _V17_LINES | {"non_deductible_product_total", "non_deductible_product", "non_deductible_tax"},
        _TAX: {"division"},
    },
}


def _pack(version: str = "17"):  # noqa: ANN202
    return DomainPackResolver().resolve(DomainKey.ACCOUNTING, "odoo", version)


def test_accounting_pack_registered_for_odoo():
    assert "ACCOUNTING/odoo" in DomainPackRegistry.available()
    assert DomainPackResolver().vocabulary(DomainKey.ACCOUNTING).version == "1.0.0"


def test_canonical_values_are_the_cdm_enumerations():
    values = load_vocabulary_spec(VOCABULARY).values
    assert {k: sorted(v) for k, v in values.items()} == {k: sorted(e) for k, e in CDM_ENUMS.items()}


def _selections() -> dict[str, dict[str, set[str]]]:
    """Valeurs de sélection des champs traduits, par version (dumps standard 17 / 18 / 19)."""
    out: dict[str, dict[str, set[str]]] = {}
    for version in ("17", "18", "19"):
        snap = OdooSchemaLoader({"json": DUMPS / f"odoo{version}_ref.json"}).build_snapshot()
        for vm in _pack(version).value_maps():
            field = next(f for f in snap.entity(vm.entity).fields if f.technical_name == vm.field)
            out.setdefault(version, {})[vm.path] = {o["value"] for o in field.metadata["selection"]}
    return out


def test_value_maps_match_the_registry_selections():
    """Le pack déclare l'union des versions (ex. line_subsection : Odoo 19) : chaque valeur existe dans au
    moins une version, aucune n'est inventée ; les valeurs NON prises en charge sont celles attendues."""
    selections = _selections()
    for vm in _pack().value_maps():
        known = set().union(*(selections[v][vm.path] for v in selections))
        assert set(vm.values) | set(vm.ignore) <= known, vm.path
        for version, by_path in selections.items():
            unsupported = by_path[vm.path] - set(vm.values) - set(vm.ignore)
            assert unsupported == UNSUPPORTED[version].get(vm.path, set()), (version, vm.path)


@pytest.mark.parametrize("version", ["17", "18", "19"])
def test_standard_odoo_accounting_domain_has_no_question(session, version):  # noqa: ANN001
    inst = _instance(session, version, f"odoo{version}_ref.json")
    outcome, svc = _accounting(session, inst)
    snap = outcome.stored.snapshot
    assert snap.domain_key == DomainKey.ACCOUNTING
    assert {e.role: e.technical_name for e in snap.entities if e.present} == {
        EntityRole.DOCUMENT: "account.move",
        EntityRole.DOCUMENT_LINE: "account.move.line",
        EntityRole.LEDGER_ACCOUNT: "account.account",
        EntityRole.TAX: "account.tax",
        EntityRole.CURRENCY: "res.currency",
    }
    assert svc.get_pending_questions(inst.id) == []
    rows = {r.path: r for r in svc.list_bindings(inst.id)}
    for path, key in {
        "account.move.name": "document_number",
        "account.move.move_type": "document_type",
        "account.move.commercial_partner_id": "partner",
        "account.move.date": "accounting_date",
        "account.move.line.account_id": "ledger_account",
        "account.move.line.balance": "line_amount",
        "account.move.line.tax_ids": "line_taxes",
        "account.account.code": "account_code",
        "account.tax.amount": "tax_rate",
        "account.tax.type_tax_use": "tax_usage",
        "res.currency.name": "currency_code",
    }.items():
        assert (rows[path].semantic_key, rows[path].meaning_status.value) == (key, "KNOWN"), path
    assert (
        rows["account.tax.tax_scope"].semantic_key is None
    )  # homonyme Odoo (biens / services) : pas confondu


def test_three_domains_coexist_on_customer17_schema(session):
    """Même instance (dump customer17, lecture seule), trois domaines : définitions, domain snapshots,
    bindings et questions séparés ; PRODUCT et PARTNER inchangés."""
    from uie_core.domains import PartnerDomainService, ProductDomainService

    inst = _instance(session, "17", "odoo17_customer.json")
    questions = {}
    for cls in (ProductDomainService, PartnerDomainService, AccountingDomainService):
        domains = cls(session, CTX)
        domains.analyze(inst.id)
        svc = SemanticEnrichmentService(session, CTX, domains=domains)
        assert domains.get_instance_domain(inst.id).snapshot.domain_key == cls.domain_key
        questions[cls.domain_key.value] = sorted(q.path for q in svc.get_pending_questions(inst.id))
    assert questions == {
        "PRODUCT": [
            "product.template.x_notes_internal",
            "product.template.x_pack_code",
            "product.template.x_supplier_ref",
        ],
        "PARTNER": ["res.partner.x_customer_no"],
        "ACCOUNTING": [],
    }


def test_value_map_validation(tmp_path):
    from uie_domain_packs.catalog.spec import DomainPackSpec
    from uie_domain_packs.catalog.yaml_pack import load_pack_spec

    spec = load_pack_spec(ROOT / "domain-packs" / "accounting" / "odoo" / "accounting_domain.yaml")
    data = spec.model_dump(mode="json")
    data["value_maps"][0]["key"] = "document_status"  # champ non déclaré avec cette clé
    with pytest.raises(ValueError, match="non déclaré"):
        DomainPackSpec.model_validate(data)
    data = spec.model_dump(mode="json")
    data["value_maps"][0]["ignore"] = ["out_invoice"]  # traduite ET ignorée
    with pytest.raises(ValueError, match="à la fois"):
        DomainPackSpec.model_validate(data)


def test_unknown_canonical_value_is_refused_at_startup(tmp_path):
    import shutil

    import yaml
    from uie_core.domains import DomainPackError
    from uie_domain_packs.catalog.yaml_pack import YamlDomainPack

    shutil.copytree(ROOT / "domain-packs" / "accounting", tmp_path / "accounting")
    path = tmp_path / "accounting" / "odoo" / "accounting_domain.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["value_maps"][1]["values"]["posted"] = "BOOKED"  # absente des valeurs canoniques
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    cls = type("TmpPack", (YamlDomainPack,), {"domain_key": DomainKey.ACCOUNTING, "system_code": "odoo",
                                               "path": path, "relative_name": "tmp"})  # fmt: skip
    with pytest.raises(DomainPackError, match="BOOKED"):
        cls().value_maps()
