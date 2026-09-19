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


# ---------------------------------------------------------------------------
# Broker commission (IBKR's published schedule)
# ---------------------------------------------------------------------------
# The FX cost model is spread-only. Correct for FX — the dealing spread IS the
# cost — and wrong for equities and bonds, where IBKR bills per SHARE with a
# per-ORDER minimum. On a small book that minimum dominates everything else.
def commission(delta_w: float, pair: Pair, price: float | None,
               equity: float) -> float:
    """Broker commission for ONE order, in the account currency.

    Equity/bond legs use IBKR's per-share schedule with its per-order floor and
    1%-of-notional cap; FX uses the bps-of-notional schedule, because a share
    count is meaningless for a currency pair. A missing or non-positive price
    charges nothing rather than raising — same guard philosophy as the spread.
    """
    from . import fx_config as _cfg

    if not delta_w or not price or price != price or price <= 0 or equity <= 0:
        return 0.0
    notional = abs(delta_w) * equity
    if notional <= 0:
        return 0.0

    if pair.asset_class == "fx":
        return max(_cfg.IBKR_FX_MIN_ORDER, notional * _cfg.IBKR_FX_BPS / 1e4)

    shares = notional / price
    fee = max(_cfg.IBKR_EQUITY_MIN_ORDER, shares * _cfg.IBKR_EQUITY_PER_SHARE)
    return min(fee, notional * _cfg.IBKR_EQUITY_MAX_PCT)


def is_executable(delta_w: float, pair: Pair, equity: float) -> bool:
    """Could this order actually be placed at the venue?

    IBKR's IDEALPRO needs a USD 25k account and 20,000-unit minimum orders, so a
    small book's ~A$900 FX leg is not a tradeable order at all. Charging it a
    per-order fee models a fee on an order that cannot exist — and on a daily
    rebalance that compounds a book to zero. Below the minimum the honest
    outcome is that the trade does not happen.
    """
    from . import fx_config as _cfg

    floor = (_cfg.VENUE_MIN_ORDER_NOTIONAL or {}).get(pair.asset_class, 0.0)
    if floor <= 0:
        return True
    return abs(delta_w) * max(equity, 0.0) >= floor


def commission_fraction(delta_w: float, pair: Pair, price: float | None,
                        equity: float) -> float:
    """`commission` expressed as a fraction of equity, to sit beside
    `cost_fraction` in the book's per-bar cost term."""
    if equity <= 0:
        return 0.0
    return commission(delta_w, pair, price, equity) / equity


# ---------------------------------------------------------------------------
# Financing: margin interest on the long debit + stock-loan fee on shorts
# ---------------------------------------------------------------------------
# Every equity and bond in the multi-asset universe ships
# ``swap_long_pips = swap_short_pips = 0.0`` deliberately (see pairs.py): the FX
# carry model IS swap points, and no equity financing model was ever written.
# The consequence was that a book holding 1.02x long and 0.45x short paid
# nothing to borrow either the cash or the shares. This is that missing charge,
# defined ONCE here so the live book and the backtest cannot re-fork it.
#
# CONVENTION — stated once, because it is easy to get wrong:
#   * Margin interest accrues on the LONG DEBIT ONLY, ``max(0, L - 1)``. A short
#     GENERATES cash rather than consuming it, so charging on ``gross - 1`` would
#     double-count the short leg.
#   * Shorts instead pay a stock-loan fee on their own notional.
#   * FX and crypto are EXCLUDED: their financing already lives in swap points
#     and perp funding, so billing them here would charge the same cost twice.
#   * Interest accrues on CALENDAR days (a weekend costs three days), which is
#     why the caller passes elapsed time rather than a bar count.
FINANCED_CLASSES = ("equity", "bond")
DAYS_PER_YEAR = 365.25


def financing_fraction(weights, get_pair=None, *, margin_rate: float,
                       borrow_rate: float, elapsed_days: float = 1.0
                       ) -> tuple[float, dict[str, float]]:
    """Financing COST for one bar, as a positive fraction of equity.

    Returns ``(total, by_pair)``. ``by_pair`` always sums to ``total`` — the
    book-level margin debit is attributed pro-rata across the financed longs
    that caused it, so the dashboard's per-leg reconciliation still balances.

    Callers fold this into the carry term (financing is negative carry), which
    keeps the book identity ``equity - start == price_pnl + carry - cost``.
    """
    if get_pair is None:
        from .pairs import get_pair as _default
        get_pair = _default
    if elapsed_days <= 0:
        return 0.0, {}

    longs: dict[str, float] = {}
    shorts: dict[str, float] = {}
    for sym, w in (weights or {}).items():
        if not w:
            continue
        try:
            asset_class = get_pair(sym).asset_class
        except Exception:
            continue                      # an unknown symbol is never financed
        if asset_class not in FINANCED_CLASSES:
            continue
        (longs if w > 0 else shorts)[sym] = abs(float(w))

    years = elapsed_days / DAYS_PER_YEAR
    by_pair: dict[str, float] = {}

    for sym, w in shorts.items():         # stock-loan fee on short notional
        by_pair[sym] = by_pair.get(sym, 0.0) + w * borrow_rate * years

    long_exposure = sum(longs.values())
    debit = max(0.0, long_exposure - 1.0)
    if debit and margin_rate:
        charge = debit * margin_rate * years
        for sym, w in longs.items():      # pro-rata across the financed longs
            by_pair[sym] = by_pair.get(sym, 0.0) + charge * (w / long_exposure)

    total = sum(by_pair.values())
    if not total:
        return 0.0, {}
    return total, {k: v for k, v in by_pair.items() if v}


def total_cost_fraction(delta_w: float, pair: Pair, price: float | None,
                        equity: float) -> float:
    """Everything one order costs, as a fraction of equity: dealing spread PLUS
    broker commission.

    THE single definition of what a trade costs. The book, the per-pair
    backtest and the dashboard's blotter reconstruction all route through this
    (or through `trade_cost`, which is just this times equity), so the charge a
    book applies and the charge the blotter shows can never disagree — a
    regression `tests/test_fx_pnl.py` pins by reconstructing one from the other.
    """
    return (cost_fraction(delta_w, pair, price)
            + commission_fraction(delta_w, pair, price, equity))


def trade_cost(delta_w: float, pair: Pair, price: float | None, equity: float) -> float:
    """Spread + commission in the account currency (the currency `equity` is in)."""
    return total_cost_fraction(delta_w, pair, price, equity) * equity


# ---------------------------------------------------------------------------
# Mark-to-market (pair move x AUD/quote translation)
# ---------------------------------------------------------------------------
def position_contribution(w: float, px_entry: float, px_now: float,
                          fx_factor: float) -> float:
    """P&L contribution of a signed weight held entry -> now, as a fraction of
    equity: ``w * fx_factor * (px_now / px_entry - 1.0)``.

    ``fx_factor`` is the AUD/quote translation over the same interval
    (``fxconv.conversion_factor``; 1.0 when not derivable).

    Only the *P&L* translates at FX, not the notional — see the derivation in
    ``fxconv``. This book is a margin book (leveraged, two-sided, vol-targeted):
    a weight is a synthetic long/short, not AUD cash converted into the quote
    currency, so the position's notional is never an AUD/quote exposure you own.
    Marking it as one — ``w * (r*f - 1)``, the formula this used to carry —
    adds a spurious ``w * (f - 1)``: an unhedged AUD/quote exposure of the full
    gross notional on every pair, and, for AUDUSD itself, exact cancellation
    (quote == USD makes ``f == 1/r``), so an AUDUSD leg booked precisely 0.0 for
    ever while still paying the spread.
    """
    return w * fx_factor * (px_now / px_entry - 1.0)


def aud_return(rets, fx_ratio):
    """Vectorised twin of :func:`position_contribution` for the panel backtests:
    a per-bar quote-currency return becomes an AUD return as ``rets *
    fx_ratio``. Same model, one definition — feed per-bar returns and the
    per-bar ``aud_per_quote`` ratio (see ``fxconv.aud_per_quote_frame``)."""
    return rets * fx_ratio


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
