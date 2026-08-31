#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.request import Request, urlopen

URLS = {
    "meta": "https://bilancoveri.com/api/v1/meta.json",
    "sitemap": "https://bilancoveri.com/sitemap.xml",
    "current": "https://bilancoveri.com/api/v1/sirketler.json",
    "search": "https://bilancoveri.com/api/v1/arama.json",
}
OUT = Path("artifacts/bilancoveri_full_detail_index")
TICKER_RE = re.compile(r"/(?:sirketler|companies)/([a-z0-9.]{2,10})(?:/|<)", re.I)


def fetch(url: str) -> bytes:
    req = Request(url, headers={"User-Agent": "TOTAL-RASYO-HESAPLAYICI/5Y-detail-index-probe"})
    with urlopen(req, timeout=30) as response:
        raw = response.read()
        status = getattr(response, "status", 200)
    if status != 200 or not raw:
        raise RuntimeError(f"fetch failed HTTP={status} bytes={len(raw)} url={url}")
    return raw


def extract_json_tickers(payload: object) -> set[str]:
    out: set[str] = set()
    if isinstance(payload, dict):
        for k, v in payload.items():
            if k in {"ticker", "kod", "code", "symbol"} and isinstance(v, str):
                out.add(v.strip().upper())
            else:
                out |= extract_json_tickers(v)
    elif isinstance(payload, list):
        for item in payload:
            if isinstance(item, (list, tuple)) and item and isinstance(item[0], str):
                out.add(item[0].strip().upper())
            else:
                out |= extract_json_tickers(item)
    return {t for t in out if t and len(t) <= 10}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    raw: dict[str, bytes] = {}
    for name, url in URLS.items():
        raw[name] = fetch(url)
        suffix = "xml" if name == "sitemap" else "json"
        (OUT / f"{name}.raw.{suffix}").write_bytes(raw[name])

    current = extract_json_tickers(json.loads(raw["current"].decode("utf-8-sig")))
    search = extract_json_tickers(json.loads(raw["search"].decode("utf-8-sig")))
    sitemap_text = raw["sitemap"].decode("utf-8", errors="strict")
    sitemap = {m.group(1).upper() for m in TICKER_RE.finditer(sitemap_text)}
    meta = json.loads(raw["meta"].decode("utf-8-sig"))

    report = {
        "contract": "BILANCOVERI_FULL_DETAIL_INDEX_PROBE_V1",
        "current_count": len(current),
        "search_count": len(search),
        "sitemap_company_ticker_count": len(sitemap),
        "sitemap_only_vs_current_search": sorted(sitemap - (current | search)),
        "current_search_not_in_sitemap": sorted((current | search) - sitemap),
        "union_count": len(current | search | sitemap),
        "meta": meta,
    }
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        "FULL_DETAIL_INDEX_PROBE_OK "
        f"current={len(current)} search={len(search)} sitemap={len(sitemap)} union={report['union_count']}"
    )
    print("SITEMAP_ONLY=" + ",".join(report["sitemap_only_vs_current_search"]))
    print("META=" + json.dumps(meta, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
