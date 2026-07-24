#!/usr/bin/env python3
"""Rebuild the animated walkthrough with FRESH real data.

Reads the committed template ``docs/explainer/how-it-works.html`` — whose one
``var DATA = {...};`` line holds every number the animation renders — refreshes
that object from live state, and writes the result to an output path
(default ``public/walkthrough.html``). Used by ``scripts/build_site.sh``.

Three independent sections are refreshed, each wrapped so a failure keeps the
value baked into the template (a Yahoo outage degrades to the last-good
snapshot, it never breaks the deploy):

* ``portfolio`` <- ``state/backtest_equity.json``  (refreshed by backtest.yml)
* ``fx``        <- ``state/fx_state_matt.json``     (refreshed hourly by *-paper.yml)
* ``regions``   <- a *bounded* live ``compute_targets`` run (each sleeve's held
  names + a small watchlist + the index), guarded by a wall-clock alarm so the
  live pull can never hang a deploy.

On a hard failure (unreadable template / no DATA marker) it exits non-zero and
the caller falls back to copying the baked template verbatim.
"""
from __future__ import annotations
import json
import math
import os
import re
import signal
import sys

# Importable no matter the cwd: running "python scripts/build_walkthrough.py"
# puts scripts/ on sys.path, not the repo root, so add the repo root explicitly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TEMPLATE = os.environ.get("WALKTHROUGH_TEMPLATE", "docs/explainer/how-it-works.html")
STATE_DIR = os.environ.get("MOMENTUM_STATE_DIR") or os.environ.get("FX_STATE_DIR") or "state"
OUT = sys.argv[1] if len(sys.argv) > 1 else "public/walkthrough.html"

# Small per-region watchlist so the live pull is bounded (held names are added
# on top). These are the also-rans / cash-sleeve names the animation can show.
WATCH = {
    "US":   ["INTU", "BSX", "MU", "AMD"],
    "FTSE": ["FLTR.L", "RMV.L", "BT-A.L"],
    "ASX":  ["MIN.AX", "LYC.AX", "EVN.AX", "IGO.AX", "RIO.AX", "BHP.AX", "FMG.AX", "PLS.AX"],
}
IDX_NAME = {"US": "S&P 500", "ASX": "ASX 200", "FTSE": "FTSE 100"}


def _num(x, n: int = 4):
    try:
        f = float(x)
        return None if (math.isnan(f) or math.isinf(f)) else round(f, n)
    except Exception:
        return None


def _load(name):
    with open(os.path.join(STATE_DIR, name)) as fh:
        return json.load(fh)


def _downsample(curve, k=180):
    m = len(curve)
    if not m:
        return []
    st = max(1, m // k)
    out = [[c[0], _num(c[1], 1)] for i, c in enumerate(curve) if i % st == 0]
    if out[-1][0] != curve[-1][0]:
        out.append([curve[-1][0], _num(curve[-1][1], 1)])
    return out


def build_portfolio():
    d = _load("backtest_equity.json")
    return {
        "start": d["start"], "end": d["end"], "initial": d["initial_capital"],
        "curve": _downsample(d["curve"]), "benchmark": _downsample(d.get("benchmark", [])),
        "metrics": {k: _num(v) for k, v in d.get("metrics", {}).items()},
        "benchmark_metrics": {k: _num(v) for k, v in d.get("benchmark_metrics", {}).items()},
        "generated_at": d.get("generated_at"), "synthetic": d.get("synthetic", False),
    }


def build_fx():
    d = _load("fx_state_matt.json")
    dec = d["decisions"]
    pairs = [{"pair": p, "w": _num(v.get("weight")), "regime": v.get("regime"),
              "agents": {k: _num(x, 3) for k, x in v.get("agents", {}).items()}}
             for p, v in dec.items()]
    pairs.sort(key=lambda x: abs(x["w"] or 0), reverse=True)
    agents = list(next(iter(dec.values()))["agents"].keys()) if dec else []
    return {"account": d.get("account", "matt"), "equity": _num(d.get("equity"), 2),
            "initial": d.get("initial_capital"), "asof": d.get("last_bar_date"),
            "agents": agents, "pairs": pairs, "n_pairs": len(pairs),
            "daily_net_pct": _num((d.get("daily") or {}).get("net_pct"), 5)}


def build_regions():
    import pandas as pd
    import trading_algo.data as data
    import trading_algo.signals as sig
    from trading_algo.regions import REGIONS

    ps = _load("paper_state_full.json")
    try:
        slm = {s["key"]: s for s in _load("backtest_equity.json").get("sleeves", [])}
    except Exception:
        slm = {}

    out = []
    for key in ("US", "ASX", "FTSE"):
        reg = REGIONS[key]
        p = reg.params
        held = ps["sleeves"][key]["positions"]
        cash = ps["sleeves"][key]["cash"]
        watch = list(dict.fromkeys(list(held.keys()) + WATCH.get(key, [])))
        prices, index_px = data.load_region(reg, "2024-01-01", tickers=watch)
        asof = prices.index[-1]
        mom = sig.momentum_score(prices, p).loc[asof]
        vol = sig.realised_vol(prices, p).loc[asof]
        trend = sig.stock_trend_ok(prices, p).loc[asof]
        regime_on = bool(sig.index_risk_on(index_px, p).loc[asof])

        def lastp(t):
            if t not in prices.columns:
                return None
            s = prices[t].dropna()
            return float(s.iloc[-1]) if len(s) else None

        names = []
        if held:
            vals = {t: (held[t] * lastp(t)) if lastp(t) else 0.0 for t in held}
            total = cash + sum(vals.values())
            for t in held:
                names.append({
                    "tk": t.replace(".L", "").replace(".AX", ""), "raw": t,
                    "mom": _num(mom.get(t)), "vol": _num(vol.get(t)),
                    "trend": bool(trend.get(t)) if t in trend.index and pd.notna(trend.get(t)) else True,
                    "weight": _num((vals[t] / total) if total else 0, 4), "selected": True})
            cash_pct = _num(cash / total * 100, 1) if total else None
            cand = [(t, mom.get(t), trend.get(t, False)) for t in prices.columns
                    if t not in held and pd.notna(mom.get(t)) and abs(float(mom.get(t))) < 2.0]
            cand.sort(key=lambda x: (bool(x[2]), float(x[1])))  # trend-fail first, then lowest mom
            for t, m, tr in cand[:2]:
                names.append({
                    "tk": t.replace(".L", "").replace(".AX", ""), "raw": t,
                    "mom": _num(m), "vol": _num(vol.get(t)), "trend": bool(tr),
                    "weight": 0.0, "selected": False})
        else:  # risk-off sleeve: show a few strong-momentum names that still go to cash
            valid = mom.dropna()
            for t in valid[valid.between(-1, 2)].sort_values(ascending=False).index[:6]:
                names.append({
                    "tk": t.replace(".AX", ""), "raw": t, "mom": _num(mom.get(t)),
                    "vol": _num(vol.get(t)), "trend": bool(trend.get(t)),
                    "weight": 0.0, "selected": False})
            cash_pct = 100.0

        names = [n for n in names if n["mom"] is not None]
        names.sort(key=lambda n: n["mom"], reverse=True)
        m = slm.get(key, {})
        out.append({
            "key": key, "currency": reg.currency, "index_name": IDX_NAME[key],
            "regime_on": regime_on, "asof": str(asof)[:10], "cash_pct": cash_pct,
            "universe_n": len(reg.universe), "n_selected": sum(1 for n in names if n["selected"]),
            "metrics": {k: _num(m.get(k)) for k in ("cagr", "ann_vol", "sharpe", "max_drawdown")},
            "names": names})
    return out


def _trim(regions):
    """Keep <=7 names per region: holdings (by weight, cap 6) + one also-ran."""
    for r in regions:
        sel = sorted((n for n in r["names"] if n["selected"]), key=lambda n: n["weight"], reverse=True)[:6]
        non = sorted((n for n in r["names"] if not n["selected"]), key=lambda n: n["mom"])[:1]
        r["names"] = (sel + non) if sel else sorted(r["names"], key=lambda n: n["mom"], reverse=True)[:6]
    return regions


def main():
    try:
        with open(TEMPLATE, encoding="utf-8") as fh:
            html = fh.read()
    except OSError as e:
        print(f"build_walkthrough: cannot read template ({e})", file=sys.stderr)
        sys.exit(1)
    marker = re.search(r"var DATA = (\{.*\});", html)
    if not marker:
        print("build_walkthrough: no DATA marker in template", file=sys.stderr)
        sys.exit(1)
    data = json.loads(marker.group(1))

    for section, fn in (("portfolio", build_portfolio), ("fx", build_fx)):
        try:
            data[section] = fn()
            print(f"build_walkthrough: refreshed {section}")
        except Exception as e:  # noqa: BLE001 - keep the baked value, never fail the build
            print(f"build_walkthrough: KEEP baked {section} ({e})", file=sys.stderr)

    def _alarm(*_a):
        raise TimeoutError("regions refresh timed out")
    try:
        signal.signal(signal.SIGALRM, _alarm)
        signal.alarm(150)
        data["regions"] = _trim(build_regions())
        print("build_walkthrough: refreshed regions (live)")
    except Exception as e:  # noqa: BLE001
        print(f"build_walkthrough: KEEP baked regions ({e})", file=sys.stderr)
    finally:
        signal.alarm(0)

    data["asof"] = (data.get("fx") or {}).get("asof") or data.get("asof") or ""

    new_line = "var DATA = " + json.dumps(data, separators=(",", ":")) + ";"
    html = html[:marker.start()] + new_line + html[marker.end():]
    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"build_walkthrough: wrote {OUT}")


if __name__ == "__main__":
    main()
