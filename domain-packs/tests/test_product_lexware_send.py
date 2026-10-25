"""Articles Lexware Office, phase 1b — ENVOI contrôlé des articles préparés (faux serveur, aucun réseau).

Même monde que l'aperçu 1a (domaine réel de customer17, mini catalogue INVENTÉ servi par un faux Odoo, cible
servie par un faux Lexware) ; même mécanisme générique que les partenaires (`DeliveryService`) : création
(POST, `version: 0`), mise à jour (PUT avec la version relue, rien d'effacé), doublon possible par numéro
d'article (SKU) → décision humaine, D11 par instance cible, issue inconnue jamais retentée."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import select
from test_partner_lexware_send import FakeSenders
from test_product_lexware_preview import _answer, _counts, _ids, world  # noqa: F401  (fixture partagée)
from uie_connectors.lexware.testing import FakeLexwareServer
from uie_core.delivery import DeliveryConflict, TargetResourceLink, TargetSendItem
from uie_core.exports import DocumentExportState, ExportDocumentRecord
from uie_core.products import ProductTargetError, ProductTargetService
from uie_core.registry import NotFound

CHAIR_RED, LAMP = "CHAIR-01-RED", "LAMP-01"


@pytest.fixture
def lw(world):  # noqa: F811
    _answer(world)
    source, target = _ids(world)
    world["lexware"].writable = True
    senders = FakeSenders(world["lexware"])
    svc = ProductTargetService(world["session"], world["acme"].products, senders=senders)
    svc.set_price_basis(source, target, "net")
    svc.set_tax_rate(source, target, "1", "19", label="19% Umsatzsteuer")
    refs = {r.prepared.sku: r.prepared.source_ref for r in svc.preview(source, target).records}
    return {**world, "svc": svc, "senders": senders, "S": source, "T": target, "refs": refs}


def _writes(server: FakeLexwareServer, method: str) -> list[dict[str, Any]]:
    return [json.loads(c.body) for c in server.calls if c.method == method and "/articles" in c.path]


def _items(view) -> dict[str, TargetSendItem]:  # noqa: ANN001
    return {i.source_ref: i for i in view.items}


def _key() -> str:
    return uuid.uuid4().hex


def _send(lw: dict[str, Any], *skus: str, **kw: Any):  # noqa: ANN202
    refs = [lw["refs"][s] for s in skus]
    return lw["svc"].send(lw["S"], lw["T"], refs=refs, request_key=kw.pop("key", _key()), confirm=True, **kw)


def _preview(lw: dict[str, Any]) -> dict[str, Any]:
    return {r.prepared.sku: r for r in lw["svc"].preview(lw["S"], lw["T"]).records}


# --------------------------------------------------------------------------- #
def test_send_needs_explicit_confirmation_and_writes_nothing_otherwise(lw):
    before = _counts(lw["session"])
    with pytest.raises(ProductTargetError, match="Confirm"):
        lw["svc"].send(lw["S"], lw["T"], refs=[lw["refs"][LAMP]], request_key=_key(), confirm=False)
    assert lw["lexware"].writes == [] and _counts(lw["session"]) == before


def test_first_send_posts_version_zero_links_and_records_d11(lw):
    view = _send(lw, LAMP, CHAIR_RED)
    assert view.run.status == "COMPLETED"
    assert {i.status for i in view.items} == {"SENT"} and {i.operation for i in view.items} == {"CREATE"}
    posts = _writes(lw["lexware"], "POST")
    assert len(posts) == 2 and all(p["version"] == 0 for p in posts)
    lamp = next(p for p in posts if p["articleNumber"] == LAMP)
    assert lamp["price"] == {"leadingPrice": "NET", "netPrice": 35.5, "taxRate": 19.0}
    assert view.names[lw["refs"][LAMP]] == "Desk Lamp"  # titre lu dans la source, jamais stocké
    s = lw["session"]
    links = s.scalars(select(TargetResourceLink)).all()
    assert {(lk.domain, lk.resource, lk.origin, lk.target_version) for lk in links} == {
        ("product", "articles", "SENT", 1)
    }
    records = s.scalars(select(ExportDocumentRecord)).all()
    assert len(records) == 2 and all(r.target_instance_id == lw["T"] and r.send_item_id for r in records)
    after = _preview(lw)
    assert after[LAMP].export_state == DocumentExportState.ALREADY_EXPORTED and after[LAMP].linked
    assert not after[LAMP].sendable and after["CHAIR-01-BLK"].sendable


def test_same_request_key_never_sends_twice_and_unchanged_articles_are_skipped(lw):
    key = _key()
    first = _send(lw, LAMP, key=key)
    calls = len(lw["lexware"].calls)
    again = _send(lw, LAMP, key=key)
    assert again.run.id == first.run.id and len(lw["lexware"].calls) == calls  # rejeu : 0 appel
    other = _send(lw, LAMP)
    assert {i.reason for i in other.items} == {"UNCHANGED"} and len(lw["lexware"].calls) == calls


def test_blocked_article_is_never_sent(lw):
    source, target = lw["S"], lw["T"]
    lw["svc"].clear_tax_rate(source, target, "1")  # taux à décider : tout redevient bloqué
    view = _send(lw, LAMP)
    assert [(i.status, i.reason) for i in view.items] == [("SKIPPED", "BLOCKED")]
    assert lw["lexware"].writes == []


def test_changed_product_is_updated_with_put_and_version_without_erasing_target_data(lw):
    _send(lw, LAMP)
    article = lw["lexware"].articles[0]
    article["note"] = "Interne Notiz in Lexware"  # donnée de la cible que la source ne gère pas
    for row in lw["odoo"].records["product.template"]:
        if row.get("default_code") == LAMP:
            row["list_price"] = 39.9
    for row in lw["odoo"].records["product.product"]:
        if row.get("default_code") == LAMP:
            row["lst_price"] = 39.9
    changed = _preview(lw)[LAMP]
    assert changed.export_state == DocumentExportState.CHANGED_SINCE_EXPORT and changed.sendable
    view = _send(lw, LAMP)
    assert [(i.status, i.operation) for i in view.items] == [("SENT", "UPDATE")]
    put = _writes(lw["lexware"], "PUT")[0]
    assert put["version"] == 1 and put["price"] == {"leadingPrice": "NET", "netPrice": 39.9, "taxRate": 19.0}
    assert put["note"] == "Interne Notiz in Lexware"  # rien d'effacé
    assert not {"id", "organizationId", "createdDate", "updatedDate"} & set(put)
    assert article["version"] == 2 and len(_writes(lw["lexware"], "POST")) == 1  # jamais de second article


def test_article_changed_in_lexware_since_the_last_send_is_a_conflict(lw):
    _send(lw, LAMP)
    lw["lexware"].articles[0]["version"] = 7  # modifié dans Lexware depuis l'envoi
    for row in lw["odoo"].records["product.template"]:
        if row.get("default_code") == LAMP:
            row["name"] = "Desk Lamp Pro"
    view = _send(lw, LAMP)
    assert [(i.status, i.reason) for i in view.items] == [("FAILED", "CONFLICT")]
    assert _writes(lw["lexware"], "PUT") == []


def test_existing_article_with_the_same_sku_needs_a_human_decision(lw):
    lw["lexware"].articles.append(
        {"id": "a-existing", "articleNumber": LAMP, "title": "Lampe (Altbestand)", "version": 3}
    )
    view = _send(lw, LAMP)
    item = _items(view)[lw["refs"][LAMP]]
    assert (item.status, item.reason, item.candidates) == (
        "NEEDS_MATCH",
        "POSSIBLE_DUPLICATE",
        ["a-existing"],
    )
    assert lw["lexware"].writes == []  # rien créé tant qu'un humain n'a pas décidé
    found = lw["svc"].matches(lw["S"], lw["T"], lw["refs"][LAMP])
    assert [(f.id, f.label) for f in found] == [("a-existing", "LAMP-01 – Lampe (Altbestand)")]

    lw["svc"].link(lw["S"], lw["T"], lw["refs"][LAMP], "a-existing")  # « c'est le même »
    assert _preview(lw)[LAMP].linked_by_match
    view = _send(lw, LAMP)
    assert [(i.status, i.operation) for i in view.items] == [("SENT", "UPDATE")]
    assert _writes(lw["lexware"], "POST") == [] and _writes(lw["lexware"], "PUT")[0]["version"] == 3


def test_create_a_new_article_anyway_is_an_explicit_choice(lw):
    lw["lexware"].articles.append({"id": "a-existing", "articleNumber": LAMP, "title": "Alt", "version": 1})
    ref = lw["refs"][LAMP]
    view = _send(lw, LAMP, create_new=[ref])
    assert [(i.status, i.operation) for i in view.items] == [("SENT", "CREATE")]
    assert len(_writes(lw["lexware"], "POST")) == 1
    with pytest.raises(DeliveryConflict):  # un lien existe déjà : plus de rapprochement possible
        lw["svc"].link(lw["S"], lw["T"], ref, "a-existing")


@pytest.mark.parametrize("status", [504, 500])
def test_unknown_outcome_is_never_retried_and_blocks_the_article_until_resolved(lw, status):
    lw["lexware"].fail[("POST", "/v1/articles")] = status
    view = _send(lw, LAMP)
    assert [(i.status, i.reason) for i in view.items] == [("UNKNOWN", "UNKNOWN_OUTCOME")]
    assert len(_writes(lw["lexware"], "POST")) == 1  # une seule tentative, jamais de retry
    assert _preview(lw)[LAMP].outcome_unknown and not _preview(lw)[LAMP].sendable
    del lw["lexware"].fail[("POST", "/v1/articles")]
    again = _send(lw, LAMP)
    assert [i.reason for i in again.items] == ["OUTCOME_UNKNOWN"] and len(_writes(lw["lexware"], "POST")) == 1
    lw["svc"].resolve_unknown(lw["S"], lw["T"], view.items[0].id, created=False, remote_id=None)
    assert _preview(lw)[LAMP].sendable  # vérifié « non créé » : renvoyable, par une nouvelle confirmation
    assert [i.status for i in _send(lw, LAMP).items] == ["SENT"]


def test_unknown_outcome_resolved_as_created_links_without_writing(lw):
    lw["lexware"].fail[("POST", "/v1/articles")] = 504
    view = _send(lw, LAMP)
    lw["lexware"].articles.append(
        {"id": "a-found", "articleNumber": LAMP, "title": "Desk Lamp", "version": 1}
    )
    writes = len(lw["lexware"].writes)
    lw["svc"].resolve_unknown(lw["S"], lw["T"], view.items[0].id, created=True, remote_id="a-found")
    assert len(lw["lexware"].writes) == writes  # aucune écriture : on enregistre ce qui existe
    after = _preview(lw)[LAMP]
    assert after.linked and after.export_state == DocumentExportState.ALREADY_EXPORTED


def test_rejected_and_access_refused(lw):
    lw["lexware"].fail[("POST", "/v1/articles")] = 406
    view = _send(lw, LAMP)
    assert [(i.status, i.reason) for i in view.items] == [("FAILED", "REJECTED")]
    lw["lexware"].fail[("POST", "/v1/articles")] = 403
    view = _send(lw, LAMP, CHAIR_RED)
    statuses = sorted((i.status, i.reason) for i in view.items)
    assert statuses == [("FAILED", "ACCESS"), ("FAILED", "NOT_ATTEMPTED")]  # l'envoi s'arrête


def test_another_organization_cannot_send_or_read_runs(lw):
    other = ProductTargetService(lw["session"], lw["globex"].products, senders=lw["senders"])
    with pytest.raises(NotFound):
        other.send(lw["S"], lw["T"], refs=[lw["refs"][LAMP]], request_key=_key(), confirm=True)
    with pytest.raises(NotFound):
        other.runs(lw["S"], lw["T"])
    assert lw["lexware"].writes == [] and lw["senders"].created == 0
