"""Targeted tests for scripts/audit_w7c_real_m2_score.py.

The expensive end-to-end path (derive(): extracts text from hundreds of
archived PDFs across two tickers' multi-year windows and calls the real
production replay) runs once per test session via a module-scoped fixture;
individual tests assert against that one result or exercise cheaper helper
functions in isolation.
"""
from __future__ import annotations

from hashlib import sha256
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.audit_w7c_real_m2_score as w7c


@pytest.fixture(scope="module")
def content():
    return w7c.derive()


@pytest.fixture(scope="module")
def archive_json(content):
    return json.loads(content["archive.json"])


@pytest.fixture(scope="module")
def anchors_json(content):
    return json.loads(content["anchors.json"])


@pytest.fixture(scope="module")
def evidence_json(content):
    return json.loads(content["evidence.json"])


@pytest.fixture(scope="module")
def gate_results_json(content):
    return json.loads(content["gate_results.json"])


@pytest.fixture(scope="module")
def negative_controls_json(content):
    return json.loads(content["negative_controls.json"])


@pytest.fixture(scope="module")
def batch_replay_json(content):
    return json.loads(content["batch_replay.json"])


@pytest.fixture(scope="module")
def verdict_json(content):
    return json.loads(content["verdict.json"])


# ---- sha helper correctness ----

def test_sha_raw_is_not_crlf_normalized():
    payload = b"PDF-BINARY-\r\n-CONTENT-THAT-MUST-NOT-BE-TOUCHED"
    assert w7c.sha_raw(payload) == sha256(payload).hexdigest()
    assert w7c.sha_raw(payload) != w7c.sha_bytes(payload)


def test_sha_bytes_is_crlf_normalized_for_generated_json():
    assert w7c.sha_bytes(b"a\r\nb") == w7c.sha_bytes(b"a\nb")


# ---- bulletin archive integrity (shared with W7-B) ----

def test_archive_manifest_contract_and_spans_the_full_archive(archive_json):
    assert archive_json["contract"] == w7c.CONTRACT
    assert archive_json["bulletin_count"] == 461
    assert archive_json["span_start"] == "2016-06-24"
    assert archive_json["span_end"] == "2023-08-31"


def test_archive_detects_tampered_pdf_bytes(tmp_path, monkeypatch):
    import shutil
    shadow = tmp_path / "archive"
    shutil.copytree(w7c.ARCHIVE_DIR, shadow)
    target = next((shadow / "bulletins").glob("*.pdf"))
    data = bytearray(target.read_bytes())
    data[-1] ^= 0xFF
    target.write_bytes(bytes(data))
    monkeypatch.setattr(w7c, "ARCHIVE_DIR", shadow)
    with pytest.raises(w7c.W7CAuditError, match="BULLETIN_HASH_MISMATCH"):
        w7c.load_bulletin_archive()


def test_archive_detects_numbering_gap(tmp_path, monkeypatch):
    import shutil
    shadow = tmp_path / "archive"
    shutil.copytree(w7c.ARCHIVE_DIR, shadow)
    manifest_path = shadow / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["entries"] = [e for e in manifest["entries"] if not (e["bulletin_year"] == 2023 and e["bulletin_num"] == 5)]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(w7c, "ARCHIVE_DIR", shadow)
    with pytest.raises(w7c.W7CAuditError, match="BULLETIN_NUMBERING_GAP"):
        w7c.load_bulletin_archive()


def test_archive_detects_duplicate_bulletin_number(tmp_path, monkeypatch):
    import shutil
    shadow = tmp_path / "archive"
    shutil.copytree(w7c.ARCHIVE_DIR, shadow)
    manifest_path = shadow / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    donor = next(e for e in manifest["entries"] if e["bulletin_year"] == 2023 and e["bulletin_num"] == 10)
    clash = dict(donor)
    clash["filename"] = donor["filename"]
    manifest["entries"].append(clash)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(w7c, "ARCHIVE_DIR", shadow)
    with pytest.raises(w7c.W7CAuditError, match="DUPLICATE_BULLETIN_NUMBER"):
        w7c.load_bulletin_archive()


# ---- share anchor resolution: always from raw text, never the precomputed field ----

def test_six_of_seven_anchors_match_their_own_stored_derived_shares(anchors_json):
    anchors = anchors_json["anchors"]
    assert set(anchors) == set(w7c.TICKERS)
    for ticker, info in anchors.items():
        if ticker == "CCOLA":
            continue
        assert info["decimal_bug_corrected"] is False
        assert info["shares_out"] == pytest.approx(info["stored_derived_shares"], rel=1e-9)


def test_ccola_anchor_is_corrected_from_the_raw_disclosure(anchors_json):
    ccola = anchors_json["anchors"]["CCOLA"]
    assert ccola["decimal_bug_corrected"] is True
    assert ccola["anchor_date"] == "2019-05-14"
    # The artifact's own derived_shares for this entry is ~686x inflated by a
    # decimal-separator mis-parse; the raw nominalValueOfShares/
    # nominalValuePerShare strings recompute to CCOLA's true, KAP-certified
    # share count -- identical to its own clean 2016-06-23 observation, i.e.
    # zero real capital change in between.
    assert ccola["shares_out"] == pytest.approx(25437078200.0, rel=1e-9)
    assert ccola["stored_derived_shares"] == pytest.approx(17445078200000.0, rel=1e-9)


def test_decimal_bug_correction_set_matches_expectation():
    assert w7c.EXPECTED_DECIMAL_BUG_CORRECTED == {"CCOLA"}


def test_census_guard_fires_if_correction_set_changes(monkeypatch):
    """Prove the guard in derive() is live: if a previously-clean ticker's raw
    recomputation ever disagreed with its stored figure (or CCOLA's stopped
    disagreeing), derive() must raise rather than silently accepting a
    changed anchor set under this audit's fixed narrative."""
    original = w7c.resolve_share_anchor

    def flipped(ticker):
        result = original(ticker)
        if ticker == "BRSAN":
            result = dict(result, decimal_bug_corrected=True)
        return result

    monkeypatch.setattr(w7c, "resolve_share_anchor", flipped)
    with pytest.raises(w7c.W7CAuditError, match="DECIMAL_BUG_CORRECTION_SET_CHANGED"):
        w7c.derive()


# ---- capital-action absence scan ----

def test_ccola_multi_year_window_only_hits_reviewed_non_action_mentions():
    archive = w7c.load_bulletin_archive()
    sources = w7c.verify_capital_action_absence("CCOLA", "2019-05-14", archive)
    assert len(sources) > 300
    assert sources[0]["date"] > "2019-05-14"
    # The archive's last bulletin at or before W7-C's 2023-07-31 price date
    # (not 2023-08-31 -- that is W7-B's later cutoff, a different audit).
    assert sources[-1]["date"] == "2023-07-28"


def test_unreviewed_mention_would_abort():
    archive = w7c.load_bulletin_archive()
    saved = dict(w7c.NAME_SEARCH_KEY)
    w7c.NAME_SEARCH_KEY["_FAKE_UNREGISTERED_"] = "KONYA ÇİMENTO"
    try:
        with pytest.raises(w7c.W7CAuditError, match="UNEXPLAINED_MENTION"):
            w7c.verify_capital_action_absence("_FAKE_UNREGISTERED_", "2019-05-31", archive)
    finally:
        w7c.NAME_SEARCH_KEY.clear()
        w7c.NAME_SEARCH_KEY.update(saved)


def test_empty_bulletin_window_rejected():
    with pytest.raises(w7c.W7CAuditError, match="EMPTY_BULLETIN_WINDOW"):
        w7c.verify_capital_action_absence("BRSAN", "2023-08-31", {"by_date": {"2023-08-31": []}})


# ---- evidence gate: the actual finding under test ----

def test_all_seven_tickers_pass_the_production_evidence_gate(gate_results_json):
    results = {row["ticker"]: row for row in gate_results_json["results"]}
    assert set(results) == set(w7c.TICKERS)
    for ticker, row in results.items():
        assert row["gate_passed"] is True, (ticker, row.get("error"))
        assert row["market_cap"] > 0
        assert row["action_evidence_sha256"]


def test_negative_controls_actually_reject(negative_controls_json):
    results = {row["ticker"]: row for row in negative_controls_json["results"]}
    assert set(results) == set(w7c.TICKERS)
    for ticker, row in results.items():
        assert row["case_a"]["rejected"] is True
        assert row["case_a"]["error"] == "future source publication"
        assert row["case_c"]["rejected"] is True
        assert row["case_c"]["error"] == "future source publication"


# ---- batch replay: the actual finding -- a real, non-zero M2 score ----

def test_batch_replay_produces_seven_real_m2_scores(batch_replay_json):
    assert batch_replay_json["m2_score_count"] == 7
    assert batch_replay_json["rejection_count"] == 0
    assert batch_replay_json["rejection_reasons"] == {}


def test_every_ticker_clears_pe_and_pb_with_full_peer_count(batch_replay_json):
    diag_by_ticker = batch_replay_json["diagnostics_by_ticker"]
    assert set(diag_by_ticker) == set(w7c.TICKERS)
    for ticker, diag in diag_by_ticker.items():
        assert diag["status"] == "OK"
        details = diag["multiple_details"]
        for multiple in ("PE", "PB"):
            assert details[multiple]["usable"] is True
            assert details[multiple]["peer_count"] == 6


def test_m2_scores_are_real_nonzero_floats_not_a_neutral_fill(batch_replay_json):
    scores = batch_replay_json["m2_scores"]
    assert len(scores) == 7
    values = {row["ticker"]: row["m2"] for row in scores}
    assert set(values) == set(w7c.TICKERS)
    for ticker, m2 in values.items():
        assert isinstance(m2, float)
        assert m2 > 0
        # Not every score collapses to the exact same value -- each ticker's
        # own price/financials genuinely drive its own valuation_score input.
    assert len(set(round(v, 6) for v in values.values())) == len(values)


def test_unexpected_rejection_would_raise_not_pass_silently(monkeypatch):
    """Prove derive()'s own guard is live: if minimum_peer_count were raised
    just past what seven tickers (six peers each) can ever supply, these seven
    no longer clear it and derive() must raise rather than silently accepting
    a changed (or absent) result."""
    import src.analytics.nonfin_valuation as nonfin_valuation

    original_from_json_file = nonfin_valuation.NonfinValuationConfig.from_json_file

    def patched(path):
        config = original_from_json_file(path)
        return nonfin_valuation.NonfinValuationConfig(
            **{**config.__dict__, "minimum_peer_count": len(w7c.TICKERS)}
        )

    monkeypatch.setattr(w7c.NonfinValuationConfig, "from_json_file", staticmethod(patched))
    with pytest.raises(w7c.W7CAuditError, match="UNEXPECTED_REJECTIONS"):
        w7c.derive()


# ---- verdict/receipt shape and reproducibility ----

def test_verdict_status_and_policy(verdict_json):
    assert verdict_json["status"] == "M2_MATERIALIZED"
    policy = verdict_json["policy"]
    assert policy["m2_materialized"] is True
    assert policy["market_cap_materialized_non_zero_interval"] is True
    assert policy["production_code_changed"] is False
    assert policy["neutral_fill"] is False
    assert policy["scope"] == "SEVEN_TICKER_CLOSED_SAMPLE_ONE_CUTOFF"


def test_derive_is_byte_identical_across_two_calls(content):
    second = w7c.derive()
    assert content.keys() == second.keys()
    for name in content:
        assert content[name] == second[name], name


def test_receipt_hash_mode_and_content_hashes_match(content):
    receipt = w7c.build_receipt(content)
    assert receipt["hash_mode"] == w7c.HASH_MODE
    for name in w7c.CONTENT_FILES:
        assert receipt["output_sha256"][name] == w7c.sha_bytes(content[name])
