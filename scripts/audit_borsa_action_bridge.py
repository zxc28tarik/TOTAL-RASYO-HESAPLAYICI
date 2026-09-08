"""Audit short KAP-share-state to raw-price bridges with official Borsa THB files."""
from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import csv
import gzip
import hashlib
import io
import json
from pathlib import Path
import zipfile

import pandas as pd
import requests


ROOT = Path(__file__).resolve().parents[1]
PRIORITY = ROOT / "data/audit/m2_priority_matrix_v1/m2_priority_cells.jsonl.gz"
KAP_HISTORY = ROOT / "data/backtest_sources/kap_share_class_history_v1/share_class_observations.jsonl.gz"
INDEX_CLOSES = ROOT / "data/backtest_sources/m3_source_package/index_closes.csv.gz"
SCHEMA_URL = "https://www.borsaistanbul.com/files/pay-piyasasi-veri-bildirim-ve-kabul-formatlari.pdf"
THB_URL = "https://borsaistanbul.com/data/thb/{year:04d}/{month:02d}/thb{stamp}1.zip"


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in gzip.decompress(path.read_bytes()).splitlines()]


def _encoded(value: object, *, indent: int | None = None) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=None if indent else (",", ":"), indent=indent,
                       allow_nan=False) + "\n").encode("utf-8")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _kap_day(value: str) -> date:
    return datetime.strptime(value, "%d/%m/%Y %H:%M:%S").date()


def _kap_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%d/%m/%Y %H:%M:%S").replace(
        tzinfo=timezone(timedelta(hours=3)))


def build_bridge_candidates(max_bridge_days: int = 120) -> tuple[list[dict], dict[date, set[str]]]:
    usable = [row for row in _jsonl(KAP_HISTORY) if row["usable"]]
    by_ticker: dict[str, list[dict]] = defaultdict(list)
    for row in usable:
        by_ticker[row["ticker"]].append(row)
    index = pd.read_csv(INDEX_CLOSES)
    trading_days = [date.fromisoformat(value) for value in
                    index.loc[index["index_code"].eq("XU100"), "trade_date"]]
    candidates: list[dict] = []
    required: dict[date, set[str]] = defaultdict(set)
    for cell in _jsonl(PRIORITY):
        if cell["historical_family"] != "NONFIN":
            continue
        price_day = date.fromisoformat(cell["price_trade_date"])
        cutoff = datetime.fromisoformat(cell["knowledge_cutoff_at"])
        histories = [row for row in by_ticker[cell["ticker"]]
                     if _kap_timestamp(row["published_at_local"]) <= cutoff]
        if not histories:
            continue
        history = max(histories, key=lambda row: _kap_timestamp(row["published_at_local"]))
        issued = cell.get("issued_capital_observation") or {}
        issued_capital = issued.get("capital")
        capital_reconciliation = "NOT_AVAILABLE"
        if issued_capital is not None:
            capital_reconciliation = (
                "MATCH" if Decimal(history["total_nominal_value_try"]) == Decimal(issued_capital)
                else "DIFFERENT_OBSERVATION_DATE_OR_STATE"
            )
        anchor_day = _kap_day(history["published_at_local"])
        bridge_days = [day for day in trading_days if anchor_day < day <= price_day]
        if len(bridge_days) > max_bridge_days:
            continue
        candidate = {
            "ticker": cell["ticker"], "month": cell["month"],
            "price_trade_date": cell["price_trade_date"],
            "knowledge_cutoff_at": cell["knowledge_cutoff_at"],
            "share_state_published_at_local": history["published_at_local"],
            "shares_out": history["derived_shares"],
            "total_nominal_value_try": history["total_nominal_value_try"],
            "issued_capital": issued_capital,
            "issued_capital_reconciliation": capital_reconciliation,
            "bridge_trading_days": [day.isoformat() for day in bridge_days],
        }
        candidates.append(candidate)
        for day in bridge_days:
            required[day].add(cell["ticker"])
    candidates.sort(key=lambda row: (row["month"], row["ticker"]))
    return candidates, required


def parse_thb_archive(raw: bytes, required_tickers: set[str]) -> tuple[list[dict], dict]:
    archive_hash = _sha(raw)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names = [name for name in archive.namelist()
                 if name.lower().endswith("1.csv") and "eng" not in name.lower()]
        if len(names) != 1:
            raise ValueError(f"THB_TURKISH_CSV_COUNT:{len(names)}")
        info = archive.getinfo(names[0])
        member = archive.read(names[0])
    text = member.decode("cp1254")
    rows = list(csv.reader(io.StringIO(text), delimiter=";"))
    if not rows:
        raise ValueError("THB_EMPTY_CSV")
    normalized = [value.strip().replace("İ", "I") for value in rows[0]]
    try:
        ticker_i = normalized.index("ISLEM  KODU")
        action_i = normalized.index("OZSERMAYE HALI")
        date_i = normalized.index("TARIH")
    except ValueError as exc:
        raise ValueError("THB_REQUIRED_SCHEMA_MISSING") from exc
    found: list[dict] = []
    for row in rows[1:]:
        if len(row) <= max(ticker_i, action_i, date_i):
            continue
        ticker = row[ticker_i].strip().upper().removesuffix(".E")
        if ticker in required_tickers:
            found.append({
                "ticker": ticker, "trade_date": row[date_i].strip(),
                "action_flag": row[action_i].strip(),
                "source_row": ";".join(row), "source_row_sha256": _sha(";".join(row).encode("cp1254")),
            })
    # ZIP stores a wall-clock timestamp without a timezone.  Borsa Istanbul's
    # archive is a Turkish market artifact, so retain the explicit provenance
    # assumption and interpret that wall clock as Europe/Istanbul (+03:00 for
    # every date in this sample).  This is availability evidence, not an event
    # effective timestamp.
    member_timestamp = datetime(*info.date_time, tzinfo=timezone(timedelta(hours=3)))
    meta = {"archive_sha256": archive_hash, "member_name": names[0],
            "member_sha256": _sha(member), "header": rows[0]}
    meta["member_timestamp_local"] = member_timestamp.isoformat()
    meta["member_timestamp_basis"] = "ZIP_MEMBER_WALL_CLOCK_ASSUMED_EUROPE_ISTANBUL"
    return found, meta


def audit(output_dir: Path, *, max_bridge_days: int = 120, workers: int = 8) -> dict:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    candidates, required = build_bridge_candidates(max_bridge_days)
    client = requests.Session()
    schema_path = output_dir / "official_thb_schema_v1_14.pdf"
    if schema_path.exists():
        schema_bytes = schema_path.read_bytes()
    else:
        schema = client.get(SCHEMA_URL, timeout=60)
        schema.raise_for_status()
        schema_bytes = schema.content
        schema_path.write_bytes(schema_bytes)

    daily_path = output_dir / "daily_thb_evidence.jsonl.gz"
    prior_daily = _jsonl(daily_path) if daily_path.exists() else []
    prior_by_date = {row["date"]: row for row in prior_daily}

    def fetch(item: tuple[date, set[str]]) -> dict:
        day, tickers = item
        url = THB_URL.format(year=day.year, month=day.month, stamp=day.strftime("%Y%m%d"))
        try:
            response = requests.get(url, timeout=60)
            response.raise_for_status()
            rows, meta = parse_thb_archive(response.content, tickers)
            return {"date": day.isoformat(), "url": url, "status": "CAPTURED",
                    "required_tickers": sorted(tickers), "rows": rows, **meta}
        except (requests.RequestException, ValueError, zipfile.BadZipFile) as exc:
            return {"date": day.isoformat(), "url": url, "status": f"ERROR:{type(exc).__name__}",
                    "required_tickers": sorted(tickers), "rows": []}

    pending = [item for item in sorted(required.items())
               if prior_by_date.get(item[0].isoformat(), {}).get("status") != "CAPTURED"
               or not prior_by_date[item[0].isoformat()].get("member_timestamp_local")
               or not item[1].issubset(set(prior_by_date[item[0].isoformat()]["required_tickers"]))]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        fresh = {row["date"]: row for row in pool.map(fetch, pending)}
    daily = [fresh.get(day.isoformat(), prior_by_date.get(day.isoformat()))
             for day in sorted(required)]
    if any(row is None for row in daily):
        raise ValueError("THB_RESUME_LOST_REQUIRED_DATE")
    daily.sort(key=lambda row: row["date"])
    by_pair = {(row["trade_date"], row["ticker"]): row
               for item in daily for row in item["rows"]}
    daily_status = {item["date"]: item["status"] for item in daily}
    daily_timestamp = {item["date"]: item.get("member_timestamp_local") for item in daily}
    results = []
    for candidate in candidates:
        reasons = []
        flags = []
        cutoff = datetime.fromisoformat(candidate["knowledge_cutoff_at"])
        for day in candidate["bridge_trading_days"]:
            if daily_status.get(day) != "CAPTURED":
                reasons.append(f"THB_ARCHIVE_MISSING:{day}")
                continue
            timestamp = daily_timestamp.get(day)
            if timestamp is None:
                reasons.append(f"THB_PUBLICATION_TIMESTAMP_MISSING:{day}")
            elif datetime.fromisoformat(timestamp) > cutoff:
                reasons.append(f"THB_AVAILABLE_AFTER_KNOWLEDGE_CUTOFF:{day}")
            evidence = by_pair.get((day, candidate["ticker"]))
            if evidence is None:
                reasons.append(f"THB_TICKER_ROW_MISSING:{day}")
            elif evidence["action_flag"] not in ("", "00"):
                flags.append({"date": day, "flag": evidence["action_flag"]})
        if flags:
            reasons.append("NONEMPTY_CORPORATE_ACTION_FLAG")
        results.append({**candidate, "status": "BRIDGE_COMPLETE_BEFORE_CUTOFF" if not reasons else "EXPLICIT_REJECTION",
                        "reasons": sorted(set(reasons)), "observed_action_flags": flags})

    daily_path.write_bytes(gzip.compress(b"".join(_encoded(row) for row in daily), mtime=0))
    result_path = output_dir / "cell_bridges.jsonl.gz"
    result_path.write_bytes(gzip.compress(b"".join(_encoded(row) for row in results), mtime=0))
    receipt = {
        "contract": "OFFICIAL_BORSA_THB_ACTION_BRIDGE_V1",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "schema_url": SCHEMA_URL, "schema_sha256": _sha(schema_bytes),
        "max_bridge_trading_days": max_bridge_days,
        "candidate_cells": len(candidates), "candidate_tickers": len({x["ticker"] for x in candidates}),
        "required_daily_archives": len(required),
        "captured_daily_archives": sum(row["status"] == "CAPTURED" for row in daily),
        "complete_before_cutoff_cells": sum(row["status"] == "BRIDGE_COMPLETE_BEFORE_CUTOFF" for row in results),
        "zero_length_complete_cells": sum(
            row["status"] == "BRIDGE_COMPLETE_BEFORE_CUTOFF" and not row["bridge_trading_days"]
            for row in results),
        "continuity_corroborated_no_action_cells": sum(
            not row["observed_action_flags"] and
            not any(reason.startswith(("THB_ARCHIVE_MISSING", "THB_TICKER_ROW_MISSING"))
                    for reason in row["reasons"])
            for row in results),
        "explicit_rejections": sum(row["status"] == "EXPLICIT_REJECTION" for row in results),
        "historical_code_semantics_assumed": False,
        "availability_policy": "ZIP_MEMBER_TIMESTAMP_MUST_BE_AT_OR_BEFORE_CELL_KNOWLEDGE_CUTOFF",
        "zip_member_timestamp_basis": "ZIP_MEMBER_WALL_CLOCK_ASSUMED_EUROPE_ISTANBUL",
        "conservative_flag_policy": "ONLY_BLANK_OR_00_ACCEPTED;ALL_OTHER_CODES_REJECTED",
        "authoritative_m2_claim_allowed": False,
        "outputs": {daily_path.name: _sha(daily_path.read_bytes()), result_path.name: _sha(result_path.read_bytes()),
                    schema_path.name: _sha(schema_path.read_bytes())},
    }
    (output_dir / "receipt.json").write_bytes(_encoded(receipt, indent=2))
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-bridge-days", type=int, default=120)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    print(json.dumps(audit(args.output_dir, max_bridge_days=args.max_bridge_days,
                           workers=args.workers), ensure_ascii=False, indent=2))
