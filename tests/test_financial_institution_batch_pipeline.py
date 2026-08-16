from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from src.analytics.financial_institution_batch_pipeline import (
    FinancialInstitutionBatchError,
    build_financial_institution_snapshots_from_frames,
    persist_financial_institution_batch,
    run_financial_institution_batch,
)
from src.analytics.financial_institution_valuation import FinancialInstitutionValuationConfig

TZ = ZoneInfo("Europe/Istanbul")
ANALYSIS = datetime(2026, 8, 5, 19, 0, tzinfo=TZ)
SHA = "a" * 64


def cfg(**patch):
    data = {
        "valuation_profile": "FINANCIAL_INSTITUTION_PB_PE",
        "valuation_version": 1,
        "source_metrics_profile": "KAP_FINANCIAL_INSTITUTION_TTM",
        "source_metrics_version": 1,
        "accounting_profile": "TFRS_LOCAL_STATUTORY",
        "accounting_version": 1,
        "share_basis": "ADJUSTED_PRICE_SERIES_V1",
        "minimum_peer_count": 2,
        "full_confidence_peer_count": 4,
    }
    data.update(patch)
    return FinancialInstitutionValuationConfig.from_dict(data)


def frames():
    tickers = ["AAA", "BBB", "CCC"]
    universe = pd.DataFrame({"ticker": tickers, "sector_family": ["FINANCIAL"] * 3})
    metrics = pd.DataFrame([
        {
            "ticker": ticker,
            "period_end": date(2026, 6, 30),
            "published_at": datetime(2026, 7, 20, 10, 0, tzinfo=TZ),
            "business_type": "FACTORING",
            "accounting_profile": "TFRS_LOCAL_STATUTORY",
            "accounting_version": 1,
            "currency": "TRY",
            "shares_out": 100,
            "share_basis": "ADJUSTED_PRICE_SERIES_V1",
            "total_equity": 1000,
            "average_equity": 950,
            "net_income_ttm": 100,
            "total_assets": 5000,
            "finance_receivables": 4200,
            "npl_gross": 150,
            "provisions": 120,
            "net_finance_income_ttm": 420,
            "funding_cost_ttm": 300,
            "operating_expenses_ttm": 140,
            "capital_adequacy_ratio": 0.18,
            "source_confidence": 0.9,
            "source_document_id": f"DOC-{ticker}",
            "source_sha256": SHA,
            "metrics_profile": "KAP_FINANCIAL_INSTITUTION_TTM",
            "metrics_version": 1,
        }
        for ticker in tickers
    ])
    prices = pd.DataFrame({
        "ticker": tickers,
        "price_trade_date": [date(2026, 8, 5)] * 3,
        "current_price": [8, 10, 12],
    })
    follow = pd.DataFrame({
        "ticker": tickers,
        "follow_score": [0.5, 0.6, 0.4],
        "follow_active": [True, True, True],
    })
    return universe, metrics, prices, follow


def test_build_snapshots_and_isolate_missing_rows():
    universe, metrics, prices, _ = frames()
    metrics = metrics[metrics.ticker != "BBB"]
    snapshots, rejections = build_financial_institution_snapshots_from_frames(
        universe=universe, metrics=metrics, prices=prices, analysis_at=ANALYSIS
    )
    assert [item.ticker for item in snapshots] == ["AAA", "CCC"]
    assert rejections == [{"ticker": "BBB", "reason": "FINANSAL_KURULUS_METRIKLERI_YOK"}]


def test_build_rejects_wrong_family_and_duplicate_inputs():
    universe, metrics, prices, _ = frames()
    bad = universe.copy(); bad.loc[0, "sector_family"] = "NONFIN"
    with pytest.raises(FinancialInstitutionBatchError, match="beklenmeyen aile"):
        build_financial_institution_snapshots_from_frames(universe=bad, metrics=metrics, prices=prices, analysis_at=ANALYSIS)
    duplicate = pd.concat([prices, prices.iloc[[0]]], ignore_index=True)
    with pytest.raises(FinancialInstitutionBatchError, match="yinelenen"):
        build_financial_institution_snapshots_from_frames(universe=universe, metrics=metrics, prices=duplicate, analysis_at=ANALYSIS)


def test_run_batch_uses_prepared_context_only(monkeypatch):
    universe, metrics, prices, follow = frames()
    metrics = metrics[metrics.ticker != "CCC"]
    monkeypatch.setattr("src.analytics.financial_institution_batch_pipeline.fetch_financial_institution_universe", lambda *a, **k: universe)
    monkeypatch.setattr("src.analytics.financial_institution_batch_pipeline.fetch_financial_institution_metrics_asof", lambda *a, **k: metrics)
    monkeypatch.setattr("src.analytics.financial_institution_batch_pipeline.fetch_financial_institution_prices", lambda *a, **k: prices)
    monkeypatch.setattr("src.analytics.financial_institution_batch_pipeline.fetch_financial_institution_follow_contexts", lambda *a, **k: follow)
    out = run_financial_institution_batch(object(), analysis_at=ANALYSIS, config=cfg(), persist=False)
    assert out["result_count"] == 2
    assert out["rejections"] == [{"ticker": "CCC", "reason": "FINANSAL_KURULUS_METRIKLERI_YOK"}]


def test_run_revalidates_direct_config_before_db(monkeypatch):
    valid = cfg()
    broken = FinancialInstitutionValuationConfig(**{**valid.__dict__, "minimum_peer_count": 0})
    monkeypatch.setattr(
        "src.analytics.financial_institution_batch_pipeline.fetch_financial_institution_universe",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("DB touched")),
    )
    with pytest.raises(Exception, match="minimum_peer_count"):
        run_financial_institution_batch(object(), analysis_at=ANALYSIS, config=broken, persist=False)


class Cursor:
    def __init__(self): self.calls = []
    def execute(self, sql, params=None): self.calls.append((sql, params))
    def __enter__(self): return self
    def __exit__(self, *args): return False


class Conn:
    def __init__(self): self.cur = Cursor()
    def cursor(self): return self.cur
    def __enter__(self): return self
    def __exit__(self, *args): return False


def sample_report():
    universe, metrics, prices, follow = frames()
    snapshots, rejections = build_financial_institution_snapshots_from_frames(
        universe=universe, metrics=metrics, prices=prices, analysis_at=ANALYSIS
    )
    from src.analytics.financial_institution_valuation import evaluate_financial_institution_batch
    contexts = {r.ticker: {"follow_score": 0.5, "follow_active": True} for r in snapshots}
    report = evaluate_financial_institution_batch(snapshots, config=cfg(), follow_contexts=contexts)
    report.update({"analysis_at": ANALYSIS, "rejections": rejections})
    return report


def test_persist_validates_top_level_contract_before_transaction(monkeypatch):
    report = sample_report()
    conn = Conn()
    monkeypatch.setattr("src.analytics.financial_institution_batch_pipeline.execute_values", lambda *a, **k: None)
    persist_financial_institution_batch(conn, report)
    assert conn.cur.calls
    bad = {**report, "config_sha256": "b" * 64}
    with pytest.raises(FinancialInstitutionBatchError, match="config SHA"):
        persist_financial_institution_batch(Conn(), bad)


def test_persist_rejects_overlap_duplicate_and_noncanonical_json(monkeypatch):
    report = sample_report()
    duplicate = {**report, "results": report["results"] + [report["results"][0]], "result_count": 4}
    with pytest.raises(FinancialInstitutionBatchError, match="yinelenen"):
        persist_financial_institution_batch(Conn(), duplicate)
    overlap = {**report, "rejections": [{"ticker": report["results"][0]["ticker"], "reason": "X"}]}
    with pytest.raises(FinancialInstitutionBatchError, match="hem sonuc"):
        persist_financial_institution_batch(Conn(), overlap)
    broken = sample_report()
    broken["results"][0]["valuation"]["diagnostics"] = {"bad": float("nan")}
    with pytest.raises(FinancialInstitutionBatchError, match="kanonik JSON"):
        persist_financial_institution_batch(Conn(), broken)
