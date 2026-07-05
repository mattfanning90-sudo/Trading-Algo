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
JUMP_DEFAULT = 0.50     # |1-day return| above this is "impossible" (US / ASX)
JUMP_GBP = 0.30         # tighter for GBP names (FTSE)


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
    return JUMP_GBP if getattr(region, "currency", None) == "GBP" else JUMP_DEFAULT


def assess(prices: pd.DataFrame, region, asof: pd.Timestamp) -> QualityReport:
    """Flag names whose price data is untrustworthy as-of `asof` (trailing only)."""
    report = QualityReport()
    if asof not in prices.index:
        # snap to the last available date <= asof
        loc = prices.index.searchsorted(asof, side="right") - 1
        if loc < 0:
            return report
        asof = prices.index[loc]

    window = prices.loc[:asof]
    jump_thr = _jump_threshold(region)

    for t in prices.columns:
        col = window[t]
        valid = col.dropna()
        if len(valid) < 2:
            report.flag(t, "insufficient history")
            continue

        last = valid.iloc[-1]
        if not np.isfinite(last) or last <= 0:
            report.flag(t, f"dead price ({last})")
            continue

        # staleness: last STALE_DAYS+1 valid closes all identical
        tail = valid.iloc[-(STALE_DAYS + 1):]
        if len(tail) >= STALE_DAYS + 1 and float(tail.max() - tail.min()) == 0.0:
            report.flag(t, f"stale ({STALE_DAYS}+ unchanged closes)")
            continue

        # gap: too many missing prints in the trailing window
        recent = col.iloc[-GAP_WINDOW:]
        missing = int(recent.isna().sum())
        if missing > MAX_GAP_DAYS:
            report.flag(t, f"gappy ({missing} missing in {len(recent)})")
            continue

        # impossible move: latest 1-day return beyond the region threshold
        prev = valid.iloc[-2]
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
