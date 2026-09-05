import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

from scripts.build_kap_bulk_public_evidence import (
    build_public_byte_evidence,
    compare_identity,
    write_sha256s,
)


def _make_zip(path: Path, payload: bytes = b"abc") -> None:
    with ZipFile(path, "w") as bundle:
        bundle.writestr("A.xls", payload)
        bundle.writestr("meta.txt", b"z")


def _identity(archive: Path) -> dict[str, object]:
    with ZipFile(archive) as bundle:
        infos = [info for info in bundle.infolist() if not info.is_dir()]
        member_count = sum(1 for info in infos if info.filename.endswith(".xls"))
        uncompressed_bytes = sum(info.file_size for info in infos)
    return {
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "size_bytes": archive.stat().st_size,
        "member_count": member_count,
        "uncompressed_bytes": uncompressed_bytes,
    }


def _manifest(tmp_path: Path, archive: Path) -> Path:
    identity = _identity(archive)
    payload = {
        "contract": "KAP_BULK_FINANCIAL_EXPORT_ARCHIVES_V1",
        "captured_at": "2026-08-31T00:00:00+00:00",
        "archive_count": 1,
        "archives": [{"filename": archive.name, **identity}],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _receipt(tmp_path: Path, archive: Path) -> Path:
    identity = _identity(archive)
    payload = {
        "contract": "KAP_BULK_ARCHIVE_REACQUISITION_V2",
        "captured_at": "2026-09-01T23:00:00+00:00",
        "archive_count": 1,
        "exact_manifest_match_count": 1,
        "mismatch_count": 0,
        "all_exact_manifest_match": True,
        "archives": [{
            "filename": archive.name,
            "expected": {"filename": archive.name, **identity},
            "observed": {
                **identity,
                "zip_readable": True,
                "download_ok": True,
                "http_status": 200,
                "content_type": "application/vnd.zip",
                "final_url": "https://kap.org.tr/tr/api/financialTable/download/2021/1",
            },
        }],
    }
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _build(manifest: Path, receipt: Path, raw: Path) -> dict[str, object]:
    return build_public_byte_evidence(
        manifest_path=manifest,
        acquisition_receipt_path=receipt,
        raw_dir=raw,
        workflow_run_id=123,
        acquisition_head_sha="a" * 40,
    )


def test_exact_old_snapshot_is_publishable(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    archive = raw / "KAP_2021_3A.zip"
    _make_zip(archive)
    manifest = _manifest(tmp_path, archive)
    receipt = _receipt(tmp_path, archive)

    evidence = _build(manifest, receipt, raw)
    assert evidence["snapshot_complete"] is True
    assert evidence["public_release_authorized"] is True
    assert evidence["all_exact_manifest_match"] is True
    assert evidence["manifest_drift_count"] == 0
    assert evidence["receipt_byte_match_count"] == 1
    assert evidence["semantic_mapping_authorized"] is False
    assert evidence["pit_materialization_authorized"] is False
    assert evidence["real_60_cutoff_scoring_authorized"] is False


def test_official_current_drift_is_publishable_but_not_manifest_exact(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    old = tmp_path / "KAP_2021_3A.zip"
    _make_zip(old, b"old")
    manifest = _manifest(tmp_path, old)

    current = raw / "KAP_2021_3A.zip"
    _make_zip(current, b"current")
    receipt = _receipt(tmp_path, current)
    receipt_payload = json.loads(receipt.read_text())
    old_identity = _identity(old)
    receipt_payload["archives"][0]["expected"] = {"filename": current.name, **old_identity}
    receipt_payload["exact_manifest_match_count"] = 0
    receipt_payload["mismatch_count"] = 1
    receipt_payload["all_exact_manifest_match"] = False
    receipt.write_text(json.dumps(receipt_payload))

    evidence = _build(manifest, receipt, raw)
    assert evidence["snapshot_complete"] is True
    assert evidence["public_release_authorized"] is True
    assert evidence["all_exact_manifest_match"] is False
    assert evidence["manifest_exact_match_count"] == 0
    assert evidence["manifest_drift_count"] == 1
    assert evidence["receipt_byte_match_count"] == 1
    assert evidence["drifts"][0]["filename"] == "KAP_2021_3A.zip"
    assert "SHA256_MISMATCH" in evidence["drifts"][0]["manifest_mismatch_reasons"]


def test_public_raw_mutation_after_acquisition_blocks_release(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    archive = raw / "KAP_2021_3A.zip"
    _make_zip(archive)
    manifest = _manifest(tmp_path, archive)
    receipt = _receipt(tmp_path, archive)
    _make_zip(archive, b"changed-after-receipt")

    evidence = _build(manifest, receipt, raw)
    assert evidence["snapshot_complete"] is False
    assert evidence["public_release_authorized"] is False
    assert evidence["receipt_byte_match_count"] == 0
    assert "SHA256_MISMATCH" in evidence["archives"][0]["receipt_mismatch_reasons"]


def test_non_official_receipt_blocks_release(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    archive = raw / "KAP_2021_3A.zip"
    _make_zip(archive)
    manifest = _manifest(tmp_path, archive)
    receipt = _receipt(tmp_path, archive)
    payload = json.loads(receipt.read_text())
    payload["archives"][0]["observed"]["final_url"] = "https://example.com/not-kap.zip"
    receipt.write_text(json.dumps(payload))

    evidence = _build(manifest, receipt, raw)
    assert evidence["snapshot_complete"] is False
    assert evidence["public_release_authorized"] is False
    assert evidence["public_byte_verified_count"] == 0


def test_missing_and_unexpected_files_block_release(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    source = tmp_path / "KAP_2021_3A.zip"
    _make_zip(source)
    manifest = _manifest(tmp_path, source)
    receipt = _receipt(tmp_path, source)
    _make_zip(raw / "KAP_2099_Y.zip")

    evidence = _build(manifest, receipt, raw)
    assert evidence["snapshot_complete"] is False
    assert evidence["missing_archives"] == ["KAP_2021_3A.zip"]
    assert evidence["unexpected_archives"] == ["KAP_2099_Y.zip"]


def test_receipt_manifest_baseline_mutation_blocks_release(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    archive = raw / "KAP_2021_3A.zip"
    _make_zip(archive)
    manifest = _manifest(tmp_path, archive)
    receipt = _receipt(tmp_path, archive)
    payload = json.loads(receipt.read_text())
    payload["archives"][0]["expected"]["member_count"] = 999
    receipt.write_text(json.dumps(payload))

    evidence = _build(manifest, receipt, raw)
    assert evidence["snapshot_complete"] is False
    assert evidence["receipt_manifest_baseline_match_count"] == 0


def test_compare_identity_checks_all_four_dimensions() -> None:
    expected = {
        "sha256": "d" * 64,
        "size_bytes": 10,
        "member_count": 2,
        "uncompressed_bytes": 30,
    }
    observed = {"zip_readable": True, **expected}
    exact, reasons = compare_identity(expected, observed)
    assert exact is True
    assert reasons == []

    for field in ("sha256", "size_bytes", "member_count", "uncompressed_bytes"):
        mutated = dict(observed)
        mutated[field] = -1 if field != "sha256" else "e" * 64
        exact, reasons = compare_identity(expected, mutated)
        assert exact is False
        assert f"{field.upper()}_MISMATCH" in reasons


def test_write_sha256s_uses_public_observed_bytes_only(tmp_path: Path) -> None:
    out = tmp_path / "SHA256SUMS.observed"
    evidence = {
        "archives": [{
            "filename": "KAP_2021_3A.zip",
            "public_observed": {"sha256": "f" * 64},
        }]
    }
    write_sha256s(evidence, out)
    assert out.read_text(encoding="utf-8") == f"{'f' * 64}  KAP_2021_3A.zip\n"
