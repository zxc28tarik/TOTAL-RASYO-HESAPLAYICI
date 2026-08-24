from __future__ import annotations

import pandas as pd
import pytest

from src.analytics import run_daily_pipeline
from src.analytics.ek1_quality import compute_ek1_score_from_good_count


@pytest.mark.parametrize(
    ("good_count", "expected"),
    [(0, 0.0), (9, 0.5), (18, 1.0), (27, 1.0), (-2, 0.0)],
)
def test_shared_ek1_math_is_exact_live_formula(good_count, expected):
    assert compute_ek1_score_from_good_count(good_count) == pytest.approx(expected)


def test_live_ek1_path_preserves_pre_refactor_coercion_and_output(monkeypatch):
    source = pd.DataFrame(
        [
            {"ticker": "AAA", "good_count_ge8": None},
            {"ticker": "BBB", "good_count_ge8": 9},
            {"ticker": "CCC", "good_count_ge8": 27},
        ]
    )
    monkeypatch.setattr(run_daily_pipeline.pd, "read_sql", lambda *args, **kwargs: source.copy())
    calls = []
    real = compute_ek1_score_from_good_count

    def spy(value):
        calls.append(value)
        return real(value)

    monkeypatch.setattr(run_daily_pipeline, "compute_ek1_score_from_good_count", spy)
    result = run_daily_pipeline._compute_ek1_goodcount(object(), pd.Timestamp("2025-01-02").date())

    assert calls == [0.0, 9.0, 27.0]
    assert result["ticker"].tolist() == ["AAA", "BBB", "CCC"]
    assert result["ek1"].tolist() == pytest.approx([0.0, 0.5, 1.0])
    assert pd.isna(result.loc[0, "good_count_ge8"])
    assert result.loc[1:, "good_count_ge8"].tolist() == [9.0, 27.0]
