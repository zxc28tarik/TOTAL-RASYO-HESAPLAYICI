#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.request import Request, urlopen

BASE = "https://bilancoveri.com/api/v1/hisse/{ticker}.json"
OUT = Path("artifacts/bilancoveri_probe")
CASES = (
    ("THYAO", "XI_29"),
    ("SISE", "XI_29"),
    ("AKBNK", "UFRS"),
)
REQUIRED_PERIODS = ("2021/9", "2023/6", "2024/6", "2025/6")
THYAO_EXPECTED = {
    "2024/6": {"1AA": 33_184_000_000, "1BL": 1_233_843_000_000},
    "2025/6": {"1AA": 98_791_000_000, "1BL": 1_652_589_000_000},
}


def fetch(ticker: str) -> tuple[bytes, str]:
    url = BASE.format(ticker=ticker.lower())
    req = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "TOTAL-RASYO-HESAPLAYICI/BilancoVeri-open-api-probe",
        },
    )
    with urlopen(req, timeout=30) as response:
        raw = response.read()
        status = getattr(response, "status", 200)
        final_url = response.geturl()
    if status != 200 or not raw:
        raise RuntimeError(f"BilancoVeri HTTP={status} ticker={ticker} bytes={len(raw)}")
    return raw, final_url


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, object]] = []
    for ticker, expected_group in CASES:
        raw, source_url = fetch(ticker)
        payload = json.loads(raw.decode("utf-8-sig"))
        company = payload.get("company")
        periods = payload.get("periods")
        labels = payload.get("labels")
        if not isinstance(company, dict) or not isinstance(periods, dict) or not isinstance(labels, dict):
            raise RuntimeError(f"BilancoVeri schema missing for {ticker}")
        if str(company.get("ticker", "")).upper() != ticker:
            raise RuntimeError(f"ticker mismatch for {ticker}")
        if str(company.get("fin_group", "")) != expected_group:
            raise RuntimeError(
                f"financial group mismatch for {ticker}: {company.get('fin_group')} != {expected_group}"
            )
        missing_periods = [period for period in REQUIRED_PERIODS if period not in periods]
        if missing_periods:
            raise RuntimeError(f"missing historical periods for {ticker}: {missing_periods}")
        if ticker == "THYAO":
            for period, expected in THYAO_EXPECTED.items():
                row = periods.get(period)
                if not isinstance(row, dict):
                    raise RuntimeError(f"THYAO period missing: {period}")
                for item_code, expected_value in expected.items():
                    actual = row.get(item_code)
                    if actual != expected_value:
                        raise RuntimeError(
                            f"THYAO original-event mismatch {period}/{item_code}: "
                            f"{actual!r} != {expected_value!r}"
                        )
        path = OUT / f"{ticker}.raw.json"
        path.write_bytes(raw)
        records.append(
            {
                "ticker": ticker,
                "financial_group": expected_group,
                "source_url": source_url,
                "raw_sha256": hashlib.sha256(raw).hexdigest(),
                "raw_size_bytes": len(raw),
                "generated_at": payload.get("generated_at"),
                "period_count": len(periods),
                "required_periods_present": list(REQUIRED_PERIODS),
            }
        )

    manifest = {
        "contract": "BILANCOVERI_OPEN_API_SOURCE_PROBE_V1",
        "scope": "probe_only_not_yet_pit_authority",
        "api_policy": "keyless self-service JSON; reasonable-use; attribution requested",
        "original_event_crosscheck": {
            "ticker": "THYAO",
            "periods": THYAO_EXPECTED,
            "result": "PASS",
        },
        "records": records,
    }
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"BILANCOVERI_PROBE_OK files={len(records)}")
    for row in records:
        print(
            f"{row['ticker']} group={row['financial_group']} periods={row['period_count']} "
            f"sha256={row['raw_sha256']} generated_at={row['generated_at']}"
        )
    print("THYAO_ORIGINAL_EVENT_CROSSCHECK=PASS")


if __name__ == "__main__":
    main()
