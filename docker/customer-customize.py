"""Personnalise l'instance client de test (odoo17-customer) via XML-RPC — IDEMPOTENT.

Crée les champs custom s'ils n'existent pas ; ne fait rien sinon :
- res.partner.x_customer_no (char, « Customer No ») — Instance Diff
- product.template.x_supplier_ref (char, « Supplier Reference ») — Product Domain (extension CUSTOM)
- product.template.x_pack_code (char, « Packaging Code ») — Semantic Enrichment : proposition packaging_code
- product.template.x_notes_internal (text, « Internal notes ») — Semantic Enrichment : aucune proposition

V2a (par défaut ; --no-catalog pour l'omettre) : petit catalogue de démonstration, INVENTÉ, idempotent (un
produit existant n'est pas recréé) : catégories imbriquées, attributs Color / Size, produits avec et sans
variantes, images générées (PNG), codes et codes-barres, fournisseur, produit archivé, extensions x_.

PARTNER (par défaut ; --no-partners pour l'omettre) : partenaires de démonstration INVENTÉS, idempotents (un
partenaire existant, retrouvé par son nom, n'est pas recréé) : société allemande cliente avec n° de TVA,
banques (IBAN d'exemple des normes) et x_customer_no ; personne ; client + fournisseur autrichien ;
fournisseur néerlandais (code postal non numérique, compte non IBAN) ; fournisseur sans pays ; contact
rattaché ; partenaire archivé ; partenaire sans rôle ; nom de plus de 50 caractères.

    uv run python docker/customer-customize.py [--url http://localhost:8027] [--db customer17] [--user admin]

Mot de passe : variable ODOO_PASSWORD (défaut « admin », instance Docker locale de test uniquement).
"""

from __future__ import annotations

import argparse
import base64
import os
import struct
import sys
import xmlrpc.client
import zlib
from typing import Any

CUSTOM_FIELDS: list[tuple[str, dict[str, str]]] = [
    (
        "res.partner",
        {"name": "x_customer_no", "field_description": "Customer No", "ttype": "char", "state": "manual"},
    ),
    (
        "product.template",
        {
            "name": "x_supplier_ref",
            "field_description": "Supplier Reference",
            "ttype": "char",
            "state": "manual",
        },
    ),
    (
        "product.template",
        {"name": "x_pack_code", "field_description": "Packaging Code", "ttype": "char", "state": "manual"},
    ),
    (
        "product.template",
        {
            "name": "x_notes_internal",
            "field_description": "Internal notes",
            "ttype": "text",
            "state": "manual",
        },
    ),
]


def ensure_custom_field(
    models: Any, db: str, uid: int, password: str, model: str, field: dict[str, str]
) -> bool:
    """Crée le champ s'il est absent. Retourne True s'il a été créé, False s'il existait déjà."""
    existing = models.execute_kw(
        db,
        uid,
        password,
        "ir.model.fields",
        "search_count",
        [[["model", "=", model], ["name", "=", field["name"]]]],
    )
    if existing:
        return False
    model_ids = models.execute_kw(
        db, uid, password, "ir.model", "search", [[["model", "=", model]]], {"limit": 1}
    )
    if not model_ids:
        raise RuntimeError(f"Modèle {model} introuvable")
    models.execute_kw(db, uid, password, "ir.model.fields", "create", [{**field, "model_id": model_ids[0]}])
    return True


# --------------------------------------------------------------------------- #
# Mini catalogue (V2a) — données INVENTÉES
# --------------------------------------------------------------------------- #
CATEGORIES = ["Furniture", "Furniture / Office Chairs", "Lighting", "Accessories", "Apparel"]
ATTRIBUTES = {"Color": ["Black", "Red", "Blue"], "Size": ["S", "M", "L"]}
SUPPLIER = "Nordic Wood Supplies"
PRODUCTS: list[dict[str, Any]] = [
    {
        "name": "Ergo Office Chair",
        "category": "Furniture / Office Chairs",
        "list_price": 249.0,
        "weight": 12.5,
        "description_sale": "Ergonomic office chair with lumbar support and adjustable armrests.",
        "x_supplier_ref": "SUP-ERGO-01",
        "x_pack_code": "PK-CH-10",
        "x_notes_internal": "Internal only: margin negotiation in progress.",
        "image": (40, 40, 48),
        "attributes": {"Color": ["Black", "Red"]},
        "variant_codes": {"Black": "CHAIR-ERGO-BLK", "Red": "CHAIR-ERGO-RED"},
        "variant_images": {"Red": (200, 40, 40)},
        "supplier": {"price": 120.0, "min_qty": 5, "delay": 21, "product_code": "NWS-7781"},
    },
    {
        "name": "Standing Desk",
        "category": "Furniture",
        "list_price": 499.0,
        "weight": 35.0,
        "description_sale": "Electric height-adjustable desk.",
        "x_supplier_ref": "SUP-DESK-02",
        "image": (150, 110, 70),
        "attributes": {"Size": ["S", "M", "L"]},
        "variant_codes": {"S": "DESK-STD-S", "M": "DESK-STD-M", "L": "DESK-STD-L"},
        "price_extra": {"M": 50.0, "L": 100.0},
        "supplier": {"price": 260.0, "min_qty": 2, "delay": 30, "product_code": "NWS-DESK"},
    },
    {
        "name": "Team T-Shirt",
        "category": "Apparel",
        "list_price": 19.9,
        "weight": 0.2,
        "description_sale": "Organic cotton t-shirt.",
        "image": (30, 90, 200),
        "attributes": {"Color": ["Black", "Blue"], "Size": ["S", "M", "L"]},
    },
    {
        "name": "LED Desk Lamp",
        "category": "Lighting",
        "list_price": 39.5,
        "weight": 1.1,
        "default_code": "LAMP-LED-01",
        "barcode": "4006381333931",
        "description_sale": "Dimmable LED lamp, warm white.",
        "x_pack_code": "PK-LMP-01",
        "image": (240, 200, 60),
    },
    {
        "name": "Monitor Arm",
        "category": "Accessories",
        "list_price": 89.0,
        "weight": 2.4,
        "default_code": "ARM-MON-01",
        "image": (120, 120, 130),
        "supplier": {"price": 41.0, "min_qty": 10, "delay": 14, "product_code": "NWS-ARM"},
    },
    {
        "name": "Cable Organizer",
        "category": "Accessories",
        "list_price": 12.0,
        "weight": 0.3,
        "default_code": "ACC-CBL-01",
        "barcode": "4006381333948",
    },
    {
        "name": "Notebook A5",
        "category": "Accessories",
        "list_price": 6.5,
        "weight": 0.25,
        "default_code": "ACC-NB-A5",
    },
    {
        "name": "Wooden Footrest (discontinued)",
        "category": "Furniture",
        "list_price": 29.0,
        "default_code": "FOOT-OLD",
        "active": False,
    },
]


def png(rgb: tuple[int, int, int], size: int = 256) -> str:
    """PNG carré (couleur + bande claire), en base64 : aucune dépendance d'image."""
    base = bytes(rgb)
    light = bytes(min(c + 70, 255) for c in rgb)
    rows = b"".join(b"\x00" + (light if size // 3 < y < size // 3 + 12 else base) * size for y in range(size))

    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        return struct.pack(">I", len(data)) + kind + data + crc

    header = chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
    raw = b"\x89PNG\r\n\x1a\n" + header + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b"")
    return base64.b64encode(raw).decode()


def ean13(seed: int) -> str:
    base = f"400638{seed:06d}"
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(base))
    return base + str((10 - total % 10) % 10)


class Odoo:
    def __init__(self, models: Any, db: str, uid: int, password: str) -> None:
        self.m, self.db, self.uid, self.pw = models, db, uid, password

    def __call__(self, model: str, method: str, *args: Any, **kw: Any) -> Any:
        return self.m.execute_kw(self.db, self.uid, self.pw, model, method, list(args), kw)

    def find(self, model: str, domain: list[Any], **kw: Any) -> list[int]:
        return self(model, "search", domain, context={"active_test": False}, **kw)


def ensure_catalog(odoo: Odoo) -> None:
    root = odoo.find("product.category", [["parent_id", "=", False]], limit=1, order="id")
    categ: dict[str, int] = {}
    for path in CATEGORIES:
        parent_path, _, name = path.rpartition(" / ")
        parent = categ.get(parent_path) or (root[0] if root else False)
        ids = odoo.find("product.category", [["name", "=", name], ["parent_id", "=", parent]], limit=1)
        categ[path] = (
            ids[0] if ids else odoo("product.category", "create", {"name": name, "parent_id": parent})
        )
    attr_ids: dict[str, int] = {}
    value_ids: dict[tuple[str, str], int] = {}
    for attr, values in ATTRIBUTES.items():
        ids = odoo.find("product.attribute", [["name", "=", attr]], limit=1)
        attr_ids[attr] = (
            ids[0] if ids else odoo("product.attribute", "create", {"name": attr, "create_variant": "always"})
        )
        for v in values:
            domain = [["name", "=", v], ["attribute_id", "=", attr_ids[attr]]]
            ids = odoo.find("product.attribute.value", domain, limit=1)
            value_ids[(attr, v)] = (
                ids[0]
                if ids
                else odoo("product.attribute.value", "create", {"name": v, "attribute_id": attr_ids[attr]})
            )
    partner = odoo.find("res.partner", [["name", "=", SUPPLIER]], limit=1)
    partner_id = (
        partner[0] if partner else odoo("res.partner", "create", {"name": SUPPLIER, "is_company": True})
    )

    for n, spec in enumerate(PRODUCTS):
        seed = 100 + 10 * n
        if odoo.find("product.template", [["name", "=", spec["name"]]], limit=1):
            print(f"product.template « {spec['name']} » : déjà présent (rien à faire)")
            continue
        vals: dict[str, Any] = {
            "name": spec["name"],
            "categ_id": categ[spec["category"]],
            "list_price": spec["list_price"],
            "weight": spec.get("weight", 0.0),
            "description_sale": spec.get("description_sale", False),
            "detailed_type": "consu",
        }
        for key in ("default_code", "barcode", "x_supplier_ref", "x_pack_code", "x_notes_internal"):
            if spec.get(key):
                vals[key] = spec[key]
        if spec.get("image"):
            vals["image_1920"] = png(spec["image"])
        if spec.get("attributes"):
            vals["attribute_line_ids"] = [
                (0, 0, {"attribute_id": attr_ids[a], "value_ids": [(6, 0, [value_ids[(a, v)] for v in vs])]})
                for a, vs in spec["attributes"].items()
            ]
        tmpl = odoo("product.template", "create", vals)
        if spec.get("supplier"):
            odoo(
                "product.supplierinfo",
                "create",
                {"product_tmpl_id": tmpl, "partner_id": partner_id, **spec["supplier"]},
            )
        ptavs = odoo(
            "product.template.attribute.value",
            "search_read",
            [["product_tmpl_id", "=", tmpl]],
            fields=["name"],
        )
        for ptav in ptavs:
            if extra := spec.get("price_extra", {}).get(ptav["name"]):
                odoo("product.template.attribute.value", "write", [ptav["id"]], {"price_extra": extra})
        variants = odoo(
            "product.product",
            "search_read",
            [["product_tmpl_id", "=", tmpl]],
            fields=["product_template_attribute_value_ids"],
            context={"active_test": False},
        )
        for i, var in enumerate(variants):
            ids = var["product_template_attribute_value_ids"]
            names = [
                p["name"] for p in odoo("product.template.attribute.value", "read", ids, fields=["name"])
            ]
            if not names:
                continue
            code = spec.get("variant_codes", {}).get(names[0]) or "-".join(
                [spec["name"][:4].upper(), *(n[:3].upper() for n in names)]
            )
            wvals: dict[str, Any] = {"default_code": code, "barcode": ean13(seed + i)}
            if color := spec.get("variant_images", {}).get(names[0]):
                wvals["image_variant_1920"] = png(color)
            odoo("product.product", "write", [var["id"]], wvals)
        if spec.get("active") is False:
            odoo("product.template", "write", [tmpl], {"active": False})
        print(f"product.template « {spec['name']} » : créé ({len(variants)} variante(s))")


# Partenaires INVENTÉS (domaine PARTNER). IBAN : exemples publics des normes, aucune donnée réelle.
PARTNERS: list[dict[str, Any]] = [
    {
        "name": "Holzbau Beispiel GmbH",
        "is_company": True,
        "ref": "HB-01",
        "company_registry": "HRB 12345",
        "customer_rank": 1,
        "street": "Hauptstraße 1",
        "street2": "2. OG",
        "zip": "80331",
        "city": "München",
        "country": "DE",
        "email": "info@holzbau-beispiel.example",
        "phone": "+49 89 000000",
        "website": "https://holzbau-beispiel.example",
        "lang": "de_DE",
        "vat": "DE123456789",
        "x_customer_no": "10042",
        "banks": [
            ("DE89 3704 0044 0532 0130 00", "Beispielbank", "BEISDEMMXXX", None),
            ("AT61 1904 3002 3457 3201", None, None, "Holzbau Beispiel Treuhand"),
        ],
    },
    {
        "name": "Anna Beispiel",
        "is_company": False,
        "customer_rank": 2,
        "street": "Gartenweg 5",
        "zip": "10115",
        "city": "Berlin",
        "country": "DE",
        "email": "anna@example.org",
        "mobile": "+49 170 0000000",
    },
    {
        "name": "Alpen Handel AG",
        "is_company": True,
        "customer_rank": 1,
        "supplier_rank": 3,
        "street": "Ringstraße 9",
        "zip": "1010",
        "city": "Wien",
        "country": "AT",
        "lang": "de_DE",
        "vat": "ATU12345678",
    },
    {
        "name": "Van Dijk Hout BV",
        "is_company": True,
        "supplier_rank": 1,
        "street": "Kade 4",
        "zip": "1012 AB",
        "city": "Amsterdam",
        "country": "NL",
        "banks": [("123456", None, None, None)],
    },
    {
        "name": "Nordwald Holz KG",
        "is_company": True,
        "supplier_rank": 2,
        "street": "Waldweg 1",
        "zip": "24103",
        "city": "Kiel",
    },
    {
        "name": "Max Kontakt",
        "is_company": False,
        "parent": "Holzbau Beispiel GmbH",
        "email": "max@holzbau-beispiel.example",
    },
    {"name": "Alt Kunde GmbH", "is_company": True, "customer_rank": 1, "country": "DE", "active": False},
    {"name": "Interessent Ohne Rolle GmbH", "is_company": True, "country": "DE"},
    {
        "name": "Sehr Lange Firmenbezeichnung Für Holzverarbeitung Und Möbelbau Süd GmbH",
        "is_company": True,
        "customer_rank": 1,
        "street": "Möbelstraße 3",
        "zip": "70173",
        "city": "Stuttgart",
        "country": "DE",
    },
]


def ensure_partners(odoo: Odoo) -> None:
    langs = {r["code"] for r in odoo("res.lang", "search_read", [["active", "=", True]], fields=["code"])}
    for spec in PARTNERS:
        if odoo.find("res.partner", [["name", "=", spec["name"]]], limit=1):
            print(f"res.partner « {spec['name']} » : déjà présent (rien à faire)")
            continue
        vals: dict[str, Any] = {
            k: v for k, v in spec.items() if k not in ("country", "parent", "banks", "lang", "active")
        }
        if spec.get("country"):
            vals["country_id"] = odoo.find("res.country", [["code", "=", spec["country"]]], limit=1)[0]
        if spec.get("parent"):
            vals["parent_id"] = odoo.find("res.partner", [["name", "=", spec["parent"]]], limit=1)[0]
        if spec.get("lang") in langs:  # langue non installée : laissée par défaut
            vals["lang"] = spec["lang"]
        pid = odoo("res.partner", "create", vals)
        for number, bank_name, bic, holder in spec.get("banks", []):
            bank_id = False
            if bank_name:
                found = odoo.find("res.bank", [["name", "=", bank_name]], limit=1)
                bank_id = found[0] if found else odoo("res.bank", "create", {"name": bank_name, "bic": bic})
            odoo(
                "res.partner.bank",
                "create",
                {
                    "acc_number": number,
                    "partner_id": pid,
                    "bank_id": bank_id,
                    "acc_holder_name": holder or False,
                },
            )
        if spec.get("active") is False:
            odoo("res.partner", "write", [pid], {"active": False})
        print(f"res.partner « {spec['name']} » : créé")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows : sortie redirigée en cp1252 sinon
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default="http://localhost:8027")
    ap.add_argument("--db", default="customer17")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--no-catalog", action="store_true", help="sans le mini catalogue")
    ap.add_argument("--no-partners", action="store_true", help="sans les partenaires de démonstration")
    args = ap.parse_args(argv)
    password = os.environ.get("ODOO_PASSWORD", "admin")

    common = xmlrpc.client.ServerProxy(f"{args.url}/xmlrpc/2/common", allow_none=True)
    uid = common.authenticate(args.db, args.user, password, {})
    if not uid:
        print(f"Authentification refusée pour {args.user}@{args.db}", file=sys.stderr)
        return 1
    models = xmlrpc.client.ServerProxy(f"{args.url}/xmlrpc/2/object", allow_none=True)
    for model, field in CUSTOM_FIELDS:
        created = ensure_custom_field(models, args.db, uid, password, model, field)
        state = "créé" if created else "déjà présent (rien à faire)"
        print(f"{model}.{field['name']} : {state}")
    if not args.no_catalog:
        ensure_catalog(Odoo(models, args.db, uid, password))
    if not args.no_partners:
        ensure_partners(Odoo(models, args.db, uid, password))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
