from __future__ import annotations

"""Fail-closed exact signal-price supplement for the real historical backtest.

The frozen Yahoo discovery is the primary execution-price source.  Borsa
Istanbul's official Equity Market Daily Bulletin (THB) is used only for the
small, explicitly audited set of signal-day holes where Yahoo has no price.

This module deliberately does *not* implement source precedence or a generic
fallback chain.  A Borsa row may fill an exact missing (ticker, signal_date)
key, and it may never overwrite a Yahoo/lineage price that already exists.
"""

from dataclasses import dataclass
import csv
import json
import math
from pathlib import Path
from typing import Iterable

import pandas as pd


PROOF_CONTRACT = "V24_BORSA_THB_SCHEMA_PROOF_V1"
PRICE_RESOLUTION = "BORSA_ISTANBUL_THB_EXACT"
SOURCE = "BORSA_ISTANBUL_THB"

EXPECTED_EXACT_KEYS = frozenset(
    {
        ("INVES", "2022-07-01"),
        ("INVES", "2022-08-01"),
        ("INVES", "2022-09-01"),
        ("KLRHO", "2023-01-02"),
        ("KLRHO", "2023-02-01"),
        ("KLRHO", "2023-03-01"),
        ("KLRHO", "2023-04-03"),
        ("KLRHO", "2023-05-02"),
        ("KLRHO", "2023-06-01"),
        ("ASGYO", "2024-01-02"),
        ("ASGYO", "2024-02-01"),
        ("ASGYO", "2024-03-01"),
    }
)

THB_BILINGUAL_COLUMNS = {
    "TARIH": "TRADE DATE",
    "ISLEM  KODU": "INSTRUMENT SERIES CODE",
    "ACILIS FIYATI": "OPENING PRICE",
    "KAPANIS FIYATI": "CLOSING PRICE",
}

SUPPLEMENT_COLUMNS = (
    "ticker",
    "trade_date",
    "open",
    "close",
    "price_source_ticker",
    "price_resolution",
    "execution_source",
    "source_url",
    "archive_sha256",
    "member",
    "member_sha256",
)

COVERAGE_REQUIRED = (
    "month",
    "signal_date",
    "index_code",
    "ticker",
    "trade_date",
    "open",
    "close",
    "price_source_ticker",
    "price_resolution",
    "has_execution_price",
)


class HistoricalPriceSupplementError(ValueError):
    pass


@dataclass(frozen=True)
class PriceSupplementAudit:
    missing_before: int
    supplemented: int
    missing_after: int
    total_rows: int


def _hex64(value: object, field: str) -> str:
    text = str(value or "").strip().lower()
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise HistoricalPriceSupplementError(f"{field} 64-hex olmali")
    return text


def _ticker(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HistoricalPriceSupplementError("ticker dolu metin olmali")
    return value.strip().upper()


def _positive_number(value: object, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise HistoricalPriceSupplementError(f"{field} sayisal olmali") from exc
    if not math.isfinite(number) or number <= 0:
        raise HistoricalPriceSupplementError(f"{field} pozitif sonlu olmali")
    return number


def _require_columns(frame: pd.DataFrame, required: Iterable[str], name: str) -> None:
    missing = set(required) - set(frame.columns)
    if missing:
        raise HistoricalPriceSupplementError(
            f"{name} missing columns: {sorted(missing)}"
        )


def load_borsa_thb_exact_signal_prices(path: str | Path) -> pd.DataFrame:
    """Parse the 12 official THB rows using the bilingual schema in the proof.

    The proof stores the first Turkish and English header rows plus the exact
    target .E series row and SHA-256 lineage for each official daily archive.
    OPEN/CLOSE are located by column *name*, never by an undocumented numeric
    position.
    """

    source_path = Path(path)
    try:
        payload = json.loads(source_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HistoricalPriceSupplementError("THB schema proof okunamadi") from exc

    if payload.get("contract") != PROOF_CONTRACT:
        raise HistoricalPriceSupplementError("THB schema proof contract uyusmuyor")

    proof_rows = payload.get("rows")
    if not isinstance(proof_rows, list) or len(proof_rows) != len(EXPECTED_EXACT_KEYS):
        raise HistoricalPriceSupplementError("THB schema proof tam 12 satir olmali")

    result: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()

    for item in proof_rows:
        if not isinstance(item, dict):
            raise HistoricalPriceSupplementError("THB proof row object olmali")

        ticker = _ticker(item.get("ticker"))
        trade_date = pd.Timestamp(item.get("trade_date")).normalize()
        date_text = trade_date.date().isoformat()
        key = (ticker, date_text)
        if key in seen:
            raise HistoricalPriceSupplementError(f"duplicate THB proof key: {key}")
        seen.add(key)

        first_lines = item.get("first_lines")
        if not isinstance(first_lines, list) or len(first_lines) < 2:
            raise HistoricalPriceSupplementError(f"{key} bilingual THB header eksik")

        tr_header = next(csv.reader([str(first_lines[0])], delimiter=";"))
        en_header = next(csv.reader([str(first_lines[1])], delimiter=";"))
        target = next(csv.reader([str(item.get("target_row", ""))], delimiter=";"))

        if len(tr_header) != 56 or len(en_header) != 56 or len(target) != 56:
            raise HistoricalPriceSupplementError(f"{key} THB field count 56 olmali")

        for tr_name, en_name in THB_BILINGUAL_COLUMNS.items():
            if tr_name not in tr_header:
                raise HistoricalPriceSupplementError(f"{key} THB column eksik: {tr_name}")
            idx = tr_header.index(tr_name)
            if en_header[idx] != en_name:
                raise HistoricalPriceSupplementError(
                    f"{key} THB bilingual semantic mismatch: {tr_name}"
                )

        values = dict(zip(tr_header, target))
        if str(values["TARIH"]).strip() != date_text:
            raise HistoricalPriceSupplementError(f"{key} THB date mismatch")
        if str(values["ISLEM  KODU"]).strip().upper() != f"{ticker}.E":
            raise HistoricalPriceSupplementError(f"{key} ordinary .E series mismatch")

        archive_url = str(item.get("archive_url") or "").strip()
        if not archive_url.startswith("https://borsaistanbul.com/data/thb/"):
            raise HistoricalPriceSupplementError(f"{key} official Borsa source URL invalid")
        member = str(item.get("member") or "").strip()
        if not member.lower().endswith(".csv"):
            raise HistoricalPriceSupplementError(f"{key} THB member CSV olmali")

        result.append(
            {
                "ticker": ticker,
                "trade_date": trade_date,
                "open": _positive_number(values["ACILIS FIYATI"], "open"),
                "close": _positive_number(values["KAPANIS FIYATI"], "close"),
                "price_source_ticker": ticker,
                "price_resolution": PRICE_RESOLUTION,
                "execution_source": SOURCE,
                "source_url": archive_url,
                "archive_sha256": _hex64(item.get("archive_sha256"), "archive_sha256"),
                "member": member,
                "member_sha256": _hex64(item.get("member_sha256"), "member_sha256"),
            }
        )

    if seen != EXPECTED_EXACT_KEYS:
        missing = sorted(EXPECTED_EXACT_KEYS - seen)
        extra = sorted(seen - EXPECTED_EXACT_KEYS)
        raise HistoricalPriceSupplementError(
            f"THB exact key set mismatch missing={missing} extra={extra}"
        )

    frame = pd.DataFrame(result, columns=SUPPLEMENT_COLUMNS)
    return frame.sort_values(["trade_date", "ticker"]).reset_index(drop=True)


def fill_exact_signal_price_gaps(
    coverage: pd.DataFrame,
    supplement: pd.DataFrame,
) -> tuple[pd.DataFrame, PriceSupplementAudit]:
    """Fill only the exact missing signal-day keys; existing prices are immutable.

    This function is intentionally not a generic fallback.  The supplement key
    set must equal the coverage's missing key set exactly.  Therefore an
    official Borsa row can neither overwrite an existing Yahoo price nor add a
    date/ticker that was not already an audited execution-price hole.
    """

    _require_columns(coverage, COVERAGE_REQUIRED, "coverage")
    _require_columns(supplement, SUPPLEMENT_COLUMNS, "supplement")

    out = coverage.copy(deep=True)
    out["ticker"] = out["ticker"].map(_ticker)
    out["signal_date"] = pd.to_datetime(out["signal_date"], errors="raise").dt.normalize()
    out["trade_date"] = pd.to_datetime(out["trade_date"], errors="coerce").dt.normalize()

    if out.duplicated(["signal_date", "ticker"]).any():
        raise HistoricalPriceSupplementError("coverage duplicate signal_date+ticker")

    supp = supplement.copy(deep=True)
    supp["ticker"] = supp["ticker"].map(_ticker)
    supp["trade_date"] = pd.to_datetime(supp["trade_date"], errors="raise").dt.normalize()
    if supp.duplicated(["trade_date", "ticker"]).any():
        raise HistoricalPriceSupplementError("supplement duplicate trade_date+ticker")

    # Existing coverage rows that claim availability must already contain a
    # valid price.  This keeps the supplement from masking a corrupt Yahoo row.
    available = out["has_execution_price"].astype(bool)
    for field in ("open", "close"):
        numeric = pd.to_numeric(out.loc[available, field], errors="coerce")
        if numeric.isna().any() or (~numeric.map(lambda x: math.isfinite(float(x)) and float(x) > 0)).any():
            raise HistoricalPriceSupplementError(
                f"coverage available row has invalid {field}"
            )

    missing_mask = ~available
    missing_keys = {
        (_ticker(row.ticker), pd.Timestamp(row.signal_date).date().isoformat())
        for row in out.loc[missing_mask, ["ticker", "signal_date"]].itertuples(index=False)
    }
    supp_keys = {
        (_ticker(row.ticker), pd.Timestamp(row.trade_date).date().isoformat())
        for row in supp[["ticker", "trade_date"]].itertuples(index=False)
    }

    if supp_keys != missing_keys:
        missing = sorted(missing_keys - supp_keys)
        overlap_or_extra = sorted(supp_keys - missing_keys)
        raise HistoricalPriceSupplementError(
            f"supplement key set must equal audited gaps missing={missing} extra_or_overlap={overlap_or_extra}"
        )

    # Audit/provenance columns exist only for supplemented rows.  Existing Yahoo
    # lineage and prices remain byte-for-byte value-equivalent in their original
    # columns.
    for col in ("execution_source", "source_url", "archive_sha256", "member", "member_sha256"):
        if col not in out.columns:
            out[col] = pd.NA

    supp_by_key = {
        (row.ticker, pd.Timestamp(row.trade_date).normalize()): row
        for row in supp.itertuples(index=False)
    }

    for idx in out.index[missing_mask]:
        ticker = out.at[idx, "ticker"]
        day = pd.Timestamp(out.at[idx, "signal_date"]).normalize()
        row = supp_by_key[(ticker, day)]
        out.at[idx, "trade_date"] = day
        out.at[idx, "open"] = float(row.open)
        out.at[idx, "close"] = float(row.close)
        out.at[idx, "price_source_ticker"] = ticker
        out.at[idx, "price_resolution"] = PRICE_RESOLUTION
        out.at[idx, "has_execution_price"] = True
        out.at[idx, "execution_source"] = row.execution_source
        out.at[idx, "source_url"] = row.source_url
        out.at[idx, "archive_sha256"] = row.archive_sha256
        out.at[idx, "member"] = row.member
        out.at[idx, "member_sha256"] = row.member_sha256

    remaining = int((~out["has_execution_price"].astype(bool)).sum())
    audit = PriceSupplementAudit(
        missing_before=int(missing_mask.sum()),
        supplemented=len(supp),
        missing_after=remaining,
        total_rows=len(out),
    )
    if remaining:
        raise HistoricalPriceSupplementError(
            f"exact supplement sonrasi {remaining} execution-price gap kaldi"
        )

    return out, audit
