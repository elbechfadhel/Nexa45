"""Extrait les pièces de démonstration d'accounting17 (INVENTÉES par docker/accounting17-setup.py) vers
une fixture servie hors ligne par FakeOdooServer : `odoo17_accounting17_records.json`.

Format `search_read` d'Odoo (many2one = [id, nom], x2many = ids, vide = False), restreint aux champs du pack
ACCOUNTING Odoo et aux pièces de référence UIE-ACC-… (aucune autre donnée de l'instance).

    uv run python packages/connectors/tests/fixtures/extract_accounting17.py [--url http://localhost:8037]
"""

from __future__ import annotations

import argparse
import json
import os
import xmlrpc.client
from pathlib import Path

HERE = Path(__file__).parent
MOVE_FIELDS = "name ref move_type state commercial_partner_id company_id narration date invoice_date"
MOVE_FIELDS += " invoice_date_due currency_id amount_total amount_tax line_ids"
LINE_FIELDS = "move_id display_type name account_id balance amount_currency price_total currency_id tax_ids"
LINE_FIELDS += " tax_line_id"
FIELDS = {
    "account.move": MOVE_FIELDS.split(),
    "account.move.line": LINE_FIELDS.split(),
    "account.account": ["code", "name"],
    "account.tax": ["name", "amount", "amount_type", "type_tax_use", "price_include"],
    "res.currency": ["name"],
    "res.company": ["name"],
    # partenaires des pièces : lectures PARTNER et ACCOUNTING partagent les identités (test)
    "res.partner": (
        "name is_company ref company_registry active parent_id type customer_rank supplier_rank street"
        " street2 zip city state_id country_id email phone mobile website lang vat bank_ids"
    ).split(),
    "res.country": ["code", "name"],
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8037")
    ap.add_argument("--db", default="accounting17")
    args = ap.parse_args()
    pw = os.environ.get("ODOO_PASSWORD", "admin")
    uid = xmlrpc.client.ServerProxy(f"{args.url}/xmlrpc/2/common").authenticate(args.db, "admin", pw, {})
    obj = xmlrpc.client.ServerProxy(f"{args.url}/xmlrpc/2/object", allow_none=True)

    def read(model: str, domain: list, ids: list[int] | None = None) -> list[dict]:
        fields = [*FIELDS[model], "display_name"]
        if ids is not None:
            return obj.execute_kw(args.db, uid, pw, model, "read", [sorted(ids)], {"fields": fields})
        return obj.execute_kw(
            args.db, uid, pw, model, "search_read", [domain], {"fields": fields, "order": "id"}
        )

    moves = read("account.move", [["ref", "like", "UIE-ACC-"]])
    lines = read("account.move.line", [["move_id", "in", [m["id"] for m in moves]]])
    accounts = read(
        "account.account", [], ids=list({ln["account_id"][0] for ln in lines if ln["account_id"]})
    )
    tax_ids = {t for ln in lines for t in ln["tax_ids"]} | {
        ln["tax_line_id"][0] for ln in lines if ln["tax_line_id"]
    }
    taxes = read("account.tax", [], ids=list(tax_ids))
    currencies = read("res.currency", [], ids=list({m["currency_id"][0] for m in moves}))
    companies = read("res.company", [], ids=list({m["company_id"][0] for m in moves}))
    partners = read("res.partner", [], ids=list({m["commercial_partner_id"][0] for m in moves}))
    countries = read("res.country", [], ids=list({p["country_id"][0] for p in partners if p["country_id"]}))
    out = {
        "_comment": "Pièces INVENTÉES d'accounting17 (docker/accounting17-setup.py), format search_read.",
        "records": {
            "account.move": moves,
            "account.move.line": lines,
            "account.account": accounts,
            "account.tax": taxes,
            "res.currency": currencies,
            "res.company": companies,
            "res.partner": partners,
            "res.country": countries,
        },
    }
    path = HERE / "odoo17_accounting17_records.json"
    path.write_text(json.dumps(out, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    print(
        f"{path.name} : {len(moves)} pièces, {len(lines)} lignes, {len(accounts)} comptes, {len(taxes)} taxes"
    )


if __name__ == "__main__":
    main()
