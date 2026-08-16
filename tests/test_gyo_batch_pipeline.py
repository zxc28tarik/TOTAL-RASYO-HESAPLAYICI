from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from src.analytics.gyo_batch_pipeline import (
    GyoBatchError,
    build_gyo_snapshots_from_frames,
    fetch_gyo_universe,
    persist_gyo_batch,
    run_gyo_batch,
)
from src.analytics.gyo_valuation import GyoValuationConfig, evaluate_gyo_batch
from src.ingest.sector_routing import SectorRoutingConfig

TZ = ZoneInfo("Europe/Istanbul")
ANALYSIS = datetime(2026, 8, 5, 19, 0, tzinfo=TZ)
SHA = "c" * 64


def cfg():
    return GyoValuationConfig.from_dict({
        "valuation_profile": "GYO_PD_NAV", "valuation_version": 1,
        "source_nav_profile": "GYO_REPORTED_NAV", "source_nav_version": 1,
        "share_basis": "ADJUSTED_PRICE_SERIES_V1",
        "minimum_peer_count": 3, "full_confidence_peer_count": 5,
    })


def frames():
    tickers = ["A", "B", "C", "D"]
    universe = pd.DataFrame({"ticker": tickers, "peer_group": ["GYO_MIXED"] * 4, "sector_family": ["GYO"] * 4})
    navs = pd.DataFrame({
        "ticker": tickers,
        "nav_asof_date": [date(2026, 6, 30)] * 4,
        "nav_published_at": [datetime(2026, 7, 20, 10, 0, tzinfo=TZ)] * 4,
        "nav_total": [1000.0] * 4,
        "shares_out": [100.0] * 4,
        "share_basis": ["ADJUSTED_PRICE_SERIES_V1"] * 4,
        "currency": ["TRY"] * 4,
        "property_portfolio_value": [1300.0] * 4,
        "nav_source_method": ["DIRECT"] * 4,
        "source_confidence": [0.9] * 4,
        "source_document_id": [f"DOC-{x}" for x in tickers],
        "source_sha256": [SHA] * 4,
        "nav_profile": ["GYO_REPORTED_NAV"] * 4,
        "nav_version": [1] * 4,
    })
    prices = pd.DataFrame({
        "ticker": tickers,
        "price_trade_date": [date(2026, 8, 5)] * 4,
        "current_price": [4.0, 5.0, 6.0, 7.0],
    })
    follow = pd.DataFrame({"ticker": tickers, "follow_score": [0.5] * 4, "follow_active": [True] * 4})
    return universe, navs, prices, follow


def test_build_snapshots_and_rejections():
    universe, navs, prices, _ = frames()
    snaps, rejected = build_gyo_snapshots_from_frames(universe=universe, navs=navs, prices=prices, analysis_at=ANALYSIS)
    assert [x.ticker for x in snaps] == ["A", "B", "C", "D"]
    assert rejected == []
    snaps, rejected = build_gyo_snapshots_from_frames(
        universe=universe,
        navs=navs[navs.ticker != "A"],
        prices=prices[prices.ticker != "B"],
        analysis_at=ANALYSIS,
    )
    assert [x.ticker for x in snaps] == ["C", "D"]
    assert rejected == [{"ticker": "A", "reason": "NAV_YOK"}, {"ticker": "B", "reason": "FIYAT_YOK"}]


def test_fetch_universe_routes_only_gyo(monkeypatch):
    raw = pd.DataFrame([
        {"ticker": "AGYO", "sector_index_code": "XGMYO", "sector_code": None},
        {"ticker": "GARAN", "sector_index_code": "XBANK", "sector_code": None},
        {"ticker": "GYOX", "sector_index_code": "XUSIN", "sector_code": "GYO"},
    ])
    monkeypatch.setattr(pd, "read_sql", lambda *a, **k: raw)
    out = fetch_gyo_universe(object(), routing_config=SectorRoutingConfig.default())
    assert out.to_dict("records") == [
        {"ticker": "AGYO", "peer_group": "XGMYO", "sector_family": "GYO"},
        {"ticker": "GYOX", "peer_group": "XUSIN", "sector_family": "GYO"},
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
    snaps, _ = build_gyo_snapshots_from_frames(universe=universe, navs=navs, prices=prices, analysis_at=ANALYSIS)
    out = evaluate_gyo_batch(
        snaps, config=cfg(),
        follow_contexts={r["ticker"]: {"follow_score": .5, "follow_active": True} for r in follow.to_dict("records")},
    )
    out.update({"analysis_at": ANALYSIS, "rejections": []})
    return out


def test_persist_validates_before_transaction(monkeypatch):
    captured = []
    monkeypatch.setattr("src.analytics.gyo_batch_pipeline.execute_values", lambda cur, sql, rows, page_size: captured.append((sql, rows)))
    persist_gyo_batch(Conn(), report())
    assert len(captured) == 2
    assert "gyo_valuation_periods" in captured[0][0]
    broken = report()
    del broken["results"][0]["valuation"]["property_portfolio_value"]
    with pytest.raises(GyoBatchError, match="eksik alanlar"):
        persist_gyo_batch(Conn(), broken)


def test_run_gyo_batch_can_skip_persistence(monkeypatch):
    universe, navs, prices, follow = frames()
    monkeypatch.setattr("src.analytics.gyo_batch_pipeline.fetch_gyo_universe", lambda *a, **k: universe)
    monkeypatch.setattr("src.analytics.gyo_batch_pipeline.fetch_gyo_navs_asof", lambda *a, **k: navs)
    monkeypatch.setattr("src.analytics.gyo_batch_pipeline.fetch_gyo_prices", lambda *a, **k: prices)
    monkeypatch.setattr("src.analytics.gyo_batch_pipeline.fetch_gyo_follow_contexts", lambda *a, **k: follow)
    out = run_gyo_batch(object(), analysis_at=ANALYSIS, config=cfg(), persist=False)
    assert out["result_count"] == 4
    assert out["rejections"] == []
    assert all(x["m2"]["m2_source"] == "GYO_PD_NAV_TWO_AXIS_V1" for x in out["results"])


def test_run_revalidates_direct_config_before_db(monkeypatch):
    valid = cfg()
    broken = GyoValuationConfig(**{**valid.__dict__, "minimum_peer_count": 0})
    monkeypatch.setattr("src.analytics.gyo_batch_pipeline.fetch_gyo_universe", lambda *a, **k: (_ for _ in ()).throw(AssertionError("DB touched")))
    with pytest.raises(Exception, match="minimum_peer_count"):
        run_gyo_batch(object(), analysis_at=ANALYSIS, config=broken, persist=False)
