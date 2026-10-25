"""Réglages d'export DATEV Debitoren/Kreditoren : aides de format déclarées par le binding (UX), motifs
officiels du Registry inchangés. Partie Registry sautée si la spec locale n'est pas générée."""

from __future__ import annotations

from pathlib import Path

import pytest
import uie_connectors  # noqa: F401
import uie_domain_packs  # noqa: F401
from uie_connectors.datev import DatevSchemaLoader
from uie_core.exports import ExportProfileService, ProfileInvalid
from uie_core.identity import GO45_DEV_ORG_ID, SystemContext
from uie_core.mapping import TargetBindingRegistry
from uie_core.registry import RegistryService

ROOT = Path(__file__).resolve().parents[2]
REAL_SPEC = ROOT / "var" / "datev" / "specs" / "extf_700" / "debitoren-kreditoren_v5.yaml"
CTX = SystemContext("test")
CHART = "Chart of accounts (Sachkontenrahmen): Two digits, e.g. 03 for SKR03, 04 for SKR04."
ORIGIN = "Origin code (Herkunft): Up to 2 letters or digits, e.g. RE."
BASE = {
    "advisor_number": 1001,
    "client_number": 1,
    "fiscal_year_start": "2026-01-01",
    "account_length": 4,
    "debtor_range": {"start": 10000, "end": 69999},
    "creditor_range": {"start": 70000, "end": 99999},
}


def test_binding_declares_format_help_for_header_texts():
    params = {
        p.name: p for p in TargetBindingRegistry.get("partner", "datev", "debitoren-kreditoren").profile
    }
    assert params["chart_of_accounts"].format_help == "Two digits, e.g. 03 for SKR03, 04 for SKR04."
    assert params["origin_code"].format_help == "Up to 2 letters or digits, e.g. RE."


@pytest.fixture
def profiles(session):  # noqa: ANN001, ANN201
    if not REAL_SPEC.exists():
        pytest.skip("spec DATEV locale absente (spec_builder)")
    registry = RegistryService(session, CTX)
    snap = registry.publish_snapshot(DatevSchemaLoader({"spec": REAL_SPEC}).build_snapshot())
    patterns = {
        f.technical_name: f.metadata.get("pattern")
        for e in registry.load_snapshot_model(snap).entities
        if e.technical_name == "header"
        for f in e.fields
    }
    # motifs officiels du portail, inchangés (aucun assouplissement)
    assert patterns["027"] == r'^["](\d{2}){0,2}["]$' and patterns["008"] == r'^["]\w{0,2}["]$'
    return ExportProfileService(session, CTX)


@pytest.mark.parametrize(
    ("values", "errors"),
    [
        (
            {"chart_of_accounts": "SKR03", "origin_code": "GO45"},
            {
                "chart_of_accounts": CHART,
                "origin_code": ORIGIN,
            },
        ),
        (
            {"chart_of_accounts": "3"},
            {"chart_of_accounts": CHART},
        ),
        # \w de la notation DATEV = [a-zA-Z0-9_] (ASCII) : une lettre accentuée est refusée
        ({"origin_code": "ÜB"}, {"origin_code": ORIGIN}),
    ],
)
def test_invalid_header_texts_get_the_declared_format(profiles, values, errors):  # noqa: ANN001
    with pytest.raises(ProfileInvalid) as exc:
        profiles.save("datev", "debitoren-kreditoren", {**BASE, **values}, GO45_DEV_ORG_ID)
    assert exc.value.errors == errors


@pytest.mark.parametrize(("chart", "origin"), [("03", "RE"), ("04", "GO"), ("", "")])
def test_valid_header_texts(profiles, chart, origin):  # noqa: ANN001
    row = profiles.save(
        "datev",
        "debitoren-kreditoren",
        {**BASE, "chart_of_accounts": chart, "origin_code": origin},
        GO45_DEV_ORG_ID,
    )
    assert row.values.get("chart_of_accounts") == (chart or None)
    assert row.values.get("origin_code") == (origin or None)
