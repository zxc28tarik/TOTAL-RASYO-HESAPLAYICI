"""Targeted tests for scripts/audit_w7b_spk_bulletin_evidence.py.

The expensive end-to-end path (derive(), ~90s: extracts text from ~100
archived PDFs and calls the real production replay) runs once per test
session via a module-scoped fixture; individual tests assert against that
one result or exercise cheaper helper functions in isolation.
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

import scripts.audit_w7b_spk_bulletin_evidence as w7b


@pytest.fixture(scope="module")
def content():
    return w7b.derive()


@pytest.fixture(scope="module")
def archive_json(content):
    return json.loads(content["archive.json"])


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
def census_json(content):
    return json.loads(content["field_completeness_census.json"])


@pytest.fixture(scope="module")
def verdict_json(content):
    return json.loads(content["verdict.json"])


# ---- sha helper correctness (the exact bug this audit's own dev caught) ----

def test_sha_raw_is_not_crlf_normalized():
    payload = b"PDF-BINARY-\r\n-CONTENT-THAT-MUST-NOT-BE-TOUCHED"
    assert w7b.sha_raw(payload) == sha256(payload).hexdigest()
    assert w7b.sha_raw(payload) != w7b.sha_bytes(payload)


def test_sha_bytes_is_crlf_normalized_for_generated_json():
    assert w7b.sha_bytes(b"a\r\nb") == w7b.sha_bytes(b"a\nb")


# ---- bulletin archive integrity ----

def test_archive_manifest_contract_and_gap_free_numbering(archive_json):
    assert archive_json["contract"] == w7b.CONTRACT
    # W7-C extended this archive from 98 bulletins (2022-06-09..2023-08-31) back
    # to 461 (2016-06-24..2023-08-31) to bridge a wider set of NONFIN tickers'
    # KAP share-basis anchors through their cutoffs; see W7C_REAL_M2_SCORE.md.
    # None of W7-B's own six tickers' anchor dates fall inside the newly added
    # span (all are 2022-06-08 or later), so this is the only value here that
    # changes -- every other assertion in this file is unaffected.
    assert archive_json["bulletin_count"] == 461
    assert archive_json["span_start"] == "2016-06-24"
    assert archive_json["span_end"] == "2023-08-31"


def test_archive_detects_tampered_pdf_bytes(tmp_path, monkeypatch):
    import shutil
    shadow = tmp_path / "archive"
    shutil.copytree(w7b.ARCHIVE_DIR, shadow)
    target = next((shadow / "bulletins").glob("*.pdf"))
    data = bytearray(target.read_bytes())
    data[-1] ^= 0xFF
    target.write_bytes(bytes(data))
    monkeypatch.setattr(w7b, "ARCHIVE_DIR", shadow)
    with pytest.raises(w7b.W7BAuditError, match="BULLETIN_HASH_MISMATCH"):
        w7b.load_bulletin_archive()


def test_archive_detects_numbering_gap(tmp_path, monkeypatch):
    import shutil
    shadow = tmp_path / "archive"
    shutil.copytree(w7b.ARCHIVE_DIR, shadow)
    manifest_path = shadow / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["entries"] = [e for e in manifest["entries"] if not (e["bulletin_year"] == 2023 and e["bulletin_num"] == 5)]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(w7b, "ARCHIVE_DIR", shadow)
    with pytest.raises(w7b.W7BAuditError, match="BULLETIN_NUMBERING_GAP"):
        w7b.load_bulletin_archive()


def test_archive_detects_duplicate_bulletin_number(tmp_path, monkeypatch):
    import shutil
    shadow = tmp_path / "archive"
    shutil.copytree(w7b.ARCHIVE_DIR, shadow)
    manifest_path = shadow / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    donor = next(e for e in manifest["entries"] if e["bulletin_year"] == 2023 and e["bulletin_num"] == 10)
    clash = dict(donor)
    clash["filename"] = donor["filename"]  # same file, same number -- a genuine duplicate entry
    manifest["entries"].append(clash)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(w7b, "ARCHIVE_DIR", shadow)
    with pytest.raises(w7b.W7BAuditError, match="DUPLICATE_BULLETIN_NUMBER"):
        w7b.load_bulletin_archive()


def test_archive_allows_two_distinct_bulletins_sharing_one_date(archive_json):
    # 52-2022 and 53-2022 were both published 2022-10-02; the real capture
    # exercises this every run of the fixture above without raising
    # DUPLICATE_BULLETIN_DATE, which is the regression this test pins.
    manifest = json.loads((w7b.ARCHIVE_DIR / "manifest.json").read_text(encoding="utf-8"))
    same_date = [e for e in manifest["entries"] if e["date"] == "2022-10-02"]
    assert {e["bulletin_num"] for e in same_date} == {52, 53}


# ---- capital-action absence scan ----

def test_unexplained_mention_aborts_rather_than_silently_passing():
    # Real bulletin 2-2023 genuinely mentions ENKA insaat (a registry-list
    # false positive KONTR/ALFAS/ENKAI's own real KNOWN_NON_ACTION_MENTIONS
    # entries already cover). Scanning under a ticker key the registry does
    # NOT recognize must abort instead of silently asserting completeness.
    archive = w7b.load_bulletin_archive()
    saved = dict(w7b.NAME_SEARCH_KEY)
    w7b.NAME_SEARCH_KEY["_FAKE_UNREGISTERED_"] = "ENKA İNŞAAT"
    try:
        with pytest.raises(w7b.W7BAuditError, match="UNEXPLAINED_MENTION"):
            w7b.verify_capital_action_absence("_FAKE_UNREGISTERED_", "2022-06-08", archive)
    finally:
        w7b.NAME_SEARCH_KEY.clear()
        w7b.NAME_SEARCH_KEY.update(saved)


def test_known_non_action_mention_does_not_abort():
    archive = w7b.load_bulletin_archive()
    sources = w7b.verify_capital_action_absence("KONTR", "2022-09-07", archive)
    assert len(sources) == 80
    assert sources[0]["date"] == "2022-09-08"
    assert sources[-1]["date"] == "2023-08-31"


def test_empty_bulletin_window_rejected():
    with pytest.raises(w7b.W7BAuditError, match="EMPTY_BULLETIN_WINDOW"):
        w7b.verify_capital_action_absence("KONTR", "2023-08-31", {"by_date": {"2023-08-31": []}})


# ---- evidence gate: the actual finding under test ----

def test_all_six_tickers_pass_the_production_evidence_gate(gate_results_json):
    results = {row["ticker"]: row for row in gate_results_json["results"]}
    assert set(results) == set(w7b.TICKERS)
    for ticker, row in results.items():
        assert row["gate_passed"] is True, (ticker, row.get("error"))
        assert row["market_cap"] > 0
        assert row["action_evidence_sha256"]


def test_market_caps_match_known_values(gate_results_json):
    expected = {
        "KONTR": 15932720947.265625,
        "SMRTG": 15308567845.91675,
    }
    results = {row["ticker"]: row for row in gate_results_json["results"]}
    for ticker, cap in expected.items():
        assert results[ticker]["market_cap"] == pytest.approx(cap, rel=1e-9)


def test_negative_controls_actually_reject(negative_controls_json):
    results = {row["ticker"]: row for row in negative_controls_json["results"]}
    assert set(results) == set(w7b.TICKERS)
    for ticker, row in results.items():
        assert row["case_a"]["rejected"] is True
        assert row["case_a"]["error"] == "future source publication"
        assert row["case_c"]["rejected"] is True
        assert row["case_c"]["error"] == "future source publication"


# ---- batch replay: the honest, documented remaining blocker ----

def test_batch_replay_produces_no_m2_score_yet(batch_replay_json):
    assert batch_replay_json["m2_score_count"] == 0
    assert batch_replay_json["rejection_count"] == len(w7b.TICKERS)
    reasons = set(batch_replay_json["rejection_reasons"].values())
    assert reasons == {"VALUATION_NOT_USABLE:YETERSIZ_MULTIPLE_KAPSAMI"}


def test_batch_replay_shows_pb_multiple_reaches_full_peer_count(batch_replay_json):
    # The concrete, checkable shape of "scale problem, not a dating problem":
    # PB already reaches minimum_peer_count=5 peers with all six tickers
    # resolved; PE/EV_EBIT/PS do not yet.
    diag = batch_replay_json["diagnostics_by_ticker"]["KONTR"]
    pb = diag["multiple_details"]["PB"]
    assert pb["peer_count"] == 5
    assert pb["usable"] is True


def test_field_completeness_census_matches_measured_ceiling(census_json):
    # Pins the exact, honest measurement behind Sec 6's "no sector clears
    # minimum_peer_count on CORE data alone" claim -- independent of the
    # evidence-dating gate entirely.
    assert census_json["signal_date"] == w7b.SIGNAL_DATE
    assert census_json["required_fields"] == ["revenue", "ebit", "net_income"]
    assert census_json["max_complete_in_any_sector"] < 5
    by_sector = census_json["by_sector"]
    assert by_sector["XUHIZ"] == {"total": 24, "complete": 1}
    assert by_sector["XUSIN"] == {"total": 34, "complete": 3}


def test_census_guard_fires_if_ceiling_reaches_threshold(monkeypatch):
    """Prove the guard in derive() is live, not decorative: if some sector's
    census count reached minimum_peer_count, derive() must raise rather than
    silently keep shipping the 'no sector clears it' narrative unchanged."""
    original = w7b.build_field_completeness_census

    def inflated(per_ticker):
        result = original(per_ticker)
        result["max_complete_in_any_sector"] = 5
        return result

    monkeypatch.setattr(w7b, "build_field_completeness_census", inflated)
    with pytest.raises(w7b.W7BAuditError, match="CENSUS_CEILING_NO_LONGER_BELOW_THRESHOLD"):
        w7b.derive()


def test_unexpected_m2_score_would_raise_not_pass_silently(monkeypatch):
    """Prove derive()'s own guard is live: if minimum_peer_count were lowered
    enough that a score DID come out, derive() must raise rather than accept
    a changed result under this audit's fixed narrative."""
    import src.analytics.nonfin_valuation as nonfin_valuation

    original_from_json_file = nonfin_valuation.NonfinValuationConfig.from_json_file

    def patched(path):
        config = original_from_json_file(path)
        return nonfin_valuation.NonfinValuationConfig(
            **{**config.__dict__, "minimum_peer_count": 1, "minimum_coverage_weight": 0.01}
        )

    monkeypatch.setattr(w7b.NonfinValuationConfig, "from_json_file", staticmethod(patched))
    with pytest.raises(w7b.W7BAuditError, match="UNEXPECTED_M2_SCORE_PRODUCED"):
        w7b.derive()


# ---- verdict/receipt shape and reproducibility ----

def test_verdict_status_and_policy(verdict_json):
    assert verdict_json["status"] == "PARTIAL_PROGRESS"
    policy = verdict_json["policy"]
    assert policy["m2_materialized"] is False
    assert policy["market_cap_materialized_non_zero_interval"] is True
    assert policy["production_code_changed"] is False
    assert policy["neutral_fill"] is False


def test_derive_is_byte_identical_across_two_calls(content):
    second = w7b.derive()
    assert content.keys() == second.keys()
    for name in content:
        assert content[name] == second[name], name


def test_receipt_hash_mode_and_content_hashes_match(content):
    receipt = w7b.build_receipt(content)
    assert receipt["hash_mode"] == w7b.HASH_MODE
    for name in w7b.CONTENT_FILES:
        assert receipt["output_sha256"][name] == w7b.sha_bytes(content[name])
