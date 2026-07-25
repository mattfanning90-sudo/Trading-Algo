"""Live-book end-to-end verification (`trading_algo.verify`).

These tests pin the *detectors*, not the current state of the real books: each
one builds a minimal book exhibiting exactly one defect and asserts the matching
finding fires (and, just as importantly, that a clean book stays silent).
"""
from datetime import datetime

import pytest

from trading_algo import verify


NOW = datetime(2026, 7, 27)


def fx_book(trades, **over):
    state = {"account": "t", "currency": "AUD", "profile": "balanced",
             "bar": "1d", "initial_capital": 10_000.0, "equity": 10_000.0,
             "positions": {}, "last_bar_date": "2026-07-24", "trades": trades}
    state.update(over)
    return state


def equity_book(trades, sleeves, **over):
    state = {"account": "t", "base_currency": "AUD",
             "initial_capital_base": 10_000.0, "allocations": {"US": 1.0},
             "sleeves": sleeves, "trades": trades,
             "equity_history": [["2026-07-24", 10_000.0]],
             "fx_snapshot": {"USD": 1.5}}
    state.update(over)
    return state


def codes(findings):
    return {f.code for f in findings}


# ---------------------------------------------------------------------------
# REALISM
# ---------------------------------------------------------------------------
def test_weekend_fx_trade_is_flagged():
    """2026-07-04 is a Saturday — no venue was quoting EURUSD."""
    out = verify.check_market_hours("t", fx_book([
        {"date": "2026-07-04 03:00", "pair": "EURUSD", "price": 1.14,
         "target_weight": 0.2, "delta_weight": 0.2}]), "fx")
    assert codes(out) == {"closed-market-trade"}
    assert out[0].detail["by_symbol"] == {"EURUSD": 1}


def test_friday_after_the_fx_close_counts_as_closed():
    """FX settles into the weekend at ~22:00 UTC Friday; 2026-07-03 is a Friday."""
    out = verify.check_market_hours("t", fx_book([
        {"date": "2026-07-03 23:00", "pair": "EURUSD", "price": 1.14,
         "target_weight": 0.2, "delta_weight": 0.2}]), "fx")
    assert codes(out) == {"closed-market-trade"}


def test_crypto_may_trade_at_the_weekend():
    """Crypto genuinely is 24/7, so a Saturday BTC fill is not a defect."""
    out = verify.check_market_hours("t", fx_book([
        {"date": "2026-07-04 03:00", "pair": "BTCUSD", "price": 60_000.0,
         "target_weight": 0.2, "delta_weight": 0.2}]), "fx")
    assert out == []


def test_sunday_evening_reopen_is_allowed():
    """The FX week restarts ~22:00 UTC Sunday; 2026-07-05 is a Sunday."""
    out = verify.check_market_hours("t", fx_book([
        {"date": "2026-07-05 23:00", "pair": "EURUSD", "price": 1.14,
         "target_weight": 0.2, "delta_weight": 0.2}]), "fx")
    assert out == []


def test_reweighting_at_a_forward_filled_price_is_flagged():
    """Two weight changes at an identical price = spread paid for no exposure."""
    out = verify.check_dead_price("t", fx_book([
        {"date": "2026-07-06", "pair": "EURUSD", "price": 1.14,
         "target_weight": 0.20, "delta_weight": 0.20},
        {"date": "2026-07-07", "pair": "EURUSD", "price": 1.14,
         "target_weight": -0.10, "delta_weight": -0.30}]))
    assert codes(out) == {"dead-price-turnover"}
    assert out[0].detail["by_symbol"] == {"EURUSD": 1}


def test_a_moving_price_is_not_dead_price_turnover():
    out = verify.check_dead_price("t", fx_book([
        {"date": "2026-07-06", "pair": "EURUSD", "price": 1.14,
         "target_weight": 0.2, "delta_weight": 0.2},
        {"date": "2026-07-07", "pair": "EURUSD", "price": 1.15,
         "target_weight": 0.1, "delta_weight": -0.1}]))
    assert out == []


def test_fractional_shares_and_free_trades_break_the_invariants():
    """Invariants #4 (whole shares) and #2 (costs always on)."""
    book = equity_book([{"date": "2026-07-06", "region": "US", "ticker": "AAPL",
                         "side": "BUY", "shares": 2.5, "fill": 100.0,
                         "commission": 0.0, "stamp_duty": 0.0}], {})
    assert codes(verify.check_whole_shares("t", book)) == {"fractional-shares"}
    assert codes(verify.check_costs_charged("t", book)) == {"uncosted-trade"}


# ---------------------------------------------------------------------------
# RECONCILE
# ---------------------------------------------------------------------------
def test_ledger_replay_reproduces_a_consistent_sleeve():
    """10_000 AUD at 1.5 = 6_666.67 USD funded; buy 10 @ 100 + 1 fee."""
    sleeve = {"currency": "USD", "cash": 6_666.666666 - 1001.0,
              "positions": {"AAPL": 10.0}}
    out = verify.reconcile_equity("t", equity_book(
        [{"date": "2026-07-06", "region": "US", "ticker": "AAPL", "side": "BUY",
          "shares": 10, "fill": 100.0, "commission": 1.0, "stamp_duty": 0.0}],
        {"US": sleeve}))
    assert out == []


def test_positions_that_the_ledger_cannot_explain_are_an_error():
    """A holding with no trade behind it means something wrote outside the ledger."""
    sleeve = {"currency": "USD", "cash": 6_666.666666, "positions": {"TSLA": 5.0}}
    out = verify.reconcile_equity("t", equity_book([], {"US": sleeve}))
    assert "position-mismatch" in codes(out)


def test_fx_weight_ledger_must_match_stored_positions():
    out = verify.reconcile_fx("t", fx_book(
        [{"date": "2026-07-06", "pair": "EURUSD", "price": 1.14,
          "target_weight": 0.25, "delta_weight": 0.25}],
        positions={"EURUSD": 0.10}))
    assert "weight-mismatch" in codes(out)


# ---------------------------------------------------------------------------
# LIVENESS
# ---------------------------------------------------------------------------
def test_funded_sleeve_that_never_traded_is_an_error():
    """The live `full` book's ASX sleeve: a third of the capital, zero trades."""
    book = equity_book([], {"ASX": {"currency": "AUD", "cash": 33_333.0,
                                    "positions": {}, "last_status": {}}},
                       allocations={"ASX": 1.0})
    assert "never-traded" in codes(verify.check_liveness("t", book, "equity", NOW))


def test_flat_sleeve_already_stamped_for_the_month_is_pinned():
    """`_should_rebalance` is False for the rest of the month, so it stays flat."""
    book = equity_book([{"date": "2026-07-01", "region": "US", "ticker": "SLV",
                         "side": "BUY", "shares": 3, "fill": 60.0,
                         "commission": 1.0, "stamp_duty": 0.0}],
                       {"US": {"currency": "USD", "cash": 600.0, "positions": {},
                               "last_rebalance_month": "2026-07",
                               "last_status": {"date": "2026-07-24",
                                               "status": "cash:idle"}}})
    assert "pinned-flat" in codes(verify.check_liveness("t", book, "equity", NOW))


def test_a_book_whose_bars_stopped_advancing_is_an_error():
    out = verify.check_liveness("t", fx_book([], last_bar_date="2026-06-01"),
                                "fx", NOW)
    assert "stale-book" in codes(out)


def test_a_sleeve_idle_for_weeks_is_surfaced():
    book = equity_book([{"date": "2026-05-01", "region": "US", "ticker": "SLV",
                         "side": "BUY", "shares": 3, "fill": 60.0,
                         "commission": 1.0, "stamp_duty": 0.0}],
                       {"US": {"currency": "USD", "cash": 600.0, "positions": {},
                               "last_status": {"date": "2026-07-24",
                                               "status": "cash:idle",
                                               "flat_since": "2026-05-02"}}})
    assert "idle-sleeve" in codes(verify.check_liveness("t", book, "equity", NOW))


# ---------------------------------------------------------------------------
# HORIZON
# ---------------------------------------------------------------------------
def test_round_trips_are_reconstructed_from_the_weight_ledger():
    trips = verify.round_trips(fx_book([
        {"date": "2026-07-06", "pair": "EURUSD", "price": 1.00,
         "target_weight": 0.25, "delta_weight": 0.25},
        {"date": "2026-07-10", "pair": "EURUSD", "price": 1.10,
         "target_weight": 0.0, "delta_weight": -0.25}]))
    assert len(trips) == 1
    sym, bars, gross, weight = trips[0]
    assert sym == "EURUSD"
    assert bars == pytest.approx(4.0)          # 4 daily bars
    assert gross == pytest.approx(0.10)        # long, +10%
    assert weight == pytest.approx(0.25)


def test_a_short_hold_against_a_long_signal_is_flagged():
    """`balanced` trades a ~57-bar breakout/momentum window; 1-bar holds don't
    give that signal a chance to resolve."""
    trades = []
    for i in range(6):
        trades += [
            {"date": f"2026-07-{2 * i + 1:02d}", "pair": "EURUSD", "price": 1.10,
             "target_weight": 0.2, "delta_weight": 0.2},
            {"date": f"2026-07-{2 * i + 2:02d}", "pair": "EURUSD", "price": 1.10,
             "target_weight": 0.0, "delta_weight": -0.2}]
    out = verify.check_horizon("t", fx_book(trades))
    assert "hold-shorter-than-signal" in codes(out)
    detail = next(f for f in out if f.code == "hold-shorter-than-signal").detail
    assert detail["horizon_bars"] == 57
    assert detail["median_bars"] == pytest.approx(1.0)


def test_horizon_check_is_silent_without_enough_round_trips():
    """Four round-trips is noise, not evidence."""
    assert verify.check_horizon("t", fx_book([
        {"date": "2026-07-01", "pair": "EURUSD", "price": 1.1,
         "target_weight": 0.2, "delta_weight": 0.2},
        {"date": "2026-07-02", "pair": "EURUSD", "price": 1.1,
         "target_weight": 0.0, "delta_weight": -0.2}])) == []


def test_intraday_profile_uses_its_own_shorter_horizon():
    """The horizon is read from the book's profile, not hardcoded."""
    assert verify.signal_horizon({"profile": "intraday"})[0] < \
           verify.signal_horizon({"profile": "balanced"})[0]


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------
def test_a_clean_book_produces_no_errors_or_warnings():
    book = fx_book([
        {"date": "2026-07-06", "pair": "EURUSD", "price": 1.10,
         "target_weight": 0.25, "delta_weight": 0.25},
        {"date": "2026-07-24", "pair": "EURUSD", "price": 1.15,
         "target_weight": 0.25, "delta_weight": 0.0}],
        positions={"EURUSD": 0.25})
    out = verify.verify_book("fx", "t", book, today=NOW)
    assert [f for f in out if f.level in (verify.ERROR, verify.WARN)] == []


def test_verify_all_reads_the_real_books_without_crashing():
    """Smoke test against whatever state is committed in the repo."""
    results = verify.verify_all()
    assert results, "no paper books discovered"
    for findings in results.values():
        for f in findings:
            assert f.level in (verify.ERROR, verify.WARN, verify.INFO)
            assert f.message
