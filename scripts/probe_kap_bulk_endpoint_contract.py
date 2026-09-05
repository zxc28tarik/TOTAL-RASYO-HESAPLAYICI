from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import quote

import requests


CONTRACT = "KAP_BULK_ENDPOINT_CONTRACT_PROBE_V1"
DEFAULT_CLIENT_CONTRACT = Path(
    "data/backtest_sources/kap_bulk_financial_source_capture/client_download_contract.json"
)
ARCHIVE_PREFIXES = {
    b"PK\x03\x04": "ZIP",
    b"Rar!": "RAR",
}


def load_client_contract(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("contract") != "PUBLIC_KAP_FINANCIAL_TABLE_DOWNLOAD_CLIENT_CONTRACT_V1":
        raise ValueError("unexpected KAP client download contract")
    templates = payload.get("bulk_templates")
    periods = payload.get("period_codes")
    if not isinstance(templates, dict) or not isinstance(periods, dict):
        raise ValueError("KAP client contract templates/period codes missing")
    return payload


def build_url(template: str, *, base_url: str, lang: str, year: int, period_code: int) -> str:
    if lang not in {"tr", "en"}:
        raise ValueError("lang must be tr or en")
    if not 2000 <= int(year) <= 2100:
        raise ValueError("year outside expected range")
    if int(period_code) not in {1, 2, 3, 4}:
        raise ValueError("period_code must be 1..4")
    return template.format(
        base_url=base_url.rstrip("/"),
        lang=quote(lang, safe=""),
        year=int(year),
        period_code=int(period_code),
    )


def _archive_kind(prefix: bytes) -> str | None:
    for signature, kind in ARCHIVE_PREFIXES.items():
        if prefix.startswith(signature):
            return kind
    return None


def probe_endpoint_contract(
    *,
    client_contract: dict[str, object],
    base_url: str,
    lang: str,
    year: int,
    period_code: int,
    timeout_seconds: int,
) -> dict[str, object]:
    templates = client_contract["bulk_templates"]
    if not isinstance(templates, dict):
        raise ValueError("bulk_templates invalid")
    check_template = templates.get("check_file_exist")
    download_template = templates.get("download")
    if not isinstance(check_template, str) or not isinstance(download_template, str):
        raise ValueError("bulk endpoint templates invalid")

    check_url = build_url(
        check_template,
        base_url=base_url,
        lang=lang,
        year=year,
        period_code=period_code,
    )
    download_url = build_url(
        download_template,
        base_url=base_url,
        lang=lang,
        year=year,
        period_code=period_code,
    )
    headers = {
        "Accept-Language": lang,
        "User-Agent": "Mozilla/5.0 KAP-bulk-contract-probe/1.0",
    }

    check = requests.get(check_url, headers=headers, timeout=timeout_seconds)
    check_body = check.content

    with requests.get(
        download_url,
        headers={**headers, "Range": "bytes=0-7"},
        timeout=timeout_seconds,
        stream=True,
    ) as download:
        prefix = next(download.iter_content(chunk_size=8), b"")[:8]
        disposition = download.headers.get("Content-Disposition")
        content_type = download.headers.get("Content-Type")
        content_length = download.headers.get("Content-Length")
        download_status = download.status_code
        final_url = download.url

    archive_kind = _archive_kind(prefix)
    return {
        "contract": CONTRACT,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "probe_period": {"year": year, "period_code": period_code},
        "check": {
            "url": check_url,
            "status": check.status_code,
            "content_type": check.headers.get("Content-Type"),
            "body_sha256": hashlib.sha256(check_body).hexdigest(),
            "body_preview": check_body[:256].decode("utf-8", errors="replace"),
        },
        "download": {
            "url": download_url,
            "final_url": final_url,
            "status": download_status,
            "content_type": content_type,
            "content_disposition": disposition,
            "content_length": content_length,
            "prefix_hex": prefix.hex(),
            "archive_kind": archive_kind,
        },
        "endpoint_verified": (
            check.status_code == 200
            and download_status in {200, 206}
            and archive_kind in {"ZIP", "RAR"}
        ),
        "download_scope": "HEADER_AND_FIRST_8_BYTES_ONLY",
        "raw_archive_persisted": False,
        "semantic_mapping_authorized": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--client-contract", type=Path, default=DEFAULT_CLIENT_CONTRACT)
    parser.add_argument("--base-url", default="https://kap.org.tr")
    parser.add_argument("--lang", default="tr")
    parser.add_argument("--year", type=int, default=2021)
    parser.add_argument("--period-code", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=int, default=60)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-verified", action="store_true")
    args = parser.parse_args()

    result = probe_endpoint_contract(
        client_contract=load_client_contract(args.client_contract),
        base_url=args.base_url,
        lang=args.lang,
        year=args.year,
        period_code=args.period_code,
        timeout_seconds=args.timeout_seconds,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    if args.require_verified and not result["endpoint_verified"]:
        raise SystemExit("KAP bulk endpoint contract was not verified")


if __name__ == "__main__":
    main()
