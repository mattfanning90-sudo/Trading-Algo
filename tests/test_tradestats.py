"""Trade/period-level statistics — win rate done right."""
import numpy as np
import pandas as pd

from trading_algo import tradestats


def _monthly(values):
    """Build a daily series whose monthly compounded returns equal `values`:
    one nonzero move per distinct calendar month, the rest flat."""
    starts = pd.date_range("2015-01-01", periods=len(values), freq="MS")
    idx = pd.bdate_range(starts[0], starts[-1] + pd.offsets.MonthEnd(1), freq="B")
    r = pd.Series(0.0, index=idx)
    for s, v in zip(starts, values):
        r.iloc[r.index.searchsorted(s)] = v      # first business day of that month
    return r


def test_known_panel():
    # 6 monthly "bets": 4 wins (+10%), 2 losses (−5%)
    r = _monthly([0.10, 0.10, -0.05, 0.10, -0.05, 0.10])
    s = tradestats.trade_stats(r, period="ME")
    assert s["n_periods"] >= 6
    assert s["win_rate"] > 0.5
    # payoff = 0.10/0.05 = 2; breakeven = 1/(1+2) = 0.333
    assert abs(s["payoff_ratio"] - 2.0) < 0.2
    assert abs(s["breakeven_win_rate"] - 0.333) < 0.05
    assert s["profit_factor"] > 1.0
    assert s["expectancy"] > 0
    lo, hi = s["win_rate_95ci"]
    assert 0.0 <= lo <= s["win_rate"] <= hi <= 1.0


def test_losing_strategy_flagged():
    # high win rate but tiny wins and one huge loss → negative expectancy
    r = _monthly([0.01, 0.01, 0.01, 0.01, -0.10])
    s = tradestats.trade_stats(r)
    assert s["win_rate"] >= 0.6              # looks good...
    assert s["expectancy"] < 0              # ...but loses money
    assert s["edge_vs_breakeven"] < 0       # win rate below breakeven


def test_max_consecutive_losses():
    r = _monthly([0.05, -0.02, -0.02, -0.02, 0.05])
    s = tradestats.trade_stats(r)
    assert s["max_consec_losses"] == 3


def test_time_in_market():
    w_full = pd.Series({"A": 0.5, "B": 0.5})
    w_cash = pd.Series(dtype=float)
    hist = {1: w_full, 2: w_full, 3: w_cash, 4: w_cash}
    assert tradestats.time_in_market(hist) == 0.5
    assert tradestats.time_in_market({}) != tradestats.time_in_market({})  # nan != nan
