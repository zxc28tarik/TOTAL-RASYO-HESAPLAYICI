from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.analytics.ek9_volatility import compute_ek9_volatility_scores
from src.analytics import run_daily_pipeline


def _legacy_scores(window: pd.DataFrame) -> pd.DataFrame:
    vol = window.std(ddof=1).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    ek9 = 1.0 - (vol / 0.06).clip(0.0, 1.0)
    return pd.DataFrame({"volatility": vol.astype(float), "ek9": ek9.astype(float)})


def test_shared_ek9_math_is_exact_legacy_formula():
    returns = pd.DataFrame(
        {
            "AAA": [0.01, -0.02, 0.03, -0.01],
            "BBB": [0.0, 0.0, 0.0, 0.0],
        }
    )
    expected = _legacy_scores(returns)
    actual = compute_ek9_volatility_scores(returns)
    pd.testing.assert_frame_equal(actual, expected)


def test_shared_ek9_math_keeps_sample_std_and_006_cap():
    returns = pd.DataFrame({"AAA": [-0.10, 0.10]})
    scored = compute_ek9_volatility_scores(returns)
    assert float(scored.loc["AAA", "volatility"]) == pytest.approx(
        np.std([-0.10, 0.10], ddof=1)
    )
    assert float(scored.loc["AAA", "ek9"]) == 0.0


def test_shared_ek9_rejects_nonfinite_input_instead_of_maximum_score():
    returns = pd.DataFrame({"AAA": [np.inf, np.inf], "BBB": [np.nan, np.nan]})
    scored = compute_ek9_volatility_scores(returns)
    assert scored.isna().all().all()


def _mock_reads(monkeypatch, prices, days):
    def read_sql(sql, *args, **kwargs):
        if "core.index_prices_daily" in sql:
            return pd.DataFrame({"trade_date": days})
        return prices.copy()
    monkeypatch.setattr(run_daily_pipeline.pd, "read_sql", read_sql)


def test_live_ek9_path_calls_shared_math_and_matches_legacy(monkeypatch):
    days = pd.bdate_range("2024-01-01", periods=70)
    step = np.arange(len(days), dtype=float)
    prices = pd.concat(
        [
            pd.DataFrame(
                {
                    "ticker": ticker,
                    "trade_date": days,
                    "px": values,
                }
            )
            for ticker, values in (
                ("AAA", 100.0 * np.power(1.01, step)),
                ("BBB", 100.0 * np.power(1.002, step)),
            )
        ],
        ignore_index=True,
    )
    _mock_reads(monkeypatch, prices, days)

    calls: list[pd.DataFrame] = []
    real = compute_ek9_volatility_scores

    def spy(frame: pd.DataFrame) -> pd.DataFrame:
        calls.append(frame.copy(deep=True))
        return real(frame)

    monkeypatch.setattr(run_daily_pipeline, "compute_ek9_volatility_scores", spy)
    result = run_daily_pipeline._compute_ek9_vol(object(), days[-1].date(), lookback=63)

    assert len(calls) == 1
    pivot = prices.pivot_table(
        index="trade_date", columns="ticker", values="px", aggfunc="last"
    ).sort_index()
    legacy_window = pivot.pct_change().tail(63)
    expected = _legacy_scores(legacy_window)
    actual = result.set_index("ticker")["ek9"].sort_index()
    pd.testing.assert_series_equal(actual, expected["ek9"].sort_index(), check_names=False)


def test_live_ek9_pct_change_explicitly_disables_fill(monkeypatch):
    days = pd.bdate_range("2024-01-01", periods=70)
    prices = pd.DataFrame(
        {
            "ticker": "AAA",
            "trade_date": days,
            "px": np.linspace(100.0, 130.0, len(days)),
        }
    )
    _mock_reads(monkeypatch, prices, days)

    original = pd.DataFrame.pct_change
    seen: list[dict] = []

    def spy(self, *args, **kwargs):
        seen.append(dict(kwargs))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(pd.DataFrame, "pct_change", spy)
    run_daily_pipeline._compute_ek9_vol(object(), days[-1].date(), lookback=63)
    assert seen == [{"fill_method": None}]


def test_live_ek9_insufficient_global_rows_remain_unscored(monkeypatch):
    days = pd.bdate_range("2024-01-01", periods=64)
    prices = pd.DataFrame(
        {
            "ticker": "AAA",
            "trade_date": days,
            "px": np.linspace(100.0, 120.0, len(days)),
        }
    )
    _mock_reads(monkeypatch, prices, days)
    result = run_daily_pipeline._compute_ek9_vol(object(), days[-1].date(), lookback=63)
    assert result["ek9"].isna().all()
    assert result["ek9_rejection_reason"].tolist() == ["EK9_WINDOW_UNAVAILABLE"]
