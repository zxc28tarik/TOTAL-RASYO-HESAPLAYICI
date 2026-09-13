"""Capture official KAP share-class history for the 3,017-cell M2 priority set.

The capture deliberately stores nominal value per class and derives a share count
only when every class has an explicit, positive per-share nominal value.  It never
uses the repository's legacy ``share_nominal_value=1`` configuration default.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import gzip
import hashlib
import json
from pathlib import Path
import re
import time

import requests


ROOT = Path(__file__).resolve().parents[1]
PRIORITY = ROOT / "data/audit/m2_priority_matrix_v1/m2_priority_cells.jsonl.gz"
MEMBERS_URL = "https://kap.org.tr/tr/bist-sirketler"
HISTORY_URL = (
    "https://kap.org.tr/tr/api/company-detail/get-history/"
    "{mkk_member_oid}/kpy41_acc5_sermayeyi_temsil_eden/N"
)
CONTRACT = "OFFICIAL_KAP_SHARE_CLASS_HISTORY_V1"


def _encoded(value: object, *, indent: int | None = None) -> bytes:
    return (
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=None if indent else (",", ":"),
            indent=indent, allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _priority_tickers(path: Path) -> set[str]:
    return {
        json.loads(line)["ticker"].strip().upper()
        for line in gzip.decompress(path.read_bytes()).splitlines()
    }


_MEMBER_PATTERN = re.compile(
    r'\\?"mkkMemberOid\\?":\\?"(?P<oid>[0-9A-Fa-f]+)\\?"'
    r'.{0,1200}?\\?"stockCode\\?":\\?"(?P<ticker>[A-Z0-9., ]+)\\?"'
)


def parse_bist_members(page: bytes) -> dict[str, str]:
    """Return stock-code to MKK member OID from KAP's official BIST roster."""
    text = page.decode("utf-8")
    result: dict[str, str] = {}
    for match in _MEMBER_PATTERN.finditer(text):
        oid = match.group("oid")
        for raw_ticker in match.group("ticker").split(","):
            ticker = raw_ticker.strip().removesuffix(".E").upper()
            if not ticker:
                continue
            previous = result.setdefault(ticker, oid)
            if previous != oid:
                raise ValueError(f"AMBIGUOUS_MKK_MEMBER_OID:{ticker}")
    if not result:
        raise ValueError("KAP_BIST_MEMBER_ROSTER_PARSE_EMPTY")
    return result


def _decimal(value: object) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError("MISSING_DECIMAL")
    try:
        if isinstance(value, (int, float, Decimal)):
            text = str(value)
        else:
            text = str(value).strip().replace(" ", "")
            if "," in text and "." in text:
                if text.rfind(",") > text.rfind("."):
                    text = text.replace(".", "").replace(",", ".")
                else:
                    text = text.replace(",", "")
            elif "," in text:
                text = text.replace(",", ".")
            elif text.count(".") > 1:
                groups = text.split(".")
                if not groups[0].lstrip("-").isdigit() or not all(
                    group.isdigit() and len(group) == 3 for group in groups[1:]
                ):
                    raise ValueError("AMBIGUOUS_DECIMAL_SEPARATORS")
                text = "".join(groups)
            # A lone dot in KAP JSON is a decimal separator.  Treating it as
            # a thousands separator inflated share counts by powers of ten.
        number = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError("INVALID_DECIMAL") from exc
    if not number.is_finite():
        raise ValueError("NONFINITE_DECIMAL")
    return number


def validate_history_row(ticker: str, row: object) -> dict[str, object]:
    """Validate one dated KAP class table and derive shares without assumptions."""
    base = {
        "ticker": ticker,
        "item_key": None,
        "published_at_local": None,
        "classes": [],
        "derived_shares": None,
        "total_nominal_value_try": None,
        "usable": False,
        "status": "INVALID_HISTORY_ROW",
    }
    if not isinstance(row, dict):
        return base
    base["item_key"] = row.get("itemKey")
    base["published_at_local"] = row.get("creationDate")
    values = row.get("value")
    if row.get("itemKey") != "kpy41_acc5_sermayeyi_temsil_eden":
        base["status"] = "UNEXPECTED_ITEM_KEY"
        return base
    try:
        datetime.strptime(str(row.get("creationDate")), "%d/%m/%Y %H:%M:%S")
    except ValueError:
        base["status"] = "INVALID_PUBLICATION_TIMESTAMP"
        return base
    if not isinstance(values, list) or not values:
        base["status"] = "EMPTY_SHARE_CLASS_TABLE"
        return base

    classes: list[dict[str, object]] = []
    total_nominal = Decimal(0)
    total_shares = Decimal(0)
    seen_groups: set[str] = set()
    try:
        for item in values:
            if not isinstance(item, dict):
                raise ValueError("INVALID_CLASS")
            group = str(item.get("shareGroup") or "").strip()
            if not group or group in seen_groups:
                raise ValueError("MISSING_OR_DUPLICATE_SHARE_GROUP")
            seen_groups.add(group)
            per_unit = item.get("monetaryUnitOfPerShare")
            total_unit = item.get("monetaryUnitOfShares")
            if not isinstance(per_unit, dict) or per_unit.get("key") != "TRY":
                raise ValueError("PER_SHARE_UNIT_NOT_TRY")
            if not isinstance(total_unit, dict) or total_unit.get("key") != "TRY":
                raise ValueError("TOTAL_NOMINAL_UNIT_NOT_TRY")
            nominal_per_share = _decimal(item.get("nominalValuePerShare"))
            nominal_total = _decimal(item.get("nominalValueOfShares"))
            if nominal_per_share <= 0 or nominal_total < 0:
                raise ValueError("NONPOSITIVE_NOMINAL_VALUE")
            shares = nominal_total / nominal_per_share
            total_nominal += nominal_total
            total_shares += shares
            classes.append({
                "share_group": group,
                "nominal_value_per_share_try": str(nominal_per_share),
                "nominal_value_of_class_try": str(nominal_total),
                "derived_class_shares": str(shares),
                "exchange_traded": item.get("exchangeTradedOrNot"),
                "registered_or_bearer": item.get("registeredOrBearerShare"),
            })
    except ValueError as exc:
        base["classes"] = classes
        base["status"] = str(exc)
        return base

    base.update({
        "classes": classes,
        "derived_shares": str(total_shares),
        "total_nominal_value_try": str(total_nominal),
        "usable": True,
        "status": "EXPLICIT_CLASS_NOMINALS_RECONCILED",
    })
    return base


def capture(output_dir: Path, *, workers: int = 4, session: requests.Session | None = None) -> dict:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    client = session or requests.Session()
    headers = {"Accept-Language": "tr", "User-Agent": "TOTAL-RASYO evidence capture/1"}
    roster_path = output_dir / "bist_member_roster.html.gz"
    if roster_path.exists():
        member_bytes = gzip.decompress(roster_path.read_bytes())
    else:
        member_response = client.get(MEMBERS_URL, headers=headers, timeout=60)
        member_response.raise_for_status()
        member_bytes = member_response.content
    members = parse_bist_members(member_bytes)
    tickers = sorted(_priority_tickers(PRIORITY))
    prior_raw_path = output_dir / "raw_responses.jsonl.gz"
    prior_rows = []
    if prior_raw_path.exists():
        prior_rows = [
            json.loads(line)
            for line in gzip.decompress(prior_raw_path.read_bytes()).splitlines()
        ]
    raw_by_ticker = {str(row["ticker"]): row for row in prior_rows}

    def fetch(ticker: str) -> tuple[str, str | None, bytes | None, str | None, str | None]:
        oid = members.get(ticker)
        if oid is None:
            return ticker, None, None, None, "MKK_MEMBER_OID_MISSING"
        url = HISTORY_URL.format(mkk_member_oid=oid)
        last_error = "HTTP_ERROR"
        for attempt in range(5):
            try:
                response = client.get(url, headers=headers, timeout=60)
                response.raise_for_status()
                return ticker, oid, response.content, response.headers.get("Date"), None
            except requests.RequestException as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                last_error = f"HTTP_ERROR:{type(exc).__name__}:{status}"
                if attempt < 4:
                    time.sleep(1.0 * (attempt + 1))
        return ticker, oid, None, None, last_error

    pending_tickers = [ticker for ticker in tickers if ticker not in raw_by_ticker]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        captures = list(pool.map(fetch, pending_tickers))

    errors: dict[str, tuple[str | None, str | None]] = {}
    for ticker, oid, raw, http_date, error in captures:
        if raw is None:
            errors[ticker] = (oid, error)
            continue
        try:
            payload = json.loads(raw)
            if not isinstance(payload, list):
                raise ValueError("NOT_ARRAY")
        except (json.JSONDecodeError, ValueError) as exc:
            errors[ticker] = (oid, f"INVALID_JSON:{exc}")
            continue
        raw_by_ticker[ticker] = {
            "ticker": ticker, "mkk_member_oid": oid,
            "url": HISTORY_URL.format(mkk_member_oid=oid),
            "http_date": http_date, "response_sha256": _sha256_bytes(raw),
            "response": payload,
        }

    raw_rows: list[dict[str, object]] = []
    observations: list[dict[str, object]] = []
    ticker_status: list[dict[str, object]] = []
    for ticker in tickers:
        stored = raw_by_ticker.get(ticker)
        if stored is None:
            oid, error = errors.get(ticker, (members.get(ticker), "MKK_MEMBER_OID_MISSING"))
            ticker_status.append({"ticker": ticker, "mkk_member_oid": oid, "status": error})
            continue
        oid = stored["mkk_member_oid"]
        payload = stored["response"]
        raw_rows.append(stored)
        validated = [validate_history_row(ticker, row) for row in payload]
        observations.extend(validated)
        ticker_status.append({
            "ticker": ticker, "mkk_member_oid": oid,
            "status": "CAPTURED" if payload else "NO_HISTORY_ROWS",
            "history_rows": len(payload),
            "usable_history_rows": sum(bool(row["usable"]) for row in validated),
        })

    raw_path = output_dir / "raw_responses.jsonl.gz"
    raw_path.write_bytes(gzip.compress(b"".join(_encoded(row) for row in raw_rows), mtime=0))
    observations.sort(key=lambda row: (str(row["ticker"]), str(row["published_at_local"])))
    observation_path = output_dir / "share_class_observations.jsonl.gz"
    observation_path.write_bytes(
        gzip.compress(b"".join(_encoded(row) for row in observations), mtime=0)
    )
    roster_path.write_bytes(gzip.compress(member_bytes, mtime=0))
    status_path = output_dir / "ticker_status.json"
    status_path.write_bytes(_encoded(ticker_status, indent=2))
    receipt = {
        "contract": CONTRACT,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "members_url": MEMBERS_URL,
        "history_url_template": HISTORY_URL,
        "priority_matrix_sha256": _sha256_bytes(PRIORITY.read_bytes()),
        "target_tickers": len(tickers),
        "roster_matched_tickers": sum(ticker in members for ticker in tickers),
        "captured_tickers": len(raw_rows),
        "tickers_with_history": sum(row.get("history_rows", 0) > 0 for row in ticker_status),
        "history_rows": len(observations),
        "usable_explicit_nominal_rows": sum(bool(row["usable"]) for row in observations),
        "economic_rule": "SUM(class_nominal_value / explicit_class_nominal_value_per_share)",
        "forbidden_rule": "ISSUED_CAPITAL / ASSUMED_1_TRY_NOMINAL",
        "action_completeness_claimed": False,
        "authoritative_m2_claim_allowed": False,
        "outputs": {
            path.name: _sha256_bytes(path.read_bytes())
            for path in (raw_path, observation_path, roster_path, status_path)
        },
    }
    (output_dir / "receipt.json").write_bytes(_encoded(receipt, indent=2))
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    print(json.dumps(capture(args.output_dir, workers=args.workers), ensure_ascii=False, indent=2))
