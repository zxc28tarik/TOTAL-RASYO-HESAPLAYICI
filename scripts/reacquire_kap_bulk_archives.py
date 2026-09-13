from __future__ import annotations

import argparse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Callable, Mapping
from zipfile import BadZipFile, ZipFile

import requests


CONTRACT = "KAP_BULK_ARCHIVE_REACQUISITION_V2"
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
RETRYABLE_HTTP_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
MAX_RETRY_DELAY_SECONDS = 300.0
MIN_429_DELAY_SECONDS = 60.0


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


def should_retry_status(status: int) -> bool:
    return int(status) in RETRYABLE_HTTP_STATUSES


def retry_delay_seconds(
    *,
    retry_after: str | None,
    attempt: int,
    base_seconds: float,
    status: int | None = None,
    now: datetime | None = None,
) -> float:
    if attempt < 1:
        raise ValueError("attempt must be >= 1")
    if base_seconds < 0:
        raise ValueError("base_seconds must be >= 0")

    parsed: float | None = None
    if retry_after:
        text = retry_after.strip()
        try:
            parsed = max(0.0, float(text))
        except ValueError:
            try:
                target = parsedate_to_datetime(text)
                if target.tzinfo is None:
                    target = target.replace(tzinfo=timezone.utc)
                current = now or datetime.now(timezone.utc)
                parsed = max(0.0, (target - current).total_seconds())
            except (TypeError, ValueError, OverflowError):
                parsed = None

    if parsed is None:
        parsed = base_seconds * (2 ** (attempt - 1))
        if status == 429:
            parsed = max(parsed, MIN_429_DELAY_SECONDS)
    return min(float(parsed), MAX_RETRY_DELAY_SECONDS)


def _failure_result(
    *,
    attempts: int,
    retry_events: list[dict[str, object]],
    error: str,
    http_status: int | None = None,
    content_type: str | None = None,
    content_disposition: str | None = None,
    final_url: str | None = None,
) -> dict[str, object]:
    return {
        "download_ok": False,
        "attempt_count": attempts,
        "retry_events": retry_events,
        "http_status": http_status,
        "content_type": content_type,
        "content_disposition": content_disposition,
        "final_url": final_url,
        "error": error,
    }


def download_one(
    session: requests.Session,
    *,
    url: str,
    destination: Path,
    timeout_seconds: int,
    max_attempts: int,
    retry_base_seconds: float,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    tmp = destination.with_suffix(destination.suffix + ".part")
    retry_events: list[dict[str, object]] = []

    for attempt in range(1, max_attempts + 1):
        tmp.unlink(missing_ok=True)
        digest = hashlib.sha256()
        size = 0
        try:
            with session.get(url, stream=True, timeout=timeout_seconds) as response:
                status = int(response.status_code)
                content_type = response.headers.get("Content-Type")
                disposition = response.headers.get("Content-Disposition")
                final_url = response.url

                if not response.ok:
                    preview = response.content[:256].decode("utf-8", errors="replace")
                    if should_retry_status(status) and attempt < max_attempts:
                        delay = retry_delay_seconds(
                            retry_after=response.headers.get("Retry-After"),
                            attempt=attempt,
                            base_seconds=retry_base_seconds,
                            status=status,
                        )
                        retry_events.append(
                            {
                                "attempt": attempt,
                                "reason": f"HTTP_{status}",
                                "delay_seconds": delay,
                                "retry_after": response.headers.get("Retry-After"),
                            }
                        )
                        sleeper(delay)
                        continue
                    return _failure_result(
                        attempts=attempt,
                        retry_events=retry_events,
                        error=f"HTTP_{status}:{preview}",
                        http_status=status,
                        content_type=content_type,
                        content_disposition=disposition,
                        final_url=final_url,
                    )

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
                "attempt_count": attempt,
                "retry_events": retry_events,
                "http_status": status,
                "content_type": content_type,
                "content_disposition": disposition,
                "final_url": final_url,
                "sha256": digest.hexdigest(),
                "size_bytes": size,
            }
        except requests.exceptions.RequestException as exc:
            tmp.unlink(missing_ok=True)
            if attempt < max_attempts:
                delay = retry_delay_seconds(
                    retry_after=None,
                    attempt=attempt,
                    base_seconds=retry_base_seconds,
                    status=None,
                )
                retry_events.append(
                    {
                        "attempt": attempt,
                        "reason": type(exc).__name__,
                        "delay_seconds": delay,
                    }
                )
                sleeper(delay)
                continue
            return _failure_result(
                attempts=attempt,
                retry_events=retry_events,
                error=f"{type(exc).__name__}:{exc}",
            )
        except Exception as exc:
            tmp.unlink(missing_ok=True)
            return _failure_result(
                attempts=attempt,
                retry_events=retry_events,
                error=f"{type(exc).__name__}:{exc}",
            )

    raise AssertionError("download retry loop exhausted unexpectedly")


def reacquire(
    *,
    manifest: dict[str, object],
    client_contract: dict[str, object],
    output_dir: Path,
    base_url: str,
    lang: str,
    timeout_seconds: int,
    max_attempts: int,
    retry_base_seconds: float,
    inter_archive_delay_seconds: float,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    archives = manifest["archives"]
    period_codes = client_contract.get("period_codes")
    templates = client_contract.get("bulk_templates")
    if not isinstance(archives, list) or not isinstance(period_codes, dict) or not isinstance(templates, dict):
        raise ValueError("manifest/client acquisition fields invalid")
    download_template = templates.get("download")
    if not isinstance(download_template, str):
        raise ValueError("client download endpoint template missing")
    if inter_archive_delay_seconds < 0:
        raise ValueError("inter_archive_delay_seconds must be >= 0")

    output_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update(
        {
            "Accept-Language": lang,
            "User-Agent": "Mozilla/5.0 TOTAL-RASYO-KAP-reacquisition/2.0",
        }
    )

    rows: list[dict[str, object]] = []
    for index, expected_raw in enumerate(archives):
        if not isinstance(expected_raw, dict):
            raise ValueError("archive row invalid")
        expected = {key: expected_raw[key] for key in REQUIRED_ARCHIVE_FIELDS}
        filename = str(expected["filename"])
        year, period, period_code = parse_manifest_filename(filename, period_codes)
        download_url = download_template.format(
            base_url=base_url.rstrip("/"),
            lang=lang,
            year=year,
            period_code=period_code,
        )
        row: dict[str, object] = {
            "filename": filename,
            "year": year,
            "period": period,
            "period_code": period_code,
            "download_url": download_url,
            "expected": expected,
            "check_policy": "OMITTED_DURING_BULK_REACQUISITION_TO_MINIMIZE_KAP_REQUEST_LOAD",
        }

        destination = output_dir / filename
        observed = download_one(
            session,
            url=download_url,
            destination=destination,
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
            retry_base_seconds=retry_base_seconds,
            sleeper=sleeper,
        )
        if observed.get("download_ok"):
            observed.update(inspect_zip(destination))
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
                    "attempt_count": observed.get("attempt_count"),
                    "retry_events": observed.get("retry_events"),
                    "observed_sha256": observed.get("sha256"),
                    "observed_size_bytes": observed.get("size_bytes"),
                    "observed_member_count": observed.get("member_count"),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )
        if index + 1 < len(archives) and inter_archive_delay_seconds:
            sleeper(inter_archive_delay_seconds)

    exact_count = sum(1 for row in rows if row["exact_manifest_match"])
    mismatch_rows = [
        {
            "filename": row["filename"],
            "reasons": row["mismatch_reasons"],
            "error": row["observed"].get("error") if isinstance(row.get("observed"), dict) else None,
            "attempt_count": row["observed"].get("attempt_count") if isinstance(row.get("observed"), dict) else None,
        }
        for row in rows
        if not row["exact_manifest_match"]
    ]
    return {
        "contract": CONTRACT,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source_manifest_contract": manifest.get("contract"),
        "source_manifest_captured_at": manifest.get("captured_at"),
        "client_contract": client_contract.get("contract"),
        "base_url": base_url,
        "lang": lang,
        "request_policy": {
            "check_endpoint_during_bulk": False,
            "max_attempts": max_attempts,
            "retry_base_seconds": retry_base_seconds,
            "min_429_delay_seconds_without_retry_after": MIN_429_DELAY_SECONDS,
            "max_retry_delay_seconds": MAX_RETRY_DELAY_SECONDS,
            "inter_archive_delay_seconds": inter_archive_delay_seconds,
            "parallelism": 1,
        },
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
    parser.add_argument("--max-attempts", type=int, default=6)
    parser.add_argument("--retry-base-seconds", type=float, default=15.0)
    parser.add_argument("--inter-archive-delay-seconds", type=float, default=5.0)
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
        max_attempts=args.max_attempts,
        retry_base_seconds=args.retry_base_seconds,
        inter_archive_delay_seconds=args.inter_archive_delay_seconds,
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
