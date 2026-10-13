"""The one drawdown-breaker definition, shared by all four engines.

The defect this pins: `peak = max(peak, equity)` is never lowered, so a book
still below its pre-crash high after a full cooldown re-trips on the next bar
and stays off forever. The high-water mark must not rise while the book is
halted (it is in cash; it did not earn that high), and on RESUME it is re-based
to the equity the book actually has, so the cooled-off book is measured against
where it is rather than where it used to be.
"""
import math

import pandas as pd
import pytest

from trading_algo import risk_breaker
from trading_algo.backtest import run_backtest


def test_peak_does_not_rise_while_halted():
    assert risk_breaker.update_peak(100.0, 120.0, halted=False) == 120.0
    assert risk_breaker.update_peak(100.0, 120.0, halted=True) == 100.0


def test_a_book_that_has_not_recovered_may_trade_after_the_cooldown():
    """The test tests/test_backtest.py:36 fails to make.

    Equity falls 30% from 100 and then sits perfectly still. After the cooldown
    the book must be allowed to trade again, and must NOT re-trip on a flat
    curve — it has no new drawdown from where it now stands.
    """
    peak, halted, cooldown = 100.0, False, 0
    # The drop trips the breaker.
    peak, halted, cooldown = risk_breaker.breaker_step(
        equity=70.0, peak=peak, halted=halted, cooldown=cooldown,
        stop=0.25, cooldown_len=3)
    assert halted is True and cooldown == 3

    # Three flat bars of cooling off.
    for _ in range(3):
        peak, halted, cooldown = risk_breaker.breaker_step(
            equity=70.0, peak=peak, halted=halted, cooldown=cooldown,
            stop=0.25, cooldown_len=3)
    assert halted is False, "the cooldown expired; the book must be tradeable"
    assert peak == 70.0, "on resume the high-water mark re-bases to the equity"

    # Ten more flat bars: a book going nowhere must not be re-halted.
    for _ in range(10):
        peak, halted, cooldown = risk_breaker.breaker_step(
            equity=70.0, peak=peak, halted=halted, cooldown=cooldown,
            stop=0.25, cooldown_len=3)
        assert halted is False, "re-tripped on a flat curve — the peak latched"


def test_a_genuine_new_drawdown_after_a_resume_still_halts():
    """Re-basing the peak must not disarm the breaker — only re-aim it."""
    peak, halted, cooldown = 100.0, True, 1
    peak, halted, cooldown = risk_breaker.breaker_step(
        equity=70.0, peak=peak, halted=halted, cooldown=cooldown,
        stop=0.25, cooldown_len=3)
    assert halted is False and peak == 70.0
    # A fresh 30% fall from the NEW mark trips it again.
    peak, halted, cooldown = risk_breaker.breaker_step(
        equity=49.0, peak=peak, halted=halted, cooldown=cooldown,
        stop=0.25, cooldown_len=3)
    assert halted is True and cooldown == 3


def test_a_halt_lasts_exactly_cooldown_len_steps():
    """Tripped on one step, halted through the next `cooldown_len - 1`, and
    tradeable on the `cooldown_len`-th: not a step early, not a step late."""
    peak, halted, cooldown = risk_breaker.breaker_step(
        equity=70.0, peak=100.0, halted=False, cooldown=0,
        stop=0.25, cooldown_len=3)
    seen = []
    for _ in range(4):
        peak, halted, cooldown = risk_breaker.breaker_step(
            equity=70.0, peak=peak, halted=halted, cooldown=cooldown,
            stop=0.25, cooldown_len=3)
        seen.append(halted)
    assert seen == [True, True, False, False]


def test_a_fall_of_exactly_the_stop_trips():
    _, halted, cooldown = risk_breaker.breaker_step(
        equity=75.0, peak=100.0, halted=False, cooldown=0,
        stop=0.25, cooldown_len=3)
    assert halted is True and cooldown == 3


def test_a_non_finite_equity_never_becomes_the_mark():
    """Re-basing takes the equity at resume as the new mark. A NaN there would
    compare False against every stop from then on — a breaker disarmed for
    good, the opposite failure to the latch. The old mark is kept instead."""
    peak, halted, cooldown = risk_breaker.breaker_step(
        equity=float("nan"), peak=100.0, halted=True, cooldown=1,
        stop=0.25, cooldown_len=3)
    assert halted is False and peak == 100.0
    peak, halted, cooldown = risk_breaker.breaker_step(
        equity=70.0, peak=peak, halted=halted, cooldown=cooldown,
        stop=0.25, cooldown_len=3)
    assert halted is True, "the breaker must still be armed"


def test_a_disabled_stop_never_halts():
    peak, halted, cooldown = risk_breaker.breaker_step(
        equity=1.0, peak=100.0, halted=False, cooldown=0,
        stop=None, cooldown_len=3)
    assert halted is False and peak == 100.0


def _crash_then_flat_market(n_pre=420, n_crash=5, n_post=240, n_names=6):
    """A market that rises, crashes ~45% in a week, then drifts up gently and
    never regains its old high. Deterministic — no RNG, no network."""
    n = n_pre + n_crash + n_post
    idx = pd.bdate_range("2015-01-01", periods=n)
    out = {}
    for i in range(n_names):
        lvl = 100.0
        path = []
        for j in range(n):
            if j < n_pre:
                step = 0.0006 + 0.00002 * i
            elif j < n_pre + n_crash:
                step = -0.115
            else:
                step = 0.0004 + 0.00002 * i
            lvl *= 1 + step + 0.004 * math.sin((j + 7 * i) * 0.7)
            path.append(lvl)
        out[f"N{i}"] = path
    prices = pd.DataFrame(out, index=idx)
    return prices, prices.mean(axis=1)


def test_backtest_breaker_does_not_latch_on_a_market_that_never_recovers(asx_region):
    """One crash must produce ONE halt, not a halt every cooldown forever.

    With the peak latched at the pre-crash high the book is permanently below
    it, so it re-trips the bar after every cooldown expires: ~40 halts over the
    240 post-crash bars. With the mark re-based at resume the flat book has no
    drawdown and is never halted again.
    """
    prices, index_px = _crash_then_flat_market()
    res = run_backtest(prices, index_px, asx_region,
                       max_drawdown_stop=0.05, cooldown_days=5)
    assert res["drawdown_halts"] >= 1, "the crash must trip the breaker at all"
    assert res["drawdown_halts"] <= 2, (
        f"{res['drawdown_halts']} halts from one crash — the high-water mark "
        "latched and the book re-trips forever")
    assert res["drawdown_halt_days"] == 5 * res["drawdown_halts"], (
        "each halt sits out exactly the 5-day cooldown")


# --- the other three engines route through the same definition --------------
def test_fx_backtest_breaker_does_not_latch_on_a_market_that_never_recovers():
    """The FX backtester carried the same latch: the `matt` backtest showed
    hundreds of halts from it. One AUD-quoted pair (no AUD/quote translation)
    held long through the same crash-then-drift path.

    The weight frame is a fixture that isolates the BREAKER — constant 100%
    long — not a strategy: `weights=` is the backtester's seam for a weight
    history computed elsewhere, and here the thing under test is what happens
    after the weights, so the agents are deliberately out of the picture."""
    from trading_algo.forex.fx_backtest import run_backtest as fx_run_backtest
    from trading_algo.forex.fx_config import profile

    n_pre, n_crash, n_post = 300, 5, 240
    idx = pd.bdate_range("2015-01-01", periods=n_pre + n_crash + n_post)
    lvl, path = 1.5, []
    for j in range(len(idx)):
        step = (0.0006 if j < n_pre else -0.115 if j < n_pre + n_crash
                else 0.0004)
        lvl *= 1 + step + 0.004 * math.sin(j * 0.7)
        path.append(lvl)
    close = pd.Series(path, index=idx)
    panel = {"EURAUD": pd.DataFrame({"open": close, "high": close * 1.001,
                                     "low": close * 0.999, "close": close})}
    weights = pd.DataFrame({"EURAUD": 1.0}, index=idx)
    p = profile("balanced").with_overrides(max_drawdown_stop=0.05,
                                           drawdown_cooldown_days=5)
    # Large enough that the venue's minimum order size never blocks a fill.
    res = fx_run_backtest(panel, p, initial_capital=1_000_000.0, weights=weights)
    assert res["drawdown_halts"] >= 1, "the crash must trip the breaker at all"
    assert res["drawdown_halts"] <= 2, (
        f"{res['drawdown_halts']} halts from one crash — the FX backtest's "
        "high-water mark latched")
    assert res["drawdown_halt_days"] == 5 * res["drawdown_halts"], (
        "each halt sits out exactly the 5-bar cooldown")


def test_paper_book_resumes_measured_from_where_it_stands(tmp_path, monkeypatch,
                                                         paper_cycle):
    """A paper book whose cooldown runs out while it is far below its mark
    re-bases the mark to the equity it resumed at, and is not re-halted on the
    next session by a level it no longer has."""
    from trading_algo import paper_trade as pt

    monkeypatch.setattr(pt, "STATE_DIR", str(tmp_path))
    pt.init_account("t", capital=300_000, synthetic=True)
    state = paper_cycle("t")                          # opens positions
    state.update(risk_halted=True, halt_cooldown=1, halt_last_day="2000-01-03",
                 peak_equity_base=10 * state["initial_capital_base"])
    pt.save_state("t", state)

    state = paper_cycle("t", sessions=1)              # the cooldown runs out
    assert state["risk_halted"] is False
    resumed_at = state["equity_history"][-1][1]
    assert state["peak_equity_base"] == pytest.approx(resumed_at, abs=0.01), (
        "the mark kept the pre-halt level")

    state = paper_cycle("t", sessions=1)
    assert state["risk_halted"] is False, "re-halted by the pre-halt mark"


def test_fx_book_resumes_measured_from_where_it_stands(tmp_path, monkeypatch):
    """The live FX book: on the bar the cooldown runs out, the stored mark is
    the equity the book resumed at, not the level it fell from."""
    from trading_algo.forex import fx_book
    from trading_algo.forex.agents import AgentPool

    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    fx_book.init_defaults(synthetic=True)
    s = fx_book.load_state("matt")
    capital = float(s["initial_capital"])
    s.update(risk_halted=True, halt_cooldown=1, peak_equity=10 * capital)
    fx_book.save_state("matt", s)

    fx_book.run_once("matt", synthetic=True, pool=AgentPool(max_workers=1))
    s = fx_book.load_state("matt")
    assert s["risk_halted"] is False
    # A fresh book holds nothing over its first bar, so it marks at capital.
    assert s["peak_equity"] == pytest.approx(capital), (
        "the mark kept the pre-halt level")


def test_fx_book_stays_halted_until_the_last_bar_of_its_cooldown(tmp_path,
                                                                 monkeypatch):
    """Two bars of cooldown left: one new bar leaves the book halted with one
    to go (the bar after resumes it — `test_fx_book_resumes_measured_...`)."""
    from trading_algo.forex import fx_book
    from trading_algo.forex.agents import AgentPool

    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    fx_book.init_defaults(synthetic=True)
    s = fx_book.load_state("matt")
    s.update(risk_halted=True, halt_cooldown=2)
    fx_book.save_state("matt", s)

    fx_book.run_once("matt", synthetic=True, pool=AgentPool(max_workers=1))
    s = fx_book.load_state("matt")
    assert s["risk_halted"] is True and s["halt_cooldown"] == 1
    assert s["positions"] == {}


# --- one re-entry convention after a resume ----------------------------------
STOP, COOLDOWN = 0.05, 3


def _paper_and_backtest_through_a_resume(tmp_path, monkeypatch, runs_per_bar):
    """One ASX book, A$300,000, opened 2023-03-01 on synthetic data whose
    stocks (not the index, so the regime gate stays open) gap down 10% on the
    fifth bar: the paper book run bar by bar, `runs_per_bar` runs on each, and
    the backtest opened on the same bar. Returns (bars, dip, paper rows of
    (bar, holds positions, halted), backtest result)."""
    from trading_algo import config as cfg
    from trading_algo import paper_trade as pt
    from trading_algo.regions import get_region

    region = get_region("ASX")
    prices, index_px = pt.latest_region_data(region, True)
    bars = prices.index
    opened = bars.searchsorted(pd.Timestamp("2023-03-01"))
    dip = opened + 4
    prices = prices.copy()
    prices.iloc[dip:] *= 0.90

    clock = {"cut": opened + 1}
    monkeypatch.setattr(pt, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(cfg, "DRAWDOWN_COOLDOWN_DAYS", COOLDOWN)
    monkeypatch.setattr(pt, "latest_region_data", lambda r, synthetic: (
        prices.iloc[:clock["cut"]], index_px.iloc[:clock["cut"]]))
    pt.init_account("t", capital=300_000, synthetic=True, allocations={"ASX": 1.0})
    state = pt.load_state("t")
    state["max_drawdown_stop"] = STOP
    pt.save_state("t", state)

    rows = []
    for _ in range(4 + COOLDOWN + 5):
        for _ in range(runs_per_bar):
            pt.run_daily("t", synthetic=True)
        state = pt.load_state("t")
        rows.append((bars[clock["cut"] - 1],
                     bool(state["sleeves"]["ASX"]["positions"]),
                     bool(state.get("risk_halted"))))
        clock["cut"] += 1

    res = run_backtest(prices, index_px, region, initial_capital=300_000.0,
                       trade_from=bars[opened], max_drawdown_stop=STOP,
                       cooldown_days=COOLDOWN)
    return bars, dip, rows, res


@pytest.mark.parametrize("runs_per_bar", [1, 3])
def test_backtest_re_enters_after_a_resume_on_the_bar_the_paper_book_does(
        tmp_path, monkeypatch, runs_per_bar):
    """After a resume the paper book clears its month pins and decides on its
    next run, then fills at the close after (paper_trade, `transition ==
    "resume"`). The backtest used to wait for the next month-end — up to a
    month of different books per halt. One convention: the first bar after
    the resume decides, the next close fills, then the normal calendar."""
    bars, dip, rows, res = _paper_and_backtest_through_a_resume(
        tmp_path, monkeypatch, runs_per_bar)
    held_bt = {d: not res["weights"][d].empty for d in res["weights"]}

    # Both engines trip on the gap and exit at the next close.
    assert [d for d, _, halted in rows if halted][0] == bars[dip]
    assert held_bt[bars[dip]] and not held_bt[bars[dip + 1]]

    after_exit = [d for d, _, _ in rows if d > bars[dip + 1]]
    paper_back = next(d for d, held, _ in rows if d in after_exit and held)
    bt_back = next((d for d in after_exit if held_bt[d]), None)
    assert bt_back == paper_back, (
        f"paper re-entered {paper_back.date()}, the backtest "
        f"{bt_back.date() if bt_back else 'not by ' + str(after_exit[-1].date())}")

    # And on exactly the bars the cooldown says: halted at the close of the
    # gap and the COOLDOWN - 1 closes after it, resumed at the next, a
    # decision on the bar after that and its fill one close later.
    assert [d for d, _, halted in rows if halted] == list(bars[dip:dip + COOLDOWN])
    assert paper_back == bars[dip + COOLDOWN + 2]
