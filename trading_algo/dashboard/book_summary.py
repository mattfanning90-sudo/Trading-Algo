"""One book's Overview answer, built from BookInputs by fixed rules — no I/O.
Screen order (docs/specs/book-screens.md): status · result · main problem ·
summary · tiles · panel · chart · months · last closed positions."""
from __future__ import annotations

from datetime import datetime

from .. import metrics
from . import book_rules as R
from . import glossary
from .textfmt import day, direction, money, money_move, pct, points


def status(inp) -> dict:
    if inp.book.get("halted"):
        return {"level": "act", "shape": "■", "word": "Halted by its safety breaker"}
    if any(f.level == "ERROR" for f in inp.findings):
        return {"level": "act", "shape": "■", "word": "Audit problem"}
    if inp.book.get("near_breaker"):
        return {"level": "look", "shape": "▲", "word": "Near its safety breaker"}
    fresh = inp.book.get("freshness")
    if fresh != "fresh":
        return {"level": "look", "shape": "▲", "word": f"Data {fresh}"}
    return {"level": "clear", "shape": "●", "word": "Running normally"}


def _guard(inp, name: str, fn, column: str) -> list[dict]:
    try:
        return fn(inp)
    except Exception as exc:            # one rule's failure is one line, not a blank screen
        inp.errors[name] = repr(exc)
        return [R.item("couldnt-measure", column, f"Couldn't measure {name} right now",
                       source="—")]


def _sentence(it: dict, prefix: str = "") -> str:
    text = f"{prefix}{it['text']}."
    cav = it.get("caveat")
    if cav:
        first = cav.split(". ")[0].rstrip(".")
        text += f" {first}."
    return text


def _tiles(inp, m) -> list[dict]:
    eq, init = float(inp.card["equity"]), float(inp.card["initial"])
    chg = eq - init
    d = R.as_of(inp)
    since = inp.card.get("since")
    breaker = inp.card.get("breaker")
    tiles = [
        {"label": "Worth", "value": money(eq), "dir": None,
         "caption": f"of {money(init)} put in" + (f", as of {day(d)}" if d else "")},
        {"label": "Since start", "value": money_move(chg), "dir": direction(chg),
         "caption": f"{pct(chg / init, 2)} since {R._on(since)}" if since and init else "—"},
    ]
    below = {"label": "Below its best", "value": pct(float(inp.card.get("off_peak") or 0.0)),
             "dir": "down" if (inp.card.get("off_peak") or 0) < 0 else None,
             "caption": (f"the safety breaker pauses it at {pct(-breaker, 0)}"
                         if breaker is not None else "no safety breaker on this book")}
    costs = float((inp.costs or {}).get("total") or 0.0)
    if inp.kind == "equity":
        if m is not None and m.get("too_short"):
            vs = {"label": "vs the market", "value": "—", "dir": None,
                  "caption": "too early to compare with the market (needs about a month of values)"}
        elif m is not None:
            vs = {"label": "vs the market",
                  "value": f"{'▲' if m['gap'] > 0 else '▼'} {points(m['gap'])} {'ahead' if m['gap'] > 0 else 'behind'}",
                  "dir": direction(m["gap"]),
                  "caption": (f"market {pct(m['bench_ret'])}; "
                              f"{'beyond' if m['beyond'] else 'within'} its normal ups and downs; "
                              "leaves out dividends")}
        elif R.long_short(inp):
            vs = {"label": "vs the market", "value": "—", "dir": None,
                  "caption": "not compared: it aims to make money whichever way markets move"}
        else:
            vs = {"label": "vs the market", "value": "—", "dir": None,
                  "caption": R.MARKET_TEXT.get(inp.market, R.MARKET_TEXT["n/a"])}
        share = R.invested_share(inp)
        if R.long_short(inp):
            inv = {"label": "Cash", "value": pct(1 - share, 0, signed=False) if share is not None else "—",
                   "dir": None, "caption": "includes the proceeds of short sales"}
        else:
            inv = {"label": "Invested", "value": pct(share, 0, signed=False) if share is not None else "—",
                   "dir": None, "caption": "the rest is in cash; Positions says why"}
        cost = {"label": "Costs so far", "value": money(costs), "dir": None,
                "caption": f"{(inp.costs or {}).get('includes', 'costs')}; slippage is inside the fill prices"}
        return tiles + [vs, below, cost, inv]
    pos, _, _, wins, _ = R.closed_stats(inp)
    if pos:
        lo, hi = metrics.wilson_interval(wins, len(pos))
        win = {"label": "Win rate", "value": pct(wins / len(pos), 0, signed=False), "dir": None,
               "caption": (f"{wins} of {len(pos)} closed positions; likely "
                           f"{pct(lo, 0, signed=False)} to {pct(hi, 0, signed=False)}")}
    else:
        win = {"label": "Win rate", "value": "—", "dir": None, "caption": "No closed positions yet"}
    turnover = float((inp.fx_book or {}).get("turnover") or 0.0)
    return tiles + [
        below, win,
        {"label": "Costs so far", "value": money(costs), "dir": None,
         "caption": "the spread paid on trades; financing is separate"},
        {"label": "Turnover", "value": f"{turnover:.0f}×", "dir": None,
         "caption": f"traded {turnover:.0f}× its capital since {R._on(since)}" if since else "—"},
    ]


def _chart(inp, m) -> dict:
    vals = R.daily_values(inp)
    init = float(inp.card["initial"])
    value = [[d.strftime("%Y-%m-%d"), round(float(v) - init, 2)] for d, v in vals.items()]
    dd = metrics.drawdown(vals) if len(vals) else vals
    below = [[d.strftime("%Y-%m-%d"), round(float(x), 6)] for d, x in dd.items()]
    bench = None
    if m is not None and not m.get("too_short") and inp.live:
        last = R.as_of(inp) or ""
        bench = [[r["date"], round(float(r["value"]) - init, 2)]
                 for r in inp.live.get("benchmark_curve") or []
                 if r.get("value") is not None and r["date"] <= last]
    value_desc = below_desc = "Not enough history yet."
    if len(value) >= 2:
        lo = min(value, key=lambda p: p[1])
        hi = max(value, key=lambda p: p[1])
        value_desc = (f"Profit and loss from {day(value[0][0])} to {day(value[-1][0])}: lowest "
                      f"{money_move(lo[1])} on {day(lo[0])}, highest {money_move(hi[1])} on "
                      f"{day(hi[0])}, now {money_move(value[-1][1])}.")
        deep = min(below, key=lambda p: p[1])
        below_desc = (f"Fall from the highest saved value: deepest {pct(deep[1])} on "
                      f"{day(deep[0])}; now {pct(below[-1][1])}.")
    return {"value": value, "below_best": below, "benchmark": bench,
            "value_desc": value_desc, "below_desc": below_desc}


def _months(inp) -> list[dict]:
    out = []
    for month, r in metrics.monthly_returns(R.daily_values(inp)).items():
        label = datetime.strptime(f"{month}-01", "%Y-%m-%d").strftime("%b %Y")
        out.append({"month": month, "label": label, "text": pct(float(r)),
                    "dir": direction(float(r))})
    return out


def _closed(inp) -> list[dict]:
    rows = sorted((p for p in inp.positions_closed if p.get("net") is not None),
                  key=lambda p: str(p["closed"]))[-5:]
    out = []
    for p in reversed(rows):
        sym = p["symbol"][1] if isinstance(p["symbol"], (tuple, list)) else p["symbol"]
        if inp.kind == "fx":
            sym = f"{sym} {p['side'].lower()}"
        held = (datetime.strptime(str(p["closed"])[:10], "%Y-%m-%d")
                - datetime.strptime(str(p["opened"])[:10], "%Y-%m-%d")).days
        out.append({"date": day(p["closed"]), "symbol": sym, "held": f"{held} days",
                    "net": money_move(p["net"]), "dir": direction(p["net"])})
    return out


def _number(items: list[dict]) -> None:
    seen: dict[str, int] = {}
    for it in items:
        n = seen.get(it["id"], 0)
        seen[it["id"]] = n + 1
        it["uid"] = f"{it['id']}#{n}"


def summarise(inp) -> dict:
    res = _guard(inp, "the result", lambda i: [R.result(i)], "result")[0]
    att = _guard(inp, "warnings", R.attention, "attention")
    right = _guard(inp, "what's going right", R.going_right, "right")
    back = _guard(inp, "what's holding it back", R.holding_back, "wrong")
    model = _guard(inp, "the model", R.model, "model")
    wrong = sorted(att + back, key=lambda i: (i["rank"] is None, i["rank"] or 0))
    _number([res] + wrong + right + model)
    ranked = [i for i in wrong if i["rank"] is not None]
    if ranked:
        main = dict(ranked[0])
    else:
        main = R.item("no-problem", "main", "No standout problem", source="the rules below")
        main["uid"] = "no-problem#0"
    summary = [{"text": _sentence(res), "item_id": res["uid"]},
               {"text": _sentence(main, "Main problem: " if ranked else ""), "item_id": main["uid"]}]
    if ranked and right:
        summary.append({"text": _sentence(right[0]), "item_id": right[0]["uid"]})
    mline = (next((i for i in model if i["id"] == "luck-test"), None)
             or next((i for i in model if i["id"] == "backtest"), None))
    if mline:
        summary.append({"text": _sentence(mline), "item_id": mline["uid"]})
    m = None
    try:
        m = R.market(inp)
    except Exception as exc:
        inp.errors["the market comparison"] = repr(exc)
    tiles = _tiles(inp, m)
    order = [(res, "text"), (main, "text")] + [(s, "text") for s in summary]
    order += [(t, "caption") for t in tiles]
    for it in right + wrong + model:
        order += [(it, "text"), (it, "caveat")]
    for (obj, key), text in zip(order, glossary.gloss([o[k] or "" for o, k in order])):
        if obj[key]:
            obj[key] = text
    return {
        "v": 1, "key": inp.entry["key"], "name": inp.card["name"], "kind": inp.kind,
        "as_of": R.as_of(inp), "status": status(inp), "result": res,
        "main_problem": main, "attention": [i for i in wrong if i["column"] == "attention"],
        "summary": summary, "tiles": tiles,
        "panel": {"right": right, "wrong": wrong, "model": model},
        "right_empty": None if right else "Nothing clearly going right yet.",
        "chart": _chart(inp, m), "months": _months(inp), "closed": _closed(inp),
        "market": inp.market, "errors": sorted(inp.errors),
    }
