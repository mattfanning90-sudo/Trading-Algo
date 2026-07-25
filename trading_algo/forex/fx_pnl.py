"""Money-terms P&L accounting for a weight-based FX book.

This is the FX counterpart of :mod:`trading_algo.pnl` (the equity sleeves' FIFO
lot ledger). It answers, in AUD rather than in fractions of equity: *what did
each position actually make or lose, what did the spread cost, and how do those
add up to the book's equity?*

Why a separate module
---------------------
An equity sleeve holds a whole number of shares bought at a stamped fill price,
so a broker-style FIFO ledger falls out of the fills log directly. An FX book
holds a **signed weight** — a fraction of equity — and the book is marked
multiplicatively each bar (``equity *= 1 + pnl + carry`` then ``*= 1 - cost``).
Weights are not quantities, so "P&L in real terms" needs an explicit convention.

THE CONVENTION (one place, documented once)
------------------------------------------
A *lot* is a slice of weight opened by one trade, and its AUD notional is fixed
at open: ``|Δweight| × equity_on(trade date)``. That is the same convention the
static FX dashboard's blotter already uses for per-trade notional and P&L-since
(``forex.dashboard._transactions``), so the two agree. Every formula — the
half-spread charge and the mark-to-market — is imported from :mod:`forex.marks`,
the ONE formula module ``fx_book.run_once`` also routes through, so the ledger
can never drift from the book that produced it.

Because the real book compounds (a weight is a fraction of *today's* equity, so
a held position's notional drifts as equity moves), notional-at-open is an
approximation for multi-bar holds. It is an honest, stable one — and the exact
figures are available alongside it: ``fx_book.run_once`` accumulates the true
per-bar price P&L, carry and spread cost in AUD into ``state["cumulative"]``,
which :func:`book_pnl` surfaces as the ``exact`` block and reconciles against.
Neither number is presented as the other.

AUD translation
---------------
A pair's price moves in its quote currency, so an AUD book's real P&L includes
the AUD/quote move (see :mod:`forex.fxconv`). Each trade carries the
execution-time ``aud_per_quote`` stamped by ``fx_book.run_once``; a lot's
translation factor is ``aud_per_quote(exit) / aud_per_quote(entry)``. Trades
predating that stamp translate at 1.0 (pair move only) and are counted in
``fx_unstamped`` so the UI can say so rather than quietly overstating accuracy.

Carry is deliberately absent from the lot ledger: financing accrues per bar on
whatever was held, not per round-trip, and cannot be reconstructed from the
trades log. It comes from the accumulator instead.
"""
from __future__ import annotations

from datetime import date as _date

from . import fxconv, marks
from .pairs import get_pair

# Weights are persisted rounded (Δweight to 4dp, positions to 5dp), so FIFO
# matching needs a dust threshold rather than exact-zero comparisons.
DUST = 1e-9

# A lot is [w_signed, entry_price, entry_apq, equity_at_open, cost_per_unit_w, date]
_W, _PX, _APQ, _EQ, _CPU, _DT = range(6)


# ---------------------------------------------------------------------------
# equity-at-date lookup (THE one implementation)
# ---------------------------------------------------------------------------
def equity_lookup(state: dict):
    """``(equity_on, eq_map)``: the ONE string-asof equity lookup for trade dates.

    Intraday keys ('YYYY-MM-DD HH:MM') rely on lexicographic ``d <= date``,
    which is order-preserving for ISO-like keys. A single implementation keeps
    every card that prices a trade in agreement — ``forex.dashboard`` imports
    this rather than keeping its own copy.
    """
    eqh = state.get("equity_history") or []
    eq_map = {d: e for d, e in eqh}
    eq_dates = [d for d, _ in eqh]
    cur_eq = float(state.get("equity", state.get("initial_capital", 0.0)) or 0.0)

    def equity_on(date: str) -> float:
        if date in eq_map:
            return float(eq_map[date])
        prior = [d for d in eq_dates if d <= date]
        return float(eq_map[prior[-1]]) if prior else cur_eq

    return equity_on, eq_map


def _apq(value) -> float | None:
    """Sanitise a stored ``aud_per_quote`` stamp: a positive finite float or None."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if (v == v and v > 0.0) else None


def _factor(entry_apq: float | None, exit_apq: float | None) -> tuple[float, bool]:
    """(AUD/quote translation over the hold, whether it was derivable)."""
    if entry_apq and exit_apq:
        return exit_apq / entry_apq, True
    return 1.0, False


def held_days(entry_date: str | None, exit_date: str | None) -> int:
    """Calendar days between two book bar keys (date or 'date HH:MM'). 0 if
    either is missing or unparseable — a display field, never a P&L input."""
    try:
        return (_date.fromisoformat(str(exit_date)[:10])
                - _date.fromisoformat(str(entry_date)[:10])).days
    except (ValueError, TypeError):
        return 0


# ---------------------------------------------------------------------------
# FIFO matching over signed weight
# ---------------------------------------------------------------------------
def _open(queue: list, dw: float, price: float, apq: float | None,
          equity: float, cost: float, when: str | None) -> None:
    """Append a signed weight lot (dw>0 long, dw<0 short) to the queue."""
    w = abs(dw)
    if w <= DUST:
        return
    queue.append([dw, float(price), apq, float(equity), cost / w, when])


def _close(queue: list, w: float, price: float, apq: float | None,
           exit_cost: float) -> dict | None:
    """Consume `w` of weight from a same-signed lot queue oldest-first (mutates
    it). Works for long and short lots alike: :func:`marks.trade_mark` carries
    the sign, so a short lot gains when the exit price is below entry.

    Returns the realised round-trip in AUD, or None if nothing matched.
    """
    if not queue:
        return None
    lot_dir = 1 if queue[0][_W] > 0 else -1
    remaining, matched = w, []
    gross = notional = entry_cost = px_w = 0.0
    fx_known = True
    while remaining > DUST and queue:
        lot = queue[0]
        take = min(remaining, abs(lot[_W]))
        fxf, known = _factor(lot[_APQ], apq)
        fx_known = fx_known and known
        gross += marks.trade_mark(lot_dir * take, lot[_PX], float(price),
                                  fxf, lot[_EQ])
        notional += take * lot[_EQ]
        entry_cost += take * lot[_CPU]
        px_w += take * lot[_PX]
        matched.append((take, lot[_DT]))
        lot[_W] -= lot_dir * take
        remaining -= take
        if abs(lot[_W]) <= DUST:
            queue.pop(0)
    filled = w - remaining
    if filled <= DUST:
        return None
    return {
        "filled": round(filled, 6),           # weight closed
        "side": "LONG" if lot_dir > 0 else "SHORT",   # side of the lots closed
        "entry": px_w / filled,
        "entry_notional": notional,           # AUD at risk when opened
        "exit": float(price),
        "gross": gross,                       # price + FX move, in AUD
        "entry_cost": entry_cost,
        "exit_cost": float(exit_cost),
        "net": gross - entry_cost - float(exit_cost),
        "entry_date": matched[0][1],
        "fx_known": fx_known,
        "left_over": round(sum(abs(lot[_W]) for lot in queue), 6),
    }


def apply_delta(lots: dict, pair: str, dw: float, price: float,
                apq: float | None, equity: float, cost: float,
                when: str | None) -> dict | None:
    """Apply one weight change with FIFO accounting (mutates `lots`).

    A change in the same direction as the open position OPENS/adds a lot; the
    opposite direction CLOSES lots oldest-first and may flip through zero,
    opening a fresh lot with the overshoot. The trade's spread cost follows the
    weight: split pro-rata on a flip, carried on the lot while it stays open.
    Returns the realised round-trip for the closed portion, else None.
    """
    if abs(dw) <= DUST:
        return None
    queue = lots.setdefault(pair, [])
    if not queue or (queue[0][_W] > 0) == (dw > 0):
        _open(queue, dw, price, apq, equity, cost, when)
        return None

    w = abs(dw)
    open_abs = sum(abs(lot[_W]) for lot in queue)
    close_w = min(w, open_abs)
    exit_cost = cost * close_w / w                    # split the spread on a flip
    r = _close(queue, close_w, price, apq, exit_cost)
    remainder = w - close_w
    if remainder > DUST:                              # flipped: open the overshoot
        _open(queue, (1 if dw > 0 else -1) * remainder, price, apq, equity,
              cost - exit_cost, when)
    if not queue:
        lots.pop(pair, None)
    return r


def trade_cost(t: dict, equity: float) -> float:
    """The spread the book charged for one trade, in AUD. Routes through
    :func:`marks.trade_cost` — the same charge ``fx_book.run_once`` applied."""
    try:
        pair = get_pair(t["pair"])
    except KeyError:
        return 0.0
    return marks.trade_cost(float(t.get("delta_weight") or 0.0), pair,
                            t.get("price"), equity)


def build_lots(trades: list[dict], equity_on) -> tuple[dict, list[dict]]:
    """Replay the whole FX trades log FIFO.

    Returns ``(open_lots, realized)`` where `open_lots` maps ``pair -> [lot,…]``
    for exposure still held, and `realized` is the closed round-trips (one per
    weight reduction that matched), each annotated with pair/quote/date and the
    AUD figures. Trades with no price, or on a symbol the pair registry no
    longer knows, are skipped — the same guard the static blotter applies.
    """
    lots: dict[str, list] = {}
    realized: list[dict] = []
    for t in trades:
        sym, price = t.get("pair"), t.get("price")
        if not sym or not price:
            continue
        try:
            pair = get_pair(sym)
        except KeyError:
            continue
        dw = float(t.get("delta_weight") or 0.0)
        when = t.get("date")
        eq = equity_on(when or "")
        cost = marks.trade_cost(dw, pair, price, eq)
        r = apply_delta(lots, sym, dw, float(price), _apq(t.get("aud_per_quote")),
                        eq, cost, when)
        if r is None:
            continue
        r.update({"date": when, "pair": sym, "quote": pair.quote,
                  "held_days": held_days(r["entry_date"], when)})
        realized.append(r)
    return {k: q for k, q in lots.items()
            if sum(abs(lot[_W]) for lot in q) > DUST}, realized


# ---------------------------------------------------------------------------
# Marking what is still open
# ---------------------------------------------------------------------------
def mark_open(open_lots: dict, last_close: dict) -> list[dict]:
    """Mark every open weight lot to the book's last stored closes.

    Fully offline: prices come from ``state["last_close"]`` (what the book
    itself last marked at) and the current AUD/quote from the same snapshot via
    :func:`fxconv.aud_per_quote`, so no network and no re-pricing is involved.
    """
    out: list[dict] = []
    for sym, queue in open_lots.items():
        try:
            pair = get_pair(sym)
        except KeyError:
            continue
        price_now = last_close.get(sym)
        try:
            price_now = float(price_now)
        except (TypeError, ValueError):
            price_now = None
        if price_now is not None and price_now != price_now:
            price_now = None
        apq_now = _apq(fxconv.aud_per_quote(pair.quote, last_close))

        weight = sum(lot[_W] for lot in queue)
        abs_w = sum(abs(lot[_W]) for lot in queue)
        if abs_w <= DUST:
            continue
        notional = sum(abs(lot[_W]) * lot[_EQ] for lot in queue)
        avg_entry = sum(abs(lot[_W]) * lot[_PX] for lot in queue) / abs_w
        entry_cost = sum(abs(lot[_W]) * lot[_CPU] for lot in queue)
        unrl = 0.0
        fx_known = True
        for lot in queue:
            fxf, known = _factor(lot[_APQ], apq_now)
            fx_known = fx_known and known
            if price_now:
                unrl += marks.trade_mark(lot[_W], lot[_PX], price_now, fxf, lot[_EQ])
        out.append({
            "pair": sym, "quote": pair.quote,
            "weight": round(weight, 5),
            "abs_weight": round(abs_w, 5),
            "avg_entry": avg_entry,
            "price": price_now,
            "notional": notional,             # AUD at risk, valued at each lot's open
            "entry_cost": entry_cost,         # spread already paid to get in
            "unrealized": unrl,               # gross of the entry spread above
            "unrealized_pct": (unrl / notional) if notional else 0.0,
            "entry_date": queue[0][_DT],
            "n_lots": len(queue),
            "fx_known": fx_known,
            "priced": price_now is not None,
        })
    out.sort(key=lambda r: -abs(r["notional"]))
    return out


# ---------------------------------------------------------------------------
# The blotter: every weight change as a money-terms fill
# ---------------------------------------------------------------------------
def blotter(state: dict, include_why: bool = False) -> list[dict]:
    """Every trade in the book as a money-terms fill, oldest first.

    Gives a weight change the three things a statement line needs: the AUD
    notional it moved (``|Δw| × equity that bar``), the spread actually charged
    (``marks.trade_cost`` — the book's own charge), and the dealing bid/ask the
    half-spread implies around the marked price. Kept here so the terminal
    dashboard and any other consumer share one derivation.

    `include_why` re-attaches each trade's stored rationale. Off by default: the
    text runs to a paragraph a trade and the decision book already carries it,
    so a 600-fill intraday blotter stays a small payload.
    """
    equity_on, _ = equity_lookup(state)
    rows: list[dict] = []
    for t in state.get("trades") or []:
        sym, price = t.get("pair"), t.get("price")
        if not sym or not price:
            continue
        try:
            pair = get_pair(sym)
        except KeyError:
            continue
        price = float(price)
        dw = float(t.get("delta_weight") or 0.0)
        eq = equity_on(t.get("date") or "")
        half = marks.half_spread_fraction(pair, price) * price
        rows.append({
            "date": t.get("date"), "pair": sym, "side": t.get("side"),
            "delta_weight": round(dw, 5),
            "target_weight": t.get("target_weight"),
            "price": price,
            "bid": round(price - half, 6), "ask": round(price + half, 6),
            "spread_bps": round(2.0 * half / price * 1e4, 2) if price else 0.0,
            "notional": round(abs(dw) * eq, 2),
            "cost": round(marks.trade_cost(dw, pair, price, eq), 4),
            "equity": round(eq, 2),
            "fx_stamped": _apq(t.get("aud_per_quote")) is not None,
            "regime": t.get("regime"),
            **({"why": t.get("why")} if include_why else {}),
        })
    return rows


# ---------------------------------------------------------------------------
# Currency exposure (the risk hidden inside the pairs)
# ---------------------------------------------------------------------------
def currency_exposure(positions: dict, equity: float) -> list[dict]:
    """Net exposure per CURRENCY from the open pairs — long EURUSD is +EUR and
    −USD — in weight and in AUD notional. An FX book's real concentration."""
    exp: dict[str, float] = {}
    for sym, w in (positions or {}).items():
        try:
            pair = get_pair(sym)
        except KeyError:
            continue
        w = float(w)
        exp[pair.base] = exp.get(pair.base, 0.0) + w
        exp[pair.quote] = exp.get(pair.quote, 0.0) - w
    return [{"currency": c, "weight": round(v, 4), "notional": round(v * equity, 2)}
            for c, v in sorted(exp.items(), key=lambda kv: -abs(kv[1]))
            if abs(v) > 1e-4]


# ---------------------------------------------------------------------------
# The whole book, in money
# ---------------------------------------------------------------------------
def book_pnl(state: dict) -> dict:
    """Everything needed to read one FX book's P&L in AUD.

    Cost accounting is exhaustive by construction: every trade's spread lands
    either in a round-trip's ``entry_cost``/``exit_cost`` or on a still-open
    lot, so ``costs == Σ(round-trip costs) + open entry costs`` — pinned by
    ``tests/test_fx_pnl.py``.

    The decomposition presented is::

        net_pnl (equity − initial) = realized + open_net + carry + residual

    where `realized` is net of entry and exit spread, `open_net` is the open
    mark less the spread already paid to get in, `carry` is the exact
    accumulated financing (None for a book that predates the accumulator), and
    `residual` is what the weight-lot convention cannot explain — compounding
    drift plus any pre-stamp FX translation. It is reported, never hidden.
    """
    equity_on, _ = equity_lookup(state)
    trades = state.get("trades") or []
    open_lots, realized = build_lots(trades, equity_on)
    positions = {k: float(v) for k, v in (state.get("positions") or {}).items()}
    open_rows = mark_open(open_lots, state.get("last_close") or {})

    equity = float(state.get("equity") or 0.0)
    initial = float(state.get("initial_capital") or 0.0)

    realized_net = sum(r["net"] for r in realized)
    realized_gross = sum(r["gross"] for r in realized)
    closed_costs = sum(r["entry_cost"] + r["exit_cost"] for r in realized)
    open_gross = sum(r["unrealized"] for r in open_rows)
    open_entry_cost = sum(r["entry_cost"] for r in open_rows)
    open_net = open_gross - open_entry_cost
    costs = closed_costs + open_entry_cost

    cum = state.get("cumulative") or {}
    carry = cum.get("carry")
    carry = float(carry) if isinstance(carry, (int, float)) else None
    exact_from = cum.get("from")

    net_pnl = equity - initial
    residual = net_pnl - realized_net - open_net - (carry or 0.0)

    # per-pair rollup: realised + open + what the spread cost to trade it
    by_pair: dict[str, dict] = {}
    for r in realized:
        b = by_pair.setdefault(r["pair"], _empty_pair())
        b["realized"] += r["net"]
        b["costs"] += r["entry_cost"] + r["exit_cost"]
        b["round_trips"] += 1
    for r in open_rows:
        b = by_pair.setdefault(r["pair"], _empty_pair())
        b["open"] += r["unrealized"] - r["entry_cost"]
        b["costs"] += r["entry_cost"]
        b["weight"] = r["weight"]
        b["notional"] = r["notional"]
    for sym, b in by_pair.items():
        b["trades"] = sum(1 for t in trades if t.get("pair") == sym)
        cp = (cum.get("by_pair") or {}).get(sym) or {}
        b["carry"] = round(float(cp["carry"]), 2) if "carry" in cp else None
        b["total"] = b["realized"] + b["open"] + (b["carry"] or 0.0)
    rollup = [{"pair": k, **{kk: (round(vv, 2) if isinstance(vv, float) else vv)
                            for kk, vv in v.items()}}
              for k, v in sorted(by_pair.items(), key=lambda kv: -abs(kv[1]["total"]))]

    turnover = sum(abs(float(t.get("delta_weight") or 0.0)) for t in trades)
    gross_w = sum(abs(w) for w in positions.values())
    net_w = sum(positions.values())
    unstamped = sum(1 for t in trades if _apq(t.get("aud_per_quote")) is None
                    and t.get("price"))

    return {
        "currency": state.get("currency", "AUD"),
        "equity": round(equity, 2),
        "initial": round(initial, 2),
        "net_pnl": round(net_pnl, 2),
        "realized": round(realized_net, 2),
        "realized_gross": round(realized_gross, 2),
        "open": round(open_net, 2),
        "open_gross": round(open_gross, 2),
        "costs": round(costs, 2),
        "carry": round(carry, 2) if carry is not None else None,
        "residual": round(residual, 2),
        "gross_notional": round(gross_w * equity, 2),
        "net_notional": round(net_w * equity, 2),
        "open_notional": round(sum(r["notional"] for r in open_rows), 2),
        "turnover": round(turnover, 2),
        "turnover_notional": round(sum(abs(float(t.get("delta_weight") or 0.0))
                                       * equity_on(t.get("date") or "")
                                       for t in trades), 2),
        "round_trips": len(realized),
        "wins": sum(1 for r in realized if r["net"] > 0),
        "n_trades": len(trades),
        "fx_unstamped": unstamped,
        "exact_from": exact_from,
        "exposure": currency_exposure(positions, equity),
        "by_pair": rollup,
        "open_rows": open_rows,
        "closed": realized,
    }


def _empty_pair() -> dict:
    return {"realized": 0.0, "open": 0.0, "costs": 0.0, "round_trips": 0,
            "trades": 0, "weight": 0.0, "notional": 0.0}
