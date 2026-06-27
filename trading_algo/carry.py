"""Cross-asset carry sleeve — the third diversifying premium.

Carry is the return you earn if prices *don't move*: hold a high-yielding asset,
collect the yield. Across a basket of yield-bearing ETFs (rates, credit, equity,
real assets) the cross-section of trailing income yields — and the way that
ranking rotates as credit spreads widen and the curve steepens/inverts — is a
genuine, largely independent signal. We trade it **cross-sectionally**: long the
high-carry assets, short the low-carry ones (`long_short`), inverse-vol sized to
a portfolio vol target, via the same L/S engine as trend (`lsbacktest`).

Honest framing (consistent with the rest of the research):
- This is **income-yield carry** (derived from total-vs-price return), not the
  full futures roll/term-structure carry of the academic FX/commodity literature
  — that needs futures curves we don't have. It is still a real, diversifying
  premium, but label it for what it is.
- Carry assets share more market/credit beta than trend's basket, so expect a
  higher correlation to equities than trend has. Its job in the multi-strat book
  is incremental diversification, not crisis alpha.

No lookahead: the yield + vol signals at date t use data <= t; the engine applies
the resulting weights at t+1 — same discipline as the equity and trend sleeves.
"""
from __future__ import annotations

import pandas as pd

from . import trend
from .config import DEFAULT_CARRY_PARAMS, CarryParams


def carry_signal(yields_row: pd.Series, long_short: bool = True) -> pd.Series:
    """Per-asset carry signal in [-1, 1] for one date.

    Cross-sectionally demean the income yields (long above-average carry, short
    below) and scale by the largest absolute deviation. With `long_short=False`,
    floor at the minimum instead (a long-only tilt toward the higher-yielders)."""
    y = yields_row.dropna()
    if y.empty:
        return pd.Series(dtype=float)
    s = (y - y.mean()) if long_short else (y - y.min())
    m = float(s.abs().max())
    return s / m if m > 0 else s * 0.0


def precompute(prices: pd.DataFrame, yields: pd.DataFrame,
               p: CarryParams) -> dict:
    """Carry signal + price-vol frames, aligned to the price calendar (causal:
    yields are forward-filled, never future-filled — see tests)."""
    y = yields.reindex(prices.index).ffill()
    sig = y.apply(lambda row: carry_signal(row, p.long_short), axis=1)
    vol = trend._realised_vol(prices, p)          # reuses the inverse-vol estimator
    return {"signal": sig, "vol": vol}


def compute_carry_targets(prices: pd.DataFrame, yields: pd.DataFrame,
                          p: CarryParams = DEFAULT_CARRY_PARAMS,
                          asof: pd.Timestamp | None = None,
                          signals_cache: dict | None = None) -> pd.Series:
    """Signed target weights for one rebalance date (default: latest available).
    Single source of truth for carry weights (mirrors trend/equity invariant).
    Sizing/vol-targeting is shared with trend via `trend.size_positions`."""
    if asof is None:
        asof = prices.index[-1]
    c = signals_cache if signals_cache is not None else precompute(prices, yields, p)
    return trend.size_positions(c["signal"].loc[asof], c["vol"].loc[asof], p)


def run_carry_backtest(prices: pd.DataFrame, yields: pd.DataFrame,
                       p: CarryParams = DEFAULT_CARRY_PARAMS,
                       initial_capital: float = 100_000.0,
                       currency: str = "USD") -> dict:
    """Walk-forward backtest of the carry sleeve (long/short, costs always on).
    Decide weights at month-end t from data <= t, apply at t+1 — same engine and
    no-lookahead discipline as trend."""
    from .lsbacktest import run_ls_backtest

    prices = prices.dropna(how="all")
    if len(prices) <= p.min_history_days:
        raise ValueError(f"carry: not enough history ({len(prices)} rows)")

    cache = precompute(prices, yields, p)
    rebal_marks = prices.resample(p.rebalance).last().index

    schedule: dict[pd.Timestamp, pd.Series] = {}
    for d in rebal_marks:
        loc = prices.index.searchsorted(d, side="right") - 1
        if loc < p.min_history_days:
            continue
        asof = prices.index[loc]
        schedule[asof] = compute_carry_targets(prices, yields, p, asof=asof,
                                               signals_cache=cache)

    return run_ls_backtest(prices, schedule, p.cost_bps, "CARRY",
                           currency=currency, initial_capital=initial_capital)
