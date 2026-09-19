"""Shuffle an OHLC panel into a market with no exploitable structure.

What this is for
----------------
A backtest number means nothing until you know what the same number looks like
on a market where there is provably nothing to find. This builds that market out
of your own bars: keep every daily move exactly as it was, destroy only the
ORDER they arrived in. Order is the only thing a strategy can learn from.

What survives, and why each matters
-----------------------------------
* **Each symbol's total return** — shuffling a set of numbers preserves their
  sum, so every permutation ends at exactly the same price. The null therefore
  already contains the market's drift, and refuses to credit a strategy for
  simply being long a rising market.
* **Each symbol's volatility and fat tails** — the same moves, reordered.
* **Cross-symbol co-movement** — ONE shuffle is shared by every symbol. This is
  not a detail: correlation is what sets a portfolio's volatility, so shuffling
  symbols independently would quietly hand the null a diversification bonus the
  real strategy never had, and the strategy would "fail" against a rigged null.
* **Each bar's internal shape** — a whole bar moves as one unit: its gap, high,
  low and close all travel together, so a bar is never taken apart,
  `high >= max(open, close)` still holds, and a range-less bar (close-only
  feeds, see `bar_quality.py`) stays range-less.

What dies: trends, momentum, mean reversion, and volatility clustering — every
pattern a rule could exploit.

Deliberate divergence from the published algorithm
--------------------------------------------------
The reference implementation (neurotrader888/mcpt) shuffles a bar's gap with a
SEPARATE permutation from its intrabar high/low/close. We do not, because that
breaks the "preserve everything but the order" contract above.

A bar's close-to-close return is `gap + (close - open)`. Shuffling those two
with different permutations pairs one bar's gap with another bar's interior,
which zeroes `Cov(gap, close - open)` and therefore CHANGES close-to-close
volatility. Measured on our own FX panels, that covariance is far from zero —
`corr(gap, intrabar)` is **-0.15 to -0.23** across EURUSD/GBPUSD/USDJPY — and
destroying it inflates the null market's volatility by **~2.2%** and shifts
pairwise cross-symbol correlations by up to **0.026**.

A null whose volatility does not match the real market is a rigged null, which
is the one thing this module exists to prevent. Keeping the gap with its own bar
preserves the covariance exactly, at the cost of leaving one relationship
intact: "a gap up tends to be followed by a drift back down *within the same
bar*". That is a within-bar effect, and every swarm archetype trades off bar
closes, so none of them can exploit it. If an intrabar strategy is ever added,
revisit this trade-off.

Validity note
-------------
This null is correct for **time-series** strategies, which judge an instrument
against its own past — that is what every swarm archetype does. It is NOT valid
for **cross-sectional** strategies that rank instruments against each other,
because preserving each symbol's total return leaks whole-sample performance
into the ranking. See `docs/PERMUTATION_TESTING.md` §4 for the measurement.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

OHLC = ["open", "high", "low", "close"]


def permute_panel(panel: dict[str, pd.DataFrame], *, seed: int,
                  start_index: int = 0) -> dict[str, pd.DataFrame]:
    """Return a shuffled copy of `panel`.

    `panel` maps symbol -> OHLC frame; all frames must share one index (use
    `fx_data.load_panel`, which aligns them).

    `start_index` keeps bars before it as REAL data and shuffles only what
    follows — the hybrid a walk-forward permutation test needs (real history,
    noise after the walk-forward start). The bar AT `start_index` is the real
    anchor the rebuilt series grows from.
    """
    symbols = list(panel)
    if not symbols:
        return {}
    index = panel[symbols[0]].index
    for sym in symbols:
        if not panel[sym].index.equals(index):
            raise ValueError(f"{sym}: index does not match {symbols[0]}; align the panel first")

    n_bars = len(index)
    if not 0 <= start_index < n_bars:
        raise ValueError(f"start_index {start_index} outside panel of {n_bars} bars")

    perm_index = start_index + 1
    perm_n = n_bars - perm_index
    if perm_n <= 1:                       # nothing meaningful to shuffle
        return {s: panel[s].copy() for s in symbols}

    # ONE shuffle: whole bars move together (gap stays with its own interior),
    # and it is shared across every symbol. Both properties are load-bearing —
    # see "Deliberate divergence" and the co-movement note in the docstring.
    rng = np.random.default_rng(seed)
    order = rng.permutation(perm_n)

    out: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        frame = panel[sym]
        raw = frame[OHLC].to_numpy(dtype=float)
        if not np.isfinite(raw).all():
            raise ValueError(f"{sym}: OHLC contains NaN/inf; clean the panel first")
        log_bars = np.log(raw)

        # Everything expressed RELATIVE, so it can be reordered and re-stacked.
        gap = log_bars[1:, 0] - log_bars[:-1, 3]        # open vs previous close
        rel_high = log_bars[:, 1] - log_bars[:, 0]      # the rest vs this bar's open
        rel_low = log_bars[:, 2] - log_bars[:, 0]
        rel_close = log_bars[:, 3] - log_bars[:, 0]

        g = gap[start_index:][order]                    # gap[i-1] is bar i's gap
        h = rel_high[perm_index:][order]
        lo = rel_low[perm_index:][order]
        c = rel_close[perm_index:][order]

        new = log_bars.copy()                           # prefix stays real
        # close_i = close_{i-1} + gap_i + (close-open)_i  ->  a cumulative sum
        closes = log_bars[start_index, 3] + np.cumsum(g + c)
        opens = np.concatenate([[log_bars[start_index, 3]], closes[:-1]]) + g
        new[perm_index:, 0] = opens
        new[perm_index:, 1] = opens + h
        new[perm_index:, 2] = opens + lo
        new[perm_index:, 3] = closes

        rebuilt = frame.copy()
        rebuilt[OHLC] = np.exp(new)
        out[sym] = rebuilt
    return out
