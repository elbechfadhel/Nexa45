"""InstanceService : instances client, test de connexion, découverte de schéma, Instance Diff.

Générique : ne connaît que `SchemaDiscoveryProvider` (résolu par system + version + interface),
`SecretStore` et le Registry standard. Aucune notion de système concret ici.
Entrypoints (CLI, API) : ils appellent ce service puis commitent (`session_scope`).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import delete, event, func, select
from sqlalchemy.orm import Session, selectinload

from uie_core.db.base import utcnow
from uie_core.identity.context import Context, ensure_context, require_platform, tenant_organization
from uie_core.identity.models import Organization
from uie_core.instances.diff import DiffItem, InstanceDiffSummary, build_summary
from uie_core.instances.discovery import (
    CapabilityCheck,
    ConnectionContext,
    ConnectionErrorKind,
    ConnectionField,
    ConnectionFieldRole,
    ConnectionTestResult,
    DiscoveryError,
    DiscoveryProviderRegistry,
    ProviderResolver,
    SchemaDiscoveryProvider,
)
from uie_core.instances.endpoints import check_endpoint, private_endpoints_allowed
from uie_core.instances.secrets import SecretStore, redact
from uie_core.registry import models as m
from uie_core.registry.compare import compare_snapshots
from uie_core.registry.schema import SchemaSnapshotModel
from uie_core.registry.service import NotFound, RegistryError, RegistryService

log = logging.getLogger(__name__)


class InstanceError(RegistryError):
    pass


class InstanceCreate(BaseModel):
    """Création d'une instance. `password` est un SecretStr : repr et dump masqués."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    system: str
    version: str
    product: str | None = None
    interface: str | None = None
    environment: str = "test"
    # V1d : organisation propriétaire. TenantContext : toujours celle du contexte (jamais celle du client) ;
    # Admin / System : obligatoire.
    organization_id: uuid.UUID | None = None
    endpoint: str
    config: dict[str, Any] = Field(default_factory=dict)  # ex. base, utilisateur (jamais de secret)
    password: SecretStr | None = None


@dataclass(frozen=True)
class InstanceDeletion:
    """Compte rendu d'une suppression (aucun secret ni secret_ref)."""

    instance_id: uuid.UUID
    name: str
    connections: int
    capabilities: int
    snapshots: int
    diffs: int
    secrets: int
    base_snapshot_ids: tuple[uuid.UUID, ...]  # snapshots standard référencés : conservés, jamais modifiés


class InstanceNameTaken(InstanceError):
    """Nom d'instance déjà utilisé dans l'organisation."""


@dataclass(frozen=True)
class ConnectionCheck:
    """Test d'une connexion AVANT tout enregistrement (V2b.1) : rien n'est stocké, ni instance ni secret."""

    result: ConnectionTestResult
    system: str
    system_name: str | None  # libellé du Registry (« Odoo »)
    version: str | None  # version détectée sur le serveur (« 17 »)
    supported: bool  # version connue du Registry : on peut enregistrer le système


@dataclass(frozen=True)
class ConnectorInfo:
    """Système connectable : son formulaire vient du connecteur, ses versions du Registry."""

    system: str
    name: str
    versions: list[str]
    fields: tuple[ConnectionField, ...]


@dataclass(frozen=True)
class SecretReplacement:
    """Remplacement du secret d'une instance : test de la NOUVELLE valeur, même compte distant, activation."""

    result: ConnectionTestResult
    account_checked: bool  # le connecteur identifie le compte distant et il est inchangé
    activated: bool  # False : vérification seulement, rien n'a été enregistré


class SecretReplacementFailed(InstanceError):
    """Nouvelle valeur refusée : l'ancienne reste active, rien n'est enregistré."""


class ConnectionFailed(InstanceError):
    """Le test de connexion précédant l'enregistrement a échoué, ou la version n'est pas prise en charge."""

    def __init__(self, check: ConnectionCheck) -> None:
        super().__init__(check.result.error or "connexion impossible")
        self.check = check


@dataclass(frozen=True)
class InstanceDiffResult:
    instance: m.Instance
    base_snapshot: m.SchemaSnapshot
    instance_snapshot: m.InstanceSchemaSnapshot
    summary: InstanceDiffSummary
    changes: list[DiffItem]


class InstanceService:
    def __init__(
        self,
        session: Session,
        ctx: Context,
        secrets: SecretStore,
        providers: ProviderResolver | None = None,
    ) -> None:
        self.s = session
        self.ctx = ensure_context(ctx)  # V1d : obligatoire, toute lecture d'instance est filtrée par lui
        self.secrets = secrets
        self.providers = providers or ProviderResolver()
        self.registry = RegistryService(session, self.ctx)

    # ------------------------------------------------------------------ #
    # CRUD
    # ------------------------------------------------------------------ #
    def create(self, data: InstanceCreate) -> m.Instance:
        tenant = tenant_organization(self.ctx)
        if tenant is not None:  # adresse saisie par un fournisseur (E3) ; l'Admin n'est pas concerné
            check_endpoint(data.endpoint, allow_private=private_endpoints_allowed())
        if tenant is not None:
            if data.organization_id not in (None, tenant):  # jamais l'organisation envoyée par le client
                raise NotFound(f"Organisation {data.organization_id} introuvable")
            org_id = tenant
        elif data.organization_id is None:
            raise InstanceError("organization_id obligatoire (contexte admin ou système)")
        else:
            org_id = data.organization_id
        if self.s.get(Organization, org_id) is None:
            raise InstanceError(f"Organisation {org_id} introuvable")
        taken = select(m.Instance).where(m.Instance.organization_id == org_id, m.Instance.name == data.name)
        if self.s.scalar(taken):
            raise InstanceNameTaken(f"Une instance nommée '{data.name}' existe déjà dans cette organisation")
        interface = self._resolve_interface(data)
        provider = self._provider_for(interface)
        inst = m.Instance(
            organization_id=org_id,
            interface_id=interface.id,
            name=data.name,
            environment=data.environment,
            status="draft",
        )
        inst.connections.append(
            m.InstanceConnection(
                connector_type=provider.connector_type,
                endpoint=data.endpoint,
                config=dict(data.config),
                secret_ref=self.secrets.put(data.password) if data.password else None,
            )
        )
        now = utcnow()
        for cap, supported in sorted(provider.declared_capabilities().items()):
            inst.capabilities.append(
                m.InstanceCapability(
                    capability=cap.value,
                    status="DECLARED" if supported else "UNAVAILABLE",
                    detail="déclarée par le connecteur, non vérifiée" if supported else "non supportée",
                    checked_at=now,
                )
            )
        self.s.add(inst)
        self.s.flush()
        log.info("instance créée id=%s name=%s system=%s", inst.id, inst.name, data.system)
        return inst

    def list(self) -> list[m.Instance]:
        return list(self.s.scalars(self._query().order_by(m.Instance.name)))

    def get(self, instance_id: uuid.UUID) -> m.Instance:
        inst = self.s.scalar(self._query().where(m.Instance.id == instance_id))
        if inst is None:
            raise NotFound(f"Instance {instance_id} introuvable")
        return inst

    def resolve(self, ref: str) -> m.Instance:
        """id (UUID) ou nom — pratique pour la CLI."""
        try:
            return self.get(uuid.UUID(ref))
        except ValueError:
            found = list(self.s.scalars(self._query().where(m.Instance.name == ref)))
            if not found:
                raise NotFound(f"Instance '{ref}' introuvable") from None
            if len(found) > 1:  # V1d : le nom n'est unique que dans une organisation
                raise InstanceError(
                    f"Nom d'instance '{ref}' ambigu ({len(found)} organisations) : utiliser son id"
                ) from None
            return found[0]

    def check_connection(
        self, system: str, endpoint: str, config: dict[str, Any], password: SecretStr | None
    ) -> ConnectionCheck:
        """Teste réellement une connexion SANS rien enregistrer et détecte la version du système (V2b.1)."""
        if tenant_organization(self.ctx) is not None:
            check_endpoint(endpoint, allow_private=private_endpoints_allowed())
        provider = self.providers.resolve(system, "", "")
        ctx = ConnectionContext(endpoint=endpoint, config=dict(config), secret=password)
        try:
            result = provider.test_connection(ctx)
        except Exception as exc:  # noqa: BLE001 — un test ne doit jamais faire planter l'entrypoint
            result = ConnectionTestResult(
                ok=False,
                error=redact(f"{type(exc).__name__}: {exc}", [password]),
                error_kind=ConnectionErrorKind.UNEXPECTED,
            )
        result = result.model_copy(update={"error": redact(result.error, [password])})
        version = provider.detected_version(result) if result.ok else None
        row = self.s.scalar(select(m.System).where(m.System.code == system))
        supported = bool(
            version
            and row
            and self.s.scalar(
                select(m.SystemVersion.id)
                .join(m.SystemProduct)
                .where(m.SystemProduct.system_id == row.id, m.SystemVersion.version == version)
                .limit(1)
            )
        )
        return ConnectionCheck(result, system, row.name if row else None, version, supported)

    def connectable_systems(self) -> list[tuple[str, str, list[str]]]:
        """Systèmes qu'un propriétaire peut connecter (V2b.1) : un provider existe ET le Registry connaît des
        versions — même critère que « supported » de check_connection. (code, nom, versions triées)."""
        out = []
        for code in DiscoveryProviderRegistry.available():
            row = self.s.scalar(select(m.System).where(m.System.code == code))
            if row is None:
                continue
            versions = self.s.scalars(
                select(m.SystemVersion.version)
                .join(m.SystemProduct)
                .where(m.SystemProduct.system_id == row.id)
            )
            ordered = sorted(
                set(versions), key=lambda v: [int(x) if x.isdigit() else x for x in v.split(".")]
            )
            if ordered:
                out.append((code, row.name, ordered))
        return out

    def connectors(self) -> list[ConnectorInfo]:
        """Systèmes connectables avec le formulaire déclaré par leur connecteur."""
        return [
            ConnectorInfo(code, name, versions, self.providers.resolve(code, "", "").connection_fields)
            for code, name, versions in self.connectable_systems()
        ]

    def connection_fields(self, instance_id: uuid.UUID) -> tuple[ConnectionField, ...]:
        """Champs de connexion du système d'une instance (pour rappeler ses valeurs NON secrètes)."""
        return self._provider_for(self.get(instance_id).interface).connection_fields

    def connection_facts(self, instance_id: uuid.UUID) -> list[tuple[str, str]]:
        """Faits sûrs du dernier test RÉUSSI (ex. société du compte), fournis par le connecteur."""
        inst = self.get(instance_id)
        conn = self._connection(inst)
        if conn.last_test_status != "OK":
            return []
        result = ConnectionTestResult(ok=True, server_info=dict(conn.last_test_info or {}))
        return self._provider_for(inst.interface).connection_summary(result)

    def check_connection_values(self, system: str, values: dict[str, SecretStr]) -> ConnectionCheck:
        """Test sans enregistrement à partir des valeurs du formulaire DÉCLARÉ par le connecteur."""
        data = self.providers.resolve(system, "", "").connection_input(values)
        return self.check_connection(system, data.endpoint, data.config, data.secret)

    def connect_values(
        self, name: str, system: str, values: dict[str, SecretStr], *, environment: str = "production"
    ) -> tuple[m.Instance, ConnectionCheck]:
        """Enregistre un système à partir des valeurs du formulaire déclaré par le connecteur."""
        data = self.providers.resolve(system, "", "").connection_input(values)
        return self.connect(name, system, data.endpoint, data.config, data.secret, environment=environment)

    def connect(
        self,
        name: str,
        system: str,
        endpoint: str,
        config: dict[str, Any],
        password: SecretStr | None,
        *,
        environment: str = "production",
    ) -> tuple[m.Instance, ConnectionCheck]:
        """Connexion d'un système par son propriétaire (V2b.1) : re-teste (le mot de passe n'est jamais gardé
        entre deux requêtes), crée l'instance avec la version DÉTECTÉE dans l'organisation du contexte, puis
        enregistre un test (statut « Connected »). Échec ou version non prise en charge → ConnectionFailed."""
        check = self.check_connection(system, endpoint, config, password)
        if not check.result.ok or not check.supported:
            raise ConnectionFailed(check)
        inst = self.create(
            InstanceCreate(
                name=name.strip(),
                system=system,
                version=check.version or "",
                environment=environment,
                endpoint=endpoint.strip(),
                config=dict(config),
                password=password,
            )
        )
        self.test_connection(inst.id)
        return self.get(inst.id), check

    def replace_secret(
        self, instance_id: uuid.UUID, values: dict[str, SecretStr], *, activate: bool = False
    ) -> SecretReplacement:
        """Remplace le SECRET d'une instance existante (ex. clé d'API) sans rien changer d'autre : même
        instance, même identifiant, mêmes liens et historique. La nouvelle valeur est testée AVANT toute
        écriture ; pour un système dont l'adresse n'identifie pas le compte, le compte distant doit être le
        même. `activate=False` : vérification seulement. Échec → `SecretReplacementFailed`, ancien secret
        conservé. Activation : nouveau secret chiffré par le SecretStore, ancien retiré APRÈS le commit."""
        inst = self.get(instance_id)  # contrôle d'appartenance AVANT tout accès au secret
        conn = self._connection(inst)
        provider = self._provider_for(inst.interface)
        secret_fields = [f.name for f in provider.connection_fields if f.role == ConnectionFieldRole.SECRET]
        if len(secret_fields) != 1 or set(values) != set(secret_fields):
            raise InstanceError(f"Seul le secret ({', '.join(secret_fields) or 'aucun'}) peut être remplacé")
        new = values[secret_fields[0]]
        if not new.get_secret_value().strip():
            raise SecretReplacementFailed("Nouvelle valeur vide")
        ctx = ConnectionContext(endpoint=conn.endpoint, config=dict(conn.config), secret=new)
        try:
            result = provider.test_connection(ctx)
        except Exception as exc:  # noqa: BLE001 — un test ne doit jamais faire planter l'entrypoint
            reason = redact(f"{type(exc).__name__}: {exc}", [new])
            raise SecretReplacementFailed(f"Test de la nouvelle valeur impossible : {reason}") from None
        result = result.model_copy(update={"error": redact(result.error, [new])})
        if not result.ok:
            raise SecretReplacementFailed(f"La nouvelle valeur est refusée : {result.error or 'échec'}")
        before = provider.account_identity(
            ConnectionTestResult(ok=True, server_info=dict(conn.last_test_info or {}))
        )
        after = provider.account_identity(result)
        if after is not None and before != after:
            raise SecretReplacementFailed("La nouvelle valeur ouvre un AUTRE compte : remplacement refusé")
        if not activate:
            return SecretReplacement(result=result, account_checked=after is not None, activated=False)
        old_ref = conn.secret_ref
        conn.secret_ref = self.secrets.put(new)
        now = utcnow()
        conn.last_test_at, conn.last_test_status, conn.last_test_error = now, "OK", None
        conn.last_test_info = result.server_info
        self._store_capabilities(inst, result.capabilities, now)
        self.s.flush()
        if old_ref and old_ref != conn.secret_ref:
            self._after_commit(lambda: self._delete_secrets([old_ref], inst.id))
        log.info("secret de l'instance %s remplacé (compte vérifié : %s)", inst.id, after is not None)
        return SecretReplacement(result=result, account_checked=after is not None, activated=True)

    def connection_context(self, instance_id: uuid.UUID) -> tuple[m.Instance, ConnectionContext]:
        """Instance et paramètres de connexion déchiffrés, pour un lecteur (V2a). Contrôle d'appartenance
        D'ABORD : une instance d'un autre tenant lève NotFound sans jamais toucher au SecretStore."""
        inst = self.get(instance_id)
        return inst, self._context(self._connection(inst))

    def assign_organization(self, instance_id: uuid.UUID, organization_id: uuid.UUID) -> m.Instance:
        """Rattache une instance existante à une organisation (Admin / System seulement). Tout ce qui dérive
        de l'instance (snapshots, domaine, décisions) la suit : ownership porté par instances seulement."""
        require_platform(self.ctx, "rattachement d'une instance à une organisation")
        inst = self.get(instance_id)
        if self.s.get(Organization, organization_id) is None:
            raise NotFound(f"Organisation {organization_id} introuvable")
        if inst.organization_id == organization_id:
            return inst
        taken = select(m.Instance.id).where(
            m.Instance.organization_id == organization_id, m.Instance.name == inst.name
        )
        if self.s.scalar(taken):
            raise InstanceError(f"Une instance nommée '{inst.name}' existe déjà dans cette organisation")
        inst.organization_id = organization_id
        self.s.flush()
        self.s.refresh(inst, ["organization"])
        log.info("instance %s rattachée à l'organisation %s", inst.id, organization_id)
        return inst

    def delete(self, instance_id: uuid.UUID) -> InstanceDeletion:
        """Supprime l'instance, sa connexion, ses capabilities, ses snapshots et leurs diffs.

        Transactionnel : une seule instruction DELETE, PostgreSQL cascade (FK ON DELETE CASCADE) ; les
        snapshots standard référencés par base_snapshot_id (FK RESTRICT, sens instance → standard) ne
        sont ni supprimés ni modifiés. Le secret est retiré du SecretStore APRÈS le commit de la
        transaction de l'entrypoint : si la suppression échoue ou est annulée, le secret reste intact.
        """
        inst = self.get(instance_id)
        snap_ids = select(m.InstanceSchemaSnapshot.id).where(m.InstanceSchemaSnapshot.instance_id == inst.id)
        report = InstanceDeletion(
            instance_id=inst.id,
            name=inst.name,
            connections=len(inst.connections),
            capabilities=len(inst.capabilities),
            snapshots=len(inst.snapshots),
            diffs=self.s.scalar(
                select(func.count())
                .select_from(m.InstanceSchemaDiff)
                .where(m.InstanceSchemaDiff.instance_snapshot_id.in_(snap_ids))
            )
            or 0,
            secrets=sum(1 for c in inst.connections if c.secret_ref),
            base_snapshot_ids=tuple(sorted({s.base_snapshot_id for s in inst.snapshots}, key=str)),
        )
        refs = [c.secret_ref for c in inst.connections if c.secret_ref]
        self.s.execute(delete(m.Instance).where(m.Instance.id == inst.id))
        self.s.expunge(inst)  # objets désormais sans ligne en base
        for obj in [*inst.connections, *inst.capabilities, *inst.snapshots]:
            if obj in self.s:
                self.s.expunge(obj)
        self._after_commit(lambda: self._delete_secrets(refs, inst.id))
        log.info(
            "instance supprimée id=%s name=%s snapshots=%s diffs=%s",
            report.instance_id,
            report.name,
            report.snapshots,
            report.diffs,
        )
        return report

    def _after_commit(self, action: Callable[[], None]) -> None:
        """Exécute `action` au commit de la transaction courante ; l'oublie en cas de rollback."""

        def on_commit(_session: Session) -> None:
            event.remove(self.s, "after_soft_rollback", on_rollback)
            action()

        def on_rollback(_session: Session, _previous: Any) -> None:
            event.remove(self.s, "after_commit", on_commit)

        event.listen(self.s, "after_commit", on_commit, once=True)
        event.listen(self.s, "after_soft_rollback", on_rollback, once=True)

    def _delete_secrets(self, refs: list[str], instance_id: uuid.UUID) -> None:
        for ref in refs:
            try:
                self.secrets.delete(ref)
            except Exception as exc:  # noqa: BLE001 — la base est déjà cohérente : on trace sans secret
                log.warning(
                    "secret de l'instance %s non supprimé du SecretStore : %s",
                    instance_id,
                    type(exc).__name__,
                )

    # ------------------------------------------------------------------ #
    # Connexion & capabilities
    # ------------------------------------------------------------------ #
    def test_connection(self, instance_id: uuid.UUID) -> ConnectionTestResult:
        inst = self.get(instance_id)
        conn = self._connection(inst)
        provider = self._provider_for(inst.interface)
        ctx = self._context(conn)
        try:
            result = provider.test_connection(ctx)
        except Exception as exc:  # noqa: BLE001 — un test de connexion ne doit jamais faire planter l'entrypoint
            reason = redact(f"{type(exc).__name__}: {exc}", [ctx.secret])
            result = ConnectionTestResult(
                ok=False, error=reason, capabilities=provider.capabilities_without_connection(reason or "")
            )
        # Défense en profondeur : rien de ce qui est persisté ou renvoyé ne contient le secret
        result = result.model_copy(update={"error": redact(result.error, [ctx.secret])})

        now = utcnow()
        conn.last_test_at = now
        conn.last_test_status = "OK" if result.ok else "FAILED"
        conn.last_test_error = result.error
        # échec : la cause classée reste consultable (message simple côté fournisseur)
        conn.last_test_info = (
            result.server_info
            if result.ok
            else {"error_kind": result.error_kind.value if result.error_kind else None}
        )
        self._store_capabilities(inst, result.capabilities, now)
        inst.status = "connected" if result.ok else "connection_failed"
        self.s.flush()
        log.info("test_connection instance=%s status=%s", inst.id, conn.last_test_status)
        return result

    # ------------------------------------------------------------------ #
    # Découverte de schéma + diff
    # ------------------------------------------------------------------ #
    def discover_schema(self, instance_id: uuid.UUID) -> m.InstanceSchemaSnapshot:
        """Crée TOUJOURS un nouveau snapshot d'instance (jamais de mise à jour d'un existant)."""
        inst = self.get(instance_id)
        conn = self._connection(inst)
        provider = self._provider_for(inst.interface)
        base_row = self._baseline(inst)  # figé ici : l'id EXACT est enregistré
        ctx = self._context(conn)
        try:
            model = provider.discover_schema(ctx)
        except DiscoveryError as exc:
            raise DiscoveryError(redact(str(exc), [ctx.secret]) or "échec") from None
        except Exception as exc:  # noqa: BLE001
            raise DiscoveryError(redact(f"{type(exc).__name__}: {exc}", [ctx.secret]) or "échec") from None
        self._check_identity(inst, model)

        base_model = self.registry.load_snapshot_model(base_row)
        row = self._persist_snapshot(inst, base_row, model, provider, base_model)
        log.info(
            "discover_schema instance=%s snapshot=#%s checksum=%s", inst.id, row.sequence, row.checksum[:12]
        )
        return row

    def get_schema_diff(self, instance_id: uuid.UUID, sequence: int | None = None) -> InstanceDiffResult:
        """Diff persisté (reproductible) du snapshot demandé (dernier par défaut) vs SA baseline."""
        inst = self.get(instance_id)
        snap = self.get_instance_snapshot(inst.id, sequence)
        rows = sorted(snap.diffs, key=lambda d: d.position)
        changes = [
            DiffItem(
                path=d.object_path,
                entity=d.entity,
                object_type=d.object_type,
                change_type=d.change_type,  # type: ignore[arg-type]
                origin=d.origin,  # type: ignore[arg-type]
                modules=d.classification.get("modules", []),
                reason=d.classification.get("reason"),
                details=d.details,
            )
            for d in rows
        ]
        return InstanceDiffResult(
            instance=inst,
            base_snapshot=self.registry.get_snapshot(snap.base_snapshot_id),
            instance_snapshot=snap,
            summary=InstanceDiffSummary.model_validate(snap.diff_summary),
            changes=changes,
        )

    def recompute_diff(self, instance_id: uuid.UUID, sequence: int | None = None) -> InstanceDiffSummary:
        """Recalcule le diff depuis le snapshot stocké et SA baseline exacte (preuve de reproductibilité)."""
        inst = self.get(instance_id)
        snap = self.get_instance_snapshot(inst.id, sequence)
        provider = self._provider_for(inst.interface)
        base_model = self.registry.load_snapshot_model(self.registry.get_snapshot(snap.base_snapshot_id))
        model = SchemaSnapshotModel.model_validate(snap.schema_model)
        result = compare_snapshots(base_model, model)
        return build_summary(result, provider.classify(base_model, model, result.changes))[0]

    def list_instance_snapshots(self, instance_id: uuid.UUID) -> list[m.InstanceSchemaSnapshot]:
        self.get(instance_id)  # V1d : contrôle d'appartenance avant toute donnée dérivée
        return list(
            self.s.scalars(
                select(m.InstanceSchemaSnapshot)
                .where(m.InstanceSchemaSnapshot.instance_id == instance_id)
                .order_by(m.InstanceSchemaSnapshot.sequence)
            )
        )

    def get_instance_snapshot(
        self, instance_id: uuid.UUID, sequence: int | None = None
    ) -> m.InstanceSchemaSnapshot:
        q = select(m.InstanceSchemaSnapshot).where(m.InstanceSchemaSnapshot.instance_id == instance_id)
        q = q.where(m.InstanceSchemaSnapshot.sequence == sequence) if sequence else q
        snap = self.s.scalar(
            q.options(selectinload(m.InstanceSchemaSnapshot.diffs))
            .order_by(m.InstanceSchemaSnapshot.sequence.desc())
            .limit(1)
        )
        if snap is None:
            raise NotFound(
                "Aucun snapshot d'instance : lancer discover-schema"
                if not sequence
                else f"Snapshot #{sequence} introuvable"
            )
        return snap

    # ------------------------------------------------------------------ #
    # interne
    # ------------------------------------------------------------------ #
    def _query(self):  # noqa: ANN202
        """Point UNIQUE de filtrage multi-tenant : toute lecture d'instance passe par ici (V1d). Une instance
        d'un autre tenant est introuvable, exactement comme un id inexistant."""
        q = select(m.Instance)
        org = tenant_organization(self.ctx)
        if org is not None:
            q = q.where(m.Instance.organization_id == org)
        return q.options(
            selectinload(m.Instance.interface)
            .selectinload(m.SystemInterface.version)
            .selectinload(m.SystemVersion.product)
            .selectinload(m.SystemProduct.system),
            selectinload(m.Instance.connections),
            selectinload(m.Instance.capabilities),
            selectinload(m.Instance.snapshots),
        )

    def _resolve_interface(self, data: InstanceCreate) -> m.SystemInterface:
        q = (
            select(m.SystemInterface)
            .join(m.SystemVersion)
            .join(m.SystemProduct)
            .join(m.System)
            .where(m.System.code == data.system, m.SystemVersion.version == data.version)
        )
        if data.product:
            q = q.where(m.SystemProduct.code == data.product)
        if data.interface:
            q = q.where(m.SystemInterface.code == data.interface)
        found = list(self.s.scalars(q))
        if not found:
            raise NotFound(
                f"Aucune interface standard pour {data.system} {data.version} : importer d'abord le snapshot "
                f"standard (uie registry import ...)"
            )
        if len(found) > 1:
            raise InstanceError(
                f"Interface ambiguë pour {data.system} {data.version} : préciser product/interface"
            )
        return found[0]

    def _provider_for(self, interface: m.SystemInterface) -> SchemaDiscoveryProvider:
        ver = interface.version
        return self.providers.resolve(ver.product.system.code, ver.version, interface.code)

    @staticmethod
    def _connection(inst: m.Instance) -> m.InstanceConnection:
        if not inst.connections:
            raise InstanceError(f"Instance {inst.name} sans connexion")
        return inst.connections[0]

    def _context(self, conn: m.InstanceConnection) -> ConnectionContext:
        secret = self.secrets.get(conn.secret_ref) if conn.secret_ref else None
        return ConnectionContext(endpoint=conn.endpoint, config=dict(conn.config), secret=secret)

    def _store_capabilities(self, inst: m.Instance, checks: list[CapabilityCheck], now: Any) -> None:
        by_cap = {c.capability: c for c in inst.capabilities}
        for chk in checks:
            row = by_cap.get(chk.capability.value)
            if row is None:
                row = m.InstanceCapability(
                    capability=chk.capability.value, checked_at=now, status=chk.status.value
                )
                inst.capabilities.append(row)
            row.status = chk.status.value
            row.detail = chk.detail
            row.checked_at = now

    def _baseline(self, inst: m.Instance) -> m.SchemaSnapshot:
        base = self.s.scalar(
            select(m.SchemaSnapshot)
            .where(m.SchemaSnapshot.interface_id == inst.interface_id)
            .order_by(m.SchemaSnapshot.created_at.desc())
            .limit(1)
        )
        if base is None:
            raise InstanceError("Aucun snapshot standard pour l'interface de l'instance")
        return self.registry.get_snapshot(base.id)

    @staticmethod
    def _check_identity(inst: m.Instance, model: SchemaSnapshotModel) -> None:
        ver = inst.interface.version
        expected = (ver.product.system.code, ver.version)
        if (model.system, model.version) != expected:
            raise InstanceError(
                f"L'instance répond comme {model.system} {model.version}, attendu {expected[0]} {expected[1]}"
            )

    def _persist_snapshot(
        self,
        inst: m.Instance,
        base_row: m.SchemaSnapshot,
        model: SchemaSnapshotModel,
        provider: SchemaDiscoveryProvider,
        base_model: SchemaSnapshotModel,
    ) -> m.InstanceSchemaSnapshot:
        result = compare_snapshots(
            base_model,
            model,
            source_label=RegistryService.snapshot_label(base_row),
            target_label=inst.name,
        )
        summary, items = build_summary(result, provider.classify(base_model, model, result.changes))
        sequence = (
            self.s.scalar(
                select(func.max(m.InstanceSchemaSnapshot.sequence)).where(
                    m.InstanceSchemaSnapshot.instance_id == inst.id
                )
            )
            or 0
        ) + 1
        now = utcnow()
        row = m.InstanceSchemaSnapshot(
            instance_id=inst.id,
            sequence=sequence,
            base_snapshot_id=base_row.id,
            checksum=model.checksum(),
            discovered_at=now,
            provenance={
                **model.source,
                "provider": type(provider).__name__,
                "discovered_at": now.isoformat(),
            },
            entity_count=len(model.entities),
            field_count=model.field_count,
            relation_count=sum(len(e.relations) for e in model.entities),
            schema_model=model.model_dump(mode="json"),
            diff_summary=summary.model_dump(mode="json"),
            metadata_={"base_snapshot_label": RegistryService.snapshot_label(base_row)},
        )
        for pos, it in enumerate(items):
            row.diffs.append(
                m.InstanceSchemaDiff(
                    position=pos,
                    entity=it.entity,
                    object_type=it.object_type,
                    object_path=it.path,
                    change_type=it.change_type.value,
                    origin=it.origin.value,
                    details=_jsonable(it.details),
                    classification={"modules": it.modules, "reason": it.reason},
                )
            )
        self.s.add(row)
        inst.status = "discovered"
        self.s.flush()
        self.s.expire(inst, ["snapshots"])
        return row


def _jsonable(d: dict[str, Any]) -> dict[str, Any]:
    from pydantic import TypeAdapter

    return TypeAdapter(dict[str, Any]).dump_python(d, mode="json")
