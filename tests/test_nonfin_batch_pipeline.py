from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from price_level_fixtures import certify_frames, SHARE_BASIS

from src.analytics.nonfin_batch_pipeline import (
    NonfinBatchError,
    build_nonfin_snapshots_from_frames,
    fetch_nonfin_universe,
    persist_nonfin_batch,
)
from src.analytics.nonfin_valuation import NonfinValuationConfig, evaluate_nonfin_batch

TZ = ZoneInfo("Europe/Istanbul")
ANALYSIS = datetime(2026, 8, 5, 19, 0, tzinfo=TZ)
PERIODS = [date(2025, 9, 30), date(2025, 12, 31), date(2026, 3, 31), date(2026, 6, 30)]


def cfg():
    return NonfinValuationConfig.from_dict({
        "valuation_profile": "T", "valuation_version": 1,
        "source_derivation_profile": "DERIVE_T", "source_derivation_version": 1,
        "multiple_weights": {"PE": 0.3, "EV_EBIT": 0.3, "PS": 0.2, "PB": 0.2},
        "minimum_peer_count": 2, "full_confidence_peer_count": 3,
    })


def frames():
    universe = pd.DataFrame([
        {"ticker": ticker, "peer_group": "XUSIN", "sector_family": "NONFIN"}
        for ticker in ("AAA", "BBB", "CCC")
    ])
    rows = []
    for t_idx, ticker in enumerate(("AAA", "BBB", "CCC"), start=1):
        for q_idx, period in enumerate(PERIODS, start=1):
            rows.append({
                "ticker": ticker, "period_end": period, "version_tag": "ORIGINAL",
                "revenue": 100 * t_idx + q_idx, "ebit": 15 * t_idx + q_idx,
                "net_income": 10 * t_idx + q_idx, "total_equity": 300 * t_idx,
                "debt_st": 20, "debt_lt": 40, "cash_and_eq": 25,
                "st_investments": 5, "shares_out": 100,
            })
    financials = pd.DataFrame(rows)
    prices = pd.DataFrame([
        {"ticker": "AAA", "price_trade_date": date(2026, 8, 5), "current_price": 7.0},
        {"ticker": "BBB", "price_trade_date": date(2026, 8, 5), "current_price": 9.0},
        {"ticker": "CCC", "price_trade_date": date(2026, 8, 5), "current_price": 11.0},
    ])
    certify_frames(financials, prices, ANALYSIS, "period_end")
    return universe, financials, prices


def test_build_snapshots_and_batch_from_frames():
    universe, financials, prices = frames()
    snapshots, rejections = build_nonfin_snapshots_from_frames(
        universe=universe, financials=financials, prices=prices,
        analysis_at=ANALYSIS, anchor_period_end=PERIODS[-1],
    )
    assert len(snapshots) == 3
    assert rejections == []
    report = evaluate_nonfin_batch(snapshots, config=cfg())
    assert report["result_count"] == 3
    assert all(row["valuation"]["status"] == "OK" for row in report["results"])


def test_missing_price_is_isolated_as_rejection():
    universe, financials, prices = frames()
    prices = prices[prices["ticker"] != "BBB"]
    snapshots, rejections = build_nonfin_snapshots_from_frames(
        universe=universe, financials=financials, prices=prices,
        analysis_at=ANALYSIS, anchor_period_end=PERIODS[-1],
    )
    assert [item.ticker for item in snapshots] == ["AAA", "CCC"]
    assert rejections == [{"ticker": "BBB", "reason": "FIYAT_YOK"}]


def test_missing_quarter_is_not_compressed():
    universe, financials, prices = frames()
    financials = financials[~((financials["ticker"] == "AAA") & (financials["period_end"] == PERIODS[1]))]
    snapshots, rejections = build_nonfin_snapshots_from_frames(
        universe=universe, financials=financials, prices=prices,
        analysis_at=ANALYSIS, anchor_period_end=PERIODS[-1],
    )
    assert "AAA" not in [item.ticker for item in snapshots]
    assert any(item["ticker"] == "AAA" and ("bitisik" in item["reason"] or "tam dort" in item["reason"]) for item in rejections)


def test_fetch_universe_routes_only_nonfin(monkeypatch):
    monkeypatch.setattr(pd, "read_sql", lambda *args, **kwargs: pd.DataFrame([
        {"ticker": "AAA", "sector_index_code": "XUSIN", "sector_code": None},
        {"ticker": "DETA", "sector_index_code": "XUSIN", "sector_code": "TECHNOLOGY"},
        {"ticker": "BANK", "sector_index_code": "XBANK", "sector_code": "BANK"},
        {"ticker": "GYO", "sector_index_code": "XGMYO", "sector_code": "GYO"},
    ]))
    out = fetch_nonfin_universe(object())
    assert out.to_dict("records") == [
        {"ticker": "AAA", "peer_group": "XUSIN", "sector_family": "NONFIN"},
        {"ticker": "DETA", "peer_group": "TECHNOLOGY", "sector_family": "NONFIN"},
    ]


class Cursor:
    def __init__(self):
        self.calls = []
    def __enter__(self): return self
    def __exit__(self, *_): return False


class Conn:
    def __init__(self): self.cur = Cursor()
    def cursor(self): return self.cur
    def __enter__(self): return self
    def __exit__(self, *_): return False


def test_persistence_writes_valuation_and_m2(monkeypatch):
    universe, financials, prices = frames()
    snapshots, _ = build_nonfin_snapshots_from_frames(
        universe=universe, financials=financials, prices=prices,
        analysis_at=ANALYSIS, anchor_period_end=PERIODS[-1],
    )
    report = evaluate_nonfin_batch(snapshots, config=cfg())
    captured = []
    monkeypatch.setattr(
        "src.analytics.nonfin_batch_pipeline.execute_values",
        lambda cur, sql, rows, page_size: captured.append((" ".join(sql.split()), rows)),
    )
    persist_nonfin_batch(Conn(), report)
    assert len(captured) == 2
    assert "analytics.nonfin_valuation_periods" in captured[0][0]
    assert "analytics.nonfin_m2_scores" in captured[1][0]
    assert len(captured[0][1]) == 3


def test_persistence_rejects_broken_report_before_db_write(monkeypatch):
    called = False
    def fake(*args, **kwargs):
        nonlocal called
        called = True
    monkeypatch.setattr("src.analytics.nonfin_batch_pipeline.execute_values", fake)
    with pytest.raises(NonfinBatchError, match="valuation/m2"):
        persist_nonfin_batch(Conn(), {"results": [{"ticker": "AAA"}]})
    assert called is False


def test_run_batch_filters_exact_derivation_profile(monkeypatch):
    from src.analytics.nonfin_batch_pipeline import run_nonfin_batch
    universe, financials, prices = frames()
    universe = universe.iloc[:1].copy()
    wanted = financials[financials["ticker"] == "AAA"].copy()
    wanted["derivation_profile"] = "DERIVE_T"
    wanted["derivation_version"] = 1
    wrong = wanted.copy()
    wrong["derivation_profile"] = "OTHER"
    wrong["revenue"] = 999999
    combined = pd.concat([wanted, wrong], ignore_index=True)
    follow = pd.DataFrame(columns=["ticker", "follow_score", "follow_active"])
    monkeypatch.setattr("src.analytics.nonfin_batch_pipeline.fetch_nonfin_universe", lambda *a, **k: universe)
    monkeypatch.setattr("src.analytics.nonfin_batch_pipeline.fetch_company_financials_asof", lambda *a, **k: combined)
    monkeypatch.setattr("src.analytics.nonfin_batch_pipeline.fetch_nonfin_prices", lambda *a, **k: prices.iloc[:1])
    monkeypatch.setattr("src.analytics.nonfin_batch_pipeline.fetch_nonfin_follow_contexts", lambda *a, **k: follow)
    report = run_nonfin_batch(
        object(), analysis_at=ANALYSIS, config=cfg(),
        anchor_period_end=PERIODS[-1], persist=False,
    )
    target = report["results"][0]["valuation"]["diagnostics"]["target_multiples"]
    assert target["PS"] == pytest.approx(700.0 / 410.0)


def test_default_batch_uses_per_ticker_latest_anchor_instead_of_global_max():
    universe, financials, prices = frames()
    # BBB has not reported 2026-Q2 yet; it should still produce a 2026-Q1 snapshot
    # instead of being rejected because AAA/CCC reported Q2.
    financials = financials[~((financials["ticker"] == "BBB") & (financials["period_end"] == PERIODS[-1]))]
    earlier = financials[(financials["ticker"] == "BBB") & (financials["period_end"] == PERIODS[0])].copy()
    earlier["period_end"] = date(2025, 6, 30)
    financials = pd.concat([financials, earlier], ignore_index=True)
    certify_frames(financials, prices, ANALYSIS, "period_end")
    snapshots, rejections = build_nonfin_snapshots_from_frames(
        universe=universe, financials=financials, prices=prices,
        analysis_at=ANALYSIS, anchor_period_end=None,
    )
    anchors = {item.ticker: item.anchor_period_end for item in snapshots}
    assert anchors == {"AAA": PERIODS[-1], "BBB": PERIODS[-2], "CCC": PERIODS[-1]}
    assert rejections == []
