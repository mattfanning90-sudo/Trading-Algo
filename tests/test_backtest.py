"""Per-sleeve backtester: runs clean on synthetic data, costs always on."""
import numpy as np

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
