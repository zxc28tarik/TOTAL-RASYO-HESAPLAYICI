from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.repair_current_ek9_gaps import merge_verified, missing_days


def fixture():
    window = pd.bdate_range("2026-06-10", periods=64).strftime("%Y-%m-%d").tolist()
    full = pd.DataFrame({"ticker": "ABC", "trade_date": window,
                         "close": np.arange(64, dtype=float) + 100,
                         "adj_close": np.arange(64, dtype=float) + 90})
    return window, full, full.drop(index=62).copy()


def test_missing_day_recovered_only_from_same_date_and_matching_price_basis():
    window, full, old = fixture()
    result, reason = merge_verified(old, "ABC", window, full)
    assert reason == "VERIFIED_DATED_PRICE_GAP_REPAIRED"
    assert missing_days(result, "ABC", window) == []
    pd.testing.assert_frame_equal(result.sort_values("trade_date").reset_index(drop=True), full)
    pd.testing.assert_frame_equal(old, full.drop(index=62))


def test_incomplete_supplement_cannot_fill_gap():
    window, full, old = fixture()
    result, reason = merge_verified(old, "ABC", window, full.drop(index=62))
    assert reason == "SUPPLEMENT_WINDOW_INCOMPLETE"
    pd.testing.assert_frame_equal(result, old)


def test_adjustment_basis_change_rejected():
    window, full, old = fixture()
    full["adj_close"] *= 0.5
    result, reason = merge_verified(old, "ABC", window, full)
    assert reason == "SUPPLEMENT_PRICE_BASIS_MISMATCH"
    pd.testing.assert_frame_equal(result, old)


def test_wrong_ticker_duplicate_and_nan_are_rejected():
    window, full, old = fixture()
    for candidate in (full.assign(ticker="OTHER"), pd.concat([full, full.iloc[[0]]])):
        result, reason = merge_verified(old, "ABC", window, candidate)
        assert reason == "SUPPLEMENT_IDENTITY_OR_DUPLICATE_INVALID"
        pd.testing.assert_frame_equal(result, old)
    full.loc[62, "adj_close"] = np.nan
    result, reason = merge_verified(old, "ABC", window, full)
    assert reason == "SUPPLEMENT_PRICE_INVALID"
    pd.testing.assert_frame_equal(result, old)

def test_receipt_crlf_reconciliation_requires_exact_original_hash(tmp_path, monkeypatch):
    import hashlib
    from scripts import repair_current_ek9_gaps as repair
    monkeypatch.setattr(repair, "ROOT", tmp_path)
    path = tmp_path / "modules.csv"
    path.write_bytes(b"ticker,ek9\nABC,0.2\n")
    receipt = {"outputs": {"modules.csv": hashlib.sha256(
        b"ticker,ek9\r\nABC,0.2\r\n").hexdigest()}}
    verified = repair.verify_outputs(tmp_path, receipt)
    assert verified[0]["transformation"] == "GIT_EOL_LF_FROM_VERIFIED_CRLF"
    path.write_bytes(b"ticker,ek9\nABC,0.3\n")
    import pytest
    with pytest.raises(ValueError, match="artifact hash mismatch"):
        repair.verify_outputs(tmp_path, receipt)


def test_binary_receipt_mutation_rejected(tmp_path, monkeypatch):
    import hashlib
    import pytest
    from scripts import repair_current_ek9_gaps as repair
    monkeypatch.setattr(repair, "ROOT", tmp_path)
    path = tmp_path / "prices.csv.gz"
    path.write_bytes(b"original")
    receipt = {"outputs": {"prices.csv.gz": hashlib.sha256(b"original").hexdigest()}}
    assert repair.verify_outputs(tmp_path, receipt) == []
    path.write_bytes(b"mutated")
    with pytest.raises(ValueError, match="artifact hash mismatch"):
        repair.verify_outputs(tmp_path, receipt)
