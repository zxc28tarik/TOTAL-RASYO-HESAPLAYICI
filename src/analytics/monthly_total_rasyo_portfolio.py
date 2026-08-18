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
* execution uses the supplied rebalance-day OPEN; holdings are marked at CLOSE,
* optional verified corporate actions are applied on their effective/ex-date:
  split/bonus factors change held share count and cash dividends enter cash,
* optional verified Borsa ticker-code changes migrate an existing position's
  identity without creating a BUY/SELL or changing shares/cost basis.

Corporate actions and ticker changes are processed chronologically between monthly
rebalance dates.  On a rebalance date itself they are applied before contribution
and before OPEN execution, so newly bought shares cannot receive a same-day
ex-dividend cash payment.  If a same-day code change and corporate action coexist,
the code change is applied first and the corporate action must use the effective
new ticker.

The engine deliberately consumes raw OPEN/CLOSE.  Adjusted-close substitution is
not part of portfolio accounting.  Transaction costs/slippage remain out of scope
for V24-B.
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
class CorporateActionApplication:
    date: pd.Timestamp
    ticker: str
    shares_before: int
    shares_after: int
    split_factor: float
    cash_dividend_per_share: float
    cash_dividend: float


@dataclass(frozen=True)
class TickerChangeApplication:
    date: pd.Timestamp
    old_ticker: str
    new_ticker: str
    shares: int
    cost_basis: float


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


def _optional_nonnegative(name: str, value: object) -> float:
    if value is None or pd.isna(value) or (isinstance(value, str) and not value.strip()):
        return 0.0
    if isinstance(value, bool):
        raise MonthlyPortfolioError(f"{name} boolean olamaz")
    return _nonnegative_amount(name, value)


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


def _normalize_corporate_actions(actions: Optional[pd.DataFrame]) -> pd.DataFrame:
    cols = ["action_date", "ticker", "split_factor", "cash_dividend_per_share"]
    if actions is None:
        return pd.DataFrame(columns=cols)
    missing = set(cols) - set(actions.columns)
    if missing:
        raise MonthlyPortfolioError(f"corporate_actions missing columns: {sorted(missing)}")
    a = actions[cols].copy()
    if a.empty:
        return a
    a["action_date"] = pd.to_datetime(a["action_date"], errors="raise").dt.normalize()
    a["ticker"] = a["ticker"].map(_ticker)
    if a.duplicated(["action_date", "ticker"]).any():
        raise MonthlyPortfolioError("duplicate corporate action for action_date+ticker")

    split_values: List[float] = []
    dividend_values: List[float] = []
    for row in a[["split_factor", "cash_dividend_per_share"]].itertuples(index=False):
        factor = _optional_nonnegative("split_factor", row.split_factor)
        dividend = _optional_nonnegative(
            "cash_dividend_per_share", row.cash_dividend_per_share
        )
        # 0 or 1 means no share-count change.  Yahoo discovery commonly emits 0
        # on dividend-only rows; canonical verified inputs may use 1 instead.
        has_split = factor not in (0.0, 1.0)
        if not has_split and dividend == 0.0:
            raise MonthlyPortfolioError("corporate action row split/temettu acisindan no-op olamaz")
        split_values.append(factor)
        dividend_values.append(dividend)
    a["split_factor"] = split_values
    a["cash_dividend_per_share"] = dividend_values
    return a.sort_values(["action_date", "ticker"]).reset_index(drop=True)


def _normalize_ticker_changes(changes: Optional[pd.DataFrame]) -> pd.DataFrame:
    cols = ["effective_date", "old_ticker", "new_ticker"]
    if changes is None:
        return pd.DataFrame(columns=cols)
    missing = set(cols) - set(changes.columns)
    if missing:
        raise MonthlyPortfolioError(f"ticker_changes missing columns: {sorted(missing)}")
    t = changes[cols].copy()
    if t.empty:
        return t
    t["effective_date"] = pd.to_datetime(t["effective_date"], errors="raise").dt.normalize()
    t["old_ticker"] = t["old_ticker"].map(_ticker)
    t["new_ticker"] = t["new_ticker"].map(_ticker)
    if (t["old_ticker"] == t["new_ticker"]).any():
        raise MonthlyPortfolioError("ticker change old_ticker ve new_ticker ayni olamaz")
    if t.duplicated(["effective_date", "old_ticker"]).any():
        raise MonthlyPortfolioError("duplicate ticker change for effective_date+old_ticker")
    if t.duplicated(["effective_date", "new_ticker"]).any():
        raise MonthlyPortfolioError("same-day ticker changes ayni new_ticker'a birlesemez")
    return t.sort_values(["effective_date", "old_ticker", "new_ticker"]).reset_index(drop=True)


class MonthlyTotalRasyoSimulator:
    def __init__(self, config: PortfolioConfig = PortfolioConfig()) -> None:
        self.config = config
        self.cash = 0.0
        self.cumulative_contribution = 0.0
        self.positions: Dict[str, Position] = {}
        self.trades: List[Trade] = []
        self.corporate_action_events: List[CorporateActionApplication] = []
        self.ticker_change_events: List[TickerChangeApplication] = []
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
        """Return target holdings and removal reasons before execution."""
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

    def _apply_ticker_changes_on_day(self, day: pd.Timestamp, rows: pd.DataFrame) -> None:
        if rows.empty:
            return
        mapping = {row.old_ticker: row.new_ticker for row in rows.itertuples(index=False)}
        moving = {old: self.positions[old] for old in mapping if old in self.positions}
        if not moving:
            return

        moving_old = set(moving)
        destinations = {mapping[old] for old in moving_old}
        collisions = sorted(destinations & (set(self.positions) - moving_old))
        if collisions:
            raise MonthlyPortfolioError(
                f"ticker change destination already held: {collisions} on {day.date()}"
            )
        if len(destinations) != len(moving_old):
            raise MonthlyPortfolioError("ticker change held positions ayni destination'a birlesemez")

        # Simultaneous rename: remove all old identities first, then install the
        # new identities.  This avoids order-dependent behavior for same-day rows.
        for old in moving_old:
            del self.positions[old]
        for old in sorted(moving_old):
            new = mapping[old]
            pos = moving[old]
            pos.ticker = new
            self.positions[new] = pos
            self.ticker_change_events.append(TickerChangeApplication(
                day, old, new, int(pos.shares), float(pos.cost_basis)
            ))

    def _apply_corporate_action(self, day: pd.Timestamp, row: object) -> None:
        ticker = str(getattr(row, "ticker"))
        if ticker not in self.positions:
            return
        pos = self.positions[ticker]
        before = int(pos.shares)
        factor = float(getattr(row, "split_factor"))
        dividend = float(getattr(row, "cash_dividend_per_share"))

        if factor not in (0.0, 1.0):
            raw_shares = before * factor
            rounded = round(raw_shares)
            if abs(raw_shares - rounded) > 1e-9:
                raise MonthlyPortfolioError(
                    f"split fractional share requires explicit cash-in-lieu contract: "
                    f"{ticker} {before}*{factor} on {day.date()}"
                )
            if rounded <= 0:
                raise MonthlyPortfolioError("split held share count sifir/negatif yapamaz")
            pos.shares = int(rounded)

        cash_dividend = float(pos.shares) * dividend
        self.cash += cash_dividend
        self.corporate_action_events.append(CorporateActionApplication(
            day,
            ticker,
            before,
            int(pos.shares),
            factor,
            dividend,
            cash_dividend,
        ))

    def _apply_events_between(
        self,
        previous_day: Optional[pd.Timestamp],
        day: pd.Timestamp,
        corporate_actions: pd.DataFrame,
        ticker_changes: pd.DataFrame,
    ) -> None:
        if previous_day is None:
            actions = corporate_actions[corporate_actions["action_date"] <= day]
            changes = ticker_changes[ticker_changes["effective_date"] <= day]
        else:
            actions = corporate_actions[
                (corporate_actions["action_date"] > previous_day) &
                (corporate_actions["action_date"] <= day)
            ]
            changes = ticker_changes[
                (ticker_changes["effective_date"] > previous_day) &
                (ticker_changes["effective_date"] <= day)
            ]

        event_days = sorted(
            set(actions["action_date"].tolist()) |
            set(changes["effective_date"].tolist())
        )
        for event_day in event_days:
            change_rows = changes[changes["effective_date"] == event_day]
            # Effective ticker identity is established first at start-of-day.
            self._apply_ticker_changes_on_day(event_day, change_rows)
            action_rows = actions[actions["action_date"] == event_day]
            for row in action_rows.sort_values("ticker").itertuples(index=False):
                self._apply_corporate_action(event_day, row)

    def run(
        self,
        signals: pd.DataFrame,
        prices: pd.DataFrame,
        contributions: pd.DataFrame,
        corporate_actions: Optional[pd.DataFrame] = None,
        ticker_changes: Optional[pd.DataFrame] = None,
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        s, p, c = self._normalize(signals, prices, contributions)
        actions = _normalize_corporate_actions(corporate_actions)
        changes = _normalize_ticker_changes(ticker_changes)

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
        previous_day: Optional[pd.Timestamp] = None

        for day in dates:
            # All verified events after the previous monthly snapshot through this
            # rebalance day are applied before this month's cash/trades.
            self._apply_events_between(previous_day, day, actions, changes)

            month = s[s["signal_date"] == day]
            signals_now = self._signal_map(month)
            contribution = contribution_map[day]
            self.cash += contribution
            self.cumulative_contribution += contribution
            trade_start = len(self.trades)

            target, removals = self._construct_target(signals_now)

            # Sales happen before buys, so sale proceeds are available in the
            # same month's equal-cash pool together with the contribution and any
            # verified dividends received since the previous snapshot.
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
            previous_day = day

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
