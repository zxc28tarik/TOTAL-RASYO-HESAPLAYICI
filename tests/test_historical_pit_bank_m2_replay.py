from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import inspect
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from src.analytics.bank_batch_pipeline import ResolvedBankAssumption, daily_price_cutoff_date
from src.analytics.bank_valuation_pipeline import BankValuationInputs, build_quarter_slots
from src.analytics.historical_pit_bank_m2_replay import (
    HistoricalPitBankM2ReplayError,
    run_historical_pit_bank_m2_replay,
)

TZ = ZoneInfo("Europe/Istanbul")
ANALYSIS = datetime(2026, 3, 1, 12, 0, tzinfo=TZ)
ANCHOR = date(2025, 12, 31)
BASE_ROE = [0.156, 0.1898, 0.1952, 0.2346, 0.2689, 0.2809]


def _rows(ticker: str, bump: float = 0.0):
    values = [None, None] + [v + bump * (i % 2) for i, v in enumerate(BASE_ROE)]
    rows = []
    for i, (slot, value) in enumerate(zip(build_quarter_slots(ANCHOR), values, strict=True)):
        if value is None:
            rows.append({
                "period_end": slot, "record_id": None, "selected_version_tag": None,
                "selected_version_sequence": None, "selected_published_at": None,
                "roe_ttm": None, "bvps": None, "payout_sus": None,
            })
        else:
            rows.append({
                "period_end": slot, "record_id": i + 1, "selected_version_tag": "ORIGINAL",
                "selected_version_sequence": 1,
                "selected_published_at": datetime(2026, 2, 20, 10, 0, tzinfo=TZ),
                "roe_ttm": value,
                "bvps": Decimal("21.24") if i == 7 else None,
                "payout_sus": Decimal("0.25") if i == 7 else None,
            })
    return rows


def _tickers():
    return tuple(f"BNK{i:02d}" for i in range(21))


def _slots():
    return {ticker: _rows(ticker, i * 0.0004) for i, ticker in enumerate(_tickers())}


def _assumption(ticker: str, *, effective_at: datetime | None = None):
    return ResolvedBankAssumption(
        inputs=BankValuationInputs(coe=0.3705, macro_cap=0.140135, band_width_shadow_mode=False),
        scope_type="BANK", scope_code="BANK",
        effective_at=effective_at or datetime(2026, 1, 1, 0, 0, tzinfo=TZ),
        source="PIT_TEST", metadata={}, risk_free_rate=0.30,
    )


def _assumptions():
    return {ticker: _assumption(ticker) for ticker in _tickers()}


def _contexts():
    cutoff = daily_price_cutoff_date(ANALYSIS)
    return pd.DataFrame([
        {
            "ticker": ticker,
            "price_trade_date": date(2026, 2, 27),
            "current_price": 5.5,
            "price_source": "HISTORICAL_SIGNAL_PRICE",
            "lag_asof_date": cutoff,
            "s_lag_effective": 0.7,
            "lag_active": True,
            "lag_source": "M2_PERIOD_FOLLOW_PROXY_V1",
        }
        for ticker in _tickers()
    ])


def test_bank_replay_has_no_database_connection_parameter():
    assert "conn" not in inspect.signature(run_historical_pit_bank_m2_replay).parameters


def test_bank_replay_reuses_v47_leave_one_out_and_two_axis_m2():
    result = run_historical_pit_bank_m2_replay(
        analysis_at=ANALYSIS,
        anchor_period_end=ANCHOR,
        quarter_slots=_slots(),
        assumptions=_assumptions(),
        contexts=_contexts(),
    )
    assert result.tickers == tuple(sorted(_tickers()))
    assert result.rejections.empty
    assert len(result.m2_scores) == 21
    assert set(result.m2_scores["m2_source"]) == {"BANK_TWO_AXIS_V47"}
    assert result.m2_scores["m2"].between(0.0, 1.0).all()
    assert all(r.get("sector_sample_size") == 20 for r in result.valuation_results)


def test_bank_replay_rejects_future_publication_without_db_fallback():
    slots = _slots()
    slots["BNK00"][7]["selected_published_at"] = datetime(2026, 3, 2, 10, 0, tzinfo=TZ)
    result = run_historical_pit_bank_m2_replay(
        analysis_at=ANALYSIS, anchor_period_end=ANCHOR,
        quarter_slots=slots, assumptions=_assumptions(), contexts=_contexts(),
    )
    assert "BNK00" in set(result.rejections["ticker"])
    assert "gelecekte yayimlanan" in result.rejections.set_index("ticker").loc["BNK00", "reason"]


def test_bank_replay_rejects_future_assumption():
    assumptions = _assumptions()
    assumptions["BNK00"] = _assumption("BNK00", effective_at=datetime(2026, 3, 2, tzinfo=TZ))
    with pytest.raises(HistoricalPitBankM2ReplayError, match="assumption"):
        run_historical_pit_bank_m2_replay(
            analysis_at=ANALYSIS, anchor_period_end=ANCHOR,
            quarter_slots=_slots(), assumptions=assumptions, contexts=_contexts(),
        )


def test_bank_replay_rejects_future_price():
    contexts = _contexts()
    contexts.loc[contexts["ticker"] == "BNK00", "price_trade_date"] = date(2026, 3, 1)
    with pytest.raises(HistoricalPitBankM2ReplayError, match="fiyat"):
        run_historical_pit_bank_m2_replay(
            analysis_at=ANALYSIS, anchor_period_end=ANCHOR,
            quarter_slots=_slots(), assumptions=_assumptions(), contexts=contexts,
        )


def test_bank_replay_requires_active_lag_exactly_at_production_cutoff():
    contexts = _contexts()
    contexts.loc[contexts["ticker"] == "BNK00", "lag_asof_date"] = date(2026, 2, 27)
    with pytest.raises(HistoricalPitBankM2ReplayError, match="production cutoff"):
        run_historical_pit_bank_m2_replay(
            analysis_at=ANALYSIS, anchor_period_end=ANCHOR,
            quarter_slots=_slots(), assumptions=_assumptions(), contexts=contexts,
        )


def test_bank_replay_requires_explicit_context_for_every_historical_bank():
    contexts = _contexts().iloc[:-1].copy()
    with pytest.raises(HistoricalPitBankM2ReplayError, match="tam eslesmeli"):
        run_historical_pit_bank_m2_replay(
            analysis_at=ANALYSIS, anchor_period_end=ANCHOR,
            quarter_slots=_slots(), assumptions=_assumptions(), contexts=contexts,
        )
