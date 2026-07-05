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

    # --- Residual (market-neutral) momentum (Blitz et al.; rank on beta-stripped
    #     returns to cut momentum's crash beta). Off by default → raw momentum. ---
    use_residual_momentum: bool = False
    resmom_beta_lookback: int = 252  # window for the rolling beta used to residualise

    # --- Value factor (price-based long-term reversal; blends with momentum) -
    use_value: bool = False         # off by default → pure momentum (unchanged)
    value_lookback_days: int = 756  # ~3y window for long-term reversal
    value_skip_days: int = 252      # skip the most recent year (momentum's domain)
    momentum_weight: float = 0.5    # composite = w_mom·rank(mom) + w_val·rank(value)
    value_weight: float = 0.5

    # --- Portfolio construction --------------------------------------------
    top_n: int = 10                 # hold top N momentum names
    max_weight: float = 0.15        # single-name cap
    target_vol: float = 0.12        # annualised portfolio vol target
    vol_lookback: int = 63          # days for realised vol estimate
    max_gross: float = 1.0          # no leverage
    avg_correlation: float = 0.6    # diversification assumption for vol targeting
    max_vol_scale: float = 1.5      # cap on vol-target leverage of the raw book

    # --- Long/short (market-neutral) mode ----------------------------------
    # Off by default → the classic long-only book (unchanged). When on,
    # compute_targets builds a dollar-neutral book: long the top `top_n` momentum
    # names and short the bottom `short_n`, each leg inverse-vol weighted, so the
    # net systematic exposure ≈ 0 and what's left is closer to pure alpha. Shorts
    # carry NEGATIVE weights; gross exposure = Σ|w| is what `max_gross` caps.
    long_short: bool = False
    short_n: int = 0                # names to short (0 → mirror top_n)

    # --- Filters ------------------------------------------------------------
    abs_momentum_floor: float = 0.0  # require positive 12-1 return
    stock_trend_ma: int = 200        # stock must be above its N-day MA
    index_trend_ma: int = 200        # index below its N-day MA -> de-risk to cash
    regime_filter: bool = True       # apply the index regime gate at all

    # --- Rebalancing --------------------------------------------------------
    rebalance: str = "ME"            # pandas offset alias: month-end

    # --- Defensive sleeve (what idle / risk-off capital earns) --------------
    # The momentum book is only ~half invested on average (filters + vol target
    # park the rest). By default that idle fraction earns 0% (cash drag). Set a
    # positive annual rate to model parking it in T-bills; for a real asset
    # (bonds/gold) the backtester takes an explicit `defensive_returns` series
    # instead, which overrides this constant. 0.0 → unchanged (0% cash).
    cash_yield: float = 0.0          # annualised yield on idle capital

    def with_overrides(self, **kwargs) -> "StrategyParams":
        """Return a copy with the given fields replaced (for per-region tuning)."""
        return replace(self, **kwargs)


# Shared default used by every sleeve unless a region overrides it.
DEFAULT_PARAMS = StrategyParams()


@dataclass(frozen=True)
class TrendParams:
    """Knobs for the time-series (trend) momentum diversifier sleeve.

    Distinct from `StrategyParams` because trend is a different strategy: each
    asset is traded long/short on its OWN trend (not ranked cross-sectionally),
    sized by inverse volatility to a portfolio vol target. Defaults follow the
    AQR "century of evidence" recipe (1/3/12-month signal blend, ~10% vol).
    """
    lookbacks: tuple = (21, 63, 252)   # 1/3/12-month trend horizons (trading days)
    vol_lookback: int = 90             # days for the inverse-vol position sizing
    target_vol: float = 0.10           # annualised portfolio vol target
    max_vol_scale: float = 2.0         # cap on vol-target leverage of the raw book
    max_gross: float = 3.0             # gross-exposure cap (diversified L/S; NOTE:
    #                                    >1 needs futures/margin — see trend.py docstring)
    avg_correlation: float = 0.20      # low: positions span 4 asset classes
    long_only: bool = False            # True = long-or-flat (no shorting; ETF-only)
    rebalance: str = "ME"              # month-end, like the equity sleeves
    min_history_days: int = 260        # need ~12m before the first signal
    cost_bps: float = 3.0              # per side (commission+slippage), liquid ETFs

    def with_overrides(self, **kwargs) -> "TrendParams":
        return replace(self, **kwargs)


DEFAULT_TREND_PARAMS = TrendParams()


@dataclass(frozen=True)
class CarryParams:
    """Knobs for the cross-asset carry diversifier sleeve.

    Carry = the return earned if prices don't move (here proxied by trailing
    income/distribution yield across a basket of yield-bearing ETFs). Traded
    cross-sectionally — long the high-carry assets, short the low-carry ones —
    inverse-vol-sized to a portfolio vol target, same L/S engine as trend.
    A third, largely uncorrelated premium for the multi-strategy book.
    """
    yield_lookback: int = 252          # trailing window for the income-yield estimate
    vol_lookback: int = 90             # days for inverse-vol position sizing
    target_vol: float = 0.10           # annualised portfolio vol target
    max_vol_scale: float = 2.0         # cap on vol-target leverage of the raw book
    max_gross: float = 3.0             # gross-exposure cap (L/S; >1 needs margin)
    avg_correlation: float = 0.30      # carry assets share more beta than trend's
    long_short: bool = True            # True = demean (L/S); False = long-only tilt
    rebalance: str = "ME"              # month-end, like the other sleeves
    min_history_days: int = 260        # need ~12m before the first yield estimate
    cost_bps: float = 3.0              # per side (commission+slippage), liquid ETFs

    def with_overrides(self, **kwargs) -> "CarryParams":
        return replace(self, **kwargs)


DEFAULT_CARRY_PARAMS = CarryParams()


@dataclass(frozen=True)
class LowRiskParams:
    """Knobs for the low-risk / betting-against-beta (BAB) diversifier sleeve.

    Long low-beta names, short high-beta names — the leverage-constraint premium
    (Frazzini-Pedersen). Sorts on a *risk characteristic* (rolling beta to the
    regime index), so it is structurally orthogonal to the return-based momentum/
    value/trend sleeves. Inverse-vol sized to a vol target via the shared L/S engine.
    """
    beta_lookback: int = 252           # window for the rolling beta estimate (~1y)
    vol_lookback: int = 90             # days for inverse-vol position sizing
    target_vol: float = 0.10           # annualised portfolio vol target
    max_vol_scale: float = 2.0         # cap on vol-target leverage of the raw book
    max_gross: float = 3.0             # gross-exposure cap (L/S; >1 needs margin)
    avg_correlation: float = 0.40      # low-beta names co-move (defensive cluster)
    long_short: bool = True            # True = demean (L/S); False = long-only tilt
    rebalance: str = "ME"              # month-end, like the other sleeves
    min_history_days: int = 300        # need ~12m+ before the first beta estimate
    cost_bps: float = 5.0              # per side; higher than ETFs (single-name turnover)
    # Single-name L/S needs guards the ETF sleeves don't: floor the per-name vol so
    # inverse-vol sizing can't hand a near-constant name a huge weight, and cap each
    # name's weight so one short can't lose >100% in a day and blow up the book.
    vol_floor: float = 0.10            # min annualised vol for inverse-vol sizing
    max_weight_per_name: float = 0.05  # per-name weight cap (gross), L and S
    min_price: float = 5.0             # exclude sub-$5 penny stocks (illiquid; the
    #                                    high-beta shorts that blew up the naive sleeve)

    def with_overrides(self, **kwargs) -> "LowRiskParams":
        return replace(self, **kwargs)


DEFAULT_LOWRISK_PARAMS = LowRiskParams()


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
# Cooldown length in distinct MARKET DAYS (not runs) flat after a breach before
# re-entry. Paper trading counts unique report dates, so the engine firing
# several times a day does not shorten it; ~21 trading days ≈ 1 month.
DRAWDOWN_COOLDOWN_DAYS = 21

# Annual borrow spread (over the risk-free rate) charged on the leveraged portion
# (gross exposure > 1) of the multi-strategy book. Leverage is not free; the
# combiner charges this so a levered CAGR isn't silently overstated.
LEVERAGE_FINANCING_SPREAD = 0.01   # 1% over rf on the borrowed fraction

# Minimum viable equity (in BASE_CURRENCY) for a sleeve to trade. Below this the
# per-trade commission floors dominate, so the sleeve holds cash instead of
# bleeding fees. Set 0 to disable the gate.
MIN_VIABLE_EQUITY_BASE = 500.0

# Minimum days between paper-trading rebalances. The monthly rebalance fires on
# the first run of a new calendar month; without a floor, funding a book late in
# a month (e.g. the 28th) churns the whole book two days later on the 1st,
# locking in losses on positions that never got a real holding period. This gates
# the calendar trigger so a freshly-funded book gets a proper hold first. Set 0
# to disable (pure calendar-month cadence). Does not affect the backtest.
MIN_REBALANCE_GAP_DAYS = 20

# ---------------------------------------------------------------------------
# Survivorship correction (backlog F13, data integrity)
# ---------------------------------------------------------------------------
# Replacement return applied on the day a held name delists with no further price
# (Shumway: ~-30% NYSE/AMEX, ~-55% Nasdaq). None disables the correction. Only
# takes effect in the point-in-time backtest path; see trading_algo/delisting.py.
DELISTING_REPLACEMENT_RETURN: float | None = None

# ---------------------------------------------------------------------------
# Market-data fallback (backlog F14, platform)
# ---------------------------------------------------------------------------
# Name of a registered secondary price source to try when the primary (Yahoo)
# returns nothing (e.g. a 403). None = primary only. Registered in data.py via
# data.register_fallback(); fallback data still passes the F7 quality gate.
DATA_FALLBACK_SOURCE: str | None = None

# ---------------------------------------------------------------------------
# Data-quality gate (backlog F7 / foundation P0-D)
# ---------------------------------------------------------------------------
# Run the shared pre-signal validator (stale / gapped / outlier / impossible-move
# detection) before compute_targets, in BOTH backtest and paper/live. Flagged
# names are dropped from the candidate set; in paper trading a flagged name that
# is already held is frozen (not traded on a bad price). Default on — a bad print
# silently corrupts every downstream number. See trading_algo/data_quality.py.
DATA_QUALITY_GATE = True

# ---------------------------------------------------------------------------
# State-file integrity (backlog F18 / foundation P0-H)
# ---------------------------------------------------------------------------
# Validate paper_state_{account}.json against state_schema on load/save. When
# True a corrupted-but-parseable file makes the run FAIL SAFE (raises, never
# resets equity or trades on a garbage file); when False the validator only
# warns (shadow mode). Default off during rollout — see product/backlog F18.
VALIDATE_STATE_FILES = False

