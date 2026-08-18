from __future__ import annotations

"""Fail-closed ticker-lineage resolver for historical Yahoo price discovery.

Yahoo commonly keeps an issuer's full history only under the *current* symbol
and removes the previous BIST trading code.  Historical Total Rasyo replay,
however, must use the ticker code that was valid on each signal date.

This module never guesses aliases.  A missing historical ticker can be recovered
only from an explicit Borsa Istanbul old_ticker -> new_ticker lineage row.  Price
rows from the successor symbol are relabelled only for dates strictly before the
lineage effective_date; successor rows on/after the effective date keep the new
code.
"""

from dataclasses import dataclass
from typing import Iterable, Tuple

import pandas as pd


REQUIRED_PRICE_COLUMNS = (
    "ticker", "yahoo_symbol", "trade_date", "open", "high", "low", "close",
    "adj_close", "volume",
)
REQUIRED_LINEAGE_COLUMNS = (
    "effective_date", "old_ticker", "new_ticker", "source_workbook_sha256",
    "event_sha256",
)


class HistoricalPriceAliasError(ValueError):
    pass


@dataclass(frozen=True)
class AliasResolution:
    old_ticker: str
    source_ticker: str
    effective_date: pd.Timestamp
    recovered_rows: int
    source_workbook_sha256: str
    event_sha256: str


def _ticker(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HistoricalPriceAliasError("ticker dolu metin olmali")
    return value.strip().upper()


def _required(frame: pd.DataFrame, columns: Iterable[str], name: str) -> None:
    missing = set(columns) - set(frame.columns)
    if missing:
        raise HistoricalPriceAliasError(f"{name} missing columns: {sorted(missing)}")


def _hex64(value: object, name: str) -> str:
    text = str(value or "").strip().lower()
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise HistoricalPriceAliasError(f"{name} 64-hex olmali")
    return text


def resolve_missing_ticker_prices(
    direct_prices: pd.DataFrame,
    missing_tickers: Iterable[str],
    ticker_lineage: pd.DataFrame,
    *,
    supplemental_prices: pd.DataFrame | None = None,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Return canonical ticker-date prices plus an auditable alias table.

    ``direct_prices`` is the frozen Yahoo discovery for the historical target
    universe. ``supplemental_prices`` may contain successor symbols that were not
    themselves members of that universe (for example a ticker renamed after it
    had already left BIST100).

    The function is intentionally strict:
    * every requested missing ticker must have exactly one direct old->new row;
    * successor price history must exist before effective_date;
    * no duplicate canonical ticker+trade_date may result;
    * no price row is fabricated, forward-filled, or date-shifted.
    """
    _required(direct_prices, REQUIRED_PRICE_COLUMNS, "direct_prices")
    _required(ticker_lineage, REQUIRED_LINEAGE_COLUMNS, "ticker_lineage")
    if supplemental_prices is not None:
        _required(supplemental_prices, REQUIRED_PRICE_COLUMNS, "supplemental_prices")

    direct = direct_prices.copy()
    direct["ticker"] = direct["ticker"].map(_ticker)
    direct["trade_date"] = pd.to_datetime(direct["trade_date"], errors="raise").dt.normalize()
    if direct.duplicated(["ticker", "trade_date"]).any():
        raise HistoricalPriceAliasError("direct_prices duplicate ticker+trade_date")

    source_pool = direct.copy()
    if supplemental_prices is not None and not supplemental_prices.empty:
        extra = supplemental_prices.copy()
        extra["ticker"] = extra["ticker"].map(_ticker)
        extra["trade_date"] = pd.to_datetime(extra["trade_date"], errors="raise").dt.normalize()
        if extra.duplicated(["ticker", "trade_date"]).any():
            raise HistoricalPriceAliasError("supplemental_prices duplicate ticker+trade_date")
        # Prefer already-frozen direct rows when the same source ticker/date is
        # present in both frames. Supplemental data is only for absent sources.
        source_pool = pd.concat([direct, extra], ignore_index=True)
        source_pool = source_pool.drop_duplicates(["ticker", "trade_date"], keep="first")

    lineage = ticker_lineage.copy()
    lineage["old_ticker"] = lineage["old_ticker"].map(_ticker)
    lineage["new_ticker"] = lineage["new_ticker"].map(_ticker)
    lineage["effective_date"] = pd.to_datetime(lineage["effective_date"], errors="raise").dt.normalize()
    if lineage.duplicated(["effective_date", "old_ticker", "new_ticker"]).any():
        raise HistoricalPriceAliasError("ticker_lineage duplicate event")

    requested = sorted({_ticker(t) for t in missing_tickers})
    if not requested:
        canonical = direct.copy()
        canonical["price_source_ticker"] = canonical["ticker"]
        canonical["price_resolution"] = "DIRECT_YAHOO"
        canonical["alias_effective_date"] = pd.NaT
        return canonical.sort_values(["ticker", "trade_date"]).reset_index(drop=True), pd.DataFrame(
            columns=[f.name for f in AliasResolution.__dataclass_fields__.values()]
        )

    canonical = direct.copy()
    canonical["price_source_ticker"] = canonical["ticker"]
    canonical["price_resolution"] = "DIRECT_YAHOO"
    canonical["alias_effective_date"] = pd.NaT
    audits: list[AliasResolution] = []

    for old_ticker in requested:
        matches = lineage[lineage["old_ticker"] == old_ticker]
        if len(matches) != 1:
            raise HistoricalPriceAliasError(
                f"{old_ticker} icin tam 1 direct ticker-lineage bekleniyor; bulunan={len(matches)}"
            )
        row = matches.iloc[0]
        new_ticker = str(row["new_ticker"])
        effective = pd.Timestamp(row["effective_date"]).normalize()
        source_workbook_sha = _hex64(row["source_workbook_sha256"], "source_workbook_sha256")
        event_sha = _hex64(row["event_sha256"], "event_sha256")

        source = source_pool[
            (source_pool["ticker"] == new_ticker) &
            (source_pool["trade_date"] < effective)
        ].copy()
        if source.empty:
            raise HistoricalPriceAliasError(
                f"{old_ticker}->{new_ticker} icin effective_date oncesi successor Yahoo fiyati yok"
            )

        # If Yahoo exposes the entire pre-rename history under the successor code,
        # remove those pre-effective successor rows from canonical output and move
        # them under the historically valid old code. Rows on/after effective date
        # remain under the successor code.
        canonical = canonical[
            ~((canonical["ticker"] == new_ticker) & (canonical["trade_date"] < effective))
        ].copy()

        source["price_source_ticker"] = new_ticker
        source["price_resolution"] = "BORSA_LINEAGE_YAHOO_ALIAS"
        source["alias_effective_date"] = effective
        source["ticker"] = old_ticker
        canonical = pd.concat([canonical, source[canonical.columns]], ignore_index=True)

        audits.append(AliasResolution(
            old_ticker=old_ticker,
            source_ticker=new_ticker,
            effective_date=effective,
            recovered_rows=int(len(source)),
            source_workbook_sha256=source_workbook_sha,
            event_sha256=event_sha,
        ))

    if canonical.duplicated(["ticker", "trade_date"]).any():
        dup = canonical.loc[canonical.duplicated(["ticker", "trade_date"], keep=False), ["ticker", "trade_date"]]
        raise HistoricalPriceAliasError(
            f"alias resolution duplicate canonical ticker+trade_date: {dup.head(5).to_dict('records')}"
        )

    audit_df = pd.DataFrame([a.__dict__ for a in audits])
    return (
        canonical.sort_values(["ticker", "trade_date"]).reset_index(drop=True),
        audit_df.sort_values(["old_ticker"]).reset_index(drop=True),
    )
