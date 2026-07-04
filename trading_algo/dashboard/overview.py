"""All-accounts overview: every paper book on disk rolled up into one AUD view.

Reads only the persisted state files (offline-safe — equity books are shown at
their last saved mark, not re-priced), so the ALL ACCOUNTS screen always
renders even when market data is unreachable.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .. import config as cfg
from .. import paper_trade
from ..forex import fx_book
from . import registry


def _load(entry: dict) -> dict | None:
    path = (paper_trade._state_file(entry["account"]) if entry["kind"] == "equity"
            else fx_book._state_file(entry["account"]))
    return registry._peek(path)


def _card(entry: dict, state: dict) -> dict | None:
    kind = entry["kind"]
    hist = state.get("equity_history") or []
    if kind == "equity":
        initial = float(state.get("initial_capital_base") or 0.0)
        equity = float(hist[-1][1]) if hist else initial
        peak = float(state.get("peak_equity_base") or equity or 1.0)
        n_positions = sum(len(s.get("positions") or {})
                          for s in (state.get("sleeves") or {}).values())
        n_line = f"{n_positions} POSITIONS · {len(state.get('trades') or [])} TRADES"
    else:
        initial = float(state.get("initial_capital") or 0.0)
        equity = float(state.get("equity") or (hist[-1][1] if hist else initial))
        peak = float(state.get("peak_equity") or equity or 1.0)
        positions = state.get("positions") or {}
        gross = sum(abs(w) for w in positions.values())
        unit = ("SYMBOLS" if any(not registry._is_pairlike(s) for s in positions)
                else "PAIRS")
        n_line = f"{len(positions)} {unit} · {gross * 100:.0f}% GROSS"

    if not hist and not initial:
        return None
    day = (hist[-1][1] / hist[-2][1] - 1.0) if len(hist) > 1 and hist[-2][1] else 0.0
    off_peak = equity / peak - 1.0 if peak else 0.0
    halted = bool(state.get("risk_halted", False))
    micro = entry.get("micro", False)

    if halted:
        status, tone = "RISK-HALTED", "bad"
    elif micro:
        status, tone = f"GATE ARMED @ A${cfg.MIN_VIABLE_EQUITY_BASE:,.0f}", "warn"
    elif off_peak < -0.01:
        status, tone = f"OFF PEAK −{abs(off_peak) * 100:.1f}%", "warn"
    else:
        status, tone = "CLEAR", "ok"

    return {
        "key": entry["key"], "account": entry["account"], "kind": kind,
        "name": entry["label"] if kind == "fx" else entry["label"].split(" · ")[0]
        if not micro else entry["label"],
        "label": entry["label"], "sub": entry["sub"],
        "equity": round(equity, 2), "initial": round(initial, 2),
        "ret": round(equity / initial - 1.0, 6) if initial else 0.0,
        "day": round(day, 6),
        "since": hist[0][0][:10] if hist else "",
        "spark": [v for _, v in hist[-40:]],
        "status": status, "status_tone": tone,
        "halted": halted,
        "n_line": n_line,
    }


def build_overview(regime_hints: dict[str, str] | None = None) -> dict:
    """Aggregate every discovered book. `regime_hints` optionally upgrades an
    equity card's status chip (e.g. 'ASX RISK-OFF') from a fresher snapshot the
    server may have cached — the overview itself never hits the network."""
    cards = []
    for entry in registry.discover_accounts():
        state = _load(entry)
        if state is None:
            continue
        card = _card(entry, state)
        if card is None:
            continue
        hint = (regime_hints or {}).get(entry["account"])
        if hint and card["status_tone"] in ("ok", "warn") and not card["halted"]:
            card["status"], card["status_tone"] = hint, "warn"
        cards.append(card)

    aum = sum(c["equity"] for c in cards)
    initial = sum(c["initial"] for c in cards)
    day_aud = sum(c["equity"] * c["day"] / (1 + c["day"]) if c["day"] > -1 else 0.0
                  for c in cards)
    for c in cards:
        c["share"] = round(c["equity"] / aum, 4) if aum else 0.0
    reds = sum(1 for c in cards if c["day"] < 0)
    best = max(cards, key=lambda c: c["ret"], default=None)
    worst = min(cards, key=lambda c: c["ret"], default=None)

    def _pick(c):
        return {"key": c["key"], "name": c["name"], "ret": c["ret"],
                "since": c["since"]} if c else None

    return {
        "kind": "all",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "totals": {
            "aum": round(aum, 2),
            "initial": round(initial, 2),
            "net_pnl": round(aum - initial, 2),
            "net_pnl_pct": round(aum / initial - 1.0, 6) if initial else 0.0,
            "day_aud": round(day_aud, 2),
            "day_pct": round(day_aud / aum, 6) if aum else 0.0,
            "books": len(cards),
            "books_red": reds,
            "halts": sum(1 for c in cards if c["halted"]),
            "best": _pick(best),
            "worst": _pick(worst),
        },
        "accounts": cards,
    }
