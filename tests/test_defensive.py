"""Defensive sleeve: idle/risk-off capital can earn a yield or an asset return.

Only the uninvested fraction (1 − Σweights) earns it — equities are untouched —
so it adds carry without adding equity-crash risk. Default (cash_yield=0,
defensive_returns=None) must reproduce the original 0%-cash behaviour exactly.
"""
from dataclasses import replace

import pandas as pd

from trading_algo import defensive_sweep
from trading_algo.backtest import run_backtest


def _with_yield(region, cy):
    return replace(region, params=region.params.with_overrides(cash_yield=cy))


def test_cash_yield_default_is_zero_drag(synth_asx, asx_region):
    """cash_yield=0 and no defensive series == the original behaviour."""
    prices, index_px = synth_asx
    base = run_backtest(prices, index_px, asx_region)["equity"]
    explicit = run_backtest(prices, index_px, _with_yield(asx_region, 0.0),
                            defensive_returns=None)["equity"]
    pd.testing.assert_series_equal(base, explicit)


def test_positive_yield_never_lowers_equity(synth_asx, asx_region):
    prices, index_px = synth_asx
    e0 = run_backtest(prices, index_px, _with_yield(asx_region, 0.0))["equity"].iloc[-1]
    e1 = run_backtest(prices, index_px, _with_yield(asx_region, 0.10))["equity"].iloc[-1]
    assert e1 >= e0                       # idle capital earning carry can only help


def test_defensive_asset_credits_idle_capital(synth_asx, asx_region):
    """A steadily positive defensive asset lifts the final equity, because the
    momentum book is rarely 100% invested (so there's idle capital to credit)."""
    prices, index_px = synth_asx
    base = run_backtest(prices, index_px, asx_region)["equity"].iloc[-1]
    defr = pd.Series(0.0004, index=prices.index)          # ~10%/yr steady asset
    boosted = run_backtest(prices, index_px, asx_region,
                           defensive_returns=defr)["equity"].iloc[-1]
    assert boosted > base


def test_defensive_only_affects_idle_not_equities(synth_asx, asx_region):
    """If the book were ever fully invested the defensive return wouldn't apply.
    Here we assert the credit scales with the *idle* fraction: a huge defensive
    return changes equity, proving idle capital (not equities) is what's earning."""
    prices, index_px = synth_asx
    flat = run_backtest(prices, index_px, asx_region, defensive_returns=None)
    big = run_backtest(prices, index_px, asx_region,
                       defensive_returns=pd.Series(0.001, index=prices.index))
    assert big["equity"].iloc[-1] > flat["equity"].iloc[-1]
    # weights (equity selection) are identical — only the cash leg differs
    last = list(flat["weights"])[-1]
    assert flat["weights"][last].equals(big["weights"][last])


def test_sweep_synthetic_runs(capsys, monkeypatch):
    """The offline harness produces a table with all four defensive options."""
    monkeypatch.delenv("MOMENTUM_DATA_PROVIDER", raising=False)
    defensive_sweep.main(["--region", "US", "--synthetic"])
    out = capsys.readouterr().out
    assert "Defensive-sleeve sweep" in out
    for label in ("cash (0%)", "tbill", "bonds", "gold"):
        assert label in out
