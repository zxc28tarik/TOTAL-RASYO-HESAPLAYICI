"""W7-A — the production evidence-dating gate is real, not a misreading.

A present-day corporate-action capture, however complete, cannot certify a
non-zero historical interval because the production evidence verifier
requires every source's own publication timestamp to predate the cutoff.
This is demonstrated against the real production function, with a backdated
control proving the rejection is about the date, not the manifest shape.
"""
from __future__ import annotations

import json

import pytest

from scripts.audit_w7a_evidence_dating_gate import (
    AUDIT,
    CONTENT_FILES,
    CONTRACT,
    W7AAuditError,
    attempt_with_source_dated,
    derive,
    sha_bytes,
)

RECEIPT = AUDIT / "receipt.json"

pytestmark = pytest.mark.skipif(
    not RECEIPT.exists(), reason="W7-A evidence-dating-gate artifacts not materialized"
)


@pytest.fixture(scope="module")
def receipt() -> dict:
    return json.loads(RECEIPT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def stored() -> dict:
    return {
        name: json.loads((AUDIT / name).read_text(encoding="utf-8")) for name in CONTENT_FILES
    }


def test_a_present_day_capture_is_rejected(stored):
    case = stored["attempt.json"]["case_a_honest_capture_timestamp"]
    assert case["accepted"] is False
    assert case["error"] == "future source publication"


def test_a_backdated_control_is_accepted(stored):
    """Proves the rejection above is about the date, not the manifest shape."""
    case = stored["attempt.json"]["case_b_backdated_control"]
    assert case["accepted"] is True
    assert case["error"] is None


def test_verdict_is_blocked_with_a_reopen_condition(stored):
    verdict = stored["verdict.json"]
    assert verdict["status"] == "BLOCKED"
    assert verdict["policy"]["m2_materialized"] is False
    assert verdict["policy"]["production_code_changed"] is False
    assert "period-contemporary" in verdict["reopen_condition"]


def test_derive_refuses_if_the_honest_timestamp_is_ever_accepted(monkeypatch):
    import scripts.audit_w7a_evidence_dating_gate as module

    def always_accept(published_at, source_ref, response_path):
        return {"published_at": published_at, "accepted": True, "error": None}

    monkeypatch.setattr(module, "attempt_with_source_dated", always_accept)
    with pytest.raises(W7AAuditError, match="HONEST_CAPTURE_TIMESTAMP_UNEXPECTEDLY_ACCEPTED"):
        module.derive()


def test_derive_refuses_if_the_control_is_ever_rejected(monkeypatch):
    import scripts.audit_w7a_evidence_dating_gate as module
    real = module.attempt_with_source_dated

    def sometimes_reject(published_at, source_ref, response_path):
        result = real(published_at, source_ref, response_path)
        if source_ref == "KAP_WINDOW_BACKDATED_CONTROL":
            return {"published_at": published_at, "accepted": False, "error": "unexpected"}
        return result

    monkeypatch.setattr(module, "attempt_with_source_dated", sometimes_reject)
    with pytest.raises(W7AAuditError, match="BACKDATED_CONTROL_UNEXPECTEDLY_REJECTED"):
        module.derive()


def test_a_source_exactly_at_the_cutoff_is_accepted():
    from datetime import date
    from scripts.audit_w7a_evidence_dating_gate import (
        COVERING_WINDOW, INVENTORY_DIR, CUTOFF,
    )
    response_path = INVENTORY_DIR / f"{COVERING_WINDOW}.response.json"
    result = attempt_with_source_dated(CUTOFF.isoformat(), "T", response_path)
    assert result["accepted"] is True


def test_a_source_one_second_after_the_cutoff_is_rejected():
    from datetime import timedelta
    from scripts.audit_w7a_evidence_dating_gate import COVERING_WINDOW, INVENTORY_DIR, CUTOFF
    response_path = INVENTORY_DIR / f"{COVERING_WINDOW}.response.json"
    one_second_late = (CUTOFF + timedelta(seconds=1)).isoformat()
    result = attempt_with_source_dated(one_second_late, "T", response_path)
    assert result["accepted"] is False
    assert result["error"] == "future source publication"


def test_stored_artifacts_match_the_receipt(receipt):
    for name in CONTENT_FILES:
        assert sha_bytes((AUDIT / name).read_bytes()) == receipt["output_sha256"][name]


def test_second_derivation_is_byte_identical(receipt):
    content = derive()
    for name in CONTENT_FILES:
        assert sha_bytes(content[name]) == receipt["output_sha256"][name]


def test_every_artifact_declares_the_same_contract(stored):
    for name in ("attempt.json", "verdict.json"):
        assert stored[name]["contract"] == CONTRACT
