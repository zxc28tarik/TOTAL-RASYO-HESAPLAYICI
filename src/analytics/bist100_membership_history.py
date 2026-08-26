from __future__ import annotations

"""Fail-closed reconstruction of historical BIST 100 membership.

The reconstruction starts from a later, authoritative 100-member snapshot and
reverses an audited stream of constituent replacement events.  This module does
not discover announcements, infer missing events, or repair ticker identities.

Event semantics are effective-date semantics: if a change is effective on day
D, the new constituents are members on D.  Therefore reconstructing membership
for target day T reverses only events with ``effective_date > T``.
"""

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Mapping, Sequence

import pandas as pd


class Bist100HistoryError(ValueError):
    pass


@dataclass(frozen=True)
class Bist100ConstituentEvent:
    effective_date: date
    included: tuple[str, ...]
    excluded: tuple[str, ...]
    source_id: str
    source_sha256: str
    event_type: str = "CONSTITUENT_CHANGE"

    @classmethod
    def build(
        cls,
        *,
        effective_date: object,
        included: Iterable[object],
        excluded: Iterable[object],
        source_id: object,
        source_sha256: object,
        event_type: object = "CONSTITUENT_CHANGE",
    ) -> "Bist100ConstituentEvent":
        day = _as_date(effective_date, "effective_date")
        inc = _ticker_tuple(included, "included")
        exc = _ticker_tuple(excluded, "excluded")
        sid = str(source_id).strip()
        sha = str(source_sha256).strip().lower()
        kind = str(event_type).strip().upper()
        if not sid:
            raise Bist100HistoryError("source_id bos olamaz")
        if len(sha) != 64 or any(ch not in "0123456789abcdef" for ch in sha):
            raise Bist100HistoryError("source_sha256 64-hex olmali")
        if not kind:
            raise Bist100HistoryError("event_type bos olamaz")
        if not inc and not exc:
            raise Bist100HistoryError("event en az bir include/exclude icermeli")
        if set(inc) & set(exc):
            raise Bist100HistoryError("ayni ticker ayni eventte hem included hem excluded olamaz")
        return cls(day, inc, exc, sid, sha, kind)


@dataclass(frozen=True)
class Bist100MembershipHistory:
    snapshot_date: date
    expected_count: int
    memberships: Mapping[date, tuple[str, ...]]
    applied_event_dates: tuple[date, ...]

    def members_on(self, day: object) -> tuple[str, ...]:
        key = _as_date(day, "day")
        try:
            return self.memberships[key]
        except KeyError as exc:
            raise Bist100HistoryError(f"membership target tarihi hesaplanmadi: {key.isoformat()}") from exc


def _as_date(value: object, field: str) -> date:
    try:
        ts = pd.Timestamp(value)
    except Exception as exc:
        raise Bist100HistoryError(f"{field} gecerli tarih olmali") from exc
    if pd.isna(ts):
        raise Bist100HistoryError(f"{field} gecerli tarih olmali")
    return ts.date()


def _ticker_tuple(values: Iterable[object], field: str) -> tuple[str, ...]:
    out: list[str] = []
    for raw in values:
        ticker = str(raw).strip().upper()
        if not ticker or ticker in {"NONE", "NAN", "<NA>"}:
            raise Bist100HistoryError(f"{field} bos/gecersiz ticker iceriyor")
        out.append(ticker)
    if len(out) != len(set(out)):
        raise Bist100HistoryError(f"{field} duplicate ticker iceriyor")
    return tuple(sorted(out))


def _snapshot(values: Iterable[object], expected_count: int) -> set[str]:
    if int(expected_count) <= 0:
        raise Bist100HistoryError("expected_count pozitif olmali")
    members = set(_ticker_tuple(values, "current_members"))
    if len(members) != int(expected_count):
        raise Bist100HistoryError(
            f"current snapshot uye sayisi {len(members)}, beklenen {int(expected_count)}"
        )
    return members


def _group_events(
    events: Sequence[Bist100ConstituentEvent],
    *,
    snapshot_date: date,
) -> dict[date, tuple[set[str], set[str]]]:
    grouped: dict[date, tuple[set[str], set[str]]] = {}
    for event in events:
        if event.effective_date > snapshot_date:
            raise Bist100HistoryError(
                f"event snapshot tarihinden sonra: {event.effective_date.isoformat()}"
            )
        included, excluded = grouped.setdefault(event.effective_date, (set(), set()))
        dup_inc = included & set(event.included)
        dup_exc = excluded & set(event.excluded)
        if dup_inc or dup_exc:
            raise Bist100HistoryError(
                f"ayni etkin tarihte duplicate event ticker: included={sorted(dup_inc)} excluded={sorted(dup_exc)}"
            )
        included.update(event.included)
        excluded.update(event.excluded)

    for day, (included, excluded) in grouped.items():
        overlap = included & excluded
        if overlap:
            raise Bist100HistoryError(
                f"ayni etkin tarihte ticker hem giriyor hem cikiyor: {day.isoformat()} {sorted(overlap)}"
            )
        if len(included) != len(excluded):
            raise Bist100HistoryError(
                f"uye sayisini korumayan event grubu: {day.isoformat()} included={len(included)} excluded={len(excluded)}"
            )
    return grouped


def reconstruct_bist100_memberships(
    *,
    current_members: Iterable[object],
    snapshot_date: object,
    events: Sequence[Bist100ConstituentEvent],
    target_dates: Iterable[object],
    expected_count: int = 100,
) -> Bist100MembershipHistory:
    snap_day = _as_date(snapshot_date, "snapshot_date")
    members = _snapshot(current_members, expected_count)
    targets = sorted({_as_date(value, "target_date") for value in target_dates}, reverse=True)
    if not targets:
        raise Bist100HistoryError("en az bir target_date gerekli")
    if targets[0] > snap_day:
        raise Bist100HistoryError("target_date snapshot tarihinden sonra olamaz")

    grouped = _group_events(events, snapshot_date=snap_day)
    event_days_desc = sorted(grouped, reverse=True)
    memberships: dict[date, tuple[str, ...]] = {}
    applied_days: list[date] = []
    event_index = 0

    for target in targets:
        # Reverse only changes strictly later than target. A change effective on
        # target day already applies to that day's BIST 100 membership.
        while event_index < len(event_days_desc) and event_days_desc[event_index] > target:
            day = event_days_desc[event_index]
            included, excluded = grouped[day]

            missing_included = included - members
            unexpectedly_present_excluded = excluded & members
            if missing_included or unexpectedly_present_excluded:
                raise Bist100HistoryError(
                    "event stream current snapshot ile tutarsiz: "
                    f"date={day.isoformat()} missing_included={sorted(missing_included)} "
                    f"present_excluded={sorted(unexpectedly_present_excluded)}"
                )

            members.difference_update(included)
            members.update(excluded)
            if len(members) != int(expected_count):
                raise Bist100HistoryError(
                    f"event geri alindiktan sonra uye sayisi bozuldu: {day.isoformat()} count={len(members)}"
                )
            applied_days.append(day)
            event_index += 1

        memberships[target] = tuple(sorted(members))

    return Bist100MembershipHistory(
        snapshot_date=snap_day,
        expected_count=int(expected_count),
        memberships=dict(sorted(memberships.items())),
        applied_event_dates=tuple(applied_days),
    )
