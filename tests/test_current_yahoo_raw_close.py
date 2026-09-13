from datetime import date

import pandas as pd

from scripts.capture_current_yahoo_raw_close import extract_latest


def test_current_price_uses_raw_close_and_never_adjusted_close():
    columns = pd.MultiIndex.from_product([[
        "SAFE.IS"
    ], ["Close", "Adj Close"]])
    frame = pd.DataFrame(
        [[10.0, 8.0], [11.0, 9.0]],
        index=pd.to_datetime(["2026-09-04", "2026-09-08"]), columns=columns,
    )
    row = extract_latest(frame, "SAFE", cutoff=date(2026, 9, 8))
    assert row["raw_close"] == 11.0
    assert row["adjusted_close_diagnostic"] == 9.0
    assert row["adjusted_close_used_for_market_cap"] is False


def test_current_price_rejects_future_only_or_missing_symbol():
    columns = pd.MultiIndex.from_product([["SAFE.IS"], ["Close", "Adj Close"]])
    frame = pd.DataFrame([[10.0, 8.0]], index=pd.to_datetime(["2026-09-09"]), columns=columns)
    assert extract_latest(frame, "SAFE", cutoff=date(2026, 9, 8)) is None
    assert extract_latest(frame, "OTHER", cutoff=date(2026, 9, 8)) is None
