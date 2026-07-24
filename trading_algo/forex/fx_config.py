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

from dataclasses import dataclass

from ..config import (
    COOLDOWN_BARS,
    Cooldown,
    RiskParams,
    lookup_registry,
)

# Daily FX bars: ~252 trading days a year (matches the equity metrics module).
ANNUALIZATION = 252


@dataclass(frozen=True)
class FXParams(RiskParams):
    """All knobs for the multi-agent FX strategy.

    Subclasses the shared `RiskParams` (trading_algo/config.py): the vol-targeting
    knobs (`target_vol`, `vol_lookback`, `avg_correlation`, `max_gross`,
    `max_vol_scale`) live there, re-declared below with the FX book's own
    (looser) defaults. `with_overrides` is inherited.
    """

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

    # --- Risk / position sizing (overrides the RiskParams base defaults) ----
    target_vol: float = 0.10           # annualised portfolio vol target
    vol_lookback: int = 30             # bars for realised-vol estimate
    avg_correlation: float = 0.30      # cross-pair correlation assumption
    max_gross: float = 3.0             # max gross leverage (Σ|w|)
    max_vol_scale: float = 3.0         # cap on vol-target leverage of raw book

    # --- Costs / execution -------------------------------------------------
    rebalance_min_delta: float = 0.02  # no-churn band: ignore tiny target moves
    include_carry: bool = True         # apply overnight swap/financing
    bar: str = "1d"                    # informational: intended data bar interval

    # --- Asset-class concentration caps --------------------------------------
    # Crypto legs are near-perfectly correlated with each other: several crypto
    # positions are effectively ONE bet. Cap total crypto gross (Σ|w|) at this
    # fraction of equity (None = off, e.g. for a crypto-only book).
    crypto_gross_cap: float | None = 0.25
    # Per-asset-class gross caps (Σ|w| per class, fraction of equity), applied
    # by risk.size_book by scaling that class's legs down proportionally.
    # Immutable tuple-of-pairs (FXParams is frozen; a dict default won't do);
    # None as a cap value disables that class. Classes come from
    # pairs.Pair.asset_class. FX carries NO entry (uncapped — G10 pairs are
    # idiosyncratic enough and max_gross still binds); crypto stays driven by
    # the dedicated `crypto_gross_cap` knob above (back-compat), which
    # risk.size_book merges into these at run time. Defaults: US equities are
    # one cluster (~0.75 gross); bond ETFs are one duration bet (~0.50 gross).
    class_gross_caps: tuple[tuple[str, float | None], ...] = (
        ("equity", 0.75), ("bond", 0.50),
    )

    # --- Drawdown circuit breaker ------------------------------------------
    max_drawdown_stop: float = 0.20    # flatten + cool off past this peak-to-trough
    # decremented once per bar — scale to the profile's bar (10 for daily,
    # 240 for 60m; see hf_crypto's 240-for-1m convention). Kept as a bare int so
    # existing callers (fx_book / fx_backtest) read it unchanged; the unit is
    # exposed explicitly via the `cooldown` property below (R2).
    drawdown_cooldown_days: int = 10

    @property
    def cooldown(self) -> Cooldown:
        """This book's drawdown cooldown, tagged with its unit (BARS for FX)."""
        return Cooldown(self.drawdown_cooldown_days, COOLDOWN_BARS)


# ---------------------------------------------------------------------------
# Named risk profiles
# ---------------------------------------------------------------------------
_PROFILES: dict[str, FXParams] = {
    "conservative": FXParams(
        target_vol=0.06, max_gross=2.0, max_vol_scale=2.0,
        per_pair_cap=0.20, max_drawdown_stop=0.12, drawdown_cooldown_days=15,
        # Phase-0 crypto bleed-stop (2026-07): the FX technical agents have
        # NEGATIVE directional edge on crypto (measured hit-rate < 50% on
        # BTC/ETH/SOL), so directional crypto here is expected-loss. Cap it
        # hard until the market-neutral funding cash-and-carry book replaces it
        # (see docs/backlog/crypto-subsystem.md). Reduces, does NOT eliminate.
        crypto_gross_cap=0.05,
        class_gross_caps=(("equity", 0.60), ("bond", 0.40)),
    ),
    # balanced is the default profile except its crypto budget is cut for the
    # same Phase-0 reason as conservative (was the 0.25 default).
    "balanced": FXParams(crypto_gross_cap=0.10),
    "aggressive": FXParams(
        target_vol=0.18, max_gross=5.0, max_vol_scale=5.0,
        per_pair_cap=0.35, max_drawdown_stop=0.30, drawdown_cooldown_days=7,
        crypto_gross_cap=0.40,
        class_gross_caps=(("equity", 1.00), ("bond", 0.75)),
    ),
    # Medium-frequency / intraday: shorter windows tuned for 15m–60m bars.
    # NOT high-frequency — see docs/HFT_REALITY.md. Live use needs a real-time
    # broker feed (OANDA/IBKR); Yahoo intraday is delayed/limited.
    "intraday": FXParams(
        ema_fast=10, ema_slow=40, donchian_window=20, roc_window=24,
        vol_lookback=24, agent_lookback=48, bb_window=20,
        target_vol=0.10, max_gross=3.0, bar="60m",
        # cooldown decrements once per NEW BAR: 240 hourly bars = 10 trading
        # days x 24 bars (mirrors hf_crypto's explicit 240-for-1m convention).
        max_drawdown_stop=0.20, drawdown_cooldown_days=240,
        # The daytrader book runs this profile over DEFAULT_UNIVERSE (FX +
        # BTC/ETH/SOL), so it carries the SAME Phase-0 defensive crypto cap as
        # 'balanced' (0.10) — never the loose 0.25 default (B2).
        crypto_gross_cap=0.10,
        # class_gross_caps: inherits the balanced defaults (equity .75 / bond .50).
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
        # class_gross_caps: default is inert here (no equities/bonds in universe).
    ),
}


def profile(name: str) -> FXParams:
    # Shared registry accessor (R1); libraries expect a KeyError on a bad name.
    return lookup_registry(_PROFILES, name, kind="profile")


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
    # universe-locked => never receives the FX-trained neural agent
    # (see fx_book.run_once ML gate).
    "multiasset": {"capital": 10_000.0, "profile": "balanced",
                   "symbols": MULTI_ASSET_UNIVERSE},
}
