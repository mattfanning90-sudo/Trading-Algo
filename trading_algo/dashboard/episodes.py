"""Closed POSITIONS (opened from flat, then back to flat or flipped) and their
results after costs, from a ledger of signed holdings plus the FIFO lots that
closed them. A position, not a lot: one name trimmed five times is one
position, not five wins (adversarial review 2026-10-14: matt's 197 closed lots
are 77 positions, and the win rate differs — 44% vs 38%)."""
from __future__ import annotations

from collections import defaultdict
from typing import Callable

from .. import pnl

_DUST = 1e-9


def _sign(q: float) -> int:
    return 1 if q > _DUST else -1 if q < -_DUST else 0


def equity_events(trades: list[dict]) -> list[tuple[str, tuple[str, str], float]]:
    held: dict[tuple[str, str], float] = {}
    out = []
    for t in sorted((t for t in trades if pnl.is_fill(t)), key=lambda t: str(t["date"])):
        sym = (t["region"], t["ticker"])
        qty = float(t["shares"]) if t["side"] == "BUY" else -float(t["shares"])
        held[sym] = held.get(sym, 0.0) + qty
        out.append((str(t["date"]), sym, held[sym]))
    return out


def fx_events(trades: list[dict]) -> list[tuple[str, str, float]]:
    return [(str(t["date"]), t["pair"], float(t.get("target_weight", 0.0)))
            for t in sorted((t for t in trades if t.get("pair") and t.get("date")),
                            key=lambda t: str(t["date"]))]


def episodes(events) -> list[dict]:
    cur: dict = {}
    open_: dict = {}
    out: list[dict] = []
    for date, sym, q in events:
        prev = cur.get(sym, 0.0)
        cur[sym] = q
        sp, sq = _sign(prev), _sign(q)
        if sp != 0 and sq != sp and sym in open_:
            ep = open_.pop(sym)
            ep["closed"] = date
            out.append(ep)
        if sq != 0 and sq != sp:
            open_[sym] = {"symbol": sym, "opened": date, "closed": None,
                          "side": "LONG" if sq > 0 else "SHORT"}
    return out + list(open_.values())


def closed_positions(events, lots, *, symbol_of: Callable, net_of: Callable,
                     exclude_dates=frozenset()) -> tuple[list[dict], int]:
    """Match closed episodes to FIFO lots and compute net P&L.

    A closed position no lot matched has net None (unmeasured), never 0.
    """
    closed = [e for e in episodes(events) if e["closed"] is not None]
    by_sym: dict = defaultdict(list)
    for e in closed:
        e["net"], e["lots"] = 0.0, 0
        by_sym[e["symbol"]].append(e)
    for lot in lots:
        d = str(lot["date"])
        for e in by_sym.get(symbol_of(lot), ()):
            if e["opened"] <= d <= e["closed"]:
                v = net_of(lot)
                e["net"] = None if (v is None or e["net"] is None) else e["net"] + float(v)
                e["lots"] += 1
                break
    kept = [e for e in closed if e["closed"][:10] not in exclude_dates]
    for e in kept:
        if e["lots"] == 0:
            e["net"] = None
        elif e["net"] is not None:
            e["net"] = round(e["net"], 2)
    return kept, len(closed) - len(kept)


def forced_dates(state: dict) -> set[str]:
    """Dates a recorded correction says a bug forced trades (e.g. FULL's
    2026-08-27 phantom-breaker liquidation)."""
    return {str(r["date"])[:10] for c in state.get("corrections") or []
            for r in c.get("rows") or [] if r.get("date")}
