from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from src.analytics.historical_valuation_price_supplement import (
    AdjustmentProof,
    CATALOG,
    HistoricalValuationPriceSupplementError,
    REQUIRED_SHARE_BASIS,
    UNRESOLVED_REASON,
    get_raw_valuation_price_evidence,
    materialize_historical_valuation_price,
)


def _evidence():
    return get_raw_valuation_price_evidence(ticker="INVES", signal_date=date(2022, 7, 1))


def _proof(evidence, **changes):
    values = dict(
        ticker=evidence.ticker,
        signal_date=evidence.signal_date,
        trade_date=evidence.trade_date,
        raw_archive_sha256=evidence.archive_sha256,
        adjustment_factor=1.0,
        factor_known_at=evidence.cutoff_local - timedelta(minutes=1),
        share_basis=REQUIRED_SHARE_BASIS,
        source_document_id="test-adjustment-proof",
        source_sha256="a" * 64,
    )
    values.update(changes)
    return AdjustmentProof(**values)


def test_catalog_is_exact_12_gap_allowlist():
    assert len(CATALOG) == 12
    assert len({(r.ticker, r.signal_date) for r in CATALOG}) == 12
    counts = {}
    for row in CATALOG:
        counts[row.ticker] = counts.get(row.ticker, 0) + 1
        assert row.trade_date <= row.price_cap_date
        assert 0 <= row.price_age_days <= 7
    assert counts == {"INVES": 3, "KLRHO": 6, "ASGYO": 3}


def test_raw_thb_close_cannot_silently_become_current_price():
    evidence = _evidence()
    with pytest.raises(HistoricalValuationPriceSupplementError, match=UNRESOLVED_REASON):
        materialize_historical_valuation_price(
            evidence=evidence,
            analysis_at=evidence.cutoff_local,
            proof=None,
        )


def test_exact_pit_adjustment_proof_can_materialize_price():
    evidence = _evidence()
    result = materialize_historical_valuation_price(
        evidence=evidence,
        analysis_at=evidence.cutoff_local,
        proof=_proof(evidence, adjustment_factor=0.5),
    )
    assert result.current_price == pytest.approx(evidence.raw_close * 0.5)
    assert result.price_trade_date == evidence.trade_date
    assert result.share_basis == REQUIRED_SHARE_BASIS


def test_signal_day_price_is_rejected():
    evidence = _evidence()
    proof = _proof(evidence, trade_date=evidence.signal_date)
    with pytest.raises(HistoricalValuationPriceSupplementError, match="trade_date"):
        materialize_historical_valuation_price(
            evidence=evidence, analysis_at=evidence.cutoff_local, proof=proof
        )


def test_future_adjustment_proof_is_rejected():
    evidence = _evidence()
    proof = _proof(evidence, factor_known_at=evidence.cutoff_local + timedelta(seconds=1))
    with pytest.raises(HistoricalValuationPriceSupplementError, match="sonrasi adjustment proof"):
        materialize_historical_valuation_price(
            evidence=evidence, analysis_at=evidence.cutoff_local, proof=proof
        )


def test_wrong_ticker_and_signal_key_are_rejected():
    evidence = _evidence()
    with pytest.raises(HistoricalValuationPriceSupplementError, match="exact key yok"):
        get_raw_valuation_price_evidence(ticker="INVESX", signal_date=evidence.signal_date)
    with pytest.raises(HistoricalValuationPriceSupplementError, match="exact key yok"):
        get_raw_valuation_price_evidence(
            ticker=evidence.ticker, signal_date=evidence.signal_date + timedelta(days=1)
        )


def test_mutated_or_stale_raw_evidence_is_rejected():
    evidence = _evidence()
    mutated = replace(evidence, trade_date=evidence.trade_date - timedelta(days=8), price_age_days=9)
    with pytest.raises(HistoricalValuationPriceSupplementError, match="canonical exact"):
        materialize_historical_valuation_price(
            evidence=mutated, analysis_at=evidence.cutoff_local, proof=_proof(evidence)
        )


def test_wrong_analysis_cutoff_is_rejected():
    evidence = _evidence()
    with pytest.raises(HistoricalValuationPriceSupplementError, match="exact historical cutoff"):
        materialize_historical_valuation_price(
            evidence=evidence,
            analysis_at=evidence.cutoff_local + timedelta(minutes=1),
            proof=_proof(evidence),
        )


def test_wrong_adjusted_share_basis_is_rejected():
    evidence = _evidence()
    proof = _proof(evidence, share_basis="RAW_CLOSE_V1")
    with pytest.raises(HistoricalValuationPriceSupplementError, match="ADJUSTED_PRICE_SERIES_V1"):
        materialize_historical_valuation_price(
            evidence=evidence, analysis_at=evidence.cutoff_local, proof=proof
        )


def test_wrong_raw_archive_hash_is_rejected():
    evidence = _evidence()
    proof = _proof(evidence, raw_archive_sha256="b" * 64)
    with pytest.raises(HistoricalValuationPriceSupplementError, match="raw archive SHA"):
        materialize_historical_valuation_price(
            evidence=evidence, analysis_at=evidence.cutoff_local, proof=proof
        )
