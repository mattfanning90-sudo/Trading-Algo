"""The "Is it working?" rules (docs/specs/book-screens.md). Each rule reads a
BookInputs and returns items; nothing here reads a file, the network or the
clock. Thresholds are the spec's, named once here."""
from __future__ import annotations

import pandas as pd

from .. import metrics
from .textfmt import day, direction, money, pct, points

SMALL_POSITIONS = 30
SMALL_DATES = 6
IDLE_BELOW = 0.5
COSTS_HALF = 0.5
FTSE_SHARE = 0.5
RESIDUAL_SHARE = 0.10
FROZEN_RECENT = 10
GAP_SPREADS = 2.0
BACKTEST_STALE_DAYS = 14
LUCK_STALE_DAYS = 30

RANK = {"halted": 1, "audit-error": 2, "near-breaker": 3, "stale": 4,
        "costs-flip": 5, "no-breaker": 6, "holds-too-short": 7,
        "loses-more-often": 8, "behind-market": 9, "mostly-idle": 10,
        "audit-warn": 11, "frozen-prices": 12}
# An audit WARN a rule already explains is shown once, by that rule.
COVERED_BY = {"idle-sleeve": "mostly-idle", "pinned-flat": "mostly-idle",
              "hold-shorter-than-signal": "holds-too-short",
              "dead-price-turnover": "frozen-prices"}
MARKET_TEXT = {
    "pending": "market comparison loading",
    "idle": "market comparison loading",
    "n/a": "market comparison not loaded",
    "offline": "the market comparison needs today's prices, which aren't available offline",
    "error": "couldn't load the market comparison",
}
DIVIDENDS = "The market figure leaves out dividends, which flatters this book."
PLAIN = {
    "no-fx-rate": "No exchange rate is saved for {subject}, so its money can't be checked",
    "position-mismatch": "The holdings saved for {subject} don't match its trade record",
    "cash-drift": "The cash saved for {subject} doesn't match its trade record",
    "missing-position": "The saved position in {subject} doesn't match its trade record",
    "weight-mismatch": "The saved position in {subject} doesn't match its trade record",
    "closed-market-trade": "Some trades were booked while their market was shut",
    "never-traded": "{subject} has never traded and its price data looks broken",
    "idle-sleeve": "{subject} has been sitting in cash for a long time",
    "pinned-flat": "{subject} holds nothing until next month's rebalance",
    "dead-price-turnover": "Some trades changed a position at a frozen price, paying costs for nothing",
    "hold-shorter-than-signal": "Positions close long before their signal horizon",
    "unreadable-state": "The saved book couldn't be read",
}


def item(rule: str, column: str, text: str, *, source: str, as_of: str | None = None,
         caveat: str | None = None, backlog: str | None = None,
         level: str | None = None, dir: str | None = None) -> dict:
    return {"id": rule, "column": column, "text": text, "caveat": caveat,
            "source": source, "as_of": as_of, "backlog": backlog, "level": level,
            "rank": RANK.get(rule), "dir": dir}


def as_of(inp) -> str | None:
    return (inp.book.get("as_of") or "")[:10] or None


def long_short(inp) -> bool:
    return bool((inp.state.get("param_overrides") or {}).get("long_short"))


def max_gross(inp) -> float:
    return float((inp.state.get("param_overrides") or {}).get("max_gross") or 1.0)


def daily_values(inp) -> pd.Series:
    """The saved value, last mark per day (unvaluable marks were already
    dropped by home._marks)."""
    daily: dict[str, float] = {}
    for t, v in inp.book.get("_marks") or []:
        daily[t.date().isoformat()] = float(v)
    keys = sorted(daily)
    return pd.Series([daily[k] for k in keys], index=pd.to_datetime(keys), dtype=float)


def _src_saved(inp) -> str:
    d = as_of(inp)
    return f"saved book, {day(d)}" if d else "saved book"


def _src_audit(inp) -> str:
    return f"live audit, {day(inp.now)}"


def plain_finding(f) -> str:
    subject = (f.detail or {}).get("subject") or "One part of the book"
    tmpl = PLAIN.get(f.code)
    return tmpl.format(subject=subject) if tmpl else f"Audit finding: {f.message}"


def market(inp) -> dict | None:
    """The book against its benchmark to the last SAVED date, and how far the
    two normally stray. None when there's nothing fair to compare."""
    if inp.kind != "equity" or long_short(inp) or inp.market != "ready" or not inp.live:
        return None
    bench = {r["date"]: float(r["value"]) for r in inp.live.get("benchmark_curve") or []
             if r.get("value") is not None}
    vals = daily_values(inp)
    days = [d for d in vals.index if d.strftime("%Y-%m-%d") in bench]
    if len(days) < 3:
        return None
    s = vals.loc[days]
    b = pd.Series([bench[d.strftime("%Y-%m-%d")] for d in days], index=days, dtype=float)
    initial = float(inp.card["initial"])
    gap = (s.iloc[-1] / initial - 1.0) - (b.iloc[-1] / initial - 1.0)
    stats = metrics.benchmark_stats(s.pct_change().dropna(), b.pct_change().dropna())
    spread = metrics.expected_gap(stats.get("TrackingError") or 0.0,
                                  metrics.years_between(days[0], days[-1]))
    return {"gap": float(gap), "spread": spread, "bench_ret": float(b.iloc[-1] / initial - 1.0),
            "beyond": abs(gap) > GAP_SPREADS * spread, "last": days[-1].strftime("%Y-%m-%d")}


def result(inp) -> dict:
    marks = inp.book.get("_marks") or []
    since = inp.card.get("since")
    if len(marks) < 2:
        text = f"Started {day(since)}; too new to judge" if since else "No saved values yet; too new to judge"
        return item("result", "result", text, source=_src_saved(inp))
    eq, init = float(inp.card["equity"]), float(inp.card["initial"])
    chg = eq - init
    word = "Up" if chg > 0 else "Down" if chg < 0 else "Flat"
    text = (f"{word} {money(abs(chg))} ({pct(chg / init, 2)}) since {day(since)}, "
            f"as of {day(as_of(inp))}")
    caveat = None
    m = market(inp)
    if m is not None:
        side = "ahead of" if m["gap"] > 0 else "behind"
        text += (f"; {points(m['gap'])} {side} the market, "
                 + ("more than its normal ups and downs" if m["beyond"]
                    else "within its normal ups and downs"))
        caveat = DIVIDENDS
    elif inp.kind == "fx" or long_short(inp):
        text += "; it aims to make money whichever way markets move, so it isn't compared with one"
    else:
        text += f"; {MARKET_TEXT.get(inp.market, MARKET_TEXT['n/a'])}"
    return item("result", "result", text, source=_src_saved(inp), as_of=as_of(inp),
                caveat=caveat, dir=direction(chg))


def attention(inp) -> list[dict]:
    out: list[dict] = []
    if inp.book.get("halted"):
        left = inp.state.get("halt_cooldown")
        tail = f": {left} trading days left" if left else ""
        out.append(item("halted", "attention", f"Paused by its safety breaker{tail}",
                        source=_src_saved(inp), level="act"))
    for f in inp.findings:
        if f.level == "ERROR":
            out.append(item("audit-error", "attention", plain_finding(f),
                            source=_src_audit(inp), level="act"))
    if inp.book.get("near_breaker"):
        out.append(item("near-breaker", "attention",
                        f"{pct(inp.card['off_peak'])} below its best; the safety breaker "
                        f"pauses it at {pct(-inp.card['breaker'], 0)}",
                        source=_src_saved(inp), level="look"))
    fresh = inp.book.get("freshness")
    if fresh in ("late", "stale"):
        d = as_of(inp)
        out.append(item("stale", "attention",
                        f"No new value since {day(d)}; the book is behind its schedule ({fresh})"
                        if d else "No saved values yet", source="saved book", level="look"))
    if inp.card.get("breaker") is None and max_gross(inp) > 1.0:
        out.append(item("no-breaker", "attention",
                        f"No safety breaker on this book, though it is geared up to "
                        f"{max_gross(inp):.0f}× its capital", source="the book's profile",
                        level="look"))
    return out


def closed_stats(inp):
    pos = [p for p in inp.positions_closed if p.get("net") is not None]
    won = sum(p["net"] for p in pos if p["net"] > 0)
    lost = -sum(p["net"] for p in pos if p["net"] < 0)
    wins = sum(1 for p in pos if p["net"] > 0)
    dates = len({str(p["closed"])[:10] for p in pos})
    return pos, won, lost, wins, dates


def _sample_caveat(n: int, dates: int, excluded: int) -> str | None:
    parts = []
    if n < SMALL_POSITIONS or dates < SMALL_DATES:
        parts.append(f"Small sample: {n} positions closed on {dates} dates.")
    if excluded:
        parts.append(f"Leaves out {excluded} positions closed by a since-fixed bug "
                     "(see the book's corrections).")
    return " ".join(parts) or None


def going_right(inp) -> list[dict]:
    out: list[dict] = []
    m = market(inp)
    if m and m["beyond"] and m["gap"] > 0:
        out.append(item("ahead-of-market", "right",
                        f"Ahead of the market by {points(m['gap'])} since {day(inp.card['since'])}, "
                        "more than its normal ups and downs", source=_src_saved(inp),
                        caveat=DIVIDENDS))
    pos, won, lost, wins, dates = closed_stats(inp)
    if pos and won - lost > 0:
        pf = f" (profit factor {won / lost:.1f})" if lost else ""
        out.append(item("winners-pay", "right",
                        f"Closed positions made more than they lost: {money(won)} won against "
                        f"{money(lost)} lost{pf}, over {len(pos)} positions",
                        source=f"trade record, {day(inp.now)}",
                        caveat=_sample_caveat(len(pos), dates, inp.excluded_forced)))
    return out
