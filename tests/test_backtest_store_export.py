"""The dashboard backtest cache contract.

The BACKTEST tab renders whatever `backtest_store.export_equity` writes, so this
pins the payload's shape: the six original metric keys must never move (the tab
reads them by name), and the widened export — WinRate, the benchmark-relative
block and the per-sleeve cost/turnover reality — must actually survive the trip
through JSON instead of being silently narrowed away again.
"""
import json

import pandas as pd
import pytest

from trading_algo import config as cfg
from trading_algo import portfolio_backtest
from trading_algo.dashboard import backtest_store

# The keys the existing BACKTEST tab reads. Renaming any of these breaks it.
STABLE = {"cagr", "ann_vol", "sharpe", "sortino", "max_drawdown", "calmar"}
BENCH_STATS = {"active_return", "beta", "alpha",
               "tracking_error", "info_ratio"}
SLEEVE_COSTS = {"avg_turnover", "total_cost_fraction",
                "drawdown_halts", "drawdown_halt_days", "data_quality_excluded"}


@pytest.fixture(scope="module")
def exported(tmp_path_factory):
    """A real (synthetic-data) export over a short window, read back from disk.

    Short window purely for speed — it still runs the real portfolio backtest,
    so the metric-key prefixes and the run_backtest result keys are the genuine
    contract, not a mock's idea of one.
    """
    real = portfolio_backtest.run_portfolio_backtest
    portfolio_backtest.run_portfolio_backtest = (
        lambda **kw: real(start="2020-01-01", end="2024-01-01", **kw))
    try:
        path = tmp_path_factory.mktemp("bt") / "cache.json"
        backtest_store.export_equity(synthetic=True, out_path=str(path))
    finally:
        portfolio_backtest.run_portfolio_backtest = real
    with open(path) as f:
        return json.load(f)


def test_out_path_leaves_the_live_cache_alone(exported):
    """--out exists so a synthetic pipeline test never lands where the dashboard
    reads it (invariant #5). `exported` wrote to a tmp path; prove it."""
    assert exported["synthetic"] is True
    live = backtest_store.load_backtest()
    assert live.get("synthetic") is not True


def test_stable_metric_keys_survive(exported):
    for block in (exported["metrics"], exported["benchmark_metrics"]):
        assert STABLE <= set(block)
        assert isinstance(block["sharpe"], float)
    for sleeve in exported["sleeves"]:
        assert STABLE <= set(sleeve)


def test_win_rate_is_exported(exported):
    """compute_metrics' WinRate(days) was being dropped by the old narrowing."""
    for block in (exported["metrics"], exported["benchmark_metrics"]):
        assert 0.0 <= block["win_rate"] <= 1.0


def test_benchmark_stats_reach_the_payload(exported):
    """Beta / alpha / tracking error answer 'does this beat owning the index'.
    They exist only at portfolio level — no sleeve is paired with its own index."""
    bs = exported["benchmark_stats"]
    assert set(bs) == BENCH_STATS
    assert all(isinstance(v, float) for v in bs.values())
    assert "beta" not in exported["sleeves"][0]


def test_sleeve_cost_and_turnover_reality(exported):
    for sleeve in exported["sleeves"]:
        assert SLEEVE_COSTS <= set(sleeve)
        assert 0.0 < sleeve["avg_turnover"] <= 2.0     # fraction of NAV traded
        assert sleeve["total_cost_fraction"] > 0       # invariant #2: costs on
        assert sleeve["drawdown_halt_days"] >= sleeve["drawdown_halts"]
        assert isinstance(sleeve["data_quality_excluded"], list)
    assert exported["fx_rebalance_cost"] >= 0
    assert set(exported["allocations"]) == {s["key"] for s in exported["sleeves"]}


def test_missing_benchmark_stats_export_nulls_not_zeros(monkeypatch, tmp_path):
    """benchmark_stats returns {} when the series barely overlap. The cache must
    say 'unavailable' (null), never a plausible-looking 0.0 beta."""
    idx = pd.bdate_range("2024-01-01", periods=5)
    eq = pd.Series([100.0, 101.0, 102.0, 101.0, 103.0], index=idx)
    stub = {
        "equity": eq, "benchmark": eq, "metrics": {}, "benchmark_metrics": {},
        "benchmark_stats": {}, "allocations": {}, "fx_rebalance_cost": 0.0,
        "sleeves": {},
    }
    monkeypatch.setattr(portfolio_backtest, "run_portfolio_backtest",
                        lambda **kw: stub)
    path = tmp_path / "cache.json"
    backtest_store.export_equity(synthetic=True, out_path=str(path))
    with open(path) as f:
        out = json.load(f)
    assert set(out["benchmark_stats"]) == BENCH_STATS
    assert all(v is None for v in out["benchmark_stats"].values())
    assert all(v is None for v in out["metrics"].values())


def test_overfitting_gate_shape_is_stable_when_it_fails(monkeypatch):
    """The gate's failure paths carry only a verdict. The UI keys off dsr / pbo /
    passed, so those must always be present — null, never a silent pass."""
    from trading_algo import data

    monkeypatch.setattr(data, "synthetic_region",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no data")))
    gates = backtest_store._overfitting_gates(True, False)
    assert set(gates) == set(cfg.ALLOCATIONS)
    for gate in gates.values():
        assert gate["dsr"] is None and gate["pbo"] is None
        assert gate["passed"] is None
        assert gate["verdict"].startswith("error:")
