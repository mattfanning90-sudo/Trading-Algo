"""Shared long/short walk-forward backtest engine.

Both the trend sleeve (signal = time-series momentum) and the carry sleeve
(signal = cross-asset yield) are signed, inverse-vol-sized, vol-targeted L/S
books over a basket of ETFs. The *signal* differs; the execution mechanics —
decide weights at month-end t, apply them at t+1, charge commission+slippage on
turnover, drift held positions daily — are identical. This module is that single
engine, so neither sleeve copies the loop (mirrors the equity sleeves' "one
weight function" invariant for the L/S side).

No lookahead: the caller supplies a `schedule` of {asof_date -> target weights}
where each entry was computed from data <= asof_date; the engine applies an
entry's weights only from the day AFTER asof_date.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .metrics import compute_metrics


def run_ls_backtest(prices: pd.DataFrame, schedule: dict, cost_bps: float,
                    sleeve: str, currency: str = "USD",
                    initial_capital: float = 100_000.0) -> dict:
    """Walk-forward a signed weight `schedule` over `prices` (costs always on).

    `schedule`: {asof_timestamp -> target weight Series}; weights decided at the
    asof date are executed the next trading day. `cost_bps` is per side
    (commission+slippage); turnover is gross (|Δ long| + |Δ short|). Returns the
    same dict shape as the equity/trend backtesters."""
    prices = prices.dropna(how="all")
    rets = prices.pct_change(fill_method=None)
    cost_rate = 2.0 * cost_bps / 1e4          # round-trip on turnover
    dates = prices.index

    current_w = pd.Series(dtype=float)
    equity = [initial_capital]
    daily_ret: list[float] = []
    turnover_log, cost_log = [], []
    weights_hist: dict[pd.Timestamp, pd.Series] = {}
    total_cost = 0.0
    pending: pd.Series | None = None

    for i in range(1, len(dates)):
        today, yday = dates[i], dates[i - 1]
        cost = 0.0
        if pending is not None:
            names = current_w.index.union(pending.index)
            delta = (pending.reindex(names, fill_value=0.0)
                     - current_w.reindex(names, fill_value=0.0))
            turnover = float(delta.abs().sum())
            cost = turnover * cost_rate
            turnover_log.append((today, turnover))
            cost_log.append((today, cost))
            total_cost += cost
            current_w = pending
            pending = None

        day_rets = rets.loc[today].reindex(current_w.index).fillna(0.0)
        r = float((current_w * day_rets).sum()) - cost
        r = max(r, -0.999)            # guard: a book can't lose >100% in a day
        daily_ret.append(r)
        equity.append(equity[-1] * (1 + r))
        weights_hist[today] = current_w

        if yday in schedule:
            pending = schedule[yday]

        # Drift held (signed) positions with the day's returns.
        if not current_w.empty:
            grown = current_w * (1 + day_rets)
            nav = 1.0 + float((current_w * day_rets).sum())
            current_w = grown / nav if nav != 0 else grown

    ret_series = pd.Series(daily_ret, index=dates[1:])
    eq = pd.Series(equity[1:], index=dates[1:])
    avg_gross = float(pd.DataFrame(weights_hist).abs().sum().mean()) if weights_hist else 0.0
    return {
        "sleeve": sleeve,
        "returns": ret_series,
        "equity": eq,
        "turnover": pd.Series(dict(turnover_log)),
        "costs": pd.Series(dict(cost_log)),
        "total_cost_fraction": total_cost,
        "avg_gross_exposure": avg_gross,
        "weights": weights_hist,
        "metrics": compute_metrics(ret_series, eq, currency=currency),
    }
