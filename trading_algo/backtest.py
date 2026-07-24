"""Per-sleeve walk-forward backtest (one region, local currency).

Design principles (the invariants from CLAUDE.md):
- No lookahead, true t+1: weights are decided at month-end D_k using data ≤ D_k,
  staged, and first affect the book (and therefore returns) on the very next
  trading bar D_{k+1} — never on or before D_k. A target is staged on the same
  bar it is decided (keyed off `today`), so it drives returns exactly one bar
  later, matching the drawdown-breaker branch and the same-day paper engine.
  Targets come from the shared `strategy.compute_targets` — the same function
  paper trading uses.
- Costs always on: commission + slippage charged on turnover every rebalance,
  plus UK stamp duty on the buy side (asymmetric).
- Returns are fractional, so the series is currency-agnostic; the portfolio
  layer converts to the base currency via FX.
"""
from __future__ import annotations

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
                 apply_delisting: bool = False,
                 volume: pd.DataFrame | None = None) -> dict:
    """Walk-forward backtest for one sleeve.

    `membership` (a constituents.MembershipTable) makes selection point-in-time:
    at each rebalance only names in the index as-of that date are eligible. When
    None the current universe is used (survivorship-biased).

    `max_drawdown_stop` is a circuit breaker: if equity falls more than this from
    its peak, the book liquidates to cash and sits out for `cooldown_days` before
    resuming. Pass None to disable.

    `apply_delisting` (backlog F13) injects a delisting return for names whose
    price series terminates before the sample end — only meaningful with real
    point-in-time data that includes since-delisted names."""
    p = region.params
    prices = prices.dropna(how="all")
    if apply_delisting:
        from . import delisting
        prices = delisting.apply_delisting_returns(prices, region)
    rets = prices.pct_change(fill_method=None)

    # Rebalance dates = last trading day on or before each period end.
    rebal_marks = prices.resample(p.rebalance).last().index
    min_hist = p.min_history_days
    if p.use_value:                              # need the long-term-reversal window
        min_hist = max(min_hist, p.value_lookback_days + 5)
    if len(prices) <= min_hist:
        raise ValueError(f"{region.key}: not enough history ({len(prices)} rows)")

    # ADV dollar-volume drives both the F15 pre-trade cap and the F6 market-impact
    # cost. Computed once when volume is supplied AND either feature is enabled.
    from .config import ADV_CAP_PCT, ADV_WINDOW, IMPACT_COEF
    advd = None
    vols_frame = None
    if volume is not None and (ADV_CAP_PCT or IMPACT_COEF):
        from . import data as _data
        advd = _data.adv_dollar(prices, volume, ADV_WINDOW)
        if IMPACT_COEF:
            from . import signals as _sig
            vols_frame = _sig.realised_vol(prices, p)

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
        capacity = None
        if ADV_CAP_PCT and advd is not None and asof in advd.index:
            capacity = (ADV_CAP_PCT * advd.loc[asof] / float(initial_capital)).dropna()
        weight_schedule[asof] = strategy.compute_targets(
            prices, index_prices, p, asof=asof, eligible=eligible, capacity=capacity)

    # ---- daily simulation ------------------------------------------------
    dates = prices.index

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
        today = dates[i]

        # Apply the target staged on the prior bar at today's prices (t+1
        # execution): a signal decided as-of D_{k} moves the book on D_{k+1}.
        cost = 0.0
        if pending is not None:
            names = current_w.index.union(pending.index)
            delta = (pending.reindex(names, fill_value=0.0)
                     - current_w.reindex(names, fill_value=0.0))
            turnover = float(delta.abs().sum())
            buy_turnover = float(delta.clip(lower=0).sum())
            # F6: per-name square-root market impact (fraction of NAV), added to
            # the one shared cost entrypoint (R1). Zero unless IMPACT_COEF is set.
            impact = 0.0
            if IMPACT_COEF and advd is not None:
                a = advd.loc[:today]
                if len(a):
                    a = a.iloc[-1]
                    v = (vols_frame.loc[:today].iloc[-1]
                         if vols_frame is not None and len(vols_frame.loc[:today])
                         else None)
                    nav = equity[-1]
                    for name, dw in delta[delta.abs() > 0].items():
                        impact += abs(dw) * fees.square_root_impact(
                            abs(dw) * nav, a.get(name), (v.get(name) if v is not None else float("nan")),
                            IMPACT_COEF)
            cost = fees.turnover_cost(region, turnover, buy_turnover, impact=impact)
            turnover_log.append((today, turnover))
            cost_log.append((today, cost))
            total_cost += cost
            current_w = pending
            pending = None

        day_rets = rets.loc[today].reindex(current_w.index).fillna(0.0)
        r = float((current_w * day_rets).sum()) - cost
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
        elif today in weight_schedule:
            # True t+1: a target decided as-of `today` (D_k) is staged now and
            # first affects the book — and thus returns — on the NEXT bar D_{k+1}.
            # Keying off `today` (not `yday`) matches the halted branch above and
            # the same-day paper engine; keying off `yday` delayed it to D_{k+2}.
            pending = weight_schedule[today]

        # Drift held weights with the day's returns.
        if not current_w.empty:
            grown = current_w * (1 + day_rets)
            nav = 1 + float((current_w * day_rets).sum())
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
