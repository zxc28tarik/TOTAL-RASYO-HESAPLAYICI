from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZipFile

import requests

from scripts.reacquire_kap_bulk_archives import (
    download_one,
    evaluate_observation,
    inspect_zip,
    parse_manifest_filename,
    retry_delay_seconds,
    should_retry_status,
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
    observed = {"download_ok": True, "zip_readable": True, **expected}
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


def test_retry_delay_honors_retry_after_and_429_fallback() -> None:
    assert retry_delay_seconds(
        retry_after="17", attempt=1, base_seconds=5, status=429
    ) == 17
    assert retry_delay_seconds(
        retry_after=None, attempt=1, base_seconds=5, status=429
    ) == 60
    now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    assert retry_delay_seconds(
        retry_after="Tue, 01 Sep 2026 12:01:30 GMT",
        attempt=1,
        base_seconds=5,
        status=429,
        now=now,
    ) == 90


def test_retryable_status_contract_is_narrow() -> None:
    assert should_retry_status(429)
    assert should_retry_status(503)
    assert not should_retry_status(404)
    assert not should_retry_status(200)


class _FakeResponse:
    def __init__(
        self,
        status: int,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
        iter_exc: Exception | None = None,
    ) -> None:
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.url = "https://kap.test/download"
        self._iter_exc = iter_exc

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 400

    @property
    def content(self) -> bytes:
        return self._body

    def iter_content(self, chunk_size: int = 1):
        if self._iter_exc:
            raise self._iter_exc
        yield self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeSession:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls = 0

    def get(self, *args, **kwargs):
        self.calls += 1
        return self.responses.pop(0)


def test_download_one_retries_429_then_succeeds(tmp_path: Path) -> None:
    sleeps: list[float] = []
    session = _FakeSession(
        [
            _FakeResponse(429, b"too many", {"Retry-After": "2"}),
            _FakeResponse(200, b"PK\x03\x04payload", {"Content-Type": "application/vnd.zip"}),
        ]
    )
    destination = tmp_path / "A.zip"
    result = download_one(
        session,
        url="https://kap.test",
        destination=destination,
        timeout_seconds=10,
        max_attempts=3,
        retry_base_seconds=5,
        sleeper=sleeps.append,
    )
    assert result["download_ok"] is True
    assert result["attempt_count"] == 2
    assert result["retry_events"] == [
        {
            "attempt": 1,
            "reason": "HTTP_429",
            "delay_seconds": 2.0,
            "retry_after": "2",
        }
    ]
    assert sleeps == [2.0]
    assert destination.read_bytes() == b"PK\x03\x04payload"


def test_download_one_retries_chunked_encoding_and_removes_partial(tmp_path: Path) -> None:
    sleeps: list[float] = []
    session = _FakeSession(
        [
            _FakeResponse(
                200,
                b"ignored",
                iter_exc=requests.exceptions.ChunkedEncodingError("broken"),
            ),
            _FakeResponse(200, b"PK\x03\x04ok"),
        ]
    )
    destination = tmp_path / "A.zip"
    result = download_one(
        session,
        url="https://kap.test",
        destination=destination,
        timeout_seconds=10,
        max_attempts=3,
        retry_base_seconds=5,
        sleeper=sleeps.append,
    )
    assert result["download_ok"] is True
    assert result["attempt_count"] == 2
    assert result["retry_events"][0]["reason"] == "ChunkedEncodingError"
    assert sleeps == [5.0]
    assert not (tmp_path / "A.zip.part").exists()
    assert destination.read_bytes() == b"PK\x03\x04ok"


def test_download_one_exhausts_retryable_errors_without_silent_success(tmp_path: Path) -> None:
    sleeps: list[float] = []
    session = _FakeSession(
        [
            _FakeResponse(503, b"unavailable"),
            _FakeResponse(503, b"still unavailable"),
        ]
    )
    destination = tmp_path / "A.zip"
    result = download_one(
        session,
        url="https://kap.test",
        destination=destination,
        timeout_seconds=10,
        max_attempts=2,
        retry_base_seconds=3,
        sleeper=sleeps.append,
    )
    assert result["download_ok"] is False
    assert result["attempt_count"] == 2
    assert result["http_status"] == 503
    assert result["error"].startswith("HTTP_503:")
    assert sleeps == [3.0]
    assert not destination.exists()
