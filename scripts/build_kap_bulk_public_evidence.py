from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Mapping
from zipfile import BadZipFile, ZipFile


CONTRACT = "KAP_BULK_PUBLIC_BYTE_EVIDENCE_V1"
EXPECTED_MANIFEST_CONTRACT = "KAP_BULK_FINANCIAL_EXPORT_ARCHIVES_V1"
DEFAULT_MANIFEST = Path(
    "data/backtest_sources/kap_bulk_financial_source_capture/archive_manifest.json"
)
IDENTITY_FIELDS = ("sha256", "size_bytes", "member_count", "uncompressed_bytes")


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


def load_manifest(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("manifest root must be an object")
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


def build_public_byte_evidence(
    *,
    manifest_path: Path,
    raw_dir: Path,
    workflow_run_id: int,
    acquisition_head_sha: str,
) -> dict[str, object]:
    if workflow_run_id <= 0:
        raise ValueError("workflow_run_id must be positive")
    if len(acquisition_head_sha) != 40 or any(c not in "0123456789abcdef" for c in acquisition_head_sha):
        raise ValueError("acquisition_head_sha must be 40 lowercase hex characters")

    manifest = load_manifest(manifest_path)
    archive_rows = manifest["archives"]
    assert isinstance(archive_rows, list)
    expected_by_name = {
        str(row["filename"]): row
        for row in archive_rows
        if isinstance(row, dict)
    }
    expected_names = set(expected_by_name)
    observed_paths = sorted(raw_dir.glob("KAP_*.zip"))
    observed_names = {path.name for path in observed_paths}
    missing = sorted(expected_names - observed_names)
    unexpected = sorted(observed_names - expected_names)

    rows: list[dict[str, object]] = []
    for filename in sorted(expected_names):
        expected = expected_by_name[filename]
        path = raw_dir / filename
        if not path.is_file():
            rows.append(
                {
                    "filename": filename,
                    "exact_manifest_match": False,
                    "mismatch_reasons": ["ARCHIVE_MISSING"],
                    "expected": {field: expected[field] for field in IDENTITY_FIELDS},
                    "observed": None,
                }
            )
            continue
        observed = observe_archive(path)
        exact, reasons = compare_identity(expected, observed)
        rows.append(
            {
                "filename": filename,
                "exact_manifest_match": exact,
                "mismatch_reasons": reasons,
                "expected": {field: expected[field] for field in IDENTITY_FIELDS},
                "observed": {field: observed.get(field) for field in (*IDENTITY_FIELDS, "zip_readable")},
            }
        )

    exact_count = sum(1 for row in rows if row["exact_manifest_match"])
    all_exact = (
        not missing
        and not unexpected
        and len(rows) == len(expected_names) == manifest.get("archive_count")
        and exact_count == len(rows)
    )
    return {
        "contract": CONTRACT,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "workflow_run_id": workflow_run_id,
        "acquisition_head_sha": acquisition_head_sha,
        "manifest_path": str(manifest_path),
        "manifest_file_sha256": sha256_file(manifest_path),
        "source_manifest_contract": manifest.get("contract"),
        "source_manifest_captured_at": manifest.get("captured_at"),
        "archive_count_expected": len(expected_names),
        "archive_count_observed": len(observed_names),
        "exact_manifest_match_count": exact_count,
        "missing_archives": missing,
        "unexpected_archives": unexpected,
        "all_exact_manifest_match": all_exact,
        "public_release_authorized": all_exact,
        "semantic_mapping_authorized": False,
        "real_60_cutoff_scoring_authorized": False,
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
        observed = row.get("observed")
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
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--workflow-run-id", type=int, required=True)
    parser.add_argument("--acquisition-head-sha", required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-sha256s", type=Path, required=True)
    parser.add_argument("--require-exact", action="store_true")
    args = parser.parse_args()

    evidence = build_public_byte_evidence(
        manifest_path=args.manifest,
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
    print(
        json.dumps(
            {
                "archive_count_expected": evidence["archive_count_expected"],
                "archive_count_observed": evidence["archive_count_observed"],
                "exact_manifest_match_count": evidence["exact_manifest_match_count"],
                "all_exact_manifest_match": evidence["all_exact_manifest_match"],
            },
            sort_keys=True,
        )
    )
    if args.require_exact and not evidence["all_exact_manifest_match"]:
        raise SystemExit("public byte evidence is not an exact manifest match")


if __name__ == "__main__":
    main()
