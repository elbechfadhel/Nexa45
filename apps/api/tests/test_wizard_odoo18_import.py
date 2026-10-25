"""Vérification RÉELLE : le fichier préparé par le mode « fichier d'abord » (quick-start → fichier préparé)
passe l'import natif d'un vrai Odoo 18 (instance Docker de référence) avec 0 erreur.

L'import est lancé en mode ESSAI d'Odoo (`base_import.import.execute_import(..., dryrun=True)`, le bouton
« Test » de l'écran d'import) : toutes les validations d'Odoo s'exécutent, puis tout est annulé ; la base de
référence n'est jamais modifiée (vérifié). Sauté si l'Odoo 18 de test ne répond pas :

    docker compose -f docker/docker-compose.yml --profile odoo up -d odoo-db odoo18
    UIE_ODOO18_URL (défaut http://127.0.0.1:8018), UIE_ODOO18_DB (ref18), admin / admin"""

from __future__ import annotations

import collections
import contextlib
import io
import json
import os
import re
import xmlrpc.client
from collections.abc import Iterator
from typing import Any

import httpx
import polars as pl
import pytest
from test_lexware_partner_api import _client
from test_wizard_quick_start_api import world  # noqa: F401 - fixture partagée (Odoo 18 du Registry)
from uie_core.wizard.quality import gs1_valid

URL = os.environ.get("UIE_ODOO18_URL", "http://127.0.0.1:8018").rstrip("/")
DB = os.environ.get("UIE_ODOO18_DB", "ref18")


def _odoo() -> tuple[Any, int] | None:
    try:
        uid = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common").authenticate(DB, "admin", "admin", {})
    except (OSError, xmlrpc.client.Error):
        return None
    return (xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object", allow_none=True), uid) if uid else None


ODOO = _odoo()
pytestmark = pytest.mark.skipif(ODOO is None, reason="Odoo 18 de test absent (docker compose --profile odoo)")


def _ean(seed: int) -> str:
    body = f"{4006381:07d}{seed:05d}"
    return next(body + str(d) for d in range(10) if gs1_valid(body + str(d)))


def _catalog(category: str = "All / Saleable") -> bytes:
    """5 produits INVENTÉS, en-têtes d'un export de boutique ; catégorie et unité par leur nom Odoo."""
    rows = [["Product name", "SKU", "Sales price", "EAN", "Category", "Unit"]]
    for i, (name, price) in enumerate(
        [
            ("Oak tray", "39.90"),
            ("Birch crate", "24.50"),
            ("Pine box", "18.75"),
            ("Ash cup", "2.40"),
            ("Elm pad", "4.90"),
        ]
    ):
        rows.append([name, f"NX-{i + 1:03d}", price, _ean(i + 1), category, "Units"])
    return "\n".join(",".join(r) for r in rows).encode()


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _web_session() -> httpx.Client:
    """Session web d'Odoo (comme le navigateur) : nécessaire pour envoyer un fichier à l'écran d'import."""
    web = httpx.Client(base_url=URL, timeout=120)
    auth = web.post(
        "/web/session/authenticate",
        json={"jsonrpc": "2.0", "params": {"db": DB, "login": "admin", "password": "admin"}},
    )
    assert auth.status_code == 200 and "error" not in auth.json(), auth.text
    return web


def _csrf(web: httpx.Client) -> str:
    page = web.get("/odoo").text
    found = re.search(r'"?csrf_token"?\s*:\s*"([^"]+)"', page)  # page du client web : csrf_token: "…"
    assert found, "csrf token introuvable"
    return found.group(1)


def _rpc(
    web: httpx.Client, model: str, method: str, args: list[Any], kwargs: dict[str, Any] | None = None
) -> Any:
    res = web.post(
        f"/web/dataset/call_kw/{model}/{method}",
        json={
            "jsonrpc": "2.0",
            "params": {"model": model, "method": method, "args": args, "kwargs": kwargs or {}},
        },
    ).json()
    assert "error" not in res, res.get("error")
    return res["result"]


def _call(model: str, method: str, *args: Any, **kw: Any) -> Any:
    assert ODOO is not None
    proxy, uid = ODOO
    return proxy.execute_kw(DB, uid, "admin", model, method, list(args), kw)


OPTIONS = {
    "has_headers": True,
    "quoting": '"',
    "separator": ",",
    "encoding": "utf-8",
    "float_thousand_separator": ",",
    "float_decimal_separator": ".",
    "date_format": "",
    "datetime_format": "",
    "keep_matches": False,
    "limit": 5000,
    "skip": 0,
    "tracking_disable": True,
    "name_create_enabled_fields": {},
    "import_set_empty_fields": [],
    "import_skip_records": [],
    "fallback_values": {},
    "advanced": False,
}


COPY = f"{DB}_wizard_test"


@contextlib.contextmanager
def _odoo_copy() -> Iterator[Any]:
    """Copie de la base de référence (celle-ci n'est JAMAIS modifiée), créée au premier besoin puis
    réutilisée : les enregistrements « à importer d'abord » y sont importés pour de vrai (mêmes identifiants
    externes à chaque passage : mis à jour, jamais en double) ; les lignes n'y font qu'un essai (annulé)."""
    try:
        xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/db").duplicate_database("admin", DB, COPY)
    except xmlrpc.client.Fault:
        pass  # déjà créée par un passage précédent
    uid = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/common").authenticate(COPY, "admin", "admin", {})
    proxy = xmlrpc.client.ServerProxy(f"{URL}/xmlrpc/2/object", allow_none=True)
    yield lambda model, method, args, kw=None: proxy.execute_kw(
        COPY, uid, "admin", model, method, args, kw or {}
    )


def _sheet_import(call: Any, model: str, frame: pl.DataFrame, dryrun: bool) -> dict[str, Any]:
    """Une feuille du fichier préparé dans l'écran d'import d'Odoo (CSV texte : seule forme que le XML-RPC
    d'Odoo transmet telle quelle pour ce champ binaire)."""
    buf = io.StringIO()
    frame.write_csv(buf)
    import_id = call(
        "base_import.import",
        "create",
        [{"res_model": model, "file": buf.getvalue(), "file_name": "sheet.csv", "file_type": "text/csv"}],
    )
    columns = list(frame.columns)
    return call(
        "base_import.import", "execute_import", [[import_id], columns, columns, OPTIONS], {"dryrun": dryrun}
    )


def _import_into_copy(prepared: bytes) -> tuple[list[dict[str, Any]], int, dict[str, int]]:
    """Feuilles « (import first) » importées pour de vrai dans une copie, puis ESSAI des lignes (« Data ») :
    (erreurs d'Odoo, lignes acceptées, enregistrements créés d'abord par feuille)."""
    sheets = pl.read_excel(io.BytesIO(prepared), sheet_id=0, infer_schema_length=0)
    readme = {str(r[0]): str(r[1]) for r in sheets["Read me"].iter_rows()}
    first = [n for n in sheets if n.endswith("(import first)")]
    created: dict[str, int] = {}
    with _odoo_copy() as call:
        for name in first:
            entity = re.search(r"into (\S+) BEFORE the sheet “Data”", readme.get("Import first", "")).group(1)
            done = _sheet_import(call, entity, sheets[name], dryrun=False)
            assert not [m for m in done.get("messages", []) if m.get("type") == "error"], done["messages"]
            created[name] = len(done.get("ids") or [])
        result = _sheet_import(call, "product.template", sheets["Data"], dryrun=True)
    errors = [m for m in result.get("messages", []) if m.get("type") == "error"]
    return errors, len(result.get("ids") or []), created


def _import_errors(
    env: dict[str, Any],
    content: bytes,
    name: str = "shop.csv",
    typed: dict[str, str] | None = None,
    choices: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Parcours de l'utilisateur jusqu'à l'essai d'import d'Odoo 18 : (erreurs d'Odoo, lignes acceptées).
    `typed` : valeur saisie par l'utilisateur pour une question bloquante sans valeur présélectionnée ;
    `choices` : décisions sur les valeurs prises par l'utilisateur dans l'aperçu (ex. devise)."""
    alice = _client(env["app"], "alice@acme.io")
    start = alice.post(
        "/api/wizard/quick-start",
        files={"file": (name, content, "text/csv")},
        data={"payload": json.dumps({"target": "registry:odoo:odoo:18"})},
    )
    assert start.status_code == 200, start.text
    body = start.json()
    # l'utilisateur confirme les correspondances proposées « à confirmer », puis accepte la valeur
    # présélectionnée de chaque question bloquante qui reste
    confirmed = [
        {"position": c["position"], "target": c["target"]}
        for c in body["mapping"]["columns"]
        if c["status"] == "to_confirm" and c["target"]
    ]
    decisions = body["decisions"] + confirmed
    mapped = {d["target"] for d in decisions}
    # une décision (devise, catégories…) est toujours répondue ; une question de champ, s'il reste vide
    remaining = [q for q in body["questions"] if q["decision"] or q["question"]["field"] not in mapped]
    answers = {
        q["question"]["field"]: q["default"] or (typed or {}).get(q["question"]["field"]) for q in remaining
    }
    assert all(answers.values()), remaining
    # une question sur les VALEURS (ex. devise) se range dans `choices`, les autres dans `fills`
    decided = {q["decision"]: answers[q["question"]["field"]] for q in remaining if q["decision"]}
    fills = [
        {"field": q["question"]["field"], "common": answers[q["question"]["field"]], "confirmed": True}
        for q in remaining
        if not q["decision"]
    ]
    proposal = {
        k: body["proposal"][k] for k in ("title", "summary", "entity", "template_name", "configuration")
    }
    proposal |= {
        "rows": [],
        "notes": [],
        "fields": [
            {k: v for k, v in f.items() if k not in ("verified", "allowed", "reference")}
            for f in body["proposal"]["fields"]
        ],
    }
    payload = {
        "answers": body["answers"],
        "proposal": proposal,
        "decisions": decisions,
        "fills": fills + body["fills"],  # décisions appliquées au-delà des 3 questions
        "choices": {**body["choices"], **decided, **(choices or {})},
    }
    preview = alice.post(
        "/api/wizard/file/preview",
        files={"file": (name, content, "text/csv")},
        data={"payload": json.dumps(payload)},
    )
    assert preview.status_code == 200, preview.text
    env["last_preview"] = preview.json()
    prepared = alice.post(
        "/api/wizard/file/prepared",
        files={"file": (name, content, "text/csv")},
        data={"payload": json.dumps(payload)},
    )
    assert prepared.status_code == 200, prepared.text
    data = pl.read_excel(io.BytesIO(prepared.content), sheet_name="Data", infer_schema_length=0)
    env["last_data"] = data  # fichier préparé du dernier passage (contrôles des tests)
    env["last_prepared"] = prepared.content
    env["last_body"] = body

    before = _call("product.template", "search_count", [])
    web = _web_session()
    import_id = _rpc(web, "base_import.import", "create", [{"res_model": "product.template"}])
    # même chemin que l'écran d'import : le fichier .xlsx envoyé tel quel (octets bruts)
    upload = web.post(
        "/base_import/set_file",
        data={"id": str(import_id), "csrf_token": _csrf(web)},
        files={"ufile": ("prepared.xlsx", prepared.content, XLSX)},
    )
    assert upload.status_code == 200, upload.text
    options = {
        "has_headers": True,
        "sheet": "Data",
        "quoting": '"',
        "separator": ",",
        "encoding": "utf-8",
        "float_thousand_separator": ",",
        "float_decimal_separator": ".",
        "date_format": "",
        "datetime_format": "",
        "keep_matches": False,
        "limit": 2000,
        "skip": 0,
        "tracking_disable": True,
        "name_create_enabled_fields": {},
        "import_set_empty_fields": [],
        "import_skip_records": [],
        "fallback_values": {},
        "advanced": False,
    }
    columns = list(data.columns)
    result = _rpc(
        web,
        "base_import.import",
        "execute_import",
        [[import_id], columns, columns, options],
        {"dryrun": True},
    )
    errors = [m for m in result.get("messages", []) if m.get("type") == "error"]
    assert _call("product.template", "search_count", []) == before  # essai : rien n'est gardé
    return errors, len(result.get("ids") or [])


def test_the_file_prepared_by_quick_start_passes_odoo18_native_import_with_no_error(world):  # noqa: F811
    errors, imported = _import_errors(world, _catalog())
    data = world["last_data"]
    assert data.height == 5
    assert {"name", "default_code", "list_price", "barcode", "categ_id", "uom_id"} <= set(data.columns)
    assert errors == [], errors  # 0 erreur d'import dans le vrai Odoo 18
    assert imported == 5  # les 5 produits passent


def test_the_check_really_sees_an_import_error(world):  # noqa: F811
    """Contrôle du test lui-même : une catégorie inconnue d'Odoo produit bien des erreurs d'import."""
    errors, _ = _import_errors(world, _catalog("Category that does not exist"))
    assert errors and all("Category that does not exist" in str(e.get("message")) for e in errors)


def test_the_1000_products_import_without_error_once_the_categories_are_created(world):  # noqa: F811
    """Les 1 000 produits du test de non-régression (catégories INVENTÉES « categories-N ») : l'utilisateur
    accepte les réponses présélectionnées (devise USD déduite, catégories créées d'abord, type, unité laissée
    au défaut de la cible). Sans créer les catégories : uniquement elles sont refusées, chacune annoncée avant
    l'import ; en les créant d'abord (copie de ref18) : 0 erreur au test d'import réel d'Odoo 18."""
    from test_wizard_1000_products import _catalog as catalog_1000

    errors, imported = _import_errors(world, catalog_1000(1000), "catalog.csv")
    data = world["last_data"]
    assert data.height == 1000 and {"name", "list_price", "barcode", "categ_id"} <= set(data.columns)
    by_field = collections.Counter(e.get("field") for e in errors)
    assert by_field == {"categ_id": 1000}, by_field  # sans créer d'abord : la catégorie, une par produit
    # annoncé AVANT l'import : la feuille des catégories à importer d'abord, jamais « inconnues de la cible »
    created_first = [(c["field"]["name"], c["count"]) for c in world["last_preview"]["created_records"]]
    assert created_first == [("categ_id", 1000)]
    assert not any(i["field"]["name"] == "categ_id" for i in world["last_preview"]["value_issues"])
    assert len(world["last_body"]["questions"]) <= 3
    assert all(q["default"] for q in world["last_body"]["questions"])  # chacune présélectionnée
    errors, imported, created = _import_into_copy(world["last_prepared"])
    assert created == {"Product Category (import first)": 1000}
    assert errors == [], errors[:5]
    assert imported == 1000


def _german_like(category: str = "Werkzeug", units: tuple[str, str, str] = ("Units",) * 3) -> bytes:
    """Liste d'articles à l'allemande (INVENTÉE) : ligne de titre, catégories du client (dont un chemin)."""
    rows = [
        "Artikelexport Testhaus GmbH;;;;;",
        ";;;;;",
        "Art.-Nr.;Artikelbezeichnung;VK netto;EAN;Warengruppe;Einheit",
        f"01000;Akku-Bohrschrauber;1.091,60;{_ean(1)};{category};{units[0]}",
        f"01001;Gartenschlauch;20,97;{_ean(2)};Garten / Bewässerung;{units[1]}",
        f"01002;Blumenerde;6,71;;Garten;{units[2]}",
    ]
    return bytes([13, 10]).decode().join(rows).encode("cp1252")  # fins de ligne CRLF


def test_missing_categories_are_created_first_then_the_products_import_without_error(world):  # noqa: F811
    """Catégories du fichier absentes d'Odoo : choix présélectionné « créer » → feuille importée d'abord
    (identifiants externes, hiérarchie « Garten / Bewässerung »), puis 0 erreur au test d'import des articles.
    Aucune écriture dans la base de référence (copie jetable)."""
    errors, _ = _import_errors(world, _german_like(), "liste.csv")
    assert errors and all(e.get("field") == "categ_id" for e in errors)  # sans créer d'abord : refusé
    body = world["last_body"]
    question = next(q for q in body["questions"] if q["decision"] == "records:categ_id")
    assert question["default"] == "create"
    errors, imported, created = _import_into_copy(world["last_prepared"])
    assert created == {"Product Category (import first)": 3}  # Werkzeug, Garten, Garten / Bewässerung
    assert errors == [], errors and imported == 3


def test_unit_values_of_the_file_are_written_with_odoo_names_and_import_without_error(world):  # noqa: F811
    """« Stück », « Meter », « Packung » (emballage compté comme une pièce : réponse présélectionnée) →
    Units, m, Units : 0 erreur au test d'import réel d'Odoo 18."""
    errors, _ = _import_errors(world, _german_like(units=("Stück", "Meter", "Packung")), "liste.csv")
    body = world["last_body"]
    question = next(q for q in body["questions"] if q["decision"] == "units:uom_id")
    assert question["default"] == "unit" and "Packung" in question["question"]["title"]
    assert world["last_data"]["uom_id"].to_list() == ["Units", "m", "Units"]
    errors, imported, _ = _import_into_copy(world["last_prepared"])
    assert errors == [], errors
    assert imported == 3


def test_importing_the_same_prepared_file_twice_updates_the_products_and_never_duplicates(world):  # noqa: F811
    """Colonne « id » du fichier préparé (identifiant externe fondé sur la référence interne) : import RÉEL
    des lignes dans la copie de ref18, puis réimport du même fichier → les mêmes produits sont mis à jour,
    aucun doublon. Les SKU sont propres à ce test : un passage précédent ne fait que les mettre à jour."""
    rows = [["Product name", "SKU", "Sales price", "Category", "Unit"]]
    rows += [
        [f"Reimport item {n}", f"NX-REIMPORT-{n}", f"{n}.50", "All / Saleable", "Units"] for n in (1, 2, 3)
    ]
    content = "\n".join(",".join(r) for r in rows).encode()
    errors, imported = _import_errors(world, content, "reimport.csv")
    assert errors == [] and imported == 3
    data = world["last_data"]
    assert data.columns[0] == "id"
    assert data["id"].to_list() == [f"__import__.nexa45_product_template_nx_reimport_{n}" for n in (1, 2, 3)]
    sheets = pl.read_excel(io.BytesIO(world["last_prepared"]), sheet_id=0, infer_schema_length=0)
    skus = [f"NX-REIMPORT-{n}" for n in (1, 2, 3)]
    with _odoo_copy() as call:
        found: list[list[int]] = []
        for _ in range(2):  # import, puis réimport du MÊME fichier
            done = _sheet_import(call, "product.template", sheets["Data"], dryrun=False)
            assert not [m for m in done.get("messages", []) if m.get("type") == "error"], done["messages"]
            found.append(sorted(done["ids"]))
            ids = call(
                "product.template",
                "search",
                [[["default_code", "in", skus]]],
                {"context": {"active_test": False}},
            )
            assert len(ids) == 3  # jamais en double
        assert found[0] == found[1]  # le réimport a mis à jour les mêmes produits
        names = call(
            "ir.model.data",
            "search_count",
            [[["module", "=", "__import__"], ["name", "like", "nexa45_product_template_nx_reimport_"]]],
        )
        assert names == 3


def test_the_import_defaults_declared_by_the_odoo_pack_are_what_odoo_really_writes():
    """Le pack Odoo DÉCLARE ce que l'import de fichier écrit dans un prix sans valeur (colonne absente →
    valeur par défaut, cellule vide → valeur d'une cellule vide) : vérifié par import RÉEL (copie)."""
    import uie_domain_packs  # noqa: F401 - enregistre les packs
    from uie_core.domains import DomainPackRegistry

    declared = DomainPackRegistry.resolve("PRODUCT", "odoo", "18").import_defaults()
    absent = pl.DataFrame({"id": ["__import__.nexa45_probe_absent"], "name": ["Probe absent"]})
    empty = pl.DataFrame(
        {
            "id": ["__import__.nexa45_probe_empty"],
            "name": ["Probe empty"],
            "list_price": [None],
            "standard_price": [None],
        },
        schema={"id": pl.String, "name": pl.String, "list_price": pl.String, "standard_price": pl.String},
    )
    with _odoo_copy() as call:
        read: dict[str, dict[str, Any]] = {}
        for kind, frame in (("absent", absent), ("empty", empty)):
            done = _sheet_import(call, "product.template", frame, dryrun=False)
            assert not [m for m in done.get("messages", []) if m.get("type") == "error"], done["messages"]
            (row,) = call(
                "product.template", "read", [done["ids"]], {"fields": ["list_price", "standard_price"]}
            )
            read[kind] = row
    for field in ("list_price", "standard_price"):
        assert float(declared[field].absent) == read["absent"][field], field
        assert float(declared[field].empty) == read["empty"][field], field
