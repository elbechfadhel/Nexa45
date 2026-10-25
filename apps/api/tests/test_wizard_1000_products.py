"""Non-régression AI Migration Wizard : un catalogue de 1 000 produits (55 colonnes, forme d'un export de
place de marché, valeurs INVENTÉES) par l'API du fournisseur.

Bug corrigé : un échec de l'IA (délai, coupure) laissait toutes les colonnes « non reconnues » sans le dire ;
le portail retenait ce « Not imported » par défaut comme un choix et le renvoyait à chaque analyse (0 matched,
54 not imported pour toute la session) ; puis « Complete missing information » demandait UN nom pour les
1 000 produits. Désormais : l'échec est dit, rien n'est retenu par défaut, et un champ de donnée sans colonne
demande QUELLE colonne du fichier le contient, jamais une valeur unique."""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from test_lexware_partner_api import _client
from test_wizard_api import world  # noqa: F401 - fixture partagée
from uie_core.domains import ProposalMeta
from uie_core.imports import ColumnProposalBatch
from uie_core.imports.proposer import ColumnDecision, ColumnProposal

HEADERS = (
    "timestamp,title,seller_name,brand,description,initial_price,final_price,currency,availability,"
    "reviews_count,categories,asin,buybox_seller,number_of_sellers,root_bs_rank,answered_questions,domain,"
    "images_count,url,video_count,image_url,item_weight,rating,product_dimensions,seller_id,"
    "date_first_available,discount,model_number,manufacturer,department,plus_content,upc,video,top_review,"
    "variations,delivery,features,format,buybox_prices,parent_asin,input_asin,ingredients,origin_url,"
    "bought_past_month,is_available,root_bs_category,bs_category,bs_rank,badge,subcategory_rank,amazon_choice,"
    "images,product_details,prices_breakdown,country_of_origin"
).split(",")
ANSWER = {
    "question_id": "target_system",
    "topic": "target_system",
    "question": "Which target system?",
    "value": "big_suite",
    "label": "Big Suite",
    "source": "ai",
}
FIELDS = [
    {"name": "name", "label": "Name", "type": "text", "required": True},
    {"name": "list_price", "label": "Sales Price", "type": "decimal", "required": False},
    {"name": "description_sale", "label": "Sales Description", "type": "text", "required": False},
]


def _catalog(rows: int = 1000) -> bytes:
    """1 000 produits inventés, 55 colonnes ; « ingredients » vide partout."""
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(HEADERS)
    for i in range(rows):
        row = {h: f"{h}-{i}" for h in HEADERS}
        row |= {
            "title": f"Trail shoe model {i}",
            "final_price": f'"{10 + i % 90}.{i % 100:02d}"',
            "initial_price": "null",
            "currency": "USD",
            "description": f"Light running shoe number {i}",
            "ingredients": "",
        }
        w.writerow([row[h] for h in HEADERS])
    return out.getvalue().encode()


def _proposal(alice: Any) -> dict[str, Any]:
    p = alice.post("/api/wizard/proposal", json={"answers": [ANSWER], "mode": "simple"}).json()
    proposal = {
        k: p[k] for k in ("title", "summary", "entity", "template_name", "configuration", "rows", "notes")
    }
    proposal["fields"] = [{**f, "length": None, "example": None, "description": None} for f in FIELDS]
    proposal["rows"] = []
    return proposal


def _batch(error: str | None = None) -> ColumnProposalBatch:
    meta = ProposalMeta(provider="fake", model="fake")
    if error:
        return ColumnProposalBatch(proposals=[], meta=meta, error=error)
    found = {"title": "name", "final_price": "list_price", "description": "description_sale"}
    return ColumnProposalBatch(
        proposals=[
            ColumnProposal(
                position=HEADERS.index(h), decision=ColumnDecision.TARGET, semantic_key=k, confidence=0.97
            )
            for h, k in found.items()
        ],
        meta=meta,
    )


def test_an_ai_failure_never_turns_1000_products_into_one_name(world):  # noqa: F811
    fake = world["fake"]
    alice = _client(world["app"], "alice@acme.io")
    proposal = _proposal(alice)
    data = _catalog()

    def send(path: str, **extra: Any):  # noqa: ANN202
        payload = json.dumps({"answers": [ANSWER], "proposal": proposal, **extra})
        r = alice.post(
            f"/api/wizard/file/{path}",
            files={"file": ("amazon-products.csv", data, "text/csv")},
            data={"payload": payload},
        )
        assert r.status_code == 200, r.text
        return r.json()

    # 1. l'IA échoue : dit comme tel, jamais « 54 not imported » décidé en silence
    fake.wizard_columns = lambda request: _batch(error="timeout")
    failed = send("mapping")
    assert failed["rows"] == 1000 and failed["ai_unavailable"] is True and failed["matched"] == 0
    assert {c["status"] for c in failed["columns"]} == {"unmatched", "empty"}
    # le portail ne retient AUCUN « Not imported » par défaut (flow.test.mjs) : rien n'est renvoyé
    # 2. nouvelle analyse, l'IA répond : les colonnes sont reconnues
    fake.wizard_columns = lambda request: _batch()
    again = send("mapping", previous=[])
    assert again["ai_unavailable"] is False and again["matched"] == 3
    targets = {c["header"]: c["target"] for c in again["columns"] if c["target"]}
    assert targets == {"title": "name", "final_price": "list_price", "description": "description_sale"}
    # pourquoi le portail ne retient rien par défaut : un « Not imported » renvoyé est un CHOIX, réappliqué
    # sans nouvelle analyse (c'était le bug) ; il n'est plus jamais compté comme une correspondance
    poisoned = send(
        "mapping", previous=[{"header": h, "target": None} for h in HEADERS if h != "ingredients"]
    )
    assert poisoned["matched"] == 0
    assert all(c["status"] == "previous" and c["target"] is None for c in poisoned["columns"] if c["filled"])
    # 3. nom sans colonne : on demande QUELLE colonne, jamais un nom unique pour 1 000 produits
    without_name = [{"position": HEADERS.index("final_price"), "target": "list_price"}]
    info = send("missing", decisions=without_name)
    name = next(q for q in info["questions"] if q["field"] == "name")
    assert name["title"] == "Which column of your file contains Name?"
    assert name["choices"] == [] and name["suggested"] is None and name["free_text"] is False
    columns = {c["header"]: c for c in name["file_columns"]}
    assert "title" in columns and columns["title"]["examples"][0] == "Trail shoe model 0"
    assert "final_price" not in columns and "ingredients" not in columns  # déjà mappée / vide
    # 4. la colonne choisie devient la correspondance : 1 000 noms, aucun champ à compléter
    chosen = [*without_name, {"position": HEADERS.index("title"), "target": "name"}]
    ready = send("preview", decisions=chosen)
    assert ready["rows"] == 1000 and ready["missing_required"] == []
    assert ready["preview"][0][0] == "Trail shoe model 0" and ready["preview"][4][0] == "Trail shoe model 4"
