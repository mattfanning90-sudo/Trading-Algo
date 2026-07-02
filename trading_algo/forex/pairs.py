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

# Cryptocurrency (USD spot, via Yahoo "BTC-USD" etc.). Crypto trades 24/7, has
# no overnight swap in spot, and runs far higher volatility than G10 FX — the
# vol-targeting risk layer automatically sizes it down, so it slots into the same
# ecosystem. Spreads are wider (~0.2% round trip) than majors.
CRYPTO: dict[str, Pair] = {
    "BTCUSD": Pair("BTCUSD", "BTC", "USD", "BTC-USD", 1.0, 120.0, 0.0, 0.0),
    "ETHUSD": Pair("ETHUSD", "ETH", "USD", "ETH-USD", 1.0, 6.0, 0.0, 0.0),
    "SOLUSD": Pair("SOLUSD", "SOL", "USD", "SOL-USD", 1.0, 0.30, 0.0, 0.0),
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

# US equities (USD-quoted), for the Alpaca / OpenBB intraday feeds. Modelled as
# "pairs" so the same agents/ensemble/book/backtest run unchanged: base = the
# ticker, quote = USD. `pip` is one cent; `spread_pips` is a conservative
# round-trip retail spread in cents (a couple of cents on a liquid name ≈ a basis
# point or two). Equity borrow/financing is not modelled here, so swap = 0 — the
# book's carry term is just zero for these (documented in docs/DATA_FEEDS.md).
EQUITIES: dict[str, Pair] = {
    "AAPL": Pair("AAPL", "AAPL", "USD", "AAPL", 0.01, 2.0, 0.0, 0.0),
    "MSFT": Pair("MSFT", "MSFT", "USD", "MSFT", 0.01, 3.0, 0.0, 0.0),
    "NVDA": Pair("NVDA", "NVDA", "USD", "NVDA", 0.01, 2.0, 0.0, 0.0),
    "SPY":  Pair("SPY",  "SPY",  "USD", "SPY",  0.01, 1.0, 0.0, 0.0),
    "QQQ":  Pair("QQQ",  "QQQ",  "USD", "QQQ",  0.01, 1.0, 0.0, 0.0),
}

# US-listed bond ETFs — the honest, tradable bond vehicle for a paper book
# (direct treasuries need a bond feed/venue we don't have). USD-quoted like the
# equities; pip = one cent; conservative round-trip spreads in cents; financing
# not modelled (swap = 0), same as equities.
BONDS: dict[str, Pair] = {
    "TLT": Pair("TLT", "TLT", "USD", "TLT", 0.01, 2.0, 0.0, 0.0),   # 20y+ treasuries
    "IEF": Pair("IEF", "IEF", "USD", "IEF", 0.01, 2.0, 0.0, 0.0),   # 7–10y treasuries
    "AGG": Pair("AGG", "AGG", "USD", "AGG", 0.01, 1.0, 0.0, 0.0),   # aggregate bond
    "SHY": Pair("SHY", "SHY", "USD", "SHY", 0.01, 1.0, 0.0, 0.0),   # 1–3y treasuries
}

ALL_PAIRS: dict[str, Pair] = {**PAIRS, **CRYPTO, **CROSSES, **EQUITIES, **BONDS}

# Default tradable universe: the seven FX majors plus the three major cryptos.
DEFAULT_UNIVERSE: list[str] = [*PAIRS, *CRYPTO]

# A liquid US-equity universe for the Alpaca / OpenBB feeds (off by default).
EQUITY_UNIVERSE: list[str] = list(EQUITIES)
BOND_UNIVERSE: list[str] = list(BONDS)

# The multi-asset book: stocks + bond ETFs, plus AUDUSD as a currency overlay —
# which also gives the AUD account its translation hub (see fxconv) naturally.
MULTI_ASSET_UNIVERSE: list[str] = [*EQUITIES, *BONDS, "AUDUSD"]


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
