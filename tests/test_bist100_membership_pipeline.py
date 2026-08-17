from __future__ import annotations

from src.analytics.bist100_membership_history import Bist100ConstituentEvent
from src.analytics.bist100_membership_pipeline import reconstruct_bist100_memberships_with_ticker_lineage
from src.analytics.ticker_lineage import TickerCodeChange, TickerLineageResolver


SHA = "a" * 64


def test_ticker_rename_is_identity_change_not_false_constituent_change():
    lineage = TickerLineageResolver([
        TickerCodeChange.build(
            effective_date="2025-11-03",
            old_ticker="EFORC",
            new_ticker="EFOR",
            source_sha256="b" * 64,
            event_sha256="c" * 64,
        )
    ])
    event = Bist100ConstituentEvent.build(
        effective_date="2025-04-02",
        included=["EFORC"],
        excluded=["AAA"],
        source_id="2025Q2",
        source_sha256=SHA,
    )

    history = reconstruct_bist100_memberships_with_ticker_lineage(
        current_members=["BBB", "CCC", "EFOR"],
        snapshot_date="2026-01-02",
        constituent_events=[event],
        ticker_lineage=lineage,
        target_dates=["2025-04-01", "2025-04-02", "2025-11-02", "2025-11-03"],
        expected_count=3,
    )

    assert history.members_on("2025-04-01") == ("AAA", "BBB", "CCC")
    assert history.members_on("2025-04-02") == ("BBB", "CCC", "EFORC")
    assert history.members_on("2025-11-02") == ("BBB", "CCC", "EFORC")
    assert history.members_on("2025-11-03") == ("BBB", "CCC", "EFOR")


def test_multi_company_rename_renders_historical_codes_after_reconstruction():
    lineage = TickerLineageResolver([
        TickerCodeChange.build(
            effective_date="2025-11-24", old_ticker="IPEKE", new_ticker="TRENJ",
            source_sha256="1" * 64, event_sha256="2" * 64,
        ),
        TickerCodeChange.build(
            effective_date="2025-11-24", old_ticker="KOZAA", new_ticker="TRMET",
            source_sha256="1" * 64, event_sha256="3" * 64,
        ),
    ])
    event = Bist100ConstituentEvent.build(
        effective_date="2025-07-01",
        included=["IPEKE"],
        excluded=["AAA"],
        source_id="2025Q3",
        source_sha256=SHA,
    )

    history = reconstruct_bist100_memberships_with_ticker_lineage(
        current_members=["TRENJ", "TRMET", "BBB"],
        snapshot_date="2026-01-02",
        constituent_events=[event],
        ticker_lineage=lineage,
        target_dates=["2025-06-30", "2025-07-01", "2025-11-23", "2025-11-24"],
        expected_count=3,
    )

    assert history.members_on("2025-06-30") == ("AAA", "BBB", "KOZAA")
    assert history.members_on("2025-07-01") == ("BBB", "IPEKE", "KOZAA")
    assert history.members_on("2025-11-23") == ("BBB", "IPEKE", "KOZAA")
    assert history.members_on("2025-11-24") == ("BBB", "TRENJ", "TRMET")
