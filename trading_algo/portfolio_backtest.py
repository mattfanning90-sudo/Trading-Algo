"""Multi-sleeve portfolio backtest, combined in the base currency (AUD).

Each regional sleeve is backtested independently in its local currency, then
converted to the base currency including the FX P&L:

    r_base_t = (1 + r_local_t) · (m_t / m_{t-1}) − 1      (m = base per local)

Sleeves start at their target allocation of capital and are trued back to target
on the configured cadence, paying an FX spread on the cash that crosses
currencies. The result is one combined AUD equity curve plus per-sleeve detail.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

import pandas as pd

from . import config as cfg
from . import constituents, data, fx
from .backtest import first_bar_on_or_after, run_backtest
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
                           allocations: dict[str, float] | None = None,
                           initial_capital: float = cfg.INITIAL_CAPITAL,
                           trade_from: pd.Timestamp | Mapping[str, pd.Timestamp]
                           | None = None) -> dict:
    """`params` overrides the strategy knobs for every sleeve; `allocations`
    overrides the capital split (both used by the tuner). `initial_capital` is
    in the BASE currency (AUD) — the portfolio's total starting capital, split
    across sleeves by `allocations`/`cfg.ALLOCATIONS`.

    I-1 (fix round 1): each sleeve's OWN `run_backtest` must be funded at its
    real size, not at a flat `cfg.INITIAL_CAPITAL` in LOCAL currency regardless
    of allocation. Before the per-order commission floor (Task 13) the backtest
    was scale-free, so every sleeve running at the same 100,000 local-currency
    number (vs. the ~A$25k a quarter-allocation sleeve is actually funded at)
    didn't matter; the floor made size matter, and a sleeve backtested 4x too
    large under-charges its own commission floor. Each sleeve is funded at
    `initial_capital * alloc[k]` (BASE currency) converted to the sleeve's own
    LOCAL currency at the FX multiplier on ITS OWN first price date (`m` is
    "base units per 1 local unit" — see `fx.py` — so local = base / m).

    `trade_from` runs the portfolio as a book OPENED that day (see
    `run_backtest`): each sleeve is funded at the rate on its first bar on or
    after it instead, and holds no currency before that bar, so every sleeve
    is worth exactly `initial_capital * alloc[k]` in the base currency there.
    It is one date for every sleeve, or a mapping {region key: date} naming
    each sleeve's own (a book whose sleeves first decided on different bars);
    the mapping must give every sleeve a date — a missing key or a None value
    is refused, since None would run that sleeve over the whole sample. None
    (the default) is the whole-sample backtest, unchanged. Each sleeve's
    result names its `open_bar` (None for the whole sample).

    FX BEFORE A SLEEVE'S OWN OPEN: its base returns are zeroed through its
    open bar, so until then the sleeve is pinned at `initial_capital *
    alloc[k]` in the BASE currency — its cash carries no currency move. The
    paper book's cash is held in LOCAL currency from `--init`, so it does
    carry that move. So the comparison's cash carries FX from its open bar's
    close, and the paper's from `--init`. The bridge measures both from
    inception, and the two differ over the time between those moments, in
    either direction:
      * `--init` first (the min-viable-size gate held a sleeve in cash past
        its first run, or a book was initialised by hand before its first
        run): the paper carries FX the comparison does not;
      * the open bar's close first (a sleeve still on the previous session
        when the book's first run decided, as on a staggered first day): the
        comparison carries FX, from that close to `--init`, that the paper
        never held.
    Either way it lands in the bridge's remainder. The scheduled workflow
    runs a new book's first pass straight after `--init`, so the gap is
    normally hours, not days."""
    regions = regions or list(allocations or cfg.ALLOCATIONS)
    undated = ([k for k in regions if trade_from.get(k) is None]
               if isinstance(trade_from, Mapping) else [])
    if undated:
        raise ValueError(f"trade_from names no open date for {sorted(undated)}")
    syn_end = end or "2026-01-01"
    # F13: apply the delisting-return correction only in the PIT path and only
    # when the knob is set — otherwise a perfect no-op.
    apply_delisting = point_in_time and cfg.DELISTING_REPLACEMENT_RETURN is not None

    # Normalise BEFORE the sleeve loop: each sleeve needs its OWN share of
    # `initial_capital` to fund its own run_backtest call below.
    alloc = cfg.allocation_shares(allocations, regions)      # normalise to 1.0

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
        volume = data.capacity_volume(prices, start, end, synthetic=synthetic)
        # base_per_local at the sleeve's own first price date — the rate this
        # sleeve is actually funded at, not an arbitrary portfolio-wide date —
        # or, for a book opened at `trade_from`, at the bar it opens on.
        m_fund = fx.align_fx(fx_tbl, prices.index, region.currency)
        opens = trade_from[key] if isinstance(trade_from, Mapping) else trade_from
        open_bar = (None if opens is None else
                    first_bar_on_or_after(prices.dropna(how="all").index, opens))
        base_per_local = float(m_fund.iloc[0] if open_bar is None
                               else m_fund.loc[open_bar])
        sleeve_capital_local = initial_capital * alloc[key] / base_per_local
        bt = run_backtest(prices, index_px, region,
                          initial_capital=sleeve_capital_local,
                          membership=membership,
                          apply_delisting=apply_delisting, volume=volume,
                          trade_from=opens)
        m = fx.align_fx(fx_tbl, bt["returns"].index, region.currency)
        bt["base_returns"] = _sleeve_base_returns(bt["returns"], m)
        if open_bar is not None:
            # Funded at `open_bar`'s rate: no FX move up to it is the book's.
            bt["base_returns"].loc[:open_bar] = 0.0
        # The bar the sleeve opened on (None: the whole sample). The bridge
        # measures it from there: before it the sleeve did not exist.
        bt["open_bar"] = open_bar
        sleeves[key] = bt
        index_by_region[key] = (index_px, region.currency)

    # ---- combine on the union of trading dates ---------------------------
    union = sorted(set().union(*[set(s["base_returns"].index) for s in sleeves.values()]))
    union = pd.DatetimeIndex(union)
    base_r = {k: s["base_returns"].reindex(union).fillna(0.0) for k, s in sleeves.items()}

    caps = {k: initial_capital * alloc[k] for k in regions}
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
    bench_equity = initial_capital * (1 + bench_ret).cumprod()

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
