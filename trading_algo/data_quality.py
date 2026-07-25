"""Pre-signal data-quality gate (backlog F7 / foundation P0-D).

Signals are only as good as the prices feeding them. A stale, gapped or
split-corrupted print — a known yfinance failure mode (see README) — can silently
corrupt every downstream number and, in live/paper trading, generate a real order
off garbage. This module is the ONE validator both the backtester and the
paper/live engine call, right before `strategy.compute_targets`, so they exclude
the same bad names identically (invariant #3 stays intact — this only trims the
*candidate* set fed to the single weight function, it never re-weights).

No lookahead (invariant #1): every check uses only prices up to and including the
as-of date. `eligible()` composes with point-in-time membership (F1) by
intersection, and is a perfect no-op when `config.DATA_QUALITY_GATE` is off or
nothing is flagged (it returns the base eligibility unchanged, including None).

Checks, as-of a rebalance date:
  * dead price     — latest close is NaN / <= 0, or too little history to trade
  * staleness      — the last N closes are identical (a frozen / stuck feed)
  * gap            — too many missing prints in the trailing window
  * impossible move — a 1-day return beyond a region-aware threshold (a likely
                      unadjusted split/spike); flagged conservatively since there
                      is no corporate-action calendar yet (a known F7 limitation).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import config as cfg

# Thresholds (module-level so they are documented in one place; the gate on/off
# switch lives in config as DATA_QUALITY_GATE).
STALE_DAYS = 5          # this many identical consecutive closes -> stale
GAP_WINDOW = 20         # trailing rows examined for gaps
MAX_GAP_DAYS = 3        # more than this many missing prints in the window -> drop
# Fallback "impossible move" threshold for a region record that does not carry
# its own `jump_threshold` (e.g. a duck-typed object in a test). The real per-
# region values now live on the Region record (regions.py): 0.50 default,
# 0.30 for GBP/FTSE.
JUMP_DEFAULT = 0.50     # |1-day return| above this is "impossible"


@dataclass
class QualityReport:
    """Names excluded as-of a date, and why."""
    excluded: set = field(default_factory=set)
    reasons: dict = field(default_factory=dict)

    def flag(self, ticker: str, reason: str) -> None:
        # keep the first (most specific) reason per ticker
        if ticker not in self.excluded:
            self.excluded.add(ticker)
            self.reasons[ticker] = reason


def _jump_threshold(region) -> float:
    # Read the per-region/per-currency threshold straight off the Region record
    # (folded in by refactor R3); fall back to the default for a record that
    # predates the field.
    return getattr(region, "jump_threshold", JUMP_DEFAULT)


# Trailing rows read for the per-name checks. Every check below looks at either
# the last GAP_WINDOW rows or the last STALE_DAYS+1 *valid* closes, so a window
# this size covers any column that isn't heavily gapped; the rare column that
# yields too few valid prints inside it falls back to its full history (see
# `_valid_tail`), which is what makes this identical to a full scan rather than
# merely equivalent.
_SCAN_WINDOW = max(GAP_WINDOW, (STALE_DAYS + 1) * 4, 40)


def _valid_tail(block: np.ndarray, col: pd.Series, upto: int) -> np.ndarray:
    """The trailing non-NaN closes for one name, cheaply.

    Reads them out of the already-materialised `block` (a bounded trailing slice
    shared by every column). Only if that window holds too few valid prints to
    answer the staleness / 1-day-return checks does it pay for a full-history
    `dropna` on that single column.
    """
    v = block[~np.isnan(block)]
    if len(v) >= STALE_DAYS + 1:
        return v
    return col.iloc[:upto + 1].dropna().to_numpy(dtype=float)


def assess(prices: pd.DataFrame, region, asof: pd.Timestamp) -> QualityReport:
    """Flag names whose price data is untrustworthy as-of `asof` (trailing only).

    Stateless and bounded: the per-name checks read a fixed trailing slice as one
    NumPy block instead of re-slicing and `dropna`-ing the full history per
    column, which is what made this O(history × names) on every rebalance. The
    one check that genuinely needs all history — "are there ≥2 prints ever?" — is
    a single vectorised `notna().sum()`. Output is unchanged.
    """
    report = QualityReport()
    loc = prices.index.searchsorted(asof, side="right") - 1
    if loc < 0:                      # snap to the last available date <= asof
        return report

    jump_thr = _jump_threshold(region)
    sub = prices.iloc[:loc + 1]

    # "Enough history to trade at all" is the only check that spans everything;
    # one vectorised pass answers it for every name at once.
    n_valid = sub.notna().sum().to_numpy()
    # Missing prints inside the trailing gap window, all names at once.
    gap_block = sub.iloc[-GAP_WINDOW:]
    n_missing = gap_block.isna().sum().to_numpy()
    gap_span = len(gap_block)
    # One bounded slice materialised once and read per column below.
    scan = sub.iloc[-_SCAN_WINDOW:].to_numpy(dtype=float, na_value=np.nan)

    for j, t in enumerate(prices.columns):
        if n_valid[j] < 2:
            report.flag(t, "insufficient history")
            continue

        valid = _valid_tail(scan[:, j], prices[t], loc)
        last = valid[-1]
        if not np.isfinite(last) or last <= 0:
            report.flag(t, f"dead price ({last})")
            continue

        # staleness: last STALE_DAYS+1 valid closes all identical
        tail = valid[-(STALE_DAYS + 1):]
        if len(tail) >= STALE_DAYS + 1 and float(tail.max() - tail.min()) == 0.0:
            report.flag(t, f"stale ({STALE_DAYS}+ unchanged closes)")
            continue

        # gap: too many missing prints in the trailing window
        missing = int(n_missing[j])
        if missing > MAX_GAP_DAYS:
            report.flag(t, f"gappy ({missing} missing in {gap_span})")
            continue

        # impossible move: latest 1-day return beyond the region threshold
        prev = valid[-2]
        if prev > 0:
            ret = last / prev - 1.0
            if abs(ret) > jump_thr:
                report.flag(t, f"impossible move ({ret:+.0%} > {jump_thr:.0%})")
                continue

    return report


def eligible(prices: pd.DataFrame, region, asof: pd.Timestamp,
             base: set | None = None) -> tuple[set | None, QualityReport]:
    """The eligible candidate set after the quality gate, and the report.

    Returns `(base, empty_report)` unchanged when the gate is off or nothing is
    flagged — so a clean run is bit-for-bit identical to no gate at all. When
    names are flagged, returns (universe - flagged), intersected with `base`
    (point-in-time membership) when one is given.
    """
    if not getattr(cfg, "DATA_QUALITY_GATE", True):
        return base, QualityReport()
    report = assess(prices, region, asof)
    if not report.excluded:
        return base, report
    universe = set(prices.columns) if base is None else set(base)
    return universe - report.excluded, report
