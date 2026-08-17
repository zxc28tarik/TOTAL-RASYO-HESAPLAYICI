from __future__ import annotations

import pytest

from src.analytics.historical_backtest_corporate_action_events import (
    ACTION_CASH_DIVIDEND,
    ACTION_SPLIT,
    CorporateActionEventError,
    HistoricalCorporateAction,
    validate_corporate_action_events,
)


SHA = "a" * 64


def test_split_and_cash_dividend_are_separate_economic_contracts():
    split = HistoricalCorporateAction.build(
        ticker="abcd",
        action_type=ACTION_SPLIT,
        ex_date="2025-06-02",
        share_multiplier=2,
        source_ref="OFFICIAL:split-1",
        source_sha256=SHA,
    )
    dividend = HistoricalCorporateAction.build(
        ticker="ABCD",
        action_type=ACTION_CASH_DIVIDEND,
        ex_date="2025-06-10",
        payment_date="2025-06-12",
        cash_per_share=1.25,
        currency="try",
        source_ref="OFFICIAL:dividend-1",
        source_sha256=SHA,
    )
    assert split.ticker == "ABCD"
    assert split.share_multiplier == 2.0
    assert split.cash_per_share is None
    assert dividend.cash_per_share == 1.25
    assert dividend.currency == "TRY"
    assert dividend.payment_date.isoformat() == "2025-06-12"
    assert split.action_id != dividend.action_id
    assert validate_corporate_action_events([dividend, split]) == (split, dividend)


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(action_type="OTHER", ex_date="2025-01-01", share_multiplier=2),
        dict(action_type=ACTION_SPLIT, ex_date="2025-01-01", share_multiplier=1),
        dict(action_type=ACTION_SPLIT, ex_date="2025-01-01", share_multiplier=0),
        dict(action_type=ACTION_SPLIT, ex_date="2025-01-01", share_multiplier=2, payment_date="2025-01-02"),
        dict(action_type=ACTION_CASH_DIVIDEND, ex_date="2025-01-01", cash_per_share=1, currency="TRY"),
        dict(action_type=ACTION_CASH_DIVIDEND, ex_date="2025-01-02", payment_date="2025-01-01", cash_per_share=1, currency="TRY"),
        dict(action_type=ACTION_CASH_DIVIDEND, ex_date="2025-01-01", payment_date="2025-01-02", cash_per_share=1, currency="USD"),
        dict(action_type=ACTION_CASH_DIVIDEND, ex_date="2025-01-01", payment_date="2025-01-02", cash_per_share=0, currency="TRY"),
        dict(action_type=ACTION_CASH_DIVIDEND, ex_date="2025-01-01", payment_date="2025-01-02", cash_per_share=1, currency="TRY", share_multiplier=2),
    ],
)
def test_rejects_ambiguous_or_impossible_event_semantics(kwargs):
    with pytest.raises(CorporateActionEventError):
        HistoricalCorporateAction.build(
            ticker="ABCD",
            source_ref="OFFICIAL:test",
            source_sha256=SHA,
            **kwargs,
        )


def test_source_hash_is_mandatory_and_action_id_is_deterministic():
    args=dict(
        ticker="ABCD",
        action_type=ACTION_SPLIT,
        ex_date="2025-01-01",
        share_multiplier=1.5,
        source_ref="OFFICIAL:test",
        source_sha256=SHA,
    )
    first=HistoricalCorporateAction.build(**args)
    second=HistoricalCorporateAction.build(**args)
    assert first.action_id == second.action_id
    with pytest.raises(CorporateActionEventError, match="64-hex"):
        HistoricalCorporateAction.build(**{**args,"source_sha256":"bad"})


def test_duplicate_economic_event_from_multiple_sources_is_rejected():
    a=HistoricalCorporateAction.build(
        ticker="ABCD", action_type=ACTION_SPLIT, ex_date="2025-01-01",
        share_multiplier=2, source_ref="OFFICIAL:a", source_sha256="a"*64,
    )
    b=HistoricalCorporateAction.build(
        ticker="ABCD", action_type=ACTION_SPLIT, ex_date="2025-01-01",
        share_multiplier=2, source_ref="OFFICIAL:b", source_sha256="b"*64,
    )
    with pytest.raises(CorporateActionEventError, match="ayni ekonomik corporate action"):
        validate_corporate_action_events([a,b])
