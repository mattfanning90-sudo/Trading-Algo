"""THE shared marking/cost formula module for the FX book and dashboard.

Round-2 item 2: the half-spread cost charge and the AUD mark-to-market of a
position were duplicated between ``fx_book.run_once`` (the book's canonical
charge) and ``dashboard._transactions`` (the blotter's reconstruction). Both
now import the formulas from HERE — one importable site, pinned by source
inspection in ``tests/test_fx_marks.py`` (book side) and the dashboard's own
pins, so the two can never diverge again.

All money amounts are in the account currency (AUD for the standard paper
books — the caller passes ``equity`` in that currency and gets the same
currency back). Fractions are fractions of equity.

Annualisation convention — THE DECISION (round-2 item 5)
--------------------------------------------------------
**Calendar-time annualisation is the project-wide convention** for turning
per-bar return moments into annual vol/Sharpe figures, everywhere the FX books
and the dashboard report them:

* bars spaced >= 12h apart annualise at ``fx_config.ANNUALIZATION`` (252
  trading days/yr — daily books are unchanged);
* faster bars annualise at ``365.25 * 86400 / bar_seconds`` calendar periods
  per year, **capped at hourly** (``24 * 365.25 = 8766``), so minute books do
  not pretend to 525,960 independent observations a year.

This is calendar time, NOT FX trading time (~6048 traded hours/yr), so hourly
vol/Sharpe are consistently, modestly overstated — a known calibration choice,
kept deliberately for simplicity and internal consistency. ``periods_per_year``
below is the ONE implementation (moved verbatim from the dashboard's ``_ppy``);
book-side prints (``fx_book.status``) and the dashboard both route through it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .fx_config import ANNUALIZATION
from .pairs import Pair


# ---------------------------------------------------------------------------
# Cost model (half the dealing spread on every weight change)
# ---------------------------------------------------------------------------
def half_spread_fraction(pair: Pair, price):
    """Half the round-trip dealing spread as a fraction of price — THE single
    definition every cost path derives from (book, per-pair backtest, and the
    panel-wide ML/research backtests), so they can't re-fork.

    A scalar price delegates to ``Pair.spread_fraction`` (guards None/0/NaN/neg
    -> 0.0). A pandas Series/ndarray price is handled vectorised as
    ``0.5 * spread_pips * pip / price`` for the panel backtests; feed clean
    positive closes (NaNs propagate, matching those callers' own dropna).
    """
    if isinstance(price, (pd.Series, pd.DataFrame, np.ndarray)):
        return 0.5 * (pair.spread_pips * pair.pip) / price
    return 0.5 * pair.spread_fraction(price)


def cost_fraction(delta_w: float, pair: Pair, price: float | None) -> float:
    """Half-spread charge on a weight change, as a fraction of equity.

    The canonical book charge: ``abs(delta_w) * half_spread_fraction(pair, price)``.
    The price guard lives in ``half_spread_fraction`` -> ``Pair.spread_fraction``,
    so a missing price charges nothing rather than blowing up.
    """
    return abs(delta_w) * half_spread_fraction(pair, price)


def trade_cost(delta_w: float, pair: Pair, price: float | None, equity: float) -> float:
    """Half-spread charge in the account currency (the currency `equity` is in)."""
    return cost_fraction(delta_w, pair, price) * equity


# ---------------------------------------------------------------------------
# Mark-to-market (pair move x AUD/quote translation)
# ---------------------------------------------------------------------------
def position_contribution(w: float, px_entry: float, px_now: float,
                          fx_factor: float) -> float:
    """P&L contribution of a signed weight held entry -> now, as a fraction of
    equity: ``w * ((px_now / px_entry) * fx_factor - 1.0)``.

    ``fx_factor`` is the AUD/quote translation over the same interval
    (``fxconv.conversion_factor``; 1.0 when not derivable).
    """
    return w * ((px_now / px_entry) * fx_factor - 1.0)


def trade_mark(delta_w: float, px_entry: float, px_now: float, fx_factor: float,
               equity: float) -> float:
    """Mark-to-market of a weight change since entry, in the account currency."""
    return position_contribution(delta_w, px_entry, px_now, fx_factor) * equity


# ---------------------------------------------------------------------------
# Annualisation (see module docstring: calendar-time IS the convention)
# ---------------------------------------------------------------------------
def periods_per_year(idx: pd.DatetimeIndex) -> float:
    """Periods-per-year implied by a DatetimeIndex's median bar spacing.

    Calendar-time convention (the project decision — see module docstring):
    >= 12h spacing -> ``ANNUALIZATION`` (252); faster -> ``365.25*86400/secs``
    capped at hourly (``24*365.25``). Empty/degenerate indexes fall back to
    daily spacing (-> 252).
    """
    med = idx.to_series().diff().median() if len(idx) else pd.NaT
    secs = med.total_seconds() if pd.notna(med) and med.total_seconds() > 0 else 86400.0
    return min(ANNUALIZATION if secs >= 43200 else 365.25 * 86400.0 / secs, 24 * 365.25)
