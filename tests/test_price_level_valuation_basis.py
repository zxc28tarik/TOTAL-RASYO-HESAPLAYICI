from __future__ import annotations

from datetime import date

import pytest

from src.analytics.historical_backtest_corporate_action_events import (
    ACTION_CASH_DIVIDEND,
    ACTION_SPLIT,
    HistoricalCorporateAction,
)
from src.analytics.price_level_valuation_basis import (
    PRICE_LEVEL_BASIS,
    SHARE_BASIS,
    PriceLevelValuationBasisError,
    build_price_level_observation,
    materialize_price_level_market_cap,
    normalize_shares_out_to_price_date,
)

SHA = "a" * 64


def split(ticker: str, ex_date: date, multiplier: float):
    return HistoricalCorporateAction.build(
        ticker=ticker,
        action_type=ACTION_SPLIT,
        ex_date=ex_date,
        share_multiplier=multiplier,
        source_ref=f"split-{ticker}-{ex_date}",
        source_sha256=SHA,
    )


def dividend(ticker: str, ex_date: date, cash: float):
    return HistoricalCorporateAction.build(
        ticker=ticker,
        action_type=ACTION_CASH_DIVIDEND,
        ex_date=ex_date,
        payment_date=ex_date,
        cash_per_share=cash,
        currency="TRY",
        source_ref=f"dividend-{ticker}-{ex_date}",
        source_sha256=SHA,
    )


def test_dividend_adjusted_close_never_becomes_price_level():
    price = build_price_level_observation(
        ticker="AAA",
        trade_date=date(2026, 8, 3),
        close=100.0,
        adjusted_close=90.0,
    )
    result = materialize_price_level_market_cap(
        price=price,
        shares_out=10_000_000,
        shares_basis_date=date(2026, 6, 30),
        corporate_actions=[dividend("AAA", date(2026, 7, 15), 10.0)],
        events_complete_through=date(2026, 8, 3),
    )
    assert result.raw_close == 100.0
    assert result.normalized_shares_out == 10_000_000
    assert result.market_cap == 1_000_000_000.0
    assert result.price_basis == PRICE_LEVEL_BASIS
    assert result.share_basis == SHARE_BASIS
    assert result.applied_share_action_ids == ()


def test_split_changes_shares_not_market_close():
    event = split("AAA", date(2026, 7, 15), 2.0)
    price = build_price_level_observation(
        ticker="AAA", trade_date=date(2026, 8, 3), close=50.0, adjusted_close=45.0
    )
    result = materialize_price_level_market_cap(
        price=price,
        shares_out=10_000_000,
        shares_basis_date=date(2026, 6, 30),
        corporate_actions=[event],
        events_complete_through=date(2026, 8, 3),
    )
    assert result.normalized_shares_out == 20_000_000
    assert result.market_cap == 1_000_000_000.0
    assert result.applied_share_action_ids == (event.action_id,)


def test_split_before_share_basis_date_is_not_applied_twice():
    normalized, ids = normalize_shares_out_to_price_date(
        ticker="AAA",
        shares_out=20_000_000,
        shares_basis_date=date(2026, 6, 30),
        price_trade_date=date(2026, 8, 3),
        corporate_actions=[split("AAA", date(2026, 6, 15), 2.0)],
        events_complete_through=date(2026, 8, 3),
    )
    assert normalized == 20_000_000
    assert ids == ()


def test_future_split_is_not_applied():
    normalized, ids = normalize_shares_out_to_price_date(
        ticker="AAA",
        shares_out=10_000_000,
        shares_basis_date=date(2026, 6, 30),
        price_trade_date=date(2026, 8, 3),
        corporate_actions=[split("AAA", date(2026, 8, 10), 2.0)],
        events_complete_through=date(2026, 8, 3),
    )
    assert normalized == 10_000_000
    assert ids == ()


def test_event_completeness_is_mandatory_fail_closed():
    price = build_price_level_observation(
        ticker="AAA", trade_date=date(2026, 8, 3), close=100.0
    )
    with pytest.raises(PriceLevelValuationBasisError, match="event kapsami"):
        materialize_price_level_market_cap(
            price=price,
            shares_out=10_000_000,
            shares_basis_date=date(2026, 6, 30),
            corporate_actions=[],
            events_complete_through=date(2026, 8, 2),
        )


def test_wrong_ticker_events_do_not_modify_shares():
    normalized, ids = normalize_shares_out_to_price_date(
        ticker="AAA",
        shares_out=10_000_000,
        shares_basis_date=date(2026, 6, 30),
        price_trade_date=date(2026, 8, 3),
        corporate_actions=[split("BBB", date(2026, 7, 15), 3.0)],
        events_complete_through=date(2026, 8, 3),
    )
    assert normalized == 10_000_000
    assert ids == ()


def test_nonpositive_raw_close_is_rejected_even_if_adjusted_close_is_positive():
    with pytest.raises(PriceLevelValuationBasisError, match="close"):
        build_price_level_observation(
            ticker="AAA", trade_date=date(2026, 8, 3), close=0, adjusted_close=90
        )


def test_price_date_cannot_precede_share_basis_date():
    with pytest.raises(PriceLevelValuationBasisError, match="oncesinde"):
        normalize_shares_out_to_price_date(
            ticker="AAA",
            shares_out=10_000_000,
            shares_basis_date=date(2026, 8, 4),
            price_trade_date=date(2026, 8, 3),
            corporate_actions=[],
            events_complete_through=date(2026, 8, 3),
        )
