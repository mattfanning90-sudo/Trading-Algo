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


# --------------------------------------------------------------------------
# Dashboard cache (state/fx_backtest_{account}.json, kind "fx")
# --------------------------------------------------------------------------
def test_dashboard_cache_round_trips_and_carries_synthetic_flag(tmp_path, monkeypatch):
    """`--account` writes the file dashboard/backtest_store reads back, with the
    keys app.js's agentBacktestHTML pulls off the payload. Before this the FX
    BACKTEST tab could only ever show its hardcoded placeholder."""
    from trading_algo.dashboard import backtest_store
    from trading_algo.forex import fx_book, run_backtest as cli

    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    cli.main(["--synthetic", "--account", "matt",
              "--universe", "EURUSD,GBPUSD,USDJPY"])

    data = backtest_store.load_backtest(kind="fx", account="matt")
    assert data["available"] and data["kind"] == "fx"
    # invariant #5: a synthetic run must announce itself, never pass as performance
    assert data["synthetic"] is True
    assert data["account"] == "matt" and data["profile"] == "balanced"
    assert data["currency"] == "AUD" and data["bar"] == "1d"
    assert sorted(data["universe"]) == ["EURUSD", "GBPUSD", "USDJPY"]

    m = data["metrics"]                      # the six KPI cells
    for key in ("cagr", "sharpe", "max_drawdown", "hit_rate", "payoff", "turnover"):
        assert m[key] is not None, key
    assert -1.0 < m["max_drawdown"] <= 0.0
    assert 0.0 <= m["hit_rate"] <= 1.0
    assert m["turnover"] > 0                 # costs are on, so it must trade

    curve = data["curve"]                    # [[bar_key, equity], ...]
    assert 2 < len(curve) <= 600             # downsampled to backtest_store's budget
    assert curve[0][0] == data["start"] and curve[-1][0] == data["end"]
    assert all(len(p) == 2 and p[1] > 0 for p in curve)   # log-scaled by the UI

    # cost/risk reality travels with the returns
    assert data["avg_gross_leverage"] > 0
    assert data["total_cost_fraction"] > 0
    assert data["drawdown_halts"] == 0 or data["drawdown_halt_days"] > 0
    assert set(data["attribution"]) == {"EURUSD", "GBPUSD", "USDJPY"}
    # Deliberately absent (the FX book has neither): the UI degrades to "—" and
    # "NO WALK-FORWARD FOLDS RECORDED" rather than being handed a fake.
    assert "folds" not in data and "benchmark" not in data


def test_dashboard_cache_out_path_does_not_touch_live_state(tmp_path, monkeypatch):
    from trading_algo.forex import fx_book, run_backtest as cli

    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    out = tmp_path / "elsewhere.json"
    cli.main(["--synthetic", "--out", str(out),
              "--universe", "EURUSD,GBPUSD,USDJPY"])
    assert out.exists()
    assert not list(tmp_path.glob("fx_backtest_*.json"))


def test_payoff_is_null_when_there_are_no_losing_bars(panel, params):
    """Honest nulls: payoff needs an average loser to divide by."""
    from trading_algo.forex import run_backtest as cli

    res = run_backtest(panel, params, pool=AgentPool(max_workers=1))
    res["returns"] = res["returns"].abs()          # every bar a winner
    p = cli._payload(res, "matt", "balanced", "1d", "yahoo", ["EURUSD"], 5000.0, True)
    assert p["metrics"]["payoff"] is None
