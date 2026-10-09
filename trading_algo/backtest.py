"""Per-sleeve walk-forward backtest (one region, local currency).

Design principles (the invariants from CLAUDE.md):
- No lookahead, next-day-CLOSE execution: weights are decided at month-end D_k
  using data ≤ D_k, staged, and EXECUTED at the close of the next trading bar
  D_{k+1}. They are never filled at D_k's own close — that is the price that
  produced the signal, and no real order can touch it. The first bar a new
  target earns a return on is therefore D_{k+2}. The drawdown breaker stages a
  liquidation the same way, for the same reason. Targets come from the shared
  `strategy.compute_targets` — the same function paper trading uses.
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
    # Imported in-function (matching the ADV/impact knobs) so that a test
    # monkeypatching config takes effect instead of binding at module load.
    from .config import (ADV_CAP_PCT, ADV_WINDOW, CASH_RATE_ANNUAL,
                         CREDIT_IDLE_CASH, IMPACT_COEF)
    advd = None
    vols_frame = None
    if volume is not None and (ADV_CAP_PCT or IMPACT_COEF):
        from . import data as _data
        advd = _data.adv_dollar(prices, volume, ADV_WINDOW)
        if IMPACT_COEF:
            from . import signals as _sig
            vols_frame = _sig.realised_vol(prices, p)

    # The signal frames are identical for every rebalance date, so build them
    # ONCE and read the as-of row per date (strategy.precompute/targets_at).
    # `targets_at` is the same single weight formula `compute_targets` runs — the
    # panel only removes the repeated recomputation, never a branch of it — so
    # invariant #3 holds and `test_consistency` proves it numerically.
    panel = strategy.precompute(prices, index_prices, p)

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
        weight_schedule[asof] = strategy.targets_at(
            panel, p, asof, eligible=eligible, capacity=capacity)

    # ---- daily simulation ------------------------------------------------
    dates = prices.index

    current_w = pd.Series(dtype=float)
    equity = [initial_capital]
    daily_ret: list[float] = []
    turnover_log: list[tuple] = []
    cost_log: list[tuple] = []
    weights_hist: dict[pd.Timestamp, pd.Series] = {}
    total_cost = 0.0
    total_cash_interest = 0.0
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

        # --- 1. today's return is earned by the book we were ALREADY holding ---
        # A target staged at the prior close is not in the book yet: it is filled
        # below, at TODAY's close, so it sits out this bar.
        day_rets = rets.loc[today].reindex(current_w.index).fillna(0.0)
        gross = float((current_w * day_rets).sum())
        r = gross
        # Interest on whatever was NOT invested over the day. `current_w` is the
        # book held THROUGH today, and its NET sum is the exposure — see
        # fees.idle_cash_credit on why net, not gross. Without this a flat book
        # earns 0% while metrics still charge it the RISK_FREE hurdle, which is a
        # double penalty worth +0.22 to +0.34 Sharpe (docs/SHARPE_RESEARCH.md §1).
        if CREDIT_IDLE_CASH and CASH_RATE_ANNUAL:
            interest = fees.idle_cash_credit(
                float(current_w.sum()), (today - dates[i - 1]).days,
                CASH_RATE_ANNUAL)
            r += interest
            total_cash_interest += interest

        # Drift the held weights to today's close — that is the book the fill
        # starts from, and the prices it happens at.
        if not current_w.empty:
            grown = current_w * (1 + day_rets)
            nav_growth = 1 + gross
            current_w = grown / nav_growth if nav_growth != 0 else grown

        # --- 2. execute the staged target AT TODAY'S CLOSE --------------------
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
            # The per-order commission floor needs the book's size and how many
            # names this rebalance actually moved — bps alone models a small
            # book as trading almost free (July finding H3). `equity[-1]` is the
            # PREVIOUS close's NAV (today's own close isn't appended yet); under
            # Task 9's next-close-fill convention the strictly correct NAV is one
            # bar's return later, which is second order and accepted here.
            cost = fees.turnover_cost(
                region, turnover, buy_turnover, impact=impact,
                nav=equity[-1], n_names=int((delta.abs() > 0).sum()))
            turnover_log.append((today, turnover))
            cost_log.append((today, cost))
            total_cost += cost
            r -= cost
            current_w = pending
            pending = None

        daily_ret.append(r)
        equity.append(equity[-1] * (1 + r))
        # The book as it stands at TODAY's close — the one that earns tomorrow's
        # return. A freshly executed target is recorded on its EXECUTION bar.
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
            pending = CASH                       # liquidate at the NEXT close
        elif today in weight_schedule:
            # A target decided as-of `today` (D_k) is staged now and EXECUTED at
            # the close of D_{k+1}; it first earns a return on D_{k+2}.
            pending = weight_schedule[today]

    ret_series = pd.Series(daily_ret, index=dates[1:])
    eq = pd.Series(equity[1:], index=dates[1:])
    return {
        "region": region.key,
        "returns": ret_series,
        "equity": eq,
        "turnover": pd.Series(dict(turnover_log)),
        "costs": pd.Series(dict(cost_log)),
        "total_cost_fraction": total_cost,
        "cash_interest_fraction": total_cash_interest,
        "weights": weights_hist,
        "point_in_time": membership is not None,
        "data_quality_excluded": sorted(dq_excluded),
        "drawdown_halts": halt_events,
        "drawdown_halt_days": halt_days,
        "metrics": compute_metrics(ret_series, eq, currency=region.currency),
    }
