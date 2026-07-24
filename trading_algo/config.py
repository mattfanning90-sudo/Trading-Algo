"""Global configuration: strategy parameters + portfolio settings.

`StrategyParams` is the single, region-agnostic description of the momentum
strategy. Every region uses `DEFAULT_PARAMS` unless it supplies an override in
`regions.py`. The signal/strategy/backtest code reads parameters *only* from a
`StrategyParams` instance passed in — never from module globals — so the same
logic runs identically for every sleeve and in both backtest and paper trading.
"""
from __future__ import annotations

from dataclasses import dataclass, replace


# ---------------------------------------------------------------------------
# Shared risk/params base (foundation refactor R1)
# ---------------------------------------------------------------------------
# The equity `StrategyParams` and the FX `FXParams` both size a book the same
# way — scale a raw signal to a target vol subject to a gross-leverage cap — so
# those five knobs mean the SAME thing on both sides. Defining them once, here,
# makes that shared vocabulary explicit; each subclass overrides the defaults
# it wants and adds its own instrument-specific knobs. `FXParams` (in
# trading_algo/forex/fx_config.py) subclasses this too.
@dataclass(frozen=True)
class RiskParams:
    """Vol-targeting / leverage knobs shared by every parameter set."""

    target_vol: float = 0.12        # annualised portfolio vol target
    vol_lookback: int = 63          # bars/days for the realised-vol estimate
    avg_correlation: float = 0.6    # diversification assumption for vol targeting
    max_gross: float = 1.0          # gross-leverage cap (Σ|w|); 1.0 = no leverage
    max_vol_scale: float = 1.5      # cap on vol-target leverage of the raw book

    def with_overrides(self, **kwargs):
        """Return a copy with the given fields replaced (for per-region tuning).

        Preserves the concrete subclass (`dataclasses.replace` keeps `type(self)`).
        """
        return replace(self, **kwargs)


# ---------------------------------------------------------------------------
# Drawdown-cooldown: a length WITH AN EXPLICIT UNIT (foundation refactor R2)
# ---------------------------------------------------------------------------
# The equity breaker counts distinct MARKET DAYS; the FX breaker counts decision
# BARS (10 for daily, 240 for 60m/1m). Historically both were called
# "…cooldown_days", which read as the same unit but was not. Tagging the unit
# onto the value removes the ambiguity and lets one shared helper interpret it.
COOLDOWN_MARKET_DAYS = "market_days"
COOLDOWN_BARS = "bars"


@dataclass(frozen=True)
class Cooldown:
    """A drawdown-breaker cooldown length together with the unit it is counted in.

    `length` is the number of decrement steps the breaker sits out; `unit` names
    what one step is (a distinct market day, or one decision bar) so nothing has
    to guess. The raw integer both sides already store IS the step count for
    their own loop, so this is a pure disambiguation — no length changes.
    """

    length: int
    unit: str = COOLDOWN_MARKET_DAYS

    def __post_init__(self) -> None:
        if self.unit not in (COOLDOWN_MARKET_DAYS, COOLDOWN_BARS):
            raise ValueError(
                f"Unknown cooldown unit {self.unit!r}; "
                f"expected {COOLDOWN_MARKET_DAYS!r} or {COOLDOWN_BARS!r}")

    @property
    def steps(self) -> int:
        """Number of decrement steps to count down (unit-agnostic count)."""
        return int(self.length)


def cooldown_steps(cd: Cooldown) -> int:
    """Shared interpreter: how many decrement steps a Cooldown means, whatever
    its unit. Both the equity and FX breakers decrement once per step."""
    return cd.steps


# ---------------------------------------------------------------------------
# Shared named-registry lookup (foundation refactor R1)
# ---------------------------------------------------------------------------
def lookup_registry(registry: dict, name: str, *, kind: str,
                    on_missing: type[BaseException] = KeyError):
    """Return `registry[name]` or raise `on_missing` with a 'Known: […]' message.

    Centralises the identical accessor the region, equity-profile and FX-profile
    registries each hand-rolled. The caller chooses the exception type so each
    keeps its own contract (CLI tools want a clean `SystemExit`; libraries want a
    `KeyError`)."""
    try:
        return registry[name]
    except KeyError:
        raise on_missing(
            f"Unknown {kind} {name!r}. Known: {list(registry)}") from None


@dataclass(frozen=True)
class StrategyParams(RiskParams):
    """All knobs for the 12-1 cross-sectional momentum strategy.

    Subclasses `RiskParams`: the vol-targeting knobs (`target_vol`,
    `vol_lookback`, `avg_correlation`, `max_gross`, `max_vol_scale`) are inherited
    from there — the defaults below are the long-only equity book's values.
    """

    # --- Signal -------------------------------------------------------------
    lookback_days: int = 252        # 12-month momentum window
    skip_days: int = 21             # skip most recent month (short-term reversal)
    min_history_days: int = 300     # exclude names with insufficient history

    # --- Value factor (price-based long-term reversal; blends with momentum) -
    use_value: bool = False         # off by default → pure momentum (unchanged)
    value_lookback_days: int = 756  # ~3y window for long-term reversal
    value_skip_days: int = 252      # skip the most recent year (momentum's domain)
    momentum_weight: float = 0.5    # composite = w_mom·rank(mom) + w_val·rank(value)
    value_weight: float = 0.5

    # --- Portfolio construction --------------------------------------------
    # (vol targeting: target_vol / vol_lookback / max_gross / avg_correlation /
    #  max_vol_scale are inherited from RiskParams with the equity defaults.)
    top_n: int = 10                 # hold top N momentum names
    max_weight: float = 0.15        # single-name cap

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

# Paper-trading only (backlog F4): also true the paper book's sleeves back to
# target allocation on each monthly rebalance, transferring CASH across sleeves
# and paying FX_SPREAD_BPS on the crossing amount (like the portfolio backtest).
# Default off — the paper sim funds each sleeve once and lets it drift (the
# realistic treasury model); turn on to keep allocations pinned to target.
PAPER_ALLOCATION_REBALANCE = False

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
# Cooldown flat after a breach before re-entry, counted in distinct MARKET DAYS
# (not runs): paper trading counts unique report dates, so the engine firing
# several times a day does not shorten it; ~21 trading days ≈ 1 month. The unit
# is carried explicitly on the Cooldown (R2) so a shared helper reads it without
# guessing; DRAWDOWN_COOLDOWN_DAYS stays as the bare-int alias existing callers
# (backtest.py / paper_trade.py) read.
DRAWDOWN_COOLDOWN = Cooldown(21, COOLDOWN_MARKET_DAYS)
DRAWDOWN_COOLDOWN_DAYS = DRAWDOWN_COOLDOWN.length

# Minimum viable equity (in BASE_CURRENCY) for a sleeve to trade. Below this the
# per-trade commission floors dominate, so the sleeve holds cash instead of
# bleeding fees. Set 0 to disable the gate.
MIN_VIABLE_EQUITY_BASE = 500.0

# ---------------------------------------------------------------------------
# Paper->live promotion gate (backlog F10)
# ---------------------------------------------------------------------------
# The hard gate before a book can trade real money (trading_algo/promotion.py):
# a live order is refused unless every criterion below is met, or an explicit
# human override is given (and audited). Set PROMOTION_GATE False to disable
# (not recommended).
PROMOTION_GATE = True
MIN_PROMOTION_REBALANCES = 6        # distinct monthly rebalances of paper history
PROMOTION_DSR_MIN = 0.95           # F2 Deflated-Sharpe floor
PROMOTION_PBO_MAX = 0.5            # F2 Probability-of-Backtest-Overfitting ceiling
PROMOTION_TRACKING_BUDGET_BPS = 200.0   # F3 live-vs-backtest tracking-error budget

# Minimum days between paper-trading rebalances. The monthly rebalance fires on
# the first run of a new calendar month; without a floor, funding a book late in
# a month (e.g. the 28th) churns the whole book two days later on the 1st,
# locking in losses on positions that never got a real holding period. This gates
# the calendar trigger so a freshly-funded book gets a proper hold first. Set 0
# to disable (pure calendar-month cadence). Does not affect the backtest.
MIN_REBALANCE_GAP_DAYS = 20

# ---------------------------------------------------------------------------
# Notifications / telemetry (backlog F12 / foundation P0-F)
# ---------------------------------------------------------------------------
# Delivery channel for risk alerts (drawdown breaker, crowding). "log" prints;
# register a webhook/email channel in notifications.py and name it here to route
# alerts off-box. See trading_algo/notifications.py.
NOTIFY_CHANNEL = "log"

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
# Pre-trade ADV / liquidity cap (backlog F15 / foundation P0-I)
# ---------------------------------------------------------------------------
# Cap each position at this fraction of the name's trailing average DOLLAR volume
# so the book never targets more than it could realistically trade. None = off
# (no cap — a perfect no-op). The cap is applied inside strategy.compute_targets
# so backtest and paper size identically (invariant #3). Needs volume data.
ADV_CAP_PCT: float | None = None
ADV_WINDOW = 20                    # trailing days for the average dollar volume

# Market-impact cost coefficient (backlog F6): adds an Almgren-style square-root
# impact term (coef · vol · sqrt(order/ADV$)) to the backtest cost. None/0 = off
# (the flat commission+slippage model, F16 baseline unchanged). Needs volume.
# Borrow/short-financing cost is intentionally deferred until a short book exists.
IMPACT_COEF: float | None = None

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
# warns (shadow mode). Default ON: fail-safe before real capital — a book must
# halt on a garbage file, never silently reset. See product/backlog F18.
VALIDATE_STATE_FILES = True

