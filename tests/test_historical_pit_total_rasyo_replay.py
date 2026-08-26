from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import src.analytics.historical_pit_total_rasyo_replay as replay_mod
from src.analytics.historical_pit_ek1_replay import HistoricalPitEk1ReplayResult
from src.analytics.historical_pit_ek4_replay import HistoricalPitEk4ReplayResult
from src.analytics.historical_pit_ek9_replay import HistoricalPitEk9ReplayResult
from src.analytics.historical_pit_m1_replay import HistoricalPitM1ReplayResult
from src.analytics.historical_pit_m3_replay import HistoricalPitM3ReplayResult
from src.analytics.historical_pit_nonfin_m2_replay import HistoricalPitNonfinM2ReplayResult
from src.analytics.historical_pit_rsc_replay import HistoricalPitRscReplayResult
from src.analytics.historical_pit_total_rasyo_replay import (
    EXPECTED_CUTOFF_MONTHS,
    HistoricalPitTotalRasyoCutoffInput,
    HistoricalPitTotalRasyoReplayError,
    combine_historical_pit_total_rasyo_results,
    run_historical_pit_total_rasyo_60_cutoffs,
    run_historical_pit_total_rasyo_cutoff,
)

ISTANBUL = ZoneInfo("Europe/Istanbul")


def _analysis() -> datetime:
    return datetime(2025, 3, 3, 10, 0, tzinfo=ISTANBUL)


def _m2(
    tickers=("AAA", "BBB"),
    *,
    analysis_at=None,
    rejected=(),
    unusable=(),
    scores=None,
) -> HistoricalPitNonfinM2ReplayResult:
    analysis_at = analysis_at or _analysis()
    rejected = set(rejected)
    unusable = set(unusable)
    score_values = scores or {ticker: 1.0 for ticker in tickers}
    score_rows = []
    rejection_rows = []
    for ticker in tickers:
        if ticker in rejected:
            rejection_rows.append({"ticker": ticker, "reason": "M2_INPUT_MISSING"})
            continue
        score_rows.append(
            {
                "ticker": ticker,
                "m2": score_values[ticker],
                "m2_source": "NONFIN_RELATIVE_TWO_AXIS_V1",
                "valuation_usable": ticker not in unusable,
                "valuation_status": "OK" if ticker not in unusable else "YETERSIZ_VERI",
                "valuation_confidence": 1.0,
            }
        )
    return HistoricalPitNonfinM2ReplayResult(
        analysis_at=analysis_at,
        tickers=tuple(tickers),
        valuation_profile="TEST",
        valuation_version=1,
        report={},
        m2_scores=pd.DataFrame(
            score_rows,
            columns=[
                "ticker",
                "m2",
                "m2_source",
                "valuation_usable",
                "valuation_status",
                "valuation_confidence",
            ],
        ),
        rejections=pd.DataFrame(rejection_rows, columns=["ticker", "reason"]),
    )


def _results(
    *,
    tickers=("AAA", "BBB"),
    asof=date(2025, 3, 3),
    market_asof=date(2025, 2, 28),
    analysis_at=None,
    good_counts=None,
    ek9_rejected=(),
):
    analysis_at = analysis_at or _analysis()
    good_counts = good_counts or {ticker: 5 for ticker in tickers}
    period = date(2024, 12, 31)
    m1_rows = [
        {"ticker": ticker, "m1": 1.0, "period_end": period, "good_count_ge8": good_counts[ticker]}
        for ticker in tickers
    ]
    m1 = HistoricalPitM1ReplayResult(
        analysis_at=analysis_at,
        asof_date=asof,
        tickers=tuple(tickers),
        period_comparison=pd.DataFrame(),
        m1_scores=pd.DataFrame(m1_rows),
    )
    m3 = HistoricalPitM3ReplayResult(
        analysis_at=analysis_at,
        asof_date=asof,
        market_asof_date=market_asof,
        start_date=date(2024, 12, 1),
        end_date=market_asof,
        tickers=tuple(tickers),
        beta_estimates=pd.DataFrame(),
        alpha_trailing=pd.DataFrame(),
        m3_scores=pd.DataFrame([{"ticker": ticker, "m3": 1.0} for ticker in tickers]),
        rejections=pd.DataFrame(columns=["ticker", "reason"]),
    )
    ek4 = HistoricalPitEk4ReplayResult(
        analysis_at=analysis_at,
        asof_date=asof,
        market_asof_date=market_asof,
        start_date=date(2025, 1, 30),
        end_date=market_asof,
        lookback_days=20,
        tickers=tuple(tickers),
        ek4_scores=pd.DataFrame([{"ticker": ticker, "ek4": 1.0} for ticker in tickers]),
        rejections=pd.DataFrame(columns=["ticker", "reason"]),
    )
    ek1 = HistoricalPitEk1ReplayResult(
        analysis_at=analysis_at,
        asof_date=asof,
        tickers=tuple(tickers),
        ek1_scores=pd.DataFrame(
            [
                {
                    "ticker": ticker,
                    "ek1": min(good_counts[ticker] / 18.0, 1.0),
                    "good_count_ge8": good_counts[ticker],
                    "period_end": period,
                }
                for ticker in tickers
            ]
        ),
        rejections=pd.DataFrame(columns=["ticker", "reason", "period_end"]),
    )
    rejected = set(ek9_rejected)
    ek9 = HistoricalPitEk9ReplayResult(
        analysis_at=analysis_at,
        asof_date=asof,
        market_asof_date=market_asof,
        start_date=date(2024, 12, 1),
        end_date=market_asof,
        lookback_days=63,
        market_index="XU100",
        tickers=tuple(tickers),
        ek9_scores=pd.DataFrame(
            [{"ticker": ticker, "ek9": 1.0} for ticker in tickers if ticker not in rejected]
        ),
        rejections=pd.DataFrame(
            [
                {"ticker": ticker, "reason": "STOCK_WINDOW_PRICE_MISSING"}
                for ticker in tickers
                if ticker in rejected
            ],
            columns=["ticker", "reason"],
        ),
    )
    return m1, m3, ek4, ek1, ek9


def _combine(*, m2=None, results=None, tickers=("AAA", "BBB")):
    m1, m3, ek4, ek1, ek9 = results or _results(tickers=tickers)
    return combine_historical_pit_total_rasyo_results(
        analysis_at=_analysis(),
        asof_date=date(2025, 3, 3),
        market_asof_date=date(2025, 2, 28),
        tickers=tuple(tickers),
        m2_replays=(m2 or _m2(tickers),),
        m1_replay=m1,
        m3_replay=m3,
        ek4_replay=ek4,
        ek1_replay=ek1,
        ek9_replay=ek9,
    )


def test_uses_production_combiner_and_real_veto_defaults(monkeypatch):
    results = _results(good_counts={"AAA": 4, "BBB": 5})
    original = replay_mod.combine_company_result
    calls = []

    def spy(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(replay_mod, "combine_company_result", spy)
    out = _combine(results=results)

    assert [call["ticker"] for call in calls] == ["AAA", "BBB"]
    aaa = next(item for item in out.company_results if item.ticker == "AAA")
    bbb = next(item for item in out.company_results if item.ticker == "BBB")
    assert aaa.veto_flag is True
    assert aaa.diagnostics["veto_threshold"] == 5
    assert aaa.diagnostics["veto_factor"] == pytest.approx(0.60)
    assert aaa.final_score == pytest.approx(aaa.base_score * 0.60)
    assert bbb.veto_flag is False
    assert bbb.final_score == pytest.approx(bbb.base_score)
    assert list(out.scores["ticker"]) == ["BBB", "AAA"]


def test_one_module_rejection_blocks_total_score_fail_closed():
    out = _combine(results=_results(ek9_rejected=("BBB",)))
    assert list(out.scores["ticker"]) == ["AAA"]
    assert list(out.rejections["ticker"]) == ["BBB"]
    row = out.rejections.iloc[0]
    assert row["total_rasyo_status"] == "YETERSIZ_VERI"
    assert "Ek9" in row["missing_modules"]
    assert row["module_reasons"]["Ek9"] == "STOCK_WINDOW_PRICE_MISSING"


def test_m2_rejection_blocks_total_score_fail_closed():
    out = _combine(m2=_m2(rejected=("BBB",)))
    assert list(out.scores["ticker"]) == ["AAA"]
    row = out.rejections.loc[out.rejections["ticker"] == "BBB"].iloc[0]
    assert "M2" in row["missing_modules"]
    assert row["engine_reason"] == "M2_INPUT_MISSING"


def test_m2_valuation_unusable_is_not_scored():
    out = _combine(m2=_m2(unusable=("BBB",)))
    assert list(out.scores["ticker"]) == ["AAA"]
    row = out.rejections.loc[out.rejections["ticker"] == "BBB"].iloc[0]
    assert "M2" in row["missing_modules"]
    assert row["insufficiency_reason"] == "DEGERLEME_KULLANILAMAZ"


def test_cross_module_asof_drift_is_rejected():
    m1, m3, ek4, ek1, ek9 = _results()
    drifted = replace(m3, asof_date=m3.asof_date + timedelta(days=1))
    with pytest.raises(HistoricalPitTotalRasyoReplayError, match="M3 asof_date"):
        _combine(results=(m1, drifted, ek4, ek1, ek9))


def test_cross_module_market_cutoff_drift_is_rejected():
    m1, m3, ek4, ek1, ek9 = _results()
    drifted = replace(ek4, market_asof_date=ek4.market_asof_date - timedelta(days=1))
    with pytest.raises(HistoricalPitTotalRasyoReplayError, match="Ek4 market_asof_date"):
        _combine(results=(m1, m3, drifted, ek1, ek9))


def test_m2_analysis_cutoff_drift_is_rejected():
    drifted = _m2(analysis_at=_analysis() - timedelta(minutes=1))
    with pytest.raises(HistoricalPitTotalRasyoReplayError, match="M2 analysis_at"):
        _combine(m2=drifted)


def test_m1_ek1_good_count_lineage_drift_is_rejected():
    m1, m3, ek4, ek1, ek9 = _results()
    mutated = ek1.ek1_scores.copy()
    mutated.loc[mutated["ticker"] == "AAA", "good_count_ge8"] = 6
    drifted = replace(ek1, ek1_scores=mutated)
    with pytest.raises(HistoricalPitTotalRasyoReplayError, match="lineage eslesmiyor"):
        _combine(results=(m1, m3, ek4, drifted, ek9))


def test_ranking_tie_break_is_ticker_ascending():
    m1, m3, ek4, ek1, ek9 = _results(tickers=("BBB", "AAA"))
    out = _combine(results=(m1, m3, ek4, ek1, ek9), tickers=("BBB", "AAA"))
    assert list(out.scores["ticker"]) == ["AAA", "BBB"]
    assert list(out.scores["rank"]) == [1, 2]


def test_cutoff_runner_calls_existing_replay_functions(monkeypatch):
    analysis = _analysis()
    asof = date(2025, 3, 3)
    market_asof = date(2025, 2, 28)
    m1, m3, ek4, ek1, ek9 = _results()
    rsc = HistoricalPitRscReplayResult(
        analysis_at=analysis,
        tickers=("AAA", "BBB"),
        ratio_scores=pd.DataFrame(),
        rsc_summary=pd.DataFrame(),
    )
    universe = pd.DataFrame(
        {"ticker": ["AAA", "BBB"], "sector_index_code": ["XUSIN", "XUSIN"]}
    )
    calendar = pd.DataFrame({"trade_date": [market_asof]})
    stock_prices = pd.DataFrame({"sentinel": [1]})
    index_prices = pd.DataFrame({"sentinel": [2]})
    calls = []

    def fake_m1(got_rsc, *, asof_date):
        calls.append(("M1", got_rsc, asof_date))
        return m1

    def fake_m3(**kwargs):
        calls.append(("M3", kwargs))
        return m3

    def fake_ek4(**kwargs):
        calls.append(("Ek4", kwargs))
        return ek4

    def fake_ek1(got_m1):
        calls.append(("Ek1", got_m1))
        return ek1

    def fake_ek9(**kwargs):
        calls.append(("Ek9", kwargs))
        return ek9

    monkeypatch.setattr(replay_mod, "run_historical_pit_m1_replay", fake_m1)
    monkeypatch.setattr(replay_mod, "run_historical_pit_m3_replay", fake_m3)
    monkeypatch.setattr(replay_mod, "run_historical_pit_ek4_replay", fake_ek4)
    monkeypatch.setattr(replay_mod, "run_historical_pit_ek1_replay", fake_ek1)
    monkeypatch.setattr(replay_mod, "run_historical_pit_ek9_replay", fake_ek9)

    out = run_historical_pit_total_rasyo_cutoff(
        HistoricalPitTotalRasyoCutoffInput(
            analysis_at=analysis,
            asof_date=asof,
            market_asof_date=market_asof,
            universe=universe,
            trading_calendar=calendar,
            stock_prices=stock_prices,
            index_prices=index_prices,
            rsc_replay=rsc,
            m2_replays=(_m2(),),
        )
    )
    assert [call[0] for call in calls] == ["M1", "M3", "Ek4", "Ek1", "Ek9"]
    assert calls[3][1] is m1
    assert calls[1][1]["universe"] is universe
    assert calls[1][1]["stock_prices"] is stock_prices
    assert calls[2][1]["index_prices"] is index_prices
    assert calls[4][1]["stock_prices"] is stock_prices
    assert len(out.company_results) == 2


def _sixty_inputs():
    inputs = []
    for period in pd.period_range("2021-08", "2026-07", freq="M"):
        asof = period.start_time.date()
        analysis = datetime(asof.year, asof.month, asof.day, 10, tzinfo=ISTANBUL)
        inputs.append(
            HistoricalPitTotalRasyoCutoffInput(
                analysis_at=analysis,
                asof_date=asof,
                market_asof_date=asof,
                universe=pd.DataFrame(),
                trading_calendar=pd.DataFrame(),
                stock_prices=pd.DataFrame(),
                index_prices=pd.DataFrame(),
                rsc_replay=None,
                m2_replays=(),
            )
        )
    return inputs


def test_60_cutoff_contract_is_exact_2021_08_through_2026_07(monkeypatch):
    calls = []

    def fake_cutoff(item):
        calls.append(item.asof_date)
        return SimpleNamespace(
            scores=pd.DataFrame(columns=replay_mod.SCORE_COLUMNS),
            rejections=pd.DataFrame(columns=replay_mod.REJECTION_COLUMNS),
        )

    monkeypatch.setattr(replay_mod, "run_historical_pit_total_rasyo_cutoff", fake_cutoff)
    out = run_historical_pit_total_rasyo_60_cutoffs(_sixty_inputs())
    assert len(calls) == 60
    assert out.cutoff_months == EXPECTED_CUTOFF_MONTHS
    assert out.cutoff_months[0] == "2021-08"
    assert out.cutoff_months[-1] == "2026-07"


def test_60_cutoff_contract_rejects_missing_or_reordered_month(monkeypatch):
    monkeypatch.setattr(
        replay_mod,
        "run_historical_pit_total_rasyo_cutoff",
        lambda item: SimpleNamespace(
            scores=pd.DataFrame(columns=replay_mod.SCORE_COLUMNS),
            rejections=pd.DataFrame(columns=replay_mod.REJECTION_COLUMNS),
        ),
    )
    inputs = _sixty_inputs()
    with pytest.raises(HistoricalPitTotalRasyoReplayError, match="tam 60 cutoff"):
        run_historical_pit_total_rasyo_60_cutoffs(inputs[:-1])
    swapped = list(inputs)
    swapped[10], swapped[11] = swapped[11], swapped[10]
    with pytest.raises(HistoricalPitTotalRasyoReplayError, match="2021-08..2026-07"):
        run_historical_pit_total_rasyo_60_cutoffs(swapped)


def test_historical_orchestrator_does_not_import_score_formula_directly():
    assert not hasattr(replay_mod, "compute_total_rasyo")
