"""Catalogue des systèmes CONNUS importé dans le Registry existant : 16 systèmes sans schéma (+ ceux qui ont
un schéma publié = 19 dans le Wizard), idempotent, jamais de version, de produit ni de snapshot supposés."""

from __future__ import annotations

from uie_connectors.known_systems import import_known_systems, load_catalog
from uie_core.identity import SystemContext
from uie_core.registry import RegistryService
from uie_core.registry.service import KNOWN_STATUS

NEW = {
    "sevdesk",
    "weclapp",
    "sap_business_one",
    "lexware_warenwirtschaft",
    "jtl_wawi",
    "billbee",
    "xentral",
    "easybill",
    "haufe_x360",
    "dynamics_365_finance",
}
KEPT = {
    "sap_s4hana",
    "dynamics_365_business_central",
    "oracle_netsuite",
    "quickbooks_online",
    "shopify",
    "xero",
}


def test_catalog_lists_the_requested_systems_once():
    codes = [s.code for s in load_catalog().systems]
    assert set(codes) == NEW | KEPT and len(codes) == 16
    # les systèmes qui ont un schéma viennent de leurs loaders, jamais du catalogue
    assert not {"odoo", "datev", "lexware"} & set(codes)


def test_import_is_idempotent_and_adds_no_schema(session):
    ctx = SystemContext("test")
    assert import_known_systems(session, ctx) == 16
    assert import_known_systems(session, ctx) == 16
    known = [s for s in RegistryService(session, ctx).list_systems() if s.status == KNOWN_STATUS]
    assert len(known) == 16
    assert all(not s.products for s in known)  # ni produit, ni version, ni snapshot
    assert {s.category for s in known} <= {"erp", "accounting", "ecommerce"}
