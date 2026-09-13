from io import BytesIO

import pytest

from scripts.discover_kap_bulk_role_namespaces_targeted import _stream_role_markers


def test_stream_role_markers_finds_multiple_namespaces_without_partial_roles() -> None:
    raw = b'<x id="banks_role_210011"></x><x id="par-banks_role_610005"></x><x id="foo_role_123"></x>'
    assert _stream_role_markers(BytesIO(raw), chunk_size=7) == {
        ("banks", "banks_role_210011"),
        ("par-banks", "par-banks_role_610005"),
        ("foo", "foo_role_123"),
    }


def test_stream_role_markers_is_case_insensitive_and_chunk_safe() -> None:
    raw = b'<x id="INSURANCE_ROLE_310001"></x>'
    assert _stream_role_markers(BytesIO(raw), chunk_size=5) == {
        ("insurance", "insurance_role_310001")
    }


def test_stream_role_markers_accepts_marker_ending_at_eof() -> None:
    assert _stream_role_markers(BytesIO(b'"insurance_role_310001'), chunk_size=5) == {
        ("insurance", "insurance_role_310001")
    }


def test_stream_role_markers_respects_markup_delimiter_before_namespace() -> None:
    raw = b'prefix"insurance_role_310001" suffix'
    assert _stream_role_markers(BytesIO(raw), chunk_size=4) == {
        ("insurance", "insurance_role_310001")
    }


def test_stream_role_markers_rejects_invalid_chunk_size() -> None:
    with pytest.raises(ValueError, match="chunk_size"):
        _stream_role_markers(BytesIO(b"banks_role_1"), chunk_size=0)
    with pytest.raises(ValueError, match="chunk_size"):
        _stream_role_markers(BytesIO(b"banks_role_1"), chunk_size=True)


def test_stream_role_markers_ignores_non_role_text() -> None:
    assert _stream_role_markers(BytesIO(b"insurance valuation bank role without taxonomy id"), chunk_size=4) == set()
