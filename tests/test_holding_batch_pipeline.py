from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from src.analytics.holding_batch_pipeline import (
    HoldingBatchError,
    build_holding_snapshots_from_frames,
    fetch_holding_universe,
    persist_holding_batch,
    run_holding_batch,
)
from src.analytics.holding_valuation import HoldingValuationConfig
from src.ingest.sector_routing import SectorRoutingConfig

TZ = ZoneInfo("Europe/Istanbul")
ANALYSIS = datetime(2026, 8, 5, 19, 0, tzinfo=TZ)
SHA = "a" * 64


def cfg():
    return HoldingValuationConfig.from_dict({
        "valuation_profile": "TEST_HOLDING_NAV",
        "valuation_version": 1,
        "source_nav_profile": "HOLDING_ADJUSTED_NAV",
        "source_nav_version": 1,
        "share_basis": "ADJUSTED_PRICE_SERIES_V1",
        "minimum_peer_count": 3,
        "full_confidence_peer_count": 5,
    })


def frames():
    tickers = ["AAA", "BBB", "CCC", "DDD"]
    universe = pd.DataFrame({
        "ticker": tickers,
        "peer_group": ["XHOLD"] * 4,
        "sector_family": ["HOLDING"] * 4,
    })
    navs = pd.DataFrame({
        "ticker": tickers,
        "nav_asof_date": [date(2026, 6, 30)] * 4,
        "nav_published_at": [datetime(2026, 7, 20, 10, 0, tzinfo=TZ)] * 4,
        "nav_total": [1000.0] * 4,
        "shares_out": [100.0] * 4,
        "currency": ["TRY"] * 4,
        "share_basis": ["ADJUSTED_PRICE_SERIES_V1"] * 4,
        "source_confidence": [0.9] * 4,
        "source_document_id": [f"DOC-{t}" for t in tickers],
        "source_sha256": [SHA] * 4,
        "nav_profile": ["HOLDING_ADJUSTED_NAV"] * 4,
        "nav_version": [1] * 4,
    })
    prices = pd.DataFrame({
        "ticker": tickers,
        "price_trade_date": [date(2026, 8, 5)] * 4,
        "current_price": [4.0, 5.0, 6.0, 7.0],
    })
    follow = pd.DataFrame({
        "ticker": tickers,
        "follow_score": [0.5, 0.5, 0.5, 0.5],
        "follow_active": [True, True, True, True],
    })
    return universe, navs, prices, follow


def test_build_snapshots_and_rejections_are_deterministic():
    universe, navs, prices, _ = frames()
    snapshots, rejected = build_holding_snapshots_from_frames(
        universe=universe, navs=navs, prices=prices, analysis_at=ANALYSIS,
    )
    assert [s.ticker for s in snapshots] == ["AAA", "BBB", "CCC", "DDD"]
    assert rejected == []

    snapshots2, rejected2 = build_holding_snapshots_from_frames(
        universe=universe,
        navs=navs[navs["ticker"] != "AAA"],
        prices=prices[prices["ticker"] != "BBB"],
        analysis_at=ANALYSIS,
    )
    assert [s.ticker for s in snapshots2] == ["CCC", "DDD"]
    assert rejected2 == [
        {"ticker": "AAA", "reason": "NAV_YOK"},
        {"ticker": "BBB", "reason": "FIYAT_YOK"},
    ]


def test_build_snapshots_rejects_duplicate_and_wrong_family():
    universe, navs, prices, _ = frames()
    dup = pd.concat([navs, navs.iloc[[0]]], ignore_index=True)
    with pytest.raises(HoldingBatchError, match="yinelenen"):
        build_holding_snapshots_from_frames(
            universe=universe, navs=dup, prices=prices, analysis_at=ANALYSIS,
        )
    wrong = universe.copy()
    wrong.loc[0, "sector_family"] = "NONFIN"
    with pytest.raises(HoldingBatchError, match="beklenmeyen aile"):
        build_holding_snapshots_from_frames(
            universe=wrong, navs=navs, prices=prices, analysis_at=ANALYSIS,
        )


def test_fetch_holding_universe_routes_only_holdings(monkeypatch):
    raw = pd.DataFrame([
        {"ticker": "KCHOL", "sector_index_code": "XHOLD", "sector_code": None},
        {"ticker": "GARAN", "sector_index_code": "XBANK", "sector_code": None},
        {"ticker": "HOLDX", "sector_index_code": "XUSIN", "sector_code": "HOLDING"},
    ])
    monkeypatch.setattr(pd, "read_sql", lambda *a, **k: raw)
    out = fetch_holding_universe(object(), routing_config=SectorRoutingConfig.default())
    assert out.to_dict("records") == [
        {"ticker": "KCHOL", "peer_group": "XHOLD", "sector_family": "HOLDING"},
        {"ticker": "HOLDX", "peer_group": "XUSIN", "sector_family": "HOLDING"},
    ]


class Cur:
    def __init__(self): self.calls = []
    def execute(self, sql, params): self.calls.append((sql, params))
    def __enter__(self): return self
    def __exit__(self, *args): return False


class Conn:
    def __init__(self): self.cur = Cur()
    def cursor(self): return self.cur
    def __enter__(self): return self
    def __exit__(self, *args): return False


def report():
    universe, navs, prices, follow = frames()
    snapshots, _ = build_holding_snapshots_from_frames(
        universe=universe, navs=navs, prices=prices, analysis_at=ANALYSIS,
    )
    from src.analytics.holding_valuation import evaluate_holding_batch
    out = evaluate_holding_batch(
        snapshots, config=cfg(),
        follow_contexts={r["ticker"]: {"follow_score": 0.5, "follow_active": True} for r in follow.to_dict("records")},
    )
    out.update({"analysis_at": ANALYSIS, "rejections": []})
    return out


def test_persist_validates_before_transaction(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "src.analytics.holding_batch_pipeline.execute_values",
        lambda cur, sql, rows, page_size: calls.append((sql, rows)),
    )
    persist_holding_batch(Conn(), report())
    assert len(calls) == 2
    assert "holding_valuation_periods" in calls[0][0]
    assert "holding_m2_scores" in calls[1][0]

    broken = report()
    del broken["results"][0]["valuation"]["source_sha256"]
    with pytest.raises(HoldingBatchError, match="eksik alanlar"):
        persist_holding_batch(Conn(), broken)


def test_persist_rejects_mismatched_m2_contract():
    broken = report()
    broken["results"][0]["m2"]["ticker"] = "OTHER"
    with pytest.raises(HoldingBatchError, match="ticker uyusmuyor"):
        persist_holding_batch(Conn(), broken)


def test_run_holding_batch_uses_frames_and_can_skip_persistence(monkeypatch):
    universe, navs, prices, follow = frames()
    monkeypatch.setattr("src.analytics.holding_batch_pipeline.fetch_holding_universe", lambda *a, **k: universe)
    monkeypatch.setattr("src.analytics.holding_batch_pipeline.fetch_holding_navs_asof", lambda *a, **k: navs)
    monkeypatch.setattr("src.analytics.holding_batch_pipeline.fetch_holding_prices", lambda *a, **k: prices)
    monkeypatch.setattr("src.analytics.holding_batch_pipeline.fetch_holding_follow_contexts", lambda *a, **k: follow)
    report_out = run_holding_batch(object(), analysis_at=ANALYSIS, config=cfg(), persist=False)
    assert report_out["result_count"] == 4
    assert report_out["rejections"] == []
    assert all(row["m2"]["m2_source"] == "HOLDING_NAV_DISCOUNT_TWO_AXIS_V1" for row in report_out["results"])


def test_run_holding_batch_rejects_non_bool_persist():
    with pytest.raises(HoldingBatchError, match="Python bool"):
        run_holding_batch(object(), analysis_at=ANALYSIS, config=cfg(), persist=1)


def test_build_snapshots_rejects_naive_nav_publication():
    universe, navs, prices, _ = frames()
    navs = navs.copy()
    navs["nav_published_at"] = navs["nav_published_at"].astype(object)
    navs.at[0, "nav_published_at"] = datetime(2026, 7, 20, 10, 0)
    snapshots, rejected = build_holding_snapshots_from_frames(
        universe=universe, navs=navs, prices=prices, analysis_at=ANALYSIS,
    )
    assert [row.ticker for row in snapshots] == ["BBB", "CCC", "DDD"]
    assert rejected == [{"ticker": "AAA", "reason": "nav_published_at timezone iceren datetime olmali"}]


def test_run_revalidates_direct_config_before_database(monkeypatch):
    valid = cfg()
    broken = HoldingValuationConfig(**{**valid.__dict__, "minimum_peer_count": 0})
    monkeypatch.setattr(
        "src.analytics.holding_batch_pipeline.fetch_holding_universe",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("DB touched")),
    )
    with pytest.raises(Exception, match="minimum_peer_count"):
        run_holding_batch(object(), analysis_at=ANALYSIS, config=broken, persist=False)


def test_run_holding_batch_keeps_rejected_ticker_context_from_breaking_batch(monkeypatch):
    universe, navs, prices, follow = frames()
    navs = navs[navs["ticker"] != "AAA"].copy()
    monkeypatch.setattr("src.analytics.holding_batch_pipeline.fetch_holding_universe", lambda *a, **k: universe)
    monkeypatch.setattr("src.analytics.holding_batch_pipeline.fetch_holding_navs_asof", lambda *a, **k: navs)
    monkeypatch.setattr("src.analytics.holding_batch_pipeline.fetch_holding_prices", lambda *a, **k: prices)
    monkeypatch.setattr("src.analytics.holding_batch_pipeline.fetch_holding_follow_contexts", lambda *a, **k: follow)
    out = run_holding_batch(object(), analysis_at=ANALYSIS, config=cfg(), persist=False)
    assert out["result_count"] == 3
    assert out["rejections"] == [{"ticker": "AAA", "reason": "NAV_YOK"}]
    assert {row["ticker"] for row in out["results"]} == {"BBB", "CCC", "DDD"}


def test_persist_records_rejections_and_clears_successes(monkeypatch):
    captured = []
    monkeypatch.setattr(
        "src.analytics.holding_batch_pipeline.execute_values",
        lambda cur, sql, rows, page_size: captured.append((sql, rows)),
    )
    rep = report()
    removed = rep["results"].pop()
    rep["result_count"] = len(rep["results"])
    rep["rejections"] = [{"ticker": removed["ticker"], "reason": "NAV_YOK"}]
    conn = Conn()
    persist_holding_batch(conn, rep)
    sqls = [" ".join(sql.split()) for sql, _ in conn.cur.calls]
    assert any("DELETE FROM analytics.holding_valuation_rejections" in sql for sql in sqls)
    assert any("INSERT INTO analytics.holding_valuation_rejections" in sql for sql in sqls)
    assert len(captured) == 2


def test_persist_rejects_ticker_in_result_and_rejection():
    rep = report()
    rep["rejections"] = [{"ticker": rep["results"][0]["ticker"], "reason": "BAD"}]
    with pytest.raises(HoldingBatchError, match="hem sonuc hem rejection"):
        persist_holding_batch(Conn(), rep)


def test_persist_rerun_is_authoritative_for_successes_and_rejections(monkeypatch):
    captured = []
    monkeypatch.setattr(
        "src.analytics.holding_batch_pipeline.execute_values",
        lambda cur, sql, rows, page_size: captured.append((sql, rows)),
    )
    rep = report()
    rejected_ticker = rep["results"].pop()["ticker"]
    rep["result_count"] = len(rep["results"])
    rep["rejections"] = [{"ticker": rejected_ticker, "reason": "NAV_YOK"}]
    conn = Conn()
    persist_holding_batch(conn, rep)
    sqls = [sql for sql, _ in conn.cur.calls]
    assert any("DELETE FROM analytics.holding_valuation_periods" in sql for sql in sqls)
    assert any("DELETE FROM analytics.holding_m2_scores" in sql for sql in sqls)
    delete_valuation = next(params for sql, params in conn.cur.calls if "DELETE FROM analytics.holding_valuation_periods" in sql)
    assert set(delete_valuation[-1]) == {"AAA", "BBB", "CCC", "DDD"}
    assert any("INSERT INTO analytics.holding_valuation_rejections" in sql for sql in sqls)
    assert len(captured) == 2


def test_persist_rejects_top_level_lineage_mismatch_before_transaction():
    rep = report()
    rep["config_sha256"] = "b" * 64
    conn = Conn()
    with pytest.raises(HoldingBatchError, match="config_sha256 report ile uyusmuyor"):
        persist_holding_batch(conn, rep)
    assert conn.cur.calls == []


def test_persist_rejects_duplicate_success_and_noncanonical_json():
    rep = report()
    rep["results"].append(rep["results"][0])
    rep["result_count"] = len(rep["results"])
    with pytest.raises(HoldingBatchError, match="yinelenen result ticker"):
        persist_holding_batch(Conn(), rep)

    rep = report()
    rep["results"][0]["valuation"]["diagnostics"] = {"bad": {1, 2}}
    with pytest.raises(HoldingBatchError, match="kanonik JSON"):
        persist_holding_batch(Conn(), rep)


def test_persist_rejects_zero_positive_fields_before_transaction():
    rep = report()
    rep["results"][0]["valuation"]["current_price"] = 0
    conn = Conn()
    with pytest.raises(HoldingBatchError, match="current_price"):
        persist_holding_batch(conn, rep)
    assert conn.cur.calls == []
