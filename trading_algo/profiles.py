"""Named paper-book profiles — canned (strategy, risk, reporting) presets.

A regular paper book runs every sleeve with its region default `StrategyParams`
and the global risk controls, and rolls up into the headline AUM total. A
*profile* lets a book deviate on three axes without touching the region registry
or the config globals:

  * ``param_overrides`` — StrategyParams knobs applied on top of each region's
    defaults (leverage, vol target, filters, long/short mode). Still fed through
    the ONE ``strategy.compute_targets`` (invariant #3) — a profile changes the
    knobs, never the weight logic.
  * ``max_drawdown_stop`` — per-book override of the drawdown circuit breaker
    (``None`` disables it entirely for a max-risk book).
  * ``group`` — which summary a book reports under on the dashboard overview.
    ``CORE`` books sum into the headline AUM; any other group (e.g.
    ``EXPERIMENTAL``) gets its OWN separate total and is excluded from the
    headline, so an unproven book can run live without polluting the number you
    watch.

The chosen profile is baked into the account's state file at ``--init`` time
(``group`` / ``param_overrides`` / ``max_drawdown_stop`` / ``profile``), so the
book stays reproducible and the dashboard can read its shape straight from disk.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import config as cfg

# Reporting groups. CORE is the headline AUM; everything else is a side total.
CORE = "CORE"
EXPERIMENTAL = "EXPERIMENTAL"


@dataclass(frozen=True)
class BookProfile:
    key: str
    label: str                      # dashboard display label
    sub: str                        # one-line subtitle
    group: str                      # reporting group (CORE / EXPERIMENTAL / …)
    allocations: dict[str, float]   # region -> weight for this book
    param_overrides: dict           # StrategyParams knobs on top of region defaults
    max_drawdown_stop: float | None # per-book breaker (None disables it)
    description: str = ""           # human summary (dashboard METHOD tab / CLI)


# NOTE on capital: profiles describe the STRATEGY, not the size — capital is a
# CLI arg (`--capital`). Both experimental books below are meant to be funded
# small (e.g. 10k) and are US-only: deepest liquidity, clean shorting for the
# long/short book, no UK stamp duty, and above the micro-mode threshold so the
# leverage / neutral weights express fully at a 10k stake.
PROFILES: dict[str, BookProfile] = {
    # Max risk acceptance + leverage. Long-only momentum but geared to 3× gross,
    # a high vol target, every timing filter off, and the drawdown breaker
    # DISABLED — it is meant to run hot.
    "ultra": BookProfile(
        key="ultra",
        label="ULTRA · 3× LEVERAGE",
        sub="LEVERAGED MOMENTUM · US · BREAKER OFF",
        group=EXPERIMENTAL,
        allocations={"US": 1.0},
        param_overrides={
            "max_gross": 3.0,          # up to 300% gross exposure
            "max_vol_scale": 5.0,      # let vol targeting gear the raw book hard
            "target_vol": 0.35,        # aggressive annualised vol target
            "regime_filter": False,    # never de-risk to cash on the regime gate
            "abs_momentum_floor": -1.0,# don't require positive momentum
            "stock_trend_ma": 50,      # looser trend filter (short MA)
            "top_n": 6,                # more concentrated than the 10-name core
            "max_weight": 0.60,        # allow big single-name bets
        },
        max_drawdown_stop=None,        # no circuit breaker — true max risk
        description=(
            "Ultra-aggressive long-only US momentum geared to 3× gross with a 35% "
            "vol target, all timing filters off and NO drawdown circuit breaker. "
            "Designed to run hot; expect large swings."),
    ),
    # Market-neutral alpha search: dollar-neutral long/short momentum, hedged to
    # ~0 net so what's left is closer to pure alpha. Keeps the drawdown breaker.
    "experimental": BookProfile(
        key="experimental",
        label="EXP · L/S NEUTRAL",
        sub="MARKET-NEUTRAL LONG/SHORT · US · PURE ALPHA",
        group=EXPERIMENTAL,
        allocations={"US": 1.0},
        param_overrides={
            "long_short": True,        # dollar-neutral long winners / short losers
            "short_n": 6,
            "top_n": 6,
            "regime_filter": False,    # neutral book is regime-agnostic by design
            "abs_momentum_floor": -1.0,# purely cross-sectional ranking
            "target_vol": 0.10,        # modest vol on a hedged book
            "max_gross": 2.0,          # 100% long + 100% short = 200% gross
            "max_vol_scale": 2.0,
            "max_weight": 0.30,
        },
        max_drawdown_stop=cfg.MAX_DRAWDOWN_STOP,   # keep the safety net
        description=(
            "Experimental market-neutral book: long the strongest momentum names, "
            "short the weakest, dollar-neutral so market beta is hedged out and the "
            "residual is closer to pure alpha. Reported on a separate total."),
    ),
}


def get_profile(key: str) -> BookProfile:
    try:
        return PROFILES[key]
    except KeyError:
        raise SystemExit(
            f"Unknown profile {key!r}. Known: {list(PROFILES)}") from None
