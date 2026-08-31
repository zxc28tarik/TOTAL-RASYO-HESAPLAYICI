#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.request import Request, urlopen

URL = "https://www.kap.org.tr/tr/api/disclosure/members/byCriteria"
OUT = Path("artifacts/kap_metadata_probe")

BODY = {
    "fromDate": "2025-08-01",
    "toDate": "2025-08-31",
    "memberType": "IGS",
    "mkkMemberOidList": [],
    "inactiveMkkMemberOidList": [],
    "disclosureClass": "FR",
    "subjectList": [],
    "isLate": "",
    "mainSector": "",
    "sector": "",
    "subSector": "",
    "marketOid": "",
    "index": "",
    "bdkReview": "",
    "bdkMemberOidList": [],
    "year": "",
    "term": "",
    "ruleType": "",
    "period": "",
    "fromSrc": False,
    "srcCategory": "",
    "disclosureIndexList": [],
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(BODY, separators=(",", ":")).encode("utf-8")
    req = Request(
        URL,
        data=encoded,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json,text/plain,*/*",
            "User-Agent": "TOTAL-RASYO-HESAPLAYICI/low-volume-pit-metadata-probe",
        },
    )
    with urlopen(req, timeout=45) as response:
        raw = response.read()
        status = getattr(response, "status", 200)
    if status != 200 or not raw:
        raise RuntimeError(f"KAP metadata probe failed HTTP={status} bytes={len(raw)}")
    payload = json.loads(raw.decode("utf-8-sig"))
    items = payload if isinstance(payload, list) else payload.get("data", [])
    if not isinstance(items, list) or not items:
        raise RuntimeError("KAP FR metadata probe returned no items")
    thyao = [
        row for row in items
        if isinstance(row, dict)
        and "THYAO" in str(row.get("stockCodes") or row.get("relatedStocks") or "")
    ]
    if not thyao:
        raise RuntimeError("Expected THYAO FR not present in August 2025 metadata")
    required = {"publishDate", "disclosureIndex", "disclosureClass", "year", "ruleType"}
    for row in thyao:
        missing = sorted(k for k in required if row.get(k) in (None, ""))
        if missing:
            raise RuntimeError(f"THYAO metadata missing fields: {missing}")
    raw_path = OUT / "kap_fr_2025-08.raw.json"
    raw_path.write_bytes(raw)
    manifest = {
        "contract": "KAP_LOW_VOLUME_FR_METADATA_PROBE_V1",
        "scope": "pit_metadata_probe_not_bulk_financial_value_source",
        "request": BODY,
        "source_url": URL,
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "raw_size_bytes": len(raw),
        "item_count": len(items),
        "thyao_items": [
            {
                key: row.get(key)
                for key in (
                    "publishDate", "disclosureIndex", "disclosureClass", "disclosureType",
                    "stockCodes", "relatedStocks", "year", "ruleType", "period", "modifyStatus",
                )
            }
            for row in thyao
        ],
    }
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"KAP_METADATA_OK items={len(items)} thyao={len(thyao)} sha256={manifest['raw_sha256']}")
    for row in manifest["thyao_items"]:
        print(json.dumps(row, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
