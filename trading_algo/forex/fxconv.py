"""Translate each pair's quote-currency P&L into the AUD account currency.

The paper books and backtest are **AUD-denominated**, but a pair's price moves in
its QUOTE currency — EURUSD in USD, USDJPY in JPY, BTCUSD in USD. To trade a pair
from an AUD account you first convert AUD into that quote currency, so moves in
AUD/quote are part of your real P&L. Treating every open position as held in its
quote currency, the AUD return of a signed weight ``w`` over a bar is::

    w * [ (price_now / price_last) * (aud_per_quote_now / aud_per_quote_last) - 1 ]

i.e. the pair move AND the quote→AUD move, both on the position's notional; idle
cash stays in AUD. (Shorts are treated as quote-currency-denominated — a documented
convention; for G10/crypto longs the first-order term is the AUD/USD move, which is
exactly what an AUD trader feels.)

AUD-per-quote rates are derived from the FX majors already in the panel, with
**AUDUSD as the hub** — no extra data needed for the standard FX book (which holds
AUDUSD, USDJPY, USDCAD, USDCHF). When a needed rate is absent (e.g. a crypto-only
book with no AUDUSD in its panel) the factor falls back to ``1.0`` (no conversion)
so nothing breaks — such a book simply reports in its quote currency until an
AUD/quote rate is available.
"""
from __future__ import annotations

import pandas as pd

# currency -> the major whose price gives USD per 1 unit of that currency
_USD_DIRECT = {"AUD": "AUDUSD", "EUR": "EURUSD", "GBP": "GBPUSD", "NZD": "NZDUSD"}
# currency -> the major whose price gives that-currency per 1 USD (so invert it)
_USD_INVERSE = {"JPY": "USDJPY", "CAD": "USDCAD", "CHF": "USDCHF"}


def _val(px, sym):
    v = px.get(sym) if hasattr(px, "get") else None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if (v == v and v > 0) else None


def usd_per(ccy: str, px) -> float | None:
    """USD per 1 unit of `ccy`, from a price lookup (symbol -> price). None if the
    needed major isn't present."""
    if ccy == "USD":
        return 1.0
    if ccy in _USD_DIRECT:
        return _val(px, _USD_DIRECT[ccy])
    if ccy in _USD_INVERSE:
        v = _val(px, _USD_INVERSE[ccy])
        return (1.0 / v) if v else None
    return None


def aud_per_quote(ccy: str, px) -> float | None:
    """AUD per 1 unit of `ccy` (AUDUSD as the hub). None if not derivable."""
    if ccy == "AUD":
        return 1.0
    u = usd_per(ccy, px)
    a = _val(px, "AUDUSD")            # USD per AUD
    if u is None or a is None:
        return None
    return u / a


def conversion_factor(quote: str, px_last, px_now) -> float:
    """AUD-translation factor for a position held last→now in a pair quoted in
    `quote`: aud_per_quote(now) / aud_per_quote(last). 1.0 if not derivable."""
    a0 = aud_per_quote(quote, px_last)
    a1 = aud_per_quote(quote, px_now)
    if not a0 or not a1:
        return 1.0
    return a1 / a0


def hub_symbols(quotes) -> list[str]:
    """The majors needed to derive aud_per_quote for these quote currencies:
    the AUDUSD hub plus each quote's USD cross. Lets a caller fetch a minimal
    historical closes frame (e.g. the dashboard blotter covering trades older
    than its bounded display panel)."""
    syms = {"AUDUSD"}
    for q in set(quotes):
        if q in _USD_DIRECT:
            syms.add(_USD_DIRECT[q])
        elif q in _USD_INVERSE:
            syms.add(_USD_INVERSE[q])
    return sorted(syms)


def aud_per_quote_frame(closes_df: pd.DataFrame, quotes) -> pd.DataFrame:
    """Vectorised AUD-per-quote series (index = closes_df.index, one column per
    distinct quote currency). NaN where a rate can't be derived from the panel."""
    a = closes_df["AUDUSD"] if "AUDUSD" in closes_df.columns else None
    out: dict[str, pd.Series] = {}
    for q in set(quotes):
        if q == "AUD":
            out[q] = pd.Series(1.0, index=closes_df.index)
            continue
        if q == "USD":
            usd = pd.Series(1.0, index=closes_df.index)
        elif q in _USD_DIRECT and _USD_DIRECT[q] in closes_df.columns:
            usd = closes_df[_USD_DIRECT[q]]
        elif q in _USD_INVERSE and _USD_INVERSE[q] in closes_df.columns:
            usd = 1.0 / closes_df[_USD_INVERSE[q]]
        else:
            usd = None
        out[q] = (usd / a) if (usd is not None and a is not None) \
            else pd.Series(float("nan"), index=closes_df.index)
    return pd.DataFrame(out)
