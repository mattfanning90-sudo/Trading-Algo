"""Bar capability: do these bars actually carry an intrabar range?

Some real, citable market data has **one price per bar** and no range at all. The
ECB daily reference fixing (see `frankfurter_data.py`) is the case that forced
this module: it is a genuine observation, but there is no open, no high, no low
and no intraday — so a frame built from it has ``open == high == low == close``.

Why that needs a contract rather than a comment
-----------------------------------------------
`indicators.py`'s range family does **not** raise on a zero-range bar; it
silently computes a *different statistic under the same name* (measured on this
project's own code, close-only vs the same closes with real highs/lows):

* ``true_range`` degenerates from ``max(h−l, |h−c₋₁|, |l−c₋₁|)`` to ``|Δclose|``,
  so **ATR reads ~0.53×** its true value — every ATR-scaled signal, stop and
  target shrinks with it.
* ``adx`` does *not* collapse to zero (the common guess). With ``h = l = c`` the
  directional moves become ``+DM = Δc⁺`` / ``−DM = Δc⁻``, so ``DI⁺ + DI⁻ ≡ 100``
  exactly and ADX becomes 100× the smoothed efficiency ratio of closes. Measured:
  median 21.6 → 17.2, and the share of bars the book calls "trending" falls
  57% → 36%.
* ``donchian`` becomes a channel of *closes* — strictly narrower than the true
  high/low channel — so breakouts trigger earlier and more often (~1.4× as many).

Fed through the real agent → ensemble → risk path, that moves 75–85% of target
weights and puts **16–21% of bars on the opposite side**, while gross leverage,
turnover and the cost bill barely move — so no risk or P&L report would show
that anything had changed. That is the definition of a silent, strategy-altering
bug, and it is why the check lives here as a hard contract.

What is *unaffected* (and therefore still sanctioned): everything that reads only
closes — ``ema``/``sma``/``rsi``/``roc``/``bollinger_z``/``realized_vol``,
`risk.pair_vols` (bit-identical), and the whole money layer (`marks`, `fx_pnl`,
`fxconv`) which never reads high/low. A close-only fixing is a perfectly good
**mark**; it is not a usable **signal** input.

Detection is *structural* (``high == low`` on every observed bar); metadata
(`.attrs`) may only corroborate it, never override — a flag with N optional
checkers is N places to forget, and `.attrs` does not survive a parquet
round-trip. The refusal is raised by the range indicators themselves so no
consumer can forget to ask, and `allow_close_only()` is the one explicit way to
proceed anyway.

Deliberately source-agnostic: `indicators`, `fx_data_quality` and `fx_book` must
not take a dependency on one data adapter to know what a bar is.
"""
from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

import numpy as np
import pandas as pd

# --- the per-book policy vocabulary (FXParams.close_only_signals) -------------
REFUSE = "refuse"       # raise before trading — the default
EXCLUDE = "exclude"     # drop those instruments from the candidate universe
ALLOW = "allow"         # proceed on the degraded statistic, deliberately
POLICIES: tuple[str, ...] = (REFUSE, EXCLUDE, ALLOW)

# The reason string the data-quality gate reports (and the dashboard renders).
CLOSE_ONLY_REASON = "close-only source (no intrabar range)"


class CloseOnlyBarsError(ValueError):
    """A range-based consumer was handed bars that have no true range.

    A `ValueError` because it is a statement about the *data*, not about the
    network or the environment — and it is always raised, never downgraded to a
    warning: the whole point is that the alternative failure mode is silent.
    """


# ---------------------------------------------------------------------------
# The explicit opt-in
# ---------------------------------------------------------------------------
# Process-global on purpose, NOT thread-local: `AgentPool` evaluates agents on a
# ThreadPoolExecutor, so a thread-local acknowledgement set by the caller would
# not be visible in the worker threads where the indicators actually run. A depth
# count (not a bool) so nesting is harmless, lock-guarded so it stays consistent.
#
# The honest caveat that follows from "global": while one book is inside the
# block, a book decided CONCURRENTLY IN THE SAME PROCESS would also be permitted.
# Nothing here does that today — `fx_book.run_all` is sequential and each account
# holds an exclusive lock — but it is why the block must wrap the single decision
# and nothing wider, and why this is not a settable module flag.
_ACK_LOCK = threading.Lock()
_ACK_DEPTH = 0


@contextmanager
def allow_close_only() -> Iterator[None]:
    """Permit range indicators to run on close-only bars inside this block.

    Use it only where the degradation is documented and intended (see
    `FXParams.close_only_signals == "allow"`, which prints and records it).
    """
    global _ACK_DEPTH
    with _ACK_LOCK:
        _ACK_DEPTH += 1
    try:
        yield
    finally:
        with _ACK_LOCK:
            _ACK_DEPTH -= 1


def close_only_acknowledged() -> bool:
    """True inside an `allow_close_only()` block."""
    return _ACK_DEPTH > 0


def check_policy(policy: str) -> str:
    """Validate and normalise a `close_only_signals` policy name."""
    name = policy.strip().lower()
    if name not in POLICIES:
        raise ValueError(f"unknown close_only_signals policy {policy!r}; "
                         f"known: {list(POLICIES)}")
    return name


# ---------------------------------------------------------------------------
# Detection (structural first, metadata only as corroboration)
# ---------------------------------------------------------------------------
def series_has_true_range(high: pd.Series, low: pd.Series) -> bool:
    """True if at least one observed bar has ``high != low``.

    ANY genuine range makes the frame usable: a single halted/quiet bar with
    ``high == low`` is normal, and so is a forward-filled row on a mixed
    calendar (which copies a *real* high/low forward). Only a series where every
    observed bar is a point — i.e. the source never had a range — is refused.

    Bars where either leg is NaN are ignored, and an all-NaN pair returns True:
    "no data" is not the same claim as "no range", and this function must not
    turn a warm-up window into a refusal.

    Deliberately numpy, not pandas: this runs inside every ATR/ADX/Donchian call,
    and pandas' per-operation overhead (tens of microseconds each) would cost more
    than the arithmetic on years of bars. Measured on a 2 900-bar pair: ~35 µs
    here versus ~220 µs for the equivalent pandas expression — ~2.5% of
    `true_range`'s own cost.
    """
    diff = np.abs(np.asarray(high, dtype="float64")
                  - np.asarray(low, dtype="float64"))
    observed = diff[~np.isnan(diff)]
    return not observed.size or bool((observed > 0.0).any())


def has_true_range(bars: pd.DataFrame) -> bool:
    """True if `bars` carries a real intrabar range.

    Structural, with `.attrs` able only to *add* the close-only verdict, never
    remove it: a source that declares itself close-only is believed, and data that
    is structurally a point series is refused whether or not it was labelled.
    """
    if bars.attrs.get("close_only") is True:
        return False
    if not {"high", "low"} <= set(bars.columns):
        return True             # no range columns at all: not this gate's call
    return series_has_true_range(bars["high"], bars["low"])


def close_only_symbols(panel: dict[str, pd.DataFrame]) -> list[str]:
    """Which symbols in `panel` have no intrabar range (sorted).

    Per-symbol on purpose: the panel this exists for is *mixed* — real ECB FX
    fixings beside real ccxt crypto bars — so a whole-panel boolean would answer
    "no" for exactly the case that needs handling.
    """
    return sorted(sym for sym, df in panel.items() if not has_true_range(df))


# ---------------------------------------------------------------------------
# Refusal
# ---------------------------------------------------------------------------
_FIX = ("Use a bar source (yahoo / oanda / ccxt / alpaca) for signals, set the "
        "book's close_only_signals='exclude' to trade without those "
        "instruments, or opt in deliberately with "
        "bar_quality.allow_close_only(). See docs/DATA_FEEDS.md "
        "(\"Close-only sources\").")


def require_series_true_range(high: pd.Series, low: pd.Series,
                              who: str = "this indicator") -> None:
    """Raise unless the high/low pair carries a real range (or was acknowledged).

    The one refusal, called by the range indicators themselves. A panel-level
    variant is deliberately absent: the per-symbol answer lives in
    `close_only_symbols()`, which is what the book's policy gate reports on.
    """
    if close_only_acknowledged() or series_has_true_range(high, low):
        return
    raise CloseOnlyBarsError(
        f"{who} needs a true high/low range, but every bar has high == low — "
        f"these are close-only bars (one observed price per bar, e.g. an ECB "
        f"daily reference fixing). ADX/ATR/Donchian would silently compute a "
        f"close-to-close statistic under a range-based name. {_FIX}")
