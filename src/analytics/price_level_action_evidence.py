"""Verified, dated event inventory for price-level share normalization.

The caller supplies an independently approved manifest hash and original source
bytes. Hash verification establishes identity, not the truth of a completeness
assertion: the source collector/reviewer must substantiate the inventory scope.
Yahoo action rows alone are not an inventory-completeness certificate.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
import json
import math
from typing import Mapping

from src.analytics.historical_backtest_corporate_action_events import (
    ACTION_CASH_DIVIDEND, ACTION_SPLIT, HistoricalCorporateAction,
    validate_corporate_action_events,
)

CONTRACT = "PRICE_LEVEL_ACTION_COVERAGE_V1"
SOURCE_SHARE_BASIS = "DATED_UNADJUSTED_SHARES_V1"


class ActionEvidenceError(ValueError):
    pass


def _instant(value: object) -> datetime:
    try:
        result = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise ActionEvidenceError("invalid publication timestamp") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ActionEvidenceError("publication timestamp must be timezone aware")
    return result


def _day(value: object) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ActionEvidenceError("invalid coverage date") from exc


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ActionEvidenceError("duplicate manifest key")
        result[key] = value
    return result


@dataclass(frozen=True)
class PriceLevelActionEvidence:
    manifest_bytes: bytes
    expected_sha256: str
    source_bytes: Mapping[str, bytes]

    def verify(
        self, *, ticker: str, shares_basis_date: date, price_trade_date: date,
        cutoff: datetime, events: tuple[HistoricalCorporateAction, ...], shares_out: float,
    ) -> str:
        """Verify the exact requested interval, event set and publication boundary."""
        cutoff = _instant(cutoff)
        digest = sha256(self.manifest_bytes).hexdigest()
        if digest != self.expected_sha256:
            raise ActionEvidenceError("coverage manifest SHA256 mismatch")
        try:
            payload = json.loads(self.manifest_bytes, object_pairs_hook=_object)
        except (ValueError, UnicodeError) as exc:
            raise ActionEvidenceError("invalid coverage manifest") from exc
        if not isinstance(payload, dict) or payload.get("contract") != CONTRACT:
            raise ActionEvidenceError("unsupported coverage contract")
        if payload.get("ticker") != ticker:
            raise ActionEvidenceError("coverage ticker mismatch")
        if payload.get("source_share_basis") != SOURCE_SHARE_BASIS:
            raise ActionEvidenceError("source share basis mismatch")
        source_shares = payload.get("source_shares_out")
        if (isinstance(source_shares, bool) or not isinstance(source_shares, (int, float))
                or not math.isfinite(source_shares) or source_shares <= 0 or source_shares != shares_out):
            raise ActionEvidenceError("source share count mismatch")
        if _day(payload.get("shares_basis_date")) != shares_basis_date:
            raise ActionEvidenceError("source share date mismatch")
        if _day(payload.get("complete_through")) != price_trade_date:
            raise ActionEvidenceError("coverage end must equal price date")
        if shares_basis_date > price_trade_date:
            raise ActionEvidenceError("coverage interval reversed")
        if payload.get("enumeration_complete") is not True:
            raise ActionEvidenceError("event enumeration incomplete")
        sources = payload.get("sources")
        if not isinstance(sources, list) or not sources:
            raise ActionEvidenceError("completeness source evidence missing")
        verified = {}
        for source in sources:
            if not isinstance(source, dict):
                raise ActionEvidenceError("invalid source record")
            ref = source.get("source_ref")
            if not isinstance(ref, str) or not ref.strip() or ref in verified:
                raise ActionEvidenceError("missing or duplicate source reference")
            raw = self.source_bytes.get(ref)
            if not isinstance(raw, bytes) or not raw:
                raise ActionEvidenceError("source bytes missing")
            if sha256(raw).hexdigest() != source.get("source_sha256"):
                raise ActionEvidenceError("source SHA256 mismatch")
            published = _instant(source.get("published_at"))
            if published > cutoff:
                raise ActionEvidenceError("future source publication")
            verified[ref] = source
        if payload.get("completeness_source_ref") not in verified:
            raise ActionEvidenceError("completeness source evidence missing")
        if payload.get("share_source_ref") not in verified:
            raise ActionEvidenceError("share source evidence missing")
        rows = payload.get("events")
        if not isinstance(rows, list):
            raise ActionEvidenceError("event inventory missing")
        inventory = {}
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("action_id"), str):
                raise ActionEvidenceError("invalid event inventory")
            if row["action_id"] in inventory:
                raise ActionEvidenceError("duplicate inventory event")
            inventory[row["action_id"]] = row
        checked = validate_corporate_action_events(events)
        if set(inventory) != {event.action_id for event in checked}:
            raise ActionEvidenceError("event inventory mismatch")
        for event in checked:
            # The legacy event dataclass is publicly constructible; rebuild its
            # canonical identity instead of trusting action_id or field values.
            rebuilt = HistoricalCorporateAction.build(
                ticker=event.ticker, action_type=event.action_type,
                ex_date=event.ex_date, payment_date=event.payment_date,
                share_multiplier=event.share_multiplier, cash_per_share=event.cash_per_share,
                currency=event.currency, source_ref=event.source_ref,
                source_sha256=event.source_sha256,
            )
            if rebuilt != event:
                raise ActionEvidenceError("event identity mismatch")
            if event.ticker != ticker:
                raise ActionEvidenceError("event ticker mismatch")
            if not shares_basis_date < event.ex_date <= price_trade_date:
                raise ActionEvidenceError("event outside exact coverage interval")
            source = verified.get(event.source_ref)
            if source is None or source["source_sha256"] != event.source_sha256:
                raise ActionEvidenceError("event source identity mismatch")
            row = inventory[event.action_id]
            kind = row.get("economic_kind")
            if event.action_type == ACTION_CASH_DIVIDEND:
                if kind != "CASH_DIVIDEND":
                    raise ActionEvidenceError("dividend economic kind mismatch")
            elif event.action_type == ACTION_SPLIT:
                if kind not in {"SPLIT", "BONUS", "REVERSE_SPLIT"}:
                    raise ActionEvidenceError("unsupported capital action: cash economics unresolved")
                if (kind == "REVERSE_SPLIT") != (event.share_multiplier < 1):
                    raise ActionEvidenceError("share multiplier direction mismatch")
            else:
                raise ActionEvidenceError("unsupported action type")
        return digest
