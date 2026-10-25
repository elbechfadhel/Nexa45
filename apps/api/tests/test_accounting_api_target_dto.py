"""DTO fournisseur de l'envoi comptable vers une cible par API : statuts, messages, rien de technique."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from uie_api.supplier_accounting_api_target import ApiSendIn, ApiSendRunOut
from uie_core.accounting.api_target import ApiSendResult


def _item(
    ref: str, status: str, reason: str | None = None, target: str | None = None, resolution: str | None = None
):
    return SimpleNamespace(
        id=uuid.uuid4(), source_ref=ref, status=status, reason=reason, target_id=target, resolution=resolution
    )


def test_send_result_maps_statuses_and_plain_messages_without_technical_data():
    run = SimpleNamespace(id=uuid.uuid4(), status="PARTIAL", created_at=datetime.now(UTC), finished_at=None)
    result = ApiSendResult(
        run=run,
        items=[
            _item("1", "SENT", target="remote-1"),
            _item("2", "SKIPPED", "CHANGED_NEEDS_APPROVAL"),
            _item("3", "SKIPPED", "WARNING_NOT_SENT"),
            _item("4", "UNKNOWN", "UNKNOWN_OUTCOME"),
            _item("5", "NEEDS_MATCH", "POSSIBLE_DUPLICATE"),
            _item("6", "UNKNOWN", "UNKNOWN_OUTCOME", resolution="NOT_CREATED"),
        ],
        numbers={"1": "RE/1", "2": "RE/2"},
    )
    out = ApiSendRunOut.of(result)
    assert out.status == "partial"
    assert out.counts.model_dump() == {
        "selected": 6,
        "sent": 1,
        "failed": 0,
        "unknown": 2,
        "skipped": 2,
        "needs_decision": 1,
    }
    by = {i.source_reference: i for i in out.items}
    assert (by["1"].status, by["1"].number, by["1"].message) == ("sent", "RE/1", None)
    assert "specific approval" in by["2"].message and "warnings" in by["3"].message
    assert "may or may not have been" in by["4"].message and by["6"].resolution == "not_created"
    assert by["5"].status == "needs_decision"
    text = out.model_dump_json().lower()
    for word in ("request_key", "fingerprint", "version", "lexware", "voucher"):
        assert word not in text, word


def test_send_request_needs_a_key_a_period_and_at_least_one_reference():
    import pytest
    from pydantic import ValidationError

    ok = {
        "request_key": "k" * 8,
        "company_id": "1",
        "period": "2026-01",
        "references": ["7"],
        "confirm": True,
    }
    assert ApiSendIn(**ok).confirm is True
    for bad in ({"references": []}, {"period": "2026-1"}, {"request_key": "short"}):
        with pytest.raises(ValidationError):
            ApiSendIn(**{**ok, **bad})
