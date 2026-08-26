from __future__ import annotations

"""Auditable corporate-action event contract for real historical backtests.

This module deliberately does *not* mutate the locked V24-B portfolio engine.
It defines the evidence boundary required before a later engine/version may
apply split/bonus-share or cash-dividend economics.

Why a separate contract is required:
- a split/bonus event changes share quantity, not cash;
- a cash dividend creates cash on its payment date, not merely an adjusted-price
  series;
- replacing raw OPEN/CLOSE with an adjusted price would silently change the
  integer-lot and cash-allocation semantics of V24-B.
"""

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from math import isfinite
from typing import Iterable, Optional

import pandas as pd


ACTION_SPLIT = "SHARE_MULTIPLIER"
ACTION_CASH_DIVIDEND = "CASH_DIVIDEND"
VALID_ACTION_TYPES = frozenset({ACTION_SPLIT, ACTION_CASH_DIVIDEND})


class CorporateActionEventError(ValueError):
    pass


def _date(value: object, field: str) -> date:
    try:
        ts = pd.Timestamp(value)
    except Exception as exc:
        raise CorporateActionEventError(f"{field} gecerli tarih olmali") from exc
    if pd.isna(ts):
        raise CorporateActionEventError(f"{field} gecerli tarih olmali")
    return ts.date()


def _ticker(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CorporateActionEventError("ticker dolu metin olmali")
    return value.strip().upper()


def _positive(value: object, field: str) -> float:
    if isinstance(value, bool):
        raise CorporateActionEventError(f"{field} pozitif sonlu sayi olmali")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise CorporateActionEventError(f"{field} pozitif sonlu sayi olmali") from exc
    if not isfinite(number) or number <= 0:
        raise CorporateActionEventError(f"{field} pozitif sonlu sayi olmali")
    return number


def _sha(value: object, field: str) -> str:
    text = str(value or "").strip().lower()
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise CorporateActionEventError(f"{field} 64-hex olmali")
    return text


@dataclass(frozen=True)
class HistoricalCorporateAction:
    ticker: str
    action_type: str
    ex_date: date
    payment_date: Optional[date]
    share_multiplier: Optional[float]
    cash_per_share: Optional[float]
    currency: Optional[str]
    source_ref: str
    source_sha256: str
    action_id: str

    @classmethod
    def build(
        cls,
        *,
        ticker: object,
        action_type: object,
        ex_date: object,
        payment_date: object = None,
        share_multiplier: object = None,
        cash_per_share: object = None,
        currency: object = None,
        source_ref: object,
        source_sha256: object,
    ) -> "HistoricalCorporateAction":
        t = _ticker(ticker)
        kind = str(action_type or "").strip().upper()
        if kind not in VALID_ACTION_TYPES:
            raise CorporateActionEventError(f"action_type gecersiz: {kind!r}")
        ex = _date(ex_date, "ex_date")
        source = str(source_ref or "").strip()
        if not source:
            raise CorporateActionEventError("source_ref dolu metin olmali")
        source_hash = _sha(source_sha256, "source_sha256")

        pay: Optional[date] = None
        mult: Optional[float] = None
        cash: Optional[float] = None
        ccy: Optional[str] = None

        if kind == ACTION_SPLIT:
            mult = _positive(share_multiplier, "share_multiplier")
            if mult == 1.0:
                raise CorporateActionEventError("share_multiplier 1 olamaz")
            if payment_date is not None or cash_per_share is not None or currency is not None:
                raise CorporateActionEventError(
                    "SHARE_MULTIPLIER payment_date/cash_per_share/currency tasiyamaz"
                )
        else:
            cash = _positive(cash_per_share, "cash_per_share")
            if payment_date is None:
                raise CorporateActionEventError("CASH_DIVIDEND payment_date gerektirir")
            pay = _date(payment_date, "payment_date")
            if pay < ex:
                raise CorporateActionEventError("payment_date ex_date'ten once olamaz")
            ccy = str(currency or "").strip().upper()
            if ccy != "TRY":
                raise CorporateActionEventError("gercek BIST backtestinde CASH_DIVIDEND currency TRY olmali")
            if share_multiplier is not None:
                raise CorporateActionEventError("CASH_DIVIDEND share_multiplier tasiyamaz")

        canonical = "|".join(
            [
                t,
                kind,
                ex.isoformat(),
                "" if pay is None else pay.isoformat(),
                "" if mult is None else format(mult, ".17g"),
                "" if cash is None else format(cash, ".17g"),
                "" if ccy is None else ccy,
                source,
                source_hash,
            ]
        )
        event_id = sha256(canonical.encode("utf-8")).hexdigest()
        return cls(
            ticker=t,
            action_type=kind,
            ex_date=ex,
            payment_date=pay,
            share_multiplier=mult,
            cash_per_share=cash,
            currency=ccy,
            source_ref=source,
            source_sha256=source_hash,
            action_id=event_id,
        )


def validate_corporate_action_events(
    events: Iterable[HistoricalCorporateAction],
) -> tuple[HistoricalCorporateAction, ...]:
    rows = tuple(events)
    seen_ids: set[str] = set()
    economic_keys: set[tuple[object, ...]] = set()
    for event in rows:
        if not isinstance(event, HistoricalCorporateAction):
            raise CorporateActionEventError("events yalnız HistoricalCorporateAction icermeli")
        if event.action_id in seen_ids:
            raise CorporateActionEventError(f"duplicate action_id: {event.action_id}")
        seen_ids.add(event.action_id)
        key = (
            event.ticker,
            event.action_type,
            event.ex_date,
            event.payment_date,
            event.share_multiplier,
            event.cash_per_share,
            event.currency,
        )
        if key in economic_keys:
            raise CorporateActionEventError(
                f"ayni ekonomik corporate action birden fazla kaynak satiriyla tekrarlandi: {event.ticker} {event.ex_date}"
            )
        economic_keys.add(key)
    return tuple(
        sorted(
            rows,
            key=lambda e: (
                e.ex_date,
                e.ticker,
                e.action_type,
                e.payment_date or e.ex_date,
                e.action_id,
            ),
        )
    )
