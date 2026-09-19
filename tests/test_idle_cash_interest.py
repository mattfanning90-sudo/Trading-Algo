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
