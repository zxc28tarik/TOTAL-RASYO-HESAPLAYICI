from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
import requests

from src.ingest.api.kap_public_universe import (
    KapPublicUniverseClient,
    KapUniverseError,
    write_universe_snapshot,
)


HTML = """
<html><body><table>
<tr><th>Kod</th><th>Sirket</th></tr>
<tr>
  <td><a href="/tr/sirket-bilgileri/ozet/1016-logo-yazilim">LOGO</a></td>
  <td><a href="/tr/sirket-bilgileri/ozet/1016-logo-yazilim">LOGO YAZILIM SANAYI A.S.</a></td>
</tr>
<tr>
  <td>ATA ATAYM</td>
  <td><a href="/tr/sirket-bilgileri/ozet/9001-ata-yatirim">ATA YATIRIM MENKUL DEGERLER A.S.</a></td>
</tr>
<tr>
  <td><a href="/tr/sirket-bilgileri/ozet/4028e4a2420327a4014209c5092c1448">KTBNK</a></td>
  <td>KUVEYT TURK KATILIM BANKASI A.S.</td>
</tr>
</table></body></html>
"""


class Response:
    def __init__(self, text=HTML, status=200):
        self.text = text
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.responses.pop(0)


def test_parses_official_bist_company_rows_and_multiple_tickers():
    frame = KapPublicUniverseClient.parse_html(HTML)
    assert frame["ticker"].tolist() == ["ATA", "ATAYM", "KTBNK", "LOGO"]
    logo = frame.set_index("ticker").loc["LOGO"]
    assert logo["kap_company_id"] == "1016"
    assert logo["company_name"] == "LOGO YAZILIM SANAYI A.S."
    kt = frame.set_index("ticker").loc["KTBNK"]
    assert kt["kap_company_id"] == "4028e4a2420327a4014209c5092c1448"
    assert bool(kt["is_active"]) is True


def test_duplicate_ticker_conflict_is_rejected():
    html = HTML.replace(
        "</table>",
        '<tr><td>LOGO</td><td><a href="/tr/sirket-bilgileri/ozet/9999-other">BASKA SIRKET</a></td></tr></table>',
    )
    with pytest.raises(KapUniverseError, match="birden fazla sirket"):
        KapPublicUniverseClient.parse_html(html)


def test_fetch_retries_and_rejects_suspiciously_small_universe():
    sleeps = []
    session = Session([Response(status=503), Response()])
    client = KapPublicUniverseClient(
        session=session,
        minimum_rows=4,
        sleeper=sleeps.append,
        clock=lambda: datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
    )
    snapshot = client.fetch()
    assert len(snapshot.frame) == 4
    assert len(snapshot.html_sha256) == 64
    assert sleeps == [0.5]

    client = KapPublicUniverseClient(
        session=Session([Response()]),
        minimum_rows=5,
        clock=lambda: datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
    )
    with pytest.raises(KapUniverseError, match="beklenenden kucuk"):
        client.fetch()


def test_snapshot_write_is_atomic_and_has_reproducibility_metadata(tmp_path):
    client = KapPublicUniverseClient(
        session=Session([Response()]),
        minimum_rows=4,
        clock=lambda: datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
    )
    snapshot = client.fetch()
    csv_path, meta_path = write_universe_snapshot(snapshot, tmp_path / "universe.csv")
    assert csv_path.exists()
    assert meta_path.exists()
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    assert metadata["row_count"] == 4
    assert metadata["fetched_at"] == "2026-08-04T12:00:00+00:00"
    assert len(metadata["tickers_sha256"]) == 64
    assert len(metadata["csv_sha256"]) == 64
    assert "LOGO" in csv_path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"url": "file:///tmp/x"}, "HTTP"),
        ({"timeout_seconds": True}, "timeout_seconds"),
        ({"timeout_seconds": float("inf")}, "timeout_seconds"),
        ({"timeout_seconds": 301}, "guvenli siniri"),
        ({"max_retries": 11}, "guvenli siniri"),
        ({"minimum_rows": 10001}, "guvenli siniri"),
    ],
)
def test_universe_client_resource_and_url_contract_is_strict(kwargs, message):
    with pytest.raises(ValueError, match=message):
        KapPublicUniverseClient(**kwargs)


def test_snapshot_writer_rejects_duplicate_or_incomplete_universe(tmp_path):
    client = KapPublicUniverseClient(
        session=Session([Response()]),
        minimum_rows=4,
        clock=lambda: datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
    )
    snapshot = client.fetch()
    duplicate = snapshot.frame.copy()
    duplicate.loc[1, "ticker"] = duplicate.loc[0, "ticker"]
    from src.ingest.api.kap_public_universe import KapUniverseSnapshot
    bad = KapUniverseSnapshot(
        frame=duplicate,
        fetched_at=snapshot.fetched_at,
        source_url=snapshot.source_url,
        html_sha256=snapshot.html_sha256,
    )
    with pytest.raises(ValueError, match="benzersiz"):
        write_universe_snapshot(bad, tmp_path / "bad.csv")

    missing = snapshot.frame.drop(columns=["company_name"])
    bad = KapUniverseSnapshot(
        frame=missing,
        fetched_at=snapshot.fetched_at,
        source_url=snapshot.source_url,
        html_sha256=snapshot.html_sha256,
    )
    with pytest.raises(ValueError, match="eksik kolonlar"):
        write_universe_snapshot(bad, tmp_path / "missing.csv")


def test_snapshot_writer_rejects_naive_timestamp_and_bad_html_hash(tmp_path):
    from dataclasses import replace
    client = KapPublicUniverseClient(
        session=Session([Response()]),
        minimum_rows=4,
        clock=lambda: datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc),
    )
    snapshot = client.fetch()
    with pytest.raises(ValueError, match="timezone"):
        write_universe_snapshot(replace(snapshot, fetched_at=datetime(2026, 8, 4, 12, 0)), tmp_path / "x.csv")
    with pytest.raises(ValueError, match="sha256"):
        write_universe_snapshot(replace(snapshot, html_sha256="bad"), tmp_path / "x.csv")
