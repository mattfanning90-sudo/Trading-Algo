"""FX agent-book snapshot for the terminal dashboard.

Everything comes from the persisted fx_state_{account}.json — decisions carry
the agent votes, indicators and "why" text; `daily` carries the attribution.
Pure read, fully offline; never touches the network.
"""
from __future__ import annotations

import math
import os
from datetime import datetime, timezone

from ..forex import fx_book
from ..forex import fx_config as fxcfg
from . import registry

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


def _rows(state: dict) -> list[dict]:
    decisions = state.get("decisions") or {}
    positions = state.get("positions") or {}
    rows = []
    for pair, d in decisions.items():
        ind = d.get("indicators") or {}
        agents = d.get("agents") or {}
        weight = _f(positions.get(pair, d.get("weight", 0.0)))
        price = ind.get("price", state.get("last_close", {}).get(pair))
        vol = ind.get("ann_vol")
        rows.append({
            "pair": pair,
            "weight": round(weight, 5),
            "tilt": round(_f(d.get("tilt")), 4),
            "regime": str(d.get("regime", "")).upper(),
            "price": _f(price, None),
            "ann_vol": _f(vol, None),
            "agents": [round(_f(agents.get(a)), 3) for a in AGENT_ORDER],
            "why": d.get("text", ""),
        })
    rows.sort(key=lambda r: -abs(r["weight"]))
    return rows


def _tape(state: dict) -> list[dict]:
    """Ticker tape: every symbol's last close, with the day-move sign taken
    from the daily attribution (`move` is the pair's own price move)."""
    moves = {b["pair"]: _f(b.get("move"))
             for b in (state.get("daily") or {}).get("by_pair", [])}
    out = []
    for pair, px in (state.get("last_close") or {}).items():
        out.append({"k": pair, "price": _f(px, None), "move": moves.get(pair)})
    return out


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
        "halt_cooldown": int(state.get("halt_cooldown", 0)),
        "breaker": p.max_drawdown_stop,
        "per_pair_cap": p.per_pair_cap,
        "target_vol": p.target_vol,
        "max_gross": p.max_gross,
        "n_trades": len(state.get("trades") or []),
        "last_bar_date": state.get("last_bar_date", ""),
        "since": hist[0][0] if hist else "",
        "equity_history": hist,
        "rows": _rows(state),
        "agent_order": AGENT_ORDER,
        "tape": _tape(state),
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
