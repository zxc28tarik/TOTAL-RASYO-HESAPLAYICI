from __future__ import annotations

"""Load the hash-proved periodic BIST 100 constituent event stream."""

import json
from pathlib import Path

from src.analytics.bist100_membership_history import (
    Bist100ConstituentEvent,
    Bist100HistoryError,
)


class Bist100PeriodicSourceError(ValueError):
    pass


def load_bist100_periodic_events_json(
    path: str | Path,
) -> tuple[Bist100ConstituentEvent, ...]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        raise Bist100PeriodicSourceError(f"periodic source okunamadi: {exc}") from exc

    if payload.get("publisher") != "Borsa Istanbul A.S.":
        raise Bist100PeriodicSourceError("periodic source publisher beklenenden farkli")
    rows = payload.get("events")
    if not isinstance(rows, list) or not rows:
        raise Bist100PeriodicSourceError("periodic source events bos/gecersiz")
    if int(payload.get("event_group_count", -1)) != len(rows):
        raise Bist100PeriodicSourceError("event_group_count events ile uyusmuyor")

    built: list[Bist100ConstituentEvent] = []
    replacement_pairs = 0
    seen_quarters: set[str] = set()
    for row in rows:
        quarter = str(row.get("quarter", "")).strip().upper()
        if not quarter or quarter in seen_quarters:
            raise Bist100PeriodicSourceError(f"quarter bos/duplicate: {quarter!r}")
        seen_quarters.add(quarter)
        if row.get("event_type") != "PERIODIC_CONSTITUENT_CHANGE":
            raise Bist100PeriodicSourceError(f"{quarter}: event_type gecersiz")
        included = row.get("included")
        excluded = row.get("excluded")
        if not isinstance(included, list) or not isinstance(excluded, list):
            raise Bist100PeriodicSourceError(f"{quarter}: include/exclude liste olmali")
        if len(included) != len(excluded):
            raise Bist100PeriodicSourceError(f"{quarter}: uye sayisini korumuyor")
        source_url = str(row.get("source_final_url", "")).strip()
        if not source_url.startswith("https://borsaistanbul.com/"):
            raise Bist100PeriodicSourceError(f"{quarter}: resmi Borsa source URL yok")
        source_sha = str(row.get("source_sha256", "")).strip().lower()
        try:
            event = Bist100ConstituentEvent.build(
                effective_date=row.get("effective_date"),
                included=included,
                excluded=excluded,
                source_id=f"{quarter}|{source_url}",
                source_sha256=source_sha,
                event_type="PERIODIC_CONSTITUENT_CHANGE",
            )
        except Bist100HistoryError as exc:
            raise Bist100PeriodicSourceError(f"{quarter}: {exc}") from exc
        built.append(event)
        replacement_pairs += len(included)

    if int(payload.get("replacement_pair_count", -1)) != replacement_pairs:
        raise Bist100PeriodicSourceError("replacement_pair_count source ile uyusmuyor")

    ordered = tuple(sorted(built, key=lambda e: e.effective_date))
    if len({e.effective_date for e in ordered}) != len(ordered):
        raise Bist100PeriodicSourceError("iki periodic quarter ayni effective_date'e sahip")
    return ordered
