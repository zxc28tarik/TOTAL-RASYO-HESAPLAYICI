from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional


MAX_SYNC_OVERLAP_SECONDS = 86_400
MAX_SYNC_WINDOW_HOURS = 24.0 * 31.0


def _aware(name: str, value: datetime | None, *, required: bool = True) -> datetime | None:
    if value is None:
        if required:
            raise ValueError(f"{name} zorunlu")
        return None
    if not isinstance(value, datetime):
        raise TypeError(f"{name} datetime olmali")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} timezone icermeli")
    return value


def _text(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} dolu metin olmali")
    return value.strip()


@dataclass(frozen=True)
class KapSyncCheckpoint:
    source: str
    stream_name: str
    cursor_value: Optional[str]
    window_start: datetime
    window_end: datetime
    last_success_at: datetime
    rows_seen: int
    pages_fetched: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", _text("source", self.source))
        object.__setattr__(self, "stream_name", _text("stream_name", self.stream_name))
        if self.cursor_value is not None:
            object.__setattr__(self, "cursor_value", _text("cursor_value", self.cursor_value))
        for name in ("window_start", "window_end", "last_success_at"):
            _aware(name, getattr(self, name))
        if self.window_end < self.window_start:
            raise ValueError("checkpoint window_end window_start'tan once olamaz")
        for name in ("rows_seen", "pages_fetched"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} negatif olmayan Python int olmali")


@dataclass(frozen=True)
class KapSyncPlan:
    requested_start: Optional[datetime]
    requested_end: datetime
    start_at: datetime
    end_at: datetime
    resume: bool
    overlap_seconds: int
    max_window_hours: float
    truncated: bool
    checkpoint_window_end: Optional[datetime]


def load_kap_sync_checkpoint(
    conn: Any,
    *,
    source: str = "MKK_KAP_API",
    stream_name: str = "disclosures",
) -> KapSyncCheckpoint | None:
    source = _text("source", source)
    stream_name = _text("stream_name", stream_name)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT source, stream_name, cursor_value, window_start, window_end,
                   last_success_at, rows_seen, pages_fetched
            FROM raw.kap_sync_state
            WHERE source = %s AND stream_name = %s
            """,
            (source, stream_name),
        )
        row = cur.fetchone()
    if row is None:
        return None
    if not isinstance(row, (tuple, list)) or len(row) != 8:
        raise ValueError("kap_sync_state sorgusu 8 kolon dondurmeli")
    return KapSyncCheckpoint(
        source=row[0], stream_name=row[1], cursor_value=row[2],
        window_start=row[3], window_end=row[4], last_success_at=row[5],
        rows_seen=row[6], pages_fetched=row[7],
    )


def plan_kap_sync_window(
    *,
    requested_start: datetime | None,
    requested_end: datetime,
    checkpoint: KapSyncCheckpoint | None = None,
    resume: bool = False,
    overlap_seconds: int = 300,
    max_window_hours: float = 24.0,
) -> KapSyncPlan:
    requested_start = _aware("requested_start", requested_start, required=False)
    requested_end = _aware("requested_end", requested_end)
    if not isinstance(resume, bool):
        raise ValueError("resume Python bool olmali")
    if isinstance(overlap_seconds, bool) or not isinstance(overlap_seconds, int):
        raise ValueError("overlap_seconds negatif olmayan Python int olmali")
    if overlap_seconds < 0 or overlap_seconds > MAX_SYNC_OVERLAP_SECONDS:
        raise ValueError("overlap_seconds guvenli aralik disinda")
    if isinstance(max_window_hours, bool) or not isinstance(max_window_hours, (int, float)):
        raise ValueError("max_window_hours pozitif sonlu sayi olmali")
    max_window_hours = float(max_window_hours)
    if (
        not math.isfinite(max_window_hours)
        or max_window_hours <= 0
        or max_window_hours > MAX_SYNC_WINDOW_HOURS
    ):
        raise ValueError("max_window_hours guvenli aralik disinda")
    if checkpoint is not None and not isinstance(checkpoint, KapSyncCheckpoint):
        raise TypeError("checkpoint KapSyncCheckpoint veya None olmali")

    if resume and checkpoint is not None:
        if checkpoint.cursor_value is not None:
            raise ValueError(
                "tamamlanmis checkpoint cursor_value icermemeli; "
                "kismi cursor state ile resume fail-closed"
            )
        start_at = checkpoint.window_end - timedelta(seconds=overlap_seconds)
        if requested_start is not None:
            start_at = max(start_at, requested_start)
    else:
        if requested_start is None:
            if resume:
                raise ValueError("checkpoint yokken --resume icin requested_start zorunlu")
            raise ValueError("requested_start zorunlu")
        start_at = requested_start

    if requested_end < start_at:
        raise ValueError("requested_end planlanan start_at'tan once olamaz")
    max_end = start_at + timedelta(hours=max_window_hours)
    end_at = min(requested_end, max_end)
    return KapSyncPlan(
        requested_start=requested_start,
        requested_end=requested_end,
        start_at=start_at,
        end_at=end_at,
        resume=resume,
        overlap_seconds=overlap_seconds,
        max_window_hours=max_window_hours,
        truncated=end_at < requested_end,
        checkpoint_window_end=None if checkpoint is None else checkpoint.window_end,
    )


MAX_BACKFILL_WINDOWS = 10_000
MAX_SYNC_LOCK_KEY_BYTES = 512


@dataclass(frozen=True)
class KapBackfillWindow:
    index: int
    start_at: datetime
    end_at: datetime
    overlap_seconds: int

    def __post_init__(self) -> None:
        if isinstance(self.index, bool) or not isinstance(self.index, int) or self.index <= 0:
            raise ValueError("index pozitif Python int olmali")
        _aware("start_at", self.start_at)
        _aware("end_at", self.end_at)
        if self.end_at < self.start_at:
            raise ValueError("end_at start_at'tan once olamaz")
        if isinstance(self.overlap_seconds, bool) or not isinstance(self.overlap_seconds, int) or self.overlap_seconds < 0:
            raise ValueError("overlap_seconds negatif olmayan Python int olmali")


def plan_kap_backfill_windows(
    *,
    start_at: datetime,
    end_at: datetime,
    max_window_hours: float = 24.0,
    overlap_seconds: int = 300,
) -> tuple[KapBackfillWindow, ...]:
    start_at = _aware("start_at", start_at)
    end_at = _aware("end_at", end_at)
    if end_at < start_at:
        raise ValueError("end_at start_at'tan once olamaz")
    if isinstance(overlap_seconds, bool) or not isinstance(overlap_seconds, int):
        raise ValueError("overlap_seconds negatif olmayan Python int olmali")
    if overlap_seconds < 0 or overlap_seconds > MAX_SYNC_OVERLAP_SECONDS:
        raise ValueError("overlap_seconds guvenli aralik disinda")
    if isinstance(max_window_hours, bool) or not isinstance(max_window_hours, (int, float)):
        raise ValueError("max_window_hours pozitif sonlu sayi olmali")
    max_window_hours = float(max_window_hours)
    if not math.isfinite(max_window_hours) or max_window_hours <= 0 or max_window_hours > MAX_SYNC_WINDOW_HOURS:
        raise ValueError("max_window_hours guvenli aralik disinda")
    window_delta = timedelta(hours=max_window_hours)
    if overlap_seconds and timedelta(seconds=overlap_seconds) >= window_delta:
        raise ValueError("overlap_seconds pencere suresinden kucuk olmali")

    windows: list[KapBackfillWindow] = []
    cursor = start_at
    while True:
        current_end = min(end_at, cursor + window_delta)
        windows.append(KapBackfillWindow(
            index=len(windows) + 1,
            start_at=cursor,
            end_at=current_end,
            overlap_seconds=0 if len(windows) == 0 else overlap_seconds,
        ))
        if current_end >= end_at:
            break
        if len(windows) >= MAX_BACKFILL_WINDOWS:
            raise ValueError("backfill pencere sayisi guvenli siniri asiyor")
        cursor = current_end - timedelta(seconds=overlap_seconds)
    return tuple(windows)


def _sync_lock_key(source: str, stream_name: str) -> str:
    source = _text("source", source)
    stream_name = _text("stream_name", stream_name)
    key = f"total_rasyo:kap_sync:{source}:{stream_name}"
    if len(key.encode("utf-8")) > MAX_SYNC_LOCK_KEY_BYTES:
        raise ValueError("sync lock anahtari byte sinirini asiyor")
    return key


def acquire_kap_sync_lock(conn: Any, *, source: str, stream_name: str) -> str:
    key = _sync_lock_key(source, stream_name)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))",
            (key,),
        )
        row = cur.fetchone()
    if not isinstance(row, (tuple, list)) or len(row) != 1 or not isinstance(row[0], bool):
        raise RuntimeError("PostgreSQL advisory lock sorgusu tek bool dondurmeli")
    if row[0] is not True:
        raise RuntimeError(
            f"MKK KAP sync zaten calisiyor: source={source!r}, stream={stream_name!r}"
        )
    return key


def release_kap_sync_lock(conn: Any, lock_key: str) -> None:
    lock_key = _text("lock_key", lock_key)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pg_advisory_unlock(hashtextextended(%s, 0))",
            (lock_key,),
        )
        row = cur.fetchone()
    if not isinstance(row, (tuple, list)) or len(row) != 1 or row[0] is not True:
        raise RuntimeError("PostgreSQL advisory lock serbest birakilamadi")
