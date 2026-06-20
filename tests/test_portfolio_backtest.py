"""Multi-sleeve portfolio backtest combined in AUD."""
from trading_algo import config as cfg
from trading_algo.portfolio_backtest import run_portfolio_backtest


def test_portfolio_runs_on_synthetic():
    result = run_portfolio_backtest(synthetic=True, start="2016-01-01", end="2022-01-01")

    assert (result["equity"] > 0).all()
    assert set(result["sleeves"]) == set(cfg.ALLOCATIONS)
    assert set(result["sleeve_equity"].columns) == set(cfg.ALLOCATIONS)
    assert "CAGR" in result["metrics"]


def test_allocations_normalised():
    result = run_portfolio_backtest(synthetic=True, start="2016-01-01", end="2020-01-01")
    assert abs(sum(result["allocations"].values()) - 1.0) < 1e-9


def test_params_and_allocation_overrides():
    from trading_algo.config import DEFAULT_PARAMS
    base = run_portfolio_backtest(synthetic=True, start="2018-01-01", end="2021-01-01")
    over = run_portfolio_backtest(
        synthetic=True, start="2018-01-01", end="2021-01-01",
        params=DEFAULT_PARAMS.with_overrides(rebalance="QE", regime_filter=False, top_n=6),
        allocations={"US": 0.5, "ASX": 0.25, "FTSE": 0.25})
    # the override changes the equity path and records the tilted allocation
    assert abs(over["allocations"]["US"] - 0.5) < 1e-9
    assert not base["equity"].equals(over["equity"])


def test_portfolio_has_benchmark():
    result = run_portfolio_backtest(synthetic=True, start="2018-01-01", end="2021-01-01")
    assert (result["benchmark"] > 0).all()
    for k in ("BenchmarkCAGR", "Beta", "Alpha", "ActiveReturn", "InfoRatio"):
        assert k in result["benchmark_stats"]


def test_each_sleeve_has_base_returns():
    result = run_portfolio_backtest(synthetic=True, start="2016-01-01", end="2020-01-01")
    for s in result["sleeves"].values():
        assert "base_returns" in s
        assert not s["base_returns"].isna().any()
