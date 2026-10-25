"""Sources de métadonnées Odoo.

Le loader Odoo ne parle pas directement à Odoo : il consomme une `OdooMetadataSource`.
Deux implémentations :
- `XmlRpcMetadataSource` : introspection d'une instance réelle (ir.model, ir.model.fields...)
- `JsonMetadataSource`   : relecture d'un dump JSON (tests, reproductibilité, hors-ligne)

`XmlRpcMetadataSource.dump()` produit exactement le JSON que `JsonMetadataSource` relit.
"""

from __future__ import annotations

import json
import xmlrpc.client
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

MODEL_FIELDS = [
    "model",
    "name",
    "transient",
    "abstract",
    "modules",
    "info",
    "state",
    "order",
]
FIELD_FIELDS = [
    "id",
    "model",
    "name",
    "field_description",
    "ttype",
    "required",
    "readonly",
    "store",
    "relation",
    "relation_field",
    "help",
    "related",
    "compute",
    "index",
    "modules",
    "state",
    "copied",
    "translate",
    "size",
    "on_delete",
    "relation_table",
    "column1",
    "column2",
    "sanitize",
    "currency_field",
    "group_expand",
    "depends",
    "company_dependent",  # Odoo ≥ 18 (sinon filtré par fields_get)
]
SELECTION_FIELDS = ["field_id", "value", "name", "sequence"]
# Attributs de fields_get absents (ou incomplets) dans ir.model.fields
FIELDS_GET_ATTRS = ["depends", "company_dependent"]
MODULE_FIELDS = ["name", "shortdesc", "installed_version", "author", "category_id"]


class OdooMetadataSource(Protocol):
    def version_info(self) -> dict[str, Any]: ...
    def installed_modules(self) -> list[dict[str, Any]]: ...
    def models(self) -> list[dict[str, Any]]: ...
    def fields(self) -> list[dict[str, Any]]: ...
    def selections(self) -> list[dict[str, Any]]: ...

    # Compléments (None = inconnu, ex. dump ancien) : voir le loader pour leur usage.
    def model_xmlids(self) -> list[dict[str, Any]] | None:
        """ir.model.data des ir.model, par id croissant : le 1er module est le module d'origine
        (ir.model.modules est trié alphabétiquement)."""
        ...

    def model_access(self) -> list[dict[str, Any]] | None:
        """ir.model.access : un model avec table a des ACL ; sert à détecter les AbstractModel
        quand ir.model n'expose pas `abstract` (Odoo < 19)."""
        ...

    def field_attributes(self) -> dict[str, dict[str, dict[str, Any]]] | None:
        """{model: {champ: {"depends": [...], "company_dependent": True}}} via fields_get (valeurs
        non vides seulement). Seul moyen de voir les champs calculés stockés
        (ir.model.fields.compute ne contient que le code des champs manuels) et les champs
        company_dependent (absents de ir.model.fields en Odoo 17)."""
        ...

    def describe(self) -> dict[str, Any]:
        """Provenance consignée dans `SchemaSnapshotModel.source`."""
        ...


@dataclass
class XmlRpcMetadataSource:
    """Introspection via /xmlrpc/2 — ne nécessite qu'un utilisateur avec droits de lecture."""

    url: str
    db: str
    username: str
    password: str = field(repr=False)  # ou API key — jamais dans repr, describe() ni dump()
    # Fabrique de proxy (url → objet exposant les méthodes XML-RPC) : garde-fous, tests
    proxy_factory: Callable[[str], Any] | None = field(default=None, repr=False)
    _uid: int | None = field(default=None, init=False, repr=False)
    _cache: dict[str, Any] = field(default_factory=dict, init=False, repr=False)

    # --- connexion --------------------------------------------------------
    def _proxy(self, service: str) -> Any:
        url = f"{self.url.rstrip('/')}/xmlrpc/2/{service}"
        if self.proxy_factory is not None:
            return self.proxy_factory(url)
        return xmlrpc.client.ServerProxy(url, allow_none=True)

    def _common(self) -> Any:
        return self._proxy("common")

    def _object(self) -> Any:
        return self._proxy("object")

    def call(self, model: str, method: str, args: list[Any], kw: dict[str, Any] | None = None) -> Any:
        """Appel brut authentifié (utilisé par le provider pour exercer les capabilities)."""
        uid = self.login()
        return self._object().execute_kw(self.db, uid, self.password, model, method, args, kw or {})

    def login(self) -> int:
        if self._uid is None:
            uid = self._common().authenticate(self.db, self.username, self.password, {})
            if not uid:
                raise PermissionError(f"Authentification Odoo refusée pour {self.username}@{self.db}")
            self._uid = int(uid)
        return self._uid

    def _search_read(
        self, model: str, domain: list[Any], fields: list[str], **kw: Any
    ) -> list[dict[str, Any]]:
        uid = self.login()
        # Certains champs n'existent pas dans toutes les versions : on filtre sur fields_get
        available = self._available_fields(model)
        fields = [f for f in fields if f in available]
        return self._object().execute_kw(
            self.db,
            uid,
            self.password,
            model,
            "search_read",
            [domain],
            {"fields": fields, "context": {"lang": "en_US", "active_test": False}, **kw},
        )

    def _available_fields(self, model: str) -> set[str]:
        key = f"fields_get:{model}"
        if key not in self._cache:
            uid = self.login()
            fg = self._object().execute_kw(
                self.db, uid, self.password, model, "fields_get", [], {"attributes": ["type"]}
            )
            self._cache[key] = set(fg)
        return self._cache[key]

    # --- OdooMetadataSource -------------------------------------------------
    def version_info(self) -> dict[str, Any]:
        if "version" not in self._cache:
            self._cache["version"] = self._common().version()
        return self._cache["version"]

    def installed_modules(self) -> list[dict[str, Any]]:
        return self._search_read(
            "ir.module.module", [["state", "=", "installed"]], MODULE_FIELDS, order="name"
        )

    def models(self) -> list[dict[str, Any]]:
        return self._search_read("ir.model", [], MODEL_FIELDS, order="model")

    def fields(self) -> list[dict[str, Any]]:
        return self._search_read("ir.model.fields", [], FIELD_FIELDS, order="model,name")

    def selections(self) -> list[dict[str, Any]]:
        try:
            return self._search_read(
                "ir.model.fields.selection", [], SELECTION_FIELDS, order="field_id,sequence"
            )
        except xmlrpc.client.Fault:
            return []

    def model_xmlids(self) -> list[dict[str, Any]]:
        return self._search_read(
            "ir.model.data", [["model", "=", "ir.model"]], ["module", "res_id"], order="id"
        )

    def model_access(self) -> list[dict[str, Any]]:
        return self._search_read("ir.model.access", [], ["model_id"], order="id")

    def field_attributes(self) -> dict[str, dict[str, dict[str, Any]]]:
        uid = self.login()
        result: dict[str, dict[str, dict[str, Any]]] = {}
        for mdl in self.models():
            try:
                fg = self._object().execute_kw(
                    self.db,
                    uid,
                    self.password,
                    mdl["model"],
                    "fields_get",
                    [],
                    {"attributes": FIELDS_GET_ATTRS},
                )
            except xmlrpc.client.Fault:
                continue  # model non instanciable côté serveur : on garde les données ir.model.fields
            attrs = {
                name: {k: v for k, v in a.items() if k in FIELDS_GET_ATTRS and v}
                for name, a in fg.items()
                if any(a.get(k) for k in FIELDS_GET_ATTRS)
            }
            if attrs:
                result[mdl["model"]] = attrs
        return result

    def describe(self) -> dict[str, Any]:
        v = self.version_info()
        return {
            "loader": "odoo.xmlrpc",
            "url": self.url,
            "db": self.db,
            "server_version": v.get("server_version"),
            "server_serie": v.get("server_serie"),
            "installed_modules": [m["name"] for m in self.installed_modules()],
        }

    def dump(self) -> dict[str, Any]:
        """Export brut → JSON relisible par JsonMetadataSource (reproductibilité)."""
        return {
            "version_info": self.version_info(),
            "describe": self.describe(),
            "modules": self.installed_modules(),
            "models": self.models(),
            "fields": self.fields(),
            "selections": self.selections(),
            "model_xmlids": self.model_xmlids(),
            "model_access": self.model_access(),
            "field_attributes": self.field_attributes(),
        }


@dataclass
class JsonMetadataSource:
    """Relit un dump produit par `XmlRpcMetadataSource.dump()`."""

    path: Path
    _data: dict[str, Any] | None = field(default=None, init=False, repr=False)

    @property
    def data(self) -> dict[str, Any]:
        if self._data is None:
            self._data = json.loads(Path(self.path).read_text(encoding="utf-8"))
        return self._data

    def version_info(self) -> dict[str, Any]:
        return self.data["version_info"]

    def installed_modules(self) -> list[dict[str, Any]]:
        return self.data.get("modules", [])

    def models(self) -> list[dict[str, Any]]:
        return self.data["models"]

    def fields(self) -> list[dict[str, Any]]:
        return self.data["fields"]

    def selections(self) -> list[dict[str, Any]]:
        return self.data.get("selections", [])

    def model_xmlids(self) -> list[dict[str, Any]] | None:
        return self.data.get("model_xmlids")

    def model_access(self) -> list[dict[str, Any]] | None:
        return self.data.get("model_access")

    def field_attributes(self) -> dict[str, dict[str, dict[str, Any]]] | None:
        return self.data.get("field_attributes")

    def describe(self) -> dict[str, Any]:
        d = dict(self.data.get("describe", {}))
        d.update({"loader": "odoo.json", "file": str(self.path)})
        return d
