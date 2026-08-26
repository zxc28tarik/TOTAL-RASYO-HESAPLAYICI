from __future__ import annotations

import json

import pytest

from src.analytics.bist100_nonperiodic_events import (
    Bist100NonperiodicSourceError,
    load_bist100_nonperiodic_events,
)


SHA = "a" * 64


def _payload(**overrides):
    event = {
        "disclosure_index": 1618229,
        "effective_date": "2026-06-18",
        "source_url": "https://www.kap.org.tr/tr/Bildirim/1618229",
        "source_detail_sha256": SHA,
        "event_type": "NONPERIODIC_CONSTITUENT_CHANGE",
        "included": ["BERA"],
        "excluded": ["KONTR"],
    }
    event.update(overrides.pop("event", {}))
    payload = {
        "publisher": "KAP / Borsa Istanbul A.S.",
        "event_count": 1,
        "events": [event],
    }
    payload.update(overrides)
    return payload


def _write(tmp_path, payload):
    path = tmp_path / "events.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_loads_audited_kap_replacement(tmp_path):
    events = load_bist100_nonperiodic_events(_write(tmp_path, _payload()))
    assert len(events) == 1
    event = events[0]
    assert event.effective_date.isoformat() == "2026-06-18"
    assert event.included == ("BERA",)
    assert event.excluded == ("KONTR",)
    assert event.source_id == "https://www.kap.org.tr/tr/Bildirim/1618229"
    assert event.source_sha256 == SHA
    assert event.event_type == "NONPERIODIC_CONSTITUENT_CHANGE"


@pytest.mark.parametrize(
    "payload",
    [
        _payload(publisher="OTHER"),
        _payload(event_count=2),
        _payload(event={"source_url": "http://www.kap.org.tr/tr/Bildirim/1618229"}),
        _payload(event={"source_url": "https://kap.org.tr/tr/Bildirim/1618229"}),
        _payload(event={"source_url": "https://www.kap.org.tr/tr/Bildirim/999"}),
        _payload(event={"source_detail_sha256": "bad"}),
        _payload(event={"event_type": "PERIODIC_CONSTITUENT_CHANGE"}),
        _payload(event={"included": []}),
        _payload(event={"included": ["BERA", "AKSEN"], "excluded": ["KONTR"]}),
        _payload(event={"included": ["KONTR"], "excluded": ["KONTR"]}),
        _payload(event={"included": ["BERA", "BERA"]}),
    ],
)
def test_rejects_invalid_source_or_replacement_contract(tmp_path, payload):
    with pytest.raises(Bist100NonperiodicSourceError):
        load_bist100_nonperiodic_events(_write(tmp_path, payload))


def test_rejects_duplicate_disclosure_index(tmp_path):
    p = _payload(event_count=2)
    p["events"] = [p["events"][0], dict(p["events"][0])]
    with pytest.raises(Bist100NonperiodicSourceError, match="duplicate disclosure_index"):
        load_bist100_nonperiodic_events(_write(tmp_path, p))
