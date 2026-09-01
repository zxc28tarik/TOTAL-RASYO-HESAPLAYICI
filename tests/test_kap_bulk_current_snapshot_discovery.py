from pathlib import Path

import pytest

import scripts.discover_kap_bulk_current_snapshot as current_discovery
from scripts.discover_kap_bulk_current_snapshot import (
    build_current_identity_manifest,
    discover_current_snapshot,
)


def _identity(seed: str, *, members: int = 1) -> dict[str, object]:
    return {
        "sha256": seed * 64,
        "size_bytes": 100,
        "member_count": members,
        "uncompressed_bytes": 500,
        "zip_readable": True,
    }


def _evidence(*, drift: bool = False) -> dict[str, object]:
    current = _identity("a", members=2)
    preserved = _identity("b", members=1) if drift else dict(current)
    return {
        "contract": "KAP_BULK_PUBLIC_BYTE_EVIDENCE_V2",
        "snapshot_complete": True,
        "public_release_authorized": True,
        "semantic_mapping_authorized": False,
        "pit_materialization_authorized": False,
        "real_60_cutoff_scoring_authorized": False,
        "archive_count_expected": 1,
        "archive_count_observed": 1,
        "receipt_byte_match_count": 1,
        "receipt_manifest_baseline_match_count": 1,
        "public_byte_verified_count": 1,
        "manifest_exact_match_count": 0 if drift else 1,
        "manifest_drift_count": 1 if drift else 0,
        "workflow_run_id": 33568804543,
        "acquisition_head_sha": "d" * 40,
        "acquisition_receipt_sha256": "e" * 64,
        "manifest_file_sha256": "f" * 64,
        "archives": [
            {
                "filename": "KAP_2025_Y.zip",
                "public_byte_verified": True,
                "receipt_byte_match": True,
                "receipt_manifest_baseline_match": True,
                "official_download_receipt_ok": True,
                "manifest_exact_match": not drift,
                "manifest_mismatch_reasons": ["SHA256_MISMATCH", "MEMBER_COUNT_MISMATCH"] if drift else [],
                "public_observed": current,
                "receipt_observed": dict(current),
                "manifest_expected": preserved,
            }
        ],
        "drifts": ([{"filename": "KAP_2025_Y.zip"}] if drift else []),
    }


def test_current_identity_manifest_accepts_exact_public_snapshot() -> None:
    manifest = build_current_identity_manifest(_evidence())
    assert manifest == {
        "archive_count": 1,
        "archives": [
            {
                "filename": "KAP_2025_Y.zip",
                "sha256": "a" * 64,
                "member_count": 2,
            }
        ],
    }


def test_current_identity_manifest_accepts_declared_drift_without_using_old_identity() -> None:
    evidence = _evidence(drift=True)
    manifest = build_current_identity_manifest(evidence)
    assert manifest["archives"][0]["sha256"] == "a" * 64
    assert manifest["archives"][0]["member_count"] == 2
    assert manifest["archives"][0]["sha256"] != evidence["archives"][0]["manifest_expected"]["sha256"]


def test_current_identity_manifest_rejects_unverified_public_row() -> None:
    evidence = _evidence()
    evidence["archives"][0]["public_byte_verified"] = False
    with pytest.raises(ValueError, match="public_byte_verified must be true"):
        build_current_identity_manifest(evidence)


def test_current_identity_manifest_rejects_receipt_byte_identity_mismatch() -> None:
    evidence = _evidence()
    evidence["archives"][0]["receipt_observed"]["member_count"] = 99
    with pytest.raises(ValueError, match="public bytes disagree with acquisition receipt"):
        build_current_identity_manifest(evidence)


def test_current_identity_manifest_rejects_drift_accounting_mutation() -> None:
    evidence = _evidence(drift=True)
    evidence["drifts"] = []
    with pytest.raises(ValueError, match="drift filename set inconsistent"):
        build_current_identity_manifest(evidence)


def test_current_identity_manifest_rejects_authorization_escalation() -> None:
    evidence = _evidence()
    evidence["semantic_mapping_authorized"] = True
    with pytest.raises(ValueError, match="semantic_mapping_authorized=false"):
        build_current_identity_manifest(evidence)


def test_discovery_output_keeps_current_snapshot_and_authorization_boundaries(monkeypatch) -> None:
    evidence = _evidence(drift=True)

    def fake_discover(archives, *, manifest, manifest_sha256):
        assert list(archives) == [Path("KAP_2025_Y.zip")]
        assert manifest["archives"][0]["sha256"] == "a" * 64
        assert manifest_sha256 is None
        return {
            "contract": "KAP_BULK_TECHNICAL_SCHEMA_DISCOVERY_V1",
            "archive_count": 1,
            "archives": [],
            "report_count": 2,
            "technical_schema_count": 1,
            "technical_schemas": [],
            "archive_manifest_verified": True,
            "source_manifest_sha256": None,
            "semantic_mapping_authorized": False,
            "purpose": "OLD",
        }

    monkeypatch.setattr(current_discovery, "discover_archives", fake_discover)
    result = discover_current_snapshot(
        [Path("KAP_2025_Y.zip")],
        public_evidence=evidence,
        public_evidence_sha256="1" * 64,
    )
    assert result["contract"] == "KAP_BULK_TECHNICAL_SCHEMA_DISCOVERY_CURRENT_SNAPSHOT_V1"
    assert result["archive_manifest_verified"] is False
    assert result["current_snapshot_public_evidence_verified"] is True
    assert result["preserved_manifest_exact_match_count"] == 0
    assert result["preserved_manifest_drift_count"] == 1
    assert result["preserved_manifest_drift_filenames"] == ["KAP_2025_Y.zip"]
    assert result["semantic_mapping_authorized"] is False
    assert result["pit_materialization_authorized"] is False
    assert result["real_60_cutoff_scoring_authorized"] is False


def test_current_snapshot_cli_module_entrypoint_imports_successfully() -> None:
    import subprocess
    import sys

    completed = subprocess.run(
        [sys.executable, "-m", "scripts.discover_kap_bulk_current_snapshot", "--help"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "--public-evidence" in completed.stdout


def test_current_snapshot_workflow_uses_module_entrypoint() -> None:
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github/workflows/v24-kap-current-snapshot-schema-discovery.yml"
    ).read_text(encoding="utf-8")
    assert "python -m scripts.discover_kap_bulk_current_snapshot" in workflow
    assert "python scripts/discover_kap_bulk_current_snapshot.py" not in workflow
