"""Per-sleeve backtester: runs clean on synthetic data, costs always on."""
import numpy as np
import pandas as pd
import pytest

from trading_algo import fees
from trading_algo.backtest import run_backtest


def test_backtest_runs_and_is_sane(synth_asx, asx_region):
    prices, index_px = synth_asx
    result = run_backtest(prices, index_px, asx_region)

    assert (result["equity"] > 0).all()
    assert not result["returns"].isna().any()
    assert np.isfinite(result["equity"].iloc[-1])

    m = result["metrics"]
    for key in ("CAGR", "AnnVol", "MaxDrawdown"):
        assert key in m
    assert -1.0 <= m["MaxDrawdown"] <= 0.0


def test_costs_are_charged(synth_asx, asx_region):
    """A non-trivial book must incur cost; cumulative cost > 0 if it traded."""
    prices, index_px = synth_asx
    result = run_backtest(prices, index_px, asx_region)
    if len(result["turnover"]) and result["turnover"].sum() > 0:
        assert result["total_cost_fraction"] > 0


def test_circuit_breaker_trips_and_limits_drawdown(synth_asx, asx_region):
    prices, index_px = synth_asx
    off = run_backtest(prices, index_px, asx_region, max_drawdown_stop=None)
    tight = run_backtest(prices, index_px, asx_region, max_drawdown_stop=0.05,
                         cooldown_days=21)
    assert off["drawdown_halts"] == 0
    assert tight["drawdown_halts"] >= 1
    assert tight["drawdown_halt_days"] > 0
    # the breaker should leave the worst drawdown no deeper than the no-stop run
    assert tight["metrics"]["MaxDrawdown"] >= off["metrics"]["MaxDrawdown"]


def test_circuit_breaker_disabled_by_default_param(synth_asx, asx_region):
    prices, index_px = synth_asx
    res = run_backtest(prices, index_px, asx_region, max_drawdown_stop=None)
    assert res["drawdown_halts"] == 0 and res["drawdown_halt_days"] == 0
    assert (res["equity"] > 0).all()


def test_ftse_stamp_duty_raises_cost(synth_asx):
    """Same synthetic prices, but the FTSE sleeve pays stamp duty on buys, so
    its cumulative cost must exceed an otherwise-identical no-duty region."""
    from trading_algo.regions import get_region
    prices, index_px = synth_asx

    ftse = get_region("FTSE")
    # reuse the ASX synthetic prices but price them through FTSE cost schedule
    ftse_like = ftse  # FTSE has stamp_duty_bps > 0
    asx = get_region("ASX")

    r_ftse = run_backtest(prices, index_px, ftse_like)
    r_asx = run_backtest(prices, index_px, asx)
    # Only compare when both actually traded
    if r_asx["turnover"].sum() > 0:
        assert fees.stamp_duty(ftse, 1.0) > fees.stamp_duty(asx, 1.0)
        assert r_ftse["total_cost_fraction"] >= r_asx["total_cost_fraction"]


def test_return_is_earned_by_the_previous_bars_book(synth_asx, asx_region):
    """Today's return belongs to the book held THROUGH today, i.e. the one
    recorded at yesterday's close.

    A target decided at D_k's close cannot be filled at that close — that is the
    very price that produced the signal. It is executed at D_{k+1}'s close, so
    the first bar it earns a return on is D_{k+2}. Restated as an identity the
    simulator must satisfy on every bar:

        returns[D] == weights_hist[D_prev] . rets[D] - cost[D] + interest[D]

    Before this change the right-hand book was `weights_hist[D]`, which is the
    same thing as filling at the signal's own close.
    """
    import pytest

    from trading_algo import config as cfg
    from trading_algo import fees

    prices, index_px = synth_asx
    res = run_backtest(prices, index_px, asx_region, max_drawdown_stop=None)
    weights_hist = res["weights"]
    costs = res["costs"]
    rets = prices.pct_change(fill_method=None)

    dates = list(res["returns"].index)
    checked = 0
    for prev, today in zip(dates, dates[1:]):
        book = weights_hist.get(prev)
        if book is None or book.empty:
            continue
        day = rets.loc[today].reindex(book.index).fillna(0.0)
        expected = float((book * day).sum())
        expected -= float(costs.get(today, 0.0))
        if cfg.CREDIT_IDLE_CASH and cfg.CASH_RATE_ANNUAL:
            expected += fees.idle_cash_credit(
                float(book.sum()), (today - prev).days, cfg.CASH_RATE_ANNUAL)
        assert float(res["returns"].loc[today]) == pytest.approx(expected, abs=1e-12), (
            f"bar {today.date()} was not earned by the book recorded at "
            f"{prev.date()}'s close")
        checked += 1
    assert checked > 100, "fixture produced too few invested bars to prove anything"


def test_small_book_pays_more_than_bps_because_of_the_floor(synth_asx, asx_region):
    """Same weights, same turnover, different NAV: the per-order floor is the
    dominant cost on a small book and invisible on a large one. Invariant #2 is
    about charging what the book would actually pay."""
    prices, index_px = synth_asx
    big = run_backtest(prices, index_px, asx_region, initial_capital=10_000_000)
    small = run_backtest(prices, index_px, asx_region, initial_capital=20_000)
    assert small["turnover"].sum() == pytest.approx(big["turnover"].sum())
    assert small["total_cost_fraction"] > big["total_cost_fraction"] * 1.2


# --- a book opened mid-sample (Task 13c) ------------------------------------
# The comparison a paper book is bridged against is "this strategy, run by this
# book, from the day it opened". History before that is WARM-UP: the signals
# read it, the book holds nothing. A Saturday, mid-month: no calendar mark is
# anywhere near it, so any decision on the next bar is the open rule's alone.
OPENED = pd.Timestamp("2019-03-16")


def _open_and_fill_bars(prices):
    loc = prices.index.searchsorted(OPENED)
    return prices.index[loc], prices.index[loc + 1]


def test_before_trade_from_the_sleeve_holds_its_capital_untouched(synth_asx, asx_region):
    """The book did not exist yet: no trade, no cost, no idle-cash interest. Its
    equity is EXACTLY the capital it is funded with on every bar before it."""
    prices, index_px = synth_asx
    res = run_backtest(prices, index_px, asx_region, initial_capital=25_000.0,
                       trade_from=OPENED)

    before = res["equity"][res["equity"].index < OPENED]
    assert len(before) > 1000, "the fixture must leave a long warm-up"
    assert (before == 25_000.0).all(), before[before != 25_000.0].head()
    assert (res["returns"][res["returns"].index < OPENED] == 0.0).all()
    assert res["turnover"].index.min() > OPENED
    assert res["costs"].index.min() > OPENED
    assert all(w.empty for d, w in res["weights"].items() if d < OPENED)


def test_the_first_bar_on_or_after_trade_from_decides_and_the_next_fills(
        synth_asx, asx_region):
    """Mirrors the paper book's first run: a sleeve that has never rebalanced
    decides on its first bar whatever the calendar says (`_should_rebalance`
    with no `last_rebalance_month`), and the fill follows the next-close
    convention (Task 9) like every other decision."""
    prices, index_px = synth_asx
    open_bar, fill_bar = _open_and_fill_bars(prices)
    res = run_backtest(prices, index_px, asx_region, initial_capital=25_000.0,
                       max_drawdown_stop=None, trade_from=OPENED)

    # decided at the open bar's close, filled at the next: nothing held, nothing
    # earned and nothing paid on the open bar itself
    assert res["equity"].loc[open_bar] == 25_000.0
    assert res["weights"][open_bar].empty
    assert res["turnover"].index[0] == fill_bar
    assert res["turnover"].iloc[0] > 0
    assert not res["weights"][fill_bar].empty


def test_the_open_bar_decision_is_compute_targets_as_of_that_bar(synth_asx, asx_region):
    """No lookahead, one weight function: the decision on the first bar is what
    `strategy.compute_targets` returns on prices up to AND INCLUDING that bar —
    exactly what the paper book's first run asks."""
    from trading_algo import data_quality, strategy

    prices, index_px = synth_asx
    open_bar, fill_bar = _open_and_fill_bars(prices)
    res = run_backtest(prices, index_px, asx_region, max_drawdown_stop=None,
                       trade_from=OPENED)

    seen = prices.loc[:open_bar]
    elig, _dq = data_quality.eligible(seen, asx_region, open_bar)
    expected = strategy.compute_targets(seen, index_px.loc[:open_bar],
                                        asx_region.params, eligible=elig)
    assert not expected.empty
    pd.testing.assert_series_equal(res["weights"][fill_bar].sort_index(),
                                   expected.sort_index(), check_names=False)


def test_trading_from_the_first_bar_is_the_whole_sample_backtest(synth_asx, asx_region):
    """`trade_from=None` is the backtest as it was. A book opened on the panel's
    first bar must reproduce it exactly too: the open bar has no history to
    decide on, and every later bar is live."""
    prices, index_px = synth_asx
    old = run_backtest(prices, index_px, asx_region)
    for res in (run_backtest(prices, index_px, asx_region, trade_from=None),
                run_backtest(prices, index_px, asx_region, trade_from=prices.index[0])):
        for key in ("equity", "returns", "turnover", "costs"):
            pd.testing.assert_series_equal(res[key], old[key], check_exact=True)
        assert res["total_cost_fraction"] == old["total_cost_fraction"]
        assert res["cash_interest_fraction"] == old["cash_interest_fraction"]
        assert res["weights"].keys() == old["weights"].keys()
        assert all(res["weights"][d].equals(old["weights"][d]) for d in old["weights"])
