from __future__ import annotations

import pytest

from src.analytics.ticker_lineage import (
    TickerCodeChange,
    TickerLineageError,
    TickerLineageResolver,
    load_ticker_code_changes_csv,
)


SHA = "a" * 64
EVENT = "b" * 64


def _event(day, old, new, event_sha=EVENT):
    return TickerCodeChange.build(
        effective_date=day,
        old_ticker=old,
        new_ticker=new,
        source_sha256=SHA,
        event_sha256=event_sha,
    )


def test_forward_and_backward_respect_effective_day_semantics():
    resolver = TickerLineageResolver([_event("2025-11-03", "EFORC", "EFOR")])
    assert resolver.forward("EFORC", from_date="2025-11-02", to_date="2025-11-03") == "EFOR"
    assert resolver.forward("EFOR", from_date="2025-11-03", to_date="2026-01-01") == "EFOR"
    assert resolver.backward("EFOR", target_date="2025-11-02", snapshot_date="2026-01-01") == "EFORC"
    assert resolver.backward("EFOR", target_date="2025-11-03", snapshot_date="2026-01-01") == "EFOR"


def test_multi_step_chain_maps_to_snapshot_and_back():
    resolver = TickerLineageResolver([
        _event("2021-11-01", "TKURU", "TETMT", "1" * 64),
        _event("2024-10-01", "TETMT", "LYDYE", "2" * 64),
    ])
    assert resolver.forward("TKURU", from_date="2021-08-01", to_date="2026-01-01") == "LYDYE"
    assert resolver.backward("LYDYE", target_date="2021-08-01", snapshot_date="2026-01-01") == "TKURU"
    assert resolver.backward("LYDYE", target_date="2022-01-01", snapshot_date="2026-01-01") == "TETMT"


def test_real_source_csv_loads_38_hash_locked_events():
    events = load_ticker_code_changes_csv("data/backtest_sources/bist_ticker_code_changes_2021-08_2026-08.csv")
    assert len(events) == 38
    resolver = TickerLineageResolver(events)
    assert resolver.forward("EFORC", from_date="2025-04-02", to_date="2026-08-17") == "EFOR"
    assert resolver.forward("IPEKE", from_date="2025-07-01", to_date="2026-08-17") == "TRENJ"
    assert resolver.forward("KOZAA", from_date="2025-01-01", to_date="2026-08-17") == "TRMET"
    assert resolver.forward("KOZAL", from_date="2025-01-01", to_date="2026-08-17") == "TRALT"
    assert resolver.backward("TRENJ", target_date="2025-11-03", snapshot_date="2026-08-17") == "IPEKE"


def test_fork_and_merge_are_rejected_fail_closed():
    with pytest.raises(TickerLineageError, match="fork"):
        TickerLineageResolver([
            _event("2024-01-01", "AAA", "BBB", "1" * 64),
            _event("2025-01-01", "AAA", "CCC", "2" * 64),
        ])
    with pytest.raises(TickerLineageError, match="merge"):
        TickerLineageResolver([
            _event("2024-01-01", "AAA", "CCC", "1" * 64),
            _event("2025-01-01", "BBB", "CCC", "2" * 64),
        ])


def test_same_day_chain_is_rejected_as_ambiguous():
    with pytest.raises(TickerLineageError, match="ayni gunde zincir"):
        TickerLineageResolver([
            _event("2024-01-01", "AAA", "BBB", "1" * 64),
            _event("2024-01-01", "BBB", "CCC", "2" * 64),
        ])
