#!/usr/bin/env python3
"""Measure whether a permutation NULL is actually a null for OUR strategy.

Background in plain words
-------------------------
A **permutation test** answers "could luck alone have done this?" by shuffling
history into thousands of fake-but-plausible alternative markets, re-running the
same strategy on each, and counting how often luck matched or beat the real
result. That count over the number of shuffles is the **p-value**.

The whole method rests on one assumption: that the shuffled market contains **no
edge**. If the shuffling leaves an edge behind, the comparison is rigged and the
p-value is meaningless. This script MEASURES that assumption rather than trusting
it, by running each candidate null on a strategy and checking whether the null's
own score sits on zero.

The published method (Masters; neurotrader888/mcpt) shuffles price bars. That is
correct for a **time-series** strategy on one instrument. This repo trades
**cross-sectional** momentum -- it ranks names against each other -- and for that
the bar shuffle leaves a large edge behind. §"Cross-sectional" in
docs/PERMUTATION_TESTING.md explains why; this script is the evidence.

Nothing here writes state. It is read-only against the price cache.

Usage
-----
    python scripts/measure_permutation_null.py                  # real US panel
    python scripts/measure_permutation_null.py --region ASX
    python scripts/measure_permutation_null.py --synthetic      # offline
    python scripts/measure_permutation_null.py --permutations 1000
"""
from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, __file__.rsplit("/scripts/", 1)[0])

from trading_algo import config
from trading_algo.data import load_region, synthetic_region
from trading_algo.regions import REGIONS

# The repo's own 12-1 spec (config.StrategyParams). Kept explicit so this
# measurement is pinned to what it measured, not to a later edit of the config.
LOOKBACK = config.StrategyParams.lookback_days   # 252d ~ 12 months of momentum
SKIP = config.StrategyParams.skip_days           # skip the last 21d (short-term reversal)
TOP_N = config.StrategyParams.top_n              # hold the top 10 names


# --------------------------------------------------------------------------
# The strategy, reduced to its testable core
# --------------------------------------------------------------------------
def rebalance_points(index: pd.DatetimeIndex, n_rows: int) -> np.ndarray:
    """Row numbers of each month-end, once there is enough history for a signal."""
    pos = pd.Series(np.arange(n_rows), index=index)
    me = pos.groupby([index.year, index.month]).last().to_numpy()
    return me[me >= LOOKBACK + SKIP]


def momentum_and_forward(log_rets: np.ndarray, me: np.ndarray):
    """Split the panel into (momentum score at each rebalance, next month's return).

    `mom[i]` uses only data up to rebalance i -- the 252 days ending 21 days
    before it. `fwd[i]` is what happened AFTERWARDS. Signal at t, return from t
    onward: invariant #1 (no lookahead) holds by construction here.
    """
    log_px = np.cumsum(log_rets, axis=0)
    mom = np.array([log_px[a - SKIP] - log_px[a - SKIP - LOOKBACK] for a in me[:-1]])
    fwd = np.array([log_px[b] - log_px[a] for a, b in zip(me[:-1], me[1:])])
    return mom, fwd


def excess_sharpe(mom: np.ndarray, fwd: np.ndarray) -> float:
    """Sharpe of top-N momentum MINUS the equal-weight universe.

    **Sharpe ratio** -- return divided by volatility, annualised: reward per unit
    of risk. Measuring it *relative to the equal-weight universe* strips out the
    general market rise, so what is left is the part momentum actually claims to
    add. Gross of costs -- this is a diagnostic of the null, never a performance
    number (invariants #2 and #5).
    """
    picks = np.argpartition(-mom, TOP_N, axis=1)[:, :TOP_N]
    rows = np.arange(len(mom))[:, None]
    monthly = fwd[rows, picks].mean(axis=1) - fwd.mean(axis=1)
    return float(monthly.mean() / monthly.std(ddof=1) * np.sqrt(12))


# --------------------------------------------------------------------------
# Candidate nulls -- the thing under test
# --------------------------------------------------------------------------
def null_common(R, mom, rng):
    """As published: ONE shuffle of the calendar, shared by every name.

    Sharing the shuffle is what preserves how names move together. Shuffling
    each name separately would also destroy that, which is worse -- see the
    'independent' row.
    """
    return R[rng.permutation(len(R))], None


def null_independent(R, mom, rng):
    """Each name shuffled on its own clock. Included to show it is a trap."""
    return np.column_stack([R[rng.permutation(len(R)), j] for j in range(R.shape[1])]), None


def null_demeaned(R, mom, rng):
    """Strip each name's average daily return, then shuffle.

    Removes the drift that momentum accidentally latches onto -- but forces each
    name to end exactly where it started, which manufactures mean reversion and
    makes the test too generous. Shown because it looks like the obvious fix.
    """
    Rd = R - R.mean(axis=0, keepdims=True)
    return Rd[rng.permutation(len(R))], None


def null_signal(R, mom, rng):
    """Shuffle WHICH NAME each momentum score belongs to, at each rebalance.

    Prices are left completely untouched. This breaks the one link the strategy
    claims exists -- "this name's past predicts this name's future" -- and
    disturbs nothing else. No drift survives into the signal, and no artificial
    mean reversion is introduced.
    """
    return R, np.array([m[rng.permutation(R.shape[1])] for m in mom])


NULLS = [
    ("independent shuffle per name", null_independent, "destroys co-movement too"),
    ("common shuffle (as published)", null_common, "leaves each name's drift in"),
    ("de-meaned, then shuffled", null_demeaned, "manufactures mean reversion"),
    ("signal shuffle across names", null_signal, "leaves prices untouched"),
]


# --------------------------------------------------------------------------
# Sanity check: the case the published method was designed for
# --------------------------------------------------------------------------
def best_donchian_sharpe(log_px: np.ndarray, lookbacks=range(20, 201, 20)) -> float:
    """Best **Donchian breakout** over a grid of lookbacks.

    Donchian breakout: go long when price closes above its highest close of the
    last N days, short when below the lowest. A classic single-instrument trend
    rule. We take the BEST N -- so the permutation null inherits the same search,
    and the advantage of having searched is priced into the null automatically.
    """
    c = pd.Series(log_px)
    fwd = c.diff().shift(-1)
    best = -np.inf
    for lb in lookbacks:
        up = c.rolling(lb - 1).max().shift(1)
        dn = c.rolling(lb - 1).min().shift(1)
        sig = pd.Series(np.nan, index=c.index)
        sig[c > up] = 1.0
        sig[c < dn] = -1.0
        pnl = (sig.ffill() * fwd).dropna()
        if len(pnl) > 2 and pnl.std(ddof=1) > 0:
            best = max(best, pnl.mean() / pnl.std(ddof=1) * np.sqrt(252))
    return float(best)


def p_value(null: np.ndarray, real: float) -> float:
    """p = (k+1)/(m+1). The +1s are not decoration: they stop the test ever
    reporting p=0, which would claim more certainty than m shuffles can give."""
    return float(((null >= real).sum() + 1) / (len(null) + 1))


def report_invariants(R: np.ndarray, rng: np.random.Generator) -> None:
    """Check what a single shared shuffle really preserves and destroy.

    These are the claims the whole method rests on. Measured, not assumed.
    """
    Rp = R[rng.permutation(len(R))]
    corr = lambda M: np.corrcoef(M, rowvar=False)[np.triu_indices(M.shape[1], 1)]
    ac1 = lambda x: float(pd.Series(x).autocorr(1))
    mkt, mktp = R.mean(axis=1), Rp.mean(axis=1)
    print("WHAT ONE SHARED SHUFFLE DOES (the method's core claims, measured):")
    print(f"  per-name total return  preserved to {np.abs(R.sum(0) - Rp.sum(0)).max():.1e}"
          "   <- every shuffle ends at the SAME price")
    print(f"  per-name volatility    preserved to {np.abs(R.std(0) - Rp.std(0)).max():.1e}")
    print(f"  pairwise co-movement   real {corr(R).mean():.4f} -> shuffled {corr(Rp).mean():.4f}"
          f"  (max drift {np.abs(corr(R) - corr(Rp)).max():.1e})")
    print(f"  volatility clustering  real {ac1(np.abs(mkt)):+.3f} -> shuffled {ac1(np.abs(mktp)):+.3f}"
          "   <- DESTROYED, as intended")
    print("  Preserving each name's total return is the useful part (you cannot pass")
    print("  by riding a bull market) AND the dangerous part (see the table below).\n")


def report_decomposition(R, mom, fwd, me, n_perm, seed) -> None:
    """Show that the as-published null is mostly 'the market', but not ONLY that.

    Note the three numbers are three separate Sharpe ratios, NOT an additive
    split: the Sharpe of (A minus B) is not the Sharpe of A minus the Sharpe of
    B, because subtracting the market also removes most of the volatility. So
    the market-relative figure is computed from its own return series.
    """
    picks = lambda m: np.argpartition(-m, TOP_N, axis=1)[:, :TOP_N]
    rows = np.arange(len(mom))[:, None]
    sr = lambda x: float(x.mean() / x.std(ddof=1) * np.sqrt(12))
    rng = np.random.default_rng(seed)
    long_only, market, relative = (np.empty(n_perm) for _ in range(3))
    for i in range(n_perm):
        mp, fp = momentum_and_forward(R[rng.permutation(len(R))], me)
        top = fp[rows, picks(mp)].mean(axis=1)
        eq = fp.mean(axis=1)
        long_only[i], market[i], relative[i] = sr(top), sr(eq), sr(top - eq)
    print("WHY THE AS-PUBLISHED NULL IS NOT ZERO (three separate Sharpes, not a split):")
    print(f"  null, plain long-only top-{TOP_N}   {long_only.mean():+.3f}   "
          "<- looks like a real edge, but...")
    print(f"  null, equal-weight universe    {market.mean():+.3f}   "
          "<- ...it is ~the market. Legitimate, not momentum's doing")
    print(f"  null, momentum MINUS market    {relative.mean():+.3f}   "
          "<- should be ZERO. This is the free peek ahead")
    print("  So judge momentum against the universe, not against zero -- and even then")
    print("  the shuffle still pays it. That residual is what the table below tests.\n")


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", default="US", help="region key (default: US)")
    ap.add_argument("--synthetic", action="store_true", help="offline synthetic prices")
    ap.add_argument("--permutations", type=int, default=200)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    region = REGIONS[args.region]
    prices, _ = (synthetic_region(region) if args.synthetic
                 else load_region(region, config.START))
    prices = prices.dropna(axis=1, how="any")
    rets = np.log(prices).diff().iloc[1:]
    R = rets.to_numpy()
    me = rebalance_points(rets.index, len(rets))
    mom, fwd = momentum_and_forward(R, me)

    tag = "SYNTHETIC (pipeline test only -- invariant #5)" if args.synthetic else "real prices"
    print(f"\n{region.key} panel: {len(rets)} days x {R.shape[1]} names, "
          f"{prices.index[0].date()} -> {prices.index[-1].date()}  [{tag}]")
    print(f"{len(mom)} monthly rebalances | {args.permutations} shuffles | seed {args.seed}")
    print("All Sharpes are GROSS of costs: a diagnostic of the null, not performance.\n")

    report_invariants(R, np.random.default_rng(args.seed))
    report_decomposition(R, mom, fwd, me, min(args.permutations, 200), args.seed)

    real = excess_sharpe(mom, fwd)
    print(f"REAL 12-1 momentum, market-relative Sharpe: {real:+.3f}\n")
    print("A null is only valid if its own score sits on ZERO -- if shuffled")
    print("history still 'makes money', the comparison is rigged.\n")
    print(f"{'null construction':<32}{'null mean':>10}{'sd':>7}{'% > 0':>8}{'p-value':>10}  why")
    print("-" * 95)

    for label, fn, why in NULLS:
        rng = np.random.default_rng(args.seed)
        scores = np.empty(args.permutations)
        for i in range(args.permutations):
            Rp, mom_p = fn(R, mom, rng)
            if mom_p is None:
                mom_p, fwd_p = momentum_and_forward(Rp, me)
            else:
                fwd_p = fwd
            scores[i] = excess_sharpe(mom_p, fwd_p)
        bias = scores.mean() / scores.std(ddof=1)
        flag = "  <-- centred" if abs(bias) < 0.25 else ""
        print(f"{label:<32}{scores.mean():>+10.3f}{scores.std(ddof=1):>7.3f}"
              f"{(scores > 0).mean():>7.0%}{p_value(scores, real):>10.4f}  {why}{flag}")

    print("\n'null mean' is how much the shuffled market pays a strategy that should")
    print("earn nothing. Only the last row is a real null; the others are rigged.")

    # -- sanity: single instrument, time-series rule (the designed use case) --
    name = prices.columns[0]
    log_px = np.log(prices[name]).to_numpy()
    real_d = best_donchian_sharpe(log_px)
    rng = np.random.default_rng(args.seed)
    daily = np.diff(log_px)
    nulls = np.array([
        best_donchian_sharpe(np.concatenate([[log_px[0]], log_px[0] + np.cumsum(daily[rng.permutation(len(daily))])]))
        for _ in range(args.permutations)
    ])
    print(f"\nSANITY -- single-instrument Donchian on {name}, the case the published")
    print( "method WAS designed for (best-of-10 lookbacks, re-searched on every shuffle):")
    print(f"  real {real_d:+.3f} | null mean {nulls.mean():+.3f} | p = {p_value(nulls, real_d):.4f}")
    print( "  The null is positive here because the shuffle preserves the stock's rise,")
    print( "  and a long-biased rule collects it. That is the method working: it refuses")
    print( "  to credit you for drift you did not have to be clever to capture.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
