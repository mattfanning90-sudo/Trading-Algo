"""The Mac home screen's one API: "am I up, and is anything wrong?".

Every figure is read from an existing source rather than recomputed: totals
and each book's fall from its stored peak (the breaker's own figure) come from
`overview`, problems come from `verify`, job health from
`trading_algo.home.jobs`. What this module adds is only (a) the change between
two saved equity marks over a period and (b) the rules for what deserves
attention. No drawdown, Sharpe or annualisation is computed here
(tests/test_metrics_single_source.py).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .. import verify
from ..home import seen
from . import overview, registry

PERIODS = ("1D", "1W", "1M", "ALL")
_PERIOD_DAYS = {"1W": 7, "1M": 30}
NEAR_BREAKER = 0.6                      # spec Q-1 (draft)
HOURLY_FRESH = timedelta(hours=14)      # measured: longest same-day gap 796 min


def _ts(stamp: str) -> datetime:
    """'2026-10-09' or '2026-10-09 02:00' -> aware UTC datetime."""
    s = str(stamp)[:16]
    fmt = "%Y-%m-%d %H:%M" if " " in s else "%Y-%m-%d"
    return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)


def _marks(state: dict) -> list[tuple[datetime, float]]:
    """Saved equity marks, oldest first; unvaluable (None/NaN) marks dropped."""
    out = []
    for stamp, value in state.get("equity_history") or []:
        v = overview._num(value)
        if v is not None:
            out.append((_ts(stamp), v))
    out.sort(key=lambda m: m[0])
    return out


def period_change(marks: list[tuple[datetime, float]], initial: float,
                  period: str, hourly: bool) -> dict:
    """The latest mark against the period's starting value.

    1D    the last completed session: the previous mark for a daily book; for
          the hourly book, its last mark of an earlier UTC day.
    1W/1M the last mark on or before 7 / 30 days before the latest mark.
    ALL   against the capital put in.
    """
    if not marks:
        return {"measurable": False}
    end_t, end_v = marks[-1]
    start = None
    if period == "ALL":
        start = initial or None
    elif period == "1D":
        if hourly:
            earlier = [v for t, v in marks if t.date() < end_t.date()]
            start = earlier[-1] if earlier else None
        elif len(marks) > 1:
            start = marks[-2][1]
    else:
        cutoff = end_t - timedelta(days=_PERIOD_DAYS[period])
        before = [v for t, v in marks if t <= cutoff]
        start = before[-1] if before else None
    if not start or start <= 0:
        return {"measurable": False}
    return {"measurable": True, "start": round(start, 2), "end": round(end_v, 2),
            "change": round(end_v - start, 2), "pct": round(end_v / start - 1.0, 6)}


def _weekdays_between(a, b) -> int:
    """Weekdays in (a, b] — how many trading days a daily book has missed."""
    n, d = 0, a
    while d < b:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def freshness(as_of: datetime, now: datetime, hourly: bool) -> str:
    if hourly:
        if now.weekday() >= 5:
            friday = now.date() - timedelta(days=now.weekday() - 4)
            return "fresh" if as_of.date() >= friday else "stale"
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return "fresh" if now - max(as_of, midnight) <= HOURLY_FRESH else "stale"
    age = _weekdays_between(as_of.date(), now.date())
    return "fresh" if age <= 1 else "late" if age == 2 else "stale"


def _daily(marks):
    daily: dict[str, float] = {}
    for t, v in marks:
        daily[t.date().isoformat()] = round(v, 2)
    return sorted(daily.items())


def _book(entry: dict, state: dict, card: dict, now: datetime) -> dict:
    marks = _marks(state)
    hourly = entry["kind"] == "fx" and str(state.get("bar") or "1d") != "1d"
    breaker = card.get("breaker")
    off_peak = card.get("off_peak", 0.0)
    return {
        "key": card["key"], "account": entry["account"], "name": card["name"],
        "kind": card["kind"], "group": card["group"], "equity": card["equity"],
        "initial": card["initial"], "hourly": hourly,
        "as_of": marks[-1][0].isoformat() if marks else None,
        "freshness": freshness(marks[-1][0], now, hourly) if marks else "stale",
        "off_peak": off_peak, "breaker": breaker,
        "near_breaker": breaker is not None and off_peak <= -NEAR_BREAKER * breaker,
        "halted": card["halted"], "status": card["status"],
        "periods": {p: period_change(marks, card["initial"], p, hourly) for p in PERIODS},
        # P&L in A$ per day (last mark of the day minus capital) — computed here so
        # the page never does money arithmetic of its own (G-4).
        "series": [[d, round(v - card["initial"], 2)] for d, v in _daily(marks)],
        "_marks": marks,
    }


def _aggregate(members: list[dict], period: str) -> dict:
    measured = [b["periods"][period] for b in members if b["periods"][period]["measurable"]]
    if not measured:
        return {"measurable": False, "measured": 0, "of": len(members)}
    start = sum(m["start"] for m in measured)
    change = sum(m["change"] for m in measured)
    return {"measurable": True, "change": round(change, 2),
            "pct": round(change / start, 6) if start else 0.0,
            "measured": len(measured), "of": len(members)}


def _pnl_series(members: list[dict]) -> list[list]:
    """Combined P&L in A$ per day: each book's last mark of the day minus its
    capital, carried forward; zero before the book started."""
    per_book = [{d: v - b["initial"] for d, v in _daily(b["_marks"])} for b in members]
    days = sorted(set().union(*per_book)) if per_book else []
    last = [0.0] * len(per_book)
    out = []
    for d in days:
        for i, daily in enumerate(per_book):
            if d in daily:
                last[i] = daily[d]
        out.append([d, round(sum(last), 2)])
    return out


def _findings(books: list[dict], now: datetime) -> list[dict]:
    keys = {(b["kind"], b["account"]): b["key"] for b in books}
    today = now.astimezone(timezone.utc).replace(tzinfo=None)
    out = []
    for book_id, found in verify.verify_all(today=today).items():
        kind, account = book_id.split(":", 1)
        for f in found:
            out.append({"book": keys.get((kind, account), account.upper()),
                        "code": f.code, "level": f.level,
                        "subject": (f.detail or {}).get("subject"),
                        "message": f.message})
    return out


def attention(books: list[dict], findings: list[dict], jobs: list[dict]) -> dict:
    act, look, known, notes = [], [], [], []
    for b in books:
        if b["halted"]:
            act.append({"level": "act", "book": b["key"], "url": None,
                        "text": f"{b['name']} is halted by its safety breaker"})
        elif b["near_breaker"]:
            look.append({"level": "look", "book": b["key"], "url": None,
                         "text": (f"{b['name']} is {abs(b['off_peak']):.1%} below its "
                                  f"peak — its safety breaker trips at "
                                  f"{b['breaker']:.0%}")})
    for f in findings:
        item = {"book": f["book"], "text": f["message"], "url": None}
        if f["level"] == "ERROR":
            act.append({"level": "act", **item})
        elif f["level"] == "WARN":
            (look if f["new"] else known).append(
                {"level": "look" if f["new"] else "known", **item})
        else:
            notes.append({"level": "note", **item})
    for j in jobs:
        if j["state"] in ("failed", "missed"):
            act.append({"level": "act", "book": None, "text": j["summary"],
                        "url": j.get("url")})
    if not books and not act:
        return {"verdict": "nodata", "jobs_unknown": any(j["state"] == "unknown" for j in jobs),
                "items": [{"level": "look", "book": None, "url": None,
                           "text": "No book data yet — connect to the internet and refresh (⌘R)"}],
                "known": known, "notes": notes}
    verdict = "act" if act else "look" if look else "clear"
    return {"verdict": verdict,
            "jobs_unknown": any(j["state"] == "unknown" for j in jobs),
            "items": act + look, "known": known, "notes": notes}


def build_home(*, jobs: list[dict], seen_path, session_start: str, first_run: bool,
               last_opened: str | None, now: datetime, snapshot: dict) -> dict:
    ov = overview.build_overview()
    cards = {c["key"]: c for c in ov["accounts"]}
    books = []
    for entry in registry.discover_accounts():
        state = overview._load(entry)
        card = cards.get(entry["key"])
        if state is not None and card is not None:
            books.append(_book(entry, state, card, now))
    groups = []
    for g in ov["groups"]:
        members = [b for b in books if b["group"] == g["name"]]
        groups.append({"name": g["name"], "aum": g["aum"], "initial": g["initial"],
                       "books": len(members),
                       "periods": {p: _aggregate(members, p) for p in PERIODS},
                       "series": _pnl_series(members)})
    findings = seen.classify(_findings(books, now), path=seen_path,
                             session_start=session_start, first_run=first_run, now=now)
    att = attention(books, findings, jobs)
    for b in books:
        del b["_marks"]
    return {"v": 1, "generated_at": now.isoformat(), "snapshot": snapshot,
            "last_opened": last_opened, "verdict": att["verdict"],
            "jobs_unknown": att["jobs_unknown"], "attention": att["items"],
            "known": att["known"], "notes": att["notes"],
            "groups": groups, "books": books, "jobs": jobs}
