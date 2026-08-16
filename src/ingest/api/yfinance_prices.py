from __future__ import annotations

from typing import Dict, Iterable, Optional
import pandas as pd


def _import_yf():
    try:
        import yfinance as yf  # type: ignore
    except Exception as e:
        raise RuntimeError("yfinance is not installed. Install with: pip install yfinance") from e
    return yf


def default_symbol_for_ticker(ticker: str) -> str:
    return f"{ticker}.IS"


def _normalize_yf_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize yfinance output to simple columns.

    yfinance may return a MultiIndex in some versions/settings and the
    `Adj Close` column contains a space. Using itertuples/getattr is therefore
    fragile. This helper keeps the downloader stable across pandas/yfinance
    versions.
    """
    if df is None or df.empty:
        return pd.DataFrame()
    d = df.copy()
    if isinstance(d.columns, pd.MultiIndex):
        # We download one symbol at a time, so prefer the first level that looks
        # like OHLCV fields. This works for both (field, ticker) and (ticker, field).
        if {"Open", "High", "Low", "Close"}.intersection(set(map(str, d.columns.get_level_values(0)))):
            d.columns = [str(c[0]) for c in d.columns]
        else:
            d.columns = [str(c[-1]) for c in d.columns]
    d = d.reset_index()
    return d


def _val(row: pd.Series, col: str):
    if col not in row.index:
        return None
    v = row[col]
    if pd.isna(v):
        return None
    try:
        return float(v)
    except Exception:
        return None


def _date_val(row: pd.Series):
    # yfinance uses Date for daily data; sometimes the column is named index.
    for c in ("Date", "Datetime", "index"):
        if c in row.index:
            return pd.to_datetime(row[c]).date()
    # Fallback: first column after reset_index.
    return pd.to_datetime(row.iloc[0]).date()


def fetch_prices(
    tickers: Iterable[str],
    start: str,
    end: str,
    symbol_map: Optional[Dict[str, str]] = None,
) -> pd.DataFrame:
    yf = _import_yf()
    symbol_map = symbol_map or {}
    rows = []

    for t in tickers:
        sym = symbol_map.get(t, default_symbol_for_ticker(t))
        raw = yf.download(sym, start=start, end=end, auto_adjust=False, progress=False)
        df = _normalize_yf_frame(raw)
        if df.empty:
            continue
        for _, row in df.iterrows():
            d = _date_val(row)
            close = _val(row, "Close")
            adj_close = _val(row, "Adj Close")
            # If yfinance does not return Adj Close, fall back to Close.
            if adj_close is None:
                adj_close = close
            rows.append((
                str(t), d,
                _val(row, "Open"),
                _val(row, "High"),
                _val(row, "Low"),
                close,
                adj_close,
                _val(row, "Volume"),
                "TRY"
            ))
    return pd.DataFrame(rows, columns=["ticker", "trade_date", "open", "high", "low", "close", "adj_close", "volume", "currency"])


def fetch_index_prices(
    index_code: str,
    yahoo_symbol: str,
    start: str,
    end: str
) -> pd.DataFrame:
    yf = _import_yf()
    raw = yf.download(yahoo_symbol, start=start, end=end, auto_adjust=False, progress=False)
    df = _normalize_yf_frame(raw)
    if df.empty:
        return pd.DataFrame(columns=["index_code", "trade_date", "open", "high", "low", "close", "volume", "currency"])
    rows = []
    for _, row in df.iterrows():
        d = _date_val(row)
        rows.append((
            index_code, d,
            _val(row, "Open"),
            _val(row, "High"),
            _val(row, "Low"),
            _val(row, "Close"),
            _val(row, "Volume"),
            "TRY"
        ))
    return pd.DataFrame(rows, columns=["index_code", "trade_date", "open", "high", "low", "close", "volume", "currency"])
