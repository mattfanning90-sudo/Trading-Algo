"""All-accounts overview: every paper book on disk rolled up into one AUD view.

Reads only the persisted state files (offline-safe — equity books are shown at
their last saved mark, not re-priced), so the ALL ACCOUNTS screen always
renders even when market data is unreachable.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from .. import config as cfg
from .. import paper_trade
from ..forex import fx_book
from . import registry


def _num(v):
    """None for a mark the book could not be valued on — never a NaN.

    A single NaN here makes json.dumps emit a bare `NaN`, which is not JSON:
    the browser's JSON.parse throws and the ENTIRE all-accounts screen renders
    empty. `state/paper_state_full.json` has four such marks right now.
    """
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _load(entry: dict) -> dict | None:
    path = (paper_trade._state_file(entry["account"]) if entry["kind"] == "equity"
            else fx_book._state_file(entry["account"]))
    return registry._peek(path)


def _card(entry: dict, state: dict) -> dict | None:
    kind = entry["kind"]
    # Marks the book could actually be valued on. An unvaluable one is dropped,
    # not charted as zero — the same rule the account pages already follow.
    hist = [(d, _num(v)) for d, v in (state.get("equity_history") or [])]
    hist = [(d, v) for d, v in hist if v is not None]
    if kind == "equity":
        initial = float(state.get("initial_capital_base") or 0.0)
        equity = float(hist[-1][1]) if hist else initial
        peak = float(state.get("peak_equity_base") or equity or 1.0)
        n_positions = sum(len(s.get("positions") or {})
                          for s in (state.get("sleeves") or {}).values())
        n_line = f"{n_positions} POSITIONS · {len(state.get('trades') or [])} TRADES"
    else:
        initial = float(state.get("initial_capital") or 0.0)
        equity = _num(state.get("equity")) or (hist[-1][1] if hist else initial)
        peak = _num(state.get("peak_equity")) or equity or 1.0
        positions = state.get("positions") or {}
        gross = sum(abs(w) for w in positions.values())
        unit = ("SYMBOLS" if any(not registry._is_pairlike(s) for s in positions)
                else "PAIRS")
        n_line = f"{len(positions)} {unit} · {gross * 100:.0f}% GROSS"

    if not hist and not initial:
        return None
    # None = "this book's last move is not measurable", which is NOT the same
    # claim as 0.0 ("it did not move"): _totals used to count the unmeasurable
    # book as flat and not-red, and the card painted "+0.00%" green.
    day = ((hist[-1][1] / hist[-2][1] - 1.0)
           if len(hist) > 1 and hist[-2][1] else None)
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

    # Reporting group: CORE books sum into the headline AUM; any other group
    # (e.g. EXPERIMENTAL) is broken out into its own separate total.
    group = str(state.get("group") or "CORE").upper() if kind == "equity" else "CORE"

    # The card colours this line by `ret` — equity against INITIAL CAPITAL,
    # since inception — so the line has to DRAW that measurement. hist[-40:] was
    # the last ~2 days of the hourly FX book, and it opened at the first mark
    # rather than at capital: a book up 51.4% drew a falling green spark, and one
    # down 0.11% drew a rising red one. Anchor at capital, sample the whole life
    # down to 40 points keeping both ends (the last mark IS the equity printed).
    pts = ([v for _, v in hist] if len(hist) <= 40
           else [hist[round(i * (len(hist) - 1) / 39)][1] for i in range(40)])
    return {
        "key": entry["key"], "account": entry["account"], "kind": kind,
        "name": entry["label"] if kind == "fx" else entry["label"].split(" · ")[0]
        if not micro else entry["label"],
        "label": entry["label"], "sub": entry["sub"],
        "group": group,
        "equity": round(equity, 2), "initial": round(initial, 2),
        "ret": round(equity / initial - 1.0, 6) if initial else 0.0,
        "day": None if day is None else round(day, 6),
        "since": hist[0][0][:10] if hist else "",
        "spark": ([initial] + pts) if initial and hist else pts,
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

    def _pick(c):
        return {"key": c["key"], "name": c["name"], "ret": c["ret"],
                "since": c["since"]} if c else None

    def _totals(group_cards: list[dict]) -> dict:
        aum = sum(c["equity"] for c in group_cards)
        initial = sum(c["initial"] for c in group_cards)
        # A book whose day is unmeasurable is EXCLUDED and counted, not silently
        # treated as flat: `nan > -1` was False, so FULL — 77% of the group —
        # contributed exactly zero to a headline that still read as a complete
        # day P&L. `day <= -1` (a total wipeout in one bar) was zeroed the same
        # way, reporting a wipe-out as "didn't move".
        measured = [c for c in group_cards if c["day"] is not None and c["day"] > -1]
        day_aud = sum(c["equity"] * c["day"] / (1 + c["day"]) for c in measured)
        best = max(group_cards, key=lambda c: c["ret"], default=None)
        worst = min(group_cards, key=lambda c: c["ret"], default=None)
        return {
            "aum": round(aum, 2),
            "initial": round(initial, 2),
            "net_pnl": round(aum - initial, 2),
            "net_pnl_pct": round(aum / initial - 1.0, 6) if initial else 0.0,
            "day_aud": round(day_aud, 2),
            "day_pct": round(day_aud / aum, 6) if aum else 0.0,
            "day_books": len(measured),
            "books": len(group_cards),
            "books_red": sum(1 for c in measured if c["day"] < 0),
            "halts": sum(1 for c in group_cards if c["halted"]),
            "best": _pick(best),
            "worst": _pick(worst),
        }

    # Split books by reporting group. CORE is the headline AUM; every other
    # group (e.g. EXPERIMENTAL) gets its OWN separate total and is EXCLUDED from
    # the headline — so an unproven / geared book can run live without moving the
    # number you actually watch.
    group_names: list[str] = []
    for c in cards:
        if c["group"] not in group_names:
            group_names.append(c["group"])
    # CORE first, the rest in first-seen order.
    group_names.sort(key=lambda g: (g != "CORE",))

    # Share is computed WITHIN each group so every group's allocation bar is 100%.
    for g in group_names:
        gcards = [c for c in cards if c["group"] == g]
        gaum = sum(c["equity"] for c in gcards)
        for c in gcards:
            c["share"] = round(c["equity"] / gaum, 4) if gaum else 0.0

    groups = [{"name": g, **_totals([c for c in cards if c["group"] == g])}
              for g in group_names]

    # Headline totals = CORE only, ALWAYS. Experimental books never move it (even
    # if they're the only books on disk) — that's the whole point of the split.
    headline = _totals([c for c in cards if c["group"] == "CORE"])

    return {
        "kind": "all",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "totals": headline,
        "groups": groups,
        "accounts": cards,
    }
