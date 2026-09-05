from dataclasses import asdict, replace
from datetime import date, datetime
from hashlib import sha256
import json

import pytest

from src.analytics.historical_backtest_corporate_action_events import HistoricalCorporateAction
from src.analytics.price_level_action_evidence import (
    CONTRACT, SOURCE_SHARE_BASIS, ActionEvidenceError, PriceLevelActionEvidence,
)
from src.analytics.price_level_valuation_basis import (
    PriceLevelValuationBasisError, build_price_level_observation, materialize_price_level_market_cap,
)

BASIS = date(2026, 6, 30)
PRICE_DATE = date(2026, 8, 3)
CUTOFF = datetime.fromisoformat("2026-08-03T18:10:00+03:00")
# Synthetic bytes are test-only and establish no production source claim.
SOURCE = b"synthetic test-only action inventory"
SHA = sha256(SOURCE).hexdigest()


def action(kind="SPLIT", *, ticker="AAA", ex_date=date(2026, 7, 15)):
    fields = ({"payment_date": ex_date, "cash_per_share": 10, "currency": "TRY"}
              if kind == "CASH_DIVIDEND" else {"share_multiplier": 0.5 if kind == "REVERSE_SPLIT" else 2})
    return HistoricalCorporateAction.build(
        ticker=ticker, action_type="CASH_DIVIDEND" if kind == "CASH_DIVIDEND" else "SHARE_MULTIPLIER",
        ex_date=ex_date, source_ref="test:inventory", source_sha256=SHA, **fields)


def payload(events=(), *, kind="SPLIT"):
    return {
        "contract": CONTRACT, "ticker": "AAA", "source_share_basis": SOURCE_SHARE_BASIS,
        "source_shares_out": 10_000_000, "share_source_ref": "test:inventory",
        "shares_basis_date": BASIS.isoformat(), "complete_through": PRICE_DATE.isoformat(),
        "enumeration_complete": True, "completeness_source_ref": "test:inventory",
        "sources": [{"source_ref": "test:inventory", "source_sha256": SHA,
                     "published_at": "2026-08-03T18:10:00+03:00"}],
        "events": [{"action_id": event.action_id, "economic_kind": kind} for event in events]}


def evidence(data):
    raw = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    return PriceLevelActionEvidence(raw, sha256(raw).hexdigest(), {"test:inventory": SOURCE})


def materialize(events=(), *, data=None, proof=None, price=None, **overrides):
    args = dict(
        price=price or build_price_level_observation(ticker="AAA", trade_date=PRICE_DATE, close=100, adjusted_close=90),
        shares_out=10_000_000, shares_basis_date=BASIS, corporate_actions=events,
        events_complete_through=PRICE_DATE, evidence=proof or evidence(data or payload(events)), cutoff=CUTOFF)
    args.update(overrides)
    return materialize_price_level_market_cap(**args)


@pytest.mark.parametrize("kind,shares", [("CASH_DIVIDEND", 10_000_000), ("SPLIT", 20_000_000),
                                        ("BONUS", 20_000_000), ("REVERSE_SPLIT", 5_000_000)])
def test_economics(kind, shares):
    events = (action(kind),)
    result = materialize(events, data=payload(events, kind=kind))
    assert result.raw_close == 100
    assert result.normalized_shares_out == shares
    assert result.market_cap == 100 * shares
    assert result.applied_share_action_ids == (() if kind == "CASH_DIVIDEND" else (events[0].action_id,))
    assert result.action_evidence_sha256 == evidence(payload(events, kind=kind)).expected_sha256


def test_date_only_completeness_is_not_proof():
    with pytest.raises(PriceLevelValuationBasisError, match="evidence required"):
        materialize(evidence=None)


@pytest.mark.parametrize("key,value,match", [
    ("ticker", "BBB", "ticker mismatch"),
    ("source_share_basis", "ADJUSTED_PRICE_SERIES_V1", "share basis"),
    ("source_shares_out", 11_000_000, "share count"),
    ("share_source_ref", "missing", "share source"),
    ("shares_basis_date", "2026-06-29", "share date"),
    ("complete_through", "2026-08-02", "coverage end"),
    ("complete_through", "2026-08-04", "coverage end"),
    ("enumeration_complete", False, "incomplete"),
    ("enumeration_complete", "true", "incomplete"),
    ("sources", [], "source evidence missing"),
    ("completeness_source_ref", "unknown", "source evidence missing"),
    ("contract", "unknown", "unsupported coverage")])
def test_bad_coverage_fails_closed(key, value, match):
    data = payload()
    data[key] = value
    with pytest.raises(ActionEvidenceError, match=match):
        materialize(data=data)


@pytest.mark.parametrize("published", ["2026-08-03T18:10:01+03:00", "2026-08-03T18:10:00"])
def test_future_or_naive_source_rejected(published):
    data = payload()
    data["sources"][0]["published_at"] = published
    with pytest.raises(ActionEvidenceError, match="future|timezone"):
        materialize(data=data)


@pytest.mark.parametrize("event", [action(ticker="BBB"), action(ex_date=BASIS), action(ex_date=date(2026, 8, 4))])
def test_wrong_ticker_or_effective_date_rejected(event):
    with pytest.raises(ActionEvidenceError, match="ticker mismatch|outside exact coverage"):
        materialize((event,))


def test_missing_event_rejected_in_both_directions():
    with pytest.raises(ActionEvidenceError, match="inventory mismatch"):
        materialize(data=payload((action(),)))
    with pytest.raises(ActionEvidenceError, match="inventory mismatch"):
        materialize((action(),), data=payload())


@pytest.mark.parametrize("kind", ["RIGHTS_ISSUE", "PAID_CAPITAL_INCREASE", "UNKNOWN"])
def test_unsupported_cash_capital_actions_rejected(kind):
    events = (action(),)
    with pytest.raises(ActionEvidenceError, match="unsupported capital action"):
        materialize(events, data=payload(events, kind=kind))


def test_dataclass_event_tampering_rejected():
    bad = replace(action(), share_multiplier=3)
    with pytest.raises(ActionEvidenceError, match="event identity mismatch"):
        materialize((bad,))


@pytest.mark.parametrize("field,value", [("close", -1), ("close", float("nan")), ("price_basis", "ADJUSTED")])
def test_direct_price_dataclass_tampering_rejected(field, value):
    price = build_price_level_observation(ticker="AAA", trade_date=PRICE_DATE, close=100)
    with pytest.raises(PriceLevelValuationBasisError):
        materialize(price=replace(price, **{field: value}))


@pytest.mark.parametrize("change", ["manifest_hash", "manifest_bytes", "source_bytes", "missing_bytes"])
def test_hash_and_byte_identity_required(change):
    proof = evidence(payload())
    if change == "manifest_hash":
        proof = replace(proof, expected_sha256="0" * 64)
    elif change == "manifest_bytes":
        proof = replace(proof, manifest_bytes=proof.manifest_bytes + b" ")
    else:
        proof = replace(proof, source_bytes={} if change == "missing_bytes" else {"test:inventory": b"changed"})
    with pytest.raises(ActionEvidenceError, match="SHA256|bytes missing"):
        materialize(proof=proof)


def test_intraday_close_and_next_session_price_are_rejected():
    for instant in ("2026-08-03T18:09:59+03:00", "2026-08-02T18:10:00+03:00"):
        with pytest.raises(PriceLevelValuationBasisError, match="unavailable"):
            materialize(cutoff=datetime.fromisoformat(instant))


def test_known_half_day_close_is_available():
    day = date(2026, 5, 26)
    data = payload()
    data.update(shares_basis_date="2026-03-31", complete_through=day.isoformat())
    data["sources"][0]["published_at"] = "2026-05-26T12:40:00+03:00"
    result = materialize(data=data, shares_basis_date=date(2026, 3, 31), events_complete_through=day,
                         price=build_price_level_observation(ticker="AAA", trade_date=day, close=100),
                         cutoff=datetime.fromisoformat("2026-05-26T12:40:00+03:00"))
    assert result.market_cap == 1_000_000_000


def test_two_materializations_are_bitwise_equal():
    first = json.dumps(asdict(materialize()), sort_keys=True, default=str).encode()
    second = json.dumps(asdict(materialize()), sort_keys=True, default=str).encode()
    assert first == second
    assert sha256(first).digest() == sha256(second).digest()
