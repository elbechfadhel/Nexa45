"""Écrivain de catalogue Odoo (V2d.2) : implémente le contrat `CatalogWriter` du Core.

Il sait ÉCRIRE des enregistrements Odoo, pas ce qu'ils veulent dire : entités, champs, relations et
identité lui sont donnés par le plan d'import du Core (semantic bindings). Toute la mécanique Odoo est
ici, jamais dans le Core :
- proxy d'écriture à LISTE BLANCHE (`WriteObjectProxy`) : lecture + create / write / load, jamais
  unlink ni autre méthode ; le `ReadOnlyObjectProxy` de la discovery et de la lecture est inchangé ;
- création idempotente par clé : méthode ORM `load` avec un identifiant externe
  `__uie_import__.item_<clé>` → création ET identifiant externe dans la même transaction Odoo ;
  rejouée avec la même clé, Odoo retrouve l'identifiant et met à jour au lieu de créer ;
- format d'import de `load` (texte, `champ/.id` = référence ou collection par id de base) ;
  `write` : many2one = id, many2many = commande (6, 0, ids) ;
- libellés : `complete_name` quand l'entité en a un (chemin « A / B », chaîne de parents créée au
  besoin), sinon `name` ; comparaison sans casse ; plusieurs correspondances = AMBIGUOUS ;
- erreurs XML-RPC / réseau → `CatalogWriteError` classée, message sans secret.
"""

from __future__ import annotations

import xmlrpc.client
from collections.abc import Callable
from typing import Any

from uie_core.imports import (
    CatalogTarget,
    CatalogWriteError,
    CatalogWriter,
    CatalogWriterRegistry,
    LabelResolution,
    LabelStatus,
    RecordId,
    RecordIds,
    WriteErrorKind,
    WriteValue,
)
from uie_core.instances import ConnectionContext, redact

from uie_connectors.odoo.discovery import READ_ONLY_METHODS, ReadOnlyViolation, _default_proxy
from uie_connectors.odoo.metadata import XmlRpcMetadataSource

WRITE_METHODS = READ_ONLY_METHODS | {"create", "write", "load"}  # jamais unlink
XMLID_MODULE = "__uie_import__"
PATH_SEPARATOR = " / "


class WriteObjectProxy:
    """Enveloppe /xmlrpc/2/object pour l'import : lecture + create / write / load, rien d'autre."""

    def __init__(self, proxy: Any) -> None:
        self._proxy = proxy

    def execute_kw(self, db: str, uid: int, password: str, model: str, method: str, *args: Any) -> Any:
        if method not in WRITE_METHODS:
            raise ReadOnlyViolation(f"Méthode '{method}' refusée pour l'import")
        return self._proxy.execute_kw(db, uid, password, model, method, *args)

    def __getattr__(self, name: str) -> Any:
        raise ReadOnlyViolation(f"Appel '{name}' refusé : seul execute_kw est autorisé")


@CatalogWriterRegistry.register
class OdooCatalogWriter(CatalogWriter):
    system_code = "odoo"

    def __init__(self, proxy_factory: Callable[[str], Any] | None = None) -> None:
        self._raw_factory = proxy_factory or _default_proxy

    def _factory(self, url: str) -> Any:
        proxy = self._raw_factory(url)
        return WriteObjectProxy(proxy) if url.rstrip("/").endswith("/object") else proxy

    def open(self, ctx: ConnectionContext) -> CatalogTarget:
        secret = ctx.secret.get_secret_value() if ctx.secret else None
        if not ctx.config.get("db") or not ctx.config.get("username") or secret is None:
            raise CatalogWriteError(WriteErrorKind.UNEXPECTED, "Connexion Odoo incomplète")
        source = XmlRpcMetadataSource(
            url=ctx.endpoint,
            db=ctx.config["db"],
            username=ctx.config["username"],
            password=secret,
            proxy_factory=self._factory,
        )
        target = OdooCatalogTarget(source, secret)
        target.guard(source.login)
        return target


class OdooCatalogTarget(CatalogTarget):
    def __init__(self, source: XmlRpcMetadataSource, secret: str | None) -> None:
        self._src = source
        self._secret = secret
        self._fields: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------ #
    def find_by(
        self, entity: str, field: str, values: list[str], *, include_archived: bool = True
    ) -> dict[str, list[str]]:
        if not values:
            return {}
        rows = self._call(
            entity,
            "search_read",
            [[[field, "in", list(values)]]],
            {"fields": [field], "context": {"active_test": False} if include_archived else {}},
        )
        out: dict[str, list[str]] = {}
        for r in rows:
            out.setdefault(str(r.get(field)), []).append(str(r["id"]))
        return out

    def resolve_labels(
        self, entity: str, labels: list[str], *, create_missing: bool
    ) -> list[LabelResolution]:
        fields = self._fields_of(entity)
        by_path = "complete_name" in fields and "parent_id" in fields
        out: list[LabelResolution] = []
        for label in labels:
            ids = self._match(entity, "complete_name" if by_path else "name", label)
            if not ids and by_path and PATH_SEPARATOR not in label:
                ids = self._match(entity, "name", label)  # « Desk » désigne « All / Desk » s'il est le seul
            if len(ids) > 1:
                out.append(LabelResolution(label=label, status=LabelStatus.AMBIGUOUS))
            elif ids:
                out.append(LabelResolution(label=label, status=LabelStatus.FOUND, id=str(ids[0])))
            elif not create_missing:
                out.append(LabelResolution(label=label, status=LabelStatus.NOT_FOUND))
            else:
                created = (
                    self._create_path(entity, label) if by_path else self._create(entity, {"name": label})
                )
                out.append(LabelResolution(label=label, status=LabelStatus.CREATED, id=str(created)))
        return out

    def create_with_key(self, entity: str, key: str, values: dict[str, WriteValue]) -> str:
        names = ["id"]
        line: list[str] = [f"{XMLID_MODULE}.item_{key}"]
        for name, value in values.items():
            if isinstance(value, RecordId):
                names.append(f"{name}/.id")
                line.append(value.id)
            elif isinstance(value, RecordIds):
                names.append(f"{name}/.id")
                line.append(",".join(value.ids))
            elif isinstance(value, bool):
                names.append(name)
                line.append("1" if value else "0")
            else:
                names.append(name)
                line.append(str(value))
        result = self._call(entity, "load", [names, [line]], {})
        ids = result.get("ids") if isinstance(result, dict) else None
        if not ids:
            messages = result.get("messages", []) if isinstance(result, dict) else []
            text = "; ".join(str(m.get("message", "")) for m in messages if isinstance(m, dict)) or "refusé"
            raise CatalogWriteError(WriteErrorKind.VALIDATION, self._clean(text))
        return str(ids[0])

    def update(self, entity: str, record_id: str, values: dict[str, WriteValue]) -> None:
        vals: dict[str, Any] = {}
        for name, value in values.items():
            if isinstance(value, RecordId):
                vals[name] = int(value.id)
            elif isinstance(value, RecordIds):
                vals[name] = [(6, 0, [int(i) for i in value.ids])]
            else:
                vals[name] = value
        if vals:
            self._call(entity, "write", [[int(record_id)], vals], {})

    # ------------------------------------------------------------------ #
    def _match(self, entity: str, field: str, label: str) -> list[int]:
        rows = self._call(
            entity,
            "search_read",
            [[[field, "ilike", label]]],
            {"fields": [field], "context": {"active_test": False}},
        )
        wanted = label.strip().casefold()
        return [r["id"] for r in rows if str(r.get(field) or "").strip().casefold() == wanted]

    def _create_path(self, entity: str, label: str) -> int:
        """« A / B » : chaque niveau retrouvé (sans casse) ou créé sous le précédent."""
        parent: int | None = None
        parts = [p.strip() for p in label.split(PATH_SEPARATOR.strip()) if p.strip()]
        rid = 0
        for i, part in enumerate(parts):
            path = PATH_SEPARATOR.join(parts[: i + 1])
            ids = self._match(entity, "complete_name", path)
            if len(ids) > 1:
                raise CatalogWriteError(WriteErrorKind.VALIDATION, f"Libellé ambigu : {path}")
            rid = ids[0] if ids else self._create(entity, {"name": part, "parent_id": parent or False})
            parent = rid
        return rid

    def _create(self, entity: str, vals: dict[str, Any]) -> int:
        return int(self._call(entity, "create", [vals], {}))

    def _fields_of(self, entity: str) -> dict[str, Any]:
        if entity not in self._fields:
            self._fields[entity] = self._call(entity, "fields_get", [], {"attributes": ["type"]})
        return self._fields[entity]

    def guard(self, op: Callable[[], Any]) -> Any:
        try:
            return op()
        except ReadOnlyViolation as exc:  # avant PermissionError (sous-classe) : méthode hors liste blanche
            raise CatalogWriteError(WriteErrorKind.UNEXPECTED, str(exc)) from None
        except PermissionError as exc:
            raise CatalogWriteError(WriteErrorKind.RIGHTS, self._clean(str(exc))) from None
        except xmlrpc.client.Fault as exc:
            raise CatalogWriteError(
                _fault_kind(exc.faultString), self._clean(_last_line(exc.faultString))
            ) from None
        except (TimeoutError, OSError, xmlrpc.client.ProtocolError) as exc:
            raise CatalogWriteError(WriteErrorKind.CONNECTION, self._clean(type(exc).__name__)) from None

    def _call(self, entity: str, method: str, args: list[Any], kw: dict[str, Any]) -> Any:
        return self.guard(lambda: self._src.call(entity, method, args, kw))

    def _clean(self, text: str) -> str:
        return (redact(text, [self._secret]) or "")[:500]


def _fault_kind(text: str) -> WriteErrorKind:
    if "AccessError" in text or "AccessDenied" in text or "Access Denied" in text:
        return WriteErrorKind.RIGHTS
    if "ValidationError" in text or "UserError" in text or "ValueError" in text:
        return WriteErrorKind.VALIDATION
    return WriteErrorKind.UNEXPECTED


def _last_line(text: str) -> str:
    lines = [ln.strip() for ln in (text or "").strip().splitlines() if ln.strip()]
    return lines[-1] if lines else "erreur"
