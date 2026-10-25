"""Génère des dumps Odoo réduits mais réalistes (format XmlRpcMetadataSource.dump()).

Utilisés par les tests et comme exemple d'import hors-ligne. Les vrais dumps
proviennent de `uie registry dump odoo --url ...` sur une instance de référence.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent


def _field(
    fid,
    model,
    name,
    ttype,
    desc,
    *,
    required=False,
    readonly=False,
    store=True,
    relation=None,
    relation_field=None,
    compute=None,
    related=None,
    help_=None,
    modules="base",
    relation_table=None,
):
    return {
        "id": fid,
        "model": model,
        "name": name,
        "field_description": desc,
        "ttype": ttype,
        "required": required,
        "readonly": readonly,
        "store": store,
        "relation": relation or False,
        "relation_field": relation_field or False,
        "help": help_ or False,
        "related": related or False,
        "compute": compute or False,
        "index": False,
        "modules": modules,
        "state": "base",
        "relation_table": relation_table or False,
    }


def build(major: str, *, with_ecommerce_field: bool, email_required: bool, extra_model: bool):
    models = [
        {
            "id": 1,
            "model": "res.partner",
            "name": "Contact",
            "transient": False,
            "abstract": False,
            "modules": "base, contacts, product, account",
            "info": "",
            "state": "base",
            "order": "complete_name ASC, id DESC",
        },
        {
            "id": 2,
            "model": "res.country",
            "name": "Country",
            "transient": False,
            "abstract": False,
            "modules": "base",
            "info": "",
            "state": "base",
            "order": "name",
        },
        {
            "id": 3,
            "model": "res.partner.category",
            "name": "Partner Tags",
            "transient": False,
            "abstract": False,
            "modules": "base",
            "info": "",
            "state": "base",
            "order": "name",
        },
        {
            "id": 4,
            "model": "product.template",
            "name": "Product",
            "transient": False,
            "abstract": False,
            "modules": "product, sale, purchase, stock",
            "info": "",
            "state": "base",
            "order": "is_favorite desc, name",
        },
        {
            "id": 5,
            "model": "product.product",
            "name": "Product Variant",
            "transient": False,
            "abstract": False,
            "modules": "product, stock",
            "info": "",
            "state": "base",
            "order": "priority desc, default_code, name, id",
        },
        {
            "id": 6,
            "model": "product.category",
            "name": "Product Category",
            "transient": False,
            "abstract": False,
            "modules": "product",
            "info": "",
            "state": "base",
            "order": "complete_name",
        },
        {
            "id": 7,
            "model": "uom.uom",
            "name": "Product Unit of Measure",
            "transient": False,
            "abstract": False,
            "modules": "uom",
            "info": "",
            "state": "base",
            "order": "name",
        },
        {
            "id": 8,
            "model": "account.move",
            "name": "Journal Entry",
            "transient": False,
            "abstract": False,
            "modules": "account",
            "info": "",
            "state": "base",
            "order": "date desc, name desc, id desc",
        },
        {
            "id": 9,
            "model": "mail.thread",
            "name": "Email Thread",
            "transient": False,
            "abstract": True,
            "modules": "mail",
            "info": "Mixin",
            "state": "base",
            "order": "id",
        },
        {
            "id": 10,
            "model": "res.config.settings",
            "name": "Config Settings",
            "transient": True,
            "abstract": False,
            "modules": "base",
            "info": "",
            "state": "base",
            "order": "id",
        },
    ]
    if extra_model:
        models.append(
            {
                "id": 11,
                "model": "product.combo",
                "name": "Product Combo",
                "transient": False,
                "abstract": False,
                "modules": "product",
                "info": "",
                "state": "base",
                "order": "sequence, id",
            }
        )

    f = []
    fid = 0

    def add(*a, **k):
        nonlocal fid
        fid += 1
        f.append(_field(fid, *a, **k))
        return fid

    for mdl in (
        "res.partner",
        "res.country",
        "res.partner.category",
        "product.template",
        "product.product",
        "product.category",
        "uom.uom",
        "account.move",
    ) + (("product.combo",) if extra_model else ()):
        add(mdl, "id", "integer", "ID", readonly=True)
        add(mdl, "create_date", "datetime", "Created on", readonly=True)
        add(mdl, "write_date", "datetime", "Last Updated on", readonly=True)
        add(
            mdl,
            "display_name",
            "char",
            "Display Name",
            readonly=True,
            store=False,
            compute="_compute_display_name",
        )

    add("res.partner", "name", "char", "Name", required=False)
    sel_email = add("res.partner", "email", "char", "Email", required=email_required)
    add("res.partner", "phone", "char", "Phone")
    add("res.partner", "vat", "char", "Tax ID", help_="The Tax Identification Number.")
    add("res.partner", "is_company", "boolean", "Is a Company")
    sel_type = add("res.partner", "type", "selection", "Address Type")
    add("res.partner", "country_id", "many2one", "Country", relation="res.country")
    add("res.partner", "parent_id", "many2one", "Related Company", relation="res.partner")
    add("res.partner", "child_ids", "one2many", "Contact", relation="res.partner", relation_field="parent_id")
    add(
        "res.partner",
        "category_id",
        "many2many",
        "Tags",
        relation="res.partner.category",
        relation_table="res_partner_res_partner_category_rel",
    )
    add("res.partner", "customer_rank", "integer", "Customer Rank", modules="account")
    add("res.partner", "supplier_rank", "integer", "Supplier Rank", modules="account")
    add(
        "res.partner",
        "property_account_receivable_id",
        "many2one",
        "Account Receivable",
        relation="account.account",
        store=False,
        modules="account",
    )
    if with_ecommerce_field:
        add("res.partner", "x_erp_customer_no", "char", "Legacy customer no", modules="__custom__")

    add("res.country", "name", "char", "Country Name", required=True)
    add("res.country", "code", "char", "Country Code", help_="ISO 3166-1 alpha-2")
    add("res.partner.category", "name", "char", "Tag Name", required=True)

    add("product.template", "name", "char", "Name", required=True, modules="product")
    add(
        "product.template",
        "default_code",
        "char",
        "Internal Reference",
        store=False,
        compute="_compute_default_code",
        modules="product",
    )
    add("product.template", "list_price", "float", "Sales Price", modules="product")
    add(
        "product.template",
        "standard_price",
        "float",
        "Cost",
        store=False,
        compute="_compute_standard_price",
        modules="product",
    )
    sel_dtype = add(
        "product.template",
        "detailed_type" if major == "17" else "type",
        "selection",
        "Product Type",
        required=True,
        modules="product",
    )
    add(
        "product.template",
        "categ_id",
        "many2one",
        "Product Category",
        required=True,
        relation="product.category",
        modules="product",
    )
    add(
        "product.template",
        "uom_id",
        "many2one",
        "Unit of Measure",
        required=True,
        relation="uom.uom",
        modules="product",
    )
    add(
        "product.template",
        "product_variant_ids",
        "one2many",
        "Products",
        relation="product.product",
        relation_field="product_tmpl_id",
        modules="product",
    )
    add(
        "product.template",
        "seller_ids",
        "one2many",
        "Vendors",
        relation="product.supplierinfo",
        relation_field="product_tmpl_id",
        modules="purchase",
    )
    add("product.template", "sale_ok", "boolean", "Can be Sold", modules="product")
    add("product.template", "purchase_ok", "boolean", "Can be Purchased", modules="product")
    if major != "17":
        add("product.template", "is_storable", "boolean", "Track Inventory", modules="stock")

    add(
        "product.product",
        "product_tmpl_id",
        "many2one",
        "Product Template",
        required=True,
        relation="product.template",
        modules="product",
    )
    add("product.product", "barcode", "char", "Barcode", modules="product")
    add(
        "product.product",
        "lst_price",
        "float",
        "Sales Price",
        store=False,
        compute="_compute_product_lst_price",
        modules="product",
    )
    # Médias (Product Domain) : en Odoo les champs Image ont le type « binary » dans ir.model.fields ;
    # image_1920 = source, les autres tailles sont générées. product.product : images de variante.
    for size in (1920, 1024, 512, 256, 128):
        add("product.template", f"image_{size}", "binary", f"Image {size}", modules="product")
        add("product.product", f"image_variant_{size}", "binary", f"Variant Image {size}", modules="product")
        add(
            "product.product",
            f"image_{size}",
            "binary",
            f"Image {size}",
            store=False,
            compute=f"_compute_image_{size}",
            modules="product",
        )
    add("product.category", "name", "char", "Name", required=True, modules="product")
    add(
        "product.category",
        "parent_id",
        "many2one",
        "Parent Category",
        relation="product.category",
        modules="product",
    )
    add("uom.uom", "name", "char", "Unit of Measure", required=True, modules="uom")
    add("account.move", "name", "char", "Number", readonly=True, modules="account")
    add("account.move", "partner_id", "many2one", "Partner", relation="res.partner", modules="account")
    add("account.move", "amount_total", "monetary", "Total", readonly=True, modules="account")
    sel_state = add("account.move", "state", "selection", "Status", required=True, modules="account")
    add(
        "account.move",
        "line_ids",
        "one2many",
        "Journal Items",
        relation="account.move.line",
        relation_field="move_id",
        modules="account",
    )
    if extra_model:
        add("product.combo", "name", "char", "Name", required=True, modules="product")

    selections = [
        {"field_id": [sel_type, "type"], "value": "contact", "name": "Contact", "sequence": 1},
        {"field_id": [sel_type, "type"], "value": "invoice", "name": "Invoice Address", "sequence": 2},
        {"field_id": [sel_type, "type"], "value": "delivery", "name": "Delivery Address", "sequence": 3},
        {
            "field_id": [sel_dtype, "type"],
            "value": "consu",
            "name": "Goods" if major != "17" else "Consumable",
            "sequence": 1,
        },
        {"field_id": [sel_dtype, "type"], "value": "service", "name": "Service", "sequence": 2},
        {"field_id": [sel_state, "state"], "value": "draft", "name": "Draft", "sequence": 1},
        {"field_id": [sel_state, "state"], "value": "posted", "name": "Posted", "sequence": 2},
        {"field_id": [sel_state, "state"], "value": "cancel", "name": "Cancelled", "sequence": 3},
    ]
    if major == "17":
        selections.append(
            {"field_id": [sel_dtype, "type"], "value": "product", "name": "Storable Product", "sequence": 3}
        )
    _ = sel_email

    modules = [
        {
            "name": n,
            "shortdesc": d,
            "installed_version": f"{major}.0.1.0",
            "author": "Odoo S.A.",
            "category_id": [1, "Hidden"],
        }
        for n, d in [
            ("base", "Base"),
            ("contacts", "Contacts"),
            ("product", "Products & Pricelists"),
            ("uom", "Units of measure"),
            ("sale_management", "Sales"),
            ("purchase", "Purchase"),
            ("stock", "Inventory"),
            ("account", "Invoicing"),
            ("mail", "Discuss"),
        ]
    ]
    return {
        "version_info": {
            "server_version": f"{major}.0",
            "server_serie": f"{major}.0",
            "server_version_info": [int(major), 0, 0, "final", 0, ""],
            "protocol_version": 1,
        },
        "describe": {
            "url": f"http://odoo{major}:8069",
            "db": f"ref{major}",
            "installed_modules": [m["name"] for m in modules],
        },
        "modules": modules,
        "models": models,
        "fields": f,
        "selections": selections,
    }


def build_customer() -> dict:
    """Instance client Odoo 17 : même base que le standard + écarts représentatifs (Instance Diff).

    - CUSTOM      : res.partner.x_customer_no (champ manuel, sans module) ; entité x_customer_segment
    - THIRD_PARTY : modules hr / project / rating hors baseline
                    (hr.employee, project.project, res.partner.employee_ids)
    - module absent démontré : mail désinstallé → mail.thread absent
    - UNKNOWN     : res.country.code absent alors que base est installé ; champ ajouté sans module
    - Product Domain : product.template.x_supplier_ref (CUSTOM, créé par customer-customize.py) et
                    product.template.project_id (THIRD_PARTY, module sale_project hors baseline)
    - Semantic Enrichment (V1b) : x_pack_code / x_notes_internal (CUSTOM), copie related
                    product.product.x_supplier_ref, extensions tierces project_template_id, rating_ids,
                    service_policy, service_tracking (exclues par règle, jamais posées au supplier)
    - risque de compatibilité : res.partner.email devenu obligatoire
    """
    d = build("17", with_ecommerce_field=False, email_required=True, extra_model=False)
    d["describe"] = {**d["describe"], "url": "http://odoo17-customer:8069", "db": "customer17"}
    next_model = max(x["id"] for x in d["models"]) + 1
    next_field = max(x["id"] for x in d["fields"]) + 1

    def model(name: str, label: str, modules: str, state: str = "base") -> None:
        nonlocal next_model
        d["models"].append(
            {
                "id": next_model,
                "model": name,
                "name": label,
                "transient": False,
                "abstract": False,
                "modules": modules,
                "info": "",
                "state": state,
                "order": "id",
            }
        )
        next_model += 1

    def fld(
        model_: str, name: str, ttype: str, desc: str, *, modules: str, state: str = "base", **kw
    ) -> None:
        nonlocal next_field
        f = _field(next_field, model_, name, ttype, desc, modules=modules, **kw)
        f["state"] = state
        d["fields"].append(f)
        next_field += 1

    # CUSTOM (créés via UI / XML-RPC : state=manual, aucun module d'origine)
    fld("res.partner", "x_customer_no", "char", "Customer No", modules="", state="manual")
    model("x_customer_segment", "Customer Segment", modules="", state="manual")
    fld("x_customer_segment", "x_name", "char", "Name", modules="", state="manual")
    # THIRD_PARTY (modules installés dans l'instance mais absents de la baseline)
    model("hr.employee", "Employee", modules="hr")
    fld("hr.employee", "name", "char", "Employee Name", modules="hr")
    fld("hr.employee", "work_contact_id", "many2one", "Work Contact", modules="hr", relation="res.partner")
    model("project.project", "Project", modules="project")
    fld("project.project", "name", "char", "Name", modules="project")
    fld("res.partner", "employee_ids", "one2many", "Employees", modules="hr", relation="hr.employee")
    # Cas réel (module rating) : champ hérité d'un mixin étendu, attribué par Odoo au module du modèle
    # hôte (account) → doit rester THIRD_PARTY car il pointe vers une entité tierce ; et champ multi-modules
    model("rating.rating", "Rating", modules="rating")
    fld("rating.rating", "rating", "float", "Rating Value", modules="rating")
    fld("account.move", "rating_ids", "one2many", "Ratings", modules="account", relation="rating.rating")
    fld("res.partner", "rating_ids", "one2many", "Ratings", modules="base, rating", relation="rating.rating")
    # Product Domain : extensions sur l'entité PRIMARY du catalogue
    fld("product.template", "x_supplier_ref", "char", "Supplier Reference", modules="", state="manual")
    fld(
        "product.template",
        "project_id",
        "many2one",
        "Project",
        modules="sale_project",
        relation="project.project",
    )
    # Semantic Enrichment (V1b) : comme sur l'instance Docker (customer-customize.py + sale_project / rating)
    fld("product.template", "x_pack_code", "char", "Packaging Code", modules="", state="manual")
    fld("product.template", "x_notes_internal", "text", "Internal notes", modules="", state="manual")
    fld(
        "product.template",
        "project_template_id",
        "many2one",
        "Project Template",
        modules="sale_project",
        relation="project.project",
    )
    fld(
        "product.template",
        "rating_ids",
        "one2many",
        "Ratings",
        modules="rating",
        relation="rating.rating",
    )
    fld("product.template", "service_policy", "selection", "Service Invoicing Policy", modules="sale_project")
    fld("product.template", "service_tracking", "selection", "Create on Order", modules="sale_project")
    # champ custom du modèle parent, hérité par délégation (_inherits) : copie related, même donnée
    fld(
        "product.product",
        "x_supplier_ref",
        "char",
        "Supplier Reference",
        modules="",
        state="manual",
        store=False,
        related="product_tmpl_id.x_supplier_ref",
    )
    # UNKNOWN : ajouté sans module connu
    fld("res.partner", "legacy_ref", "char", "Legacy Ref", modules="")
    # Module standard absent (démontrable) : mail désinstallé
    d["models"] = [x for x in d["models"] if x["model"] != "mail.thread"]
    d["fields"] = [x for x in d["fields"] if x["model"] != "mail.thread"]
    # Élément standard manquant sans module absent (base est installé) → UNKNOWN
    d["fields"] = [x for x in d["fields"] if (x["model"], x["name"]) != ("res.country", "code")]

    extra = [
        ("hr", "Employees"),
        ("project", "Project"),
        ("rating", "Customer Rating"),
        ("sale_project", "Sales - Project"),
    ]
    d["modules"] = [x for x in d["modules"] if x["name"] != "mail"] + [
        {
            "name": n,
            "shortdesc": desc,
            "installed_version": "17.0.1.0",
            "author": "Odoo S.A.",
            "category_id": [1, "Hidden"],
        }
        for n, desc in extra
    ]
    d["describe"]["installed_modules"] = [x["name"] for x in d["modules"]]
    return d


def _write(name: str, data: dict) -> None:
    # LF quel que soit l'OS (sinon CRLF sous Windows : fichiers « modifiés » sans changement réel)
    (HERE / name).write_text(json.dumps(data, indent=1), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    _write(
        "odoo17_ref.json", build("17", with_ecommerce_field=False, email_required=False, extra_model=False)
    )
    _write("odoo18_ref.json", build("18", with_ecommerce_field=False, email_required=False, extra_model=True))
    _write("odoo17_customer_instance.json", build_customer())
    print("fixtures written")
