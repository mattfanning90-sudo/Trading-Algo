"""The "Is it working?" rules (docs/specs/book-screens.md). Each rule reads a
BookInputs and returns items; nothing here reads a file, the network or the
clock. Thresholds are the spec's, named once here."""
from __future__ import annotations

import math
from datetime import datetime, timezone

import pandas as pd

from .. import config as cfg
from .. import fx, metrics, pnl
from ..forex import fx_config
from ..regions import get_region
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
MIN_MARKET_DAYS = 20          # about a month of trading days before any market verdict

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
    "never-traded": "{subject} is funded but has never traded",
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


def _on(stamp) -> str:
    """A saved date as display text, or 'an unknown date' — never a crash."""
    return day(stamp) if stamp else "an unknown date"


def _src_saved(inp) -> str:
    d = as_of(inp)
    return f"saved book, {day(d)}" if d else "saved book"


def _src_audit(inp) -> str:
    return f"live audit, {day(inp.now)}"


def plain_finding(f) -> str:
    subject = (f.detail or {}).get("subject") or "One part of the book"
    tmpl = PLAIN.get(f.code)
    return tmpl.format(subject=subject) if tmpl else f"Audit finding: {f.message}"


def _num(v: float) -> str:
    """A ratio to 2 dp with the typographic minus, never a bare hyphen."""
    return f"{v:.2f}".replace("-", "\u2212")


def market(inp) -> dict | None:
    """The book against its benchmark to the last SAVED date, and how far the
    two normally stray. None when there's nothing fair to compare; with fewer
    than MIN_MARKET_DAYS aligned days it returns {"too_short": True, ...} and
    no verdict."""
    if inp.kind != "equity" or long_short(inp) or inp.market != "ready" or not inp.live:
        return None
    bench = {r["date"]: float(r["value"]) for r in inp.live.get("benchmark_curve") or []
             if r.get("value") is not None and math.isfinite(float(r["value"]))}
    vals = daily_values(inp)
    days = [d for d in vals.index if d.strftime("%Y-%m-%d") in bench]
    if len(days) < MIN_MARKET_DAYS:
        return {"too_short": True, "days": len(days)}
    s = vals.loc[days]
    b = pd.Series([bench[d.strftime("%Y-%m-%d")] for d in days], index=days, dtype=float)
    initial = float(inp.card["initial"])
    gap = (s.iloc[-1] / initial - 1.0) - (b.iloc[-1] / initial - 1.0)
    stats = metrics.benchmark_stats(s.pct_change().dropna(), b.pct_change().dropna())
    te = stats.get("TrackingError") or 0.0
    spread = metrics.expected_gap(te if math.isfinite(te) else 0.0,
                                  metrics.years_between(days[0], days[-1]))
    return {"gap": float(gap), "spread": spread, "bench_ret": float(b.iloc[-1] / initial - 1.0),
            "beyond": abs(gap) > GAP_SPREADS * spread, "too_short": False, "last": days[-1].strftime("%Y-%m-%d")}


def result(inp) -> dict:
    marks = inp.book.get("_marks") or []
    since = inp.card.get("since")
    if len(marks) < 2:
        text = f"Started {_on(since)}; too new to judge" if since else "No saved values yet; too new to judge"
        return item("result", "result", text, source=_src_saved(inp))
    eq, init = float(inp.card["equity"]), float(inp.card["initial"])
    chg = eq - init
    word = "Up" if chg > 0 else "Down" if chg < 0 else "Flat"
    text = (f"{word} {money(abs(chg))} ({pct(chg / init, 2)}) since {_on(since)}, "
            f"as of {_on(as_of(inp))}")
    caveat = None
    m = market(inp)
    if m is not None and m["too_short"]:
        text += "; too early to compare with the market (needs about a month of values)"
    elif m is not None:
        side = "ahead of" if m["gap"] > 0 else "behind"
        text += (f"; {points(m['gap'])} {side} the market, "
                 + ("more than its normal ups and downs" if m["beyond"]
                    else "within its normal ups and downs"))
        caveat = DIVIDENDS
    elif inp.kind == "fx" or long_short(inp):
        text += "; it aims to make money whichever way markets move, so it isn't compared with a market index"
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
                        f"{pct(abs(inp.card['off_peak']), signed=False)} below its best; the safety breaker "
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
    if m and not m["too_short"] and m["beyond"] and m["gap"] > 0:
        out.append(item("ahead-of-market", "right",
                        f"Ahead of the market by {points(m['gap'])} since {_on(inp.card.get('since'))}, "
                        "more than its normal ups and downs", source=_src_saved(inp),
                        caveat=DIVIDENDS))
    pos, won, lost, wins, dates = closed_stats(inp)
    if pos and won - lost > 0:
        pf = f", a profit factor of {won / lost:.1f}," if lost else ""
        out.append(item("winners-pay", "right",
                        f"Closed positions made more than they lost: {money(won)} won against "
                        f"{money(lost)} lost{pf} over {len(pos)} positions",
                        source=f"trade record, {day(inp.now)}",
                        caveat=_sample_caveat(len(pos), dates, inp.excluded_forced)))
    return out


REGIME_CAVEAT = ("Holding cash below the trend gives up some growth but limits the worst "
                 "falls (system review, section 15).")
SHORT_WHY = {
    "paused": "paused by the safety breaker",
    "data": "kept in cash because its price data failed checks",
    "fx": "kept in cash with no exchange rate",
    "below-min": "too small to trade",
    "regime-off": "waiting, by design, while its market is below its long-term trend",
    "no-eligible": "holding nothing, as nothing passed the tests",
    "insufficient": "holding nothing, as it couldn't form both sides",
    "staged": "opening positions at the next close",
    "pinned": "waiting for next month's rebalance",
    "empty": "holding nothing",
    "invested": "sized down by the volatility target and whole-share rounding",
}


def _base_per_local(inp, ccy: str | None) -> float:
    base = inp.state.get("base_currency", cfg.BASE_CURRENCY)
    return fx.base_per_local(inp.state.get("fx_snapshot") or {}, ccy, base) or 0.0


def _region_name(key: str) -> str:
    try:
        return get_region(key).name
    except Exception:          # a key the registry doesn't know (tests, a retired region)
        return key


def staged_buy(sleeve: dict) -> dict:
    """The weights of a staged target that holds something, else {}. Same test
    as verify._staged_buy: paper_trade decides at one close and fills at the
    next, so a flat sleeve with these weights is about to invest."""
    return dict((sleeve.get("pending_target") or {}).get("weights") or {})


def invested_share(inp) -> float | None:
    if inp.kind != "equity":
        return None
    eq = float(inp.card.get("equity") or 0.0)
    if eq <= 0:
        return None
    cash = sum(float(s.get("cash") or 0.0) * _base_per_local(inp, s.get("currency"))
               for s in (inp.state.get("sleeves") or {}).values())
    return 1.0 - cash / eq


def _last_fill(inp, key: str) -> str | None:
    dates = [str(t["date"]) for t in inp.state.get("trades") or []
             if t.get("region") == key and pnl.is_fill(t)]
    return max(dates) if dates else None


def sleeve_reason(inp, key: str, sleeve: dict) -> tuple[str, str, str | None]:
    status = (sleeve.get("last_status") or {}).get("status") or ""
    reason = sleeve.get("last_flat_reason")
    holds = bool(sleeve.get("positions"))
    decided_this_month = sleeve.get("last_rebalance_month") == inp.now.strftime("%Y-%m")
    nxt = " Next decision: the start of next month." if (not holds and decided_this_month) else ""
    if inp.state.get("risk_halted"):
        left = inp.state.get("halt_cooldown")
        return "paused", f"Paused by the safety breaker{f': {left} trading days left' if left else ''}.", "act"
    if status == "cash:stale-data" or reason == "data-quality":
        return "data", "Kept in cash: its price data failed the quality checks." + nxt, "act"
    if status == "cash:fx-unavailable":
        return "fx", "Kept in cash: no exchange rate was available." + nxt, "act"
    if status == "cash:below-min":
        return "below-min", "Too small to trade yet: below the minimum size the strategy can hold." + nxt, None
    if not holds and reason == "regime-off":
        last = _last_fill(inp, key)
        since = "since the book began" if last is None else f"since {day(last)}"
        when = sleeve.get("last_rebalance_date")
        at = f"at the last decision ({day(when)}) " if when else ""
        return ("regime-off", f"Waiting in cash {since}: {at}its market was below its "
                f"long-term trend.{nxt}", None)
    if not holds and reason == "no-eligible-names":
        return "no-eligible", "Holding nothing: no share passed the momentum and trend tests at the last decision." + nxt, None
    if not holds and reason == "insufficient-names":
        return "insufficient", "Holding nothing: it couldn't form both the long and the short side." + nxt, None
    staged = staged_buy(sleeve)
    if not holds and staged:
        n, when = len(staged), (sleeve.get("pending_target") or {}).get("date")
        return ("staged", f"Opening {n} position{'s' if n != 1 else ''} at the next close "
                f"(decided {_on(when)}).", None)
    if not holds and decided_this_month:
        return "pinned", "Holding nothing until next month's rebalance.", None
    if not holds:
        return "empty", "Holding nothing.", None
    n = len(sleeve["positions"])
    return ("invested", f"Investing in {n} holding{'s' if n != 1 else ''}; part of its money "
            "stays in cash because positions are sized to a volatility target and whole "
            "shares round down.", None)


def costs_flip(inp) -> list[dict]:
    costs = float((inp.costs or {}).get("total") or 0.0)
    if costs <= 0:
        return []
    if inp.kind == "fx" and inp.fx_book:
        res = float(inp.fx_book["net_pnl"])
    else:
        res = float(inp.card["equity"]) - float(inp.card["initial"])
    before = res + costs
    res_c = round(res, 2)
    gain_to_loss = before > 0 > res_c
    if res_c > 0:
        fires = costs > COSTS_HALF * before
    elif res_c == 0:
        fires = True
    else:
        fires = gain_to_loss or costs > COSTS_HALF * (-res)
    if not fires:
        return []
    if gain_to_loss:
        text = f"Costs ({money(costs)}) turned a {money(before)} gain into a {money(-res)} loss"
    elif res_c > 0:
        text = (f"Costs ({money(costs)}) took {pct(costs / before, 0, signed=False)} "
                f"of the {money(before)} made before costs")
    elif res_c == 0:
        text = f"Costs ({money(costs)}) took everything the book made before costs"
    else:
        text = (f"Costs ({money(costs)}) are {pct(costs / -res, 0, signed=False)} of the "
                f"{money(-res)} loss; before costs the book was {money(before, signed=True)}")
    backlog = None
    if inp.kind == "equity":
        caveat = f"Costs here are {inp.costs['includes']}; slippage is already inside the fill prices."
        gbp = float((inp.costs.get("by_ccy") or {}).get("GBP") or 0.0)
        if gbp > FTSE_SHARE * costs:
            text += (f", mostly from UK trading: {money(gbp)}, of which "
                     f"{money(float((inp.costs.get('stamp') or {}).get('GBP') or 0.0))} was stamp duty")
            backlog = "F25"
    else:
        caveat = f"Costs here are {inp.costs['includes']}; financing is counted separately."
        resid = float((inp.fx_book or {}).get("residual") or 0.0)
        if res and abs(resid) > RESIDUAL_SHARE * abs(res):
            caveat += (f" The trade-by-trade figures leave {money(resid, signed=True)} of the "
                       "result unexplained (compounding and currency translation).")
    return [item("costs-flip", "wrong", text, source=f"trade record, {day(inp.now)}",
                 caveat=caveat, backlog=backlog)]


def holds_too_short(inp) -> list[dict]:
    f = next((f for f in inp.findings if f.code == "hold-shorter-than-signal"), None)
    if f is None:
        return []
    d = f.detail or {}
    tail = f"; {d['cut_short']} of {d['round_trips']} positions closed before a quarter of it"
    if str(inp.state.get("bar") or "1d") == "1d":
        text = (f"Sells early: half its positions close within {d['median_bars']:.0f} days, "
                f"but its signal horizon is {d['horizon_bars']} trading days "
                f"(about {d['horizon_bars'] / 5:.0f} weeks){tail}")
    else:
        text = (f"Sells early: half its positions close within {d['median_bars']:.0f} bars, "
                f"but its signal horizon is {d['horizon_bars']} bars{tail}")
    return [item("holds-too-short", "wrong", text, source=_src_audit(inp),
                 caveat="Holding longer has not been shown to earn more (tracked as fix F20).",
                 backlog="F20")]


def loses_more_often(inp) -> list[dict]:
    pos, _, _, wins, _ = closed_stats(inp)
    if not pos:
        return []
    lo, hi = metrics.wilson_interval(wins, len(pos))
    if hi >= 0.5:
        return []
    caveat = "Counts whole positions after costs."
    if inp.excluded_forced:
        caveat += f" Leaves out {inp.excluded_forced} closed by a since-fixed bug."
    return [item("loses-more-often", "wrong",
                 f"Loses more often than it wins: {wins} of {len(pos)} closed positions made "
                 f"money ({pct(wins / len(pos), 0, signed=False)}); the likely range is "
                 f"{pct(lo, 0, signed=False)} to {pct(hi, 0, signed=False)}",
                 source=f"trade record, {day(inp.now)}", caveat=caveat)]


def behind_market(inp) -> list[dict]:
    m = market(inp)
    if not m or m.get("too_short") or not m["beyond"] or m["gap"] >= 0:
        return []
    return [item("behind-market", "wrong",
                 f"Behind the market by {points(m['gap'])} since {_on(inp.card.get('since'))}, "
                 "more than its normal ups and downs", source=_src_saved(inp), caveat=DIVIDENDS)]


def mostly_idle(inp) -> list[dict]:
    if inp.kind != "equity" or long_short(inp):
        return []
    share = invested_share(inp)
    eq = float(inp.card.get("equity") or 0.0)
    soon = sum(float(s.get("cash") or 0.0) * _base_per_local(inp, s.get("currency"))
               * min(1.0, sum(max(float(w), 0.0) for w in staged_buy(s).values()))
               for s in (inp.state.get("sleeves") or {}).values() if not s.get("positions"))
    if share is None or share + soon / eq >= IDLE_BELOW:   # cash about to be invested isn't idle
        return []
    parts, sized_down = [], False
    for key, sleeve in (inp.state.get("sleeves") or {}).items():
        cash_share = float(sleeve.get("cash") or 0.0) * _base_per_local(inp, sleeve.get("currency")) / eq
        if cash_share < 0.01:
            continue
        code = sleeve_reason(inp, key, sleeve)[0]
        sized_down = sized_down or code == "invested"
        parts.append(f"{_region_name(key)}: {pct(cash_share, 0, signed=False)}, {SHORT_WHY[code]}")
    text = f"Mostly idle: {pct(1 - share, 0, signed=False)} of the book is in cash; " + "; ".join(parts)
    return [item("mostly-idle", "wrong", text, source=_src_saved(inp),
                 backlog="F23" if sized_down else None,
                 caveat=("Only the sized-down part is tracked as a fix (F23 volatility target, "
                         "F24 whole-share rounding).") if sized_down else None)]


def frozen_prices(inp) -> list[dict]:
    f = next((f for f in inp.findings if f.code == "dead-price-turnover"), None)
    recent = int(((f.detail or {}).get("recent") or 0)) if f else 0
    if recent < FROZEN_RECENT:
        return []
    return [item("frozen-prices", "wrong",
                 f"{recent} position changes in the last 30 days were made at a frozen price, "
                 "paying the spread for nothing", source=_src_audit(inp), backlog="F21")]


def holding_back(inp) -> list[dict]:
    out: list[dict] = []
    for rule in (costs_flip, holds_too_short, loses_more_often, behind_market,
                 mostly_idle, frozen_prices):
        out.extend(rule(inp))
    fired = {i["id"] for i in out}
    for f in inp.findings:
        if f.level == "WARN" and COVERED_BY.get(f.code) not in fired:
            out.append(item("audit-warn", "wrong", plain_finding(f), source=_src_audit(inp)))
    return out


_N = fx_config.REVIEW_NOTICE
_i, _j = _N.find("One known"), _N.find("Paper money only.")
FX_ARTEFACTS = _N[_i:_j].strip() if _i >= 0 and _j > _i else ""


def _days_old(stamp, now: datetime) -> int | None:
    try:
        t = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (now - t).days


def _settings_differ(inp, bt: dict) -> bool:
    mine = {k: round(float(v), 2) for k, v in (inp.state.get("allocations") or {}).items()}
    theirs = {k: round(float(v), 2) for k, v in (bt.get("allocations") or {}).items()}
    return mine != theirs or bool(inp.state.get("param_overrides")) or bool(inp.entry.get("micro"))


def backtest_line(inp) -> list[dict]:
    bt = inp.backtest or {}
    if not bt.get("available"):
        return [item("backtest", "model", "No saved backtest for this book yet",
                     source="backtest store")]
    gen = bt.get("generated_at")
    src = f"saved backtest, {_on(gen)}" if gen else "saved backtest"
    if bt.get("synthetic"):
        return [item("backtest", "model", "The saved backtest ran on made-up test data, so it "
                     "says nothing about real performance", source=src)]
    m = bt.get("metrics") or {}
    if m.get("cagr") is None or m.get("sharpe") is None:
        return [item("backtest", "model", "Couldn't read the saved backtest", source=src)]
    text = (f"Backtest, {str(bt.get('start'))[:4]}\u2013{str(bt.get('end'))[:4]}: "
            f"{pct(m['cagr'])} a year, Sharpe {_num(m['sharpe'])}")
    caveats: list[str] = []
    age = _days_old(gen, inp.now) if gen else None
    saved = (f"Saved {_on(gen)}" + (f", {age} days ago; may not reflect changes since."
                                    if age is not None and age > BACKTEST_STALE_DAYS else ".")
             if gen else None)
    if inp.kind == "equity":
        bm = bt.get("benchmark_metrics") or {}
        if bm.get("cagr") is not None and bm.get("sharpe") is not None:
            text += f", against the benchmark at {pct(bm['cagr'])} and {_num(bm['sharpe'])}"
        if _settings_differ(inp, bt):
            caveats.append("This is the standard strategy, not this book's settings.")
        if saved:
            caveats.append(saved)
        caveats.append("It is survivorship-biased, so an upper bound; no sleeve passes a "
                       "standalone Deflated-Sharpe test.")
        caveats.append("The benchmark leaves out dividends, which flatters the strategy.")
    else:
        if FX_ARTEFACTS:
            caveats.append(FX_ARTEFACTS)
        if saved:
            caveats.append(saved)
    return [item("backtest", "model", text, source=src, as_of=str(gen)[:10] if gen else None,
                 caveat=" ".join(caveats) or None)]


def luck_test(inp) -> list[dict]:
    r = inp.permtest
    if inp.kind != "fx" or not r or r.get("p_value") is None:
        return []
    p = float(r["p_value"])
    if p >= 0.67:
        how = "most of the time"
    elif p >= 0.1:
        how = f"about 1 in {max(1, round(1 / p))} times"
    else:
        how = "only occasionally" if p >= 0.05 else "rarely"
    if p >= 0.05:
        text = (f"Re-run on shuffled, patternless prices, the search for better agent settings "
                f"finds results this good {how}, so its best finds look like luck; "
                f"the p-value is {p:.2f}")
    else:
        text = (f"Re-run on shuffled, patternless prices, the search for better agent settings "
                f"{how} finds results this good, so its best finds are unlikely to be luck "
                f"alone; the p-value is {p:.2f}")
    used = "none" if inp.roster == 0 else (str(inp.roster) if inp.roster is not None else "an unknown number")
    w = r.get("window") or {}
    span = f" of {_on(w['start'])}\u2013{_on(w['end'])} data" if w.get("start") and w.get("end") else ""
    created = r.get("created")
    caveat = (f"This tests the settings search, not this book's trades: the book uses {used} "
              f"of the search's finds. Null: {r.get('n_permutations')} bar shuffles{span}; "
              f"run {_on(created)}.")
    age = _days_old(created, inp.now) if created else None
    if age is not None and age > LUCK_STALE_DAYS:
        caveat += f" Over {LUCK_STALE_DAYS} days old."
    return [item("luck-test", "model", text,
                 source=f"permutation report, {_on(created)}" if created else "permutation report",
                 as_of=str(created)[:10] if created else None, caveat=caveat, backlog="F22")]


def not_measured(inp) -> list[dict]:
    text = ("Does the ranking pick winners? Not measured yet; it needs the decision record"
            if inp.kind == "equity" else
            "Which agents are right? Not measured yet; it needs the agent scorecard")
    return [item("not-measured", "model", text, source="not yet built")]


def model(inp) -> list[dict]:
    return backtest_line(inp) + luck_test(inp) + not_measured(inp)
