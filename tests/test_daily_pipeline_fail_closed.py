from __future__ import annotations

from datetime import date

import pandas as pd

from src.analytics.run_daily_pipeline import _upsert_module_scores


class Cursor:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class Conn:
    def cursor(self):
        return Cursor()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_module_upsert_preserves_missing_m2_and_total_instead_of_neutralizing(monkeypatch):
    captured = {}

    def fake_execute_values(_cur, _sql, rows, page_size):
        captured["rows"] = rows
        captured["page_size"] = page_size

    monkeypatch.setattr("src.analytics.run_daily_pipeline.execute_values", fake_execute_values)
    frame = pd.DataFrame([{
        "ticker": "SAFE", "period_end": date(2026, 6, 30),
        "m1": 0.8, "m2": None, "m3": 0.6, "m2_source": None,
        "m2_score_inputs": None, "ek1": 0.4, "ek4": 0.5, "ek9": 0.6,
        "base_score": None, "final_score": None, "good_count_ge8": 7,
        "decision": "YETERSIZ_VERI", "veto_flag": None,
    }])
    _upsert_module_scores(Conn(), frame, date(2026, 9, 8), 63)
    row = captured["rows"][0]
    assert row[6] == 0.6
    assert row[5] is None
    assert row[7] is None
    assert row[14] is None
    assert row[15] is None
    assert row[17] == "YETERSIZ_VERI"
    assert row[18] is None

