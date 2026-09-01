from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Mapping
from zipfile import BadZipFile, ZipFile

import requests


CONTRACT = "KAP_BULK_ARCHIVE_REACQUISITION_V1"
DEFAULT_MANIFEST = Path(
    "data/backtest_sources/kap_bulk_financial_source_capture/archive_manifest.json"
)
DEFAULT_CLIENT_CONTRACT = Path(
    "data/backtest_sources/kap_bulk_financial_source_capture/client_download_contract.json"
)
FILENAME_RE = re.compile(r"^KAP_(?P<year>\d{4})_(?P<period>3A|6A|9A|Y)\.zip$")
REQUIRED_ARCHIVE_FIELDS = (
    "filename",
    "sha256",
    "size_bytes",
    "member_count",
    "uncompressed_bytes",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_manifest_filename(filename: str, period_codes: Mapping[str, object]) -> tuple[int, str, int]:
    match = FILENAME_RE.fullmatch(filename)
    if not match:
        raise ValueError(f"unexpected KAP archive filename: {filename}")
    year = int(match.group("year"))
    period = match.group("period")
    code = period_codes.get(period)
    if not isinstance(code, int) or code not in {1, 2, 3, 4}:
        raise ValueError(f"missing/invalid period code for {period}")
    return year, period, code


def load_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload


def load_inputs(manifest_path: Path, client_contract_path: Path) -> tuple[dict[str, object], dict[str, object]]:
    manifest = load_json(manifest_path)
    client = load_json(client_contract_path)
    if manifest.get("contract") != "KAP_BULK_FINANCIAL_EXPORT_ARCHIVES_V1":
        raise ValueError("unexpected archive manifest contract")
    if client.get("contract") != "PUBLIC_KAP_FINANCIAL_TABLE_DOWNLOAD_CLIENT_CONTRACT_V1":
        raise ValueError("unexpected client download contract")
    archives = manifest.get("archives")
    declared_count = manifest.get("archive_count")
    if not isinstance(archives, list) or not archives:
        raise ValueError("archive manifest archives list missing")
    if not isinstance(declared_count, int) or declared_count != len(archives):
        raise ValueError("archive manifest archive_count inconsistent")
    names: set[str] = set()
    for row in archives:
        if not isinstance(row, dict):
            raise ValueError("archive manifest row must be an object")
        for field in REQUIRED_ARCHIVE_FIELDS:
            if field not in row:
                raise ValueError(f"archive manifest field missing: {field}")
        filename = row["filename"]
        if not isinstance(filename, str) or filename in names:
            raise ValueError(f"archive manifest filename invalid/duplicate: {filename}")
        names.add(filename)
    return manifest, client


def inspect_zip(path: Path) -> dict[str, object]:
    try:
        with ZipFile(path) as bundle:
            infos = [info for info in bundle.infolist() if not info.is_dir()]
            xls_members = [info for info in infos if info.filename.endswith(".xls")]
            return {
                "zip_readable": True,
                "member_count": len(xls_members),
                "uncompressed_bytes": sum(int(info.file_size) for info in infos),
                "first_member": infos[0].filename if infos else None,
                "last_member": infos[-1].filename if infos else None,
            }
    except (BadZipFile, OSError) as exc:
        return {
            "zip_readable": False,
            "member_count": None,
            "uncompressed_bytes": None,
            "zip_error": f"{type(exc).__name__}:{exc}",
        }


def evaluate_observation(expected: Mapping[str, object], observed: Mapping[str, object]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if not observed.get("download_ok"):
        reasons.append("DOWNLOAD_NOT_OK")
    if not observed.get("zip_readable"):
        reasons.append("ZIP_NOT_READABLE")
    comparisons = (
        ("sha256", "SHA256_MISMATCH"),
        ("size_bytes", "SIZE_BYTES_MISMATCH"),
        ("member_count", "MEMBER_COUNT_MISMATCH"),
        ("uncompressed_bytes", "UNCOMPRESSED_BYTES_MISMATCH"),
    )
    for field, reason in comparisons:
        if observed.get(field) != expected.get(field):
            reasons.append(reason)
    return not reasons, reasons


def _download_one(
    session: requests.Session,
    *,
    url: str,
    destination: Path,
    timeout_seconds: int,
) -> dict[str, object]:
    tmp = destination.with_suffix(destination.suffix + ".part")
    tmp.unlink(missing_ok=True)
    digest = hashlib.sha256()
    size = 0
    try:
        with session.get(url, stream=True, timeout=timeout_seconds) as response:
            status = response.status_code
            content_type = response.headers.get("Content-Type")
            disposition = response.headers.get("Content-Disposition")
            final_url = response.url
            if not response.ok:
                preview = response.content[:256].decode("utf-8", errors="replace")
                return {
                    "download_ok": False,
                    "http_status": status,
                    "content_type": content_type,
                    "content_disposition": disposition,
                    "final_url": final_url,
                    "error": f"HTTP_{status}:{preview}",
                }
            with tmp.open("wb") as handle:
                for block in response.iter_content(chunk_size=1024 * 1024):
                    if not block:
                        continue
                    handle.write(block)
                    digest.update(block)
                    size += len(block)
        tmp.replace(destination)
        return {
            "download_ok": True,
            "http_status": status,
            "content_type": content_type,
            "content_disposition": disposition,
            "final_url": final_url,
            "sha256": digest.hexdigest(),
            "size_bytes": size,
        }
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        return {
            "download_ok": False,
            "error": f"{type(exc).__name__}:{exc}",
        }


def reacquire(
    *,
    manifest: dict[str, object],
    client_contract: dict[str, object],
    output_dir: Path,
    base_url: str,
    lang: str,
    timeout_seconds: int,
) -> dict[str, object]:
    archives = manifest["archives"]
    period_codes = client_contract.get("period_codes")
    templates = client_contract.get("bulk_templates")
    if not isinstance(archives, list) or not isinstance(period_codes, dict) or not isinstance(templates, dict):
        raise ValueError("manifest/client acquisition fields invalid")
    download_template = templates.get("download")
    check_template = templates.get("check_file_exist")
    if not isinstance(download_template, str) or not isinstance(check_template, str):
        raise ValueError("client endpoint templates missing")

    output_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update(
        {
            "Accept-Language": lang,
            "User-Agent": "Mozilla/5.0 TOTAL-RASYO-KAP-reacquisition/1.0",
        }
    )

    rows: list[dict[str, object]] = []
    for expected_raw in archives:
        if not isinstance(expected_raw, dict):
            raise ValueError("archive row invalid")
        expected = {key: expected_raw[key] for key in REQUIRED_ARCHIVE_FIELDS}
        filename = str(expected["filename"])
        year, period, period_code = parse_manifest_filename(filename, period_codes)
        format_args = {
            "base_url": base_url.rstrip("/"),
            "lang": lang,
            "year": year,
            "period_code": period_code,
        }
        check_url = check_template.format(**format_args)
        download_url = download_template.format(**format_args)
        row: dict[str, object] = {
            "filename": filename,
            "year": year,
            "period": period,
            "period_code": period_code,
            "check_url": check_url,
            "download_url": download_url,
            "expected": expected,
        }

        try:
            check = session.get(check_url, timeout=timeout_seconds)
            row["check"] = {
                "status": check.status_code,
                "content_type": check.headers.get("Content-Type"),
                "body_sha256": hashlib.sha256(check.content).hexdigest(),
                "body_preview": check.content[:256].decode("utf-8", errors="replace"),
            }
        except Exception as exc:
            row["check"] = {"error": f"{type(exc).__name__}:{exc}"}

        destination = output_dir / filename
        observed = _download_one(
            session,
            url=download_url,
            destination=destination,
            timeout_seconds=timeout_seconds,
        )
        if observed.get("download_ok"):
            observed.update(inspect_zip(destination))
            # Recompute from disk to protect against write/rename corruption.
            observed["sha256"] = sha256_file(destination)
            observed["size_bytes"] = destination.stat().st_size
        row["observed"] = observed
        exact, reasons = evaluate_observation(expected, observed)
        row["exact_manifest_match"] = exact
        row["mismatch_reasons"] = reasons
        rows.append(row)
        print(
            json.dumps(
                {
                    "filename": filename,
                    "exact_manifest_match": exact,
                    "mismatch_reasons": reasons,
                    "observed_sha256": observed.get("sha256"),
                    "observed_size_bytes": observed.get("size_bytes"),
                    "observed_member_count": observed.get("member_count"),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )

    exact_count = sum(1 for row in rows if row["exact_manifest_match"])
    mismatch_rows = [
        {"filename": row["filename"], "reasons": row["mismatch_reasons"]}
        for row in rows
        if not row["exact_manifest_match"]
    ]
    return {
        "contract": CONTRACT,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source_manifest_contract": manifest.get("contract"),
        "source_manifest_captured_at": manifest.get("captured_at"),
        "source_manifest_sha256": hashlib.sha256(
            (json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        ).hexdigest(),
        "client_contract": client_contract.get("contract"),
        "base_url": base_url,
        "lang": lang,
        "archive_count": len(rows),
        "exact_manifest_match_count": exact_count,
        "mismatch_count": len(rows) - exact_count,
        "all_exact_manifest_match": exact_count == len(rows) == manifest.get("archive_count"),
        "mismatches": mismatch_rows,
        "archives": rows,
        "raw_archives_committed_to_git": False,
        "raw_archives_output_dir": str(output_dir),
        "semantic_mapping_authorized": False,
        "next_gate": "RUN_TECHNICAL_SCHEMA_DISCOVERY_ONLY_IF_ALL_EXACT_MANIFEST_MATCH",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--client-contract", type=Path, default=DEFAULT_CLIENT_CONTRACT)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--base-url", default="https://kap.org.tr")
    parser.add_argument("--lang", default="tr")
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--require-exact", action="store_true")
    args = parser.parse_args()

    manifest, client = load_inputs(args.manifest, args.client_contract)
    result = reacquire(
        manifest=manifest,
        client_contract=client,
        output_dir=args.output_dir,
        base_url=args.base_url,
        lang=args.lang,
        timeout_seconds=args.timeout_seconds,
    )
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "archive_count": result["archive_count"],
                "exact_manifest_match_count": result["exact_manifest_match_count"],
                "mismatch_count": result["mismatch_count"],
                "all_exact_manifest_match": result["all_exact_manifest_match"],
                "receipt": str(args.receipt),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    if args.require_exact and not result["all_exact_manifest_match"]:
        raise SystemExit("one or more KAP bulk archives differ from the preserved manifest")


if __name__ == "__main__":
    main()
