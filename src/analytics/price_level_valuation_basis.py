from __future__ import annotations

"""Economic basis contract for point-in-time price-level valuation.

Adjusted/total-return prices are useful for return continuity, but they are not
an actual historical market price level when cash dividends are back-adjusted.
Price-level valuation therefore uses raw market CLOSE and moves the share count
through effective share-multiplier corporate actions only.

The helper is deliberately fail-closed: callers must certify that the supplied
corporate-action event set is complete through the selected price date before a
market capitalization can be materialized.
"""

from dataclasses import dataclass
from datetime import date, datetime
import math
from typing import Iterable

from src.analytics.price_level_action_evidence import PriceLevelActionEvidence

from src.analytics.historical_backtest_corporate_action_events import (
    ACTION_CASH_DIVIDEND,
    ACTION_SPLIT,
    HistoricalCorporateAction,
    validate_corporate_action_events,
)

PRICE_LEVEL_BASIS = "POINT_IN_TIME_MARKET_CLOSE_V1"
SHARE_BASIS = "POINT_IN_TIME_MARKET_CLOSE_SHARES_V1"


class PriceLevelValuationBasisError(ValueError):
    pass


def _date(value: object, field: str) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise PriceLevelValuationBasisError(f"{field} date olmali")
    return value


def _ticker(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PriceLevelValuationBasisError("ticker dolu metin olmali")
    return value.strip().upper()


def _positive(value: object, field: str) -> float:
    if isinstance(value, bool):
        raise PriceLevelValuationBasisError(f"{field} pozitif sonlu sayi olmali")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise PriceLevelValuationBasisError(f"{field} pozitif sonlu sayi olmali") from exc
    if not math.isfinite(number) or number <= 0:
        raise PriceLevelValuationBasisError(f"{field} pozitif sonlu sayi olmali")
    return number


@dataclass(frozen=True)
class PriceLevelObservation:
    ticker: str
    trade_date: date
    close: float
    adjusted_close_diagnostic: float | None
    price_basis: str = PRICE_LEVEL_BASIS


@dataclass(frozen=True)
class PriceLevelMarketCap:
    ticker: str
    trade_date: date
    shares_basis_date: date
    raw_close: float
    normalized_shares_out: float
    market_cap: float
    applied_share_action_ids: tuple[str, ...]
    action_evidence_sha256: str
    price_basis: str = PRICE_LEVEL_BASIS
    share_basis: str = SHARE_BASIS


def build_price_level_observation(
    *,
    ticker: str,
    trade_date: date,
    close: object,
    adjusted_close: object | None = None,
) -> PriceLevelObservation:
    """Use raw CLOSE as price level; Adj Close is diagnostics only."""

    adj: float | None = None
    if adjusted_close is not None:
        adj = _positive(adjusted_close, "adjusted_close")
    return PriceLevelObservation(
        ticker=_ticker(ticker),
        trade_date=_date(trade_date, "trade_date"),
        close=_positive(close, "close"),
        adjusted_close_diagnostic=adj,
    )


def normalize_shares_out_to_price_date(
    *,
    ticker: str,
    shares_out: object,
    shares_basis_date: date,
    price_trade_date: date,
    corporate_actions: Iterable[HistoricalCorporateAction],
    events_complete_through: date,
) -> tuple[float, tuple[str, ...]]:
    """Move a dated share count to the price date using effective split events.

    This is arithmetic only; materialization additionally requires verified
    evidence. Cash dividends never change the share count. A split/bonus
    event is represented by ACTION_SPLIT/SHARE_MULTIPLIER and is applied only
    when ``shares_basis_date < ex_date <= price_trade_date``.
    """

    t = _ticker(ticker)
    basis_date = _date(shares_basis_date, "shares_basis_date")
    price_date = _date(price_trade_date, "price_trade_date")
    complete = _date(events_complete_through, "events_complete_through")
    if price_date < basis_date:
        raise PriceLevelValuationBasisError("price_trade_date shares_basis_date oncesinde olamaz")
    if complete < price_date:
        raise PriceLevelValuationBasisError("corporate-action event kapsami price_trade_date'e kadar tam olmali")

    normalized = _positive(shares_out, "shares_out")
    applied: list[str] = []
    for event in validate_corporate_action_events(corporate_actions):
        if event.ticker != t:
            continue
        if event.ex_date <= basis_date or event.ex_date > price_date:
            continue
        if event.action_type == ACTION_CASH_DIVIDEND:
            continue
        if event.action_type != ACTION_SPLIT or event.share_multiplier is None:
            raise PriceLevelValuationBasisError("beklenmeyen corporate-action share semantigi")
        normalized *= _positive(event.share_multiplier, "share_multiplier")
        if not math.isfinite(normalized) or normalized <= 0:
            raise PriceLevelValuationBasisError("normalize shares_out pozitif sonlu olmali")
        applied.append(event.action_id)
    return float(normalized), tuple(applied)


def materialize_price_level_market_cap(
    *,
    price: PriceLevelObservation,
    shares_out: object,
    shares_basis_date: date,
    corporate_actions: Iterable[HistoricalCorporateAction],
    events_complete_through: date,
    evidence: PriceLevelActionEvidence | None = None,
    cutoff: datetime | None = None,
) -> PriceLevelMarketCap:
    if not isinstance(price, PriceLevelObservation):
        raise PriceLevelValuationBasisError("price PriceLevelObservation olmali")
    if price.price_basis != PRICE_LEVEL_BASIS:
        raise PriceLevelValuationBasisError("price basis mismatch")
    # Revalidate direct dataclass construction, including sign and finite values.
    price = build_price_level_observation(
        ticker=price.ticker, trade_date=price.trade_date, close=price.close,
        adjusted_close=price.adjusted_close_diagnostic,
    )
    if not isinstance(evidence, PriceLevelActionEvidence):
        raise PriceLevelValuationBasisError("verified corporate-action evidence required")
    if not isinstance(cutoff, datetime) or cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise PriceLevelValuationBasisError("timezone-aware valuation cutoff required")
    from zoneinfo import ZoneInfo
    from src.analytics.historical_cutoff_execution_policy import TOTAL_RASYO_MONTHLY_OPEN_V1
    policy = TOTAL_RASYO_MONTHLY_OPEN_V1
    end = (policy.half_day_session_end if price.trade_date.isoformat() in policy.half_day_cutoff_dates
           else policy.full_day_session_end)
    available_at = datetime.combine(price.trade_date, end, tzinfo=ZoneInfo("Europe/Istanbul"))
    if available_at > cutoff:
        raise PriceLevelValuationBasisError("price close unavailable at valuation cutoff")
    events = tuple(corporate_actions)
    evidence_sha = evidence.verify(
        ticker=price.ticker, shares_basis_date=_date(shares_basis_date, "shares_basis_date"),
        price_trade_date=price.trade_date, cutoff=cutoff, events=events,
        shares_out=_positive(shares_out, "shares_out"),
    )
    normalized, applied = normalize_shares_out_to_price_date(
        ticker=price.ticker,
        shares_out=shares_out,
        shares_basis_date=shares_basis_date,
        price_trade_date=price.trade_date,
        corporate_actions=events,
        events_complete_through=events_complete_through,
    )
    market_cap = price.close * normalized
    if not math.isfinite(market_cap) or market_cap <= 0:
        raise PriceLevelValuationBasisError("market_cap pozitif sonlu olmali")
    return PriceLevelMarketCap(
        ticker=price.ticker,
        trade_date=price.trade_date,
        shares_basis_date=_date(shares_basis_date, "shares_basis_date"),
        raw_close=price.close,
        normalized_shares_out=normalized,
        market_cap=float(market_cap),
        applied_share_action_ids=applied,
        action_evidence_sha256=evidence_sha,
    )
