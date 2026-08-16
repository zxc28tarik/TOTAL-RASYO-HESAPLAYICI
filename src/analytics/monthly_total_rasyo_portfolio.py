from __future__ import annotations

"""V24-B — deterministic monthly Total Rasyo portfolio execution engine.

This module intentionally does not calculate Total Rasyo signals.  It consumes a
PIT-safe monthly signal snapshot produced by an upstream collector and applies the
locked portfolio rules only:

* rebalance/contribution date is the first trading day supplied by the caller,
* at most six stock positions and no forced minimum,
* buy only AL,
* held AL is kept and may receive the month's cash allocation,
* held IZLE is kept but receives no new cash,
* held UZAK is sold,
* when full, stronger AL names replace the weakest holding,
* ranking is deterministic: score DESC, ticker ASC,
* monthly contribution and same-day sale proceeds share one cash pool,
* stock purchases use integer shares,
* available cash is split equally across current AL targets; integer-lot residue
  remains cash,
* execution uses the supplied rebalance-day OPEN; holdings are marked at CLOSE.

Transaction costs/slippage are deliberately out of scope for V24-B.
"""

from dataclasses import dataclass
from math import floor, isfinite
from typing import Dict, List, Mapping, Optional, Tuple

import pandas as pd


VALID_DECISIONS = frozenset({"AL", "IZLE", "UZAK"})
NEG_INF = float("-inf")


class MonthlyPortfolioError(ValueError):
    pass


@dataclass(frozen=True)
class PortfolioConfig:
    max_positions: int = 6

    def __post_init__(self) -> None:
        if isinstance(self.max_positions, bool) or not isinstance(self.max_positions, int):
            raise MonthlyPortfolioError("max_positions tam sayi olmali")
        if not 1 <= self.max_positions <= 6:
            raise MonthlyPortfolioError("max_positions 1 ile 6 arasinda olmali")


@dataclass
class Position:
    ticker: str
    shares: int = 0
    cost_basis: float = 0.0

    @property
    def avg_cost(self) -> float:
        return self.cost_basis / self.shares if self.shares else 0.0


@dataclass(frozen=True)
class Trade:
    date: pd.Timestamp
    ticker: str
    side: str
    shares: int
    price: float
    gross: float
    reason: str
    score: Optional[float]
    decision: Optional[str]


@dataclass(frozen=True)
class MonthlySnapshot:
    date: pd.Timestamp
    contribution: float
    cumulative_contribution: float
    cash: float
    holdings_value: float
    nav: float
    holdings: str
    buys: str
    sells: str


def _ticker(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MonthlyPortfolioError("ticker dolu metin olmali")
    return value.strip().upper()


def _positive_price(name: str, value: object) -> float:
    try:
        px = float(value)
    except (TypeError, ValueError) as exc:
        raise MonthlyPortfolioError(f"{name} sayisal olmali") from exc
    if not isfinite(px) or px <= 0:
        raise MonthlyPortfolioError(f"{name} pozitif ve sonlu olmali")
    return px


def _nonnegative_amount(name: str, value: object) -> float:
    try:
        amount = float(value)
    except (TypeError, ValueError) as exc:
        raise MonthlyPortfolioError(f"{name} sayisal olmali") from exc
    if not isfinite(amount) or amount < 0:
        raise MonthlyPortfolioError(f"{name} negatif olmayan sonlu sayi olmali")
    return amount


def _score(value: object, *, decision: str) -> float:
    if value is None or pd.isna(value):
        if decision == "AL":
            raise MonthlyPortfolioError("AL satirinda final_score eksik olamaz")
        return NEG_INF
    try:
        score = float(value)
    except (TypeError, ValueError) as exc:
        raise MonthlyPortfolioError("final_score sayisal olmali") from exc
    if not isfinite(score):
        raise MonthlyPortfolioError("final_score sonlu olmali")
    return score


def _rank_key(ticker: str, signals: Mapping[str, Mapping[str, object]]) -> Tuple[float, str]:
    """Best-first ranking key: lower tuple sorts first."""
    return (-float(signals.get(ticker, {}).get("score", NEG_INF)), ticker)


class MonthlyTotalRasyoSimulator:
    def __init__(self, config: PortfolioConfig = PortfolioConfig()) -> None:
        self.config = config
        self.cash = 0.0
        self.cumulative_contribution = 0.0
        self.positions: Dict[str, Position] = {}
        self.trades: List[Trade] = []
        self.snapshots: List[MonthlySnapshot] = []

    @staticmethod
    def _validate_columns(signals: pd.DataFrame, prices: pd.DataFrame,
                          contributions: pd.DataFrame) -> None:
        required = (
            (signals, {"signal_date", "ticker", "final_score", "decision"}, "signals"),
            (prices, {"trade_date", "ticker", "open", "close"}, "prices"),
            (contributions, {"signal_date", "contribution"}, "contributions"),
        )
        for frame, columns, name in required:
            missing = columns - set(frame.columns)
            if missing:
                raise MonthlyPortfolioError(f"{name} missing columns: {sorted(missing)}")

    @staticmethod
    def _normalize(signals: pd.DataFrame, prices: pd.DataFrame,
                   contributions: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        MonthlyTotalRasyoSimulator._validate_columns(signals, prices, contributions)
        s, p, c = signals.copy(), prices.copy(), contributions.copy()

        s["signal_date"] = pd.to_datetime(s["signal_date"], errors="raise").dt.normalize()
        p["trade_date"] = pd.to_datetime(p["trade_date"], errors="raise").dt.normalize()
        c["signal_date"] = pd.to_datetime(c["signal_date"], errors="raise").dt.normalize()

        s["ticker"] = s["ticker"].map(_ticker)
        p["ticker"] = p["ticker"].map(_ticker)
        s["decision"] = s["decision"].astype(str).str.strip().str.upper()

        bad_decisions = set(s["decision"]) - VALID_DECISIONS
        if bad_decisions:
            raise MonthlyPortfolioError(f"invalid decisions: {sorted(bad_decisions)}")
        if s.duplicated(["signal_date", "ticker"]).any():
            raise MonthlyPortfolioError("duplicate signal rows for signal_date+ticker")
        if p.duplicated(["trade_date", "ticker"]).any():
            raise MonthlyPortfolioError("duplicate price rows for trade_date+ticker")
        if c.duplicated(["signal_date"]).any():
            raise MonthlyPortfolioError("duplicate contribution rows")

        p["open"] = [_positive_price("open", v) for v in p["open"]]
        p["close"] = [_positive_price("close", v) for v in p["close"]]
        c["contribution"] = [_nonnegative_amount("contribution", v) for v in c["contribution"]]

        scores: List[float] = []
        for row in s[["final_score", "decision"]].itertuples(index=False):
            scores.append(_score(row.final_score, decision=row.decision))
        s["normalized_score"] = scores
        return s, p, c

    @staticmethod
    def _signal_map(month_signals: pd.DataFrame) -> Dict[str, dict]:
        return {
            row.ticker: {"score": float(row.normalized_score), "decision": row.decision}
            for row in month_signals.itertuples(index=False)
        }

    def _construct_target(self, signals: Dict[str, dict]) -> Tuple[List[str], Dict[str, str]]:
        """Return target holdings and removal reasons before execution.

        New entries may only be current AL. Existing non-UZAK positions are
        incumbents.  If incumbents + new AL exceed capacity, the deterministic
        score-desc/ticker-asc ranking selects the strongest names.  Therefore an
        equal-score candidate with a lexicographically smaller ticker is stronger.
        """
        removals: Dict[str, str] = {}
        survivors: List[str] = []

        for ticker in self.positions:
            decision = signals.get(ticker, {}).get("decision")
            if decision == "UZAK":
                removals[ticker] = "UZAK"
            else:
                survivors.append(ticker)

        new_al = [
            ticker for ticker, payload in signals.items()
            if payload["decision"] == "AL" and ticker not in survivors
        ]
        pool = survivors + new_al
        target = sorted(pool, key=lambda t: _rank_key(t, signals))[: self.config.max_positions]
        target_set = set(target)

        for ticker in survivors:
            if ticker not in target_set:
                removals[ticker] = "ROTATE_TO_STRONGER_AL"

        return target, removals

    @staticmethod
    def _price(price_map: Mapping[Tuple[pd.Timestamp, str], Tuple[float, float]],
               day: pd.Timestamp, ticker: str, *, side: str) -> float:
        key = (day, ticker)
        if key not in price_map:
            raise MonthlyPortfolioError(
                f"missing execution price for {side} {ticker} on {day.date()}"
            )
        return price_map[key][0]

    def _sell_all(self, day: pd.Timestamp, ticker: str, price: float, reason: str,
                  payload: Optional[Mapping[str, object]]) -> None:
        pos = self.positions[ticker]
        gross = pos.shares * price
        self.cash += gross
        self.trades.append(Trade(
            day, ticker, "SELL", int(pos.shares), price, gross, reason,
            None if payload is None or payload.get("score") == NEG_INF else float(payload["score"]),
            None if payload is None else str(payload.get("decision")),
        ))
        del self.positions[ticker]

    def _buy(self, day: pd.Timestamp, ticker: str, price: float, budget: float,
             reason: str, payload: Mapping[str, object]) -> None:
        spend_cap = min(float(budget), self.cash)
        shares = floor(spend_cap / price)
        if shares <= 0:
            return
        gross = shares * price
        self.cash -= gross
        pos = self.positions.get(ticker)
        if pos is None:
            pos = Position(ticker=ticker)
            self.positions[ticker] = pos
        pos.shares += int(shares)
        pos.cost_basis += gross
        self.trades.append(Trade(
            day, ticker, "BUY", int(shares), price, gross, reason,
            float(payload["score"]), str(payload["decision"]),
        ))

    def run(self, signals: pd.DataFrame, prices: pd.DataFrame,
            contributions: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
        s, p, c = self._normalize(signals, prices, contributions)

        price_map = {
            (row.trade_date, row.ticker): (float(row.open), float(row.close))
            for row in p.itertuples(index=False)
        }
        contribution_map = {
            row.signal_date: float(row.contribution) for row in c.itertuples(index=False)
        }

        # The contribution schedule is the authoritative monthly clock.  A month
        # with zero signal rows must still add cash and produce a snapshot.
        dates = sorted(contribution_map)

        for day in dates:
            month = s[s["signal_date"] == day]
            signals_now = self._signal_map(month)
            contribution = contribution_map[day]
            self.cash += contribution
            self.cumulative_contribution += contribution
            trade_start = len(self.trades)

            target, removals = self._construct_target(signals_now)

            # Sales happen before buys, so sale proceeds are available in the
            # same month's equal-cash pool together with the contribution.
            for ticker in sorted(removals):
                if ticker not in self.positions:
                    continue
                px = self._price(price_map, day, ticker, side="SELL")
                self._sell_all(day, ticker, px, removals[ticker], signals_now.get(ticker))

            target = [ticker for ticker in target if ticker not in removals]
            if len(target) > self.config.max_positions:
                raise AssertionError("target exceeds max_positions")

            al_targets = [
                ticker for ticker in target
                if signals_now.get(ticker, {}).get("decision") == "AL"
            ]
            al_targets = sorted(al_targets, key=lambda t: _rank_key(t, signals_now))

            # Fixed slice is intentional.  We do NOT recycle the unspent residue
            # of an expensive name into the next name because the locked neutral
            # default is equal cash allocation; residual stays cash.
            equal_budget = self.cash / len(al_targets) if al_targets else 0.0
            if equal_budget > 0.0:
                for ticker in al_targets:
                    px = self._price(price_map, day, ticker, side="BUY")
                    reason = "MONTHLY_DCA_AL" if ticker in self.positions else "NEW_AL"
                    self._buy(day, ticker, px, equal_budget, reason, signals_now[ticker])

            holdings_value = 0.0
            for ticker, pos in self.positions.items():
                key = (day, ticker)
                if key not in price_map:
                    raise MonthlyPortfolioError(
                        f"missing close price for held {ticker} on {day.date()}"
                    )
                holdings_value += pos.shares * price_map[key][1]

            new_trades = self.trades[trade_start:]
            buys = ", ".join(t.ticker for t in new_trades if t.side == "BUY")
            sells = ", ".join(t.ticker for t in new_trades if t.side == "SELL")
            holdings = ", ".join(sorted(self.positions))
            self.snapshots.append(MonthlySnapshot(
                day, contribution, self.cumulative_contribution, self.cash,
                holdings_value, self.cash + holdings_value, holdings, buys, sells,
            ))

        trade_columns = list(Trade.__dataclass_fields__)
        snapshot_columns = list(MonthlySnapshot.__dataclass_fields__)
        trades_df = pd.DataFrame([trade.__dict__ for trade in self.trades], columns=trade_columns)
        monthly_df = pd.DataFrame([snapshot.__dict__ for snapshot in self.snapshots], columns=snapshot_columns)
        return trades_df, monthly_df


def benchmark_dca(contributions: pd.DataFrame, benchmark_prices: pd.DataFrame) -> pd.DataFrame:
    """Apply the exact same contribution dates/cashflows to a benchmark index.

    Fractional benchmark units are used because an index level itself is not a
    stock lot.  Purchases use the supplied day's OPEN and valuation uses CLOSE.
    """
    required_c = {"signal_date", "contribution"}
    required_b = {"trade_date", "open", "close"}
    if missing := required_c - set(contributions.columns):
        raise MonthlyPortfolioError(f"contributions missing columns: {sorted(missing)}")
    if missing := required_b - set(benchmark_prices.columns):
        raise MonthlyPortfolioError(f"benchmark_prices missing columns: {sorted(missing)}")

    c, b = contributions.copy(), benchmark_prices.copy()
    c["signal_date"] = pd.to_datetime(c["signal_date"], errors="raise").dt.normalize()
    b["trade_date"] = pd.to_datetime(b["trade_date"], errors="raise").dt.normalize()
    if c.duplicated(["signal_date"]).any():
        raise MonthlyPortfolioError("duplicate contribution rows")
    if b.duplicated(["trade_date"]).any():
        raise MonthlyPortfolioError("duplicate benchmark price rows")

    c["contribution"] = [_nonnegative_amount("contribution", v) for v in c["contribution"]]
    b["open"] = [_positive_price("benchmark open", v) for v in b["open"]]
    b["close"] = [_positive_price("benchmark close", v) for v in b["close"]]
    price_map = {row.trade_date: (row.open, row.close) for row in b.itertuples(index=False)}

    units = 0.0
    cumulative = 0.0
    rows = []
    for row in c.sort_values("signal_date").itertuples(index=False):
        day = row.signal_date
        if day not in price_map:
            raise MonthlyPortfolioError(f"missing benchmark price on {day.date()}")
        amount = float(row.contribution)
        cumulative += amount
        open_px, close_px = price_map[day]
        units += amount / open_px
        rows.append((day, amount, cumulative, units, units * close_px))

    return pd.DataFrame(rows, columns=[
        "date", "contribution", "cumulative_contribution", "units", "benchmark_value",
    ])
