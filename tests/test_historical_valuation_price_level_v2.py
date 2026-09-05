"""Actual THB bytes with explicitly synthetic share/action unit-test evidence."""
from pathlib import Path
from dataclasses import asdict
import pandas as pd
import pytest
from price_level_fixtures import certify_frames
from src.analytics.historical_valuation_price_supplement import (
    CATALOG, materialize_historical_price_level_v2, HistoricalValuationPriceSupplementError)
from src.analytics.price_level_valuation_basis import SHARE_BASIS, PriceLevelValuationBasisError

ROOT = Path(__file__).resolve().parents[1] / "data/backtest_sources/p2_raw_close_pit_v2"

@pytest.mark.parametrize("evidence", CATALOG, ids=lambda e: f"{e.ticker}-{e.signal_date}")
def test_raw_close_and_synthetic_certified_shares_are_deterministic(evidence):
    source = pd.DataFrame([{"ticker": evidence.ticker, "shares_out": 100.0,
                           "period_end": evidence.trade_date}])
    prices = pd.DataFrame([{"ticker": evidence.ticker, "current_price": evidence.raw_close,
                           "price_trade_date": evidence.trade_date}])
    certify_frames(source, prices, evidence.cutoff_local, "period_end")
    args = dict(evidence=evidence, analysis_at=evidence.cutoff_local,
        raw_archive_bytes=(ROOT / Path(evidence.archive_url).name).read_bytes(),
        shares_out=100.0, shares_basis_date=evidence.trade_date,
        action_bundle=prices.iloc[0]["action_bundle"])
    a = materialize_historical_price_level_v2(**args)
    assert asdict(a) == asdict(materialize_historical_price_level_v2(**args))
    assert a.raw_close == evidence.raw_close
    assert a.market_cap == pytest.approx(evidence.raw_close * 100.0)
    assert a.share_basis == SHARE_BASIS
    with pytest.raises(PriceLevelValuationBasisError, match="ACTION_COMPLETENESS_EVIDENCE_MISSING"):
        materialize_historical_price_level_v2(**{**args, "action_bundle": None})
    with pytest.raises(HistoricalValuationPriceSupplementError, match="RAW_ARCHIVE_SHA256_MISMATCH"):
        materialize_historical_price_level_v2(**{**args, "raw_archive_bytes": args["raw_archive_bytes"] + b"x"})
