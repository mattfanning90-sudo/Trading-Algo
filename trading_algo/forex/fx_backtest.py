"""Walk-forward FX backtest — costs always on, no lookahead.

Design (mirrors the equity sleeve's invariants):
- **No lookahead**: weights come from `fx_strategy.target_weights_history`, where
  weightₜ uses data ≤ t. Here we *decide* at bar t and *earn* the return over
  t→t+1 (the position is established at t's close and held into t+1).
- **Costs always on**: every change in a pair's weight crosses half the dealing
  spread; held positions accrue overnight carry/financing each day. Metrics are
  never reported gross.
- **Risk overlay**: a peak-to-trough drawdown breaker flattens the book and sits
  out for a cooldown, identical in spirit to the equity backtester.

Returns are fractional, so the curve is in account-currency terms via the paper
book's equity; the backtest itself is currency-agnostic.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import fx_strategy
from . import fxconv
from . import marks
from .agents import AgentPool
from .fx_config import ACCOUNT_CURRENCY, FX_RISK_FREE, FXParams
from .fx_data import closes
from .pairs import get_pair
from ..metrics import compute_metrics


def _sign(x: float) -> int:
    return 1 if x > 0 else (-1 if x < 0 else 0)


def run_backtest(panel: dict[str, pd.DataFrame], p: FXParams,
                 pool: AgentPool | None = None,
                 initial_capital: float = 5_000.0) -> dict:
    """Simulate the multi-agent FX book over `panel`. Returns curves + metrics."""
    weights = fx_strategy.target_weights_history(panel, p, pool=pool)
    if weights.empty or len(weights) < 3:
        raise ValueError("not enough history to backtest")

    px = closes(panel).reindex(weights.index).ffill()
    rets = px.pct_change(fill_method=None).fillna(0.0)
    pairs = list(weights.columns)
    specs = {s: get_pair(s) for s in pairs}
    dates = weights.index

    # AUD account: translate each pair's quote-currency return into AUD (see
    # fxconv). audq_ratioₜ = aud_per_quoteₜ / aud_per_quoteₜ₋₁ per pair; the AUD
    # return of a pair over a bar is (1+pair_ret)*ratio − 1. Falls back to the raw
    # return wherever the AUD/quote rate isn't derivable from the panel.
    audq = fxconv.aud_per_quote_frame(px, [specs[s].quote for s in pairs])
    audq_pair = pd.DataFrame({s: audq[specs[s].quote] for s in pairs})
    audq_ratio = (audq_pair / audq_pair.shift(1)).reindex(columns=pairs).fillna(1.0)
    aud_rets = (1.0 + rets) * audq_ratio - 1.0

    held = pd.Series(0.0, index=pairs)
    equity = [float(initial_capital)]
    daily: list[float] = []
    turnover_log: list[float] = []
    cost_log: list[float] = []
    carry_log: list[float] = []
    gross_log: list[float] = []
    attribution = pd.Series(0.0, index=pairs)
    weights_hist: dict[pd.Timestamp, pd.Series] = {}

    peak = float(initial_capital)
    halted = False
    cooldown = 0
    halt_events = 0
    halt_days = 0
    total_cost = total_carry = 0.0

    for i in range(len(dates) - 1):
        d, nxt = dates[i], dates[i + 1]
        target = pd.Series(0.0, index=pairs) if halted else weights.loc[d].reindex(pairs).fillna(0.0)
        price_d = px.loc[d]

        # No-churn band: only move a pair when the target shifts enough to matter.
        diff = target - held
        move = diff.where(diff.abs() >= p.rebalance_min_delta, 0.0)
        held = held + move

        # Turnover cost: half the dealing spread per unit weight moved.
        cost = 0.0
        for s in pairs:
            m = move[s]
            if m:
                cost += abs(m) * marks.half_spread_fraction(specs[s], price_d[s])

        # Overnight carry/financing on the positions held into the next bar.
        carry = 0.0
        if p.include_carry:
            for s in pairs:
                w = held[s]
                if w:
                    carry += abs(w) * specs[s].carry_fraction(price_d[s], _sign(w))

        ret_nxt = aud_rets.loc[nxt]              # AUD-translated pair returns
        pair_pnl = held * ret_nxt.reindex(pairs).fillna(0.0)
        attribution += pair_pnl
        day_ret = float(pair_pnl.sum()) + carry - cost

        equity.append(equity[-1] * (1.0 + day_ret))
        daily.append(day_ret)
        turnover_log.append(float(move.abs().sum()))
        cost_log.append(cost)
        carry_log.append(carry)
        gross_log.append(float(held.abs().sum()))
        weights_hist[nxt] = held.copy()
        total_cost += cost
        total_carry += carry

        # Drawdown circuit breaker (decision at close, flat from next bar).
        peak = max(peak, equity[-1])
        if halted:
            halt_days += 1
            cooldown -= 1
            if cooldown <= 0:
                halted = False
        elif p.max_drawdown_stop is not None and equity[-1] / peak - 1 <= -p.max_drawdown_stop:
            halted = True
            cooldown = p.drawdown_cooldown_days
            halt_events += 1

    idx = dates[1:]
    ret_series = pd.Series(daily, index=idx)
    eq = pd.Series(equity[1:], index=idx)
    return {
        "returns": ret_series,
        "equity": eq,
        "weights": weights_hist,
        "turnover": pd.Series(turnover_log, index=idx),
        "costs": pd.Series(cost_log, index=idx),
        "carry": pd.Series(carry_log, index=idx),
        "avg_gross_leverage": float(np.mean(gross_log)) if gross_log else 0.0,
        "attribution": attribution.sort_values(ascending=False),
        "total_cost_fraction": total_cost,
        "total_carry_fraction": total_carry,
        "drawdown_halts": halt_events,
        "drawdown_halt_days": halt_days,
        "metrics": compute_metrics(ret_series, eq, risk_free=FX_RISK_FREE,
                                   currency=ACCOUNT_CURRENCY),
    }
