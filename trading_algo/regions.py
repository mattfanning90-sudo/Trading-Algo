"""Region registry — one entry per regional sleeve.

A `Region` bundles everything that differs between the FTSE, US and ASX books:
universe, regime index, currency, fee schedule, market calendar, the Yahoo
ticker convention, the IBKR routing details, and any per-region strategy
overrides. Everything downstream is parameterised by a `Region`, so adding a
fourth market (e.g. TSX or HKEX) is just one more entry here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time

from . import universes
from .config import DEFAULT_PARAMS, StrategyParams


@dataclass(frozen=True)
class Region:
    key: str                       # short id, e.g. "ASX"
    name: str
    currency: str                  # local trading currency: AUD / USD / GBP
    index_ticker: str              # Yahoo index for the regime filter
    yahoo_suffix: str              # appended to bare symbols on Yahoo (".AX", "", ".L")
    ibkr_exchange: str             # IBKR routing exchange ("ASX", "SMART", "LSE")
    timezone: str                  # IANA tz for the local market
    market_open: time              # local cash-session open
    market_close: time             # local cash-session close
    commission_bps: float          # broker commission, basis points of notional
    min_commission: float          # commission floor, in local currency
    slippage_bps: float            # modelled slippage per side, basis points
    stamp_duty_bps: float          # tax on BUYS only (UK SDRT); 0 elsewhere
    price_scale: float             # multiply raw Yahoo price by this to get `currency`
    universe: list[str] = field(default_factory=list)
    params: StrategyParams = DEFAULT_PARAMS
    constituents_file: str | None = None   # optional point-in-time membership (CSV/parquet)
    # Defensive assets the idle/risk-off sleeve can rotate into, in the region's
    # OWN currency (invariant #6 — never import another currency into a sleeve).
    # Logical name -> ticker, e.g. {"tbill": "BIL", "bonds": "IEF", "gold": "GLD"}.
    defensive_assets: dict[str, str] = field(default_factory=dict)
    # Rebalance "dust" floor in this region's LOCAL currency: a per-name trade
    # smaller than this is skipped so the commission floor doesn't dominate.
    # Per-region (not a shared constant) so the threshold is comparable in AUD
    # across markets instead of applying one number to AUD/USD/GBP/CAD alike.
    min_trade_value: float = 500.0

    @property
    def all_tickers(self) -> list[str]:
        """Universe plus the regime index — everything to download for the sleeve."""
        return [*self.universe, self.index_ticker]

    def to_local(self, raw_price: float) -> float:
        """Convert a raw Yahoo quote into the region's trading currency.

        LSE ordinary shares are quoted in pence (GBX); price_scale=0.01 turns
        them into pounds so the sleeve is internally consistent in GBP.
        """
        return raw_price * self.price_scale


REGIONS: dict[str, Region] = {
    "ASX": Region(
        key="ASX",
        name="Australia (ASX)",
        currency="AUD",
        index_ticker="^AXJO",          # S&P/ASX 200
        yahoo_suffix=".AX",
        ibkr_exchange="ASX",
        timezone="Australia/Sydney",
        market_open=time(10, 0),
        market_close=time(16, 0),
        commission_bps=8.0,            # IBKR ASX ~0.08%
        min_commission=5.0,            # A$5 floor
        slippage_bps=10.0,
        stamp_duty_bps=0.0,
        price_scale=1.0,
        universe=universes.ASX,
        min_trade_value=500.0,         # ~A$500 dust floor
    ),
    "US": Region(
        key="US",
        name="United States",
        currency="USD",
        index_ticker="^GSPC",          # S&P 500
        yahoo_suffix="",
        ibkr_exchange="SMART",
        timezone="America/New_York",
        market_open=time(9, 30),
        market_close=time(16, 0),
        commission_bps=2.0,            # IBKR US ~ very low (per-share approx as bps)
        min_commission=1.0,            # US$1 floor
        slippage_bps=5.0,              # deep liquidity in large caps + ETFs
        stamp_duty_bps=0.0,
        price_scale=1.0,
        universe=universes.US,
        # USD-denominated defensive assets (match the sleeve currency):
        # BIL = 1-3m T-bills (carry), IEF = 7-10y Treasuries (carry + crash rally),
        # GLD = gold (crisis hedge, no yield).
        defensive_assets={"tbill": "BIL", "bonds": "IEF", "gold": "GLD"},
        min_trade_value=330.0,         # ~A$500 in USD
    ),
    "FTSE": Region(
        key="FTSE",
        name="United Kingdom (LSE)",
        currency="GBP",
        index_ticker="^FTSE",          # FTSE 100
        yahoo_suffix=".L",
        ibkr_exchange="LSE",
        timezone="Europe/London",
        market_open=time(8, 0),
        market_close=time(16, 30),
        commission_bps=5.0,            # IBKR LSE ~0.05%
        min_commission=1.0,            # £1 floor
        slippage_bps=8.0,
        stamp_duty_bps=50.0,           # UK SDRT 0.5% on share PURCHASES
        price_scale=0.01,              # pence (GBX) -> pounds (GBP)
        universe=universes.FTSE,
        min_trade_value=260.0,         # ~A$500 in GBP
    ),
    # 4th sleeve, scaffolded but UNFUNDED: fully backtestable via
    # `run_backtest --region TSX`, but intentionally absent from
    # config.ALLOCATIONS until a walk-forward backtest justifies capital.
    "TSX": Region(
        key="TSX",
        name="Canada (TSX)",
        currency="CAD",
        index_ticker="^GSPTSE",        # S&P/TSX Composite
        yahoo_suffix=".TO",
        ibkr_exchange="TSE",           # IBKR Toronto Stock Exchange
        timezone="America/Toronto",
        market_open=time(9, 30),
        market_close=time(16, 0),
        commission_bps=3.0,            # IBKR Canada tiered ~0.03% equiv
        min_commission=1.0,            # C$1 floor
        slippage_bps=8.0,              # liquid large caps, thinner than US mega
        stamp_duty_bps=0.0,
        price_scale=1.0,
        universe=universes.TSX,
        min_trade_value=450.0,         # ~A$500 in CAD
    ),
}


def get_region(key: str) -> Region:
    try:
        return REGIONS[key]
    except KeyError:
        raise KeyError(f"Unknown region {key!r}. Known: {list(REGIONS)}") from None


def all_region_keys() -> list[str]:
    return list(REGIONS)
