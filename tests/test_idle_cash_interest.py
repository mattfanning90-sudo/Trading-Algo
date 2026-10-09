"""Interest credited on uninvested cash.

The sleeves sit 56-66% in cash (regime filter + vol targeting), and the reported
Sharpe subtracts RISK_FREE as a hurdle. Crediting 0% on that idle cash while
charging the full hurdle penalises the book twice for being flat — measured at
+0.22 to +0.34 Sharpe across the four sleeves (docs/SHARPE_RESEARCH.md §1).

The invariant that matters: a 100%-cash book must earn the cash rate, so its
EXCESS return is zero and its Sharpe is zero. That is the only coherent null for
the PSR/DSR machinery, which tests against "no skill".
"""
import numpy as np
import pandas as pd
import pytest

from trading_algo import config as cfg
from trading_algo import fees
from trading_algo.backtest import run_backtest


# --- the accrual helper ----------------------------------------------------
def test_fully_invested_book_earns_no_interest():
    assert fees.idle_cash_credit(1.0, 1, 0.035) == 0.0


def test_flat_book_earns_the_full_rate():
    # A year of calendar days at the annual rate, on 100% cash.
    assert fees.idle_cash_credit(0.0, 365, 0.035) == pytest.approx(0.035)


def test_half_invested_book_earns_half():
    a = fees.idle_cash_credit(0.5, 30, 0.04)
    b = fees.idle_cash_credit(0.0, 30, 0.04)
    assert a == pytest.approx(b / 2)


def test_levered_book_is_never_credited():
    """Σw > 1 is a margin DEBIT. The borrow charge is a separate, unmodelled
    cost (§7) — this helper must never pay a levered book, nor return a
    negative number that would silently become one."""
    for lev in (1.5, 3.0, 10.0):
        assert fees.idle_cash_credit(lev, 30, 0.035) == 0.0


def test_dollar_neutral_book_has_its_equity_on_deposit():
    """Longs consume cash, shorts generate it, so Σw = 0 means the whole equity
    sits as a credit balance — not that the book is un-invested."""
    neutral = fees.idle_cash_credit(0.0, 30, 0.035)   # long 1.0 / short 1.0
    flat = fees.idle_cash_credit(0.0, 30, 0.035)      # holding cash
    assert neutral == flat > 0


def test_zero_rate_is_a_no_op():
    assert fees.idle_cash_credit(0.0, 365, 0.0) == 0.0


# --- wired into the backtest ----------------------------------------------
def test_crediting_cash_raises_return_of_a_partly_flat_book(synth_asx, asx_region):
    prices, index_px = synth_asx
    on = run_backtest(prices, index_px, asx_region)
    gross = pd.Series({d: float(np.abs(w).sum()) for d, w in on["weights"].items()})
    if gross.mean() >= 0.999:
        return                      # nothing idle in this fixture; nothing to test
    assert cfg.CREDIT_IDLE_CASH, "default expected on"
    assert on["cash_interest_fraction"] > 0
    # The credit is bounded by the rate itself over the sample.
    years = len(on["returns"]) / 252
    assert on["cash_interest_fraction"] <= cfg.CASH_RATE_ANNUAL * years * 1.05


def test_credit_is_reported_and_reconciles(synth_asx, asx_region):
    """The accrual must be visible in the result, not folded in silently."""
    prices, index_px = synth_asx
    res = run_backtest(prices, index_px, asx_region)
    assert "cash_interest_fraction" in res
    assert res["cash_interest_fraction"] >= 0.0


# --- wired into the paper book --------------------------------------------
# The paper book pays the backtest's rule through the backtest's function, on
# the cash it held over the window since its last accrual, and books each
# credit as an `INT` ledger row: a cash event (`pnl.CASH_EVENTS`), not a fill.
def _accrue(sleeve, px, today, log=None):
    from trading_algo import paper_trade as pt
    from trading_algo.regions import get_region

    return pt._accrue_cash_interest(get_region("US"), sleeve, px, today,
                                    [] if log is None else log)


def test_paper_book_earns_the_same_rate_the_backtest_pays():
    """`full` is 72.8% cash. Paying 0% on that while the backtest credits
    CASH_RATE_ANNUAL is ~255bps/yr of divergence against a 200bps tracking
    budget — the single biggest engine disagreement in the review."""
    sleeve = {"currency": "USD", "cash": 7_000.0, "positions": {"AAA": 30},
              "last_interest_date": "2026-06-01"}
    px = pd.Series({"AAA": 100.0})                  # equity 10_000, 70% cash
    log = []

    credit = _accrue(sleeve, px, "2026-07-01", log)

    # 30 calendar days, ACT/365, on the CASH balance only
    assert credit == pytest.approx(7_000.0 * cfg.CASH_RATE_ANNUAL * 30 / 365)
    assert sleeve["cash"] == pytest.approx(7_000.0 + credit)
    assert sleeve["last_interest_date"] == "2026-07-01"
    assert log == [{"date": "2026-07-01", "region": "US", "ticker": "CASH",
                    "side": "INT", "shares": 1, "fill": credit,
                    "commission": 0.0, "stamp_duty": 0.0, "currency": "USD"}]


def test_paper_interest_is_the_backtest_definition_not_a_second_one():
    """Same function, same answer — the point of the change is one definition.
    backtest.py credits idle_cash_credit(Σw held through the bar, calendar
    days, rate) on the NAV the bar started from; here Σw = 0.75."""
    sleeve = {"currency": "USD", "cash": 2_500.0, "positions": {"AAA": 75},
              "last_interest_date": "2026-06-01"}
    px = pd.Series({"AAA": 100.0})                  # equity 10_000, Σw = 0.75
    credit = _accrue(sleeve, px, "2026-06-04")
    assert credit == pytest.approx(
        fees.idle_cash_credit(0.75, 3, cfg.CASH_RATE_ANNUAL) * 10_000.0)


def test_a_weekend_accrues_three_days(monkeypatch):
    """Friday's close to Monday's is one bar and three calendar days, as the
    backtest counts it: 10,000 at 3.65% is 1.00 a day, so 3.00."""
    monkeypatch.setattr(cfg, "CREDIT_IDLE_CASH", True)
    monkeypatch.setattr(cfg, "CASH_RATE_ANNUAL", 0.0365)
    sleeve = {"currency": "USD", "cash": 10_000.0, "positions": {},
              "last_interest_date": "2026-06-05"}       # a Friday
    assert _accrue(sleeve, pd.Series(dtype=float), "2026-06-08") == \
        pytest.approx(3.00, abs=1e-9)                  # the Monday


def test_paper_interest_does_not_double_accrue_within_one_session():
    """The engine fires up to three times a day, once per regional close."""
    sleeve = {"currency": "USD", "cash": 10_000.0, "positions": {},
              "last_interest_date": "2026-06-01"}
    px = pd.Series(dtype=float)
    log = []
    first = _accrue(sleeve, px, "2026-06-02", log)
    second = _accrue(sleeve, px, "2026-06-02", log)
    assert first > 0 and second == 0.0
    assert len(log) == 1


def test_an_earlier_bar_never_moves_the_window_back():
    """A feed can briefly serve an older last bar. Moving the window back to
    it would pay the days since then a second time on the next run."""
    sleeve = {"currency": "USD", "cash": 10_000.0, "positions": {},
              "last_interest_date": "2026-06-10"}
    assert _accrue(sleeve, pd.Series(dtype=float), "2026-06-08") == 0.0
    assert sleeve["last_interest_date"] == "2026-06-10"
    assert sleeve["cash"] == 10_000.0


def test_the_first_run_only_arms_the_window():
    """No prior accrual date, no window: back-paying a book's history is not
    something to do silently (the dividend credit's rule too)."""
    sleeve = {"currency": "USD", "cash": 10_000.0, "positions": {}}
    log = []
    assert _accrue(sleeve, pd.Series(dtype=float), "2026-06-02", log) == 0.0
    assert sleeve["last_interest_date"] == "2026-06-02"
    assert sleeve["cash"] == 10_000.0 and log == []


def test_a_levered_long_that_owes_cash_is_never_credited():
    """Long 12,000 on 10,000 of equity (Σw = 1.2) borrows 2,000: negative cash
    is a margin debit. The borrow charge is a separate, unmodelled cost and
    must not appear here as a negative credit."""
    sleeve = {"currency": "USD", "cash": -2_000.0, "positions": {"AAA": 120},
              "last_interest_date": "2026-06-01"}
    px = pd.Series({"AAA": 100.0})
    log = []
    assert _accrue(sleeve, px, "2026-07-01", log) == 0.0
    assert sleeve["cash"] == -2_000.0 and log == []


def test_a_net_short_books_proceeds_earn_the_full_rate_in_both_engines():
    """Under the net convention a short's proceeds sit in cash and earn the
    full rate. Short 5,000 on 10,000 of equity is 15,000 of cash: Σw = -0.5,
    idle 1.5. The backtest pays idle_cash_credit on the NET sum of its signed
    weights times its NAV; the paper pays the same on its sleeve. Both are
    15,000 x r x 30 / 365. (A real broker usually pays a lower rebate on
    short proceeds; neither engine models that.)"""
    sleeve = {"currency": "USD", "cash": 15_000.0, "positions": {"BBB": -50},
              "last_interest_date": "2026-06-01"}
    px = pd.Series({"BBB": 100.0})                  # equity 10,000; Σw = -0.5
    paper = _accrue(sleeve, px, "2026-07-01")
    current_w = pd.Series({"BBB": -0.5})            # the backtest's book
    backtest = fees.idle_cash_credit(float(current_w.sum()), 30,
                                     cfg.CASH_RATE_ANNUAL) * 10_000.0
    assert paper == pytest.approx(backtest)
    assert paper == pytest.approx(15_000.0 * cfg.CASH_RATE_ANNUAL * 30 / 365)


def test_an_interest_row_is_a_cash_event_and_never_a_fill():
    """Every ledger reader asks `pnl.is_fill` / `pnl.is_cash_event`. An INT
    row read as a trade is a SELL of a ticker called CASH: it would open a
    phantom short lot."""
    from trading_algo import pnl

    sleeve = {"currency": "USD", "cash": 10_000.0, "positions": {},
              "last_interest_date": "2026-06-01"}
    log = []
    credit = _accrue(sleeve, pd.Series(dtype=float), "2026-06-02", log)
    (row,) = log
    assert pnl.is_cash_event(row) and not pnl.is_fill(row)
    assert pnl.cash_amount(row) == credit
    assert "decision" not in row, "TCA counts a row with a decision as a fill"
    assert pnl.build_lots(log) == ({}, [])


def test_interest_accrues_before_this_runs_fill_on_the_cash_it_held(
        tmp_path, monkeypatch, paper_cycle):
    """Under the next-close fill the book decides on one bar and fills at the
    next. The window (decision bar, fill bar] was held ALL in cash — the fill
    happens at its end — so its credit is on the funded cash, accrued before
    the fill and before the dividend credit. The next window is held at what
    the fill left in cash."""
    from trading_algo import paper_trade as pt
    from trading_algo import pnl

    monkeypatch.setattr(pt, "STATE_DIR", str(tmp_path))
    pt.init_account("t", capital=300_000, synthetic=True, allocations={"US": 1.0})
    order = []
    real_int, real_div = pt._accrue_cash_interest, pt._credit_dividends

    def spy_int(region, sleeve, px, today, trade_log):
        order.append(("INT", dict(sleeve["positions"])))
        return real_int(region, sleeve, px, today, trade_log)

    def spy_div(region, sleeve, today, trade_log, synthetic, **kw):
        order.append(("DIV", dict(sleeve["positions"])))
        return real_div(region, sleeve, today, trade_log, synthetic, **kw)

    monkeypatch.setattr(pt, "_accrue_cash_interest", spy_int)
    monkeypatch.setattr(pt, "_credit_dividends", spy_div)

    funded = pt.load_state("t")["sleeves"]["US"]["cash"]
    decided = paper_cycle("t", sessions=1)["equity_history"][-1][0]
    state = paper_cycle("t", sessions=1)                         # the fill
    filled = state["equity_history"][-1][0]
    assert state["sleeves"]["US"]["positions"], "the second session must fill"
    assert order == [("INT", {}), ("DIV", {}), ("INT", {}), ("DIV", {})]

    rows = [t for t in state["trades"] if t["side"] == "INT"]
    days = (pd.Timestamp(filled) - pd.Timestamp(decided)).days
    assert [(t["date"], t["currency"]) for t in rows] == [(filled, "USD")]
    assert rows[0]["fill"] == pytest.approx(
        fees.idle_cash_credit(0.0, days, cfg.CASH_RATE_ANNUAL) * funded)
    assert pnl.is_fill(state["trades"][-1]) and state["trades"].index(rows[0]) == 0, \
        "the credit is booked before the fill it precedes"

    held = state["sleeves"]["US"]["cash"]                        # after the fill
    state = paper_cycle("t", sessions=1)
    later = state["equity_history"][-1][0]
    days = (pd.Timestamp(later) - pd.Timestamp(filled)).days
    last = [t for t in state["trades"] if t["side"] == "INT"][-1]
    assert last["date"] == later
    assert last["fill"] == pytest.approx(held * cfg.CASH_RATE_ANNUAL * days / 365)


# --- the convention's edges: net exposure, and a halted book ---------------
def test_a_long_short_sleeve_earns_on_its_net_cash_not_its_gross():
    """Σw is NET (fees.idle_cash_credit): a dollar-neutral sleeve, long 10,000
    and short 10,000 on 10,000 of equity, has all 10,000 on deposit and earns
    the full rate on it — 10,000 x r x 30/365. Read off GROSS (2.0) it would
    look levered and earn nothing."""
    sleeve = {"currency": "USD", "cash": 10_000.0,
              "positions": {"AAA": 100, "BBB": -100},
              "last_interest_date": "2026-06-01"}
    px = pd.Series({"AAA": 100.0, "BBB": 100.0})    # equity 10,000; net 0; gross 2
    credit = _accrue(sleeve, px, "2026-07-01")
    assert credit == pytest.approx(10_000.0 * cfg.CASH_RATE_ANNUAL * 30 / 365)


def test_the_experimental_long_short_book_earns_on_its_short_proceeds(
        tmp_path, monkeypatch, paper_cycle):
    """End to end on the real long/short profile. Once it holds shorts, its
    cash includes their proceeds and its gross is far above 1; the window's
    credit is still that whole cash balance x r x days / 365, as the
    backtest's net Σw pays it."""
    from trading_algo import paper_trade as pt

    monkeypatch.setattr(pt, "STATE_DIR", str(tmp_path))
    pt.init_account("e", capital=10_000, synthetic=True, profile="experimental")
    before = paper_cycle("e")                                 # decide, then fill
    sleeve = before["sleeves"]["US"]
    assert any(n < 0 for n in sleeve["positions"].values()), "needs a short"
    cash, last = sleeve["cash"], sleeve["last_interest_date"]

    state = paper_cycle("e", sessions=1)
    bar = state["equity_history"][-1][0]
    row = [t for t in state["trades"] if t["side"] == "INT"][-1]
    assert row["date"] == bar
    assert row["fill"] == pytest.approx(
        cash * cfg.CASH_RATE_ANNUAL * (pd.Timestamp(bar) - pd.Timestamp(last)).days / 365)


def test_a_halted_book_accrues_every_session_it_sits_out(
        tmp_path, monkeypatch, paper_cycle):
    """The breaker parks the book in cash; the backtest credits that cash
    every bar of the halt (its `current_w` is CASH). So does the paper: each
    halted session books its own INT row on the cash held since the last.
    Skipping while halted would leave the cash unpaid for those days — or pay
    them late, in one lump, on the first run after the cooldown."""
    from trading_algo import paper_trade as pt

    monkeypatch.setattr(pt, "STATE_DIR", str(tmp_path))
    pt.init_account("h", capital=300_000, synthetic=True, allocations={"US": 1.0})
    state = paper_cycle("h")                                  # open the book
    state["risk_halted"], state["halt_cooldown"] = True, 10
    pt.save_state("h", state)
    for _ in range(3):
        sleeve = pt.load_state("h")["sleeves"]["US"]
        cash, last = sleeve["cash"], sleeve["last_interest_date"]
        state = paper_cycle("h", sessions=1)
        assert state["risk_halted"] is True
        bar = state["equity_history"][-1][0]
        row = [t for t in state["trades"] if t["side"] == "INT"][-1]
        assert row["date"] == bar, "a halted session must book its own interest"
        assert row["fill"] == pytest.approx(
            cash * cfg.CASH_RATE_ANNUAL
            * (pd.Timestamp(bar) - pd.Timestamp(last)).days / 365)
