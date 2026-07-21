"""Backlog F5: monthly paper-book tearsheet."""
from trading_algo import attribution, tearsheet


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
