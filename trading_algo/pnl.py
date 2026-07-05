"""FIFO position accounting derived from the fills ledger.

The trade log — every buy and sell, each stamped with the *actual* fill price
the simulator executed at — is the single source of truth for paper P&L. This is
exactly how a broker reconstructs your statement: from the trade confirmations,
not from a separately-kept running tally that can drift. Everything here replays
those real fills with FIFO lot matching and returns:

  * the open lots still held  -> cost basis for unrealised marks
  * the closed round-trips     -> realised P&L (matches what a broker reports)

There is deliberately no parallel stored `cost_basis` / `realized_pnl`; if you
want either number you derive it from the fills, so it can never disagree with
the actual trade record.

A "lot" is ``[qty, price, cost_per_share, date]`` — `cost_per_share` is the
commission/stamp allocated to that lot so realised P&L is net of entry costs.
"""
from __future__ import annotations


def add_lot(lots: dict, key: tuple, qty: int, price: float,
            cost: float, when: str | None) -> None:
    """Append a BUY lot to the FIFO queue for `key` (mutates `lots`)."""
    if qty <= 0:
        return
    lots.setdefault(key, []).append([qty, float(price), cost / qty, when])


def consume(lots: dict, key: tuple, qty: int, price: float,
            exit_cost: float) -> dict | None:
    """Sell `qty` LONG shares of `key`, consuming lots oldest-first.

    Long-only helper kept for direct callers; the general (short-aware) entry
    point is `apply_fill`. Returns the realised round-trip, or None if there
    were no long lots to match against.
    """
    queue = lots.get(key, [])
    if not queue or queue[0][0] < 0:
        return None
    return _close(queue, qty, price, exit_cost)


def _close(queue: list, qty: int, price: float, exit_cost: float) -> dict | None:
    """Consume `qty` shares from a same-signed lot queue oldest-first (mutates
    it). Works for both long lots (positive qty) and short lots (negative qty):
    a long lot gains when the exit price is above entry, a short lot when it is
    below. Returns the realised round-trip, or None if nothing matched."""
    if not queue:
        return None
    lot_dir = 1 if queue[0][0] > 0 else -1   # int → keep integer share counts exact
    remaining, matched, entry_cost = qty, [], 0.0
    while remaining > 0 and queue:
        lot = queue[0]
        take = min(remaining, abs(lot[0]))
        matched.append((take, lot[1], lot[3]))
        entry_cost += take * lot[2]
        lot[0] -= lot_dir * take          # shrink the lot toward zero
        remaining -= take
        if lot[0] == 0:
            queue.pop(0)
    filled = qty - remaining
    if filled <= 0:
        return None
    entry_notional = sum(m * px for m, px, _ in matched)
    # Long: gross = exit − entry. Short: gross = entry − exit (sell high, buy back).
    gross = lot_dir * (filled * float(price) - entry_notional)
    return {
        "filled": filled,
        "entry": entry_notional / filled,
        "entry_notional": entry_notional,
        "exit": float(price),
        "gross": gross,
        "entry_cost": entry_cost,
        "exit_cost": float(exit_cost),
        "net": gross - entry_cost - float(exit_cost),
        "entry_date": matched[0][2],
        "left_over": sum(abs(lot[0]) for lot in queue),
    }


def _open(queue: list, delta: int, price: float, cost: float,
          when: str | None) -> None:
    """Append a signed lot (delta>0 long, delta<0 short) to the queue."""
    qty = abs(delta)
    if qty == 0:
        return
    sign = 1 if delta > 0 else -1
    queue.append([sign * qty, float(price), cost / qty, when])


def apply_fill(lots: dict, key: tuple, delta: int, price: float,
               cost: float, when: str | None) -> dict | None:
    """Apply one signed fill (delta>0 buy, delta<0 sell) with FIFO accounting,
    handling long AND short positions (mutates `lots`).

    A fill in the same direction as the open position OPENS/adds a lot; a fill in
    the opposite direction CLOSES lots oldest-first (and may flip through zero,
    opening a fresh lot with the overshoot). Returns the realised round-trip for
    the closed portion, or None when the fill only opened exposure.
    """
    if delta == 0:
        return None
    queue = lots.setdefault(key, [])
    same_dir = (not queue) or ((queue[0][0] > 0) == (delta > 0))
    if same_dir:
        _open(queue, delta, price, cost, when)
        return None

    qty = abs(delta)
    open_abs = sum(abs(lot[0]) for lot in queue)
    close_qty = min(qty, open_abs)
    exit_cost = cost * close_qty / qty                # split the fee on a flip
    r = _close(queue, close_qty, price, exit_cost)
    remainder = qty - close_qty
    if remainder > 0:                                 # flipped: open the overshoot
        new_delta = (1 if delta > 0 else -1) * remainder
        _open(queue, new_delta, price, cost - exit_cost, when)
    if not queue:
        lots.pop(key, None)
    return r


def _trade_cost(t: dict) -> float:
    return float(t.get("commission", 0.0)) + float(t.get("stamp_duty", 0.0))


def build_lots(trades: list[dict]) -> tuple[dict, list[dict]]:
    """Replay the whole fills log FIFO.

    Returns ``(open_lots, realized)`` where `open_lots` maps
    ``(region, ticker) -> [lot, ...]`` for names still held, and `realized` is
    the list of closed round-trips (one per sell that matched shares), each
    annotated with the trade's date/region/ticker/currency.
    """
    lots: dict[tuple, list] = {}
    realized: list[dict] = []
    for t in trades:
        key = (t["region"], t["ticker"])
        qty = int(t["shares"])
        delta = qty if t["side"] == "BUY" else -qty
        r = apply_fill(lots, key, delta, float(t["fill"]), _trade_cost(t), t.get("date"))
        if r is None:
            continue
        r.update({"date": t.get("date"), "region": t["region"],
                  "ticker": t["ticker"], "currency": t.get("currency")})
        realized.append(r)
    open_lots = {k: q for k, q in lots.items() if sum(abs(lot[0]) for lot in q) > 0}
    return open_lots, realized


def open_basis(open_lots: dict) -> dict[tuple, float]:
    """Average cost of the remaining held lots, per ``(region, ticker)`` — the
    correct unrealised-P&L basis (the actual price paid for the shares you still
    hold, under FIFO)."""
    out: dict[tuple, float] = {}
    for key, queue in open_lots.items():
        total = sum(abs(lot[0]) for lot in queue)   # abs → also covers short lots
        if total > 0:
            out[key] = sum(abs(lot[0]) * lot[1] for lot in queue) / total
    return out
