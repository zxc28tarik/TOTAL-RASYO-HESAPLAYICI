import pytest

from scripts.probe_kap_bulk_endpoint_contract import _archive_kind, build_url


def test_build_url_uses_public_client_year_period_shape() -> None:
    template = "{base_url}/{lang}/api/financialTable/download/{year}/{period_code}"
    assert build_url(
        template,
        base_url="https://kap.org.tr/",
        lang="tr",
        year=2021,
        period_code=1,
    ) == "https://kap.org.tr/tr/api/financialTable/download/2021/1"


def test_build_url_rejects_unknown_period_code() -> None:
    with pytest.raises(ValueError, match="period_code"):
        build_url(
            "{base_url}/{lang}/{year}/{period_code}",
            base_url="https://kap.org.tr",
            lang="tr",
            year=2021,
            period_code=5,
        )


def test_archive_kind_accepts_zip_and_rar_and_rejects_html() -> None:
    assert _archive_kind(b"PK\x03\x04rest") == "ZIP"
    assert _archive_kind(b"Rar!rest") == "RAR"
    assert _archive_kind(b"<html>") is None
