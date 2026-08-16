from __future__ import annotations

"""V24-F ingestion for authoritative historical backtest schedules.

No historical value is embedded in this module.  It only canonicalizes,
validates and persists externally sourced wage intervals and monthly PIT
cutoff/execution timestamps into append-only registries.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd


class HistoricalBacktestScheduleError(ValueError):
    pass


def _text(value: object, name: str, *, optional: bool = False) -> str | None:
    if value is None or pd.isna(value):
        if optional:
            return None
        raise HistoricalBacktestScheduleError(f"{name} bos olamaz")
    out = str(value).strip()
    if not out:
        if optional:
            return None
        raise HistoricalBacktestScheduleError(f"{name} bos olamaz")
    return out


def _date(value: object, name: str, *, optional: bool = False) -> date | None:
    if value is None or pd.isna(value) or (isinstance(value, str) and not value.strip()):
        if optional:
            return None
        raise HistoricalBacktestScheduleError(f"{name} bos olamaz")
    try:
        return pd.Timestamp(value).date()
    except Exception as exc:
        raise HistoricalBacktestScheduleError(f"{name} gecersiz tarih") from exc


def _aware_timestamp(value: object, name: str) -> pd.Timestamp:
    if value is None or pd.isna(value) or (isinstance(value, str) and not value.strip()):
        raise HistoricalBacktestScheduleError(f"{name} bos olamaz")
    try:
        ts = pd.Timestamp(value)
    except Exception as exc:
        raise HistoricalBacktestScheduleError(f"{name} gecersiz timestamp") from exc
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise HistoricalBacktestScheduleError(f"{name} timezone-aware olmali")
    return ts


def _positive_decimal(value: object, name: str) -> Decimal:
    if isinstance(value, bool) or value is None or pd.isna(value):
        raise HistoricalBacktestScheduleError(f"{name} pozitif ve sonlu sayi olmali")
    try:
        out = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError) as exc:
        raise HistoricalBacktestScheduleError(f"{name} pozitif ve sonlu sayi olmali") from exc
    if not out.is_finite() or out <= 0:
        raise HistoricalBacktestScheduleError(f"{name} pozitif ve sonlu sayi olmali")
    return out


def _decimal_text(value: Decimal) -> str:
    out = format(value.normalize(), "f")
    if "." in out:
        out = out.rstrip("0").rstrip(".")
    return out


def _sha64(value: object, name: str) -> str:
    out = _text(value, name)
    assert out is not None
    out = out.lower()
    if len(out) != 64 or any(ch not in "0123456789abcdef" for ch in out):
        raise HistoricalBacktestScheduleError(f"{name} 64-hex olmali")
    return out


def _canonical_sha(payload: Mapping[str, object]) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(raw).hexdigest()


def _utc_text(ts: pd.Timestamp) -> str:
    return ts.tz_convert("UTC").isoformat()


@dataclass(frozen=True)
class WageScheduleRecord:
    schedule_key: str
    valid_from: date
    valid_to: date | None
    net_min_wage: Decimal
    source: str
    source_ref: str | None
    source_sha256: str
    row_sha256: str

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> "WageScheduleRecord":
        key = _text(payload.get("schedule_key"), "schedule_key")
        assert key is not None
        valid_from = _date(payload.get("valid_from"), "valid_from")
        valid_to = _date(payload.get("valid_to"), "valid_to", optional=True)
        assert valid_from is not None
        if valid_to is not None and valid_to <= valid_from:
            raise HistoricalBacktestScheduleError("valid_to valid_from'dan sonra olmali")
        wage = _positive_decimal(payload.get("net_min_wage"), "net_min_wage")
        canonical = {
            "schedule_key": key,
            "valid_from": valid_from.isoformat(),
            "valid_to": None if valid_to is None else valid_to.isoformat(),
            "net_min_wage": _decimal_text(wage),
            "source": _text(payload.get("source"), "source"),
            "source_ref": _text(payload.get("source_ref"), "source_ref", optional=True),
            "source_sha256": _sha64(payload.get("source_sha256"), "source_sha256"),
        }
        return cls(
            schedule_key=key,
            valid_from=valid_from,
            valid_to=valid_to,
            net_min_wage=wage,
            source=str(canonical["source"]),
            source_ref=canonical["source_ref"],
            source_sha256=str(canonical["source_sha256"]),
            row_sha256=_canonical_sha(canonical),
        )

    def canonical_dict(self) -> dict[str, object]:
        return {
            "schedule_key": self.schedule_key,
            "valid_from": self.valid_from.isoformat(),
            "valid_to": None if self.valid_to is None else self.valid_to.isoformat(),
            "net_min_wage": _decimal_text(self.net_min_wage),
            "source": self.source,
            "source_ref": self.source_ref,
            "source_sha256": self.source_sha256,
        }


@dataclass(frozen=True)
class CutoffScheduleRecord:
    profile_key: str
    signal_date: date
    cutoff_at: datetime
    execution_at: datetime
    source: str
    source_ref: str | None
    source_sha256: str
    row_sha256: str

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> "CutoffScheduleRecord":
        key = _text(payload.get("profile_key"), "profile_key")
        assert key is not None
        signal_date = _date(payload.get("signal_date"), "signal_date")
        assert signal_date is not None
        cutoff = _aware_timestamp(payload.get("cutoff_at"), "cutoff_at")
        execution = _aware_timestamp(payload.get("execution_at"), "execution_at")
        if cutoff >= execution:
            raise HistoricalBacktestScheduleError("cutoff_at execution_at'tan once olmali")
        if execution.tz_convert("Europe/Istanbul").date() != signal_date:
            raise HistoricalBacktestScheduleError(
                "execution_at Istanbul takvim gunu signal_date ile ayni olmali"
            )
        canonical = {
            "profile_key": key,
            "signal_date": signal_date.isoformat(),
            "cutoff_at": _utc_text(cutoff),
            "execution_at": _utc_text(execution),
            "source": _text(payload.get("source"), "source"),
            "source_ref": _text(payload.get("source_ref"), "source_ref", optional=True),
            "source_sha256": _sha64(payload.get("source_sha256"), "source_sha256"),
        }
        return cls(
            profile_key=key,
            signal_date=signal_date,
            cutoff_at=cutoff.to_pydatetime(),
            execution_at=execution.to_pydatetime(),
            source=str(canonical["source"]),
            source_ref=canonical["source_ref"],
            source_sha256=str(canonical["source_sha256"]),
            row_sha256=_canonical_sha(canonical),
        )

    def canonical_dict(self) -> dict[str, object]:
        return {
            "profile_key": self.profile_key,
            "signal_date": self.signal_date.isoformat(),
            "cutoff_at": _utc_text(pd.Timestamp(self.cutoff_at)),
            "execution_at": _utc_text(pd.Timestamp(self.execution_at)),
            "source": self.source,
            "source_ref": self.source_ref,
            "source_sha256": self.source_sha256,
        }


def _validate_wage_non_overlapping(rows: Iterable[WageScheduleRecord]) -> tuple[WageScheduleRecord, ...]:
    items = tuple(rows)
    grouped: dict[str, list[WageScheduleRecord]] = {}
    for row in items:
        grouped.setdefault(row.schedule_key, []).append(row)
    for key, group in grouped.items():
        ordered = sorted(group, key=lambda x: x.valid_from)
        for left, right in zip(ordered, ordered[1:]):
            if left.valid_to is None or right.valid_from < left.valid_to:
                raise HistoricalBacktestScheduleError(
                    f"{key} icin cakisan minimum-wage araliklari"
                )
    return items


def wage_records_from_frame(frame: pd.DataFrame) -> tuple[WageScheduleRecord, ...]:
    required = {
        "schedule_key", "valid_from", "valid_to", "net_min_wage",
        "source", "source_sha256",
    }
    missing = required - set(frame.columns)
    if missing:
        raise HistoricalBacktestScheduleError(f"eksik wage kolonlari: {sorted(missing)}")
    rows: list[WageScheduleRecord] = []
    seen: dict[tuple[str, date], str] = {}
    for line_no, payload in enumerate(frame.to_dict("records"), start=2):
        try:
            row = WageScheduleRecord.from_mapping(payload)
        except HistoricalBacktestScheduleError as exc:
            raise HistoricalBacktestScheduleError(f"satir {line_no}: {exc}") from exc
        identity = (row.schedule_key, row.valid_from)
        prior = seen.get(identity)
        if prior is not None:
            if prior != row.row_sha256:
                raise HistoricalBacktestScheduleError(
                    f"satir {line_no}: ayni schedule_key+valid_from farkli icerik tasiyor"
                )
            continue
        seen[identity] = row.row_sha256
        rows.append(row)
    if not rows:
        raise HistoricalBacktestScheduleError("minimum-wage schedule bos")
    return _validate_wage_non_overlapping(rows)


def cutoff_records_from_frame(frame: pd.DataFrame) -> tuple[CutoffScheduleRecord, ...]:
    required = {
        "profile_key", "signal_date", "cutoff_at", "execution_at",
        "source", "source_sha256",
    }
    missing = required - set(frame.columns)
    if missing:
        raise HistoricalBacktestScheduleError(f"eksik cutoff kolonlari: {sorted(missing)}")
    rows: list[CutoffScheduleRecord] = []
    seen: dict[tuple[str, date], str] = {}
    for line_no, payload in enumerate(frame.to_dict("records"), start=2):
        try:
            row = CutoffScheduleRecord.from_mapping(payload)
        except HistoricalBacktestScheduleError as exc:
            raise HistoricalBacktestScheduleError(f"satir {line_no}: {exc}") from exc
        identity = (row.profile_key, row.signal_date)
        prior = seen.get(identity)
        if prior is not None:
            if prior != row.row_sha256:
                raise HistoricalBacktestScheduleError(
                    f"satir {line_no}: ayni profile_key+signal_date farkli icerik tasiyor"
                )
            continue
        seen[identity] = row.row_sha256
        rows.append(row)
    if not rows:
        raise HistoricalBacktestScheduleError("cutoff schedule bos")
    return tuple(rows)


def _load_csv(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if not p.is_file():
        raise HistoricalBacktestScheduleError(f"schedule CSV bulunamadi: {p}")
    try:
        frame = pd.read_csv(p, dtype=object)
    except Exception as exc:
        raise HistoricalBacktestScheduleError(f"schedule CSV okunamadi: {p}") from exc
    if len(frame) > 100_000:
        raise HistoricalBacktestScheduleError("schedule CSV 100000 satir sinirini asiyor")
    return frame


def load_wage_schedule_csv(path: str | Path) -> tuple[WageScheduleRecord, ...]:
    return wage_records_from_frame(_load_csv(path))


def load_cutoff_schedule_csv(path: str | Path) -> tuple[CutoffScheduleRecord, ...]:
    return cutoff_records_from_frame(_load_csv(path))


WAGE_INSERT_SQL = """
INSERT INTO core.backtest_minimum_wage_schedule (
  schedule_key, valid_from, valid_to, net_min_wage,
  source, source_ref, source_sha256, row_sha256
) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT (schedule_key, valid_from) DO NOTHING
RETURNING row_sha256
"""

CUTOFF_INSERT_SQL = """
INSERT INTO analytics.backtest_signal_cutoff_schedule (
  profile_key, signal_date, cutoff_at, execution_at,
  source, source_ref, source_sha256, row_sha256
) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT (profile_key, signal_date) DO NOTHING
RETURNING row_sha256
"""


def persist_wage_schedule_records(conn: Any, rows: Iterable[WageScheduleRecord]) -> int:
    items = _validate_wage_non_overlapping(tuple(rows))
    inserted = 0
    with conn:
        with conn.cursor() as cur:
            for row in items:
                if not isinstance(row, WageScheduleRecord):
                    raise HistoricalBacktestScheduleError("wage rows WageScheduleRecord olmali")
                if WageScheduleRecord.from_mapping(row.canonical_dict()) != row:
                    raise HistoricalBacktestScheduleError("wage record kanonik degil")
                cur.execute(
                    WAGE_INSERT_SQL,
                    (row.schedule_key, row.valid_from, row.valid_to, str(row.net_min_wage),
                     row.source, row.source_ref, row.source_sha256, row.row_sha256),
                )
                returned = cur.fetchone()
                if returned is not None:
                    inserted += 1
                    continue
                cur.execute(
                    """SELECT row_sha256 FROM core.backtest_minimum_wage_schedule
                       WHERE schedule_key=%s AND valid_from=%s""",
                    (row.schedule_key, row.valid_from),
                )
                existing = cur.fetchone()
                if existing is None or str(existing[0]).strip() != row.row_sha256:
                    raise HistoricalBacktestScheduleError(
                        "wage exact replay kimligi farkli icerik tasiyor"
                    )
    return inserted


def persist_cutoff_schedule_records(conn: Any, rows: Iterable[CutoffScheduleRecord]) -> int:
    items = tuple(rows)
    inserted = 0
    with conn:
        with conn.cursor() as cur:
            for row in items:
                if not isinstance(row, CutoffScheduleRecord):
                    raise HistoricalBacktestScheduleError("cutoff rows CutoffScheduleRecord olmali")
                if CutoffScheduleRecord.from_mapping(row.canonical_dict()) != row:
                    raise HistoricalBacktestScheduleError("cutoff record kanonik degil")
                cur.execute(
                    CUTOFF_INSERT_SQL,
                    (row.profile_key, row.signal_date, row.cutoff_at, row.execution_at,
                     row.source, row.source_ref, row.source_sha256, row.row_sha256),
                )
                returned = cur.fetchone()
                if returned is not None:
                    inserted += 1
                    continue
                cur.execute(
                    """SELECT row_sha256 FROM analytics.backtest_signal_cutoff_schedule
                       WHERE profile_key=%s AND signal_date=%s""",
                    (row.profile_key, row.signal_date),
                )
                existing = cur.fetchone()
                if existing is None or str(existing[0]).strip() != row.row_sha256:
                    raise HistoricalBacktestScheduleError(
                        "cutoff exact replay kimligi farkli icerik tasiyor"
                    )
    return inserted
