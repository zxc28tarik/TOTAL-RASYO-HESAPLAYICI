from __future__ import annotations
from datetime import date
import pandas as pd


def get_trading_days(conn) -> list[date]:
    df = pd.read_sql(
        "SELECT trade_date FROM core.index_prices_daily WHERE index_code='XU100' ORDER BY trade_date ASC",
        conn
    )
    return list(pd.to_datetime(df["trade_date"]).dt.date)


def next_trading_day(d: date, trading_days: list[date]) -> date:
    if not trading_days:
        raise ValueError("Trading calendar is empty")
    for td in trading_days:
        if td > d:
            return td
    return trading_days[-1]


def add_trading_days(start: date, n: int, trading_days: list[date]) -> date:
    if not trading_days:
        raise ValueError("Trading calendar is empty")
    if start not in trading_days:
        valid = [d for d in trading_days if d >= start]
        start = valid[0] if valid else trading_days[-1]
    idx = trading_days.index(start)
    j = idx + n
    return trading_days[-1] if j >= len(trading_days) else trading_days[j]
