from __future__ import annotations

"""Hold until the price says sell; the random twin must differ in the pick only."""

import numpy as np
import pandas as pd
import pytest

from scripts.backtest_horizon_strategy import PointInTime
from scripts.backtest_trailing_hold import RULES, daily_menus, run

TICKERS = [f"T{i:02d}" for i in range(12)]


def _panel(signals, al=("T00",)):
    rows = []
    for signal in signals:
        for i, ticker in enumerate(TICKERS):
            rows.append({"month": signal.strftime("%Y-%m"), "signal_ts": signal, "ticker": ticker,
                         "score_percentile": 1.0 if ticker in al else (i + 1) / 20,
                         "decision": "AL" if ticker in al else "UZAK",
                         "equity": 1e6, "nominal": 1e4})
    return pd.DataFrame(rows)


def _flat(sessions, level=100.0):
    return pd.DataFrame(level, index=sessions, columns=TICKERS)


def _setup(adj, panel, sessions):
    index = pd.Series(1000.0, index=sessions)
    menus = daily_menus(PointInTime(panel, adj), sessions)
    return menus, index


def test_a_twenty_percent_fall_from_the_peak_sells_and_a_smaller_one_holds():
    sessions = pd.bdate_range("2025-02-03", periods=60)
    adj = _flat(sessions)
    # T00 is the only AL name and the cheapest, so it is bought on day one.
    adj.loc[:, "T00"] = 50.0
    path = [50.0 * (1 + 0.01 * d) for d in range(30)]           # rises to ~64.5
    peak = path[-1]
    path += [peak * 0.85] * 10 + [peak * 0.79] * 20              # -15% holds, -21% sells
    adj["T00"] = path
    panel = _panel([sessions[0]])
    menus, index = _setup(adj, panel, sessions)
    result = run(menus, adj, index, sessions)
    trade = result["trades"][0]
    assert trade["ticker"] == "T00" and trade["exit"] == "IZ_SUREN_STOP"
    # The first -21% close is session 40; the stop acts on the next session.
    assert trade["exit_date"] == str(sessions[41].date())


def test_there_is_no_time_limit_a_rising_stock_is_held_to_the_end():
    sessions = pd.bdate_range("2025-02-03", periods=220)
    adj = _flat(sessions)
    adj["T00"] = [50.0 * (1.002 ** d) for d in range(len(sessions))]
    panel = _panel([s for s in sessions if s.day <= 3 and s.weekday() < 5][:1])
    menus, index = _setup(adj, panel, sessions)
    trades = run(menus, adj, index, sessions)["trades"]
    assert len(trades) == 1 and trades[0]["exit"] == "ACIK"


def test_a_stopped_name_is_not_bought_back_in_the_same_month():
    sessions = pd.bdate_range("2025-02-03", periods=20)
    adj = _flat(sessions)
    adj["T00"] = [50.0] * 5 + [38.0] * 15      # -24% on session 5, stays down
    panel = _panel([sessions[0]])
    menus, index = _setup(adj, panel, sessions)
    trades = run(menus, adj, index, sessions)["trades"]
    assert [t["exit"] for t in trades] == ["IZ_SUREN_STOP"]


def test_the_random_twin_buys_at_the_same_moments_in_the_same_number():
    rng = np.random.default_rng(3)
    sessions = pd.bdate_range("2025-02-03", periods=120)
    adj = pd.DataFrame(100 * np.exp(np.cumsum(rng.normal(0, 0.02, (len(sessions), len(TICKERS))), axis=0)),
                       index=sessions, columns=TICKERS)
    signals = [sessions[0], sessions[40], sessions[80]]
    panel = _panel(signals, al=("T00", "T01", "T02"))
    menus, index = _setup(adj, panel, sessions)
    real = run(menus, adj, index, sessions)
    twin = run(menus, adj, index, sessions, rng=np.random.default_rng(1), buys=real["buys"])
    # Slots never bind here (three AL names, ten slots), so the twin must mirror
    # every buy of the strategy -- not only the first, which the original test
    # checked and which let a twin that bought far more often slip through.
    assert twin["buys"] == real["buys"]
    assert sum(real["buys"].values()) == len(real["trades"])


def test_prices_after_a_date_cannot_change_a_decision_before_it():
    rng = np.random.default_rng(5)
    sessions = pd.bdate_range("2025-02-03", periods=160)
    adj = pd.DataFrame(100 * np.exp(np.cumsum(rng.normal(0, 0.025, (len(sessions), len(TICKERS))), axis=0)),
                       index=sessions, columns=TICKERS)
    panel = _panel([sessions[0], sessions[40], sessions[80], sessions[120]], al=("T00", "T03", "T07"))
    menus, index = _setup(adj, panel, sessions)
    base = run(menus, adj, index, sessions)
    for cut in sessions[15:-5:12]:
        later = adj.index > cut
        shocked_adj = adj.copy()
        shocked_adj.loc[later] = adj.loc[later].to_numpy() * rng.uniform(0.3, 3.0, (later.sum(), adj.shape[1]))
        shocked_menus = daily_menus(PointInTime(panel, shocked_adj), sessions)
        shocked = run(shocked_menus, shocked_adj, index, sessions)
        keep = lambda r: [t for t in r["trades"] if pd.Timestamp(t["exit_date"]) <= cut and t["exit"] != "ACIK"]
        assert keep(base) == keep(shocked), cut
        assert base["curve"].loc[:cut].equals(shocked["curve"].loc[:cut]), cut


def test_the_rules_are_the_brief():
    assert RULES["trailing_stop"] == 0.20
    assert RULES["time_limit"] is None
    assert "AL" in RULES["buy_if"] and "0.50" in RULES["buy_if"]
    assert RULES["window_first_signal"] == "2025-02" and RULES["window_last_signal"] == "2026-07"
    assert RULES["random_draws"] >= 1000
