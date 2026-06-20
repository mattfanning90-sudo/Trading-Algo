"""Currency-pair registry — one `Pair` record per tradable FX instrument.

Mirrors the design of the equity sleeve's `Region` registry: everything that
differs between instruments (pip size, the Yahoo ticker convention, the typical
dealing spread, and the overnight financing/carry) lives in one immutable
record, so the agents, risk and execution layers stay instrument-agnostic.

Conventions
-----------
* A pair's *price* is quote-currency per 1 unit of base (EURUSD ≈ 1.08 USD/EUR).
* `pip` is the price increment of one pip: 0.0001 for most pairs, 0.01 for the
  JPY crosses.
* `spread_pips` is the typical round-trip dealing spread in pips (retail/IBKR-ish,
  deliberately conservative). The book charges half of it per side.
* `swap_long_pips` / `swap_short_pips` are the *daily* financing in pips for
  holding one unit of notional long / short overnight. Positive means you are
  paid to hold that side (positive carry). These are illustrative steady-state
  levels — the live system would refresh them from the broker.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Pair:
    symbol: str                # canonical id, e.g. "EURUSD"
    base: str                  # base currency (you are long this when long the pair)
    quote: str                 # quote currency (price is quote per base)
    yahoo_ticker: str          # Yahoo FX ticker, e.g. "EURUSD=X"
    pip: float                 # price value of one pip
    spread_pips: float         # typical dealing spread, in pips (round trip)
    swap_long_pips: float      # daily financing for a long, in pips (+ = you earn)
    swap_short_pips: float     # daily financing for a short, in pips (+ = you earn)

    @property
    def is_jpy(self) -> bool:
        return self.quote == "JPY" or self.base == "JPY"

    def spread_fraction(self, price: float) -> float:
        """Round-trip spread as a fraction of price (cross it twice = full spread)."""
        if not price or price != price or price <= 0:
            return 0.0
        return (self.spread_pips * self.pip) / price

    def carry_fraction(self, price: float, side: int) -> float:
        """Daily carry as a fraction of notional for a long (+1) or short (-1)."""
        if not price or price != price or price <= 0 or side == 0:
            return 0.0
        swap = self.swap_long_pips if side > 0 else self.swap_short_pips
        return (swap * self.pip) / price


# The seven majors — deepest liquidity, tightest spreads, best for low latency.
PAIRS: dict[str, Pair] = {
    "EURUSD": Pair("EURUSD", "EUR", "USD", "EURUSD=X", 0.0001, 0.6, -0.10, -0.20),
    "GBPUSD": Pair("GBPUSD", "GBP", "USD", "GBPUSD=X", 0.0001, 0.9, -0.05, -0.25),
    "USDJPY": Pair("USDJPY", "USD", "JPY", "USDJPY=X", 0.01, 0.7, 0.55, -0.95),
    "AUDUSD": Pair("AUDUSD", "AUD", "USD", "AUDUSD=X", 0.0001, 0.8, 0.05, -0.30),
    "USDCAD": Pair("USDCAD", "USD", "CAD", "USDCAD=X", 0.0001, 1.0, 0.10, -0.35),
    "USDCHF": Pair("USDCHF", "USD", "CHF", "USDCHF=X", 0.0001, 1.0, 0.45, -0.80),
    "NZDUSD": Pair("NZDUSD", "NZD", "USD", "NZDUSD=X", 0.0001, 1.2, 0.10, -0.35),
}

# Extra crosses available but off by default — flip into DEFAULT_UNIVERSE to use.
CROSSES: dict[str, Pair] = {
    "EURGBP": Pair("EURGBP", "EUR", "GBP", "EURGBP=X", 0.0001, 0.9, -0.08, -0.15),
    "EURJPY": Pair("EURJPY", "EUR", "JPY", "EURJPY=X", 0.01, 1.2, 0.35, -0.75),
    "GBPJPY": Pair("GBPJPY", "GBP", "JPY", "GBPJPY=X", 0.01, 1.6, 0.40, -0.85),
    "AUDJPY": Pair("AUDJPY", "AUD", "JPY", "AUDJPY=X", 0.01, 1.4, 0.45, -0.80),
    "AUDNZD": Pair("AUDNZD", "AUD", "NZD", "AUDNZD=X", 0.0001, 1.5, 0.02, -0.10),
    "EURAUD": Pair("EURAUD", "EUR", "AUD", "EURAUD=X", 0.0001, 1.6, -0.15, -0.05),
}

ALL_PAIRS: dict[str, Pair] = {**PAIRS, **CROSSES}

# Default tradable universe: the seven majors.
DEFAULT_UNIVERSE: list[str] = list(PAIRS)


def get_pair(symbol: str) -> Pair:
    try:
        return ALL_PAIRS[symbol]
    except KeyError:
        raise KeyError(f"Unknown pair {symbol!r}. Known: {list(ALL_PAIRS)}") from None


def currencies_in(symbols: list[str]) -> set[str]:
    """Every currency referenced by a set of pairs (for FX reporting/conversion)."""
    out: set[str] = set()
    for s in symbols:
        p = get_pair(s)
        out.update((p.base, p.quote))
    return out
