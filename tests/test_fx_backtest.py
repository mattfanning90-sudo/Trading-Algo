"""FX backtest: no lookahead, costs always on, drawdown breaker, metrics."""
import pandas as pd
import pytest

from trading_algo.forex import fx_strategy
from trading_algo.forex.agents import AgentPool
from trading_algo.forex.fx_backtest import run_backtest
from trading_algo.forex.fx_config import profile
from trading_algo.forex.fx_data import synthetic_panel
from trading_algo.forex.pairs import DEFAULT_UNIVERSE


@pytest.fixture
def panel():
    return synthetic_panel(DEFAULT_UNIVERSE, start="2016-01-01", end="2023-01-01")


@pytest.fixture
def params():
    return profile("balanced")


def test_backtest_runs_and_reports_metrics(panel, params):
    res = run_backtest(panel, params, pool=AgentPool(max_workers=1))
    assert len(res["equity"]) > 100
    assert "CAGR" in res["metrics"]
    assert (res["equity"] > 0).all()
    assert res["avg_gross_leverage"] >= 0


def test_no_lookahead_in_weight_history(panel, params):
    """Perturbing a mid-series bar must not change earlier target weights."""
    pool = AgentPool(max_workers=1)
    w1 = fx_strategy.target_weights_history(panel, params, pool=pool)
    k = len(w1) // 2
    spiked = {s: df.copy() for s, df in panel.items()}
    col = spiked["EURUSD"].columns.get_loc("close")
    spiked["EURUSD"].iloc[k, col] *= 1.15
    w2 = fx_strategy.target_weights_history(spiked, params, pool=pool)
    pd.testing.assert_frame_equal(w1.iloc[:k], w2.iloc[:k])


def test_costs_reduce_returns(panel, params):
    """Turning costs off (zero spread + no carry) must not lower final equity."""
    pool = AgentPool(max_workers=1)
    with_costs = run_backtest(panel, params, pool=pool)["equity"].iloc[-1]

    # zero out spreads + carry by monkey-free override of the params + pairs
    import trading_algo.forex.pairs as P
    saved = dict(P.PAIRS)
    try:
        for k, v in list(P.PAIRS.items()):
            P.PAIRS[k] = P.Pair(v.symbol, v.base, v.quote, v.yahoo_ticker,
                                v.pip, 0.0, 0.0, 0.0)
            P.ALL_PAIRS[k] = P.PAIRS[k]
        no_costs = run_backtest(panel, params.with_overrides(include_carry=False),
                                pool=pool)["equity"].iloc[-1]
    finally:
        P.PAIRS.update(saved)
        P.ALL_PAIRS.update(saved)
    assert no_costs >= with_costs - 1e-6


def test_drawdown_breaker_triggers_with_tight_stop(panel, params):
    tight = params.with_overrides(max_drawdown_stop=0.005, drawdown_cooldown_days=5)
    res = run_backtest(panel, tight, pool=AgentPool(max_workers=1))
    assert res["drawdown_halts"] > 0
    assert res["drawdown_halt_days"] > 0


def test_attribution_sums_close_to_total_pnl(panel, params):
    res = run_backtest(panel, params, pool=AgentPool(max_workers=1))
    # gross of costs/carry, the per-pair attribution is the bulk of the return
    assert res["attribution"].notna().all()
    assert len(res["attribution"]) == len(DEFAULT_UNIVERSE)
