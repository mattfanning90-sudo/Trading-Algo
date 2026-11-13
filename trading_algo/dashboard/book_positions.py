"""One book's Positions answer: what it holds, what each holding cost and is
worth, and — per region or asset group — whether it's investing or waiting and
why. Holdings and cost come from the saved book (instant, offline); today's
prices come only from a finished background fetch, and their age is shown."""
from __future__ import annotations

import math
from datetime import timezone

from .. import pnl
from ..forex import sessions
from ..forex.pairs import get_pair
from ..regions import get_region
from . import book_rules as R
from . import glossary
from .textfmt import day, direction, money, money_move, pct

ASSET_GROUP = {"fx": "Currencies", "crypto": "Crypto", "equity": "Shares", "bond": "Bonds"}
_PRICE_TEXT = {"pending": "Fetching today's prices…", "idle": "Fetching today's prices…",
               "offline": "Valued at cost: today's prices aren't available offline.",
               "error": "Couldn't fetch today's prices; valued at cost.",
               "n/a": "Valued at cost."}


def _ok(*vals) -> bool:
    """True when every value is a real finite number (never NaN/inf/None)."""
    return all(isinstance(v, (int, float)) and math.isfinite(v) for v in vals)


def _prices(inp) -> dict:
    if inp.kind == "fx":
        d = R.as_of(inp)
        return {"status": "saved", "text": f"Valued at the book's last saved prices ({day(d)})." if d
                else "Valued at the book's last saved prices."}
    if inp.market == "ready" and inp.live:
        return {"status": "ready", "text": f"Prices as of {R._on(inp.live.get('as_of'))}."}
    return {"status": inp.market, "text": _PRICE_TEXT.get(inp.market, _PRICE_TEXT["n/a"])}


def _equity_groups(inp) -> list[dict]:
    state = inp.state
    basis = pnl.open_basis(pnl.build_lots(state.get("trades") or [])[0])
    live = ({s["key"]: s for s in inp.live.get("sleeves") or []}
            if inp.market == "ready" and inp.live else {})
    groups = []
    for key in state.get("allocations") or (state.get("sleeves") or {}):
        sleeve = (state.get("sleeves") or {}).get(key) or {}
        ccy = sleeve.get("currency") or get_region(key).currency
        code, why, level = R.sleeve_reason(inp, key, sleeve)
        marks = {p["ticker"]: p for p in (live.get(key) or {}).get("positions") or []}
        rows = []
        for ticker, shares in (sleeve.get("positions") or {}).items():
            short = shares < 0
            avg = basis.get((key, ticker))
            row = {"symbol": ticker, "side": "SHORT" if short else "LONG",
                   "qty": (f"{abs(int(shares)):,} share{'s' if abs(int(shares)) != 1 else ''}"
                       f"{' short' if short else ''}"),
                   "bought": (f"{'Sold' if short else 'Bought'} at {money(avg, ccy)}"
                              if avg else "Cost unknown"),
                   "now": None, "worth": None, "gain": None, "gain_pct": None,
                   "dir": "flat", "note": None}
            lp = marks.get(ticker)
            if lp and lp.get("priced") and _ok(lp.get("price"), lp.get("value_base"),
                                                  lp.get("unrealized_base")):
                row.update({"now": f"now {money(lp['price'], ccy)}",
                            "worth": money(lp["value_base"]),
                            "gain": money_move(lp["unrealized_base"]),
                            "gain_pct": (pct(lp["unrealized_pct"]) if _ok(lp.get("unrealized_pct"))
                                         else None),
                            "dir": direction(lp["unrealized_base"])})
            elif lp:
                row.update({"now": "no price today", "note": "No price today; held at cost."})
            rows.append(row)
        groups.append({"key": key, "name": get_region(key).name, "currency": ccy,
                       "level": level, "why": why,
                       "caveat": R.REGIME_CAVEAT if code == "regime-off" else None,
                       "cash": f"{money(float(sleeve.get('cash') or 0.0), ccy)} in cash",
                       "rows": rows})
    return sorted(groups, key=lambda g: 0 if g["level"] == "act" else 1)


def _fx_groups(inp) -> list[dict]:
    by_class: dict[str, list[dict]] = {}
    for r in (inp.fx_book or {}).get("open_rows") or []:
        short = r["weight"] < 0
        usable = _ok(r.get("unrealized"), r.get("entry_cost"), r.get("notional"))
        net = float(r["unrealized"]) - float(r["entry_cost"]) if usable else 0.0
        notional = float(r["notional"]) if usable else 0.0
        avg = r.get("avg_entry") if _ok(r.get("avg_entry")) else None
        now = r.get("price") if _ok(r.get("price")) else None
        by_class.setdefault(get_pair(r["pair"]).asset_class, []).append({
            "symbol": r["pair"], "side": "SHORT" if short else "LONG",
            "qty": f"{pct(abs(r['weight']), 0, signed=False)} of the book {'short' if short else 'long'}",
            "bought": f"{'Sold' if short else 'Bought'} at {avg:.5g}" if avg else "Cost unknown",
            "now": f"now {now:.5g}" if r.get("priced") and now else "no price today",
            "worth": money(notional) if usable else None,
            "gain": money_move(net) if usable else None,
            "gain_pct": pct(net / notional) if usable and notional else None,
            "dir": direction(net) if usable else "flat",
            "note": None if r.get("priced") and usable and now else "No price today; held at cost."})
    ts = inp.now.astimezone(timezone.utc).replace(tzinfo=None)
    symbols = [row["symbol"] for rows in by_class.values() for row in rows]
    shut = sessions.closed_symbols(symbols, ts, str(inp.state.get("bar") or "1d"))
    groups = []
    for cls in ("fx", "crypto", "equity", "bond"):
        rows = by_class.get(cls)
        if not rows:
            continue
        frozen = all(r["symbol"] in shut for r in rows)
        n = len(rows)
        groups.append({"key": cls, "name": ASSET_GROUP[cls], "currency": "AUD", "level": None,
                       "why": ("Market shut now: positions frozen until it reopens." if frozen
                               else f"{n} open position{'s' if n != 1 else ''}."),
                       "caveat": None, "cash": None, "rows": rows})
    return groups


def positions_view(inp) -> dict:
    groups = _fx_groups(inp) if inp.kind == "fx" else _equity_groups(inp)
    n = sum(len(g["rows"]) for g in groups)
    notes = (["Gains are the price move, at today's exchange rate."] if inp.kind == "equity"
             else ["Gains are after the spread paid to get in."])
    if R.long_short(inp):
        notes.append("Cash includes the proceeds of short sales, so it can be more than "
                     "the book is worth.")
    order = [(g, "why") for g in groups] + [(g, "caveat") for g in groups]
    texts = glossary.gloss([g[k] or "" for g, k in order] + notes)
    for (g, k), t in zip(order, texts):
        if g[k]:
            g[k] = t
    notes = texts[len(order):]
    return {"v": 1, "key": inp.entry["key"], "name": inp.card["name"], "kind": inp.kind,
            "count": f"{n} holding{'s' if n != 1 else ''}", "prices": _prices(inp),
            "notes": notes, "groups": groups, "market": inp.market}
