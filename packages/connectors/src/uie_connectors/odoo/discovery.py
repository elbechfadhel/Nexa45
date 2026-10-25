"""OdooSchemaDiscoveryProvider : adapter Odoo du contrat générique SchemaDiscoveryProvider.

- test de connexion réel (XML-RPC) et vérification des capabilities en LECTURE SEULE : tout appel
  passe par `ReadOnlyObjectProxy`, qui refuse toute méthode hors liste blanche (aucune écriture
  possible, même par erreur) ;
- découverte = `OdooSchemaLoader` existant (même format que le snapshot standard) ;
- classification Odoo des différences (préfixe x_, module __custom__, modules standard ou non),
  qui n'a pas sa place dans le Core.
"""

from __future__ import annotations

import re
import xmlrpc.client
from collections.abc import Callable
from typing import Any

from uie_core.instances import (
    CapabilityCheck,
    CapabilityStatus,
    ClassifiedChange,
    ConnectionContext,
    ConnectionErrorKind,
    ConnectionField,
    ConnectionFieldRole,
    ConnectionTestResult,
    DiffAnalysis,
    DiscoveryError,
    DiscoveryProviderRegistry,
    MissingModule,
    Origin,
    SchemaDiscoveryProvider,
    redact,
)
from uie_core.registry import Capability, ChangeType, SchemaLoaderError, SchemaSnapshotModel
from uie_core.registry.compare import Change

from uie_connectors.odoo.loader import OdooSchemaLoader
from uie_connectors.odoo.metadata import XmlRpcMetadataSource

# Seules méthodes ORM autorisées vers une instance client (lecture) : tout le reste est refusé
READ_ONLY_METHODS = frozenset({"search_read", "search_count", "search", "read", "fields_get"})
PROBE_MODEL = "res.partner"  # entité de base présente sur toute instance Odoo
CUSTOM_MODULE = "__custom__"


class ReadOnlyViolation(PermissionError):
    pass


class ReadOnlyObjectProxy:
    """Enveloppe /xmlrpc/2/object : n'accepte que `execute_kw` sur des méthodes de lecture."""

    def __init__(self, proxy: Any) -> None:
        self._proxy = proxy

    def execute_kw(self, db: str, uid: int, password: str, model: str, method: str, *args: Any) -> Any:
        if method not in READ_ONLY_METHODS:
            raise ReadOnlyViolation(
                f"Méthode '{method}' refusée : l'accès aux instances est en lecture seule"
            )
        return self._proxy.execute_kw(db, uid, password, model, method, *args)

    def __getattr__(self, name: str) -> Any:
        raise ReadOnlyViolation(f"Appel '{name}' refusé : seul execute_kw en lecture est autorisé")


def _default_proxy(url: str) -> Any:
    return xmlrpc.client.ServerProxy(url, allow_none=True)


@DiscoveryProviderRegistry.register
class OdooSchemaDiscoveryProvider(SchemaDiscoveryProvider):
    system_code = "odoo"
    connector_type = "odoo.xmlrpc"
    connection_fields = (
        ConnectionField(
            name="url",
            label="Server URL",
            role=ConnectionFieldRole.ENDPOINT,
            help="The address of your server, e.g. https://mycompany.odoo.com",
            placeholder="https://mycompany.odoo.com",
        ),
        ConnectionField(
            name="database",
            label="Database",
            role=ConnectionFieldRole.CONFIG,
            key="db",
            help="The Odoo database name.",
        ),
        ConnectionField(
            name="username", label="Username", role=ConnectionFieldRole.CONFIG, help="Your Odoo username."
        ),
        ConnectionField(
            name="password",
            label="Password or API key",
            role=ConnectionFieldRole.SECRET,
            help="We recommend an Odoo API key (Preferences → Account Security → New API Key): it can be "
            "revoked at any time without changing your password.",
        ),
    )

    def __init__(self, proxy_factory: Callable[[str], Any] | None = None) -> None:
        self._raw_factory = proxy_factory or _default_proxy

    def _factory(self, url: str) -> Any:
        proxy = self._raw_factory(url)
        return ReadOnlyObjectProxy(proxy) if url.rstrip("/").endswith("/object") else proxy

    # ------------------------------------------------------------------ #
    # Connexion
    # ------------------------------------------------------------------ #
    def declared_capabilities(self) -> dict[Capability, bool]:
        # L'ORM Odoo via XML-RPC sait lire et écrire ; pas de flux d'événements dans ce connecteur.
        return {c: c != Capability.EVENTS for c in Capability}

    def _source(self, ctx: ConnectionContext) -> XmlRpcMetadataSource:
        missing = [k for k in ("db", "username") if not ctx.config.get(k)]
        if missing or ctx.secret is None:
            raise DiscoveryError(
                f"Connexion Odoo incomplète : manque {missing + (['password'] if not ctx.secret else [])}"
            )
        return XmlRpcMetadataSource(
            url=ctx.endpoint,
            db=ctx.config["db"],
            username=ctx.config["username"],
            password=ctx.secret.get_secret_value(),
            proxy_factory=self._factory,
        )

    def test_connection(self, ctx: ConnectionContext) -> ConnectionTestResult:
        try:
            src = self._source(ctx)
            version = src.version_info()
            uid = src.login()
        except (DiscoveryError, PermissionError, OSError, xmlrpc.client.Error) as exc:
            reason = redact(f"{type(exc).__name__}: {exc}", [ctx.secret]) or "échec"
            return ConnectionTestResult(
                ok=False,
                error=reason,
                error_kind=_error_kind(exc),
                capabilities=self.capabilities_without_connection(reason),
            )
        info = {
            "server_version": version.get("server_version"),
            "server_serie": version.get("server_serie"),
            "protocol_version": version.get("protocol_version"),
            "db": ctx.config.get("db"),
            "username": ctx.config.get("username"),
            "uid": uid,
        }
        return ConnectionTestResult(
            ok=True, server_info=info, capabilities=self.detect_capabilities(ctx, src)
        )

    def detected_version(self, result: ConnectionTestResult) -> str | None:
        """server_serie « 17.0 » (ou « saas~17.2 ») → version du Registry « 17 »."""
        serie = str(result.server_info.get("server_serie") or result.server_info.get("server_version") or "")
        m = re.search(r"(\d+)\.", serie)
        return m.group(1) if m else None

    def detect_capabilities(
        self, ctx: ConnectionContext, src: XmlRpcMetadataSource | None = None
    ) -> list[CapabilityCheck]:
        src = src or self._source(ctx)
        state: dict[str, Any] = {}

        def read() -> str:
            rows = src.call(PROBE_MODEL, "search_read", [[]], {"fields": ["id", "display_name"], "limit": 1})
            return f"search_read {PROBE_MODEL} (limit 1) → {len(rows)} enregistrement(s)"

        def search() -> str:
            state["ids"] = src.call(PROBE_MODEL, "search", [[]], {"limit": 50})
            n = src.call(PROBE_MODEL, "search_count", [[]])
            return f"search + search_count {PROBE_MODEL} → {n} enregistrement(s)"

        def bulk_read() -> str:
            ids = state.get("ids") or src.call(PROBE_MODEL, "search", [[]], {"limit": 50})
            rows = src.call(PROBE_MODEL, "read", [ids], {"fields": ["id"]})
            return f"read de {len(rows)} enregistrement(s) {PROBE_MODEL} en un appel"

        def schema() -> str:
            fg = src.call(PROBE_MODEL, "fields_get", [], {"attributes": ["type"]})
            src.call(
                "ir.model", "search_read", [[["model", "=", PROBE_MODEL]]], {"fields": ["model"], "limit": 1}
            )
            src.call(
                "ir.model.fields",
                "search_read",
                [[["model", "=", PROBE_MODEL]]],
                {"fields": ["name"], "limit": 1},
            )
            return f"fields_get {PROBE_MODEL} ({len(fg)} champs) + lecture ir.model / ir.model.fields"

        exercised: dict[Capability, Callable[[], str]] = {
            Capability.READ: read,
            Capability.SEARCH: search,
            Capability.BULK_READ: bulk_read,
            Capability.SCHEMA_DISCOVERY: schema,
        }
        checks = []
        for cap in Capability:
            if cap in exercised:
                checks.append(self._exercise(cap, exercised[cap], ctx))
            elif self.declared_capabilities()[cap]:
                checks.append(
                    CapabilityCheck(
                        capability=cap,
                        status=CapabilityStatus.DECLARED,
                        detail="déclarée par le connecteur ; jamais exercée (aucune écriture en V1)",
                    )
                )
            else:
                checks.append(
                    CapabilityCheck(
                        capability=cap,
                        status=CapabilityStatus.UNAVAILABLE,
                        detail="non supportée par odoo.xmlrpc",
                    )
                )
        return checks

    @staticmethod
    def _exercise(cap: Capability, op: Callable[[], str], ctx: ConnectionContext) -> CapabilityCheck:
        try:
            return CapabilityCheck(capability=cap, status=CapabilityStatus.VERIFIED, detail=op())
        except xmlrpc.client.Fault as exc:  # refus serveur (droits) : l'opération n'est pas disponible
            detail = redact(
                f"refus serveur : {str(exc.faultString).strip().splitlines()[-1][:200]}", [ctx.secret]
            )
            return CapabilityCheck(capability=cap, status=CapabilityStatus.UNAVAILABLE, detail=detail)
        except Exception as exc:  # noqa: BLE001 — erreur réseau / inattendue : on ne sait pas
            detail = redact(f"{type(exc).__name__}: {exc}", [ctx.secret])
            return CapabilityCheck(capability=cap, status=CapabilityStatus.UNKNOWN, detail=detail)

    # ------------------------------------------------------------------ #
    # Découverte
    # ------------------------------------------------------------------ #
    def discover_schema(self, ctx: ConnectionContext) -> SchemaSnapshotModel:
        try:
            return OdooSchemaLoader({}, source=self._source(ctx)).build_snapshot()
        except (PermissionError, SchemaLoaderError, OSError, xmlrpc.client.Error) as exc:
            raise DiscoveryError(redact(f"{type(exc).__name__}: {exc}", [ctx.secret]) or "échec") from None

    # ------------------------------------------------------------------ #
    # Classification Odoo
    # ------------------------------------------------------------------ #
    def classify(
        self, base: SchemaSnapshotModel, instance: SchemaSnapshotModel, changes: list[Change]
    ) -> DiffAnalysis:
        std = _installed(base)
        inst = _installed(instance)
        # Un module n'est déclaré absent que si l'instance publie SA liste de modules installés
        missing = sorted(std - inst) if inst else []
        classified: dict[str, ClassifiedChange] = {}
        explained_by: dict[str, int] = {}

        # 1er passage : origine des entités ajoutées (leurs champs/relations peuvent en hériter)
        added_entities = {
            c.path: _origin(*_element(instance, c), _mods(instance, c), std)
            for c in changes
            if c.object_type == "entity" and c.change_type == ChangeType.ADDED
        }
        for c in changes:
            if c.change_type == ChangeType.UNCHANGED:
                continue
            side = base if c.change_type == ChangeType.REMOVED else instance
            name, meta = _element(side, c)
            modules = list(meta.get("modules") or [])
            cl = _origin(name, meta, modules, std)
            if c.change_type == ChangeType.ADDED and c.object_type != "entity":
                target = _relation_target(instance, c)
                cl = _added(cl, modules, std, target, added_entities.get(target or ""))
            if c.change_type == ChangeType.REMOVED:
                cl = _removed(cl, modules, std, missing, bool(inst))
                if cl.explained_by_missing:
                    explained_by[cl.explained_by_missing] = explained_by.get(cl.explained_by_missing, 0) + 1
            classified[c.path] = cl.model_copy(update={"path": c.path})

        return DiffAnalysis(
            classified=classified,
            missing_modules=[
                MissingModule(
                    name=mod,
                    evidence="installé dans le snapshot standard (installed_modules), absent de l'instance",
                    removed_elements=explained_by.get(mod, 0),
                )
                for mod in missing
            ],
            extra_modules=sorted(inst - std),
            notes={"standard_modules": len(std), "instance_modules": len(inst)},
        )


# --------------------------------------------------------------------------- #
# helpers de classification (conventions Odoo)
# --------------------------------------------------------------------------- #
def _error_kind(exc: Exception) -> ConnectionErrorKind:
    """Cause lisible d'un échec (le message technique reste pour l'Admin, jamais pour le fournisseur)."""
    if isinstance(exc, DiscoveryError):
        return ConnectionErrorKind.INCOMPLETE
    if isinstance(exc, PermissionError):  # authenticate a répondu False
        return ConnectionErrorKind.AUTH_FAILED
    if isinstance(exc, xmlrpc.client.Fault):
        text = str(exc.faultString).lower()
        if "database" in text and ("does not exist" in text or "not found" in text):
            return ConnectionErrorKind.DATABASE_NOT_FOUND
        if "access denied" in text or "authentication" in text:
            return ConnectionErrorKind.AUTH_FAILED
        return ConnectionErrorKind.UNEXPECTED
    if isinstance(exc, OSError | xmlrpc.client.ProtocolError):  # réseau, ou pas un serveur Odoo à cette URL
        return ConnectionErrorKind.UNREACHABLE
    return ConnectionErrorKind.UNEXPECTED


def _installed(snap: SchemaSnapshotModel) -> set[str]:
    mods = snap.source.get("installed_modules") or [m.technical_name for m in snap.modules]
    return set(mods)


def _element(side: SchemaSnapshotModel, c: Change) -> tuple[str, dict[str, Any]]:
    """Nom local + metadata de l'élément concerné par un changement (entité, champ ou relation)."""
    ent = side.entity(c.entity)
    if ent is None:
        return c.entity, {}
    if c.object_type == "entity":
        return ent.technical_name, ent.metadata
    if c.object_type == "field":
        fname = c.path[len(c.entity) + 1 :]
    else:  # relation « entité[champ->cible] » : classée comme son champ source
        fname = c.path[len(c.entity) + 1 :].split("->", 1)[0]
        if fname == "*":
            return ent.technical_name, ent.metadata
    fld = ent.field(fname)
    return fname, (fld.metadata if fld else {})


def _mods(side: SchemaSnapshotModel, c: Change) -> list[str]:
    return list(_element(side, c)[1].get("modules") or [])


def _relation_target(instance: SchemaSnapshotModel, c: Change) -> str | None:
    """Entité cible d'une relation ajoutée, ou d'un champ relationnel ajouté."""
    if c.object_type == "relation":
        return c.path.rsplit("->", 1)[-1].rstrip("]")
    ent = instance.entity(c.entity)
    fname = c.path[len(c.entity) + 1 :]
    rel = next((r for r in ent.relations if r.source_field == fname), None) if ent else None
    return rel.target_entity if rel else None


def _added(
    cl: ClassifiedChange,
    modules: list[str],
    std: set[str],
    target: str | None,
    target_origin: ClassifiedChange | None,
) -> ClassifiedChange:
    """Un élément AJOUTÉ par rapport à la baseline n'est pas « standard » s'il est démontrablement
    introduit par une extension : un de ses modules est hors baseline, ou il pointe vers une entité
    elle-même ajoutée par une extension. (Odoo attribue les champs hérités d'un mixin étendu — ex.
    rating_ids ajouté par `rating` — au module du modèle hôte, `account`, `stock`... : la seule règle
    « au moins un module standard » les classerait à tort STANDARD.)"""
    if cl.origin != Origin.STANDARD:
        return cl
    extra = [m for m in modules if m not in std]
    if extra:
        return cl.model_copy(
            update={
                "origin": Origin.THIRD_PARTY,
                "reason": f"introduit par module(s) hors baseline : {', '.join(extra)}",
            }
        )
    if target_origin is not None and target_origin.origin in (Origin.THIRD_PARTY, Origin.CUSTOM):
        return cl.model_copy(
            update={
                "origin": target_origin.origin,
                "reason": f"référence {target}, entité ajoutée ({target_origin.origin.value.lower()})",
            }
        )
    return cl


def _origin(name: str, meta: dict[str, Any], modules: list[str], std: set[str]) -> ClassifiedChange:
    if name.startswith("x_") or CUSTOM_MODULE in modules or meta.get("custom"):
        why = (
            "préfixe x_"
            if name.startswith("x_")
            else ("module __custom__" if CUSTOM_MODULE in modules else "créé manuellement")
        )
        return ClassifiedChange(path="", origin=Origin.CUSTOM, modules=modules, reason=why)
    if not modules:
        return ClassifiedChange(
            path="", origin=Origin.UNKNOWN, modules=[], reason="aucun module d'origine connu"
        )
    in_std = [m for m in modules if m in std]
    if in_std:
        return ClassifiedChange(
            path="", origin=Origin.STANDARD, modules=modules, reason=f"module standard {in_std[0]}"
        )
    return ClassifiedChange(
        path="",
        origin=Origin.THIRD_PARTY,
        modules=modules,
        reason=f"module(s) hors baseline : {', '.join(modules)}",
    )


def _removed(
    cl: ClassifiedChange, modules: list[str], std: set[str], missing: list[str], inst_known: bool
) -> ClassifiedChange:
    """Un élément standard absent n'est attribué à un module absent que si c'est démontré."""
    if cl.origin != Origin.STANDARD:
        return cl
    std_mods = [m for m in modules if m in std]
    if inst_known and std_mods and all(m in missing for m in std_mods):
        return cl.model_copy(
            update={
                "explained_by_missing": std_mods[0],
                "reason": f"module {std_mods[0]} non installé dans l'instance",
            }
        )
    return cl.model_copy(
        update={"origin": Origin.UNKNOWN, "reason": "élément standard absent sans module manquant démontré"}
    )
