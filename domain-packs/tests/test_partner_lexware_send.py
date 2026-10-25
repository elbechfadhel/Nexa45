"""Phase 1b Lexware Office — ENVOI contrôlé de partenaires (faux serveur, aucun réseau).

Même monde que l'aperçu (source IMAGINAIRE « acme-erp », aucun connecteur de source) : création (version 0),
mise à jour sans rien effacer (version), D11 par instance cible, rapprochement manuel, issue inconnue jamais
retentée, arrêt sur refus d'accès, reprise d'un envoi interrompu, isolation des organisations."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from test_partner_lexware_preview import _counts, world  # noqa: F401  (fixture partagée)
from uie_connectors.lexware import LexwareClient, LexwareRecordSender
from uie_connectors.lexware.client import ReadOnlyViolation
from uie_connectors.lexware.ratelimit import TokenBucket
from uie_connectors.lexware.testing import FAKE_KEY, FakeLexwareServer
from uie_core.delivery import (
    DeliveryConflict,
    DeliveryError,
    RecordSenderResolver,
    TargetResourceLink,
    TargetSendItem,
    TargetSendRun,
)
from uie_core.exports import DocumentExportState, ExportDocumentRecord
from uie_core.instances import ConnectionContext
from uie_core.partners import PartnerTargetError, PartnerTargetService
from uie_core.registry import NotFound

READY = ["SAP-BP/0000104711", "B-1"]  # deux sociétés prêtes du monde de test
BLOCKED = "customers.csv:row-9"


class FakeSenders(RecordSenderResolver):
    def __init__(self, server: FakeLexwareServer, interrupt_on: int | None = None) -> None:
        self.server = server
        self.created = 0
        self.interrupt_on = interrupt_on

    def supports(self, system: str, interface: str) -> bool:
        return system == "lexware"

    def create(self, system: str, interface: str, ctx: ConnectionContext) -> LexwareRecordSender:
        assert ctx.secret is not None
        self.created += 1
        noop = TokenBucket(clock=lambda: 0.0, sleep=lambda _s: None)
        client = LexwareClient(
            ctx.secret, transport=self.server.transport, bucket=noop, sleep=lambda _s: None, writable=True
        )
        sender = LexwareRecordSender(ctx, client=client)
        if self.interrupt_on is not None:
            original, calls = sender.create, {"n": 0}

            def create(resource: str, body: dict[str, Any]):  # noqa: ANN202
                calls["n"] += 1
                if calls["n"] == self.interrupt_on:
                    original(resource, body)  # écrit, puis le processus « meurt » avant d'enregistrer
                    raise KeyboardInterrupt
                return original(resource, body)

            sender.create = create  # type: ignore[method-assign]
        return sender


@pytest.fixture
def lw(world):  # noqa: F811
    a = world["acme"]
    world["server"].writable = True
    senders = FakeSenders(world["server"])
    svc = PartnerTargetService(world["session"], a["svc"].ctx, a["src"], senders=senders)
    return {**world, "a": a, "svc": svc, "senders": senders, "S": a["source"].id, "T": a["target"].id}


def _posts(server: FakeLexwareServer) -> list[dict[str, Any]]:
    return [json.loads(c.body) for c in server.calls if c.method == "POST"]


def _items(view) -> dict[str, TargetSendItem]:  # noqa: ANN001
    return {i.source_ref: i for i in view.items}


def _key() -> str:
    return uuid.uuid4().hex


# --------------------------------------------------------------------------- #
# Création, confirmation, D11
# --------------------------------------------------------------------------- #
def test_send_needs_explicit_confirmation_and_writes_nothing_otherwise(lw):
    before = _counts(lw["session"])
    with pytest.raises(PartnerTargetError):
        lw["svc"].send(lw["S"], lw["T"], refs=READY, request_key=_key(), confirm=False)
    assert lw["server"].writes == [] and _counts(lw["session"]) == before


def test_first_send_creates_with_version_zero_links_and_records_d11_per_target(lw):
    view = lw["svc"].send(lw["S"], lw["T"], refs=[*READY, BLOCKED], request_key=_key(), confirm=True)
    items = _items(view)
    assert view.run.status == "COMPLETED"
    assert [items[r].status for r in READY] == ["SENT", "SENT"] and items[BLOCKED].status == "SKIPPED"
    assert items[BLOCKED].reason == "BLOCKED"
    posts = _posts(lw["server"])
    assert len(posts) == 2 and all(p["version"] == 0 for p in posts)  # jamais le bloqué
    assert all("number" not in json.dumps(p["roles"]) for p in posts)  # numéros attribués par la cible
    s = lw["session"]
    links = s.scalars(select(TargetResourceLink)).all()
    assert {(lk.source_ref, lk.origin, lk.target_version) for lk in links} == {(r, "SENT", 1) for r in READY}
    records = s.scalars(select(ExportDocumentRecord)).all()
    assert len(records) == 2 and all(r.target_instance_id == lw["T"] and r.send_item_id for r in records)
    assert all(r.export_run_id is None and r.origin == "EXPORTED" for r in records)

    preview = {r.prepared.source_ref: r for r in lw["svc"].preview(lw["S"], lw["T"]).records}
    assert preview[READY[0]].export_state == DocumentExportState.ALREADY_EXPORTED and preview[READY[0]].linked
    assert not preview[READY[0]].sendable


def test_same_request_key_never_sends_twice_and_unchanged_records_are_skipped(lw):
    key = _key()
    first = lw["svc"].send(lw["S"], lw["T"], refs=READY, request_key=key, confirm=True)
    calls = len(lw["server"].calls)
    again = lw["svc"].send(lw["S"], lw["T"], refs=READY, request_key=key, confirm=True)
    assert again.run.id == first.run.id and len(lw["server"].calls) == calls  # rejeu = même envoi, 0 appel
    other = lw["svc"].send(lw["S"], lw["T"], refs=READY, request_key=_key(), confirm=True)
    assert {i.reason for i in other.items} == {"UNCHANGED"} and len(lw["server"].calls) == calls


def test_history_is_per_target_instance_and_datev_style_file_history_is_untouched(lw):
    lw["svc"].send(lw["S"], lw["T"], refs=READY, request_key=_key(), confirm=True)
    second, _ = lw["a"]["svc"].connect_values("books-2", "lexware", {"api_key": SecretStr(FAKE_KEY)})
    states = {r.export_state for r in lw["svc"].preview(lw["S"], second.id).records}
    assert states == {DocumentExportState.NEW}  # autre compte cible : son propre historique


# --------------------------------------------------------------------------- #
# Mise à jour (version), conflit
# --------------------------------------------------------------------------- #
def _change_source(lw, ref: str, name: str) -> None:  # noqa: ANN001
    src = lw["a"]["src"]
    partners = [
        p.model_copy(update={"organization_name": name, "name": name}) if p.source_ref == ref else p
        for p in src.result.partners
    ]
    src.result = src.result.model_copy(update={"partners": partners})


def test_changed_record_is_updated_with_the_target_version_without_erasing_unmanaged_data(lw):
    lw["svc"].send(lw["S"], lw["T"], refs=READY, request_key=_key(), confirm=True)
    link = lw["session"].scalar(select(TargetResourceLink).where(TargetResourceLink.source_ref == READY[1]))
    stored = next(c for c in lw["server"].contacts if c["id"] == link.target_id)
    stored["note"] = "added by hand in the target system"  # donnée que la source ne gère pas
    _change_source(lw, READY[1], "Alpen Handel Holding AG")
    preview = {r.prepared.source_ref: r for r in lw["svc"].preview(lw["S"], lw["T"]).records}
    assert preview[READY[1]].export_state == DocumentExportState.CHANGED_SINCE_EXPORT

    view = lw["svc"].send(lw["S"], lw["T"], refs=[READY[1]], request_key=_key(), confirm=True)
    item = _items(view)[READY[1]]
    assert item.status == "SENT" and item.operation == "UPDATE" and item.target_version == 2
    put = next(json.loads(c.body) for c in lw["server"].calls if c.method == "PUT")
    assert put["version"] == 1 and "id" not in put and "organizationId" not in put
    assert stored["company"]["name"] == "Alpen Handel Holding AG"
    assert stored["note"] == "added by hand in the target system"  # rien effacé
    assert stored["roles"]["customer"]["number"] >= 10001  # numéro de la cible conservé
    origins = [
        r.origin for r in lw["session"].scalars(select(ExportDocumentRecord)) if r.source_ref == READY[1]
    ]
    assert sorted(origins) == ["EXPORTED", "UPDATED"]  # historique ajouté, jamais modifié


def test_record_changed_in_the_target_since_last_send_is_a_conflict_nothing_written(lw):
    lw["svc"].send(lw["S"], lw["T"], refs=[READY[1]], request_key=_key(), confirm=True)
    lw["server"].contacts[0]["version"] = 5  # modifié dans la cible
    _change_source(lw, READY[1], "Alpen Handel Neu AG")
    view = lw["svc"].send(lw["S"], lw["T"], refs=[READY[1]], request_key=_key(), confirm=True)
    item = _items(view)[READY[1]]
    assert (item.status, item.reason) == ("FAILED", "CONFLICT") and view.run.status == "NOT_SENT"
    assert not any(c.method == "PUT" for c in lw["server"].calls)


# --------------------------------------------------------------------------- #
# Rapprochement : jamais de fusion automatique
# --------------------------------------------------------------------------- #
def _seed_existing(server: FakeLexwareServer, name: str) -> str:
    cid = str(uuid.uuid4())
    server.contacts.append(
        {
            "id": cid,
            "version": 3,
            "archived": False,
            "roles": {"customer": {"number": 10500}},
            "company": {"name": name.upper()},  # même nom à la casse près
            "note": "",
        }
    )
    return cid


def test_possible_duplicate_is_never_created_until_a_human_decides(lw):
    existing = _seed_existing(lw["server"], "Alpen Handel AG")
    view = lw["svc"].send(lw["S"], lw["T"], refs=READY, request_key=_key(), confirm=True)
    items = _items(view)
    assert (items["B-1"].status, items["B-1"].reason) == ("NEEDS_MATCH", "POSSIBLE_DUPLICATE")
    assert items["B-1"].candidates == [existing] and view.run.status == "PARTIAL"
    assert len(_posts(lw["server"])) == 1  # seul l'autre partenaire a été créé
    matches = lw["svc"].matches(lw["S"], lw["T"], "B-1")
    assert [(m.id, m.label) for m in matches] == [(existing, "ALPEN HANDEL AG")]

    # choix humain 1 : c'est le même → lien, puis mise à jour (jamais une seconde fiche)
    lw["svc"].link(lw["S"], lw["T"], "B-1", existing)
    assert {r.prepared.source_ref: r for r in lw["svc"].preview(lw["S"], lw["T"]).records}[
        "B-1"
    ].linked_by_match
    view = lw["svc"].send(lw["S"], lw["T"], refs=["B-1"], request_key=_key(), confirm=True)
    assert (_items(view)["B-1"].operation, _items(view)["B-1"].status) == ("UPDATE", "SENT")
    assert len(_posts(lw["server"])) == 1


def test_create_new_anyway_is_an_explicit_choice(lw):
    _seed_existing(lw["server"], "Alpen Handel AG")
    view = lw["svc"].send(
        lw["S"], lw["T"], refs=["B-1"], request_key=_key(), confirm=True, create_new=["B-1"]
    )
    assert _items(view)["B-1"].status == "SENT" and len(_posts(lw["server"])) == 1


def test_manual_link_rules(lw):
    a = _seed_existing(lw["server"], "Somebody Else GmbH")
    lw["svc"].link(lw["S"], lw["T"], "B-1", a)
    with pytest.raises(DeliveryConflict):  # une fiche cible = un seul partenaire de la source
        lw["svc"].link(lw["S"], lw["T"], READY[0], a)
    with pytest.raises(DeliveryError):  # identifiant inexistant : lu avant tout lien
        lw["svc"].link(lw["S"], lw["T"], READY[0], str(uuid.uuid4()))
    lw["svc"].unlink(lw["S"], lw["T"], "B-1")  # jamais utilisé : correction possible
    lw["svc"].link(lw["S"], lw["T"], "B-1", a)
    lw["svc"].send(lw["S"], lw["T"], refs=["B-1"], request_key=_key(), confirm=True)
    with pytest.raises(DeliveryConflict):  # utilisé par un envoi : plus retirable
        lw["svc"].unlink(lw["S"], lw["T"], "B-1")


# --------------------------------------------------------------------------- #
# Erreurs, issue inconnue, reprise
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("status", [504, 500])
def test_unknown_outcome_is_never_retried_and_blocks_the_record_until_resolved(lw, status):
    lw["server"].fail[("POST", "/v1/contacts")] = status
    view = lw["svc"].send(lw["S"], lw["T"], refs=["B-1"], request_key=_key(), confirm=True)
    item = _items(view)["B-1"]
    assert (item.status, item.reason) == ("UNKNOWN", "UNKNOWN_OUTCOME")
    assert sum(1 for c in lw["server"].calls if c.method == "POST") == 1  # aucun nouvel essai
    preview = {r.prepared.source_ref: r for r in lw["svc"].preview(lw["S"], lw["T"]).records}
    assert preview["B-1"].outcome_unknown and not preview["B-1"].sendable
    again = lw["svc"].send(lw["S"], lw["T"], refs=["B-1"], request_key=_key(), confirm=True)
    assert _items(again)["B-1"].reason == "OUTCOME_UNKNOWN"

    # vérifié par un humain : pas créé → renvoyable
    lw["svc"].resolve_unknown(lw["S"], lw["T"], item.id, created=False, remote_id=None)
    del lw["server"].fail[("POST", "/v1/contacts")]
    view = lw["svc"].send(lw["S"], lw["T"], refs=["B-1"], request_key=_key(), confirm=True)
    assert _items(view)["B-1"].status == "SENT"


def test_unknown_outcome_resolved_as_created_links_without_writing(lw):
    lw["server"].fail[("POST", "/v1/contacts")] = 504
    item = _items(lw["svc"].send(lw["S"], lw["T"], refs=["B-1"], request_key=_key(), confirm=True))["B-1"]
    cid = _seed_existing(lw["server"], "Alpen Handel AG")  # trouvé dans la cible par l'utilisateur
    writes = len(lw["server"].writes)
    lw["svc"].resolve_unknown(lw["S"], lw["T"], item.id, created=True, remote_id=cid)
    assert len(lw["server"].writes) == writes
    state = {r.prepared.source_ref: r for r in lw["svc"].preview(lw["S"], lw["T"]).records}["B-1"]
    assert state.linked and state.export_state == DocumentExportState.ALREADY_EXPORTED
    with pytest.raises(DeliveryConflict):
        lw["svc"].resolve_unknown(lw["S"], lw["T"], item.id, created=False, remote_id=None)


def test_access_refused_stops_the_send_and_leaves_the_rest_safe_to_resend(lw):
    lw["server"].fail[("POST", "/v1/contacts")] = 403
    view = lw["svc"].send(lw["S"], lw["T"], refs=READY, request_key=_key(), confirm=True)
    items = _items(view)
    assert (items[READY[0]].status, items[READY[0]].reason) == ("FAILED", "ACCESS")
    assert (items[READY[1]].status, items[READY[1]].reason) == ("FAILED", "NOT_ATTEMPTED")
    assert sum(1 for c in lw["server"].calls if c.method == "POST") == 1


def test_rejected_record_keeps_field_names_only_and_others_continue(lw):
    lw["server"].queue = []
    original = lw["server"]._write

    def refuse_first(request):  # noqa: ANN001, ANN202
        lw["server"]._write = original
        import httpx

        return httpx.Response(406, json={"IssueList": [{"source": "company.name"}], "message": "secret?"})

    lw["server"]._write = refuse_first
    view = lw["svc"].send(lw["S"], lw["T"], refs=READY, request_key=_key(), confirm=True)
    items = _items(view)
    assert (items[READY[0]].status, items[READY[0]].reason) == ("FAILED", "REJECTED")
    assert items[READY[0]].error_fields == ["company.name"] and items[READY[1]].status == "SENT"
    assert view.run.status == "PARTIAL"


def test_interrupted_send_resumes_without_rewriting_a_started_record(lw):
    lw["svc"].senders = FakeSenders(lw["server"], interrupt_on=1)
    key = _key()
    with pytest.raises(KeyboardInterrupt):
        lw["svc"].send(lw["S"], lw["T"], refs=READY, request_key=key, confirm=True)
    run = lw["session"].scalar(select(TargetSendRun).where(TargetSendRun.request_key == key))
    assert run.status == "RUNNING"
    with pytest.raises(DeliveryConflict):  # un seul envoi en cours
        lw["svc"].send(lw["S"], lw["T"], refs=READY, request_key=_key(), confirm=True)
    lw["svc"].senders = FakeSenders(lw["server"])
    view = lw["svc"].resume(lw["S"], lw["T"], run.id)
    items = _items(view)
    assert (items[READY[0]].status, items[READY[0]].reason) == ("UNKNOWN", "INTERRUPTED")
    # le second, jamais commencé, est traité à la reprise : il partage l'e-mail de la fiche créée juste avant
    # la coupure (et non liée) → proposé comme doublon possible au lieu d'une seconde création
    assert (items[READY[1]].status, items[READY[1]].candidates) == (
        "NEEDS_MATCH",
        [lw["server"].contacts[0]["id"]],
    )
    assert view.run.status == "NOT_SENT"
    assert len(_posts(lw["server"])) == 1  # le premier n'a jamais été réécrit


# --------------------------------------------------------------------------- #
# Sécurité, isolation, frontières
# --------------------------------------------------------------------------- #
def test_secret_is_read_only_for_the_send_and_never_stored(lw):
    store, s = lw["store"], lw["session"]
    reads = store.reads
    lw["svc"].preview(lw["S"], lw["T"])
    assert store.reads == reads  # l'aperçu ne lit pas la clé
    lw["svc"].send(lw["S"], lw["T"], refs=READY, request_key=_key(), confirm=True)
    assert store.reads > reads
    for model in (TargetSendRun, TargetSendItem, TargetResourceLink, ExportDocumentRecord):
        for row in s.scalars(select(model)):
            assert FAKE_KEY not in str({c.key: getattr(row, c.key) for c in model.__table__.columns})


def test_another_organization_cannot_send_link_or_read_runs(lw):
    view = lw["svc"].send(lw["S"], lw["T"], refs=READY, request_key=_key(), confirm=True)
    g = lw["make"]("globex")
    other = PartnerTargetService(lw["session"], g["svc"].ctx, g["src"], senders=FakeSenders(lw["server"]))
    for call in (
        lambda: other.send(lw["S"], lw["T"], refs=READY, request_key=_key(), confirm=True),
        lambda: other.run(lw["S"], lw["T"], view.run.id),
        lambda: other.link(lw["S"], lw["T"], "B-1", "x"),
        lambda: other.run(g["source"].id, g["target"].id, view.run.id),
    ):
        with pytest.raises(NotFound):
            call()


def test_write_client_never_deletes_and_read_client_never_writes():
    client = LexwareClient(SecretStr(FAKE_KEY), transport=FakeLexwareServer().transport, writable=True)
    with pytest.raises(ReadOnlyViolation):
        client.request("DELETE", "/v1/contacts/x")
    with pytest.raises(ReadOnlyViolation):
        LexwareClient(SecretStr(FAKE_KEY)).request("POST", "/v1/contacts", json={})


def test_delivery_core_names_no_system():
    from pathlib import Path

    core = Path(__file__).resolve().parents[2] / "packages" / "core" / "src" / "uie_core" / "delivery"
    for py in core.glob("*.py"):
        low = py.read_text(encoding="utf-8").lower()
        assert not any(w in low for w in ("lexware", "odoo", "datev", "contacts")), py.name
