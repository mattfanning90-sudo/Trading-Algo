"""FX agent-book snapshot for the terminal dashboard.

Everything comes from the persisted fx_state_{account}.json — decisions carry
the agent votes, indicators and "why" text; `daily` carries the attribution.
Pure read, fully offline; never touches the network.

Two layers of the same book
---------------------------
* the **decision** layer (`rows`, `attribution`, `tape`, `news`,
  `data_quality`) — what the agent ensemble thinks, in signed weights and
  tilts, on which indicator readings, over which symbols it was allowed to see;
* the **money** layer (`pnl`, `positions_money`, `blotter`, `closed`,
  `drawdown`, `exposure`, `cumulative`) — the same book in AUD, so the FX
  screens report profit and loss the way the equity screens do.

The money layer is entirely derived by `forex.fx_pnl` from the persisted state:
open weight lots marked to the book's own last closes, FIFO round-trips, spread
charged, and the exact per-bar price/carry/spread accumulator the book keeps.
No re-pricing, no network. See `forex/fx_pnl.py` for the conventions.
"""
from __future__ import annotations

import math
import os
from datetime import date, datetime, timedelta, timezone

from ..forex import fx_book
from ..forex import fx_config as fxcfg
from ..forex import fx_pnl
from ..forex import news as fxnews
from . import registry

NEWS_WINDOW_DAYS = 45     # how far back the economic-calendar feed reaches

# An hourly book fills ~24×/day, so its blotter and round-trip ledger grow
# without bound. Ship the most recent slice and say so (`*_count` vs the rows
# sent) — the footer TOTALS always come from the full ledger, never from the
# rows displayed, so a bounded table can't understate the P&L.
LEDGER_MAX = 400

# Vote order the terminal shows: T·B·M·R·C·N.
AGENT_ORDER = ["trend", "breakout", "momentum", "meanrev", "carry", "neural"]


def _f(v, default=0.0):
    """Persisted floats can be NaN (e.g. ann_vol on a short history); NaN in
    json.dumps produces invalid JSON that kills the whole page — sanitise."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def _profile_params(state: dict) -> fxcfg.FXParams:
    try:
        return fxcfg.profile(state.get("profile", "balanced"))
    except KeyError:
        return fxcfg.FXParams()


def _rows(state: dict, book: dict | None = None) -> list[dict]:
    """The decision book, one row per instrument — now carrying that
    instrument's money position too (notional, cost basis, open and realised
    P&L in the account currency), so a row reads as both a view and a holding.

    Every OPEN position appears even when no decision was recorded for it (a
    held leg must never be invisible); decision-only rows (flat views) stay.
    """
    decisions = state.get("decisions") or {}
    positions = state.get("positions") or {}
    last_close = state.get("last_close") or {}
    open_by_pair = {r["pair"]: r for r in (book or {}).get("open_rows", [])}
    money_by_pair = {r["pair"]: r for r in (book or {}).get("by_pair", [])}
    rows = []
    for pair in list(decisions) + [p for p in positions if p not in decisions]:
        d = decisions.get(pair) or {}
        ind = d.get("indicators") or {}
        agents = d.get("agents") or {}
        weight = _f(positions.get(pair, d.get("weight", 0.0)))
        price = ind.get("price", last_close.get(pair))
        vol = ind.get("ann_vol")
        mk = open_by_pair.get(pair) or {}
        mn = money_by_pair.get(pair) or {}
        rows.append({
            "pair": pair,
            "weight": round(weight, 5),
            "tilt": round(_f(d.get("tilt")), 4),
            "regime": str(d.get("regime", "")).upper(),
            "price": _f(price, None),
            "ann_vol": _f(vol, None),
            # The agents' OWN indicator readings for this bar (ema_fast/slow,
            # adx, rsi, roc, bb_z, donchian_hi/lo, price, ann_vol) — so the
            # terminal shows what the vote was actually cast on instead of
            # re-deriving oscillators from bars it made up. {} when a decision
            # carried none; a field is null when it was NaN on this history.
            "indicators": {k: _f(v, None) for k, v in ind.items()},
            "agents": [round(_f(agents.get(a)), 3) for a in AGENT_ORDER],
            "why": d.get("text", ""),
            # --- money terms (account currency) ---
            "notional": round(_f(mk.get("notional")), 2),
            "avg_entry": _f(mk.get("avg_entry"), None),
            "unrealized": round(_f(mk.get("unrealized")), 2) if mk else 0.0,
            "unrealized_pct": round(_f(mk.get("unrealized_pct")), 4) if mk else 0.0,
            "realized": round(_f(mn.get("realized")), 2),
            "costs": round(_f(mn.get("costs")), 2),
            "total_pnl": round(_f(mn.get("total")), 2),
            "held_since": mk.get("entry_date") or "",
            "fx_known": bool(mk.get("fx_known", True)),
        })
    rows.sort(key=lambda r: -abs(r["weight"]))
    return rows


def _day_moves(state: dict) -> dict[str, float]:
    """Per-instrument price move on the latest bar, from the daily attribution.

    Only legs the book actually HELD into that bar appear — a position opened on
    the bar itself has no move to report, and the UI shows '—' rather than a
    misleading 0.00%."""
    return {b["pair"]: _f(b.get("move"))
            for b in (state.get("daily") or {}).get("by_pair", [])
            if b.get("pair")}


def _positions_money(book: dict, state: dict) -> list[dict]:
    """Open positions as a money book: notional at risk, cost basis, the day's
    move and open P&L in the account currency — the FX analogue of the equity
    OPEN BOOK table."""
    moves = _day_moves(state)
    positions = state.get("positions") or {}
    out = []
    for r in book.get("open_rows", []):
        out.append({
            "pair": r["pair"], "quote": r["quote"],
            "weight": round(_f(positions.get(r["pair"], r["weight"])), 5),
            "lot_weight": r["weight"],
            "avg_entry": _f(r.get("avg_entry"), None),
            "price": _f(r.get("price"), None),
            "notional": round(_f(r.get("notional")), 2),
            "entry_cost": round(_f(r.get("entry_cost")), 2),
            "unrealized": round(_f(r.get("unrealized")), 2),
            "unrealized_pct": round(_f(r.get("unrealized_pct")), 4),
            "day_move": (round(moves[r["pair"]], 6)
                         if r["pair"] in moves else None),
            "held_since": r.get("entry_date") or "",
            "n_lots": r.get("n_lots", 0),
            "fx_known": bool(r.get("fx_known", True)),
            "priced": bool(r.get("priced", True)),
        })
    return out


def _closed(book: dict, totals: dict) -> dict:
    """FIFO closed round-trips in the account currency — the FX counterpart of
    the equity CLOSED TRADES ledger. `net` is the price+FX move less the spread
    paid on BOTH legs, so it is what the round-trip actually put in the book."""
    rows = []
    for r in book.get("closed", []):
        rows.append({
            "date": r.get("date"), "pair": r.get("pair"), "side": r.get("side"),
            "weight": round(_f(r.get("filled")), 5),
            "entry": _f(r.get("entry"), None), "exit": _f(r.get("exit"), None),
            "held_days": int(r.get("held_days") or 0),
            "notional": round(_f(r.get("entry_notional")), 2),
            "gross": round(_f(r.get("gross")), 2),
            "costs": round(_f(r.get("entry_cost")) + _f(r.get("exit_cost")), 2),
            "net": round(_f(r.get("net")), 2),
            "return_pct": (round(_f(r.get("net")) / _f(r.get("entry_notional")), 4)
                           if _f(r.get("entry_notional")) else 0.0),
            "fx_known": bool(r.get("fx_known", True)),
        })
    rows.sort(key=lambda r: (str(r["date"]), -abs(r["net"])))
    # Totals come from the ledger's own unrounded sums (`totals`), not from
    # re-adding the rounded display rows — so the footer can never drift a cent
    # from the NET P&L tile, and bounding the rows below stays safe.
    return {
        "rows": rows[-LEDGER_MAX:],
        "shown": min(len(rows), LEDGER_MAX),
        "net": totals["realized"],
        "gross": totals["realized_gross"],
        "costs": round(totals["costs"] - sum(r["entry_cost"]
                                             for r in book.get("open_rows", [])), 2),
        "wins": totals["wins"],
        "count": totals["round_trips"],
    }


def _drawdown(hist: list) -> list[dict]:
    """Drawdown-from-peak series off the persisted equity curve — the same
    chart the equity screens carry, in percent of peak.

    A mark the book could not be valued on (NaN in equity_history) is SKIPPED,
    not read as zero: `_f`'s 0.0 default would print a −100% drawdown that never
    happened, and the equity line on the same screen already drops it."""
    out, peak = [], None
    for d, e in hist or []:
        v = _f(e, None)
        if v is None:
            continue
        peak = v if peak is None else max(peak, v)
        out.append({"date": d, "dd": round(v / peak - 1.0, 6) if peak else 0.0})
    return out


def _cumulative(state: dict) -> dict | None:
    """The book's EXACT money decomposition, accumulated bar by bar by
    `fx_book.run_once`: price P&L (incl. FX translation), carry/financing and
    spread, total and per instrument. `equity − start_equity` equals
    `price_pnl + carry − cost` by construction. None for a book whose history
    predates the accumulator (nothing to show rather than a wrong number)."""
    cum = state.get("cumulative")
    if not isinstance(cum, dict) or "from" not in cum:
        return None
    legs = [{"pair": k, "pnl": round(_f(v.get("pnl")), 2),
             "carry": round(_f(v.get("carry")), 2),
             "cost": round(_f(v.get("cost")), 2),
             "net": round(_f(v.get("pnl")) + _f(v.get("carry"))
                          - _f(v.get("cost")), 2)}
            for k, v in (cum.get("by_pair") or {}).items()]
    legs.sort(key=lambda r: -abs(r["net"]))
    start = _f(cum.get("start_equity"))
    return {
        "from": cum.get("from"), "to": cum.get("to", ""),
        "bars": int(cum.get("bars") or 0),
        "start_equity": round(start, 2),
        "price_pnl": round(_f(cum.get("price_pnl")), 2),
        "carry": round(_f(cum.get("carry")), 2),
        "cost": round(_f(cum.get("cost")), 2),
        "net": round(_f(cum.get("price_pnl")) + _f(cum.get("carry"))
                     - _f(cum.get("cost")), 2),
        "from_inception": abs(start - _f(state.get("initial_capital"))) < 0.01,
        "by_pair": legs,
    }


def _tape(state: dict) -> list[dict]:
    """Ticker tape: every symbol's last close, with the day-move sign taken
    from the daily attribution (`move` is the pair's own price move)."""
    moves = {b["pair"]: _f(b.get("move"))
             for b in (state.get("daily") or {}).get("by_pair", [])}
    out = []
    for pair, px in (state.get("last_close") or {}).items():
        out.append({"k": pair, "price": _f(px, None), "move": moves.get(pair)})
    return out


def _pair_currencies(symbols) -> list[str]:
    """Fiat currency codes referenced by a book's pairs (crypto/equity legs that
    aren't in the economic calendar drop out via news.FIAT)."""
    out: list[str] = []
    for s in symbols or []:
        s = str(s).upper()
        if len(s) == 6:
            for c in (s[:3], s[3:]):
                if c in fxnews.FIAT and c not in out:
                    out.append(c)
    return out


def _news(state: dict) -> list[dict]:
    """Recent high-impact economic releases for the currencies the book trades,
    dated so the chart can mark when each landed. Offline-safe: [] with no
    NEWS_API_KEY / no network / nothing relevant (news.calendar_range never
    raises)."""
    symbols = list((state.get("decisions") or {}).keys()) or (state.get("symbols") or [])
    currencies = _pair_currencies(symbols)
    if not currencies:
        return []
    anchor_raw = str(state.get("last_bar_date") or "")[:10]
    try:
        anchor = date.fromisoformat(anchor_raw)
    except ValueError:
        anchor = datetime.now(timezone.utc).date()
    start = (anchor - timedelta(days=NEWS_WINDOW_DAYS)).isoformat()
    end = (anchor + timedelta(days=2)).isoformat()      # include imminent events
    return fxnews.calendar_range(currencies, start, end, high_only=True)


def build_fx_snapshot(account: str) -> dict:
    """Full agent-book payload for one FX account (display key or raw name)."""
    entry = registry.resolve(account)
    if entry is not None and entry["kind"] == "fx":
        account = entry["account"]
    # fx_book.load_state raises SystemExit (a BaseException) on a missing file,
    # which would escape the server's error handling — pre-check ourselves.
    if not os.path.exists(fx_book._state_file(account)):
        raise FileNotFoundError(f"no FX account '{account}'")
    state = fx_book.load_state(account)
    if entry is None:
        entry = registry._fx_entry(account, state)
    p = _profile_params(state)

    equity = _f(state.get("equity"))
    initial = _f(state.get("initial_capital"))
    peak = _f(state.get("peak_equity"), equity) or equity
    hist = state.get("equity_history") or []
    daily = state.get("daily") or {}

    day_pct = daily.get("net_pct")
    day_aud = daily.get("net_aud")
    if day_pct is None and len(hist) > 1 and hist[-2][1]:
        day_pct = hist[-1][1] / hist[-2][1] - 1.0
        day_aud = hist[-1][1] - hist[-2][1]
    day_pct = _f(day_pct, None) if day_pct is not None else None
    day_aud = _f(day_aud, None) if day_aud is not None else None

    positions = {k: _f(w) for k, w in (state.get("positions") or {}).items()}
    gross = sum(abs(w) for w in positions.values())
    net = sum(positions.values())
    n_long = sum(1 for w in positions.values() if w > 0)
    n_short = sum(1 for w in positions.values() if w < 0)

    regimes = [str(d.get("regime", "")).upper()
               for d in (state.get("decisions") or {}).values()]

    # --- the money layer (all derived offline from the persisted book) -------
    book = fx_pnl.book_pnl(state)
    pnl = {k: v for k, v in book.items()
           if k not in ("open_rows", "closed", "by_pair", "exposure")}
    closed = _closed(book, pnl)
    blotter = fx_pnl.blotter(state)

    # Symbols the quality gate dropped from the target book on the last bar (a
    # book silently trading one leg fewer must not be invisible). None — not an
    # empty dict — on any book written before fx_book persisted this, since
    # "nothing flagged" and "never measured" are different claims.
    dq = state.get("data_quality")
    dq = {"date": str(dq.get("date") or ""),
          "excluded": [str(s) for s in (dq.get("excluded") or [])],
          "reasons": {str(k): str(v) for k, v in (dq.get("reasons") or {}).items()},
          } if isinstance(dq, dict) else None

    return {
        "kind": "fx",
        "account": account,
        "key": entry["key"],
        "label": entry["label"],
        "sub": entry["sub"],
        "profile": state.get("profile", "balanced"),
        "bar": state.get("bar", "1d"),
        "source": state.get("source", ""),
        "base_currency": state.get("currency", "AUD"),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "equity": round(equity, 2),
        "initial": round(initial, 2),
        "peak": round(peak, 2),
        "off_peak": round(equity / peak - 1.0, 6) if peak else 0.0,
        "total_return": round(equity / initial - 1.0, 6) if initial else 0.0,
        "day_pct": round(day_pct, 6) if day_pct is not None else None,
        "day_aud": round(day_aud, 2) if day_aud is not None else None,
        "gross": round(gross, 4),
        "net": round(net, 4),
        "n_long": n_long,
        "n_short": n_short,
        "risk_halted": bool(state.get("risk_halted", False)),
        "data_quality": dq,
        "halt_cooldown": int(state.get("halt_cooldown", 0)),
        "breaker": p.max_drawdown_stop,
        "per_pair_cap": p.per_pair_cap,
        "target_vol": p.target_vol,
        "max_gross": p.max_gross,
        "n_trades": len(state.get("trades") or []),
        "last_bar_date": state.get("last_bar_date", ""),
        "since": hist[0][0] if hist else "",
        "equity_history": hist,
        "rows": _rows(state, book),
        # --- money layer: the FX book in the account currency ---------------
        "pnl": pnl,
        "positions_money": _positions_money(book, state),
        "pnl_by_pair": book["by_pair"],
        "exposure": book["exposure"],
        "blotter": blotter[-LEDGER_MAX:],
        "blotter_shown": min(len(blotter), LEDGER_MAX),
        "blotter_count": len(blotter),
        "closed": closed,
        "drawdown": _drawdown(hist),
        "cumulative": _cumulative(state),
        "agent_order": AGENT_ORDER,
        "tape": _tape(state),
        "news": _news(state),
        "news_available": bool(fxnews._key()),
        "attribution": [
            {"pair": b.get("pair"), "contrib": _f(b.get("contrib")),
             "move": _f(b.get("move")), "fx": _f(b.get("fx")),
             "weight": _f(b.get("weight"))}
            for b in daily.get("by_pair", [])
        ],
        "daily": {k: (_f(daily.get(k), None) if daily.get(k) is not None else None)
                  for k in ("pnl_pct", "carry_pct", "cost_pct", "net_pct", "net_aud")}
                 | {"date": daily.get("date")},
        "regime_counts": {
            "trending": sum(1 for r in regimes if r == "TRENDING"),
            "ranging": sum(1 for r in regimes if r == "RANGING"),
        },
    }
