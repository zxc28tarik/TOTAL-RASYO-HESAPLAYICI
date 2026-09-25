from __future__ import annotations

"""Profit-taking must fire where it says, and the no-profit variant must equal the old engine."""

import numpy as np
import pandas as pd
import pytest

from scripts.backtest_horizon_strategy import PointInTime
from scripts.backtest_take_profit import RULES, VARIANTS, run
from scripts.backtest_trailing_hold import daily_menus, run as hold_run

TICKERS = [f"T{i:02d}" for i in range(12)]


def _panel(signals, al=("T00",)):
    rows = []
    for signal in signals:
        for i, ticker in enumerate(TICKERS):
            rows.append({"month": signal.strftime("%Y-%m"), "signal_ts": signal, "ticker": ticker,
                         "score_percentile": 1.0 if ticker in al else (i + 1) / 20,
                         "decision": "AL" if ticker in al else "UZAK", "equity": 1e6, "nominal": 1e4})
    return pd.DataFrame(rows)


def _single(path):
    sessions = pd.bdate_range("2025-02-03", periods=len(path))
    adj = pd.DataFrame(100.0, index=sessions, columns=TICKERS)
    adj["T00"] = path
    panel = _panel([sessions[0]])
    menus = daily_menus(PointInTime(panel, adj), sessions)
    return menus, adj, pd.Series(1000.0, index=sessions), sessions


def test_half_is_sold_at_thirty_and_the_rest_at_seventy():
    path = [50.0] * 3 + [66.0] * 5 + [86.0] * 5          # +32% then +72%
    menus, adj, index, sessions = _single(path)
    result = run(menus, adj, index, sessions, **VARIANTS["A_yarisi_30_kalani_70"])
    (position,) = result["positions"]
    reasons = [(e["reason"], e["share"]) for e in position["exits"]]
    assert reasons == [("KAR_AL_YARISI", 0.5), ("KAR_AL_TAMAMI", 0.5)]
    # Triggers are judged on the previous close and filled the next session.
    assert position["exits"][0]["date"] == str(sessions[4].date())
    assert position["exits"][1]["date"] == str(sessions[9].date())
    assert position["return"] == pytest.approx(0.5 * 66 / 50 + 0.5 * 86 / 50 - 1.0)


def test_after_the_half_is_taken_the_stop_still_guards_the_rest():
    path = [50.0] * 3 + [66.0] * 5 + [52.0] * 5          # +32%, then -21% from the 66 peak
    menus, adj, index, sessions = _single(path)
    (position,) = run(menus, adj, index, sessions, **VARIANTS["A_yarisi_30_kalani_70"])["positions"]
    assert [e["reason"] for e in position["exits"]] == ["KAR_AL_YARISI", "IZ_SUREN_STOP"]


def test_a_single_threshold_sells_everything_at_once():
    path = [50.0] * 3 + [76.0] * 5
    menus, adj, index, sessions = _single(path)
    (position,) = run(menus, adj, index, sessions, **VARIANTS["C_tamami_50"])["positions"]
    assert [(e["reason"], e["share"]) for e in position["exits"]] == [("KAR_AL_TAMAMI", 1.0)]


def test_without_profit_taking_the_engine_equals_the_trailing_hold_engine():
    rng = np.random.default_rng(11)
    sessions = pd.bdate_range("2025-02-03", periods=200)
    adj = pd.DataFrame(100 * np.exp(np.cumsum(rng.normal(0.001, 0.025, (len(sessions), len(TICKERS))), axis=0)),
                       index=sessions, columns=TICKERS)
    panel = _panel([sessions[0], sessions[60], sessions[120]], al=("T00", "T04", "T09"))
    menus = daily_menus(PointInTime(panel, adj), sessions)
    index = pd.Series(1000.0, index=sessions)
    new = run(menus, adj, index, sessions, **VARIANTS["F_kar_almadan"])
    old = hold_run(menus, adj, index, sessions)
    assert new["curve"].to_numpy() == pytest.approx(old["curve"].to_numpy(), rel=1e-12)
    assert len(new["positions"]) == len(old["trades"])


def test_the_twin_mirrors_the_buys_of_its_own_variant():
    rng = np.random.default_rng(12)
    sessions = pd.bdate_range("2025-02-03", periods=150)
    adj = pd.DataFrame(100 * np.exp(np.cumsum(rng.normal(0.001, 0.02, (len(sessions), len(TICKERS))), axis=0)),
                       index=sessions, columns=TICKERS)
    panel = _panel([sessions[0], sessions[50], sessions[100]], al=("T01", "T02"))
    menus = daily_menus(PointInTime(panel, adj), sessions)
    index = pd.Series(1000.0, index=sessions)
    for policy in VARIANTS.values():
        real = run(menus, adj, index, sessions, **policy)
        twin = run(menus, adj, index, sessions, **policy, rng=np.random.default_rng(0), buys=real["buys"])
        assert twin["buys"] == real["buys"]


def test_prices_after_a_date_cannot_change_a_decision_before_it():
    rng = np.random.default_rng(13)
    sessions = pd.bdate_range("2025-02-03", periods=150)
    adj = pd.DataFrame(100 * np.exp(np.cumsum(rng.normal(0.002, 0.03, (len(sessions), len(TICKERS))), axis=0)),
                       index=sessions, columns=TICKERS)
    panel = _panel([sessions[0], sessions[50], sessions[100]], al=("T00", "T05", "T08"))
    index = pd.Series(1000.0, index=sessions)
    policy = VARIANTS["A_yarisi_30_kalani_70"]
    base = run(daily_menus(PointInTime(panel, adj), sessions), adj, index, sessions, **policy)
    for cut in sessions[15:-5:12]:
        later = adj.index > cut
        shocked = adj.copy()
        shocked.loc[later] = adj.loc[later].to_numpy() * rng.uniform(0.3, 3.0, (later.sum(), adj.shape[1]))
        other = run(daily_menus(PointInTime(panel, shocked), sessions), shocked, index, sessions, **policy)
        assert base["curve"].loc[:cut].equals(other["curve"].loc[:cut]), cut
        # Positions are listed in closing order, and closings after the cut may
        # legitimately differ, so compare the early history as a sorted set.
        early = lambda r: sorted((p["ticker"], p["entry_date"],
                                  str([e for e in p["exits"] if e["date"] <= str(cut.date())]))
                                 for p in r["positions"] if p["entry_date"] <= str(cut.date()))
        assert early(base) == early(other), cut


def test_the_rules_take_their_numbers_from_the_brief_and_report_every_variant():
    assert RULES["primary_variant"] == "A_yarisi_30_kalani_70"
    assert VARIANTS["A_yarisi_30_kalani_70"] == {"half_at": 0.30, "all_at": 0.70}
    assert set(VARIANTS) == {"A_yarisi_30_kalani_70", "B_tamami_30", "C_tamami_50", "D_tamami_70",
                             "E_tamami_100", "F_kar_almadan"}
    assert RULES["every_variant_reported"] is True


def test_a_dust_position_left_by_an_exhausted_sleeve_is_held_not_crashed_on():
    # With every name AL and the sleeve spent, a buy can be funded with a
    # rounding residue. The first version took "units near zero" to mean "sold
    # out" and then read a sale record that did not exist; seed 14 reproduces it.
    names = [f"T{i:02d}" for i in range(14)]
    rng = np.random.default_rng(14)
    sessions = pd.bdate_range("2025-02-03", periods=160)
    adj = pd.DataFrame(100 * np.exp(np.cumsum(rng.normal(0.001, 0.03, (len(sessions), len(names))), axis=0)),
                       index=sessions, columns=names)
    rows = [{"month": s.strftime("%Y-%m"), "signal_ts": s, "ticker": t, "score_percentile": (i + 1) / len(names),
             "decision": "AL", "equity": 1e6, "nominal": 1e4}
            for s in (sessions[0], sessions[40], sessions[80], sessions[120]) for i, t in enumerate(names)]
    menus = daily_menus(PointInTime(pd.DataFrame(rows), adj), sessions)
    index = pd.Series(1000.0, index=sessions)
    policy = VARIANTS["F_kar_almadan"]
    real = run(menus, adj, index, sessions, **policy)
    for draw in range(20):
        twin = run(menus, adj, index, sessions, **policy, rng=np.random.default_rng(draw), buys=real["buys"])
        assert all(p["exits"] for p in twin["positions"])
