from __future__ import annotations

import pandas as pd
import pytest

from src.analytics.historical_price_aliases import (
    HistoricalPriceAliasError,
    resolve_missing_ticker_prices,
)


SHA_A = "a" * 64
SHA_B = "b" * 64


def _prices(rows):
    out = []
    for ticker, day, close in rows:
        out.append({
            "ticker": ticker,
            "yahoo_symbol": f"{ticker}.IS",
            "trade_date": day,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "adj_close": close,
            "volume": 100,
        })
    return pd.DataFrame(out)


def _lineage(old="OLD", new="NEW", effective="2025-06-02"):
    return pd.DataFrame([{
        "effective_date": effective,
        "old_ticker": old,
        "new_ticker": new,
        "source_workbook_sha256": SHA_A,
        "event_sha256": SHA_B,
    }])


def test_moves_successor_pre_effective_history_to_old_ticker_and_keeps_post_effective_new():
    direct = _prices([
        ("NEW", "2025-05-30", 10.0),
        ("NEW", "2025-06-02", 11.0),
        ("AAA", "2025-05-30", 20.0),
    ])

    resolved, audit = resolve_missing_ticker_prices(
        direct, ["OLD"], _lineage()
    )

    got = {(r.ticker, str(r.trade_date.date())) for r in resolved.itertuples(index=False)}
    assert ("OLD", "2025-05-30") in got
    assert ("NEW", "2025-05-30") not in got
    assert ("NEW", "2025-06-02") in got
    old = resolved[resolved.ticker == "OLD"].iloc[0]
    assert old.price_source_ticker == "NEW"
    assert old.price_resolution == "BORSA_LINEAGE_YAHOO_ALIAS"
    assert audit.iloc[0].old_ticker == "OLD"
    assert audit.iloc[0].source_ticker == "NEW"
    assert int(audit.iloc[0].recovered_rows) == 1


def test_can_use_supplemental_successor_not_present_in_target_union():
    direct = _prices([("AAA", "2025-05-30", 20.0)])
    supplemental = _prices([
        ("NEW", "2025-05-30", 10.0),
        ("NEW", "2025-06-02", 11.0),
    ])

    resolved, audit = resolve_missing_ticker_prices(
        direct, ["OLD"], _lineage(), supplemental_prices=supplemental
    )

    old = resolved[resolved.ticker == "OLD"]
    assert len(old) == 1
    assert float(old.iloc[0].close) == 10.0
    assert audit.iloc[0].recovered_rows == 1


def test_missing_ticker_without_exact_lineage_fails_closed():
    direct = _prices([("AAA", "2025-05-30", 20.0)])
    with pytest.raises(HistoricalPriceAliasError, match="tam 1 direct ticker-lineage"):
        resolve_missing_ticker_prices(direct, ["UNKNOWN"], _lineage())


def test_lineage_without_pre_effective_successor_history_fails_closed():
    direct = _prices([("NEW", "2025-06-02", 11.0)])
    with pytest.raises(HistoricalPriceAliasError, match="effective_date oncesi successor Yahoo fiyati yok"):
        resolve_missing_ticker_prices(direct, ["OLD"], _lineage())


def test_does_not_shift_dates_or_invent_rows():
    direct = _prices([
        ("NEW", "2025-05-29", 9.0),
        ("NEW", "2025-05-30", 10.0),
        ("NEW", "2025-06-02", 11.0),
    ])
    resolved, _ = resolve_missing_ticker_prices(direct, ["OLD"], _lineage())
    old_days = resolved.loc[resolved.ticker == "OLD", "trade_date"].dt.strftime("%Y-%m-%d").tolist()
    assert old_days == ["2025-05-29", "2025-05-30"]
    assert len(resolved) == len(direct)
