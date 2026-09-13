from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Mapping
from zipfile import BadZipFile, ZipFile


CONTRACT = "KAP_BULK_PUBLIC_BYTE_EVIDENCE_V2"
EXPECTED_MANIFEST_CONTRACT = "KAP_BULK_FINANCIAL_EXPORT_ARCHIVES_V1"
ACQUISITION_CONTRACTS = {"KAP_BULK_ARCHIVE_REACQUISITION_V1", "KAP_BULK_ARCHIVE_REACQUISITION_V2"}
DEFAULT_MANIFEST = Path(
    "data/backtest_sources/kap_bulk_financial_source_capture/archive_manifest.json"
)
IDENTITY_FIELDS = ("sha256", "size_bytes", "member_count", "uncompressed_bytes")
OFFICIAL_DOWNLOAD_PREFIX = "https://kap.org.tr/tr/api/financialTable/download/"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def inspect_zip(path: Path) -> dict[str, object]:
    try:
        with ZipFile(path) as bundle:
            infos = [info for info in bundle.infolist() if not info.is_dir()]
            xls_members = [info for info in infos if info.filename.endswith(".xls")]
            return {
                "zip_readable": True,
                "member_count": len(xls_members),
                "uncompressed_bytes": sum(int(info.file_size) for info in infos),
            }
    except (BadZipFile, OSError) as exc:
        return {
            "zip_readable": False,
            "member_count": None,
            "uncompressed_bytes": None,
            "zip_error": f"{type(exc).__name__}:{exc}",
        }


def _load_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload


def load_manifest(path: Path) -> dict[str, object]:
    payload = _load_json(path)
    if payload.get("contract") != EXPECTED_MANIFEST_CONTRACT:
        raise ValueError("unexpected manifest contract")
    archives = payload.get("archives")
    if not isinstance(archives, list) or not archives:
        raise ValueError("manifest archives list missing")
    if payload.get("archive_count") != len(archives):
        raise ValueError("manifest archive_count inconsistent")
    names: set[str] = set()
    for row in archives:
        if not isinstance(row, dict):
            raise ValueError("manifest archive row must be an object")
        filename = row.get("filename")
        if not isinstance(filename, str) or not filename or filename in names:
            raise ValueError(f"manifest filename invalid/duplicate: {filename}")
        names.add(filename)
        for field in IDENTITY_FIELDS:
            if field not in row:
                raise ValueError(f"manifest identity field missing: {field}")
    return payload


def load_acquisition_receipt(path: Path) -> dict[str, object]:
    payload = _load_json(path)
    if payload.get("contract") not in ACQUISITION_CONTRACTS:
        raise ValueError("unexpected acquisition receipt contract")
    archives = payload.get("archives")
    if not isinstance(archives, list) or not archives:
        raise ValueError("acquisition receipt archives list missing")
    if payload.get("archive_count") != len(archives):
        raise ValueError("acquisition receipt archive_count inconsistent")
    names: set[str] = set()
    for row in archives:
        if not isinstance(row, dict):
            raise ValueError("acquisition receipt row must be an object")
        filename = row.get("filename")
        observed = row.get("observed")
        if not isinstance(filename, str) or not filename or filename in names:
            raise ValueError(f"acquisition filename invalid/duplicate: {filename}")
        if not isinstance(observed, dict):
            raise ValueError(f"acquisition observed identity missing: {filename}")
        names.add(filename)
    return payload


def observe_archive(path: Path) -> dict[str, object]:
    observed: dict[str, object] = {
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }
    observed.update(inspect_zip(path))
    return observed


def compare_identity(
    expected: Mapping[str, object],
    observed: Mapping[str, object],
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if not observed.get("zip_readable"):
        reasons.append("ZIP_NOT_READABLE")
    for field in IDENTITY_FIELDS:
        if observed.get(field) != expected.get(field):
            reasons.append(f"{field.upper()}_MISMATCH")
    return not reasons, reasons


def _official_download_receipt_ok(row: Mapping[str, object]) -> bool:
    observed = row.get("observed")
    if not isinstance(observed, Mapping):
        return False
    final_url = observed.get("final_url")
    return (
        observed.get("download_ok") is True
        and observed.get("http_status") == 200
        and observed.get("zip_readable") is True
        and observed.get("content_type") == "application/vnd.zip"
        and isinstance(final_url, str)
        and final_url.startswith(OFFICIAL_DOWNLOAD_PREFIX)
    )


def build_public_byte_evidence(
    *,
    manifest_path: Path,
    acquisition_receipt_path: Path,
    raw_dir: Path,
    workflow_run_id: int,
    acquisition_head_sha: str,
) -> dict[str, object]:
    if workflow_run_id <= 0:
        raise ValueError("workflow_run_id must be positive")
    if len(acquisition_head_sha) != 40 or any(c not in "0123456789abcdef" for c in acquisition_head_sha):
        raise ValueError("acquisition_head_sha must be 40 lowercase hex characters")

    manifest = load_manifest(manifest_path)
    receipt = load_acquisition_receipt(acquisition_receipt_path)
    manifest_rows = manifest["archives"]
    receipt_rows = receipt["archives"]
    assert isinstance(manifest_rows, list)
    assert isinstance(receipt_rows, list)
    expected_by_name = {str(row["filename"]): row for row in manifest_rows if isinstance(row, dict)}
    receipt_by_name = {str(row["filename"]): row for row in receipt_rows if isinstance(row, dict)}
    expected_names = set(expected_by_name)
    receipt_names = set(receipt_by_name)
    observed_paths = sorted(raw_dir.glob("KAP_*.zip"))
    observed_names = {path.name for path in observed_paths}

    missing = sorted(expected_names - observed_names)
    unexpected = sorted(observed_names - expected_names)
    receipt_missing = sorted(expected_names - receipt_names)
    receipt_unexpected = sorted(receipt_names - expected_names)

    rows: list[dict[str, object]] = []
    for filename in sorted(expected_names):
        manifest_expected = expected_by_name[filename]
        receipt_row = receipt_by_name.get(filename)
        path = raw_dir / filename
        if not path.is_file():
            rows.append({
                "filename": filename,
                "manifest_exact_match": False,
                "receipt_byte_match": False,
                "receipt_manifest_baseline_match": False,
                "public_byte_verified": False,
                "manifest_mismatch_reasons": ["ARCHIVE_MISSING"],
                "receipt_mismatch_reasons": ["ARCHIVE_MISSING"],
                "manifest_expected": {field: manifest_expected[field] for field in IDENTITY_FIELDS},
                "receipt_observed": None,
                "public_observed": None,
            })
            continue

        public_observed = observe_archive(path)
        manifest_exact, manifest_reasons = compare_identity(manifest_expected, public_observed)
        if not isinstance(receipt_row, dict) or not isinstance(receipt_row.get("observed"), dict):
            receipt_exact = False
            receipt_reasons = ["ACQUISITION_RECEIPT_MISSING"]
            receipt_observed = None
            receipt_baseline_exact = False
            official_receipt_ok = False
        else:
            receipt_observed = receipt_row["observed"]
            receipt_expected = receipt_row.get("expected")
            receipt_exact, receipt_reasons = compare_identity(receipt_observed, public_observed)
            receipt_baseline_exact = (
                isinstance(receipt_expected, Mapping)
                and all(receipt_expected.get(field) == manifest_expected.get(field) for field in IDENTITY_FIELDS)
            )
            official_receipt_ok = _official_download_receipt_ok(receipt_row)

        rows.append({
            "filename": filename,
            "manifest_exact_match": manifest_exact,
            "receipt_byte_match": receipt_exact,
            "receipt_manifest_baseline_match": receipt_baseline_exact,
            "official_download_receipt_ok": official_receipt_ok,
            "public_byte_verified": receipt_exact and receipt_baseline_exact and official_receipt_ok,
            "manifest_mismatch_reasons": manifest_reasons,
            "receipt_mismatch_reasons": receipt_reasons,
            "manifest_expected": {field: manifest_expected[field] for field in IDENTITY_FIELDS},
            "receipt_observed": (
                {field: receipt_observed.get(field) for field in (*IDENTITY_FIELDS, "zip_readable")}
                if isinstance(receipt_observed, dict) else None
            ),
            "public_observed": {field: public_observed.get(field) for field in (*IDENTITY_FIELDS, "zip_readable")},
        })

    manifest_exact_count = sum(1 for row in rows if row["manifest_exact_match"])
    receipt_match_count = sum(1 for row in rows if row["receipt_byte_match"])
    receipt_baseline_match_count = sum(1 for row in rows if row["receipt_manifest_baseline_match"])
    public_verified_count = sum(1 for row in rows if row["public_byte_verified"])
    snapshot_complete = (
        not missing
        and not unexpected
        and not receipt_missing
        and not receipt_unexpected
        and len(rows) == len(expected_names) == manifest.get("archive_count") == receipt.get("archive_count")
        and receipt_match_count == len(rows)
        and receipt_baseline_match_count == len(rows)
        and public_verified_count == len(rows)
    )
    drift_rows = [
        {
            "filename": row["filename"],
            "manifest_mismatch_reasons": row["manifest_mismatch_reasons"],
            "manifest_expected": row["manifest_expected"],
            "current_observed": row["public_observed"],
        }
        for row in rows
        if not row["manifest_exact_match"]
    ]
    return {
        "contract": CONTRACT,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "workflow_run_id": workflow_run_id,
        "acquisition_head_sha": acquisition_head_sha,
        "manifest_path": str(manifest_path),
        "manifest_file_sha256": sha256_file(manifest_path),
        "acquisition_receipt_path": str(acquisition_receipt_path),
        "acquisition_receipt_sha256": sha256_file(acquisition_receipt_path),
        "source_manifest_contract": manifest.get("contract"),
        "source_manifest_captured_at": manifest.get("captured_at"),
        "acquisition_receipt_contract": receipt.get("contract"),
        "acquisition_captured_at": receipt.get("captured_at"),
        "archive_count_expected": len(expected_names),
        "archive_count_observed": len(observed_names),
        "manifest_exact_match_count": manifest_exact_count,
        "manifest_drift_count": len(rows) - manifest_exact_count,
        "receipt_byte_match_count": receipt_match_count,
        "receipt_manifest_baseline_match_count": receipt_baseline_match_count,
        "public_byte_verified_count": public_verified_count,
        "acquisition_declared_exact_manifest_match_count": receipt.get("exact_manifest_match_count"),
        "acquisition_declared_mismatch_count": receipt.get("mismatch_count"),
        "acquisition_declared_all_exact_manifest_match": receipt.get("all_exact_manifest_match"),
        "missing_archives": missing,
        "unexpected_archives": unexpected,
        "receipt_missing_archives": receipt_missing,
        "receipt_unexpected_archives": receipt_unexpected,
        "all_exact_manifest_match": manifest_exact_count == len(rows) and snapshot_complete,
        "snapshot_complete": snapshot_complete,
        "public_release_authorized": snapshot_complete,
        "semantic_mapping_authorized": False,
        "pit_materialization_authorized": False,
        "real_60_cutoff_scoring_authorized": False,
        "drifts": drift_rows,
        "archives": rows,
    }


def write_sha256s(evidence: Mapping[str, object], path: Path) -> None:
    archives = evidence.get("archives")
    if not isinstance(archives, list):
        raise ValueError("evidence archives missing")
    lines: list[str] = []
    for row in archives:
        if not isinstance(row, dict):
            raise ValueError("evidence archive row invalid")
        observed = row.get("public_observed")
        filename = row.get("filename")
        if not isinstance(filename, str) or not isinstance(observed, dict):
            continue
        sha256 = observed.get("sha256")
        if isinstance(sha256, str) and len(sha256) == 64:
            lines.append(f"{sha256}  {filename}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--acquisition-receipt", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--workflow-run-id", type=int, required=True)
    parser.add_argument("--acquisition-head-sha", required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-sha256s", type=Path, required=True)
    parser.add_argument("--require-exact", action="store_true")
    parser.add_argument("--require-publishable-snapshot", action="store_true")
    args = parser.parse_args()

    evidence = build_public_byte_evidence(
        manifest_path=args.manifest,
        acquisition_receipt_path=args.acquisition_receipt,
        raw_dir=args.raw_dir,
        workflow_run_id=args.workflow_run_id,
        acquisition_head_sha=args.acquisition_head_sha,
    )
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(evidence, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    write_sha256s(evidence, args.output_sha256s)
    print(json.dumps({
        "archive_count_expected": evidence["archive_count_expected"],
        "archive_count_observed": evidence["archive_count_observed"],
        "manifest_exact_match_count": evidence["manifest_exact_match_count"],
        "manifest_drift_count": evidence["manifest_drift_count"],
        "receipt_byte_match_count": evidence["receipt_byte_match_count"],
        "receipt_manifest_baseline_match_count": evidence["receipt_manifest_baseline_match_count"],
        "snapshot_complete": evidence["snapshot_complete"],
        "all_exact_manifest_match": evidence["all_exact_manifest_match"],
    }, sort_keys=True))
    if args.require_exact and not evidence["all_exact_manifest_match"]:
        raise SystemExit("public byte evidence is not an exact preserved-manifest match")
    if args.require_publishable_snapshot and not evidence["public_release_authorized"]:
        raise SystemExit("public byte evidence snapshot is incomplete or disagrees with acquisition receipt")


if __name__ == "__main__":
    main()
