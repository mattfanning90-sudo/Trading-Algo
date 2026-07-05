"""Per-sleeve walk-forward backtest (one region, local currency).

Design principles (the invariants from CLAUDE.md):
- No lookahead: weights are decided at month-end t using data ≤ t, and applied
  from the next trading day t+1. Targets come from the shared
  `strategy.compute_targets` — the same function paper trading uses.
- Costs always on: commission + slippage charged on turnover every rebalance,
  plus UK stamp duty on the buy side (asymmetric).
- Returns are fractional, so the series is currency-agnostic; the portfolio
  layer converts to the base currency via FX.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import data_quality, fees
from . import strategy
from .config import DRAWDOWN_COOLDOWN_DAYS, INITIAL_CAPITAL, MAX_DRAWDOWN_STOP
from .metrics import compute_metrics
from .regions import Region


def run_backtest(prices: pd.DataFrame, index_prices: pd.Series, region: Region,
                 initial_capital: float = INITIAL_CAPITAL,
                 membership=None,
                 max_drawdown_stop: float | None = MAX_DRAWDOWN_STOP,
                 cooldown_days: int = DRAWDOWN_COOLDOWN_DAYS,
                 defensive_returns: pd.Series | None = None,
                 apply_delisting: bool = False) -> dict:
    """Walk-forward backtest for one sleeve.

    `membership` (a constituents.MembershipTable) makes selection point-in-time:
    at each rebalance only names in the index as-of that date are eligible. When
    None the current universe is used (survivorship-biased).

    `max_drawdown_stop` is a circuit breaker: if equity falls more than this from
    its peak, the book liquidates to cash and sits out for `cooldown_days` before
    resuming. Pass None to disable.

    `defensive_returns` is the daily fractional return of the asset the idle /
    risk-off fraction of the book is parked in (e.g. a bond or gold ETF, in the
    sleeve's own currency). When None the idle fraction earns the constant
    `params.cash_yield` instead (0% by default = plain cash). Either way, only
    the *uninvested* fraction (1 − Σweights) earns it — equities are unaffected,
    so this adds carry without adding equity-crash risk.

    `apply_delisting` (backlog F13) injects a delisting return for names whose
    price series terminates before the sample end — only meaningful with real
    point-in-time data that includes since-delisted names."""
    p = region.params
    prices = prices.dropna(how="all")
    if apply_delisting:
        from . import delisting
        prices = delisting.apply_delisting_returns(prices, region)
    rets = prices.pct_change(fill_method=None)
    # Constant fallback yield as a per-trading-day rate (compounded to annual).
    daily_cash_yield = (1.0 + p.cash_yield) ** (1.0 / 252.0) - 1.0
    if defensive_returns is not None:
        defensive_returns = defensive_returns.reindex(prices.index).fillna(0.0)

    # Rebalance dates = last trading day on or before each period end.
    rebal_marks = prices.resample(p.rebalance).last().index
    min_hist = p.min_history_days
    if p.use_value:                              # need the long-term-reversal window
        min_hist = max(min_hist, p.value_lookback_days + 5)
    if len(prices) <= min_hist:
        raise ValueError(f"{region.key}: not enough history ({len(prices)} rows)")

    # Build the heavy indicator frames once and reuse them for every rebalance
    # (they're causal, so per-date .loc reads are identical to recomputing —
    # see strategy.precompute). Keeps compute_targets the single weight builder.
    signals_cache = strategy.precompute(prices, index_prices, p)

    weight_schedule: dict[pd.Timestamp, pd.Series] = {}
    dq_excluded: set[str] = set()
    for d in rebal_marks:
        loc_idx = prices.index.searchsorted(d, side="right") - 1
        if loc_idx < min_hist:
            continue
        asof = prices.index[loc_idx]
        base_elig = membership.members_asof(asof) if membership is not None else None
        eligible, dq = data_quality.eligible(prices, region, asof, base_elig)
        dq_excluded |= dq.excluded
        weight_schedule[asof] = strategy.compute_targets(
            prices, index_prices, p, asof=asof, eligible=eligible,
            signals_cache=signals_cache)

    # ---- daily simulation ------------------------------------------------
    dates = prices.index
    cost_rate = fees.round_trip_cost_rate(region)
    stamp_rate = region.stamp_duty_bps / 1e4

    current_w = pd.Series(dtype=float)
    equity = [initial_capital]
    daily_ret: list[float] = []
    turnover_log: list[tuple] = []
    cost_log: list[tuple] = []
    weights_hist: dict[pd.Timestamp, pd.Series] = {}
    total_cost = 0.0
    pending: pd.Series | None = None

    # Drawdown circuit breaker state
    peak = float(initial_capital)
    halted = False
    cooldown = 0
    halt_days = 0
    halt_events = 0
    CASH = pd.Series(dtype=float)

    for i in range(1, len(dates)):
        today, yday = dates[i], dates[i - 1]

        # Apply yesterday's signal at today's prices (t+1 execution).
        cost = 0.0
        if pending is not None:
            names = current_w.index.union(pending.index)
            delta = (pending.reindex(names, fill_value=0.0)
                     - current_w.reindex(names, fill_value=0.0))
            turnover = float(delta.abs().sum())
            buy_turnover = float(delta.clip(lower=0).sum())
            cost = turnover * cost_rate + buy_turnover * stamp_rate
            turnover_log.append((today, turnover))
            cost_log.append((today, cost))
            total_cost += cost
            current_w = pending
            pending = None

        day_rets = rets.loc[today].reindex(current_w.index).fillna(0.0)
        equity_ret = float((current_w * day_rets).sum())
        # Idle / risk-off fraction earns the defensive return (asset or cash_yield).
        idle_frac = max(0.0, 1.0 - float(current_w.sum()))
        cash_ret = (float(defensive_returns.loc[today])
                    if defensive_returns is not None else daily_cash_yield)
        r = equity_ret + idle_frac * cash_ret - cost
        daily_ret.append(r)
        equity.append(equity[-1] * (1 + r))
        weights_hist[today] = current_w

        # --- drawdown circuit breaker (decision at close, execute t+1) ---
        peak = max(peak, equity[-1])
        if halted:
            halt_days += 1
            cooldown -= 1
            if cooldown <= 0:
                halted = False
        elif max_drawdown_stop is not None and equity[-1] / peak - 1 <= -max_drawdown_stop:
            halted = True
            cooldown = cooldown_days
            halt_events += 1

        if halted:
            pending = CASH                       # liquidate / stay flat next day
        elif yday in weight_schedule:
            pending = weight_schedule[yday]

        # Drift held weights with the day's returns. The idle fraction grows by
        # the defensive return, so it's in the book's NAV growth factor too —
        # keeping the equity weights honest fractions of total capital.
        if not current_w.empty:
            grown = current_w * (1 + day_rets)
            nav = 1.0 + equity_ret + idle_frac * cash_ret
            current_w = grown / nav if nav != 0 else grown

    ret_series = pd.Series(daily_ret, index=dates[1:])
    eq = pd.Series(equity[1:], index=dates[1:])
    return {
        "region": region.key,
        "returns": ret_series,
        "equity": eq,
        "turnover": pd.Series(dict(turnover_log)),
        "costs": pd.Series(dict(cost_log)),
        "total_cost_fraction": total_cost,
        "weights": weights_hist,
        "point_in_time": membership is not None,
        "data_quality_excluded": sorted(dq_excluded),
        "drawdown_halts": halt_events,
        "drawdown_halt_days": halt_days,
        "metrics": compute_metrics(ret_series, eq, currency=region.currency),
    }
