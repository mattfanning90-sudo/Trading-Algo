"""Backlog F1: quantify survivorship bias (static vs point-in-time CAGR)."""
from trading_algo import run_backtest


def test_pit_impact_reports_static_pit_and_delta():
    imp = run_backtest.pit_impact(synthetic=True)
    assert set(imp) == {"static_cagr", "pit_cagr", "delta"}
    assert imp["delta"] == imp["static_cagr"] - imp["pit_cagr"]
    for v in imp.values():
        assert isinstance(v, float)
