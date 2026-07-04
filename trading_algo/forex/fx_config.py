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
    # "equal"    : straight average of agents (the robust 1/N baseline)
    # "adaptive" : weight by trailing information ratio per pair
    # "hedge"    : Hedge / multiplicative-weights (Cesa-Bianchi & Lugosi) with a
    #              fixed-share floor — provable regret, low overfitting surface
    agent_weighting: str = "hedge"
    agent_lookback: int = 120          # bars used to score / window losses
    agent_floor_weight: float = 0.1    # fixed-share floor (min relative weight)
    hedge_eta: float = 1.0             # Hedge learning rate (small = more shrinkage)
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
    bar: str = "1d"                    # informational: intended data bar interval

    # --- Asset-class concentration cap ---------------------------------------
    # Crypto legs are near-perfectly correlated with each other: several crypto
    # positions are effectively ONE bet. Cap total crypto gross (Σ|w|) at this
    # fraction of equity (None = off, e.g. for a crypto-only book).
    crypto_gross_cap: float | None = 0.25

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
        crypto_gross_cap=0.15,
    ),
    "balanced": FXParams(),  # the defaults above
    "aggressive": FXParams(
        target_vol=0.18, max_gross=5.0, max_vol_scale=5.0,
        per_pair_cap=0.35, max_drawdown_stop=0.30, drawdown_cooldown_days=7,
        crypto_gross_cap=0.40,
    ),
    # Medium-frequency / intraday: shorter windows tuned for 15m–60m bars.
    # NOT high-frequency — see docs/HFT_REALITY.md. Live use needs a real-time
    # broker feed (OANDA/IBKR); Yahoo intraday is delayed/limited.
    "intraday": FXParams(
        ema_fast=10, ema_slow=40, donchian_window=20, roc_window=24,
        vol_lookback=24, agent_lookback=48, bb_window=20,
        target_vol=0.10, max_gross=3.0, bar="60m",
    ),
    # High-frequency-CAPABLE crypto (minute scale; NOT microsecond HFT — see
    # docs/CRYPTO_HF.md). Short windows, crypto-sized risk, a churn band to keep
    # 1-minute turnover (and cost) sane. Run via `engine --loop` on a low-latency
    # VPS with `--exchange binance --bar 1m`.
    "hf_crypto": FXParams(
        ema_fast=12, ema_slow=48, donchian_window=24, roc_window=30,
        vol_lookback=60, agent_lookback=120, bb_window=20,
        target_vol=0.20, max_gross=3.0, per_pair_cap=0.40,
        max_drawdown_stop=0.15, drawdown_cooldown_days=240,
        rebalance_min_delta=0.05, include_carry=True, bar="1m",
        crypto_gross_cap=None,           # crypto-ONLY book: the cap would strangle it
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

# Ready-to-run paper books, each an isolated state file with its own capital,
# risk profile, universe and bar cadence. Add more here or via the CLI.
#   matt / partner — the original daily FX+crypto books.
#   daytrader      — the DAY-TRADING book: $10k, intraday profile on 60m bars,
#                    advanced hourly by the day-paper workflow. Honest note:
#                    Yahoo intraday is ~15-min delayed — fine for paper cadence,
#                    not a live-feed simulation.
#   multiasset     — the full STOCK + BOND book: $10k, daily bars, US equities +
#                    bond ETFs plus an AUDUSD overlay (which doubles as the AUD
#                    translation hub for exact AUD marking).
from .pairs import MULTI_ASSET_UNIVERSE  # noqa: E402  (no circularity: pairs is leaf)

ACCOUNTS: dict[str, dict] = {
    "matt":       {"capital": DEFAULT_CAPITAL, "profile": "balanced"},
    "partner":    {"capital": DEFAULT_CAPITAL, "profile": "conservative"},
    "daytrader":  {"capital": 10_000.0, "profile": "intraday", "bar": "60m"},
    "multiasset": {"capital": 10_000.0, "profile": "balanced",
                   "symbols": MULTI_ASSET_UNIVERSE},
}
