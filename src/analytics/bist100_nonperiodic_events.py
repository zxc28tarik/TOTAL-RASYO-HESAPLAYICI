from __future__ import annotations

"""Strict loader for audited non-periodic BIST100 constituent replacements.

This module does not discover KAP disclosures or infer replacements.  It accepts
only a canonical event file built from already-audited Borsa Istanbul KAP
evidence and converts it into the fail-closed membership event contract.
"""

import json
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

from src.analytics.bist100_membership_history import (
    Bist100ConstituentEvent,
    Bist100HistoryError,
)


class Bist100NonperiodicSourceError(Bist100HistoryError):
    pass


_EXPECTED_PUBLISHER = "KAP / Borsa Istanbul A.S."
_EVENT_TYPE = "NONPERIODIC_CONSTITUENT_CHANGE"


def _canonical_kap_url(value: object, disclosure_index: int) -> str:
    url = str(value or "").strip()
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc.lower() != "www.kap.org.tr":
        raise Bist100NonperiodicSourceError("nonperiodic source_url resmi www.kap.org.tr HTTPS URL olmali")
    expected = f"/tr/Bildirim/{disclosure_index}"
    if parsed.path != expected or parsed.query or parsed.fragment:
        raise Bist100NonperiodicSourceError(
            f"nonperiodic source_url disclosureIndex ile eslesmiyor: {disclosure_index}"
        )
    return url


def _ticker_tuple(values: object, field: str) -> tuple[str, ...]:
    if not isinstance(values, list):
        raise Bist100NonperiodicSourceError(f"{field} liste olmali")
    out: list[str] = []
    for raw in values:
        ticker = str(raw or "").strip().upper()
        if not ticker or ticker in {"NONE", "NAN", "<NA>"}:
            raise Bist100NonperiodicSourceError(f"{field} bos/gecersiz ticker iceriyor")
        out.append(ticker)
    if len(out) != len(set(out)):
        raise Bist100NonperiodicSourceError(f"{field} duplicate ticker iceriyor")
    return tuple(sorted(out))


def load_bist100_nonperiodic_events(path: str | Path) -> tuple[Bist100ConstituentEvent, ...]:
    source_path = Path(path)
    try:
        payload = json.loads(source_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise Bist100NonperiodicSourceError("nonperiodic event JSON okunamadi") from exc
    if not isinstance(payload, dict):
        raise Bist100NonperiodicSourceError("nonperiodic event JSON object olmali")
    if payload.get("publisher") != _EXPECTED_PUBLISHER:
        raise Bist100NonperiodicSourceError("nonperiodic publisher beklenen KAP/Borsa Istanbul degil")
    rows = payload.get("events")
    if not isinstance(rows, list):
        raise Bist100NonperiodicSourceError("nonperiodic events liste olmali")
    declared = payload.get("event_count")
    if type(declared) is not int or declared != len(rows):
        raise Bist100NonperiodicSourceError("nonperiodic event_count events uzunluguyla eslesmiyor")

    out: list[Bist100ConstituentEvent] = []
    seen_indices: set[int] = set()
    ticker_date_sides: set[tuple[str, str, str]] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise Bist100NonperiodicSourceError("nonperiodic event row object olmali")
        idx = row.get("disclosure_index")
        if type(idx) is not int or idx <= 0:
            raise Bist100NonperiodicSourceError("disclosure_index pozitif integer olmali")
        if idx in seen_indices:
            raise Bist100NonperiodicSourceError(f"duplicate disclosure_index: {idx}")
        seen_indices.add(idx)
        source_url = _canonical_kap_url(row.get("source_url"), idx)
        sha = str(row.get("source_detail_sha256") or "").strip().lower()
        if len(sha) != 64 or any(ch not in "0123456789abcdef" for ch in sha):
            raise Bist100NonperiodicSourceError("source_detail_sha256 64-hex olmali")
        if row.get("event_type") != _EVENT_TYPE:
            raise Bist100NonperiodicSourceError("nonperiodic event_type sozlesmeye uymuyor")
        included = _ticker_tuple(row.get("included"), "included")
        excluded = _ticker_tuple(row.get("excluded"), "excluded")
        if not included or not excluded:
            raise Bist100NonperiodicSourceError("nonperiodic replacement iki tarafta da ticker icermeli")
        if len(included) != len(excluded):
            raise Bist100NonperiodicSourceError("nonperiodic replacement uye sayisini korumali")
        if set(included) & set(excluded):
            raise Bist100NonperiodicSourceError("ticker ayni nonperiodic eventte hem included hem excluded olamaz")

        event = Bist100ConstituentEvent.build(
            effective_date=row.get("effective_date"),
            included=included,
            excluded=excluded,
            source_id=source_url,
            source_sha256=sha,
            event_type=_EVENT_TYPE,
        )
        day = event.effective_date.isoformat()
        for side, tickers in (("IN", included), ("OUT", excluded)):
            for ticker in tickers:
                key=(day, ticker, side)
                if key in ticker_date_sides:
                    raise Bist100NonperiodicSourceError(
                        f"ayni tarihte duplicate nonperiodic ticker-side: {day} {ticker} {side}"
                    )
                ticker_date_sides.add(key)
        out.append(event)

    return tuple(sorted(out, key=lambda x: (x.effective_date, x.source_id)))
