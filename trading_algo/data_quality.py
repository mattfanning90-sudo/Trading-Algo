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
  * near-frozen    — the feed still ticks, but over almost no distinct levels AND
                     at an implausibly low volatility (a degraded/rounded feed).
                     This one matters disproportionately: inverse-vol weighting
                     rewards low measured vol, so a dead feed earns the LARGEST
                     weight and drags the sleeve's vol estimate down far enough
                     to pull maximum vol-target leverage in behind it.
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
from . import pnl

# Thresholds (module-level so they are documented in one place; the gate on/off
# switch lives in config as DATA_QUALITY_GATE).
STALE_DAYS = 5          # this many identical consecutive closes -> stale
GAP_WINDOW = 20         # trailing rows examined for gaps
MAX_GAP_DAYS = 3        # more than this many missing prints in the window -> drop
# Near-frozen feed: BOTH conditions must hold. Either one alone misfires on real
# instruments — a low-priced stock on a coarse tick grid repeats closes while
# genuinely moving, and a short-duration bond ETF is calm but prints a fresh
# close every day. Measured across the live ASX/US/FTSE universes (246 names),
# the conjunction flags only feeds that are actually degraded.
FROZEN_WINDOW = 63      # trailing valid closes examined (matches vol_lookback)
FROZEN_DISTINCT = 0.5   # distinct closes below this share of the window, AND...
FROZEN_VOL = 0.05       # ...annualised vol below this -> the feed is dead
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
# FROZEN_WINDOW gets HEADROOM, not an exact fit: the block is a fixed slice of
# rows, so sizing it to exactly FROZEN_WINDOW means one missing print leaves a
# single valid close too few and the frozen check silently never runs — for
# every name, not just the gapped one.
_SCAN_WINDOW = max(GAP_WINDOW, (STALE_DAYS + 1) * 4, FROZEN_WINDOW * 2, 40)


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
    # A name the feed quotes in another currency than the sleeve's is not a bad
    # price — it is the WRONG price for this book. It carries its own scale in
    # `region.quote_overrides` so any existing holding still marks correctly,
    # but it must not be selected into the sleeve: converting it would need an
    # FX rate, and holding it unconverted breaks invariant #6.
    overrides = dict(getattr(region, "quote_overrides", {}) or {})
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
        if t in overrides:
            report.flag(t, f"quote currency mismatch (scale {overrides[t]} vs "
                           f"region default {region.price_scale})")
            continue
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

        # near-frozen: barely any distinct closes AND implausibly low vol.
        if len(valid) >= FROZEN_WINDOW:
            win = valid[-FROZEN_WINDOW:]
            if win.min() > 0:
                distinct = len(np.unique(win)) / len(win)
                if distinct < FROZEN_DISTINCT:
                    vol = float(np.std(np.diff(win) / win[:-1], ddof=1)) * np.sqrt(252)
                    if vol < FROZEN_VOL:
                        report.flag(t, f"near-frozen ({distinct:.0%} distinct "
                                       f"closes, {vol:.1%} vol)")
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


def missing_names(region, prices: pd.DataFrame, requested: list[str]) -> QualityReport:
    """Requested names with no column at all — they never loaded.

    `assess` judges COLUMNS. A ticker whose download failed has none:
    `data.load_prices` drops the all-NaN column and the name simply ceases to
    exist, so a delisted, renamed or mistyped symbol in `universes.py` reads as
    a smaller universe rather than a broken one. Returns a report flagging
    exactly those names and alerts once per call, so a dead ticker is visible
    in the run log and in the notification channel instead of nowhere.
    """
    absent = [t for t in requested if t not in prices.columns]
    report = QualityReport()
    for t in absent:
        report.flag(t, "no data loaded")
    if absent:
        from . import notifications
        key = getattr(region, "key", "?")
        notifications.notify(
            "dead_tickers",
            f"{key}: {len(absent)} universe name(s) returned no data and were "
            f"dropped: {', '.join(absent)} — a delisted, renamed or mistyped "
            "symbol in universes.py, not a smaller universe",
            level="alert", region=getattr(region, "key", None), tickers=absent)
    return report


# Ratios a corporate action leaves between a close the book traded at and that
# same session's close once the feed has rewritten it: an n-for-1 split
# rewrites history by 1/n, a 1-for-n consolidation by n. 5, 10 and 25 are not
# hypothetical — NOW, NVDA/AVGO/NFLX and BKNG split by them since 2024. 100 is
# a pence/pounds quote change.
_SPLIT_RATIOS = (1.5, 2, 3, 4, 5, 7, 8, 10, 20, 25, 50, 100)
SPLIT_FACTORS = tuple(sorted({1.0 / n for n in _SPLIT_RATIOS}
                             | {float(n) for n in _SPLIT_RATIOS}))
# Relative match. Dividends rewrite history too, pulling a healthy ratio just
# below 1 (0.980-1.005 across the 116 comparable live fills), so a split on a
# name that has also paid out lands a little under its factor. 5% absorbs that
# and keeps every factor's window disjoint from its neighbours' and from 1.
SPLIT_TOLERANCE = 0.05


def suspected_split(prices: pd.DataFrame, held, fills,
                    *, tolerance: float = SPLIT_TOLERANCE) -> dict:
    """Held names whose price history no longer matches the book's own fills.

    Yahoo back-adjusts the WHOLE history for a split on every download (its
    raw Close, which `data.load_prices` builds on, is split-adjusted), so a
    split never shows as a jump inside the series: NVDA's 10-for-1 on 2024-06-10 reads
    120.54 -> 121.44 across it. What changes is the past. The close on the day
    the book last traded a name no longer matches the price the book recorded,
    and the share count is now wrong by exactly that ratio. A market move can
    never do this: it changes later closes, not that one.

    DETECTION ONLY. Adjusting the share count is deliberately out of scope
    (spec §10): a wrong adjustment corrupts the book permanently, while halting
    the sleeve is reversible and visible. Restricted to names actually HELD,
    because a share count is the only thing at risk.

    `fills` is the sleeve's ledger; cash events (`pnl.is_fill` False) record
    cash per share, not a price, and are skipped. The recorded close is the
    row's pre-slippage `decision`, or its `fill` on rows older than that field
    — slippage is a few bp, far inside `tolerance`.

    Returns {ticker: (observed ratio, matched factor)}; empty when nothing
    matches, which is the overwhelmingly common case.
    """
    last: dict = {}
    for f in fills or ():
        if pnl.is_fill(f) and f["date"] >= last.get(f["ticker"], {}).get("date", ""):
            last[f["ticker"]] = f
    out: dict[str, tuple[float, float]] = {}
    for t, shares in dict(held or {}).items():
        f = last.get(t)
        day = pd.Timestamp(f["date"]) if f else None
        if not shares or f is None or t not in prices.columns or day not in prices.index:
            continue
        now, then = float(prices.at[day, t]), float(f.get("decision") or f["fill"])
        if not (now > 0 and then > 0):                 # NaN-safe
            continue
        ratio = now / then
        for factor in SPLIT_FACTORS:
            if abs(ratio - factor) <= tolerance * factor:
                out[t] = (ratio, factor)
                break
    return out


# A dividend VALUE the price contradicts. A real dividend takes about itself off
# the close on its ex-date (the ex-date return regresses on -D/C with slope
# 0.73-1.02 across the four markets, 2012-2026); a typo, a double count or a
# value never rescaled for a later consolidation does not. Only a LARGE one can
# be judged: a normal day moves 1-2.5%, which swamps half of a small dividend.
# Over all 13,143 dividends in the four universes 2012-2026 this rejects 11
# (MRO.L's eight 2012-15 values, AV.L 2022-05-16, IHG.L 2016-05-05, DHR
# 2016-07-05) and keeps every large real one (TSCO.L 2021 21%, MDLZ 2012 33%).
DIVIDEND_CHECK_SIZE = 0.08      # D at least this share of the prior close...
DIVIDEND_MIN_FALL = 0.5         # ...must take at least this share of D off it
# An older rejection is history, not news: the full panel is re-read on every
# run, so it is printed and never posted (verify.HISTORICAL_CUTOFF_DAYS ages
# its findings for the same reason).
DIVIDEND_ALERT_DAYS = 30


def implausible_dividends(close: pd.DataFrame, dividends: pd.DataFrame,
                          *, source: str = "prices") -> tuple[pd.DataFrame, QualityReport]:
    """`dividends` with every value its own price contradicts set to 0, every
    value it cannot judge YET set to NaN, and a report naming each rejection.

    The ONE rule for a dividend value, on both paths a dividend enters a book:
    the backtest's prices (`data._download_primary`, before
    `data.dividend_adjusted`) and the paper book's cash (`data.dividends`).
    A dividend D of at least DIVIDEND_CHECK_SIZE of the ticker's last close
    before its ex-date must be matched by a fall to its first close on or
    after it of at least DIVIDEND_MIN_FALL x D. `close` is the raw
    (split-adjusted) close the dividends belong to, on the same index.

    UNDECIDED (NaN): no close before it (its size is unknown), or a large one
    with no close on or after its ex-date yet. Neither applied nor rejected,
    and not reported: the caller judges it again once the close is in. The
    backtest does so for free (it re-reads full history every run, and an
    undecided value adjusts no close either way); the paper defers it.
    """
    prior = close.ffill().shift(1).reindex(index=dividends.index, columns=dividends.columns)
    after = close.bfill().reindex(index=dividends.index, columns=dividends.columns)
    large = dividends >= DIVIDEND_CHECK_SIZE * prior
    bad = large & (prior - after < DIVIDEND_MIN_FALL * dividends)
    undecided = (dividends > 0) & (prior.isna() | (large & after.isna()))
    report, events = QualityReport(), []
    for i, j in zip(*np.nonzero(bad.to_numpy())):
        day, t = bad.index[i], bad.columns[j]
        d, p, a = (float(f.iat[i, j]) for f in (dividends, prior, after))
        events.append({"ticker": t, "date": str(day.date()), "dividend": d,
                       "prior_close": p, "ex_close": a})
        note = f"{day.date()} {d:g} = {d / p:.1%} of {p:g}, close moved {a / p - 1:+.1%}"
        report.reasons[t] = (report.reasons[t] + "; " + note if t in report.excluded
                             else f"implausible dividend: {note}")
        report.excluded.add(t)
    if events:
        from . import notifications
        recent = (pd.Timestamp(max(e["date"] for e in events))
                  >= pd.Timestamp.now().normalize() - pd.Timedelta(days=DIVIDEND_ALERT_DAYS))
        notifications.notify(
            "implausible_dividend",
            f"{source}: {len(events)} dividend value(s) the price contradicts "
            "were NOT applied — " + " | ".join(f"{t}: {r}" for t, r in report.reasons.items()),
            level="alert" if recent else "info", channel=None if recent else "log",
            source=source, events=events)
    return dividends.mask(bad, 0.0).mask(undecided, np.nan), report


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
