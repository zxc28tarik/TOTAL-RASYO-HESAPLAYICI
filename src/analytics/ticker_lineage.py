from __future__ import annotations

"""Fail-closed ticker identity resolver for historical BIST reconstruction."""

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable, Sequence

import pandas as pd


class TickerLineageError(ValueError):
    pass


@dataclass(frozen=True)
class TickerCodeChange:
    effective_date: date
    old_ticker: str
    new_ticker: str
    source_sha256: str
    event_sha256: str

    @classmethod
    def build(
        cls,
        *,
        effective_date: object,
        old_ticker: object,
        new_ticker: object,
        source_sha256: object,
        event_sha256: object,
    ) -> "TickerCodeChange":
        day = _as_date(effective_date)
        old = _ticker(old_ticker)
        new = _ticker(new_ticker)
        if old == new:
            raise TickerLineageError("old_ticker ve new_ticker ayni olamaz")
        source = _sha(source_sha256, "source_sha256")
        event = _sha(event_sha256, "event_sha256")
        return cls(day, old, new, source, event)


def _as_date(value: object) -> date:
    try:
        ts = pd.Timestamp(value)
    except Exception as exc:
        raise TickerLineageError("effective_date gecerli tarih olmali") from exc
    if pd.isna(ts):
        raise TickerLineageError("effective_date gecerli tarih olmali")
    return ts.date()


def _ticker(value: object) -> str:
    text = str(value).strip().upper()
    if not text or text in {"NAN", "NONE", "<NA>"}:
        raise TickerLineageError("ticker bos/gecersiz olamaz")
    return text


def _sha(value: object, field: str) -> str:
    text = str(value).strip().lower()
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise TickerLineageError(f"{field} 64-hex olmali")
    return text


def load_ticker_code_changes_csv(path: str | Path) -> tuple[TickerCodeChange, ...]:
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    required = {
        "effective_date",
        "old_ticker",
        "new_ticker",
        "source_workbook_sha256",
        "event_sha256",
    }
    missing = required - set(frame.columns)
    if missing:
        raise TickerLineageError(f"ticker lineage CSV kolonlari eksik: {sorted(missing)}")
    events = tuple(
        TickerCodeChange.build(
            effective_date=row.effective_date,
            old_ticker=row.old_ticker,
            new_ticker=row.new_ticker,
            source_sha256=row.source_workbook_sha256,
            event_sha256=row.event_sha256,
        )
        for row in frame.itertuples(index=False)
    )
    return events


class TickerLineageResolver:
    def __init__(self, events: Sequence[TickerCodeChange]):
        self.events = tuple(sorted(events, key=lambda e: (e.effective_date, e.old_ticker, e.new_ticker)))
        self._validate()

    def _validate(self) -> None:
        seen_exact: set[tuple[date, str, str]] = set()
        outgoing: dict[str, list[TickerCodeChange]] = {}
        incoming: dict[str, list[TickerCodeChange]] = {}
        by_date: dict[date, list[TickerCodeChange]] = {}
        for event in self.events:
            key = (event.effective_date, event.old_ticker, event.new_ticker)
            if key in seen_exact:
                raise TickerLineageError(f"duplicate ticker-change event: {key}")
            seen_exact.add(key)
            outgoing.setdefault(event.old_ticker, []).append(event)
            incoming.setdefault(event.new_ticker, []).append(event)
            by_date.setdefault(event.effective_date, []).append(event)

        # One ticker identity cannot fork to two different new codes. A ticker may
        # reappear only as the result of an earlier change and later change again.
        for old, rows in outgoing.items():
            if len(rows) > 1:
                raise TickerLineageError(f"ticker lineage fork/old-code reuse desteklenmiyor: {old}")
        for new, rows in incoming.items():
            if len(rows) > 1:
                raise TickerLineageError(f"ticker lineage merge/new-code reuse desteklenmiyor: {new}")
        for day, rows in by_date.items():
            olds = {row.old_ticker for row in rows}
            news = {row.new_ticker for row in rows}
            overlap = olds & news
            if overlap:
                raise TickerLineageError(
                    f"ayni gunde zincir ticker degisimi desteklenmiyor: {day.isoformat()} {sorted(overlap)}"
                )

        # Detect cycles across dates.
        next_code = {event.old_ticker: event.new_ticker for event in self.events}
        for start in next_code:
            cur = start
            visited: set[str] = set()
            while cur in next_code:
                if cur in visited:
                    raise TickerLineageError(f"ticker lineage cycle: {start}")
                visited.add(cur)
                cur = next_code[cur]

    def forward(self, ticker: object, *, from_date: object, to_date: object) -> str:
        """Map the code valid on from_date to the code valid on to_date.

        A code change effective on D is already reflected in the ticker valid on D,
        so only events with from_date < D <= to_date are applied.
        """
        current = _ticker(ticker)
        left = _as_date(from_date)
        right = _as_date(to_date)
        if right < left:
            raise TickerLineageError("to_date from_date'ten once olamaz")
        for event in self.events:
            if left < event.effective_date <= right and current == event.old_ticker:
                current = event.new_ticker
        return current

    def backward(self, ticker: object, *, target_date: object, snapshot_date: object) -> str:
        """Map a snapshot-date code to the historical code valid on target_date."""
        current = _ticker(ticker)
        target = _as_date(target_date)
        snap = _as_date(snapshot_date)
        if target > snap:
            raise TickerLineageError("target_date snapshot_date'ten sonra olamaz")
        for event in reversed(self.events):
            if target < event.effective_date <= snap and current == event.new_ticker:
                current = event.old_ticker
        return current

    def forward_set(self, tickers: Iterable[object], *, from_date: object, to_date: object) -> tuple[str, ...]:
        mapped = [self.forward(t, from_date=from_date, to_date=to_date) for t in tickers]
        if len(mapped) != len(set(mapped)):
            raise TickerLineageError("forward mapping iki ticker kimligini ayni koda birlestirdi")
        return tuple(sorted(mapped))

    def backward_set(self, tickers: Iterable[object], *, target_date: object, snapshot_date: object) -> tuple[str, ...]:
        mapped = [self.backward(t, target_date=target_date, snapshot_date=snapshot_date) for t in tickers]
        if len(mapped) != len(set(mapped)):
            raise TickerLineageError("backward mapping iki ticker kimligini ayni koda birlestirdi")
        return tuple(sorted(mapped))
