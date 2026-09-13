from __future__ import annotations

import pandas as pd
import pytest

from src.analytics.current_nonfin_follow import materialize_current_nonfin_follow


def valuation(ticker, period, mid, status="OK"):
    return {"ticker": ticker, "anchor_period_end": period, "V_mid": mid, "status": status}


def test_real_consecutive_bands_and_adjusted_prices_materialize_follow():
    rows, rejected = materialize_current_nonfin_follow(
        current_valuations=[valuation("AAA", "2026-06-30", 120)],
        previous_valuations=[valuation("AAA", "2026-03-31", 100)],
        adjusted_prices=pd.DataFrame([
            {"ticker": "AAA", "trade_date": "2026-03-31", "adj_close": 50},
            {"ticker": "AAA", "trade_date": "2026-09-08", "adj_close": 55},
        ]),
    )
    assert rejected == []
    assert rows[0]["follow_active"] is True
    assert rows[0]["band_mid_change_1q"] == pytest.approx(0.2)
    assert rows[0]["price_change_since_previous_period"] == pytest.approx(0.1)
    assert rows[0]["follow_score"] == pytest.approx(0.7)


def test_missing_previous_valuation_is_explicit_rejection_not_neutral_fill():
    rows, rejected = materialize_current_nonfin_follow(
        current_valuations=[valuation("AAA", "2026-06-30", 120)],
        previous_valuations=[],
        adjusted_prices=pd.DataFrame(columns=["ticker", "trade_date", "adj_close"]),
    )
    assert rows == []
    assert rejected == [{"ticker": "AAA", "reason": "PREVIOUS_VALUATION_NOT_OK"}]
