"""Global configuration: strategy parameters + portfolio settings.

`StrategyParams` is the single, region-agnostic description of the momentum
strategy. Every region uses `DEFAULT_PARAMS` unless it supplies an override in
`regions.py`. The signal/strategy/backtest code reads parameters *only* from a
`StrategyParams` instance passed in — never from module globals — so the same
logic runs identically for every sleeve and in both backtest and paper trading.
"""
from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class StrategyParams:
    """All knobs for the 12-1 cross-sectional momentum strategy."""

    # --- Signal -------------------------------------------------------------
    lookback_days: int = 252        # 12-month momentum window
    skip_days: int = 21             # skip most recent month (short-term reversal)
    min_history_days: int = 300     # exclude names with insufficient history

    # --- Portfolio construction --------------------------------------------
    top_n: int = 10                 # hold top N momentum names
    max_weight: float = 0.15        # single-name cap
    target_vol: float = 0.12        # annualised portfolio vol target
    vol_lookback: int = 63          # days for realised vol estimate
    max_gross: float = 1.0          # no leverage
    avg_correlation: float = 0.6    # diversification assumption for vol targeting
    max_vol_scale: float = 1.5      # cap on vol-target leverage of the raw book

    # --- Filters ------------------------------------------------------------
    abs_momentum_floor: float = 0.0  # require positive 12-1 return
    stock_trend_ma: int = 200        # stock must be above its N-day MA
    index_trend_ma: int = 200        # index below its N-day MA -> de-risk to cash
    regime_filter: bool = True       # apply the index regime gate at all

    # --- Rebalancing --------------------------------------------------------
    rebalance: str = "ME"            # pandas offset alias: month-end

    def with_overrides(self, **kwargs) -> "StrategyParams":
        """Return a copy with the given fields replaced (for per-region tuning)."""
        return replace(self, **kwargs)


# Shared default used by every sleeve unless a region overrides it.
DEFAULT_PARAMS = StrategyParams()


# ---------------------------------------------------------------------------
# Portfolio-level configuration
# ---------------------------------------------------------------------------

BASE_CURRENCY = "AUD"               # combined equity + reporting currency

# Capital split across regional sleeves (must reference region keys in regions.py).
# Equal third each — rebalanced back to target on the configured cadence.
ALLOCATIONS: dict[str, float] = {
    "ASX": 1 / 3,
    "US": 1 / 3,
    "FTSE": 1 / 3,
}

# How often to true sleeve capital back to ALLOCATIONS (pandas offset alias).
# "ME" = every month-end rebalance; "YE" = annually; None = never (let them drift).
ALLOCATION_REBALANCE = "ME"

# Cost charged when moving cash between sleeves across currencies (FX spread).
FX_SPREAD_BPS = 5.0

# Backtest / account sizing (in BASE_CURRENCY).
START = "2012-01-01"
INITIAL_CAPITAL = 100_000

# Annualised cash rate used as the risk-free benchmark in metrics (AUD ~ RBA cash).
RISK_FREE = 0.035

# ---------------------------------------------------------------------------
# Risk controls
# ---------------------------------------------------------------------------
# Drawdown circuit breaker: if the book falls more than this from its peak,
# liquidate to cash and sit out for a cooldown, then resume. Set None to disable.
MAX_DRAWDOWN_STOP = 0.25            # 25% peak-to-trough
DRAWDOWN_COOLDOWN_DAYS = 21         # ~1 month flat after a breach before re-entry

# Minimum viable equity (in BASE_CURRENCY) for a sleeve to trade. Below this the
# per-trade commission floors dominate, so the sleeve holds cash instead of
# bleeding fees. Set 0 to disable the gate.
MIN_VIABLE_EQUITY_BASE = 500.0

