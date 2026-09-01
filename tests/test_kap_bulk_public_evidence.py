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


def _manifest(tmp_path: Path, archive: Path) -> Path:
    import hashlib
    import json
    from zipfile import ZipFile

    with ZipFile(archive) as bundle:
        infos = [info for info in bundle.infolist() if not info.is_dir()]
        member_count = sum(1 for info in infos if info.filename.endswith(".xls"))
        uncompressed_bytes = sum(info.file_size for info in infos)
    sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    payload = {
        "contract": "KAP_BULK_FINANCIAL_EXPORT_ARCHIVES_V1",
        "captured_at": "2026-08-31T00:00:00+00:00",
        "archive_count": 1,
        "archives": [
            {
                "filename": archive.name,
                "sha256": sha,
                "size_bytes": archive.stat().st_size,
                "member_count": member_count,
                "uncompressed_bytes": uncompressed_bytes,
            }
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_build_public_byte_evidence_exact_match(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    archive = raw / "KAP_2021_3A.zip"
    _make_zip(archive)
    manifest = _manifest(tmp_path, archive)

    evidence = build_public_byte_evidence(
        manifest_path=manifest,
        raw_dir=raw,
        workflow_run_id=123,
        acquisition_head_sha="a" * 40,
    )
    assert evidence["all_exact_manifest_match"] is True
    assert evidence["public_release_authorized"] is True
    assert evidence["semantic_mapping_authorized"] is False
    assert evidence["real_60_cutoff_scoring_authorized"] is False
    assert evidence["exact_manifest_match_count"] == 1


def test_build_public_byte_evidence_rejects_mutated_archive(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    archive = raw / "KAP_2021_3A.zip"
    _make_zip(archive)
    manifest = _manifest(tmp_path, archive)
    _make_zip(archive, payload=b"mutated")

    evidence = build_public_byte_evidence(
        manifest_path=manifest,
        raw_dir=raw,
        workflow_run_id=123,
        acquisition_head_sha="b" * 40,
    )
    assert evidence["all_exact_manifest_match"] is False
    assert evidence["public_release_authorized"] is False
    assert "SHA256_MISMATCH" in evidence["archives"][0]["mismatch_reasons"]


def test_build_public_byte_evidence_rejects_missing_and_unexpected_files(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    source = tmp_path / "KAP_2021_3A.zip"
    _make_zip(source)
    manifest = _manifest(tmp_path, source)
    _make_zip(raw / "KAP_2099_Y.zip")

    evidence = build_public_byte_evidence(
        manifest_path=manifest,
        raw_dir=raw,
        workflow_run_id=123,
        acquisition_head_sha="c" * 40,
    )
    assert evidence["all_exact_manifest_match"] is False
    assert evidence["missing_archives"] == ["KAP_2021_3A.zip"]
    assert evidence["unexpected_archives"] == ["KAP_2099_Y.zip"]


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


def test_write_sha256s_uses_observed_bytes_only(tmp_path: Path) -> None:
    out = tmp_path / "SHA256SUMS.observed"
    evidence = {
        "archives": [
            {
                "filename": "KAP_2021_3A.zip",
                "observed": {"sha256": "f" * 64},
            }
        ]
    }
    write_sha256s(evidence, out)
    assert out.read_text(encoding="utf-8") == f"{'f' * 64}  KAP_2021_3A.zip\n"
