"""FX configuration: strategy parameters, risk profiles and paper accounts.

`FXParams` is the single, instrument-agnostic description of the FX strategy
(indicator windows, ensemble behaviour, risk/sizing). Like the equity sleeve's
`StrategyParams`, every part of the pipeline reads its knobs *only* from a passed
`FXParams` instance — never module globals — so backtest and live paper trading
run identically.

Named profiles (`conservative` / `balanced` / `aggressive`) let each paper
account carry its own risk appetite; the two ready-to-run books below give the
account holder a balanced profile and their partner a conservative one.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

# Daily FX bars: ~252 trading days a year (matches the equity metrics module).
ANNUALIZATION = 252


@dataclass(frozen=True)
class FXParams:
    """All knobs for the multi-agent FX strategy."""

    # --- Indicator windows -------------------------------------------------
    ema_fast: int = 20
    ema_slow: int = 100
    adx_window: int = 14
    adx_trend_min: float = 20.0      # ADX above this => trending regime
    rsi_window: int = 14
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    bb_window: int = 20
    bb_z: float = 2.0                 # Bollinger band width in std devs
    donchian_window: int = 55        # breakout channel length (turtle-ish)
    roc_window: int = 60             # rate-of-change momentum horizon
    atr_window: int = 14

    # --- Ensemble (the parallel agent layer) -------------------------------
    agent_weighting: str = "adaptive"  # "equal" | "adaptive"
    agent_lookback: int = 120          # bars used to score each agent (adaptive)
    agent_floor_weight: float = 0.1    # min relative weight any agent keeps
    per_pair_cap: float = 0.25         # max |net weight| per pair (frac of equity)

    # --- Risk / position sizing --------------------------------------------
    target_vol: float = 0.10           # annualised portfolio vol target
    vol_lookback: int = 30             # bars for realised-vol estimate
    avg_correlation: float = 0.30      # cross-pair correlation assumption
    max_gross: float = 3.0             # max gross leverage (Σ|w|)
    max_vol_scale: float = 3.0         # cap on vol-target leverage of raw book

    # --- Costs / execution -------------------------------------------------
    rebalance_min_delta: float = 0.02  # no-churn band: ignore tiny target moves
    include_carry: bool = True         # apply overnight swap/financing

    # --- Drawdown circuit breaker ------------------------------------------
    max_drawdown_stop: float = 0.20    # flatten + cool off past this peak-to-trough
    drawdown_cooldown_days: int = 10

    def with_overrides(self, **kwargs) -> "FXParams":
        return replace(self, **kwargs)


# ---------------------------------------------------------------------------
# Named risk profiles
# ---------------------------------------------------------------------------
_PROFILES: dict[str, FXParams] = {
    "conservative": FXParams(
        target_vol=0.06, max_gross=2.0, max_vol_scale=2.0,
        per_pair_cap=0.20, max_drawdown_stop=0.12, drawdown_cooldown_days=15,
    ),
    "balanced": FXParams(),  # the defaults above
    "aggressive": FXParams(
        target_vol=0.18, max_gross=5.0, max_vol_scale=5.0,
        per_pair_cap=0.35, max_drawdown_stop=0.30, drawdown_cooldown_days=7,
    ),
}


def profile(name: str) -> FXParams:
    try:
        return _PROFILES[name]
    except KeyError:
        raise KeyError(f"Unknown profile {name!r}. Known: {list(_PROFILES)}") from None


def profile_names() -> list[str]:
    return list(_PROFILES)


# ---------------------------------------------------------------------------
# Account / portfolio configuration
# ---------------------------------------------------------------------------
ACCOUNT_CURRENCY = "AUD"             # paper-book equity + reporting currency
FX_RISK_FREE = 0.035                 # AUD cash benchmark for metrics (RBA-ish)
DEFAULT_CAPITAL = 5_000.0           # starting paper capital per account
START = "2015-01-01"                 # default backtest start

# Two ready-to-run paper books: the account holder and their partner, each with
# its own isolated state file and risk profile. Add more here or via the CLI.
ACCOUNTS: dict[str, dict] = {
    "matt":    {"capital": DEFAULT_CAPITAL, "profile": "balanced"},
    "partner": {"capital": DEFAULT_CAPITAL, "profile": "conservative"},
}
