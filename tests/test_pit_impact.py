"""Backlog F1: quantify survivorship bias (static vs point-in-time CAGR).

Task 27 extended pit_impact's return dict with a "per_region" entry (a dict,
not a float; see tests/test_run_backtest.py for its own contract), so this
pins the portfolio-level trio as a SUBSET of the keys rather than the exact
set, and checks float-ness only on those three rather than on every value.
"""
from trading_algo import run_backtest


def test_pit_impact_reports_static_pit_and_delta():
    imp = run_backtest.pit_impact(synthetic=True)
    assert {"static_cagr", "pit_cagr", "delta"}.issubset(imp)
    assert imp["delta"] == imp["static_cagr"] - imp["pit_cagr"]
    for k in ("static_cagr", "pit_cagr", "delta"):
        assert isinstance(imp[k], float)
