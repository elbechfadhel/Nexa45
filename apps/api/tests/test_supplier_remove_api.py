"""V2b.1a : un fournisseur supprime un de ses systèmes (suppression définitive, S2), tout membre (S1).

Même monde que le catalogue V2a (domaine réel de customer17, catalogue synchronisé). Le secret est retiré du
SecretStore APRÈS le commit (garanti par le Core : test_instances_core) ; ici, la transaction de test est
annulée, on vérifie donc les données en base et l'absence de tout appel au système source.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from test_catalog_api import _as, world  # noqa: F401 — fixtures partagées
from uie_core.catalog.records import CatalogProductRecord, CatalogSyncRecord
from uie_core.registry import models as m


def _count(session, model, iid) -> int:  # noqa: ANN001
    return session.scalar(select(func.count()).select_from(model).where(model.instance_id == uuid.UUID(iid)))


def test_supplier_removes_own_system_and_everything_that_belongs_to_it(world, session):  # noqa: F811
    alice, sid = _as(world, "alice@acme.io"), world["ids"]["acme"]
    assert alice.post(f"/api/systems/{sid}/catalog/sync").status_code == 200
    assert alice.get("/api/products").json()["total"] == 3
    for model in (
        m.InstanceConnection,
        m.InstanceSchemaSnapshot,
        m.FieldSemanticBinding,
        CatalogProductRecord,
    ):
        assert _count(session, model, sid) > 0
    world["store"].reads, world["readers"].opened = 0, 0

    r = alice.call("DELETE", f"/api/systems/{sid}")
    assert r.status_code == 200, r.text
    assert r.json() == {"name": "customer17", "removed": True}
    session.flush()

    assert session.get(m.Instance, uuid.UUID(sid)) is None
    for model in (
        m.InstanceConnection,
        m.InstanceSchemaSnapshot,
        m.ProductDomainSnapshotRecord,
        m.FieldSemanticBinding,
        CatalogSyncRecord,
        CatalogProductRecord,
    ):
        assert _count(session, model, sid) == 0, model.__tablename__
    # ni secret déchiffré, ni système contacté
    assert world["store"].reads == 0 and world["readers"].opened == 0
    assert sid not in [s["id"] for s in alice.get("/api/systems").json()]
    assert alice.get("/api/products").json()["total"] == 0
    assert alice.get(f"/api/systems/{sid}").status_code == 404
    assert alice.call("DELETE", f"/api/systems/{sid}").status_code == 404  # déjà supprimé


def test_another_supplier_cannot_remove_it(world, session):  # noqa: F811
    bob, sid = _as(world, "bob@globex.io"), world["ids"]["acme"]
    missing = str(uuid.uuid4())
    foreign, unknown = (
        bob.call("DELETE", f"/api/systems/{sid}"),
        bob.call("DELETE", f"/api/systems/{missing}"),
    )
    assert foreign.status_code == unknown.status_code == 404
    assert foreign.json()["detail"].replace(sid, "<id>") == unknown.json()["detail"].replace(missing, "<id>")
    session.flush()
    assert session.get(m.Instance, uuid.UUID(sid)) is not None  # intact
    assert _count(session, m.FieldSemanticBinding, sid) > 0
    # son propre système, lui, est supprimable
    own = world["ids"]["globex"]
    assert bob.call("DELETE", f"/api/systems/{own}").json() == {"name": "customer17", "removed": True}


def test_admin_without_organization_cannot_use_the_supplier_route(world):  # noqa: F811
    admin = _as(world, "admin@go45.local")
    assert admin.call("DELETE", f"/api/systems/{world['ids']['acme']}").status_code == 403
