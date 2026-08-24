from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.analytics.ek4_momentum import compute_ek4_momentum_point
from src.analytics import run_daily_pipeline


def test_shared_ek4_point_math_is_exact_live_formula():
    point = compute_ek4_momentum_point(
        stock_start=100.0,
        stock_end=120.0,
        sector_start=100.0,
        sector_end=110.0,
    )
    assert point.stock_return == pytest.approx(0.20)
    assert point.sector_return == pytest.approx(0.10)
    assert point.excess_return == pytest.approx(0.10)
    assert point.score == pytest.approx(0.75)


@pytest.mark.parametrize(
    ("stock_end", "sector_end", "expected"),
    [(200.0, 100.0, 1.0), (50.0, 100.0, 0.0), (110.0, 110.0, 0.5)],
)
def test_shared_ek4_score_clips_like_live_formula(stock_end, sector_end, expected):
    point = compute_ek4_momentum_point(
        stock_start=100.0,
        stock_end=stock_end,
        sector_start=100.0,
        sector_end=sector_end,
    )
    assert point.score == pytest.approx(expected)


def test_live_ek4_path_calls_shared_point_math(monkeypatch):
    days = pd.bdate_range("2024-01-01", periods=22)
    universe = pd.DataFrame([{"ticker": "AAA", "sec": "XTEST"}])
    stocks = pd.DataFrame(
        {
            "ticker": "AAA",
            "trade_date": days,
            "px": np.linspace(100.0, 121.0, len(days)),
        }
    )
    indices = pd.concat(
        [
            pd.DataFrame(
                {
                    "index_code": code,
                    "trade_date": days,
                    "px": np.linspace(100.0, end, len(days)),
                }
            )
            for code, end in (("XU100", 115.0), ("XTEST", 110.5))
        ],
        ignore_index=True,
    )
    reads = iter([universe, stocks, indices])
    monkeypatch.setattr(run_daily_pipeline.pd, "read_sql", lambda *args, **kwargs: next(reads))

    calls = []
    real = compute_ek4_momentum_point

    def spy(**kwargs):
        calls.append(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(run_daily_pipeline, "compute_ek4_momentum_point", spy)
    result = run_daily_pipeline._compute_ek4_momentum(
        object(), days[-1].date(), lookback=20
    )

    assert len(calls) == 1
    assert result.to_dict("records")[0]["ticker"] == "AAA"
    expected = real(**calls[0]).score
    assert float(result.iloc[0]["ek4"]) == pytest.approx(expected)
