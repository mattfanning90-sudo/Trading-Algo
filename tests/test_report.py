"""Markdown backtest report."""
from trading_algo.portfolio_backtest import run_portfolio_backtest
from trading_algo.report import portfolio_markdown


def test_report_markdown_has_sections():
    result = run_portfolio_backtest(synthetic=True, start="2018-01-01", end="2021-01-01")
    md = portfolio_markdown(result, synthetic=True, point_in_time=False)
    for needle in ("# Multi-Region Momentum", "## Portfolio",
                   "vs Benchmark", "Per-sleeve", "Allocations",
                   "SYNTHETIC DATA"):
        assert needle in md
    # one row per sleeve in the per-sleeve table
    for k in ("ASX", "US", "FTSE"):
        assert f"| {k} |" in md
