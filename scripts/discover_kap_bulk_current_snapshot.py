from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Iterable, Mapping

from scripts.discover_kap_bulk_schema_signatures import discover_archives


CONTRACT = "KAP_BULK_TECHNICAL_SCHEMA_DISCOVERY_CURRENT_SNAPSHOT_V1"
EXPECTED_PUBLIC_EVIDENCE_CONTRACT = "KAP_BULK_PUBLIC_BYTE_EVIDENCE_V2"
IDENTITY_FIELDS = ("sha256", "size_bytes", "member_count", "uncompressed_bytes")
AUTHORIZATION_FIELDS = (
    "semantic_mapping_authorized",
    "pit_materialization_authorized",
    "real_60_cutoff_scoring_authorized",
)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_nonnegative_int(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"public evidence {key} invalid")
    return value


def _validate_identity(identity: Mapping[str, object], *, label: str) -> None:
    sha256 = identity.get("sha256")
    size_bytes = identity.get("size_bytes")
    member_count = identity.get("member_count")
    uncompressed_bytes = identity.get("uncompressed_bytes")
    if not isinstance(sha256, str) or len(sha256) != 64 or any(
        c not in "0123456789abcdef" for c in sha256.lower()
    ):
        raise ValueError(f"{label} sha256 invalid")
    if not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes <= 0:
        raise ValueError(f"{label} size_bytes invalid")
    if not isinstance(member_count, int) or isinstance(member_count, bool) or member_count < 0:
        raise ValueError(f"{label} member_count invalid")
    if not isinstance(uncompressed_bytes, int) or isinstance(uncompressed_bytes, bool) or uncompressed_bytes < 0:
        raise ValueError(f"{label} uncompressed_bytes invalid")


def _identities_equal(left: Mapping[str, object], right: Mapping[str, object]) -> bool:
    return all(left.get(field) == right.get(field) for field in IDENTITY_FIELDS)


def build_current_identity_manifest(public_evidence: Mapping[str, object]) -> dict[str, object]:
    if public_evidence.get("contract") != EXPECTED_PUBLIC_EVIDENCE_CONTRACT:
        raise ValueError("unexpected public byte evidence contract")
    if public_evidence.get("snapshot_complete") is not True:
        raise ValueError("public byte evidence snapshot_complete must be true")
    if public_evidence.get("public_release_authorized") is not True:
        raise ValueError("public byte evidence release authorization missing")
    for field in AUTHORIZATION_FIELDS:
        if public_evidence.get(field) is not False:
            raise ValueError(f"discovery requires {field}=false")

    expected_count = _require_nonnegative_int(public_evidence, "archive_count_expected")
    observed_count = _require_nonnegative_int(public_evidence, "archive_count_observed")
    receipt_match_count = _require_nonnegative_int(public_evidence, "receipt_byte_match_count")
    baseline_match_count = _require_nonnegative_int(
        public_evidence, "receipt_manifest_baseline_match_count"
    )
    public_verified_count = _require_nonnegative_int(public_evidence, "public_byte_verified_count")
    manifest_exact_count = _require_nonnegative_int(public_evidence, "manifest_exact_match_count")
    manifest_drift_count = _require_nonnegative_int(public_evidence, "manifest_drift_count")

    archives = public_evidence.get("archives")
    if not isinstance(archives, list) or not archives:
        raise ValueError("public evidence archives missing")
    if not (
        expected_count
        == observed_count
        == receipt_match_count
        == baseline_match_count
        == public_verified_count
        == len(archives)
    ):
        raise ValueError("public evidence archive verification counts inconsistent")
    if manifest_exact_count + manifest_drift_count != expected_count:
        raise ValueError("public evidence manifest exact/drift counts inconsistent")

    names: set[str] = set()
    current_manifest_rows: list[dict[str, object]] = []
    drift_names_from_rows: set[str] = set()
    exact_from_rows = 0

    for row in archives:
        if not isinstance(row, Mapping):
            raise ValueError("public evidence archive row must be an object")
        filename = row.get("filename")
        if not isinstance(filename, str) or not filename or filename in names:
            raise ValueError(f"public evidence filename invalid/duplicate: {filename}")
        names.add(filename)

        for flag in (
            "public_byte_verified",
            "receipt_byte_match",
            "receipt_manifest_baseline_match",
            "official_download_receipt_ok",
        ):
            if row.get(flag) is not True:
                raise ValueError(f"public evidence {flag} must be true: {filename}")

        public_observed = row.get("public_observed")
        receipt_observed = row.get("receipt_observed")
        manifest_expected = row.get("manifest_expected")
        if not isinstance(public_observed, Mapping):
            raise ValueError(f"public observed identity missing: {filename}")
        if not isinstance(receipt_observed, Mapping):
            raise ValueError(f"receipt observed identity missing: {filename}")
        if not isinstance(manifest_expected, Mapping):
            raise ValueError(f"preserved manifest identity missing: {filename}")
        _validate_identity(public_observed, label=f"public observed {filename}")
        _validate_identity(receipt_observed, label=f"receipt observed {filename}")
        _validate_identity(manifest_expected, label=f"manifest expected {filename}")
        if public_observed.get("zip_readable") is not True or receipt_observed.get("zip_readable") is not True:
            raise ValueError(f"zip readability evidence missing: {filename}")
        if not _identities_equal(public_observed, receipt_observed):
            raise ValueError(f"public bytes disagree with acquisition receipt: {filename}")

        actual_manifest_exact = _identities_equal(public_observed, manifest_expected)
        if row.get("manifest_exact_match") is not actual_manifest_exact:
            raise ValueError(f"manifest_exact_match flag inconsistent: {filename}")
        mismatch_reasons = row.get("manifest_mismatch_reasons")
        if not isinstance(mismatch_reasons, list):
            raise ValueError(f"manifest mismatch reasons missing: {filename}")
        if actual_manifest_exact:
            if mismatch_reasons:
                raise ValueError(f"exact archive unexpectedly has mismatch reasons: {filename}")
            exact_from_rows += 1
        else:
            if not mismatch_reasons:
                raise ValueError(f"drifted archive lacks mismatch reasons: {filename}")
            drift_names_from_rows.add(filename)

        current_manifest_rows.append(
            {
                "filename": filename,
                "sha256": str(public_observed["sha256"]).lower(),
                "member_count": int(public_observed["member_count"]),
            }
        )

    drifts = public_evidence.get("drifts")
    if not isinstance(drifts, list):
        raise ValueError("public evidence drifts list missing")
    drift_names_declared: set[str] = set()
    for row in drifts:
        if not isinstance(row, Mapping) or not isinstance(row.get("filename"), str):
            raise ValueError("public evidence drift row invalid")
        filename = str(row["filename"])
        if filename in drift_names_declared:
            raise ValueError(f"duplicate public evidence drift filename: {filename}")
        drift_names_declared.add(filename)

    if exact_from_rows != manifest_exact_count:
        raise ValueError("public evidence row exact count inconsistent")
    if len(drift_names_from_rows) != manifest_drift_count:
        raise ValueError("public evidence row drift count inconsistent")
    if drift_names_from_rows != drift_names_declared:
        raise ValueError("public evidence drift filename set inconsistent")

    return {
        "archive_count": expected_count,
        "archives": sorted(current_manifest_rows, key=lambda item: str(item["filename"])),
    }


def discover_current_snapshot(
    archives: Iterable[Path],
    *,
    public_evidence: Mapping[str, object],
    public_evidence_sha256: str,
) -> dict[str, object]:
    current_identity_manifest = build_current_identity_manifest(public_evidence)
    result = discover_archives(
        archives,
        manifest=current_identity_manifest,
        manifest_sha256=None,
    )
    result["contract"] = CONTRACT
    result["archive_manifest_verified"] = False
    result.pop("source_manifest_sha256", None)
    result["current_snapshot_public_evidence_verified"] = True
    result["source_public_evidence_sha256"] = public_evidence_sha256
    result["source_workflow_run_id"] = public_evidence.get("workflow_run_id")
    result["source_acquisition_head_sha"] = public_evidence.get("acquisition_head_sha")
    result["source_acquisition_receipt_sha256"] = public_evidence.get("acquisition_receipt_sha256")
    result["preserved_manifest_sha256"] = public_evidence.get("manifest_file_sha256")
    result["preserved_manifest_exact_match_count"] = public_evidence.get("manifest_exact_match_count")
    result["preserved_manifest_drift_count"] = public_evidence.get("manifest_drift_count")
    result["preserved_manifest_drift_filenames"] = sorted(
        str(row["filename"])
        for row in public_evidence.get("drifts", [])
        if isinstance(row, Mapping) and isinstance(row.get("filename"), str)
    )
    result["semantic_mapping_authorized"] = False
    result["pit_materialization_authorized"] = False
    result["real_60_cutoff_scoring_authorized"] = False
    result["purpose"] = "DISCOVERY_ONLY_CURRENT_OFFICIAL_SNAPSHOT_ROLE_ROW_LABEL_EVIDENCE"
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, action="append", required=True)
    parser.add_argument("--public-evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    evidence_bytes = args.public_evidence.read_bytes()
    public_evidence = json.loads(evidence_bytes.decode("utf-8"))
    if not isinstance(public_evidence, dict):
        raise ValueError("public evidence JSON root must be an object")
    result = discover_current_snapshot(
        args.archive,
        public_evidence=public_evidence,
        public_evidence_sha256=_sha256_bytes(evidence_bytes),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    args.output.write_text(text, encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "sha256": _sha256_bytes(text.encode("utf-8")),
                "archive_count": result["archive_count"],
                "report_count": result["report_count"],
                "technical_schema_count": result["technical_schema_count"],
                "preserved_manifest_exact_match_count": result["preserved_manifest_exact_match_count"],
                "preserved_manifest_drift_count": result["preserved_manifest_drift_count"],
                "current_snapshot_public_evidence_verified": result[
                    "current_snapshot_public_evidence_verified"
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
