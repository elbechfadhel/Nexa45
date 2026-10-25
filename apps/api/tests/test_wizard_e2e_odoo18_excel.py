"""Bout en bout, par la Supplier API, du cas prioritaire : un fichier Excel de 5 produits → un Excel pour
l'import natif d'Odoo 18, SANS connexion à Odoo.

Parcours exact du test manuel : Odoo → 18 → Products → « Goods only » → fichier (Référence, Nom, Prix,
Code-barres, Description) → correspondances → aperçu → champs manquants (« Piece » pour les unités) → fichier
final. Registry Odoo 18 RÉEL (dump), LLM simulé et volontairement muet sur le type de produit : « Goods only »
→ Goods doit venir de la déduction sûre (valeur admise du Registry), jusqu'au fichier exporté. Aucun envoi."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any

import polars as pl
import pytest
import xlsxwriter
from pydantic import SecretStr
from test_lexware_partner_api import _client
from test_supplier_api import PW
from uie_api import deps
from uie_api.app import create_app
from uie_connectors.odoo import OdooSchemaLoader
from uie_core.domains import ProposalMeta
from uie_core.identity import Organization, OrganizationMembership, SystemContext, User, hash_password
from uie_core.imports import ColumnProposal, ColumnProposalBatch
from uie_core.imports.proposer import ColumnDecision
from uie_core.registry import RegistryService
from uie_core.wizard import WizardRaw, WizardRequest, clear_cache

DUMPS = Path(__file__).resolve().parents[3] / "docs" / "dumps"
HEADERS = ["Référence", "Nom", "Prix", "Code-barres", "Description"]
PRODUCTS = [
    ["NX-LAMP-001", "Lampe de bureau LED", "39.9", "2100000001018", "Lampe LED réglable pour bureau"],
    ["NX-MUG-002", "Mug isotherme 450 ml", "24.5", "2100000001025", "Mug de voyage en acier inoxydable"],
    ["NX-BOX-003", "Boîte de rangement", "18.75", "2100000001032", "Boîte empilable avec couvercle"],
    ["NX-PEN-004", "Stylo bille bleu", "2.4", "2100000001049", "Stylo à bille, encre bleue"],
    ["NX-PAD-005", "Bloc-notes A5", "4.9", "2100000001056", "Bloc de 80 feuilles lignées"],
]
TARGET = {
    "title": "Odoo 18 Products (spreadsheet import)",
    "summary": "Products for the Odoo spreadsheet import.",
    "entity": "product.template",
    "template_name": None,
    "configuration": [],
    "fields": [
        {
            "name": n,
            "label": lb,
            "type": t,
            "length": None,
            "required": r,
            "example": None,
            "description": None,
        }
        for n, lb, t, r in [
            ("default_code", "Internal Reference", "text", False),
            ("name", "Name", "text", True),
            ("list_price", "Sales Price", "decimal", False),
            ("barcode", "Barcode", "text", False),
            ("description_sale", "Sales Description", "text", False),
            ("type", "Product Type", "code", True),
            ("uom_id", "Unit of Measure", "text", True),  # import natif : désignée par son NOM
            ("uom_po_id", "Purchase Unit", "text", True),
        ]
    ],
    "sample_rows": [],
    "notes": [],
}


def _excel() -> bytes:
    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True, "strings_to_numbers": False})
    ws = wb.add_worksheet()
    for c, h in enumerate(HEADERS):
        ws.write_string(0, c, h)
    for r, row in enumerate(PRODUCTS, start=1):
        for c, v in enumerate(row):
            ws.write_string(r, c, v)
    wb.close()
    return buf.getvalue()


class ScenarioProposer:
    """LLM simulé : questions du parcours, structure, correspondances ; MUET sur le type de produit."""

    cache_identity = "e2e-odoo18:1"

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.columns_sent: list[list[str]] = []  # en-têtes envoyés à chaque appel « columns »

    def _raw(self, data: Any) -> WizardRaw:
        return WizardRaw(data=data, meta=ProposalMeta(provider="fake", model="fake"))

    def wizard_question(self, request: WizardRequest) -> WizardRaw:
        self.calls.append("question")
        topics = [a.topic for a in request.answers]

        def q(topic: str, title: str, *labels: str) -> WizardRaw:
            return self._raw(
                {
                    "done": False,
                    "remaining": 2,
                    "question": {
                        "id": topic,
                        "topic": topic,
                        "title": title,
                        "help": None,
                        "allow_other": True,
                        "choices": [
                            {"value": lb.lower().replace(" ", "_"), "label": lb, "description": None}
                            for lb in labels
                        ],
                    },
                }
            )

        if "version" not in topics:
            return q("version", "Which Odoo version?", "17", "18")
        if "data_type" not in topics:
            return q("data_type", "What kind of data do you want to import?", "Products", "Customers")
        if "product_kind" not in topics:
            return q("product_kind", "What do you sell?", "Goods only", "Services only", "Goods and services")
        return self._raw({"done": True, "remaining": 0, "question": None})

    def wizard_target(self, request: WizardRequest) -> WizardRaw:
        self.calls.append("target")
        # réponse changée (« Goods and services ») → structure régénérée, avec un SECOND prix (coût)
        if any(a.topic == "product_kind" and a.label != "Goods only" for a in request.answers):
            cost = {**TARGET["fields"][2], "name": "standard_price", "label": "Cost"}
            return self._raw(
                {**TARGET, "title": "Odoo 18 Products and Services", "fields": [*TARGET["fields"], cost]}
            )
        return self._raw(TARGET)

    def wizard_columns(self, request: Any) -> ColumnProposalBatch:
        self.calls.append("columns")
        self.columns_sent.append([c.header for c in request.columns])
        # deux prix dans la structure : « Prix » devient ambigu, l'IA s'abstient (cas réel du bug)
        ambiguous = any(t.key == "standard_price" for t in request.targets)
        keys = {"Référence": "default_code", "Nom": "name", "Code-barres": "barcode"}
        proposals = [
            ColumnProposal(
                position=c.position,
                decision=ColumnDecision.TARGET,
                semantic_key=keys[c.header],
                confidence=0.99,
            )
            for c in request.columns
            if c.header in keys
        ]
        proposals += [
            ColumnProposal(
                position=c.position,
                decision=ColumnDecision.TARGET,
                semantic_key={"Prix": "list_price", "Description": "description_sale"}[c.header],
                confidence=0.8 if c.header == "Prix" else 0.91,
            )
            for c in request.columns
            if c.header in ("Prix", "Description") and not (ambiguous and c.header == "Prix")
        ]
        return ColumnProposalBatch(proposals=proposals, meta=ProposalMeta(provider="fake", model="fake"))

    def wizard_missing(self, request: Any) -> WizardRaw:
        self.calls.append("missing")
        units = [{"label": "Piece", "standard_name": "Units"}, {"label": "Kilogram", "standard_name": "kg"}]
        return self._raw(
            {
                "fields": [
                    {
                        "name": f.name,
                        "title": "How do you sell your products?"
                        if f.name == "uom_id"
                        else "How do you buy them?",
                        "help": None,
                        "suggested_value": None,
                        "explanation": None,
                        "choices": [],
                        "business_choices": units,
                    }
                    for f in request.fields
                    if f.kind == "reference"
                ]
                # volontairement AUCUNE déduction pour le type de produit
            }
        )


@pytest.fixture
def world(session):  # noqa: ANN001, ANN201
    clear_cache()
    RegistryService(session, SystemContext("test")).publish_snapshot(
        OdooSchemaLoader({"json": DUMPS / "odoo18_ref.json"}).build_snapshot()
    )
    org = Organization(name="Acme", slug="acme")
    session.add(org)
    session.flush()
    user = User(email="alice@acme.io", display_name="Alice", password_hash=hash_password(SecretStr(PW)))
    session.add(user)
    session.flush()
    session.add(OrganizationMembership(organization_id=org.id, user_id=user.id, role="OWNER"))
    session.flush()
    fake = ScenarioProposer()
    app = create_app()
    app.dependency_overrides[deps.get_session] = lambda: session
    app.dependency_overrides[deps.get_wizard_proposer] = lambda: fake
    yield {"app": app, "fake": fake}
    clear_cache()


def test_excel_to_odoo18_native_import_without_connection_end_to_end(world):
    alice = _client(world["app"], "alice@acme.io")
    answers: list[dict[str, Any]] = []

    def answer(step: dict[str, Any], pick: str) -> None:
        q = step["question"]
        c = next(c for c in q["choices"] if c["label"] == pick)
        answers.append(
            {
                "question_id": q["id"],
                "topic": q["topic"],
                "question": q["title"],
                "value": c["value"],
                "label": c["label"],
                "source": c["source"],
            }
        )

    # 1. parcours : Odoo (Registry, sans appel au LLM) → 18 (versions du Registry) → Products → Goods only
    first = alice.post("/api/wizard/next", json={"answers": [], "mode": "simple"}).json()
    assert world["fake"].calls == []
    answer(first, "Odoo")
    for pick in ("18", "Products", "Goods only"):
        answer(alice.post("/api/wizard/next", json={"answers": answers, "mode": "simple"}).json(), pick)
    assert answers[1]["value"] == "registry:odoo:odoo:18"  # version du Registry, vérifiée
    done = alice.post("/api/wizard/next", json={"answers": answers, "mode": "simple"}).json()
    assert done["done"] is True
    p = alice.post("/api/wizard/proposal", json={"answers": answers, "mode": "simple"}).json()
    assert p["verification"] == "verified"  # colonnes toutes connues du schéma Odoo 18
    assert [a["value"] for a in next(f for f in p["fields"] if f["name"] == "type")["allowed"]] == [
        "consu",
        "service",
        "combo",
    ]
    proposal = {
        k: p[k] for k in ("title", "summary", "entity", "template_name", "configuration", "rows", "notes")
    }
    proposal["fields"] = [
        {k: v for k, v in f.items() if k not in ("verified", "allowed", "reference")} for f in p["fields"]
    ]
    excel = _excel()

    def send(path: str, decisions: list[dict[str, Any]], fills: list[dict[str, Any]] | None = None):  # noqa: ANN202
        payload = {"answers": answers, "proposal": proposal, "decisions": decisions, "fills": fills or []}
        return alice.post(
            f"/api/wizard/file/{path}",
            files={"file": ("Nexa45_5_produits_test.xlsx", excel, "application/octet-stream")},
            data={"payload": json.dumps({**payload, "mode": "simple"})},
        )

    # 1 bis. questions PRÉVISIBLES avant le fichier : seule l'unité de vente. Le type se déduit de
    # « Goods only » ; l'unité d'achat est calculée par Odoo 18 d'après l'unité de vente (Registry :
    # depends uom_id)
    early = alice.post("/api/wizard/early", json={"answers": answers, "proposal": proposal, "mode": "simple"})
    assert early.status_code == 200, early.text
    assert [q["field"] for q in early.json()["questions"]] == ["uom_id"]
    assert next(f for f in p["fields"] if f["name"] == "uom_po_id")["computed_from"] == ["uom_id"]
    answered = alice.post(
        "/api/wizard/early",
        json={"answers": answers, "proposal": proposal, "fills": [{"field": "uom_id", "common": "Units"}]},
    )
    assert answered.status_code == 422  # sans connexion : valeur non vérifiable → confirmation exigée
    early_fills = [{"field": "uom_id", "common": "Units", "confirmed": True}]
    answered = alice.post(
        "/api/wizard/early", json={"answers": answers, "proposal": proposal, "fills": early_fills}
    )
    assert answered.status_code == 200 and answered.json()["questions"] == []

    # 2. correspondances : 5 lignes ; Prix à confirmer
    m = send("mapping", []).json()
    assert m["rows"] == 5 and m["to_confirm"] == 1
    decisions = [{"position": c["position"], "target": c["target"]} for c in m["columns"]]

    # 3. premier aperçu : « Goods only » → Goods appliqué d'office (sans LLM) ; unités à compléter ; non prêt
    first_preview = send("preview", decisions).json()
    assert [(a["field"], a["value"]) for a in first_preview["auto_filled"]] == [("type", "Goods")]
    assert {f["name"] for f in first_preview["missing_required"]} == {"uom_id"}
    assert [(x["field"]["name"], x["source"]) for x in first_preview["set_by_target"]] == [
        ("uom_po_id", "Unit of Measure")
    ]
    assert first_preview["status"] == "to_complete"
    checks = {c["label"]: c["ok"] for c in first_preview["checks"]}
    linked = next(k for k in checks if k.startswith("Categories, units and other linked values"))
    assert checks[linked] is False  # aucune connexion

    # 4. champs manquants : ni le type, ni l'unité d'achat (calculée par Odoo) ne sont demandés
    info = send("missing", decisions).json()
    asked = {q["field"]: q for q in info["questions"]}
    assert set(asked) == {"uom_id"}
    assert asked["uom_id"]["hint_names"]["Piece"] == "Units"
    assert asked["uom_id"]["choices"] == []  # sans connexion : aucune valeur ni identifiant proposés
    # réponse donnée AVANT le fichier : plus aucune question après l'analyse
    assert send("missing", decisions, early_fills).json()["questions"] == []

    # 5. le client choisit « Piece » (→ « Units », confirmé)
    fills = early_fills
    final = send("preview", decisions, fills).json()
    assert final["missing_required"] == [] and final["rows"] == 5
    assert final["status"] == "prepared"  # préparé, PAS prêt : unités non vérifiables sans connexion
    checks = {c["label"]: c["ok"] for c in final["checks"]}
    assert checks["All required fields have a value"] is True
    assert checks[next(k for k in checks if k.startswith("Categories, units and other"))] is False
    assert len(final["warnings"]) == 1 and all("not verified" in w for w in final["warnings"])
    assert final["data_type"] == "Products"

    # 6. fichier final : les réponses initiales vont jusqu'à l'export
    xlsx = send("prepared", decisions, fills)
    assert xlsx.status_code == 200, xlsx.text
    data = pl.read_excel(io.BytesIO(xlsx.content), sheet_name="Data", infer_schema_length=0)
    assert data.height == 5
    assert data["type"].to_list() == ["consu"] * 5
    assert data["uom_id"].to_list() == ["Units"] * 5
    assert "uom_po_id" not in data.columns  # laissée vide : Odoo la calcule d'après l'unité de vente
    assert data["default_code"].to_list() == [r[0] for r in PRODUCTS]
    # prix écrits comme NOMBRES (mêmes valeurs) ; « Goods » noté « rempli d'après vos réponses »
    typed = pl.read_excel(io.BytesIO(xlsx.content), sheet_name="Data")
    assert typed["list_price"].dtype.is_numeric() and typed["list_price"].to_list() == [
        39.9,
        24.5,
        18.75,
        2.4,
        4.9,
    ]
    about = dict(pl.read_excel(io.BytesIO(xlsx.content), sheet_name="Read me", has_header=False).rows())
    assert about["Filled from your answers"] == "type = Goods"
    assert "type" not in about["Completed by you"]
    about = pl.read_excel(io.BytesIO(xlsx.content), sheet_name="Read me", has_header=False)
    texts = [str(v) for col in about.columns for v in about[col].to_list()]
    assert any(t.startswith("Prepared file, not yet ready for import") for t in texts)
    assert not any(t.startswith("Ready for import") for t in texts)


def _journey(alice, kind: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:  # noqa: ANN001
    """Parcours Odoo → 18 → Products → `kind` ; renvoie les réponses et la structure proposée."""
    answers: list[dict[str, Any]] = []
    step = alice.post("/api/wizard/next", json={"answers": [], "mode": "simple"}).json()
    for pick in ("Odoo", "18", "Products", kind):
        q = step["question"]
        c = next(c for c in q["choices"] if c["label"] == pick)
        answers.append(
            {
                "question_id": q["id"],
                "topic": q["topic"],
                "question": q["title"],
                "value": c["value"],
                "label": c["label"],
                "source": c["source"],
            }
        )
        step = alice.post("/api/wizard/next", json={"answers": answers, "mode": "simple"}).json()
    p = alice.post("/api/wizard/proposal", json={"answers": answers, "mode": "simple"}).json()
    proposal = {
        k: p[k] for k in ("title", "summary", "entity", "template_name", "configuration", "rows", "notes")
    }
    proposal["fields"] = [
        {k: v for k, v in f.items() if k not in ("verified", "allowed", "reference")} for f in p["fields"]
    ]
    return answers, proposal


def test_prices_survive_a_changed_questionnaire_from_source_to_final_excel(world):
    """Régression « Prix → list_price perdu » : même fichier de 5 produits, réponse au questionnaire changée →
    structure régénérée avec un second prix (coût) → l'IA ne reconnaît plus « Prix ». Le choix déjà retenu
    pour cet en-tête est réappliqué (sans redemander à l'IA) et les 5 prix arrivent dans l'Excel final ; sans
    lui, la colonne n'est JAMAIS perdue en silence (« Not imported from your file »)."""
    alice = _client(world["app"], "alice@acme.io")
    excel = _excel()
    source_digest = hashlib.sha256(excel).hexdigest()
    prices = [39.9, 24.5, 18.75, 2.4, 4.9]
    units = [{"field": "uom_id", "common": "Units", "confirmed": True}]

    def send(path: str, answers: list, proposal: dict, **extra: Any):  # noqa: ANN202
        payload = {"answers": answers, "proposal": proposal, "mode": "simple", **extra}
        res = alice.post(
            f"/api/wizard/file/{path}",
            files={"file": ("Nexa45_5_produits_test.xlsx", excel, "application/octet-stream")},
            data={"payload": json.dumps(payload)},
        )
        assert res.status_code == 200, res.text
        return res

    def final_prices(res) -> list[float]:  # noqa: ANN001
        typed = pl.read_excel(io.BytesIO(res.content), sheet_name="Data")
        assert typed["list_price"].dtype.is_numeric()
        return typed["list_price"].to_list()

    # 1. premier parcours (« Goods only ») : Prix → list_price confirmé ; 5 prix dans l'export
    answers, proposal = _journey(alice, "Goods only")
    m = send("mapping", answers, proposal).json()
    prix = next(c for c in m["columns"] if c["header"] == "Prix")
    assert prix["target"] == "list_price"
    decisions = [{"position": c["position"], "target": c["target"]} for c in m["columns"]]
    assert final_prices(send("prepared", answers, proposal, decisions=decisions, fills=units)) == prices
    # ce que le navigateur retient du premier passage : en-tête → champ choisi
    previous = [
        {"header": c["header"], "target": d["target"]} for c, d in zip(m["columns"], decisions, strict=True)
    ]

    # 2. réponse changée → nouvelle structure (deux prix) ; même fichier
    answers2, proposal2 = _journey(alice, "Goods and services")
    assert {f["name"] for f in proposal2["fields"]} >= {"list_price", "standard_price"}

    # 2a. sans le choix précédent, l'IA ne reconnaît plus Prix : non importé, mais SIGNALÉ (jamais silencieux)
    lost = send("mapping", answers2, proposal2).json()
    assert next(c for c in lost["columns"] if c["header"] == "Prix")["target"] is None
    decisions_lost = [{"position": c["position"], "target": c["target"]} for c in lost["columns"]]
    preview = send("preview", answers2, proposal2, decisions=decisions_lost, fills=units).json()
    assert preview["not_imported"] == ["Prix"]
    res = send("prepared", answers2, proposal2, decisions=decisions_lost, fills=units)
    about = dict(pl.read_excel(io.BytesIO(res.content), sheet_name="Read me", has_header=False).rows())
    assert about["Not imported from your file"].startswith("Prix:")

    # 2b. avec le choix précédent : réappliqué, Prix n'est pas renvoyé à l'IA, les 5 prix arrivent à l'export
    world["fake"].columns_sent.clear()
    m2 = send("mapping", answers2, proposal2, previous=previous).json()
    prix2 = next(c for c in m2["columns"] if c["header"] == "Prix")
    assert (prix2["status"], prix2["target"]) == ("previous", "list_price") and m2["to_confirm"] == 0
    assert all("Prix" not in sent for sent in world["fake"].columns_sent)
    decisions2 = [{"position": c["position"], "target": c["target"]} for c in m2["columns"]]
    preview2 = send("preview", answers2, proposal2, decisions=decisions2, fills=units).json()
    assert preview2["not_imported"] == []
    res2 = send("prepared", answers2, proposal2, decisions=decisions2, fills=units)
    assert final_prices(res2) == prices
    mapping_sheet = pl.read_excel(io.BytesIO(res2.content), sheet_name="Mapping").rows()
    assert ("Prix", "list_price") in mapping_sheet

    # le fichier source n'est jamais modifié : les octets envoyés à chaque étape sont ceux du départ
    assert hashlib.sha256(excel).hexdigest() == source_digest
    assert pl.read_excel(io.BytesIO(excel), infer_schema_length=0)["Prix"].to_list() == [
        r[2] for r in PRODUCTS
    ]
