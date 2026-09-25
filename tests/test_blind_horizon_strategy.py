from __future__ import annotations

"""The horizon strategy must be blind to the future, and its sizing must do what it says."""

import numpy as np
import pandas as pd
import pytest

from scripts.backtest_horizon_strategy import (
    RULES, PointInTime, phantom_moves, rescale_for_phantoms, run_strategy,
)

TICKERS = [f"T{i:02d}" for i in range(12)]


def _world(seed: int = 7):
    rng = np.random.default_rng(seed)
    sessions = pd.bdate_range("2024-01-01", "2024-08-30")
    steps = rng.normal(0.0005, 0.02, size=(len(sessions), len(TICKERS)))
    adj = pd.DataFrame(100 * np.exp(np.cumsum(steps, axis=0)), index=sessions, columns=TICKERS)
    closes = adj.copy()
    index = pd.Series(1000 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, len(sessions)))), index=sessions)
    signals = [sessions[sessions >= pd.Timestamp(f"2024-{m:02d}-01")][0] for m in range(1, 9)]
    rows = []
    for signal in signals:
        order = rng.permutation(len(TICKERS))
        for rank, i in enumerate(order):
            percentile = (rank + 1) / len(TICKERS)
            rows.append({"month": signal.strftime("%Y-%m"), "signal_ts": signal, "ticker": TICKERS[i],
                         "score_percentile": percentile,
                         "decision": "AL" if percentile > 0.75 else ("IZLE" if percentile > 0.5 else "UZAK"),
                         "equity": 1e6 * (1 + i), "nominal": 1e4})
    return pd.DataFrame(rows), adj, closes, index, sessions


def _run(panel, adj, closes, index, sessions, horizon=3):
    return run_strategy(PointInTime(panel, closes), adj, index, horizon_months=horizon, sessions=sessions)


def test_changing_every_price_after_a_date_changes_no_decision_before_it():
    panel, adj, closes, index, sessions = _world()
    base = _run(panel, adj, closes, index, sessions)
    rng = np.random.default_rng(99)
    # Many cut points, because a leak only shows at a cut that falls inside the
    # window it reaches forward; one cut can miss a week-long leak entirely.
    cuts = sessions[20:-5:10]
    for cut in cuts:
        later = adj.index > cut
        shock = rng.uniform(0.3, 3.0, size=(later.sum(), adj.shape[1]))
        adj2, closes2 = adj.copy(), closes.copy()
        adj2.loc[later] = adj.loc[later].to_numpy() * shock
        closes2.loc[later] = closes.loc[later].to_numpy() * shock
        index2 = index.copy()
        index2.loc[later] = index.loc[later] * rng.uniform(0.5, 2.0, later.sum())
        shocked = _run(panel, adj2, closes2, index2, sessions)

        before = [e for e in base["events"] if pd.Timestamp(e["date"]) <= cut]
        assert before == [e for e in shocked["events"] if pd.Timestamp(e["date"]) <= cut], cut
        assert base["curve"].loc[:cut].equals(shocked["curve"].loc[:cut]), cut
    assert any(pd.Timestamp(e["date"]) <= cuts[0] for e in base["events"])


def test_the_view_never_hands_out_todays_close_or_a_month_not_yet_signalled():
    panel, adj, closes, index, sessions = _world()
    view = PointInTime(panel, closes)
    day = pd.Timestamp("2024-03-13")
    seen = view.closes_before(day)
    assert seen.equals(closes.loc[closes.index < day].iloc[-1])
    first_signal = panel.signal_ts.min()
    assert view.cohort(first_signal - pd.Timedelta(days=1)).empty
    assert view.month(pd.Timestamp("2024-03-01")) == "2024-03"
    assert view.month(pd.Timestamp("2024-02-29")) == "2024-02"


def test_each_idea_gets_one_slot_so_few_ideas_mean_a_small_stock_weight():
    panel, adj, closes, index, sessions = _world()
    run = _run(panel, adj, closes, index, sessions)
    curve = run["curve"]
    assert curve.positions.max() <= RULES["max_positions"]
    # Slot size is a tenth of the book at purchase, so weight tracks the count.
    for positions, weight in zip(curve.positions, curve.stock_weight):
        if positions:
            assert weight < positions * 0.1 * 2.0


def test_every_trade_is_bought_as_a_qualifier_and_sold_for_a_stated_reason():
    panel, adj, closes, index, sessions = _world()
    run = _run(panel, adj, closes, index, sessions)
    buys = [e for e in run["events"] if e["action"] == "AL"]
    assert buys
    assert all(e["pb_pct"] <= 0.5 for e in buys)
    reasons = {t["reason"] for t in run["trades"]}
    assert reasons <= {"SKOR_ZAYIFLADI", "PDDD_PAHALANDI", "HEDEF_SURE_DOLDU", "FIYAT_YOK", "ACIK_POZISYON"}


def test_a_longer_target_holds_longer():
    panel, adj, closes, index, sessions = _world()
    short = pd.DataFrame(_run(panel, adj, closes, index, sessions, horizon=1)["trades"])
    long = pd.DataFrame(_run(panel, adj, closes, index, sessions, horizon=6)["trades"])
    held = lambda t: (pd.to_datetime(t.exit_date) - pd.to_datetime(t.entry_date)).dt.days.mean()
    assert held(long) >= held(short)


def test_a_limit_breaking_drop_is_a_phantom_and_a_limit_sized_one_is_not():
    calendar = pd.DatetimeIndex(pd.bdate_range("2024-01-01", periods=6))
    frame = pd.DataFrame({
        "ticker": ["SPLIT"] * 6 + ["REAL"] * 6,
        "trade_date": list(calendar) * 2,
        "adj_close": [10, 10, 5, 5, 5, 5, 10, 10, 8.5, 8.5, 8.5, 8.5],
    })
    events = phantom_moves(frame, calendar)
    assert list(events.ticker) == ["SPLIT"]
    pivot = frame.pivot(index="trade_date", columns="ticker", values="adj_close").astype(float)
    fixed = rescale_for_phantoms(pivot, events)
    assert fixed["SPLIT"].pct_change().abs().max() == pytest.approx(0.0)
    assert fixed["REAL"].equals(pivot["REAL"])


def test_a_reopening_after_a_long_closure_is_allowed_a_multi_session_move():
    calendar = pd.DatetimeIndex([pd.Timestamp("2023-02-06"), pd.Timestamp("2023-02-07"),
                                 pd.Timestamp("2023-02-15"), pd.Timestamp("2023-02-16")])
    frame = pd.DataFrame({"ticker": ["X"] * 4, "trade_date": calendar, "adj_close": [10.0, 10.0, 13.0, 13.0]})
    assert phantom_moves(frame, calendar).empty


def test_the_rules_are_the_ones_the_brief_asked_for():
    assert RULES["horizons_months"] == [1, 3, 6]
    assert RULES["max_positions"] == 10
    assert "AL" in RULES["buy_if"] and "0.50" in RULES["buy_if"]
    assert RULES["idle_money"] == "held in XU100"
    assert RULES["unseen_period"] == ["2021-08-01", "2025-07-31"]
