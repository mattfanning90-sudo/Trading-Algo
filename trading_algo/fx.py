"""FX conversion of local-currency sleeves into the base reporting currency.

A *multiplier* m for currency C means: base_amount = local_amount · m, i.e. m is
"base units per 1 unit of C". For the base currency itself m == 1. Yahoo quotes
the pair "BASE+C=X" as "C per 1 BASE", so m = 1 / that quote.

    AUD base, USD local:  Yahoo AUDUSD=X = USD per AUD ≈ 0.66  ->  m_USD = 1/0.66 ≈ 1.52
    AUD base, GBP local:  Yahoo AUDGBP=X = GBP per AUD ≈ 0.52  ->  m_GBP = 1/0.52 ≈ 1.92
"""
from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

from . import data, regions

# Synthetic anchor levels (base units per 1 local unit, AUD base) for currencies
# that have NO regional sleeve — sourced from the Region records otherwise (see
# `synth_level`). Offline/synthetic path only (invariant #5).
_EXTRA_SYNTH_LEVEL = {"EUR": 1.63, "JPY": 0.0098}


def synth_level(ccy: str) -> float:
    """Synthetic FX anchor for `ccy` (base=AUD units per 1 ccy), for offline
    tests only (invariant #5). Region currencies read their anchor straight off
    the Region record (folded in by refactor R3), so adding a region needs no
    edit here; a handful of non-sleeve currencies come from a small residual.
    Unknown -> 1.0."""
    for r in regions.REGIONS.values():
        if r.currency == ccy and r.synthetic_fx_anchor is not None:
            return r.synthetic_fx_anchor
    return _EXTRA_SYNTH_LEVEL.get(ccy, 1.0)


def fx_ticker(base: str, ccy: str) -> str:
    """Yahoo pair giving 'ccy per 1 base' (invert it to get base-per-ccy)."""
    return f"{base}{ccy}=X"


def load_fx(currencies: list[str], start: str, end: str | None = None,
            base: str = "AUD", use_cache: bool = True) -> pd.DataFrame:
    """DataFrame of multipliers (base per 1 local) indexed by date, one column
    per currency. The base currency column is a constant 1.0."""
    foreign = sorted({c for c in currencies if c != base})
    cols = {base: None}

    if foreign:
        tickers = [fx_ticker(base, c) for c in foreign]
        # Include a hash of the currency SET in the key: a different set of
        # foreign currencies must not collide with (and read back) a file cached
        # for another set, which would surface a missing pair as an all-NaN column.
        set_hash = hashlib.sha1(",".join(foreign).encode()).hexdigest()[:12]
        raw = data.load_prices(tickers, start, end,
                               cache_key=f"FX:{base}:{start}:{end}:{set_hash}",
                               use_cache=use_cache)
        for c in foreign:
            t = fx_ticker(base, c)
            cols[c] = (1.0 / raw[t]) if t in raw.columns else np.nan

    idx = next((s.index for s in cols.values() if s is not None), None)
    out = pd.DataFrame(index=idx)
    for c in currencies:
        if c == base:
            out[c] = 1.0
        else:
            out[c] = cols.get(c)
    return out.ffill()


def synthetic_fx(currencies: list[str], start: str = "2012-01-01",
                 end: str = "2026-01-01", base: str = "AUD",
                 seed: int = 7) -> pd.DataFrame:
    """Random-walk FX multipliers around plausible anchors. Offline use only."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, end)
    out = pd.DataFrame(index=dates)
    for i, c in enumerate(currencies):
        if c == base:
            out[c] = 1.0
            continue
        level = synth_level(c)
        steps = rng.normal(0.0, 0.005, len(dates))
        out[c] = level * np.exp(np.cumsum(steps))
    return out


def align_fx(fx: pd.DataFrame, index: pd.Index, currency: str) -> pd.Series:
    """Multiplier series for one currency aligned to a price index (ffill)."""
    if currency not in fx.columns:
        return pd.Series(1.0, index=index)
    return fx[currency].reindex(index).ffill().bfill()
