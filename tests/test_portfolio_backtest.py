"""Multi-sleeve portfolio backtest combined in AUD."""
import pandas as pd
import pytest

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


def test_each_sleeve_funded_at_allocation_in_local_currency(monkeypatch):
    """I-1 (fix round 1): each sleeve's run_backtest must be funded at
    initial_capital * alloc[k], converted to the sleeve's own LOCAL currency
    at a non-1.0 FX rate — not at a flat cfg.INITIAL_CAPITAL in local currency
    regardless of allocation. The per-order commission floor (Task 13) made
    the backtest size-dependent, so this now changes the measured cost."""
    import trading_algo.portfolio_backtest as pb

    captured: dict[str, float] = {}
    real_run_backtest = pb.run_backtest

    def spy(prices, index_px, region, **kwargs):
        captured[region.key] = kwargs["initial_capital"]
        return real_run_backtest(prices, index_px, region, **kwargs)

    monkeypatch.setattr(pb, "run_backtest", spy)

    # A hand-built, deterministic FX table: a NON-1.0 multiplier for every
    # sleeve currency, so "local" and "base" provably differ throughout.
    rate = {"USD": 2.0, "GBP": 4.0}

    def fake_synthetic_fx(currencies, start=None, end=None, base="AUD", seed=7):
        idx = pd.bdate_range(start, end)
        return pd.DataFrame({c: rate.get(c, 1.0) for c in currencies}, index=idx)

    monkeypatch.setattr(pb.fx, "synthetic_fx", fake_synthetic_fx)

    allocations = {"US": 0.6, "FTSE": 0.4}
    initial_capital = 50_000.0
    pb.run_portfolio_backtest(
        regions=["US", "FTSE"], synthetic=True,
        start="2016-01-01", end="2020-01-01",
        allocations=allocations, initial_capital=initial_capital)

    tot = sum(allocations.values())
    alloc = {k: v / tot for k, v in allocations.items()}
    assert captured["US"] == pytest.approx(initial_capital * alloc["US"] / rate["USD"])
    assert captured["FTSE"] == pytest.approx(initial_capital * alloc["FTSE"] / rate["GBP"])
    # not the un-scaled default, and not equal to each other despite a 0.6/0.4
    # allocation split — the FX rate must have actually been applied.
    assert captured["US"] != pytest.approx(cfg.INITIAL_CAPITAL)
    assert captured["US"] != pytest.approx(captured["FTSE"])
