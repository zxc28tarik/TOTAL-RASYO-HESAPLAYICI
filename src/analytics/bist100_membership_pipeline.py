from __future__ import annotations

"""Compose BIST100 constituent events with independent ticker-code lineage."""

from typing import Iterable, Sequence

from src.analytics.bist100_membership_history import (
    Bist100ConstituentEvent,
    Bist100MembershipHistory,
    _as_date,
    reconstruct_bist100_memberships,
)
from src.analytics.ticker_lineage import TickerLineageResolver


def reconstruct_bist100_memberships_with_ticker_lineage(
    *,
    current_members: Iterable[object],
    snapshot_date: object,
    constituent_events: Sequence[Bist100ConstituentEvent],
    ticker_lineage: TickerLineageResolver,
    target_dates: Iterable[object],
    expected_count: int = 100,
) -> Bist100MembershipHistory:
    """Reconstruct historical members without mistaking ticker renames for membership changes.

    Constituent events are expressed with the ticker codes valid on each event's
    effective date. They are first mapped forward into snapshot-date identity
    space, where the existing fail-closed reverse reconstructor runs. Final
    memberships are then rendered back to the ticker code valid on each target
    date for price lookup and point-in-time reporting.
    """
    snap = _as_date(snapshot_date, "snapshot_date")
    canonical_events: list[Bist100ConstituentEvent] = []
    for event in constituent_events:
        canonical_events.append(
            Bist100ConstituentEvent.build(
                effective_date=event.effective_date,
                included=ticker_lineage.forward_set(
                    event.included,
                    from_date=event.effective_date,
                    to_date=snap,
                ),
                excluded=ticker_lineage.forward_set(
                    event.excluded,
                    from_date=event.effective_date,
                    to_date=snap,
                ),
                source_id=event.source_id,
                source_sha256=event.source_sha256,
                event_type=event.event_type,
            )
        )

    canonical = reconstruct_bist100_memberships(
        current_members=current_members,
        snapshot_date=snap,
        events=canonical_events,
        target_dates=target_dates,
        expected_count=expected_count,
    )
    rendered = {
        day: ticker_lineage.backward_set(
            members,
            target_date=day,
            snapshot_date=snap,
        )
        for day, members in canonical.memberships.items()
    }
    return Bist100MembershipHistory(
        snapshot_date=canonical.snapshot_date,
        expected_count=canonical.expected_count,
        memberships=rendered,
        applied_event_dates=canonical.applied_event_dates,
    )
