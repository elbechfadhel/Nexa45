"""Extrait des vrais dumps Odoo (docs/dumps/odooNN_ref.json) → fixtures de test réduites.

Les dumps complets (~6 Mo) viennent de `uie registry dump odoo --url ...` sur les instances de
référence ; cet extrait garde quelques models représentatifs des particularités rencontrées
en introspection réelle (ordre de ir.model.modules, AbstractModel sans `abstract` avant Odoo 19,
champs calculés stockés, company_dependent, translate booléen / mode).

    uv run python packages/connectors/tests/fixtures/extract_real.py
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent
DUMPS = HERE.parents[3] / "docs" / "dumps"

MODELS = {
    "res.partner": {
        "id",
        "name",
        "email",
        "display_name",
        "commercial_partner_id",
        "child_ids",  # one2many avec domain : depends = ["child_ids.active"]
        "type",
        "write_date",
    },
    "res.partner.category": {"id", "name", "write_date"},
    "res.country": {"id", "name", "write_date"},
    "product.template": {"id", "name", "description", "property_account_income_id", "write_date"},
    "mail.thread": {"id", "message_ids"},  # AbstractModel
    "format.address.mixin": {"id"},  # AbstractModel
    "mail.followers": {"id", "res_model", "res_id", "partner_id"},  # table sans write_date
    "account.invoice.report": {"id", "move_id", "price_subtotal"},  # vue SQL sans write_date
}


def extract(version: int) -> dict:
    d = json.loads((DUMPS / f"odoo{version}_ref.json").read_text(encoding="utf-8"))
    models = [m for m in d["models"] if m["model"] in MODELS]
    model_ids = {m["id"] for m in models}
    fields = [f for f in d["fields"] if f["model"] in MODELS and f["name"] in MODELS[f["model"]]]
    field_ids = {f["id"] for f in fields}
    return {
        "version_info": d["version_info"],
        "describe": d["describe"],
        "modules": d["modules"],
        "models": models,
        "fields": fields,
        "selections": [s for s in d["selections"] if s["field_id"][0] in field_ids],
        "model_xmlids": [x for x in d["model_xmlids"] if x["res_id"] in model_ids],
        "model_access": [a for a in d["model_access"] if a["model_id"][0] in model_ids],
        "field_attributes": {
            m: {f: attrs for f, attrs in fa.items() if f in MODELS[m]}
            for m, fa in d["field_attributes"].items()
            if m in MODELS
        },
    }


if __name__ == "__main__":
    for v in (17, 19):
        out = HERE / f"odoo{v}_real_extract.json"
        out.write_text(json.dumps(extract(v), indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"-> {out.name}")
