"""Instance Odoo 17 ALLEMANDE de test pour le domaine ACCOUNTING (phase 3) — IDEMPOTENT.

Base `accounting17` sur le conteneur `odoo17-accounting` (profil Compose « accounting », port 8037) : jamais
customer17 (instance de régression PRODUCT / PARTNER, non touchée). Uniquement par l'API XML-RPC standard
d'Odoo (création de la base, installation de modules, chargement du plan comptable, factures créées puis
validées par `action_post`) : aucune table comptable écrite à la main.

1. Base `accounting17` (pays DE, langue de_DE, sans démo) si elle n'existe pas. 2. Modules : account,
l10n_de ; société « GO45 Demo GmbH » (DE, EUR) ; plan comptable SKR03 (`de_skr03`). 3. Partenaires, articles
et pièces INVENTÉS, retrouvés par une référence stable (`ref` = UIE-ACC-…) : une pièce existante n'est
jamais recréée. Chaque pièce porte une condition de paiement STANDARD d'Odoo (identifiant externe
`account.account_payment_term_*`) : l'échéance est calculée par Odoo depuis la date de facture. Sans
condition, Odoo 17 prend la date du jour de la création pour échéance (`_compute_invoice_date_due`) : une
pièce existante dont la condition diffère est corrigée UNE fois par le circuit normal (remise en brouillon,
condition, puis même état final), jamais par écriture directe d'une date.

    docker compose -f docker/docker-compose.yml --profile accounting up -d odoo17-accounting
    uv run python docker/accounting17-setup.py [--url http://localhost:8037] [--db accounting17]

Mots de passe : ODOO_MASTER_PASSWORD (gestionnaire de bases, défaut « admin ») et ODOO_PASSWORD (utilisateur
admin de la base, défaut « admin ») — instance Docker locale de test uniquement."""

from __future__ import annotations

import argparse
import os
import sys
import xmlrpc.client
from typing import Any

COMPANY = "GO45 Demo GmbH"
CHART = "de_skr03"
PERIOD = "2026-01"  # période principale des pièces de démonstration

# Partenaires INVENTÉS (distincts de customer17 : autre instance, autres identités source)
PARTNERS: list[dict[str, Any]] = [
    {
        "key": "baeckerei",
        "name": "Bäckerei Sonnenschein GmbH",
        "is_company": True,
        "street": "Marktplatz 3",
        "zip": "50667",
        "city": "Köln",
        "vat": "DE811907980",
        "email": "info@baeckerei-sonnenschein.example",
    },
    {
        "key": "hoffmann",
        "name": "Lena Hoffmann",
        "is_company": False,
        "street": "Lindenstraße 8",
        "zip": "60311",
        "city": "Frankfurt am Main",
    },
    {
        "key": "holzhandel",
        "name": "Holzhandel Nord GmbH",
        "is_company": True,
        "street": "Hafenweg 12",
        "zip": "20457",
        "city": "Hamburg",
        "vat": "DE118645675",
    },
    {
        "key": "buero",
        "name": "Bürobedarf Meier KG",
        "is_company": True,
        "street": "Gewerbering 4",
        "zip": "80939",
        "city": "München",
    },
    {
        "key": "rhein",
        "name": "Rhein Logistik AG",
        "is_company": True,
        "street": "Rheinallee 21",
        "zip": "55116",
        "city": "Mainz",
        "vat": "DE129273398",
    },
    # nom de plus de 50 caractères : refusé à l'export Debitoren/Kreditoren → aucun compte DATEV (cas bloqué)
    {
        "key": "longname",
        "name": "Gesellschaft für Innenausbau und Holzverarbeitung Süddeutschland mbH",
        "is_company": True,
        "street": "Industriestraße 77",
        "zip": "70173",
        "city": "Stuttgart",
    },
]

# Articles (taxe et compte d'après le plan chargé : choisis à l'exécution, jamais codés en SQL)
PRODUCTS: list[dict[str, Any]] = [
    {"key": "beratung", "name": "Beratung (Stunde)", "type": "service", "price": 120.0, "rate": 19},
    {"key": "eiche", "name": "Holzplatte Eiche", "type": "consu", "price": 85.0, "rate": 19},
    {"key": "fachbuch", "name": "Fachbuch Holzbau", "type": "consu", "price": 39.9, "rate": 7},
    {"key": "material", "name": "Büromaterial", "type": "consu", "price": 25.0, "rate": 19},
]

# condition de paiement par défaut (identifiant externe standard du module account) ; « payment_term » d'une
# pièce la remplace (échéances différentes, donc vérifiables)
DEFAULT_PAYMENT_TERM = "account_payment_term_30days"

# Pièces : (référence stable, type, partenaire, date de pièce, date comptable, lignes, état final)
# ligne = (article, quantité, prix, taux de taxe ou liste de taux, compte imposé (code) ou None, libellé)
DOCUMENTS: list[dict[str, Any]] = [
    {
        "ref": "UIE-ACC-A",
        "type": "out_invoice",
        "partner": "baeckerei",
        "date": "2026-01-12",
        "lines": [("eiche", 4, 85.0, 19, None, None)],
    },
    {
        "ref": "UIE-ACC-B",
        "type": "out_invoice",
        "partner": "hoffmann",
        "date": "2026-01-15",
        "payment_term": "account_payment_term_15days",
        "lines": [
            ("beratung", 3, 120.0, 19, None, None),
            ("beratung", 2, 95.0, 19, None, "Beratung Planung"),
            ("fachbuch", 2, 39.9, 7, None, None),
        ],
    },
    {
        "ref": "UIE-ACC-C",
        "type": "out_refund",
        "partner": "baeckerei",
        "date": "2026-01-20",
        "lines": [("eiche", 1, 85.0, 19, None, "Gutschrift beschädigte Platte")],
    },
    {
        "ref": "UIE-ACC-D",
        "type": "in_invoice",
        "partner": "holzhandel",
        "date": "2026-01-08",
        "payment_term": "account_payment_term_45days",
        "lines": [("eiche", 20, 52.0, 19, None, None)],
    },
    {
        "ref": "UIE-ACC-E",
        "type": "in_refund",
        "partner": "holzhandel",
        "date": "2026-01-22",
        "lines": [("eiche", 2, 52.0, 19, None, "Rücksendung")],
    },
    {
        "ref": "UIE-ACC-F1",
        "type": "out_invoice",
        "partner": "rhein",
        "date": "2026-01-16",
        "lines": [("beratung", 5, 120.0, 19, None, None)],
    },
    {
        "ref": "UIE-ACC-F2",
        "type": "in_invoice",
        "partner": "rhein",
        "date": "2026-01-18",
        "lines": [("material", 1, 480.0, 19, None, "Transport Januar")],
    },
    # avertissement : date de pièce (décembre) ≠ mois comptable (janvier) et libellé de plus de 60 caractères
    {
        "ref": "UIE-ACC-G",
        "type": "out_invoice",
        "partner": "baeckerei",
        "date": "2025-12-30",
        "accounting_date": "2026-01-05",
        "lines": [
            (
                "eiche",
                2,
                85.0,
                19,
                None,
                "Holzplatten Eiche geölt für die Ladeneinrichtung, Lieferung inklusive Montage vor Ort",
            )
        ],
    },
    # bloqué plus tard : partenaire sans compte DATEV (nom trop long pour Debitoren/Kreditoren)
    {
        "ref": "UIE-ACC-H",
        "type": "out_invoice",
        "partner": "longname",
        "date": "2026-01-19",
        "lines": [("beratung", 1, 120.0, 19, None, None)],
    },
    # bloqué plus tard : compte de produit volontairement laissé sans correspondance DATEV
    {
        "ref": "UIE-ACC-I",
        "type": "out_invoice",
        "partner": "rhein",
        "date": "2026-01-21",
        "lines": [("beratung", 2, 120.0, 19, "8200", "Sonstige Erlöse")],
    },
    # non exportées : brouillon, annulée
    {
        "ref": "UIE-ACC-J",
        "type": "out_invoice",
        "partner": "hoffmann",
        "date": "2026-01-25",
        "state": "draft",
        "lines": [("beratung", 1, 120.0, 19, None, None)],
    },
    {
        "ref": "UIE-ACC-K",
        "type": "out_invoice",
        "partner": "baeckerei",
        "date": "2026-01-26",
        "state": "cancel",
        "lines": [("eiche", 1, 85.0, 19, None, None)],
    },
    # non prise en charge V1 (détectée à la lecture) : deux taxes sur une ligne
    {
        "ref": "UIE-ACC-L",
        "type": "out_invoice",
        "partner": "rhein",
        "date": "2026-01-27",
        "lines": [("beratung", 1, 120.0, [19, 7], None, "Zwei Steuern auf einer Zeile")],
    },
    # autre période (filtrage par mois)
    {
        "ref": "UIE-ACC-M",
        "type": "out_invoice",
        "partner": "hoffmann",
        "date": "2026-02-03",
        "lines": [("beratung", 1, 120.0, 19, None, None)],
    },
]


class Odoo:
    def __init__(self, url: str, db: str, uid: int, password: str) -> None:
        self.m = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object", allow_none=True)
        self.db, self.uid, self.pw = db, uid, password

    def __call__(self, model: str, method: str, *args: Any, **kw: Any) -> Any:
        try:
            return self.m.execute_kw(self.db, self.uid, self.pw, model, method, list(args), kw)
        except xmlrpc.client.Fault as exc:
            # méthode exécutée ; sa valeur de retour None n'est pas sérialisable en XML-RPC (button_cancel)
            if "cannot marshal None" in exc.faultString:
                return None
            raise

    def find(self, model: str, domain: list[Any], **kw: Any) -> list[int]:
        return self(model, "search", domain, context={"active_test": False}, **kw)


def ensure_database(url: str, db: str, master: str, password: str) -> None:
    dbs = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/db", allow_none=True)
    if dbs.db_exist(db):
        print(f"base {db} : déjà présente (rien à faire)")
        return
    print(f"base {db} : création (DE, de_DE, sans démo)…")
    dbs.create_database(master, db, False, "de_DE", password, "admin", "DE", "")


def ensure_modules(odoo: Odoo, names: list[str]) -> None:
    for name in names:
        [mod] = odoo("ir.module.module", "search_read", [["name", "=", name]], fields=["state"])
        if mod["state"] != "installed":
            print(f"module {name} : installation…")
            odoo("ir.module.module", "button_immediate_install", [mod["id"]])
        else:
            print(f"module {name} : déjà installé")


def ensure_company(odoo: Odoo) -> int:
    [company] = odoo(
        "res.company",
        "search_read",
        [],
        fields=["name", "currency_id", "country_id", "chart_template"],
        limit=1,
        order="id",
    )
    eur = odoo.find("res.currency", [["name", "=", "EUR"]], limit=1)[0]
    de = odoo.find("res.country", [["code", "=", "DE"]], limit=1)[0]
    vals: dict[str, Any] = {}
    if company["name"] != COMPANY:
        vals["name"] = COMPANY
    if not company["country_id"] or company["country_id"][0] != de:
        vals["country_id"] = de
    if company["currency_id"][0] != eur:
        odoo("res.currency", "write", [eur], {"active": True})
        vals["currency_id"] = eur
    if vals:
        odoo("res.company", "write", [company["id"]], vals)
    if company["chart_template"] != CHART:
        if odoo.find("account.move", [["company_id", "=", company["id"]]], limit=1):
            sys.exit(f"Plan comptable {company['chart_template']} déjà utilisé par des pièces : arrêt")
        print(f"plan comptable : chargement {CHART}…")
        odoo("account.chart.template", "try_loading", CHART, company["id"])
    print(f"société {COMPANY} : DE, EUR, plan {CHART}")
    return company["id"]


def tax(odoo: Odoo, company: int, use: str, rate: float) -> int:
    ids = odoo.find(
        "account.tax",
        [
            ["company_id", "=", company],
            ["type_tax_use", "=", use],
            ["amount_type", "=", "percent"],
            ["amount", "=", rate],
            ["price_include", "=", False],
            ["active", "=", True],
        ],
        order="sequence,id",
    )
    if not ids:
        sys.exit(f"Aucune taxe {use} {rate} % dans le plan chargé")
    return ids[0]


def ensure_partners(odoo: Odoo) -> dict[str, int]:
    de = odoo.find("res.country", [["code", "=", "DE"]], limit=1)[0]
    out = {}
    for p in PARTNERS:
        ids = odoo.find("res.partner", [["name", "=", p["name"]]], limit=1)
        if ids:
            out[p["key"]] = ids[0]
            continue
        vals = {k: v for k, v in p.items() if k != "key"} | {"country_id": de, "lang": "de_DE"}
        out[p["key"]] = odoo("res.partner", "create", vals)
        print(f"partenaire {p['name']} : créé")
    return out


def ensure_products(odoo: Odoo, company: int) -> dict[str, dict[str, Any]]:
    out = {}
    for p in PRODUCTS:
        ids = odoo.find("product.product", [["name", "=", p["name"]]], limit=1)
        if not ids:
            tmpl = odoo(
                "product.template",
                "create",
                {
                    "name": p["name"],
                    "type": p["type"],
                    "list_price": p["price"],
                    "taxes_id": [(6, 0, [tax(odoo, company, "sale", p["rate"])])],
                    "supplier_taxes_id": [(6, 0, [tax(odoo, company, "purchase", p["rate"])])],
                },
            )
            ids = odoo.find("product.product", [["product_tmpl_id", "=", tmpl]], limit=1)
            print(f"article {p['name']} : créé")
        out[p["key"]] = {"id": ids[0]}
    return out


def payment_term(odoo: Odoo, xmlid: str) -> int:
    """Condition de paiement standard par son identifiant externe (jamais par son libellé, traduit)."""
    rows = odoo(
        "ir.model.data",
        "search_read",
        [["module", "=", "account"], ["name", "=", xmlid], ["model", "=", "account.payment.term"]],
        fields=["res_id"],
    )
    if not rows:
        sys.exit(f"Condition de paiement {xmlid} absente")
    return rows[0]["res_id"]


def ensure_payment_term(odoo: Odoo, move: int, ref: str, term: int) -> None:
    """Pièce existante : condition attendue → rien ; sinon correction par le circuit normal d'Odoo (remise en
    brouillon, condition, même état final), l'échéance étant recalculée par Odoo."""
    [row] = odoo("account.move", "read", [move], fields=["state", "invoice_payment_term_id"])
    if row["invoice_payment_term_id"] and row["invoice_payment_term_id"][0] == term:
        print(f"pièce {ref} : déjà présente (rien à faire)")
        return
    state = row["state"]
    if state != "draft":
        odoo("account.move", "button_draft", [move])
    odoo("account.move", "write", [move], {"invoice_payment_term_id": term})
    if state == "posted":
        odoo("account.move", "action_post", [move])
    elif state == "cancel":
        odoo("account.move", "button_cancel", [move])
    print(f"pièce {ref} : échéance corrigée (condition de paiement)")


def ensure_documents(
    odoo: Odoo, company: int, partners: dict[str, int], products: dict[str, dict[str, Any]]
) -> None:
    for d in DOCUMENTS:
        term = payment_term(odoo, d.get("payment_term", DEFAULT_PAYMENT_TERM))
        existing = odoo.find("account.move", [["ref", "=", d["ref"]], ["company_id", "=", company]], limit=1)
        if existing:
            ensure_payment_term(odoo, existing[0], d["ref"], term)
            continue
        use = "sale" if d["type"].startswith("out_") else "purchase"
        lines = []
        for product, qty, price, rates, account_code, label in d["lines"]:
            taxes = [tax(odoo, company, use, r) for r in (rates if isinstance(rates, list) else [rates])]
            line: dict[str, Any] = {
                "product_id": products[product]["id"],
                "quantity": qty,
                "price_unit": price,
                "tax_ids": [(6, 0, taxes)],
            }
            if label:
                line["name"] = label
            if account_code:
                acc = odoo.find(
                    "account.account", [["code", "=", account_code], ["company_id", "=", company]], limit=1
                )
                if not acc:
                    sys.exit(f"Compte {account_code} absent du plan chargé")
                line["account_id"] = acc[0]
            lines.append((0, 0, line))
        move = odoo(
            "account.move",
            "create",
            {
                "move_type": d["type"],
                "partner_id": partners[d["partner"]],
                "ref": d["ref"],
                "invoice_date": d["date"],
                "date": d.get("accounting_date", d["date"]),
                "invoice_payment_term_id": term,
                "invoice_line_ids": lines,
            },
        )
        state = d.get("state", "posted")
        if state == "posted":
            odoo("account.move", "action_post", [move])
        elif state == "cancel":
            odoo("account.move", "button_cancel", [move])
        print(f"pièce {d['ref']} ({d['type']}, {state}) : créée")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default="http://localhost:8037")
    ap.add_argument("--db", default="accounting17")
    args = ap.parse_args(argv)
    if args.db == "customer17":
        sys.exit("customer17 est l'instance de régression PRODUCT / PARTNER : jamais configurée ici")
    password = os.environ.get("ODOO_PASSWORD", "admin")
    ensure_database(args.url, args.db, os.environ.get("ODOO_MASTER_PASSWORD", "admin"), password)
    common = xmlrpc.client.ServerProxy(f"{args.url}/xmlrpc/2/common", allow_none=True)
    uid = common.authenticate(args.db, "admin", password, {})
    if not uid:
        print(f"Authentification refusée pour admin@{args.db}", file=sys.stderr)
        return 1
    odoo = Odoo(args.url, args.db, uid, password)
    ensure_modules(odoo, ["account", "l10n_de"])
    company = ensure_company(odoo)
    partners = ensure_partners(odoo)
    products = ensure_products(odoo, company)
    ensure_documents(odoo, company, partners, products)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
