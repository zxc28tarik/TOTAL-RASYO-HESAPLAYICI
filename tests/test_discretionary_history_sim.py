from __future__ import annotations

"""The historical decision sheet must show nothing after its date; orders fill the session after."""

import numpy as np
import pandas as pd
import pytest

import scripts.discretionary_history_sim as sim


@pytest.fixture()
def world(tmp_path, monkeypatch):
    days = [d.date().isoformat() for d in pd.bdate_range("2025-03-03", periods=80)]
    rng = np.random.default_rng(4)
    tickers = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]
    rows = []
    for t in tickers + ["XU100"]:
        path = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, len(days))))
        rows += [{"ticker": t, "trade_date": d, "close": p, "adj_close": p} for d, p in zip(days, path)]
    base = tmp_path / "hist"
    base.mkdir()
    pd.DataFrame(rows).to_csv(base / "prices.csv.gz", index=False)
    pd.DataFrame(columns=["ticker", "date", "kind", "value"]).to_csv(base / "actions.csv", index=False)
    panel = pd.DataFrame([{"ticker": t, "signal_date": "2025-04-01", "decision": "AL" if i < 3 else "UZAK",
                           "total_rasyo_100": 90 + i, "sector_index_code": "XUSIN", "nominal": 1e6,
                           "equity": 1e8 * (i + 1)} for i, t in enumerate(tickers)])
    panel.to_csv(base / "panel.csv.gz", index=False)
    monkeypatch.setattr(sim, "BASE", base)
    monkeypatch.setattr(sim, "SIM", base / "sim")
    monkeypatch.setattr(sim, "LEDGER", base / "sim/ledger.jsonl")
    monkeypatch.setattr(sim, "STATE", base / "sim/state.json")
    monkeypatch.setattr(sim, "MARKS", base / "sim/marks.csv")
    return base, days


def test_the_sheet_is_identical_whatever_happens_after_its_date(world):
    base, days = world
    before = sim.sheet(days[30])
    prices = pd.read_csv(base / "prices.csv.gz")
    prices.loc[prices.trade_date > days[30], "close"] *= 7.0
    prices.to_csv(base / "prices.csv.gz", index=False)
    assert sim.sheet(days[30]) == before


def test_a_decision_on_d_fills_at_the_next_close_and_money_arrives_monthly(world):
    base, days = world
    sim.advance(days[21])                              # 2025-04-01 is the first payment
    sim.order(days[21], "BUY", "AAA", tl=5_000, why="test")
    out = sim.advance(days[25])
    prices = pd.read_csv(base / "prices.csv.gz")
    fill = [e for e in sim._ledger() if e["event"] == "FILL"][0]
    expected = prices.loc[(prices.ticker == "AAA") & (prices.trade_date == days[22]), "close"].iloc[0]
    assert fill["date"] == days[22] and fill["price"] == pytest.approx(expected)
    assert out["last"]["paid_in"] == 15_000.0
