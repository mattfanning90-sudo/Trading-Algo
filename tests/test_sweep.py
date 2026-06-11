"""Parameter robustness sweep."""
import pandas as pd

from trading_algo import data, sweep
from trading_algo.regions import get_region


def _short_synth():
    region = get_region("ASX")
    prices, index_px = data.synthetic_region(region, "2017-01-01", "2022-01-01")
    return region, prices, index_px


def test_sweep_grid_shape_and_values():
    region, prices, index_px = _short_synth()
    grid = sweep.sweep_region(region, prices, index_px,
                              top_ns=[8, 10], lookbacks=[126, 252], metric="sharpe")
    assert grid.shape == (2, 2)
    assert list(grid.columns) == [8, 10]
    assert grid.notna().to_numpy().any()   # at least some cells computed


def test_robustness_report_keys():
    region, prices, index_px = _short_synth()
    grid = sweep.sweep_region(region, prices, index_px,
                              top_ns=[8, 10, 12], lookbacks=[189, 252], metric="sharpe")
    rep = sweep.robustness_report(grid)
    for key in ("best", "best_params", "mean", "std", "cv", "pct_positive", "verdict"):
        assert key in rep
    assert rep["best_params"]["top_n"] in (8, 10, 12)


def test_metric_value_handles_sharpe_key():
    metrics = {"Sharpe (vs 3.5%)": 0.7, "CAGR": 0.1}
    assert sweep._metric_value(metrics, "sharpe") == 0.7
    assert sweep._metric_value(metrics, "CAGR") == 0.1
