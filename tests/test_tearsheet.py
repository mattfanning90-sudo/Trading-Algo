"""Backlog F5: monthly paper-book tearsheet."""
import pandas as pd

from trading_algo import attribution, metrics, tearsheet
from trading_algo import config as cfg


def _state():
    return {
        "account": "full", "base_currency": "AUD", "initial_capital_base": 100_000,
        "equity_history": [["2026-06-11", 100_000.0], ["2026-06-20", 100_800.0],
                           ["2026-07-03", 100_510.0]],
        "sleeves": {
            "US": {"currency": "USD", "cash": 20_200.0, "positions": {"AAPL": 10}},
            "ASX": {"currency": "AUD", "cash": 33_333.0, "positions": {}},
        },
        "trades": [{"region": "US", "shares": 10, "decision": 100.0, "fill": 100.5,
                    "commission": 1.0, "currency": "USD"}],
    }


def test_tearsheet_has_sections_and_disclaimer():
    md = tearsheet.account_tearsheet(_state())
    assert "Paper Tearsheet — full" in md
    assert "PAPER TRADING" in md                      # disclaimer present
    for section in ("## Headline", "## Sleeves", "## Realized cost"):
        assert section in md


def test_total_return_reconciles_exactly():
    state = _state()
    md = tearsheet.account_tearsheet(state)
    exact = attribution.total_return(state["equity_history"])   # +0.51%
    assert f"{exact:+.2%}" in md                       # AC2: 0-tolerance reconciliation


def test_handles_empty_history():
    state = {"account": "new", "base_currency": "AUD", "initial_capital_base": 1000,
             "equity_history": [], "sleeves": {}, "trades": []}
    md = tearsheet.account_tearsheet(state)
    assert "Total return | +0.00%" in md               # no crash on a fresh book


def test_idle_sleeve_shows_zero_positions():
    md = tearsheet.account_tearsheet(_state())
    # ASX sleeve is all cash -> 0 positions in the table
    assert "| ASX |" in md and "| 0 |" in md


def test_tearsheet_routes_through_the_metrics_layer():
    """No local 252 and no hand-rolled drawdown loop: every figure on the
    tearsheet is the metrics primitive's value, to the last bit."""
    eh = _state()["equity_history"]
    rets = attribution.equity_returns(eh)
    ppy = metrics.periods_per_year(rets.index)
    equity = pd.Series([float(v) for _, v in eh],
                       index=pd.to_datetime([d for d, _ in eh])).sort_index()

    stats = tearsheet._annualised(eh)
    assert stats["periods_per_year"] == ppy
    assert stats["ann_vol"] == metrics.annualised_vol(rets, periods_per_year=ppy)
    assert stats["max_drawdown"] == metrics.max_drawdown(equity).value
    assert stats["sharpe"] == round(
        metrics.sharpe(rets, periods_per_year=ppy, risk_free=0.035).value, 2)


def test_tearsheet_rows_name_their_convention():
    """A reader cannot be shown a Sharpe without being told which Sharpe."""
    md = tearsheet.account_tearsheet(_state())
    assert "| Sharpe (vs 3.5%, 252/yr) |" in md
    assert "| Ann. vol (252/yr) |" in md
    assert "\u2248daily" not in md                     # the vague old label is gone


def test_tearsheet_annualises_an_hourly_history_at_its_own_spacing():
    """A book marked hourly must not be annualised as if its marks were days."""
    idx = pd.date_range("2026-06-01", periods=60, freq="h")
    vals = [10_000.0 * (1.0 + 0.0004 * ((i % 7) - 3)) ** i for i in range(60)]
    eh = [[d.strftime("%Y-%m-%d %H:%M"), v] for d, v in zip(idx, vals, strict=True)]
    stats = tearsheet._annualised(eh)
    ppy = stats["periods_per_year"]
    assert ppy > 2000                             # hourly ppy ~ 8766, not 252
    # The label alone is not the fix: the FIGURES must be annualised at the
    # ppy the page prints beside them, or an hourly book reads "8766/yr"
    # next to a number annualised at 252 — a mislabel no reader could catch.
    rets = attribution.equity_returns(eh)
    assert stats["ann_vol"] == metrics.annualised_vol(rets, periods_per_year=ppy)
    assert stats["sharpe"] == round(
        metrics.sharpe(rets, periods_per_year=ppy,
                       risk_free=cfg.RISK_FREE).value, 2)
    assert stats["sharpe_label"] == f"Sharpe (vs {cfg.RISK_FREE:.1%}, {ppy:g}/yr)"


def test_trades_to_date_counts_fills_not_dividends():
    s = _state()
    s["trades"].append({"region": "US", "ticker": "AAPL", "side": "DIV",
                        "shares": 10, "fill": 0.5, "commission": 0.0,
                        "currency": "USD"})
    assert "| Trades to date | 1 |" in tearsheet.account_tearsheet(s)
