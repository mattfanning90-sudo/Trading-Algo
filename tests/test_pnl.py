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
