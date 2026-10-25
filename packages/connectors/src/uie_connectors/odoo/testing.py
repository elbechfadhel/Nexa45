"""Double de test : faux serveur Odoo XML-RPC servi depuis un dump JSON (`uie registry dump`).

Permet d'exercer le vrai code du provider (test de connexion, capabilities, discovery) hors ligne.
Chaque appel est enregistré dans `calls` : les tests vérifient ainsi qu'aucune écriture n'est tentée.
V2a : `records` (modèle → enregistrements, format de search_read Odoo) sert aussi des DONNÉES : search_read,
search_count et read, filtre « in », pagination, archivés (active_test) et binaires (bin_size).

    fake = FakeOdooServer.from_dump(path, password="admin").with_records(records_path)

    fake = FakeOdooServer.from_dump(path, password="admin")
    provider = OdooSchemaDiscoveryProvider(proxy_factory=fake.proxy)
"""

from __future__ import annotations

import json
import xmlrpc.client
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_META_TABLES = {
    "ir.module.module": "modules",
    "ir.model": "models",
    "ir.model.fields": "fields",
    "ir.model.fields.selection": "selections",
    "ir.model.data": "model_xmlids",
    "ir.model.access": "model_access",
}
_PARTNERS = [{"id": 1, "display_name": "My Company"}, {"id": 3, "display_name": "Administrator"}]


@dataclass
class FakeOdooServer:
    dump: dict[str, Any]
    db: str
    username: str = "admin"
    password: str = "admin"
    uid: int = 2
    calls: list[tuple[str, str]] = field(default_factory=list)
    records: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # écriture (V2d.2) : identifiants externes « module.nom » → (modèle, id), comme ir.model.data
    xmlids: dict[str, tuple[str, int]] = field(default_factory=dict)
    lose_response_on: str | None = None  # identifiant externe dont la réponse de `load` se perd (une fois)

    def with_records(self, path: str | Path) -> FakeOdooServer:
        self.records = json.loads(Path(path).read_text(encoding="utf-8"))["records"]
        return self

    @classmethod
    def from_dump(cls, path: str | Path, **kw: Any) -> FakeOdooServer:
        dump = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(dump=dump, db=kw.pop("db", dump.get("describe", {}).get("db", "odoo")), **kw)

    # --- fabrique compatible XmlRpcMetadataSource.proxy_factory -----------
    def proxy(self, url: str) -> Any:
        return _Common(self) if url.rstrip("/").endswith("/common") else _Object(self)

    @property
    def methods_called(self) -> set[str]:
        return {m for _, m in self.calls}

    # --- implémentation ----------------------------------------------------
    def _check(self, db: str, uid: int, password: str) -> None:
        if db != self.db or uid != self.uid or password != self.password:
            raise xmlrpc.client.Fault(3, "Access Denied")

    def execute_kw(self, model: str, method: str, args: list[Any], kw: dict[str, Any]) -> Any:
        self.calls.append((model, method))
        if method == "fields_get":
            return self._fields_get(model, kw.get("attributes") or [])
        if model in _META_TABLES:
            rows = self._filter(self.dump.get(_META_TABLES[model]) or [], args[0] if args else [])
            if method == "search_read":
                return self._project(rows, kw.get("fields"), kw.get("limit"))
            if method == "search_count":
                return len(rows)
            if method == "search":
                return [r["id"] for r in rows][: kw.get("limit") or None]
        if model in self.records:
            return self._records(model, method, args, kw)
        if model == "res.partner":
            if method == "search_read":
                return self._project(_PARTNERS, kw.get("fields"), kw.get("limit"))
            if method == "search_count":
                return len(_PARTNERS)
            if method == "search":
                return [p["id"] for p in _PARTNERS][: kw.get("limit") or None]
            if method == "read":
                ids = set(args[0])
                return self._project([p for p in _PARTNERS if p["id"] in ids], kw.get("fields"), None)
        raise xmlrpc.client.Fault(2, f"FakeOdooServer : {model}.{method} non simulé")

    def _records(self, model: str, method: str, args: list[Any], kw: dict[str, Any]) -> Any:
        if method in ("create", "write", "load"):
            return self._write(model, method, args)
        ctx = kw.get("context") or {}
        rows = self.records[model]
        if ctx.get("active_test", True):
            rows = [r for r in rows if r.get("active", True)]
        if method == "read":
            ids = list(args[0])
            by_id = {r["id"]: r for r in rows}
            return [self._shape(model, by_id[i], kw.get("fields"), ctx) for i in ids if i in by_id]
        for cond in args[0] if args else []:
            key, op, value = cond
            if op == "in":
                rows = [r for r in rows if _ref_id(r.get(key)) in value]
            elif op == "=":
                rows = [r for r in rows if _ref_id(r.get(key)) == value]
            elif op in (">=", "<="):
                keep = (lambda a, b: a >= b) if op == ">=" else (lambda a, b: a <= b)
                rows = [r for r in rows if r.get(key) not in (None, False) and keep(r.get(key), value)]
            elif op == "ilike":
                rows = [r for r in rows if str(value).casefold() in str(r.get(key) or "").casefold()]
            else:
                raise xmlrpc.client.Fault(2, f"FakeOdooServer : opérateur {op} non simulé")
        rows = sorted(rows, key=lambda r: r["id"])
        if method == "search_count":
            return len(rows)
        if method == "search_read":
            offset, limit = kw.get("offset") or 0, kw.get("limit")
            page = rows[offset : offset + limit] if limit else rows[offset:]
            return [self._shape(model, r, kw.get("fields"), ctx) for r in page]
        raise xmlrpc.client.Fault(2, f"FakeOdooServer : {model}.{method} non simulé")

    # --- écriture (V2d.2) : create / write / load, jamais unlink ---------------------------------------
    def _write(self, model: str, method: str, args: list[Any]) -> Any:
        rows = self.records[model]
        if method == "create":
            return self._create(model, args[0])
        if method == "write":
            ids, vals = args[0], args[1]
            for r in rows:
                if r["id"] in ids:
                    r.update(self._decode(model, vals))
            return True
        # load : une ligne par enregistrement, colonne « id » = identifiant externe
        fields, data = args[0], args[1]
        out_ids: list[int] = []
        for line in data:
            values: dict[str, Any] = {}
            xmlid = None
            for f, v in zip(fields, line, strict=True):
                if f == "id":
                    xmlid = v
                elif f.endswith("/.id"):
                    ids = [int(x) for x in str(v).split(",") if x]
                    values[f[: -len("/.id")]] = ids if self._is_many(model, f[: -len("/.id")]) else ids[0]
                else:
                    values[f] = v
            if not values.get("name") and not (xmlid and xmlid in self.xmlids):
                error = {"type": "error", "message": "Missing required value: name"}
                return {"ids": False, "messages": [error]}
            if xmlid and xmlid in self.xmlids:
                rid = self.xmlids[xmlid][1]
                for r in rows:
                    if r["id"] == rid:
                        r.update(values)
            else:
                rid = self._create(model, values)
                if xmlid:
                    self.xmlids[xmlid] = (model, rid)
            out_ids.append(rid)
            if xmlid and xmlid == self.lose_response_on:
                self.lose_response_on = None
                raise OSError("connexion perdue (simulée) après la création")
        return {"ids": out_ids, "messages": []}

    def _create(self, model: str, vals: dict[str, Any]) -> int:
        rows = self.records[model]
        rid = max((r["id"] for r in rows), default=0) + 1
        row = {"id": rid, "active": True, **self._decode(model, vals)}
        if "complete_name" in {f["name"] for f in self.dump["fields"] if f["model"] == model}:
            parent = next((r for r in rows if r["id"] == _ref_id(row.get("parent_id"))), None)
            row["complete_name"] = f"{parent['complete_name']} / {row['name']}" if parent else row["name"]
        rows.append(row)
        return rid

    def _decode(self, model: str, vals: dict[str, Any]) -> dict[str, Any]:
        out = {}
        for k, v in vals.items():
            if isinstance(v, list) and v and isinstance(v[0], list | tuple):  # commandes x2many [(6, 0, ids)]
                out[k] = list(v[0][2])
            else:
                out[k] = v
        return out

    def _is_many(self, model: str, name: str) -> bool:
        return any(
            f["model"] == model and f["name"] == name and f["ttype"] in ("many2many", "one2many")
            for f in self.dump["fields"]
        )

    def _shape(self, model: str, row: dict[str, Any], fields: list[str] | None, ctx: dict[str, Any]) -> dict:
        binaries = {f["name"] for f in self.dump["fields"] if f["model"] == model and f["ttype"] == "binary"}
        out = {"id": row["id"]}
        for k in fields or list(row):
            v = row.get(k, False)
            if k in binaries and v and ctx.get("bin_size"):
                v = f"{len(v)} bytes"  # comme Odoo : la taille au lieu du contenu
            out[k] = v
        return out

    def _fields_get(self, model: str, attributes: list[str]) -> dict[str, Any]:
        if model in _META_TABLES:
            keys = {k for r in self.dump.get(_META_TABLES[model]) or [] for k in r}
            return {k: {"type": "char"} for k in keys | {"id"}}
        extra = (self.dump.get("field_attributes") or {}).get(model, {})
        out = {}
        for f in self.dump["fields"]:
            if f["model"] == model:
                desc: dict[str, Any] = {"type": f["ttype"]}
                for a in attributes:
                    if a == "depends":
                        desc[a] = extra.get(f["name"], {}).get("depends", [])
                    elif a == "company_dependent":
                        desc[a] = extra.get(f["name"], {}).get("company_dependent", False)
                out[f["name"]] = desc
        return out

    @staticmethod
    def _filter(rows: list[dict[str, Any]], domain: list[Any]) -> list[dict[str, Any]]:
        for cond in domain:
            if isinstance(cond, list | tuple) and len(cond) == 3 and cond[1] == "=":
                key, _, value = cond
                if key == "state" and value == "installed":
                    continue  # le dump ne contient que les modules installés
                rows = [r for r in rows if r.get(key) == value]
        return rows

    @staticmethod
    def _project(
        rows: list[dict[str, Any]], fields: list[str] | None, limit: int | None
    ) -> list[dict[str, Any]]:
        out = [{k: v for k, v in r.items() if not fields or k in fields or k == "id"} for r in rows]
        return out[:limit] if limit else out


class _Common:
    def __init__(self, server: FakeOdooServer) -> None:
        self._s = server

    def version(self) -> dict[str, Any]:
        self._s.calls.append(("common", "version"))
        return self._s.dump["version_info"]

    def authenticate(self, db: str, username: str, password: str, _ctx: dict[str, Any]) -> int | bool:
        self._s.calls.append(("common", "authenticate"))
        ok = db == self._s.db and username == self._s.username and password == self._s.password
        return self._s.uid if ok else False


class _Object:
    def __init__(self, server: FakeOdooServer) -> None:
        self._s = server

    def execute_kw(self, db: str, uid: int, password: str, model: str, method: str, *rest: Any) -> Any:
        self._s._check(db, uid, password)
        args = rest[0] if rest else []
        kw = rest[1] if len(rest) > 1 else {}
        return self._s.execute_kw(model, method, args, kw)


def _ref_id(value: Any) -> Any:
    """Valeur comparable d'un champ : many2one [id, nom] → id."""
    return value[0] if isinstance(value, list | tuple) and value else value
