"""Multi-sleeve portfolio backtest, combined in the base currency (AUD).

Each regional sleeve is backtested independently in its local currency, then
converted to the base currency including the FX P&L:

    r_base_t = (1 + r_local_t) · (m_t / m_{t-1}) − 1      (m = base per local)

Sleeves start at their target allocation of capital and are trued back to target
on the configured cadence, paying an FX spread on the cash that crosses
currencies. The result is one combined AUD equity curve plus per-sleeve detail.
"""
from __future__ import annotations

from dataclasses import replace

import pandas as pd

from . import config as cfg
from . import constituents, data, fx
from .backtest import run_backtest
from .config import StrategyParams
from .metrics import benchmark_stats, compute_metrics
from .regions import get_region


def _sleeve_base_returns(local_rets: pd.Series, fx_mult: pd.Series) -> pd.Series:
    """Convert a local fractional return series to base-currency returns,
    including the currency move."""
    m = fx_mult.reindex(local_rets.index).ffill().bfill()
    fx_ret = m / m.shift(1) - 1.0
    fx_ret.iloc[0] = 0.0
    return (1 + local_rets) * (1 + fx_ret) - 1.0


def run_portfolio_backtest(regions: list[str] | None = None,
                           synthetic: bool = False,
                           start: str = cfg.START,
                           end: str | None = None,
                           point_in_time: bool = False,
                           params: StrategyParams | None = None,
                           allocations: dict[str, float] | None = None) -> dict:
    """`params` overrides the strategy knobs for every sleeve; `allocations`
    overrides the capital split (both used by the tuner)."""
    regions = regions or list(allocations or cfg.ALLOCATIONS)
    syn_end = end or "2026-01-01"
    # F13: apply the delisting-return correction only in the PIT path and only
    # when the knob is set — otherwise a perfect no-op.
    apply_delisting = point_in_time and cfg.DELISTING_REPLACEMENT_RETURN is not None

    sleeves: dict[str, dict] = {}
    index_by_region: dict[str, tuple] = {}
    currencies = [get_region(r).currency for r in regions]

    if synthetic:
        fx_tbl = fx.synthetic_fx(currencies, start=start, end=syn_end,
                                 base=cfg.BASE_CURRENCY)
    else:
        fx_tbl = fx.load_fx(currencies, start, end, base=cfg.BASE_CURRENCY)

    for key in regions:
        region = get_region(key)
        if params is not None:
            region = replace(region, params=params)
        membership = None
        if point_in_time:
            membership = (constituents.synthetic_membership(region, start, syn_end)
                          if synthetic else constituents.get_membership(region))
        pit_tickers = membership.all_tickers if membership is not None else None

        if synthetic:
            prices, index_px = data.synthetic_region(region, start=start, end=syn_end)
        else:
            prices, index_px = data.load_region(region, start, end, tickers=pit_tickers)
        bt = run_backtest(prices, index_px, region, membership=membership,
                          apply_delisting=apply_delisting)
        m = fx.align_fx(fx_tbl, bt["returns"].index, region.currency)
        bt["base_returns"] = _sleeve_base_returns(bt["returns"], m)
        sleeves[key] = bt
        index_by_region[key] = (index_px, region.currency)

    # ---- combine on the union of trading dates ---------------------------
    union = sorted(set().union(*[set(s["base_returns"].index) for s in sleeves.values()]))
    union = pd.DatetimeIndex(union)
    base_r = {k: s["base_returns"].reindex(union).fillna(0.0) for k, s in sleeves.items()}

    alloc = {k: (allocations or cfg.ALLOCATIONS)[k] for k in regions}
    tot_alloc = sum(alloc.values())
    alloc = {k: v / tot_alloc for k, v in alloc.items()}  # normalise to 1.0

    caps = {k: cfg.INITIAL_CAPITAL * alloc[k] for k in regions}
    rebal_dates = (set(pd.Series(0, index=union).resample(cfg.ALLOCATION_REBALANCE)
                       .last().index) if cfg.ALLOCATION_REBALANCE else set())

    equity_rows, sleeve_rows, fx_cost_total = [], [], 0.0
    for dt in union:
        for k in regions:
            caps[k] *= (1 + base_r[k].loc[dt])
        total = sum(caps.values())

        if dt in rebal_dates and cfg.ALLOCATION_REBALANCE:
            targets = {k: total * alloc[k] for k in regions}
            crossing = sum(abs(caps[k] - targets[k]) for k in regions) / 2.0
            cost = crossing * cfg.FX_SPREAD_BPS / 1e4
            fx_cost_total += cost
            total -= cost
            caps = {k: total * alloc[k] for k in regions}

        equity_rows.append((dt, sum(caps.values())))
        sleeve_rows.append((dt, {k: caps[k] for k in regions}))

    equity = pd.Series(dict(equity_rows))
    returns = equity.pct_change(fill_method=None).fillna(0.0)
    sleeve_equity = pd.DataFrame({dt: row for dt, row in sleeve_rows}).T

    # Benchmark: equal-weight buy-and-hold of the regional indices, in AUD.
    parts = []
    for idx, ccy in index_by_region.values():
        mult = fx.align_fx(fx_tbl, idx.index, ccy)
        idx_aud_ret = (idx * mult).pct_change(fill_method=None)
        parts.append(idx_aud_ret.reindex(union).fillna(0.0))
    bench_ret = sum(parts) / len(parts)
    bench_equity = cfg.INITIAL_CAPITAL * (1 + bench_ret).cumprod()

    return {
        "equity": equity,
        "returns": returns,
        "sleeve_equity": sleeve_equity,
        "sleeves": sleeves,
        "allocations": alloc,
        "fx_rebalance_cost": fx_cost_total,
        "point_in_time": point_in_time,
        "benchmark": bench_equity,
        "benchmark_metrics": compute_metrics(bench_ret, bench_equity, currency=cfg.BASE_CURRENCY),
        "benchmark_stats": benchmark_stats(returns, bench_ret),
        "metrics": compute_metrics(returns, equity, currency=cfg.BASE_CURRENCY),
    }
