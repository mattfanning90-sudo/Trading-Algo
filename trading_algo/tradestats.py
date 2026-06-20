"""Trade / period-level statistics — win rate done *right*.

Win rate in isolation is misleading: it trades off against payoff size (a 35%-win
trend system can beat a 70%-win mean-reversion system). So this reports the full
panel the research says matters — profit factor, payoff ratio, expectancy, the
*breakeven* win rate (what you'd need just to not lose money given your payoff), a
Wilson confidence interval on the win rate (because it's an estimate from a finite
sample), max consecutive losses, and a fractional-Kelly size.

Computed on PERIOD returns — each period is one "bet" for a periodically-rebalanced
book — so it needs no fragile per-name trade reconstruction. Use period="ME" for
monthly bets (the rebalance cadence). These are distinct from the daily-return
stats that feed Sharpe/drawdown (metrics.py); report both.

Identities used: expectancy = p·avgWin − (1−p)·avgLoss; profit factor =
ΣwIns/Σ|losses| = p·R/(1−p); payoff R = avgWin/avgLoss; breakeven p = 1/(1+R);
Kelly f* = p − (1−p)/R.
"""
from __future__ import annotations

import math

import pandas as pd


def _wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a proportion k/n (better than Wald for
    small n or win rates near 0/1)."""
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (centre - half, centre + half)


def _max_consecutive(mask) -> int:
    best = cur = 0
    for v in mask:
        cur = cur + 1 if v else 0
        best = max(best, cur)
    return best


def trade_stats(returns: pd.Series, period: str = "ME", flat_eps: float = 1e-6) -> dict:
    """Period-level trade statistics. `returns` = daily fractional returns.

    Periods within `flat_eps` of zero are treated as FLAT (the book sat in cash,
    not a bet) and excluded from the win/loss counts — otherwise cash months
    deflate the win rate and make it inconsistent with the profit factor. The
    share of flat periods is reported as `pct_flat`."""
    r = returns.dropna()
    allp = ((1 + r).resample(period).prod() - 1.0).dropna()
    n_total = len(allp)
    p = allp[allp.abs() > flat_eps]                  # active bets only
    n = len(p)
    if n == 0:
        return {"period": period, "n_periods": n_total, "n_active": 0,
                "pct_flat": 1.0 if n_total else float("nan")}
    wins, losses = p[p > 0], p[p < 0]
    nwin = int(len(wins))
    win_rate = nwin / n
    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(-losses.mean()) if len(losses) else 0.0          # positive
    gross_win, gross_loss = float(wins.sum()), float(-losses.sum())
    profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf")
    payoff = avg_win / avg_loss if avg_loss > 0 else float("inf")
    expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss
    breakeven = avg_loss / (avg_win + avg_loss) if (avg_win + avg_loss) > 0 else float("nan")
    kelly = (win_rate - (1 - win_rate) / payoff) if 0 < payoff < float("inf") else float("nan")
    lo, hi = _wilson(nwin, n)
    return {
        "period": period,
        "n_periods": n_total,
        "n_active": n,
        "pct_flat": round((n_total - n) / n_total, 3) if n_total else float("nan"),
        "win_rate": round(win_rate, 3),
        "win_rate_95ci": (round(lo, 3), round(hi, 3)),
        "avg_win": round(avg_win, 4),
        "avg_loss": round(avg_loss, 4),
        "payoff_ratio": round(payoff, 2),
        "profit_factor": round(profit_factor, 2),
        "expectancy": round(expectancy, 4),
        "breakeven_win_rate": round(breakeven, 3),
        "edge_vs_breakeven": round(win_rate - breakeven, 3) if breakeven == breakeven else float("nan"),
        "max_consec_losses": _max_consecutive(p < 0),
        "max_consec_wins": _max_consecutive(p > 0),
        "best_period": round(float(p.max()), 4),
        "worst_period": round(float(p.min()), 4),
        "kelly_fraction": round(kelly, 3) if kelly == kelly else float("nan"),
        "half_kelly": round(kelly / 2, 3) if kelly == kelly else float("nan"),
    }


def time_in_market(weights_hist: dict) -> float:
    """Fraction of days with any gross exposure (cash sits out). A book in cash
    most of the time has a very different risk profile than a fully-invested one."""
    if not weights_hist:
        return float("nan")
    invested = sum(1 for w in weights_hist.values()
                   if getattr(w, "abs", None) is not None and float(w.abs().sum()) > 1e-9)
    return round(invested / len(weights_hist), 3)
