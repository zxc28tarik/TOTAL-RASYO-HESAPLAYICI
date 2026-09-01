from pathlib import Path
from zipfile import ZipFile

from scripts.reacquire_kap_bulk_archives import (
    evaluate_observation,
    inspect_zip,
    parse_manifest_filename,
)


def test_parse_manifest_filename_maps_all_periods() -> None:
    codes = {"3A": 1, "6A": 2, "9A": 3, "Y": 4}
    assert parse_manifest_filename("KAP_2021_3A.zip", codes) == (2021, "3A", 1)
    assert parse_manifest_filename("KAP_2021_6A.zip", codes) == (2021, "6A", 2)
    assert parse_manifest_filename("KAP_2021_9A.zip", codes) == (2021, "9A", 3)
    assert parse_manifest_filename("KAP_2021_Y.zip", codes) == (2021, "Y", 4)


def test_inspect_zip_counts_xls_and_total_uncompressed_bytes(tmp_path: Path) -> None:
    archive = tmp_path / "KAP_2021_3A.zip"
    with ZipFile(archive, "w") as bundle:
        bundle.writestr("A.xls", b"abc")
        bundle.writestr("B.xls", b"12345")
        bundle.writestr("metadata.txt", b"zz")
    observed = inspect_zip(archive)
    assert observed["zip_readable"] is True
    assert observed["member_count"] == 2
    assert observed["uncompressed_bytes"] == 10


def test_evaluate_observation_requires_all_four_identity_dimensions() -> None:
    expected = {
        "sha256": "a" * 64,
        "size_bytes": 100,
        "member_count": 2,
        "uncompressed_bytes": 500,
    }
    observed = {
        "download_ok": True,
        "zip_readable": True,
        **expected,
    }
    exact, reasons = evaluate_observation(expected, observed)
    assert exact is True
    assert reasons == []

    mutated = dict(observed)
    mutated["member_count"] = 3
    exact, reasons = evaluate_observation(expected, mutated)
    assert exact is False
    assert reasons == ["MEMBER_COUNT_MISMATCH"]


def test_evaluate_observation_rejects_failed_or_non_zip_download() -> None:
    expected = {
        "sha256": "a" * 64,
        "size_bytes": 100,
        "member_count": 2,
        "uncompressed_bytes": 500,
    }
    observed = {
        "download_ok": False,
        "zip_readable": False,
        "sha256": None,
        "size_bytes": None,
        "member_count": None,
        "uncompressed_bytes": None,
    }
    exact, reasons = evaluate_observation(expected, observed)
    assert exact is False
    assert "DOWNLOAD_NOT_OK" in reasons
    assert "ZIP_NOT_READABLE" in reasons
    assert "SHA256_MISMATCH" in reasons
