import io
from datetime import datetime
import zipfile

from scripts.audit_borsa_action_bridge import parse_thb_archive


def _archive(action="", bom=False):
    header = "TARIH;ISLEM  KODU;OZSERMAYE HALI\n"
    body = f"2024-01-31;AAA.E;{action}\n2024-01-31;BBB.E;03\n"
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        raw = (header + body).encode("cp1254")
        archive.writestr("thb202401311.csv", (b"\xef\xbb\xbf" if bom else b"") + raw)
    return output.getvalue()


def test_thb_parser_preserves_blank_action_flag_and_source_hashes():
    rows, meta = parse_thb_archive(_archive(), {"AAA"})
    assert rows[0]["ticker"] == "AAA"
    assert rows[0]["action_flag"] == ""
    assert len(meta["archive_sha256"]) == 64
    assert len(meta["member_sha256"]) == 64
    assert datetime.fromisoformat(meta["member_timestamp_local"]).utcoffset().total_seconds() == 10800
    assert meta["member_timestamp_basis"] == "ZIP_MEMBER_WALL_CLOCK_ASSUMED_EUROPE_ISTANBUL"


def test_thb_parser_preserves_nonempty_flag_for_fail_closed_rejection():
    rows, _ = parse_thb_archive(_archive("06"), {"AAA"})
    assert rows[0]["action_flag"] == "06"


def test_thb_parser_accepts_official_utf8_bom_before_date_header():
    rows, _ = parse_thb_archive(_archive(bom=True), {"AAA"})
    assert rows[0]["ticker"] == "AAA"
