from __future__ import annotations

"""The forward paper book: blind fills, settled closes, payments, lots, costs, actions."""

from datetime import datetime, timezone

import pandas as pd
import pytest

from scripts.paper_portfolio import (
    COST, MONTHLY, Book, can_fill, final_sessions, mark, process_session,
)

NO_ACTIONS = pd.DataFrame(columns=["date", "ticker", "kind", "value"])


def _order(i, time, side, ticker, **kw):
    return {"event": "ORDER", "id": f"O{i:04d}", "time": time, "side": side, "ticker": ticker, "why": "t", **kw}


def test_an_order_never_fills_on_its_own_istanbul_day():
    friday_evening = "2026-09-25T19:00:00+00:00"            # 22:00 Istanbul, Friday
    assert not can_fill(friday_evening, "2026-09-25")
    assert can_fill(friday_evening, "2026-09-28")
    before_open = "2026-09-28T05:00:00+00:00"               # 08:00 Istanbul, Monday
    assert not can_fill(before_open, "2026-09-28")           # conservative: the next session, not this one
    late_utc = "2026-09-28T22:30:00+00:00"                  # already Tuesday 01:30 in Istanbul
    assert not can_fill(late_utc, "2026-09-29")


def test_todays_close_is_used_only_once_it_has_settled():
    dates = ["2026-09-25", "2026-09-28"]
    assert final_sessions(dates, datetime(2026, 9, 28, 14, 0, tzinfo=timezone.utc)) == ["2026-09-25"]
    assert final_sessions(dates, datetime(2026, 9, 28, 16, 0, tzinfo=timezone.utc)) == dates


def _row(**prices):
    return pd.Series({"XU100": 10_000.0, **prices})


def test_payments_arrive_on_the_start_and_each_new_month_and_feed_both_books():
    book = Book()
    process_session(book, "2026-09-28", _row(), NO_ACTIONS, [])
    process_session(book, "2026-09-29", _row(), NO_ACTIONS, [])
    process_session(book, "2026-10-01", _row(), NO_ACTIONS, [])
    assert book.paid_in == 2 * MONTHLY
    assert book.cash == 2 * MONTHLY
    assert book.index_units == pytest.approx(2 * MONTHLY * (1 - COST) / 10_000.0)


def test_buys_are_whole_shares_with_commission_and_sells_realise_against_cost():
    book = Book()
    orders = [_order(1, "2026-09-25T19:00:00+00:00", "BUY", "AAA", tl=5_000.0)]
    events = process_session(book, "2026-09-28", _row(AAA=33.0), NO_ACTIONS, orders)
    fill = [e for e in events if e["event"] == "FILL"][0]
    assert fill["qty"] == int(5_000 / (33.0 * (1 + COST)))
    assert book.cash == pytest.approx(MONTHLY - fill["qty"] * 33.0 * (1 + COST))
    orders.append(_order(2, "2026-09-28T19:00:00+00:00", "SELL", "AAA", fraction=0.5))
    events = process_session(book, "2026-09-29", _row(AAA=40.0), NO_ACTIONS, orders)
    sell = [e for e in events if e["event"] == "FILL"][0]
    assert sell["qty"] == fill["qty"] // 2
    assert sell["realised_tl"] == pytest.approx(sell["qty"] * 40 * (1 - COST) - sell["qty"] / fill["qty"] * fill["tl"],
                                                abs=0.01)


def test_an_order_without_a_print_waits_instead_of_failing():
    book = Book()
    orders = [_order(1, "2026-09-25T19:00:00+00:00", "BUY", "AAA", tl=5_000.0)]
    process_session(book, "2026-09-28", _row(AAA=float("nan")), NO_ACTIONS, orders)
    assert "O0001" not in book.done_orders
    process_session(book, "2026-09-29", _row(AAA=10.0), NO_ACTIONS, orders)
    assert book.shares["AAA"] > 0


def test_dividends_are_credited_and_bonus_issues_scale_the_share_count():
    book = Book(cash=0.0, shares={"AAA": 100}, cost_basis={"AAA": 1000.0}, paid_in=MONTHLY,
                last_session="2026-09-28")
    actions = pd.DataFrame([{"date": "2026-09-29", "ticker": "AAA", "kind": "DIVIDEND", "value": 1.5},
                            {"date": "2026-09-29", "ticker": "AAA", "kind": "SPLIT", "value": 2.0}])
    process_session(book, "2026-09-29", _row(AAA=5.0), actions, [])
    assert book.cash == pytest.approx(150.0)
    assert book.shares["AAA"] == 200


def test_a_mark_values_both_books_on_the_same_close():
    book = Book(cash=100.0, shares={"AAA": 10}, paid_in=MONTHLY, index_units=1.5)
    row = mark(book, "2026-09-29", _row(AAA=20.0), {"AAA": 19.0})
    assert row["total"] == pytest.approx(300.0)
    assert row["xu100_book"] == pytest.approx(15_000.0)
