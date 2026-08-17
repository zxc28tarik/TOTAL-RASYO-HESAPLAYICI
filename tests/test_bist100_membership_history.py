from __future__ import annotations

import pytest

from src.analytics.bist100_membership_history import (
    Bist100ConstituentEvent,
    Bist100HistoryError,
    reconstruct_bist100_memberships,
)


SHA1 = "1" * 64
SHA2 = "2" * 64


def _event(day, included, excluded, *, source="A", sha=SHA1):
    return Bist100ConstituentEvent.build(
        effective_date=day,
        included=included,
        excluded=excluded,
        source_id=source,
        source_sha256=sha,
    )


def test_reverse_reconstruction_uses_new_members_on_effective_day():
    # Original set A,B,C. Jan 2: D replaces A. Feb 1: E replaces B.
    current = ["C", "D", "E"]
    events = [
        _event("2022-01-02", ["D"], ["A"]),
        _event("2022-02-01", ["E"], ["B"], source="B", sha=SHA2),
    ]

    history = reconstruct_bist100_memberships(
        current_members=current,
        snapshot_date="2022-03-01",
        events=events,
        target_dates=["2022-01-01", "2022-01-02", "2022-02-01", "2022-02-02"],
        expected_count=3,
    )

    assert history.members_on("2022-01-01") == ("A", "B", "C")
    assert history.members_on("2022-01-02") == ("B", "C", "D")
    assert history.members_on("2022-02-01") == ("C", "D", "E")
    assert history.members_on("2022-02-02") == ("C", "D", "E")


def test_same_effective_date_events_are_reversed_as_one_net_change():
    current = ["C", "D", "E", "F"]
    events = [
        _event("2022-02-01", ["E"], ["A"], source="1", sha=SHA1),
        _event("2022-02-01", ["F"], ["B"], source="2", sha=SHA2),
    ]

    history = reconstruct_bist100_memberships(
        current_members=current,
        snapshot_date="2022-03-01",
        events=events,
        target_dates=["2022-01-31", "2022-02-01"],
        expected_count=4,
    )

    assert history.members_on("2022-01-31") == ("A", "B", "C", "D")
    assert history.members_on("2022-02-01") == ("C", "D", "E", "F")


def test_incomplete_event_stream_fails_closed_against_current_snapshot():
    # D is supposedly included by the event but is absent from the known later snapshot.
    events = [_event("2022-02-01", ["D"], ["A"])]
    with pytest.raises(Bist100HistoryError, match="current snapshot ile tutarsiz"):
        reconstruct_bist100_memberships(
            current_members=["B", "C", "E"],
            snapshot_date="2022-03-01",
            events=events,
            target_dates=["2022-01-01"],
            expected_count=3,
        )


def test_event_group_must_preserve_index_member_count():
    events = [_event("2022-02-01", ["D", "E"], ["A"])]
    with pytest.raises(Bist100HistoryError, match="uye sayisini korumayan"):
        reconstruct_bist100_memberships(
            current_members=["B", "C", "D"],
            snapshot_date="2022-03-01",
            events=events,
            target_dates=["2022-01-01"],
            expected_count=3,
        )


def test_same_ticker_cannot_be_both_included_and_excluded_on_same_date():
    events = [
        _event("2022-02-01", ["D"], ["A"], source="1", sha=SHA1),
        _event("2022-02-01", ["A"], ["D"], source="2", sha=SHA2),
    ]
    with pytest.raises(Bist100HistoryError, match="hem giriyor hem cikiyor"):
        reconstruct_bist100_memberships(
            current_members=["B", "C", "D"],
            snapshot_date="2022-03-01",
            events=events,
            target_dates=["2022-01-01"],
            expected_count=3,
        )


def test_snapshot_must_contain_exact_expected_count():
    with pytest.raises(Bist100HistoryError, match="current snapshot uye sayisi"):
        reconstruct_bist100_memberships(
            current_members=["A", "B"],
            snapshot_date="2022-03-01",
            events=[],
            target_dates=["2022-02-01"],
            expected_count=3,
        )


def test_future_event_relative_to_snapshot_is_rejected():
    with pytest.raises(Bist100HistoryError, match="snapshot tarihinden sonra"):
        reconstruct_bist100_memberships(
            current_members=["A", "B", "C"],
            snapshot_date="2022-03-01",
            events=[_event("2022-04-01", ["D"], ["A"])],
            target_dates=["2022-02-01"],
            expected_count=3,
        )


def test_source_hash_is_mandatory_64_hex():
    with pytest.raises(Bist100HistoryError, match="64-hex"):
        Bist100ConstituentEvent.build(
            effective_date="2022-01-01",
            included=["D"],
            excluded=["A"],
            source_id="official",
            source_sha256="not-a-hash",
        )
