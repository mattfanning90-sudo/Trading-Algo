"""FIFO position accounting derived from the fills ledger (trading_algo.pnl)."""
import pytest

from trading_algo import pnl


def _buy(tkr, sh, px, comm=0.0, date="2026-06-01"):
    return {"date": date, "region": "US", "ticker": tkr, "side": "BUY",
            "shares": sh, "fill": px, "commission": comm, "stamp_duty": 0.0,
            "currency": "USD"}


def _sell(tkr, sh, px, comm=0.0, date="2026-07-01"):
    return {"date": date, "region": "US", "ticker": tkr, "side": "SELL",
            "shares": sh, "fill": px, "commission": comm, "stamp_duty": 0.0,
            "currency": "USD"}


def test_fifo_matches_oldest_lots_first():
    trades = [_buy("AAA", 10, 100.0), _buy("AAA", 10, 120.0), _sell("AAA", 15, 150.0)]
    open_lots, realized = pnl.build_lots(trades)
    assert len(realized) == 1
    r = realized[0]
    # FIFO: 10 @ 100 + 5 @ 120 -> entry avg 106.67, not the 110 average-cost blend
    assert r["entry"] == pytest.approx((10 * 100 + 5 * 120) / 15)
    assert r["gross"] == pytest.approx(15 * 150 - (10 * 100 + 5 * 120))
    assert r["left_over"] == 5
    # the 5 shares still held are the remainder of the second (120) lot
    assert pnl.open_basis(open_lots)[("US", "AAA")] == pytest.approx(120.0)


def test_realized_is_net_of_entry_and_exit_costs():
    trades = [_buy("AAA", 10, 100.0, comm=1.0), _sell("AAA", 10, 110.0, comm=1.0)]
    _, realized = pnl.build_lots(trades)
    r = realized[0]
    assert r["gross"] == pytest.approx(100.0)          # (110-100)*10
    assert r["net"] == pytest.approx(100.0 - 2.0)      # minus both commissions


def test_fully_closed_name_has_no_open_lot():
    trades = [_buy("AAA", 10, 100.0), _sell("AAA", 10, 130.0)]
    open_lots, realized = pnl.build_lots(trades)
    assert ("US", "AAA") not in pnl.open_basis(open_lots)
    assert realized[0]["net"] == pytest.approx(300.0)


def test_sell_without_matching_lot_is_ignored():
    # a sell with no prior buy (shouldn't happen for a real account) yields nothing
    _, realized = pnl.build_lots([_sell("AAA", 5, 100.0)])
    assert realized == []


def test_a_dividend_row_is_not_a_lot():
    """DIV moves cash, never shares. Treated as a fill it reads as a SELL and
    would close the whole position."""
    from trading_algo import pnl

    trades = [
        {"date": "2026-06-01", "region": "US", "ticker": "AAA", "side": "BUY",
         "shares": 10, "fill": 100.0, "commission": 1.0, "stamp_duty": 0.0,
         "currency": "USD"},
        {"date": "2026-06-15", "region": "US", "ticker": "AAA", "side": "DIV",
         "shares": 10, "fill": 0.5, "commission": 0.0, "stamp_duty": 0.0,
         "currency": "USD"},
    ]
    open_lots, realized = pnl.build_lots(trades)
    assert realized == []
    assert sum(abs(lot[0]) for lot in open_lots[("US", "AAA")]) == 10


def test_a_dividend_on_a_short_is_not_a_cover():
    """A short OWES the dividend, so its DIV row carries the signed (negative)
    share count — which a replay reading every non-BUY as a SELL turns into a
    BUY of 50 that covers the short."""
    trades = [_sell("BBB", 50, 20.0, date="2026-06-01"),
              {"date": "2026-06-15", "region": "US", "ticker": "BBB",
               "side": "DIV", "shares": -50, "fill": 0.2, "commission": 0.0,
               "stamp_duty": 0.0, "currency": "USD"}]
    open_lots, realized = pnl.build_lots(trades)
    assert realized == []
    assert sum(lot[0] for lot in open_lots[("US", "BBB")]) == -50
