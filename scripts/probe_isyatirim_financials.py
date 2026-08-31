#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = "https://www.isyatirim.com.tr/_layouts/15/Isyatirim.Website/Common/Data.aspx/MaliTablo"
OUT = Path("artifacts/isyatirim_probe")
YEARS = (2023, 2024, 2025)
PERIODS = (12, 9, 6, 3)
CASES = (
    ("AKBNK", "UFRS"),
    ("SISE", "XI_29"),
    ("THYAO", "XI_29"),
)


def fetch(ticker: str, group: str, year: int) -> tuple[bytes, str]:
    params = {
        "companyCode": ticker,
        "exchange": "TRY",
        "financialGroup": group,
    }
    for idx, period in enumerate(PERIODS, start=1):
        params[f"year{idx}"] = year
        params[f"period{idx}"] = period
    url = BASE + "?" + urlencode(params)
    req = Request(
        url,
        headers={
            "User-Agent": "TOTAL-RASYO-HESAPLAYICI/isyatirim-source-probe",
            "Accept": "application/json,text/plain,*/*",
        },
    )
    with urlopen(req, timeout=45) as response:
        raw = response.read()
        final_url = response.geturl()
        status = getattr(response, "status", 200)
    if status != 200:
        raise RuntimeError(f"HTTP {status}: {ticker}/{year}")
    if not raw:
        raise RuntimeError(f"empty response: {ticker}/{year}")
    return raw, final_url


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, object]] = []
    for ticker, group in CASES:
        for year in YEARS:
            raw, final_url = fetch(ticker, group, year)
            try:
                payload = json.loads(raw.decode("utf-8-sig"))
            except Exception as exc:
                raise RuntimeError(f"non-JSON response: {ticker}/{year}") from exc
            rows = payload.get("value") if isinstance(payload, dict) else None
            if not isinstance(rows, list) or not rows:
                raise RuntimeError(f"no financial rows: {ticker}/{year}/{group}")
            path = OUT / f"{ticker}_{year}_{group}.json"
            path.write_bytes(raw)
            manifest.append(
                {
                    "ticker": ticker,
                    "year": year,
                    "financial_group": group,
                    "periods": list(PERIODS),
                    "source_url": final_url,
                    "raw_sha256": hashlib.sha256(raw).hexdigest(),
                    "raw_size_bytes": len(raw),
                    "row_count": len(rows),
                    "first_item_code": rows[0].get("itemCode") if isinstance(rows[0], dict) else None,
                }
            )
    manifest_path = OUT / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "contract": "ISYATIRIM_LIVE_FINANCIAL_SOURCE_PROBE_V1",
                "scope": "probe_only_not_pit_authority",
                "years": list(YEARS),
                "records": manifest,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    print(f"PROBE_OK files={len(manifest)} manifest={manifest_path}")
    for row in manifest:
        print(
            f"{row['ticker']} {row['year']} {row['financial_group']} "
            f"rows={row['row_count']} sha256={row['raw_sha256']}"
        )


if __name__ == "__main__":
    main()
