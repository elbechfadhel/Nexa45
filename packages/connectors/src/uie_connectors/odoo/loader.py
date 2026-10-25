"""Schema Loader Odoo : ir.model / ir.model.fields → SchemaSnapshotModel.

Le schéma logique ORM est prioritaire (document SKR §6). Le mapping physique
(table / colonne PostgreSQL) est dérivé par convention Odoo quand il existe :
- model `res.partner` → table `res_partner` (sauf abstract/transient) ;
- champ stocké non relationnel-collection → colonne du même nom ;
- one2many : pas de colonne ; many2many : table de liaison (`relation_table`).
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from uie_core.registry import (
    Capability,
    DataType,
    EntityDef,
    FieldDef,
    LoaderRegistry,
    ModuleDef,
    RelationDef,
    RelationType,
    SchemaLoader,
    SchemaLoaderError,
)

from uie_connectors.odoo.metadata import (
    JsonMetadataSource,
    OdooMetadataSource,
    XmlRpcMetadataSource,
)

# ttype Odoo → type logique commun
TTYPE_MAP: dict[str, DataType] = {
    "char": DataType.STRING,
    "text": DataType.TEXT,
    "html": DataType.TEXT,
    "integer": DataType.INTEGER,
    "float": DataType.DECIMAL,
    "monetary": DataType.DECIMAL,
    "boolean": DataType.BOOLEAN,
    "date": DataType.DATE,
    "datetime": DataType.DATETIME,
    "selection": DataType.ENUM,
    "binary": DataType.BINARY,
    "json": DataType.JSON,
    "properties": DataType.JSON,
    "properties_definition": DataType.JSON,
    "many2one": DataType.REFERENCE,
    "many2one_reference": DataType.REFERENCE,
    "reference": DataType.REFERENCE,
    "one2many": DataType.COLLECTION,
    "many2many": DataType.COLLECTION,
}
RELATION_MAP: dict[str, RelationType] = {
    "many2one": RelationType.MANY_TO_ONE,
    "one2many": RelationType.ONE_TO_MANY,
    "many2many": RelationType.MANY_TO_MANY,
}
# Champs techniques présents sur tous les models : conservés mais marqués
TECHNICAL_FIELDS = {"id", "create_uid", "create_date", "write_uid", "write_date", "__last_update"}


def _m2o_name(value: Any) -> Any:
    """Odoo renvoie les many2one sous forme [id, "name"] : on garde le nom."""
    if isinstance(value, list | tuple) and len(value) == 2:
        return value[1]
    return value or None


@LoaderRegistry.register
class OdooSchemaLoader(SchemaLoader):
    """config :
    url, db, username, password  → introspection XML-RPC
    json                          → relecture d'un dump
    include_transient (bool, défaut False)
    modules (list[str] | None)    → filtre optionnel sur les modules d'origine
    """

    system_code: ClassVar[str] = "odoo"

    def __init__(
        self, config: dict[str, Any] | None = None, source: OdooMetadataSource | None = None
    ) -> None:
        super().__init__(config)
        self.source = source or self._make_source()
        self._models: list[dict[str, Any]] | None = None

    def _make_source(self) -> OdooMetadataSource:
        c = self.config
        if c.get("json"):
            return JsonMetadataSource(path=c["json"])
        missing = [k for k in ("url", "db", "username", "password") if not c.get(k)]
        if missing:
            raise SchemaLoaderError(f"Config Odoo incomplète, manque : {missing} (ou 'json')")
        return XmlRpcMetadataSource(url=c["url"], db=c["db"], username=c["username"], password=c["password"])

    # ------------------------------------------------------------------ #
    # identification
    # ------------------------------------------------------------------ #
    def identify_system(self) -> tuple[str, str]:
        return "odoo", "odoo"

    def identify_version(self) -> tuple[str, str, str, str]:
        v = self.source.version_info()
        serie = str(v.get("server_serie") or v.get("server_version") or "")
        major = re.match(r"(\d+)", serie)
        if not major:
            raise SchemaLoaderError(f"Version Odoo illisible : {v}")
        full = str(v.get("server_version", ""))
        edition = "enterprise" if "+e" in full or full.endswith("e") else "community"
        return major.group(1), edition, "orm", "orm"

    def source_description(self) -> dict[str, Any]:
        d = self.source.describe()
        d.setdefault("include_transient", bool(self.config.get("include_transient")))
        return d

    # ------------------------------------------------------------------ #
    # extraction
    # ------------------------------------------------------------------ #
    def extract_modules(self) -> list[ModuleDef]:
        return [
            ModuleDef(
                technical_name=m["name"],
                display_name=m.get("shortdesc") or None,
                metadata={
                    k: _m2o_name(m.get(k)) for k in ("installed_version", "author", "category_id") if m.get(k)
                },
            )
            for m in self.source.installed_modules()
        ]

    def extract_entities(self) -> list[EntityDef]:
        include_transient = bool(self.config.get("include_transient"))
        module_filter = set(self.config.get("modules") or [])

        selections: dict[Any, list[dict[str, Any]]] = {}
        for s in self.source.selections():
            fid = s["field_id"][0] if isinstance(s["field_id"], list | tuple) else s["field_id"]
            selections.setdefault(fid, []).append({"value": s["value"], "label": s.get("name")})

        fields_by_model: dict[str, list[dict[str, Any]]] = {}
        for f in self.source.fields():
            fields_by_model.setdefault(f["model"], []).append(f)

        # Modules par ordre de chargement (ir.model.data par id) : le 1er est le module d'origine
        load_order: dict[Any, list[str]] = {}
        for x in self.source.model_xmlids() or []:
            mods = load_order.setdefault(x["res_id"], [])
            if x["module"] not in mods:
                mods.append(x["module"])
        access = self.source.model_access()
        acl_model_ids = None if access is None else {self._m2o_id(a["model_id"]) for a in access}
        field_attrs = self.source.field_attributes() or {}

        entities: list[EntityDef] = []
        for mdl in self.source.models():
            name = mdl["model"]
            transient = bool(mdl.get("transient"))
            if transient and not include_transient:
                continue
            modules = self._ordered_modules(mdl, load_order)
            origin = modules[0] if modules else None
            if module_filter and origin not in module_filter:
                continue
            model_fields = fields_by_model.get(name, [])
            kind_inferred = False
            if transient:
                kind = "transient"
            else:
                abstract, kind_inferred = self._is_abstract(mdl, model_fields, acl_model_ids)
                kind = "abstract" if abstract else "entity"
            physical = name.replace(".", "_") if kind == "entity" else None

            fdefs: list[FieldDef] = []
            rels: list[RelationDef] = []
            for f in model_fields:
                fdef, rel = self._convert_field(
                    f, physical, selections, field_attrs.get(name, {}).get(f["name"])
                )
                fdefs.append(fdef)
                if rel:
                    rels.append(rel)

            entities.append(
                EntityDef(
                    technical_name=name,
                    display_name=mdl.get("name") or None,
                    module=origin,
                    kind=kind,
                    physical_name=physical,
                    fields=fdefs,
                    relations=rels,
                    capabilities=self._entity_capabilities(kind),
                    metadata={
                        "modules": modules,
                        "order": mdl.get("order") or None,
                        "info": (mdl.get("info") or None) if kind != "entity" else None,
                        **({"custom": True} if name.startswith("x_") or mdl.get("state") == "manual" else {}),
                        **({"kind_inferred": True} if kind_inferred else {}),
                    },
                )
            )
        return entities

    def extract_capabilities(self) -> dict[Capability, bool]:
        return {
            Capability.SCHEMA_DISCOVERY: True,
            Capability.READ: True,
            Capability.SEARCH: True,
            Capability.CREATE: True,
            Capability.UPDATE: True,
            Capability.DELETE: True,
            Capability.BULK_READ: True,
            Capability.BULK_WRITE: True,
            Capability.EVENTS: False,
        }

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _modules_list(value: Any) -> list[str]:
        if not value:
            return []
        return [x.strip() for x in str(value).split(",") if x.strip()]

    @staticmethod
    def _m2o_id(value: Any) -> Any:
        return value[0] if isinstance(value, list | tuple) else value

    @classmethod
    def _ordered_modules(cls, mdl: dict[str, Any], load_order: dict[Any, list[str]]) -> list[str]:
        """ir.model.modules est trié alphabétiquement (« account, base… » pour res.partner) :
        on le réordonne selon l'ordre de chargement quand ir.model.data est disponible."""
        listed = cls._modules_list(mdl.get("modules"))
        ordered = [m for m in load_order.get(mdl.get("id"), []) if not listed or m in listed]
        return ordered + [m for m in listed if m not in ordered]

    @staticmethod
    def _is_abstract(
        mdl: dict[str, Any], fields: list[dict[str, Any]], acl_model_ids: set[Any] | None
    ) -> tuple[bool, bool]:
        """→ (abstract, inféré ?). Odoo ≥ 19 expose ir.model.abstract. Avant : un AbstractModel
        n'a ni champs de log (write_date) ni ACL (obligatoires pour tout model avec table depuis
        Odoo 14). Sur Odoo 19 de référence : 343/346 models bien classés, aucun faux positif."""
        if "abstract" in mdl:
            return bool(mdl["abstract"]), False
        if acl_model_ids is not None:
            has_log = any(f["name"] == "write_date" for f in fields)
            return (not has_log and mdl.get("id") not in acl_model_ids), True
        name = mdl["model"]
        return name.endswith(".mixin") or name in {"base", "mail.thread", "mail.activity.mixin"}, True

    @staticmethod
    def _entity_capabilities(kind: str) -> dict[Capability, bool]:
        if kind != "entity":
            return {
                Capability.READ: kind == "transient",
                Capability.CREATE: False,
                Capability.UPDATE: False,
                Capability.DELETE: False,
            }
        return {
            Capability.READ: True,
            Capability.SEARCH: True,
            Capability.CREATE: True,
            Capability.UPDATE: True,
            Capability.DELETE: True,
        }

    def _convert_field(
        self,
        f: dict[str, Any],
        physical_table: str | None,
        selections: dict[Any, list[dict[str, Any]]],
        extra: dict[str, Any] | None = None,
    ) -> tuple[FieldDef, RelationDef | None]:
        ttype = f.get("ttype") or "unknown"
        stored = bool(f.get("store", True))
        extra = extra or {}
        # Un x2many avec domain a pour depends les champs de son domain, préfixés par son propre nom
        # (« child_ids.active ») : ce n'est pas un calcul, on ne garde que les vraies dépendances.
        depends = [d for d in extra.get("depends") or [] if not d.startswith(f["name"] + ".")]
        # company_dependent : ir.model.fields en Odoo ≥ 18, fields_get seulement en Odoo 17
        company_dependent = bool(f.get("company_dependent") or extra.get("company_dependent"))
        # ir.model.fields.compute ne contient que le code des champs manuels : un champ non stocké
        # est calculé ou related (sauf company_dependent, stocké dans ir.property en Odoo 17) ;
        # un champ stocké est calculé s'il a des depends.
        computed = (
            bool(f.get("compute"))
            or bool(f.get("related"))
            or bool(depends)
            or (not stored and not company_dependent)
        )
        # Odoo ≥ 19 : translate = mode ("standard", "html_translate"...) au lieu d'un booléen
        translate = f.get("translate")
        column: str | None = None
        if physical_table and stored and ttype not in ("one2many", "many2many"):
            column = f["name"]

        meta: dict[str, Any] = {
            k: v
            for k, v in {
                "related": f.get("related") or None,
                "compute": bool(f.get("compute")) or None,
                "index": f.get("index") or None,
                "translate": True if translate else None,
                "translate_mode": translate if isinstance(translate, str) and translate else None,
                "depends": list(depends) if depends and not f.get("related") else None,
                "company_dependent": True if company_dependent else None,
                "size": f.get("size") or None,
                "on_delete": f.get("on_delete") or None,
                "currency_field": f.get("currency_field") or None,
                "modules": self._modules_list(f.get("modules")) or None,
                "technical": True if f["name"] in TECHNICAL_FIELDS else None,
                # x_ (convention Odoo) ou state=manual (créé via UI / Studio / XML-RPC)
                "custom": True if f["name"].startswith("x_") or f.get("state") == "manual" else None,
            }.items()
            if v is not None
        }
        if ttype == "selection":
            sel = selections.get(f["id"]) if "id" in f else None
            if sel:
                meta["selection"] = sel
        if ttype == "many2many":
            meta["relation_table"] = f.get("relation_table") or None

        fdef = FieldDef(
            technical_name=f["name"],
            label=f.get("field_description") or None,
            data_type=TTYPE_MAP.get(ttype, DataType.UNKNOWN),
            native_type=ttype,
            required=bool(f.get("required")),
            readonly=bool(f.get("readonly")),
            stored=stored,
            computed=computed,
            physical_column=column,
            help=f.get("help") or None,
            metadata=meta,
        )
        rel = None
        if ttype in RELATION_MAP and f.get("relation"):
            rel = RelationDef(
                source_field=f["name"],
                target_entity=f["relation"],
                relation_type=RELATION_MAP[ttype],
                inverse_field=f.get("relation_field") or None,
                metadata={"relation_table": f.get("relation_table")} if f.get("relation_table") else {},
            )
        return fdef, rel
