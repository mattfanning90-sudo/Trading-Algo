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
from . import bar_quality

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

    # --- Holding policy (see position_policy.py) ---------------------------
    # Entry/exit hysteresis. A single threshold makes a target hovering at the
    # boundary open and close repeatedly, paying half the spread each way; the
    # GAP between these two is what stops that. `entry_threshold` is the |weight|
    # needed to OPEN (or to reverse onto the other side); `exit_threshold` is the
    # |weight| at or below which an open position is closed. Must satisfy
    # exit <= entry. Both 0.0 = off (plain no-churn band, the prior behaviour).
    entry_threshold: float = 0.0
    exit_threshold: float = 0.0
    # Bars a fresh position is protected from being CLOSED, so a signal gets the
    # chance to resolve instead of being cut as noise. A target sign reversal and
    # the drawdown breaker both override it — those are risk events, not churn.
    min_hold_bars: int = 0

    # --- Data capability: what to do with CLOSE-ONLY bars ------------------
    # Some real sources publish one price per bar and no range at all (the ECB
    # daily fixing behind `frankfurter_data`), and ADX/ATR/Donchian silently
    # compute a different statistic on such data — see `bar_quality.py` for the
    # measurements. So the choice is made once, per book, in the open:
    # "refuse" (default) raises before trading, "exclude" freezes those
    # instruments out of the candidate universe, "allow" proceeds on the degraded
    # reading and records it. Validated in __post_init__ so a typo can never
    # silently mean "refuse".
    close_only_signals: str = "refuse"

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

    def __post_init__(self) -> None:
        # Frozen dataclass: validate only, never assign. A bad policy name must
        # fail at construction, not degrade to a default nobody chose.
        bar_quality.check_policy(self.close_only_signals)
        # exit > entry inverts the hysteresis into a machine that opens on weak
        # conviction and closes on strong — silently the opposite of the intent,
        # and invisible in the metrics until the spread bill arrives.
        if self.exit_threshold > self.entry_threshold:
            raise ValueError(
                f"exit_threshold ({self.exit_threshold}) must be <= "
                f"entry_threshold ({self.entry_threshold}): the exit band sits "
                f"INSIDE the entry band, otherwise a position closes on more "
                f"conviction than it took to open")
        if min(self.entry_threshold, self.exit_threshold) < 0:
            raise ValueError("entry_threshold / exit_threshold must be >= 0")
        if self.min_hold_bars < 0:
            raise ValueError("min_hold_bars must be >= 0")

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

# --- Broker costs: IBKR's published schedule -------------------------------
# Sourced from IBKR's own pricing pages (2026-09), not invented. IBKR Pro /
# Tiered, which is what an Australian resident trades on (IBKR Lite is US-only).
#
# MARGIN. Tier I (USD 0-100k) = IBKR Benchmark (Fed Funds effective) + 1.50%,
# quoted at 5.12%. The spread narrows to +0.25% above USD 3M and the rate is
# blended across tiers — irrelevant at this book's size, but that is why it is a
# single number here rather than a tier table. It MOVES with the benchmark;
# re-check it rather than trusting this constant indefinitely.
MARGIN_RATE_ANNUAL = 0.0512
# BORROW. IBKR bills the actual securities-lending rate, which is per-instrument
# and moves daily — there is no published constant. 0.25% is an indicative
# general-collateral / easy-to-borrow level, which is what every name in the
# multi-asset universe is. A hard-to-borrow name can be multiples of this.
SHORT_BORROW_ANNUAL = 0.0025

# COMMISSION. The FX stack charged spread ONLY, which is right for FX (the
# dealing spread IS the cost) and wrong for equities and bonds, where IBKR bills
# per SHARE with a per-ORDER minimum. On a small book the minimum dominates: a
# USD 0.35 floor on a USD 1,000 position is 3.5 bps a side, ~50x SPY's
# half-spread. Modelling it is the difference between a plausible-looking small
# book and an honest one.
#
# Amounts are charged in the ACCOUNT currency. IBKR bills these in USD, so for
# the AUD books the floor is understated by the AUD/USD rate (~A$0.53, not
# A$0.35) — a known, documented approximation; the per-order floor's EXISTENCE
# is what changes the answer, not its last 35%.
IBKR_EQUITY_PER_SHARE = 0.0037   # 0.0035 tiered + 0.0002 clearing
IBKR_EQUITY_MIN_ORDER = 0.35     # per order
IBKR_EQUITY_MAX_PCT = 0.01       # capped at 1% of trade value
IBKR_FX_BPS = 0.20               # FX is bps of notional, not per share
# IBKR's USD 2.00 FX per-order minimum is DEFAULTED OFF, deliberately. These
# books' execution venue is not specified anywhere — the data comes from Yahoo
# and ccxt, and the repo ships an OANDA adapter; OANDA charges spread only and
# allows micro lots. Turning it on models IBKR IDEALPRO specifically, where a
# A$5k book rebalancing 16 pairs pays ~USD 32 a BAR and is wiped out inside a
# year. That is a true statement about IDEALPRO, not about the strategy, so it
# is opt-in rather than a silent default. Measured impact: docs/DATA_FEEDS.md.
IBKR_FX_MIN_ORDER = 0.00         # set to 2.00 to model IBKR IDEALPRO

# VENUE MINIMUM ORDER SIZE. IBKR's FX desk (IDEALPRO) requires an account over
# USD 25,000 AND a minimum order of 20,000 units of the base currency. A A$5-10k
# book rebalancing 16 pairs trades ~A$900 a leg — those orders CANNOT BE PLACED,
# so charging them a USD 2.00 minimum models a fee on an order that does not
# exist, and compounds a book straight to zero.
#
# Orders below the venue minimum are therefore SKIPPED, not charged: the book
# simply cannot reach its target that bar. That is what a real account would
# experience, and it is the same idea as the equity side's
# `config.MIN_VIABLE_EQUITY_BASE`. Set to 0.0 to disable the check.
# DEFAULTED OFF for the same reason as the FX per-order minimum: it encodes
# IBKR IDEALPRO's access rules, and nothing declares IDEALPRO as the venue. Set
# to {"fx": 20_000.0} to model it — at which point a A$5-10k book can place no
# FX order at all and its turnover collapses ~27x, which is the honest IDEALPRO
# answer and the reason these books would need to be far larger to trade there.
VENUE_MIN_ORDER_NOTIONAL: dict[str, float] = {}
DEFAULT_CAPITAL = 5_000.0           # starting paper capital per account
# Yahoo carries the FX majors from 2003-12-01 (EURGBP from 1999); verified
# 2026-09-16. Training started 2015 against that, and the neural layer overfits
# at 24k rows — history is the cheapest data there is.
START = "2003-12-01"                 # default backtest start

# Ready-to-run paper books, each an isolated state file with its own capital,
# risk profile, universe, bar cadence and DATA SOURCE. Add more here or via the
# CLI. `source` is the default a NEW book is opened with (init_defaults); an
# existing book keeps the source persisted in its own state until a human
# overrides it once with `--source` (which the book then remembers).
#
#   matt / partner — the original daily FX+crypto books, on `auto` (per-symbol
#                    routing): crypto legs from a real exchange via ccxt (free,
#                    keyless), FX legs from Yahoo bars. FX is deliberately NOT
#                    routed to the ECB fixing — those bars have no intrabar range
#                    (see feeds.ROUTES / bar_quality.py / docs/DATA_FEEDS.md).
#   daytrader      — the DAY-TRADING book: $10k, intraday profile on 60m bars,
#                    advanced hourly by the day-paper workflow. Stays on `yahoo`:
#                    a routed intraday panel would splice ccxt bar edges onto
#                    Yahoo's (unverified alignment). Honest note: Yahoo intraday
#                    is ~15-min delayed — a real hourly-cadence paper exercise,
#                    not a live-feed simulation.
#   multiasset     — the full STOCK + BOND book: $10k, daily bars, US equities +
#                    bond ETFs plus an AUDUSD overlay (which doubles as the AUD
#                    translation hub for exact AUD marking).
from .pairs import MULTI_ASSET_UNIVERSE  # noqa: E402  (no circularity: pairs is leaf)

ACCOUNTS: dict[str, dict] = {
    "matt":       {"capital": DEFAULT_CAPITAL, "profile": "balanced",
                   "source": "auto"},
    "partner":    {"capital": DEFAULT_CAPITAL, "profile": "conservative",
                   "source": "auto"},
    "daytrader":  {"capital": 10_000.0, "profile": "intraday", "bar": "60m",
                   "source": "yahoo"},
    # universe-locked => never receives the FX-trained neural agent
    # (see fx_book.run_once ML gate).
    "multiasset": {"capital": 10_000.0, "profile": "balanced",
                   "source": "yahoo", "symbols": MULTI_ASSET_UNIVERSE},
}
