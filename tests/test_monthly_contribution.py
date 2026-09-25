from __future__ import annotations

"""Monthly payments: money in must equal money accounted for, and it must all be invested."""

import pandas as pd
import pytest

from scripts.backtest_horizon_strategy import PointInTime
from scripts.backtest_monthly_contribution import COST, index_book, strategy_book, xirr
from scripts.backtest_trailing_hold import daily_menus

TICKERS = [f"T{i:02d}" for i in range(12)]


def _world(al=()):
    sessions = pd.bdate_range("2025-02-03", periods=60)
    adj = pd.DataFrame(100.0, index=sessions, columns=TICKERS)
    adj["T00"] = 50.0                                   # cheapest P/B
    rows = [{"month": sessions[0].strftime("%Y-%m"), "signal_ts": sessions[0], "ticker": t,
             "score_percentile": 1.0 if t in al else (i + 1) / 20, "decision": "AL" if t in al else "UZAK",
             "equity": 1e6, "nominal": 1e4} for i, t in enumerate(TICKERS)]
    menus = daily_menus(PointInTime(pd.DataFrame(rows), adj), sessions)
    return menus, adj, pd.Series(1000.0, index=sessions), sessions


def test_with_nothing_to_buy_every_payment_waits_in_xu100():
    menus, adj, index, sessions = _world()
    pays = {sessions[0]: 15_000.0, sessions[21]: 15_000.0}
    book = strategy_book(menus, adj, index, sessions, pays)
    assert book["curve"].iloc[-1] == pytest.approx(30_000.0 * (1 - COST))
    assert index_book(index, sessions, pays).iloc[-1] == pytest.approx(30_000.0 * (1 - COST))


def test_all_waiting_money_goes_into_the_qualifying_name_with_no_cap():
    menus, adj, index, sessions = _world(al=("T00",))
    pays = {sessions[0]: 15_000.0}
    book = strategy_book(menus, adj, index, sessions, pays)
    buys = [t for t in book["trades"] if t["islem"] == "AL"]
    assert [b["hisse"] for b in buys] == ["T00"]
    # Payment enters the sleeve (one cost), then moves to the stock (two more).
    assert book["curve"].iloc[-1] == pytest.approx(15_000.0 * (1 - COST) ** 3)


def test_xirr_recovers_a_known_rate():
    start = pd.Timestamp("2025-01-01")
    assert xirr([(start, -100.0), (start + pd.Timedelta(days=365), 110.0)]) == pytest.approx(0.10, abs=1e-9)
