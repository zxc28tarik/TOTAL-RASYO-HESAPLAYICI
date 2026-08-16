from __future__ import annotations

"""V24-D historical BIST universe membership ingestion.

The current ``core.universe_stocks`` table is a mutable one-row-per-ticker
snapshot and therefore cannot support survivorship-bias-free historical
backtests.  This module ingests explicit half-open historical intervals into the
append-only ``core.universe_membership_history`` table.
"""

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd


class HistoricalUniverseIngestError(ValueError):
    pass


def _text(value: object, name: str, *, optional: bool = False) -> str | None:
    if value is None or pd.isna(value):
        if optional:
            return None
        raise HistoricalUniverseIngestError(f"{name} bos olamaz")
    if not isinstance(value, str):
        value = str(value)
    out = value.strip()
    if not out:
        if optional:
            return None
        raise HistoricalUniverseIngestError(f"{name} bos olamaz")
    return out


def _date(value: object, name: str, *, optional: bool = False) -> date | None:
    if value is None or pd.isna(value) or (isinstance(value, str) and not value.strip()):
        if optional:
            return None
        raise HistoricalUniverseIngestError(f"{name} bos olamaz")
    try:
        return pd.Timestamp(value).date()
    except Exception as exc:
        raise HistoricalUniverseIngestError(f"{name} gecersiz tarih") from exc


def _bool(value: object, name: str) -> bool:
    if value is None or pd.isna(value):
        raise HistoricalUniverseIngestError(f"{name} bos olamaz")
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        token = value.strip().lower()
        if token in {"true", "1", "yes", "evet"}:
            return True
        if token in {"false", "0", "no", "hayir", "hayır"}:
            return False
    raise HistoricalUniverseIngestError(f"{name} true/false olmali")


def _sha64(value: object, name: str) -> str:
    out = _text(value, name)
    assert out is not None
    out = out.lower()
    if len(out) != 64 or any(ch not in "0123456789abcdef" for ch in out):
        raise HistoricalUniverseIngestError(f"{name} 64-hex olmali")
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


@dataclass(frozen=True)
class UniverseMembershipRecord:
    ticker: str
    valid_from: date
    valid_to: date | None
    is_tradable: bool
    company_name: str | None
    sector_index_code: str | None
    sector_code: str | None
    source: str
    source_ref: str | None
    source_sha256: str
    row_sha256: str

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> "UniverseMembershipRecord":
        ticker = _text(payload.get("ticker"), "ticker")
        assert ticker is not None
        ticker = ticker.upper()
        valid_from = _date(payload.get("valid_from"), "valid_from")
        valid_to = _date(payload.get("valid_to"), "valid_to", optional=True)
        assert valid_from is not None
        if valid_to is not None and valid_to <= valid_from:
            raise HistoricalUniverseIngestError("valid_to valid_from'dan sonra olmali")
        canonical = {
            "ticker": ticker,
            "valid_from": valid_from.isoformat(),
            "valid_to": None if valid_to is None else valid_to.isoformat(),
            "is_tradable": _bool(payload.get("is_tradable"), "is_tradable"),
            "company_name": _text(payload.get("company_name"), "company_name", optional=True),
            "sector_index_code": _text(payload.get("sector_index_code"), "sector_index_code", optional=True),
            "sector_code": _text(payload.get("sector_code"), "sector_code", optional=True),
            "source": _text(payload.get("source"), "source"),
            "source_ref": _text(payload.get("source_ref"), "source_ref", optional=True),
            "source_sha256": _sha64(payload.get("source_sha256"), "source_sha256"),
        }
        return cls(
            ticker=canonical["ticker"],
            valid_from=valid_from,
            valid_to=valid_to,
            is_tradable=bool(canonical["is_tradable"]),
            company_name=canonical["company_name"],
            sector_index_code=canonical["sector_index_code"],
            sector_code=canonical["sector_code"],
            source=str(canonical["source"]),
            source_ref=canonical["source_ref"],
            source_sha256=str(canonical["source_sha256"]),
            row_sha256=_canonical_sha(canonical),
        )

    def canonical_dict(self) -> dict[str, object]:
        return {
            "ticker": self.ticker,
            "valid_from": self.valid_from.isoformat(),
            "valid_to": None if self.valid_to is None else self.valid_to.isoformat(),
            "is_tradable": self.is_tradable,
            "company_name": self.company_name,
            "sector_index_code": self.sector_index_code,
            "sector_code": self.sector_code,
            "source": self.source,
            "source_ref": self.source_ref,
            "source_sha256": self.source_sha256,
        }


def _validate_non_overlapping(rows: Iterable[UniverseMembershipRecord]) -> tuple[UniverseMembershipRecord, ...]:
    items = tuple(rows)
    by_ticker: dict[str, list[UniverseMembershipRecord]] = {}
    for row in items:
        by_ticker.setdefault(row.ticker, []).append(row)
    for ticker, group in by_ticker.items():
        ordered = sorted(group, key=lambda row: row.valid_from)
        for left, right in zip(ordered, ordered[1:]):
            if left.valid_to is None or right.valid_from < left.valid_to:
                raise HistoricalUniverseIngestError(
                    f"{ticker} icin cakisan historical universe araliklari"
                )
    return items


def records_from_frame(frame: pd.DataFrame) -> tuple[UniverseMembershipRecord, ...]:
    required = {"ticker", "valid_from", "valid_to", "is_tradable", "source", "source_sha256"}
    missing = required - set(frame.columns)
    if missing:
        raise HistoricalUniverseIngestError(f"eksik kolonlar: {sorted(missing)}")
    rows: list[UniverseMembershipRecord] = []
    seen: dict[tuple[str, date], str] = {}
    for line_no, payload in enumerate(frame.to_dict("records"), start=2):
        try:
            row = UniverseMembershipRecord.from_mapping(payload)
        except HistoricalUniverseIngestError as exc:
            raise HistoricalUniverseIngestError(f"satir {line_no}: {exc}") from exc
        key = (row.ticker, row.valid_from)
        prior = seen.get(key)
        if prior is not None:
            if prior != row.row_sha256:
                raise HistoricalUniverseIngestError(
                    f"satir {line_no}: ayni ticker+valid_from farkli icerik tasiyor"
                )
            continue
        seen[key] = row.row_sha256
        rows.append(row)
    if not rows:
        raise HistoricalUniverseIngestError("historical universe girdisi bos")
    return _validate_non_overlapping(rows)


def load_historical_universe_csv(path: str | Path) -> tuple[UniverseMembershipRecord, ...]:
    p = Path(path)
    if not p.is_file():
        raise HistoricalUniverseIngestError(f"historical universe CSV bulunamadi: {p}")
    try:
        frame = pd.read_csv(p, dtype=object)
    except Exception as exc:
        raise HistoricalUniverseIngestError(f"historical universe CSV okunamadi: {p}") from exc
    if len(frame) > 1_000_000:
        raise HistoricalUniverseIngestError("historical universe CSV 1000000 satir sinirini asiyor")
    return records_from_frame(frame)


INSERT_SQL = """
INSERT INTO core.universe_membership_history (
  ticker, valid_from, valid_to, is_tradable,
  company_name, sector_index_code, sector_code,
  source, source_ref, source_sha256, row_sha256
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (ticker, valid_from) DO NOTHING
RETURNING row_sha256
"""


def persist_historical_universe_records(conn: Any, rows: Iterable[UniverseMembershipRecord]) -> int:
    items = _validate_non_overlapping(tuple(rows))
    if not items:
        return 0
    for row in items:
        if not isinstance(row, UniverseMembershipRecord):
            raise HistoricalUniverseIngestError("persist rows UniverseMembershipRecord olmali")
        rebuilt = UniverseMembershipRecord.from_mapping(row.canonical_dict())
        if rebuilt != row:
            raise HistoricalUniverseIngestError("historical universe record kanonik degil")

    inserted = 0
    with conn:
        with conn.cursor() as cur:
            for row in items:
                cur.execute(
                    "SELECT row_sha256 FROM core.universe_membership_history WHERE ticker=%s AND valid_from=%s",
                    (row.ticker, row.valid_from),
                )
                existing_before = cur.fetchone()
                if existing_before:
                    if str(existing_before[0]).lower() != row.row_sha256:
                        raise HistoricalUniverseIngestError(
                            f"{row.ticker}@{row.valid_from} ayni kimlikte farkli icerik tasiyor"
                        )
                    continue
                cur.execute(INSERT_SQL, (
                    row.ticker, row.valid_from, row.valid_to, row.is_tradable,
                    row.company_name, row.sector_index_code, row.sector_code,
                    row.source, row.source_ref, row.source_sha256, row.row_sha256,
                ))
                returned = cur.fetchone()
                if returned:
                    if str(returned[0]).lower() != row.row_sha256:
                        raise HistoricalUniverseIngestError("veritabani farkli row_sha256 dondurdu")
                    inserted += 1
                    continue
                cur.execute(
                    """
                    SELECT row_sha256
                      FROM core.universe_membership_history
                     WHERE ticker=%s AND valid_from=%s
                    """,
                    (row.ticker, row.valid_from),
                )
                existing = cur.fetchone()
                if not existing or str(existing[0]).lower() != row.row_sha256:
                    raise HistoricalUniverseIngestError(
                        f"{row.ticker}@{row.valid_from} ayni kimlikte farkli icerik tasiyor"
                    )
    return inserted


def fetch_historical_universe_membership(conn: Any) -> pd.DataFrame:
    query = """
    SELECT ticker, valid_from, valid_to, is_tradable,
           company_name, sector_index_code, sector_code,
           source, source_ref, source_sha256, row_sha256
      FROM core.universe_membership_history
     ORDER BY ticker, valid_from
    """
    return pd.read_sql_query(query, conn)
