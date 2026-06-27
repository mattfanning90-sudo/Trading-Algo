"""Static candlestick + analytics dashboard for the FX paper books (GitHub Pages).

Per book: a performance card (equity vs 1/N buy-and-hold + metrics), candlesticks
with EMA overlays and BUY/SELL markers, an agent scorecard, and a trade journal
where every entry is explained in **plain English** — what we did, which agents
drove it, the evidence (each technical term defined inline), and the outcome.
Plus a "How it works" page with a flow diagram, and styled hover tooltips that
define each metric.

The plain-English explanations are generated at render time from the signals and
indicator readings stored on each trade, so improvements show up immediately and
apply to every trade (no re-seeding needed).

    python -m trading_algo.forex.dashboard --all --out-dir public
    python -m trading_algo.forex.dashboard --account matt -o matt.html
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

import math

from . import fx_data
from . import indicators as ind
from .agents import AgentPool
from .fx_book import list_accounts, load_state
from .fx_config import ANNUALIZATION, FX_RISK_FREE, profile
from .fx_strategy import target_weights_history
from .pairs import get_pair
from .validation import _norm_ppf, probabilistic_sharpe_ratio

_LWC = "https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"
_MERMAID = "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"
_OUTCOME_BARS = 10

# One-line beginner role for each agent (full version, used in hover tooltips).
_AGENT_ROLES = {
    "trend": "trend-follower — compares a short-term vs long-term average to ride established moves",
    "breakout": "breakout — buys/sells when price pushes past its recent high/low range",
    "meanrev": "mean-reversion — bets an over-stretched price snaps back to its average",
    "momentum": "momentum — assumes the recent direction tends to persist",
    "carry": "carry — leans toward the currency that pays more interest",
    "neural": "deep-learning model — a neural net trained on past patterns",
}

# Short clauses used inside the plain-English sentences.
_AGENT_SHORT = {
    "trend": "rides established up/down moves",
    "breakout": "acts when price breaks its recent range",
    "meanrev": "fades over-stretched prices back to average",
    "momentum": "recent direction tends to persist",
    "carry": "favours the higher-interest currency",
    "neural": "a neural net trained on past patterns",
}


def _agent_phrase(name):
    return f"the <b>{name}</b> agent ({_AGENT_SHORT.get(name, '')})"

GLOSSARY = {
    "Sharpe": "Return per unit of risk (annual return above cash ÷ how much it swings). ~1 good, >2 excellent. The headline 'is this any good?' score.",
    "Volatility": "How much the equity swings, per year. Higher = bumpier ride and bigger possible losses.",
    "Max drawdown": "The worst peak-to-trough fall — your maximum pain if you'd bought at the top.",
    "Win rate": "Share of days the book finished up. (High win rate ≠ profit — a few big losses can still sink it.)",
    "Benchmark": "1/N buy-and-hold: just hold every instrument equally, no model. If the algo can't beat this, the model isn't adding value.",
    "Agent scorecard": "How each agent's signal alone would have done over this window (no leverage). 'ensemble' is the blend; 'buy&hold' is passive. Shows which edge is working — but one good window is NOT proof.",
    "Regime": "Whether the market is trending (ADX high) or ranging/choppy (ADX low). Trend agents shine in trends; mean-reversion shines in ranges.",
    "Tilt": "The ensemble's net conviction for a pair, from -1 (max short) to +1 (max long).",
    "Gross leverage": "Total size of all positions vs your capital. 3x = holding $15k of positions on $5k.",
    "Outcome": "The signed price move over the next %d days after the trade — did the call actually work? (⏳ until enough days pass.)" % _OUTCOME_BARS,
    "Mid price": "The execution reference price — the midpoint between bid and ask at the time of the trade.",
    "Bid": "The price you can SELL at (what a buyer will pay). Always a touch below mid.",
    "Ask": "The price you can BUY at (what a seller will accept). Always a touch above mid. (Sometimes called the 'offer'.)",
    "Spread": "Ask − bid, the dealer's cut on every round trip, here in basis points (1 bp = 0.01%). You pay half of it each time you enter or exit — the main cost of trading.",
    "Notional": "The dollar size of the position change: |Δweight| × equity. A 0.2 weight change on a $5,000 book = $1,000 traded.",
    "Transaction cost": "What this trade actually cost you in spread: half the spread × the notional traded. Always charged — there is no free trade.",
    "P&L since": "Mark-to-market of this weight change since it was made: Δweight × the price move since × equity. It's this trade's running contribution to the book — NOT lot-by-lot realised profit (this is a weight-based book).",
    "Delta weight": "How much the position in this pair changed on this trade, as a fraction of equity (+ = bought/added long, − = sold/added short).",
    "PSR": "Probabilistic Sharpe Ratio — the probability the TRUE Sharpe is above zero given how few days we have and how lumpy the returns are (accounts for skew/fat tails). A short run of gains has a low PSR: it's the honest 'is this skill or luck?' gauge. >95% = confident.",
    "Significance": "How long until we could tell this edge from luck. The Minimum Track Record Length: the number of trading days needed for PSR to clear 95%. Until then, treat the P&L as noise.",
    "Cost drag": "Total spread paid as a share of gross profit — how big a bite trading costs take out of what the strategy made before costs. Lower is better; over 100% means costs exceeded the gross gain.",
    "Cost wedge": "Gross equity (before spread) vs net equity (after). The gap between the two lines IS the cumulative trading cost — watch it widen with turnover.",
    "Drawdown curve": "How far below the previous peak the book is, every day (the 'underwater' plot). Flat at 0 = at a new high; deep dips = the painful stretches.",
    "Net exposure": "Your true bet per CURRENCY once the pairs are decomposed (long EURUSD = long EUR + short USD). Reveals hidden concentration — e.g. several pairs all leaving you short USD.",
    "Realized vol": "How much the book has actually swung (annualised), vs the profile's target. Far below target = under-risked; far above = the vol-targeting isn't keeping up.",
}


def _panel(symbols, synthetic):
    if synthetic:
        return fx_data.synthetic_panel(symbols)
    start = (datetime.now(timezone.utc) - timedelta(days=550)).strftime("%Y-%m-%d")
    return fx_data.load_panel(symbols, start, use_cache=True)


# ---------------------------------------------------------------------------
# Plain-English explanation (generated from stored signals + indicators)
# ---------------------------------------------------------------------------
def _size_word(w):
    a = abs(w)
    return "no" if a < 0.02 else "a small" if a < 0.09 else "a medium" if a < 0.18 else "a large"


def _beginner_explanation(side, weight, agents, indicators, pair):
    weight = weight or 0.0
    if not side or abs(weight) < 1e-6:
        return (f"<b>No position in {pair}.</b> The agents disagree, or their signals are "
                f"too weak to clear our minimum-trade threshold — so we stay out. "
                f"Not trading is a valid, cost-saving decision.")
    sgn = 1 if weight > 0 else -1
    direction = "LONG" if weight > 0 else "SHORT"
    bet = "rise" if weight > 0 else "fall"
    parts = [f"<b>We're {direction} {pair}</b> — a bet the price will <b>{bet}</b> — at "
             f"{_size_word(weight)} position (<b>{abs(weight)*100:.0f}%</b> of the book)."]

    agents = agents or {}
    agree = sorted(((n, v) for n, v in agents.items() if v * sgn > 0.1), key=lambda kv: -abs(kv[1]))
    against = sorted(((n, v) for n, v in agents.items() if v * sgn < -0.1), key=lambda kv: -abs(kv[1]))
    if agree:
        names = " and ".join(_agent_phrase(n) for n, _ in agree[:2])
        parts.append(f"<b>Why this direction:</b> {names} both point this way.")
    if against:
        ph = _agent_phrase(against[0][0])
        parts.append(f"{ph[0].upper()}{ph[1:]} leaned the other way, "
                     f"which is why the bet is kept modest.")

    iv = indicators or {}
    ev = []
    ef, es = iv.get("ema_fast"), iv.get("ema_slow")
    if ef is not None and es is not None:
        rel = "above" if ef >= es else "below"
        td = "an up-trend" if ef >= es else "a down-trend"
        ev.append(f"the short-term average is {rel} the long-term average ({td}) "
                  f"— this is what the orange/blue 'EMA' lines on the chart show")
    adx = iv.get("adx")
    if adx is not None:
        strong = "a strong, established trend" if adx >= 20 else "a weak, choppy market"
        ev.append(f"ADX is {adx:.0f} → {strong} (ADX = trend <i>strength</i>, 0–100; above 20 = real trend)")
    rsi = iv.get("rsi")
    if rsi is not None:
        tag = "oversold/cheap" if rsi < 30 else "overbought/expensive" if rsi > 70 else "neutral"
        ev.append(f"RSI is {rsi:.0f} ({tag}; RSI runs 0–100, under 30 = oversold, over 70 = overbought)")
    roc = iv.get("roc")
    if roc is not None:
        ev.append(f"it has moved {roc*100:+.0f}% over the last ~60 days")
    if ev:
        parts.append("<b>The evidence:</b> " + "; ".join(ev) + ".")

    vol = iv.get("ann_vol")
    if vol is not None:
        parts.append(f"<b>Why this size:</b> we use <i>volatility targeting</i> — when a market is "
                     f"jumpy (here about {vol*100:.0f}%/year) we automatically trade smaller to keep "
                     f"overall risk steady.")
    return " ".join(parts)


def _curve_metrics(dates, values) -> dict:
    if not values or len(values) < 2:
        return {}
    s = pd.Series(values, index=pd.to_datetime(dates), dtype=float)
    out = {"total_return": round(float(s.iloc[-1] / s.iloc[0] - 1.0), 4)}
    r = s.pct_change().dropna()
    if len(r) >= 5 and r.std() > 0:
        out["sharpe"] = round(float((r.mean() * ANNUALIZATION - FX_RISK_FREE)
                                    / (r.std() * np.sqrt(ANNUALIZATION))), 2)
        out["vol"] = round(float(r.std() * np.sqrt(ANNUALIZATION)), 4)
        out["max_dd"] = round(float((s / s.cummax() - 1.0).min()), 4)
        out["win_rate"] = round(float((r > 0).mean()), 3)
    return out


def _agent_attribution(panel, p, bars) -> dict:
    pool = AgentPool(max_workers=1)
    _, signals, tilts = target_weights_history(panel, p, pool=pool, return_parts=True)
    rets = fx_data.closes(panel).pct_change(fill_method=None)
    if not signals:
        return {}
    names = list(next(iter(signals.values())).columns)
    out = {}
    for name in names:
        daily = pd.DataFrame({s: signals[s][name].shift(1) * rets[s]
                              for s in signals}).mean(axis=1).tail(bars).fillna(0.0)
        out[name] = round(float((1 + daily).prod() - 1.0), 4)
    ens = pd.DataFrame({s: tilts[s].shift(1) * rets[s]
                        for s in tilts}).mean(axis=1).tail(bars).fillna(0.0)
    out["ensemble"] = round(float((1 + ens).prod() - 1.0), 4)
    bh = rets.mean(axis=1).tail(bars).fillna(0.0)
    out["buy&hold"] = round(float((1 + bh).prod() - 1.0), 4)
    return out


def _pair_payload(sym, bars_df, trades, decision, p, bars):
    df = bars_df.dropna(subset=["open", "high", "low", "close"]).tail(bars)
    if df.empty:
        return None
    close = df["close"]
    ef, es = ind.ema(close, p.ema_fast), ind.ema(close, p.ema_slow)
    candles = [{"time": d.strftime("%Y-%m-%d"),
                "open": round(float(o), 6), "high": round(float(h), 6),
                "low": round(float(l), 6), "close": round(float(c), 6)}
               for d, o, h, l, c in zip(df.index, df["open"], df["high"],
                                        df["low"], df["close"])]
    line = lambda s: [{"time": d.strftime("%Y-%m-%d"), "value": round(float(v), 6)}
                      for d, v in s.items() if v == v]

    pos_of = {d.strftime("%Y-%m-%d"): i for i, d in enumerate(df.index)}
    closes_arr = close.to_numpy()
    first = df.index[0].strftime("%Y-%m-%d")
    out_trades = []
    for t in trades:
        if t.get("pair") != sym or t.get("date", "") < first:
            continue
        i = pos_of.get(t["date"])
        fwd, outcome = None, "open"
        entry = t.get("price")
        if i is not None and entry and i + _OUTCOME_BARS < len(closes_arr):
            s = 1.0 if t["side"] == "BUY" else -1.0
            fwd = round(float(s * (closes_arr[i + _OUTCOME_BARS] / entry - 1.0)), 4)
            outcome = "win" if fwd > 0 else "loss"
        why = _beginner_explanation(t.get("side"), t.get("target_weight"),
                                    t.get("agents"), t.get("indicators"), sym)
        out_trades.append({"time": t["date"], "side": t["side"], "price": t.get("price"),
                           "weight": t.get("target_weight"), "regime": t.get("regime"),
                           "why": why, "agents": t.get("agents"),
                           "fwd_return": fwd, "outcome": outcome})

    if decision:
        decision = {**decision, "text": _beginner_explanation(
            "LONG" if decision.get("weight", 0) > 0 else "SHORT",
            decision.get("weight"), decision.get("agents"),
            decision.get("indicators"), sym)}
    return {"candles": candles, "ema_fast": line(ef), "ema_slow": line(es),
            "trades": out_trades, "decision": decision}


def _transactions(state, panel, max_rows=400) -> dict:
    """Enriched transaction blotter: per-trade price economics + P&L since.

    For each weight change we reconstruct the bid/ask/spread (from the pair's
    dealing spread), the dollar notional traded (|Δweight| × equity that day), the
    transaction cost actually charged, and the trade's running mark-to-market
    contribution (Δweight × price move since × equity). The book is weight-based,
    so 'P&L since' is an honest marginal contribution, not lot-by-lot realised P&L.
    """
    eqh = state.get("equity_history", [])
    eq_map = {d: e for d, e in eqh}
    eq_dates = [d for d, _ in eqh]
    cur_equity = float(state.get("equity", state.get("initial_capital", 0.0)))

    def equity_on(date: str) -> float:
        if date in eq_map:
            return float(eq_map[date])
        prior = [d for d in eq_dates if d <= date]
        return float(eq_map[prior[-1]]) if prior else cur_equity

    closes = fx_data.closes(panel)
    last = closes.iloc[-1] if not closes.empty else pd.Series(dtype=float)

    rows = []
    tot_cost = tot_notional = tot_pnl = 0.0
    for t in state.get("trades", []):
        sym, price = t.get("pair"), t.get("price")
        if not sym or not price:
            continue
        try:
            pair = get_pair(sym)
        except KeyError:
            continue
        spread_px = pair.spread_pips * pair.pip          # round-trip, in price terms
        half = spread_px / 2.0
        eq = equity_on(t.get("date", ""))
        dw = float(t.get("delta_weight") or 0.0)
        notional = abs(dw) * eq
        cost = abs(dw) * 0.5 * pair.spread_fraction(price) * eq   # matches the book's charge
        lastpx = float(last.get(sym)) if last.get(sym) == last.get(sym) else None
        move = (lastpx / price - 1.0) if lastpx else None
        pnl = (dw * move * eq) if move is not None else None
        tot_cost += cost
        tot_notional += notional
        if pnl is not None:
            tot_pnl += pnl
        rows.append({
            "time": t.get("date"), "pair": sym, "side": t.get("side"),
            "dweight": round(dw, 4), "target": t.get("target_weight"),
            "price": round(float(price), 6),
            "bid": round(price - half, 6), "ask": round(price + half, 6),
            "spread_bps": round(spread_px / price * 1e4, 2),
            "notional": round(notional, 2), "cost": round(cost, 4),
            "last": round(lastpx, 6) if lastpx else None,
            "move": round(move, 4) if move is not None else None,
            "pnl": round(pnl, 2) if pnl is not None else None,
            "regime": t.get("regime"), "why": t.get("why"),
        })
    rows.reverse()                                        # newest first
    return {"rows": rows[:max_rows], "shown": min(len(rows), max_rows),
            "count": len(rows),
            "totals": {"cost": round(tot_cost, 2), "notional": round(tot_notional, 2),
                       "pnl": round(tot_pnl, 2)}}


def _exposure(state) -> dict:
    """Net exposure per CURRENCY from the open pairs: long EURUSD = +EUR, −USD.
    This is the real risk concentration an FX book carries (hidden in the pairs)."""
    exp: dict[str, float] = {}
    for sym, w in state.get("positions", {}).items():
        try:
            pair = get_pair(sym)
        except KeyError:
            continue
        exp[pair.base] = exp.get(pair.base, 0.0) + float(w)
        exp[pair.quote] = exp.get(pair.quote, 0.0) - float(w)
    return {k: round(v, 4) for k, v in
            sorted(exp.items(), key=lambda kv: -abs(kv[1])) if abs(v) > 1e-4}


def _min_track_record_days(returns) -> int | None:
    """Bailey & López de Prado Minimum Track Record Length: how many observations
    you need before PSR clears 95% (i.e. before an apparent edge is distinguishable
    from luck). None if the strategy isn't beating cash (no length would confirm)."""
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) < 3 or r.std() == 0:
        return None
    sr = float(r.mean() / r.std())
    if sr <= 0:
        return None
    g3 = float(((r - r.mean()) ** 3).mean() / r.std() ** 3)
    g4 = float(((r - r.mean()) ** 4).mean() / r.std() ** 4)
    z = _norm_ppf(0.95)
    mintrl = 1.0 + (1.0 - g3 * sr + (g4 - 1.0) / 4.0 * sr ** 2) * (z / sr) ** 2
    return int(math.ceil(mintrl))


def _risk_costs(state, p) -> dict:
    """Risk, cost-efficiency and statistical-significance analytics for one book.

    Honest by design: the cost wedge shows how much spread eats gross P&L, and the
    PSR / minimum-track-record figures say plainly when results are still just noise.
    """
    eqh = state.get("equity_history", [])
    eq_map = {d: e for d, e in eqh}
    eq_dates = [d for d, _ in eqh]
    cur_eq = float(state.get("equity", state.get("initial_capital", 0.0)))

    def equity_on(date: str) -> float:
        if date in eq_map:
            return float(eq_map[date])
        prior = [d for d in eq_dates if d <= date]
        return float(eq_map[prior[-1]]) if prior else cur_eq

    total_cost = 0.0
    cost_on_date: dict[str, float] = {}
    for t in state.get("trades", []):
        sym, price = t.get("pair"), t.get("price")
        if not sym or not price:
            continue
        try:
            pair = get_pair(sym)
        except KeyError:
            continue
        c = (abs(float(t.get("delta_weight") or 0.0)) * 0.5
             * pair.spread_fraction(price) * equity_on(t.get("date", "")))
        total_cost += c
        cost_on_date[t.get("date", "")] = cost_on_date.get(t.get("date", ""), 0.0) + c

    out = {"target_vol": round(p.target_vol, 4), "exposure": _exposure(state),
           "total_cost": round(total_cost, 2), "n_obs": max(len(eqh) - 1, 0),
           "currency": state.get("currency", "AUD"),
           "drawdown": [], "cost_curve": [], "psr": None, "realized_vol": None,
           "vol_ratio": None, "cost_drag": None, "min_track_days": None}
    if len(eqh) < 2:
        return out

    s = pd.Series([float(e) for _, e in eqh], index=[d for d, _ in eqh], dtype=float)
    dd = s / s.cummax() - 1.0
    out["drawdown"] = [{"time": d, "value": round(float(v), 4)} for d, v in dd.items()]

    base = float(s.iloc[0]) or float(state["initial_capital"])
    cum, curve = 0.0, []
    for d, e in zip(s.index, s.values):
        cum += cost_on_date.get(d, 0.0)
        curve.append({"time": d, "net": round(100.0 * e / base, 4),
                      "gross": round(100.0 * (e + cum) / base, 4),
                      "cum_cost": round(cum, 2)})
    out["cost_curve"] = curve

    r = s.pct_change().dropna()
    if len(r) >= 2 and r.std() > 0:
        out["realized_vol"] = round(float(r.std() * np.sqrt(ANNUALIZATION)), 4)
        out["vol_ratio"] = round(out["realized_vol"] / p.target_vol, 2) if p.target_vol else None
    if len(r) >= 3:
        out["psr"] = round(float(probabilistic_sharpe_ratio(r.values)), 3)
        out["min_track_days"] = _min_track_record_days(r.values)
    gross_profit = float(s.iloc[-1]) + cum - base
    out["cost_drag"] = round(total_cost / gross_profit, 3) if gross_profit > 0 else None
    return out


def build_payload(account, synthetic=False, bars=180):
    state = load_state(account)
    symbols = state.get("symbols", [])
    p = profile(state.get("profile", "balanced"))
    panel = _panel(symbols, synthetic)
    decisions = state.get("decisions", {})

    data, pairs = {}, []
    for sym in symbols:
        if sym not in panel:
            continue
        payload = _pair_payload(sym, panel[sym], state.get("trades", []),
                                decisions.get(sym), p, bars)
        if payload:
            data[sym] = payload
            pairs.append(sym)

    eq = state.get("equity", state["initial_capital"])
    eqh = state.get("equity_history", [])
    book_curve, book_metrics = [], {}
    if eqh:
        base = eqh[0][1] or state["initial_capital"]
        book_curve = [{"time": d, "value": round(100.0 * v / base, 4)} for d, v in eqh]
        book_metrics = _curve_metrics([d for d, _ in eqh], [v for _, v in eqh])

    closes_df = fx_data.closes(panel)
    bench_curve, bench_metrics = [], {}
    if not closes_df.empty:
        # Honest day-one comparison: clip the buy-and-hold benchmark to the book's
        # OWN live window and re-base it to 100 on the book's first day, so both
        # lines start together and the metrics table compares the same period.
        # Before the book has any history, fall back to a longer window so the
        # chart still shows price context.
        if eqh:
            start = pd.Timestamp(eqh[0][0])
            w = closes_df[closes_df.index >= start]
            if len(w) < 2:
                w = closes_df.tail(bars)
        else:
            w = closes_df.tail(bars)
        bh_ret = w.pct_change(fill_method=None).mean(axis=1).fillna(0.0)
        bh_eq = (1 + bh_ret).cumprod()
        bh_eq = 100.0 * bh_eq / bh_eq.iloc[0]            # start exactly at 100
        bench_curve = [{"time": d.strftime("%Y-%m-%d"), "value": round(float(v), 4)}
                       for d, v in bh_eq.items()]
        bench_metrics = _curve_metrics([d.strftime("%Y-%m-%d") for d in bh_eq.index],
                                       list(bh_eq.values))

    return {
        "account": account, "profile": state.get("profile", "balanced"),
        "currency": state.get("currency", "AUD"),
        "initial": state["initial_capital"], "equity": round(float(eq), 2),
        "ret": eq / state["initial_capital"] - 1.0,
        "trades_total": len(state.get("trades", [])),
        "gross": round(sum(abs(v) for v in state.get("positions", {}).values()), 2),
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "halted": state.get("risk_halted", False),
        "positions": [{"sym": k, "w": round(float(v), 4)}
                      for k, v in sorted(state.get("positions", {}).items(),
                                         key=lambda kv: -abs(kv[1]))],
        "book_curve": book_curve, "book_metrics": book_metrics,
        "bench_curve": bench_curve, "bench_metrics": bench_metrics,
        "transactions": _transactions(state, panel),
        "risk": _risk_costs(state, p),
        "attribution": _agent_attribution(panel, p, bars),
        "glossary": GLOSSARY, "agent_roles": _AGENT_ROLES,
        "pairs": pairs, "data": data,
    }


# ---------------------------------------------------------------------------
# Per-book HTML
# ---------------------------------------------------------------------------
_PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FX Paper · __ACCOUNT__</title>
<script src="__LWC__"></script>
<style>
:root{--bg:#0d1117;--panel:#161b22;--bd:#30363d;--fg:#e6edf3;--mut:#8b949e;
--up:#26a69a;--dn:#ef5350;--accent:#58a6ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
a{color:var(--accent)}
.nav{display:flex;gap:1rem;padding:.6rem 1.5rem;border-bottom:1px solid var(--bd);font-size:.85rem}
header{padding:1.1rem 1.5rem;border-bottom:1px solid var(--bd)}
h1{margin:0;font-size:1.15rem}.sub{color:var(--mut);font-size:.82rem;margin-top:.25rem}
.stats{display:flex;gap:1.4rem;flex-wrap:wrap;margin-top:.7rem}
.stat .v{font-size:1.05rem;font-weight:600}.stat .k{color:var(--mut);font-size:.7rem;text-transform:uppercase}
.pos{color:var(--up)}.neg{color:var(--dn)}
.section{padding:1rem 1.5rem}.grid2{display:grid;grid-template-columns:1fr 320px;gap:1rem}
@media(max-width:900px){.grid2{grid-template-columns:1fr}}
.card{border:1px solid var(--bd);border-radius:12px;background:var(--panel);padding:1rem}
.card h2{margin:0 0 .6rem;font-size:.9rem}
#eqchart{height:240px}#chart{height:430px}
.tabs{display:flex;gap:.4rem;flex-wrap:wrap;padding:0 1.5rem}
.tab{padding:.35rem .7rem;border:1px solid var(--bd);border-radius:999px;background:var(--panel);
color:var(--fg);cursor:pointer;font-size:.85rem}.tab.on{border-color:var(--accent);color:var(--accent)}
.wrap{display:grid;grid-template-columns:1fr 400px;gap:1rem;padding:1rem 1.5rem}
@media(max-width:900px){.wrap{grid-template-columns:1fr}}
.side{display:flex;flex-direction:column;gap:1rem;min-width:0}
.muted{color:var(--mut)}.why{font-size:.86rem;line-height:1.5}
.legend{font-size:.72rem;color:var(--mut);margin-top:.5rem;line-height:1.5}
.row{display:flex;align-items:center;gap:.5rem;margin:.25rem 0;font-size:.78rem}
.row .name{width:90px;color:var(--mut)}.bar{flex:1;height:9px;background:#21262d;border-radius:4px;position:relative}
.bar i{position:absolute;top:0;bottom:0;border-radius:4px}.val{width:60px;text-align:right}
.metrics{display:grid;grid-template-columns:auto 1fr 1fr;gap:.3rem .8rem;font-size:.82rem;align-items:center}
.metrics .hd{color:var(--mut);font-size:.7rem;text-transform:uppercase}
.journal{max-height:560px;overflow:auto}
.j{border:1px solid var(--bd);border-radius:10px;padding:.7rem;margin-bottom:.6rem;cursor:pointer}
.j:hover{border-color:var(--accent)}.j .hd{display:flex;justify-content:space-between;font-size:.84rem;gap:.5rem}
.badge{font-size:.68rem;padding:.05rem .4rem;border-radius:6px;border:1px solid var(--bd);color:var(--mut)}
.B{color:var(--up)}.S{color:var(--dn)}.win{color:var(--up)}.loss{color:var(--dn)}
.foot{padding:1rem 1.5rem;color:var(--mut);font-size:.75rem}
/* styled hover tooltip (replaces native title) */
.tip{position:relative;cursor:help;border-bottom:1px dotted var(--mut)}
.tip:hover::after{content:attr(data-tip);position:absolute;left:0;bottom:135%;z-index:30;
  width:260px;white-space:normal;background:#0b0f14;border:1px solid var(--accent);color:var(--fg);
  padding:.55rem .65rem;border-radius:8px;font-size:.74rem;font-weight:400;line-height:1.45;
  text-transform:none;box-shadow:0 6px 18px rgba(0,0,0,.5)}
.tip:hover::before{content:"";position:absolute;left:14px;bottom:128%;border:6px solid transparent;
  border-top-color:var(--accent);z-index:30}
.txnwrap{overflow:auto;max-height:560px;border:1px solid var(--bd);border-radius:10px}
table.txn{width:100%;border-collapse:collapse;font-size:.78rem;font-variant-numeric:tabular-nums}
table.txn th,table.txn td{padding:.4rem .6rem;text-align:right;white-space:nowrap;border-bottom:1px solid #21262d}
table.txn th{position:sticky;top:0;background:#0b0f14;color:var(--mut);font-weight:600;
  text-transform:uppercase;font-size:.66rem;z-index:2}
table.txn td:first-child,table.txn th:first-child,table.txn td.l,table.txn th.l{text-align:left}
table.txn tbody tr:hover{background:#1b2230}
table.txn tfoot td{position:sticky;bottom:0;background:#0b0f14;font-weight:600;border-top:1px solid var(--bd)}
.txnsearch{margin:.2rem 0 .6rem;padding:.35rem .6rem;background:#0b0f14;border:1px solid var(--bd);
  border-radius:8px;color:var(--fg);font-size:.8rem;width:200px}
/* layout: sticky in-page nav + responsive card bands */
.subnav{position:sticky;top:0;z-index:20;display:flex;gap:.4rem;flex-wrap:wrap;
  padding:.5rem 1.5rem;background:rgba(13,17,23,.93);backdrop-filter:blur(6px);
  border-bottom:1px solid var(--bd)}
.subnav a{padding:.3rem .75rem;border-radius:999px;border:1px solid var(--bd);
  color:var(--mut);text-decoration:none;font-size:.8rem}
.subnav a:hover{border-color:var(--accent);color:var(--accent)}
section{padding:1.25rem 1.5rem;scroll-margin-top:3.4rem}
.band{display:flex;align-items:baseline;gap:.6rem;margin:0 0 .9rem;font-size:1.05rem;font-weight:600}
.band .h{color:var(--mut);font-size:.78rem;font-weight:400}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:1rem;align-items:start}
.span2{grid-column:span 2}@media(max-width:680px){.span2{grid-column:span 1}}
.kpis{display:flex;gap:1rem;flex-wrap:wrap;margin-top:.8rem}
.kpi{flex:1 1 120px;border:1px solid var(--bd);border-radius:10px;background:var(--panel);padding:.6rem .8rem}
.kpi .v{font-size:1.1rem;font-weight:600}.kpi .k{color:var(--mut);font-size:.66rem;text-transform:uppercase;margin-top:.15rem}
</style></head><body>
<div class="nav"><a href="index.html">← All books</a><a href="how.html">📖 How it works — start here</a></div>
<header>
  <h1>FX Paper Book · <span style="color:var(--accent)">__ACCOUNT__</span>
    <span class="badge">__PROFILE__</span>__HALT__</h1>
  <div class="sub">base __CCY__ · updated __UPDATED__ · candlesticks, performance vs buy-and-hold, and a plain-English reason + outcome for every trade. Hover any underlined word for its meaning.</div>
  <div class="stats" id="stats"></div>
</header>

<div class="subnav">
  <a href="#overview">Overview</a>
  <a href="#risk">Risk &amp; costs</a>
  <a href="#pairs">Pair explorer</a>
  <a href="#txns">Transactions</a>
</div>

<section id="overview">
  <div class="band">Overview <span class="h">equity vs buy-and-hold · performance · positions</span></div>
  <div class="cards">
    <div class="card span2"><h2><span class="tip" data-tip="__T_BENCH__">Equity vs buy-and-hold</span> <span class="muted" style="font-weight:400">(both start at 100)</span></h2><div id="eqchart"></div></div>
    <div class="card"><h2>Performance <span class="muted" style="font-weight:400">vs buy &amp; hold</span></h2><div id="metrics" class="metrics"></div></div>
    <div class="card"><h2>Open positions <span class="muted" style="font-weight:400">(signed % of equity)</span></h2><div id="positionscard"></div></div>
    <div class="card"><h2><span class="tip" data-tip="__T_SCORE__">Agent scorecard</span> <span class="muted" style="font-weight:400">(this window)</span></h2><div id="agentcard"></div></div>
  </div>
</section>

<section id="risk">
  <div class="band">Risk, costs &amp; significance <span class="h">is the edge real after costs &amp; luck?</span></div>
  <div class="cards">
    <div class="card span2"><h2>Costs &amp; <span class="tip" data-tip="__T_PSR__">is it luck?</span></h2>
      <div class="stats" id="riskstats"></div>
      <div id="sigtext" class="why" style="margin-top:.7rem"></div></div>
    <div class="card"><h2><span class="tip" data-tip="__T_EXP__">Net currency exposure</span></h2><div id="exposurecard"></div></div>
    <div class="card"><h2><span class="tip" data-tip="__T_DD__">Drawdown (underwater)</span></h2><div id="ddchart" style="height:200px"></div></div>
    <div class="card"><h2><span class="tip" data-tip="__T_WEDGE__">Costs vs gross P&amp;L</span></h2><div id="costchart" style="height:200px"></div></div>
  </div>
</section>

<section id="pairs">
  <div class="band">Pair explorer <span class="h">candlesticks · today's read · the reason for every trade</span></div>
  <div class="tabs" id="tabs"></div>
  <div class="wrap">
    <div id="chart"></div>
    <div class="side">
      <div class="card"><h2><span class="tip" data-tip="__T_TILT__">Today's read</span> · <span id="curpair"></span></h2>
        <div id="decision" class="why muted"></div>
        <div id="agents" style="margin-top:.7rem"></div>
        <div class="legend" id="legend"></div></div>
      <div class="card"><h2>Trade journal — plain-English reason &amp; <span class="tip" data-tip="__T_OUT__">outcome</span></h2>
        <div id="journal" class="journal"></div></div>
    </div>
  </div>
</section>

<section id="txns">
  <div class="band">Transactions <span class="h">full blotter · price economics &amp; P&amp;L</span></div>
  <div class="card">
    <h2>Full blotter <span class="muted" style="font-weight:400" id="txnsub"></span></h2>
    <input class="txnsearch" id="txnsearch" placeholder="filter by pair, e.g. BTC">
    <div class="txnwrap"><table class="txn" id="txntable"></table></div>
    <div class="legend">Every column is hover-defined. <b>P&amp;L since</b> is each trade's running
      mark-to-market contribution (Δweight × price move since × equity) — an honest marginal figure
      for a weight-based book, not lot-by-lot realised profit. Costs are always on.</div>
  </div>
</section>

<div class="foot">Paper money. Six agents (trend · breakout · mean-reversion · momentum · carry · deep-learning),
blended by a Hedge ensemble and sized by volatility targeting. Out-of-sample testing found no statistically
significant edge — this is a learning tool, not a forecast. <a href="how.html">See how it all fits together →</a></div>
<script>
const DASH = __DATA__;
const G = DASH.glossary||{}, ROLES = DASH.agent_roles||{};
const pct = v => v==null? "–" : (v>=0?"+":"")+(v*100).toFixed(2)+"%";
const fmt = v => v==null? "–" : (Math.abs(v)>=100? v.toFixed(2) : v.toPrecision(5));
const tip = (txt,term)=>`<span class="tip" data-tip="${(G[term]||'').replace(/"/g,'&quot;')}">${txt}</span>`;
let chart;

(function(){
  const m=DASH.book_metrics||{};
  const items=[["Equity",DASH.equity.toLocaleString()+" "+DASH.currency,null],
    ["Return",pct(DASH.ret),"Benchmark"],["Sharpe",(m.sharpe??"–"),"Sharpe"],
    ["Max drawdown",(m.max_dd!=null?pct(m.max_dd):"–"),"Max drawdown"],
    ["Gross lev.",DASH.gross+"x","Gross leverage"],["Trades",DASH.trades_total,null]];
  document.getElementById('stats').innerHTML=items.map(([k,v,g])=>
    `<div class=stat><div class=v>${v}</div><div class=k>${g?tip(k,g):k}</div></div>`).join('');
})();

function bars(obj, hi){
  if(!obj||!Object.keys(obj).length) return '<div class="muted">no data</div>';
  const max=Math.max(0.0001,...Object.values(obj).map(v=>Math.abs(v)));
  return Object.entries(obj).map(([n,v])=>{
    const w=Math.min(Math.abs(v)/max,1)*50,left=v>=0?50:50-w,col=v>=0?'var(--up)':'var(--dn)';
    const em=(hi&&hi.includes(n))?'font-weight:700;color:var(--fg)':'';
    const nm=ROLES[n]?`<span class="tip" data-tip="${ROLES[n].replace(/"/g,'&quot;')}">${n}</span>`:n;
    return `<div class=row><div class=name style="${em}">${nm}</div>`+
      `<div class=bar><i style="left:${left}%;width:${w}%;background:${col}"></i>`+
      `<i style="left:50%;width:1px;background:#555"></i></div>`+
      `<div class=val style="color:${col}">${pct(v)}</div></div>`;}).join('');
}

(function(){
  const b=DASH.book_metrics||{},k=DASH.bench_metrics||{};
  const rows=[["Return","total_return",true,"Benchmark"],["Sharpe","sharpe",false,"Sharpe"],
    ["Volatility","vol",true,"Volatility"],["Max drawdown","max_dd",true,"Max drawdown"],
    ["Win rate","win_rate",true,"Win rate"]];
  const cell=(m,key,isP)=>{const v=m[key];return v==null?"–":(isP?pct(v):v);};
  document.getElementById('metrics').innerHTML=`<div class=hd></div><div class=hd>Book</div><div class=hd>Buy&amp;Hold</div>`+
    rows.map(([lbl,key,isP,g])=>`<div>${tip(lbl,g)}</div><div>${cell(b,key,isP)}</div><div class=muted>${cell(k,key,isP)}</div>`).join('');
  document.getElementById('agentcard').innerHTML=bars(DASH.attribution,["ensemble","buy&hold"]);
  const pos=DASH.positions||[];
  document.getElementById('positionscard').innerHTML=pos.length?bars(Object.fromEntries(pos.map(p=>[p.sym,p.w]))):'<div class="muted">Flat — no open positions right now.</div>';
})();

(function(){
  const el=document.getElementById('eqchart');
  if(!(DASH.bench_curve||[]).length&&!(DASH.book_curve||[]).length){el.innerHTML='<p class=muted style="padding:1rem">This fills in as the book trades over the coming days.</p>';return;}
  const c=LightweightCharts.createChart(el,{layout:{background:{color:'#161b22'},textColor:'#e6edf3'},
    grid:{vertLines:{color:'#21262d'},horzLines:{color:'#21262d'}},rightPriceScale:{borderColor:'#30363d'},timeScale:{borderColor:'#30363d'},autoSize:true});
  if((DASH.bench_curve||[]).length)c.addLineSeries({color:'#8b949e',lineWidth:1,title:'Buy&Hold'}).setData(DASH.bench_curve);
  if((DASH.book_curve||[]).length)c.addLineSeries({color:'#58a6ff',lineWidth:2,title:'Book'}).setData(DASH.book_curve);
  c.timeScale().fitContent();
})();

(function(){
  const rk=DASH.risk||{}, C=rk.currency||DASH.currency;
  const volTxt=rk.realized_vol==null?"–":pct(rk.realized_vol)+(rk.vol_ratio!=null?` (${rk.vol_ratio}× target)`:"");
  const dragTxt=rk.cost_drag==null?"–":(rk.cost_drag*100).toFixed(0)+"% of gross";
  const psrTxt=rk.psr==null?"–":(rk.psr*100).toFixed(0)+"%";
  const items=[["Spread cost",(rk.total_cost!=null?rk.total_cost.toLocaleString()+" "+C:"–"),null],
    ["Cost drag",dragTxt,"Cost drag"],["Realized vol",volTxt,"Realized vol"],["PSR",psrTxt,"PSR"]];
  const rs=document.getElementById('riskstats');
  if(rs)rs.innerHTML=items.map(([k,v,g])=>`<div class=stat><div class=v>${v}</div><div class=k>${g?tip(k,g):k}</div></div>`).join('');
  let sig;
  if(rk.psr==null) sig="Not enough history yet to judge significance — give it a few more days of returns.";
  else if(rk.min_track_days==null) sig=`PSR ${psrTxt}: the book isn't beating cash yet, so no length of track record would confirm a real edge at this rate. ${tip('What is this?','Significance')}`;
  else{const more=Math.max(0,rk.min_track_days-rk.n_obs);
    sig=`<b>PSR ${psrTxt}</b> — the probability the true Sharpe is above zero, from ${rk.n_obs} days of returns. To be 95% confident this isn't luck you'd need ≈ <b>${rk.min_track_days}</b> trading days (~${Math.round(rk.min_track_days/21)} months) — about <b>${more}</b> more. Until then, treat the P&L as noise. ${tip('Why?','Significance')}`;}
  const st=document.getElementById('sigtext'); if(st)st.innerHTML=sig;
  const exp=rk.exposure||{}, ec=document.getElementById('exposurecard');
  if(ec)ec.innerHTML=Object.keys(exp).length?bars(exp):'<div class=muted>Flat.</div>';
  const mk=el=>LightweightCharts.createChart(el,{layout:{background:{color:'#161b22'},textColor:'#e6edf3'},grid:{vertLines:{color:'#21262d'},horzLines:{color:'#21262d'}},rightPriceScale:{borderColor:'#30363d'},timeScale:{borderColor:'#30363d'},autoSize:true});
  const dd=rk.drawdown||[], de=document.getElementById('ddchart');
  if(de&&dd.length){const ch=mk(de);ch.addAreaSeries({lineColor:'#ef5350',topColor:'rgba(239,83,80,.0)',bottomColor:'rgba(239,83,80,.35)',lineWidth:2}).setData(dd.map(d=>({time:d.time,value:+(d.value*100).toFixed(3)})));ch.timeScale().fitContent();}
  else if(de)de.innerHTML='<p class=muted style="padding:1rem">Fills in as the book trades.</p>';
  const cc=rk.cost_curve||[], ce=document.getElementById('costchart');
  if(ce&&cc.length>1){const ch=mk(ce);
    ch.addLineSeries({color:'#8b949e',lineWidth:1,title:'Gross (pre-cost)'}).setData(cc.map(d=>({time:d.time,value:d.gross})));
    ch.addLineSeries({color:'#58a6ff',lineWidth:2,title:'Net'}).setData(cc.map(d=>({time:d.time,value:d.net})));
    ch.timeScale().fitContent();}
  else if(ce)ce.innerHTML='<p class=muted style="padding:1rem">The gap between gross &amp; net = cumulative spread cost. Fills in as the book trades.</p>';
})();

(function(){
  const T=DASH.transactions||{rows:[],totals:{}};
  const el=document.getElementById('txntable'),sub=document.getElementById('txnsub');
  if(!T.rows||!T.rows.length){el.innerHTML='<tbody><tr><td class=l>This fills in as the book trades.</td></tr></tbody>';return;}
  if(sub)sub.textContent=`(${T.shown} of ${T.count} shown, newest first)`;
  const cols=[["Time"],["Pair"],["Side"],["Δw→tgt","Delta weight"],["Mid","Mid price"],
    ["Bid","Bid"],["Ask","Ask"],["Spread bps","Spread"],["Notional","Notional"],
    ["Cost","Transaction cost"],["Last"],["Move"],["P&L since","P&L since"]];
  const money=v=>v==null?"–":(v<0?"-":"")+Math.abs(v).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});
  const head='<thead><tr>'+cols.map(([lbl,g])=>`<th${(lbl==='Pair'||lbl==='Time')?' class=l':''}>${g?tip(lbl,g):lbl}</th>`).join('')+'</tr></thead>';
  const ccy=DASH.currency;
  function rowHTML(r){
    const sideCls=r.side==='BUY'?'B':'S';
    const dw=`<span class=${r.dweight>=0?'win':'loss'}>${(r.dweight>=0?'+':'')+r.dweight}</span> → ${r.target!=null?r.target:'–'}`;
    return `<tr title="${(r.why||'').replace(/"/g,'&quot;')}">`+
      `<td class=l>${r.time}</td>`+
      `<td class=l>${r.pair}${r.regime?` <span class=badge>${r.regime}</span>`:''}</td>`+
      `<td class="${sideCls}">${r.side}</td>`+
      `<td>${dw}</td>`+
      `<td>${fmt(r.price)}</td>`+
      `<td class=muted>${fmt(r.bid)}</td>`+
      `<td class=muted>${fmt(r.ask)}</td>`+
      `<td>${r.spread_bps}</td>`+
      `<td>${money(r.notional)}</td>`+
      `<td class=loss>${money(r.cost)}</td>`+
      `<td class=muted>${r.last!=null?fmt(r.last):'–'}</td>`+
      `<td class=${r.move==null?'muted':(r.move>=0?'win':'loss')}>${r.move==null?'–':pct(r.move)}</td>`+
      `<td class=${r.pnl==null?'muted':(r.pnl>=0?'win':'loss')}>${money(r.pnl)}</td></tr>`;
  }
  const tt=T.totals||{};
  const foot=`<tfoot><tr><td class=l colspan=8>Totals · ${T.count} trades (${ccy})</td>`+
    `<td>${money(tt.notional)}</td><td class=loss>${money(tt.cost)}</td><td></td><td></td>`+
    `<td class=${(tt.pnl||0)>=0?'win':'loss'}>${money(tt.pnl)}</td></tr></tfoot>`;
  function render(f){const rows=f?T.rows.filter(r=>r.pair.toLowerCase().includes(f)):T.rows;
    el.innerHTML=head+'<tbody>'+rows.map(rowHTML).join('')+'</tbody>'+foot;}
  render('');
  document.getElementById('txnsearch').addEventListener('input',e=>render(e.target.value.trim().toLowerCase()));
})();

function showPair(sym){
  document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('on',t.dataset.s===sym));
  document.getElementById('curpair').textContent=sym;
  const d=DASH.data[sym];
  document.getElementById('chart').innerHTML='';
  chart=LightweightCharts.createChart(document.getElementById('chart'),{layout:{background:{color:'#161b22'},textColor:'#e6edf3'},
    grid:{vertLines:{color:'#21262d'},horzLines:{color:'#21262d'}},rightPriceScale:{borderColor:'#30363d'},timeScale:{borderColor:'#30363d'},autoSize:true});
  const cs=chart.addCandlestickSeries({upColor:'#26a69a',downColor:'#ef5350',wickUpColor:'#26a69a',wickDownColor:'#ef5350',borderVisible:false});
  cs.setData(d.candles);
  chart.addLineSeries({color:'#f5a623',lineWidth:1,priceLineVisible:false}).setData(d.ema_fast);
  chart.addLineSeries({color:'#58a6ff',lineWidth:1,priceLineVisible:false}).setData(d.ema_slow);
  cs.setMarkers(d.trades.map(t=>({time:t.time,position:t.side==='BUY'?'belowBar':'aboveBar',
    color:t.side==='BUY'?'#26a69a':'#ef5350',shape:t.side==='BUY'?'arrowUp':'arrowDown',
    text:t.side+(t.weight!=null?' '+Math.round(t.weight*100)+'%':'')})));
  chart.timeScale().fitContent();
  const dec=d.decision||{};
  document.getElementById('decision').innerHTML=dec.text||'No active position — agents flat or conflicted here.';
  document.getElementById('agents').innerHTML=bars(dec.agents);
  document.getElementById('legend').innerHTML='<b>Agent votes</b> above run −1 (max short) to +1 (max long). Hover a name for what it does. The orange/blue lines on the chart are the short- and long-term averages (EMAs).';
  const j=document.getElementById('journal');
  if(!d.trades.length){j.innerHTML='<div class="muted">No trades for '+sym+' yet.</div>';return;}
  j.innerHTML=d.trades.slice().reverse().map(t=>{
    const cls=t.side==='BUY'?'B':'S';
    const oc=t.outcome==='win'?`<span class=win>✅ ${pct(t.fwd_return)}</span>`:t.outcome==='loss'?`<span class=loss>❌ ${pct(t.fwd_return)}</span>`:`<span class=muted>⏳ open</span>`;
    return `<div class="j" data-t="${t.time}"><div class=hd><span class="${cls}">${t.side} ${sym} @ ${fmt(t.price)}</span><span>${oc}</span></div>`+
      `<div class=hd style="margin-top:.2rem"><span class=badge>${t.time}${t.regime?' · '+t.regime:''}</span></div>`+
      `<div class="why" style="margin-top:.4rem">${t.why||'(no rationale)'}</div></div>`;}).join('');
  j.querySelectorAll('.j').forEach(el=>el.onclick=()=>{const times=d.candles.map(c=>c.time),i=times.indexOf(el.dataset.t);
    if(i>=0)chart.timeScale().setVisibleRange({from:times[Math.max(0,i-30)],to:times[Math.min(times.length-1,i+8)]});});
}
const tabs=document.getElementById('tabs');
DASH.pairs.forEach(s=>{const b=document.createElement('div');b.className='tab';b.dataset.s=s;b.textContent=s;b.onclick=()=>showPair(s);tabs.appendChild(b);});
if(DASH.pairs.length)showPair(DASH.pairs[0]);else document.getElementById('chart').innerHTML='<p style="padding:2rem;color:#8b949e">No data yet.</p>';
</script></body></html>"""


def render(payload: dict) -> str:
    g = payload["glossary"]
    repl = {
        "__ACCOUNT__": payload["account"], "__PROFILE__": payload["profile"],
        "__CCY__": payload["currency"], "__UPDATED__": payload["updated"],
        "__HALT__": ' <span class="badge" style="color:#ef5350">RISK-HALTED</span>'
                    if payload["halted"] else "",
        "__LWC__": _LWC,
        "__T_BENCH__": g["Benchmark"], "__T_TILT__": g["Tilt"], "__T_OUT__": g["Outcome"],
        "__T_PSR__": g["PSR"], "__T_DD__": g["Drawdown curve"], "__T_WEDGE__": g["Cost wedge"],
        "__T_SCORE__": g["Agent scorecard"], "__T_EXP__": g["Net exposure"],
        "__DATA__": json.dumps(payload, separators=(",", ":")),
    }
    html = _PAGE
    for k, v in repl.items():
        html = html.replace(k, v)
    return html


def export_account(account, synthetic=False, out_path=None, bars=180) -> str:
    payload = build_payload(account, synthetic=synthetic, bars=bars)
    html = render(payload)
    if out_path:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "w") as f:
            f.write(html)
        print(f"  wrote {out_path} ({len(payload['pairs'])} pairs, {payload['trades_total']} trades)")
    return html


# ---------------------------------------------------------------------------
# "How it works" page (flow diagram + beginner explanation)
# ---------------------------------------------------------------------------
_HOW = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>How it works — FX Paper Books</title>
<script src="__MERMAID__"></script>
<style>
body{margin:0;background:#0d1117;color:#e6edf3;font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;line-height:1.6}
.nav{display:flex;gap:1rem;padding:.6rem 1.5rem;border-bottom:1px solid #30363d;font-size:.85rem}
a{color:#58a6ff}.wrap{max-width:860px;margin:0 auto;padding:1.5rem}
h1{font-size:1.5rem}h2{font-size:1.15rem;margin-top:2rem;border-bottom:1px solid #30363d;padding-bottom:.3rem}
.diagram{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:1rem;margin:1rem 0;overflow:auto}
.step{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:.8rem 1rem;margin:.7rem 0}
.step b{color:#58a6ff}.muted{color:#8b949e}
table{border-collapse:collapse;width:100%;font-size:.9rem;margin:1rem 0}
td,th{border:1px solid #30363d;padding:.5rem .6rem;text-align:left;vertical-align:top}
th{background:#161b22}.warn{background:#1d1410;border:1px solid #5c4012;border-radius:10px;padding:1rem;margin:1.5rem 0}
</style></head><body>
<div class="nav"><a href="index.html">← All books</a></div>
<div class="wrap">
<h1>How this trading system works</h1>
<p class="muted">A plain-English tour of what happens between "market data comes in" and "a position
shows up on the dashboard" — and <i>why</i> each step exists.</p>

<div class="diagram"><pre class="mermaid">
flowchart TD
  D["📈 Market data<br/>daily candles · 7 FX majors + 3 crypto"] --> AG
  subgraph AG["🤖 6 agents — each hunts a different 'edge', in parallel"]
    A1["Trend<br/>short vs long average"]
    A2["Breakout<br/>new highs / lows"]
    A3["Mean-reversion<br/>fade extremes"]
    A4["Momentum<br/>recent direction persists"]
    A5["Carry<br/>interest-rate gap"]
    A6["Deep learning<br/>neural net"]
  end
  AG --> E["⚖️ Ensemble (Hedge blend)<br/>leans on agents that have been right lately"]
  E --> R["🛡️ Risk sizing<br/>volatility target · per-pair &amp; leverage caps · drawdown breaker"]
  R --> P["💼 Positions → paper book<br/>(matt &amp; partner, separate)"]
  P --> DB["📊 Dashboard<br/>candles · why · outcome"]
  P --> V["🔬 Validation<br/>walk-forward · Deflated Sharpe · PBO"]
  V -. "is the edge real?" .-> E
</pre></div>

<h2>Step by step</h2>
<div class="step"><b>1. Market data.</b> Every weekday we pull the latest daily price "candle" (open/high/low/close)
for 7 major currency pairs and 3 cryptos. Everything downstream reads only data up to <i>now</i> — never the
future. <span class="muted">Why: using future data ("lookahead") is the #1 way backtests lie.</span></div>

<div class="step"><b>2. Six agents vote.</b> Each agent is a small, independent strategy that looks for one kind of
edge and outputs a vote from −1 (strong sell) to +1 (strong buy). <span class="muted">Why diversify: trend and
mean-reversion are opposites — blending weakly-related ideas is steadier than betting on one.</span></div>

<div class="step"><b>3. The ensemble blends the votes.</b> A "Hedge" algorithm gives more weight to agents that
have been right recently, with a floor so none is ever switched fully off. <span class="muted">Why: it adapts
without wild swings, and has mathematical guarantees against over-trusting a lucky agent.</span></div>

<div class="step"><b>4. Risk sizing turns the blended view into a position.</b> <i>Volatility targeting</i> scales
the bet so total risk stays roughly constant (smaller in jumpy markets); per-pair and leverage caps limit any
single bet; a drawdown breaker flattens everything if losses get too deep. <span class="muted">Why: surviving
is the prerequisite for compounding — controlling losses matters more than picking winners.</span></div>

<div class="step"><b>5. The paper book trades.</b> Two separate books (you &amp; your partner) hold the positions
with realistic costs (the bid/ask spread on every trade, overnight financing). No real money.</div>

<div class="step"><b>6. Validation keeps us honest.</b> We re-test on data the model never saw (walk-forward) and
score it with the <i>Deflated Sharpe Ratio</i> and <i>Probability of Backtest Overfitting</i> — which penalise
us for how many ideas we tried. <span class="muted">Why: it's easy to find a pretty pattern by luck; these tests
tell us if an edge is <b>real</b>. So far, honestly, none clears the bar.</span></div>

<h2>What each agent looks at</h2>
<table><tr><th>Agent</th><th>What it does</th><th>Shines when…</th></tr>
<tr><td>Trend</td><td>Compares a short-term vs long-term average; rides the direction</td><td>markets are trending</td></tr>
<tr><td>Breakout</td><td>Acts when price pushes past its recent high/low range</td><td>a new move is starting</td></tr>
<tr><td>Mean-reversion</td><td>Bets an over-stretched price snaps back to its average</td><td>markets are range-bound</td></tr>
<tr><td>Momentum</td><td>Assumes recent direction persists</td><td>steady moves continue</td></tr>
<tr><td>Carry</td><td>Leans toward the currency paying more interest</td><td>calm, risk-on markets</td></tr>
<tr><td>Deep learning</td><td>A neural net trained to maximise risk-adjusted return</td><td>patterns repeat (use with care)</td></tr>
</table>

<h2>From AUD to a trade — and back</h2>
<p class="muted">Your account is in <b>AUD</b>, but the pairs settle in other currencies (EUR/USD trades in
US dollars, USD/JPY in yen). So every position is a two-step currency journey, and AUD/USD moves are part of
your real P&amp;L — not just the pair's move.</p>
<div class="diagram"><pre class="mermaid">
flowchart LR
  A["🇦🇺 AUD account<br/>your capital"] -->|"leg 1: convert AUD→USD<br/>at today's AUD/USD"| U["💵 USD<br/>(the quote currency)"]
  U -->|"buy the pair"| POS["📈 EUR/USD position<br/>P&amp;L builds up in USD"]
  POS -->|"close / mark to market"| U2["💵 USD proceeds + P&amp;L"]
  U2 -->|"leg 2: convert USD→AUD<br/>at AUD/USD now"| A2["🇦🇺 back to AUD<br/>your real P&amp;L"]
  A2 -. "if AUD/USD moved while you held,<br/>your AUD P&amp;L changes even if the pair didn't" .-> A
</pre></div>
<div class="step"><b>Two FX legs, always.</b> To open a USD-quoted pair from AUD you first buy USD (leg 1);
when you close you convert the USD result back to AUD (leg 2). If the Aussie strengthens against the US dollar
while you hold, you get fewer AUD back — a genuine loss even if the pair itself was flat (and a gain if it weakens).
<span class="muted">The books and backtest now apply this translation for every pair (AUD/USD, AUD/JPY, …) using
the majors already in the panel, so the equity you see is <b>true AUD</b> — pair move, currency move and costs all
included.</span></div>

<h2>How to read a trade in the journal</h2>
<div class="step">Each entry shows: <b>BUY/SELL pair @ price</b> · the <b>outcome</b> (✅ win / ❌ loss / ⏳ still open —
the price move over the next ~10 days), then a plain-English paragraph: <b>what</b> we did, <b>which agents</b>
drove it, <b>the evidence</b> (with each term defined), and <b>why that size</b>. The arrows on the candle chart
mark where each trade happened.</div>

<div class="warn"><b>Honest note.</b> This is paper money and a <b>learning tool</b>. Daily FX is extremely hard to
beat, and our own out-of-sample tests found <b>no statistically significant edge</b>. The value here is seeing
<i>how</i> systematic decisions are made and judged — not a promise of profit.</div>
</div>
<script>mermaid.initialize({startOnLoad:true,theme:'dark'});</script>
</body></html>"""


def build_how_page(out_dir) -> None:
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "how.html"), "w") as f:
        f.write(_HOW.replace("__MERMAID__", _MERMAID))
    print(f"  wrote {out_dir}/how.html")


def build_index(accounts, out_dir) -> None:
    os.makedirs(out_dir, exist_ok=True)
    cards = []
    for a in accounts:
        try:
            s = load_state(a)
            eq = s.get("equity", s["initial_capital"])
            r = eq / s["initial_capital"] - 1
            cards.append(f'<a class=card href="fx_{a}.html"><div class=name>{a}</div>'
                         f'<div class=amt>{eq:,.0f} {s.get("currency","AUD")} '
                         f'({r:+.2%}) · {s.get("profile","")}</div></a>')
        except SystemExit:
            continue
    html = ("<!doctype html><meta charset=utf-8><title>FX Paper Books</title>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<style>body{font-family:system-ui,sans-serif;background:#0d1117;color:#e6edf3;"
            "margin:0;padding:3rem;max-width:720px}h1{margin:0 0 .25rem}.s{color:#8b949e;margin:0 0 1.5rem}"
            "a.card{display:block;margin:1rem 0;padding:1.25rem 1.5rem;border:1px solid #30363d;"
            "border-radius:14px;background:#161b22;color:#e6edf3;text-decoration:none}"
            "a.card:hover{border-color:#58a6ff}.name{font-size:1.2rem;font-weight:600;color:#58a6ff}"
            ".amt{color:#8b949e;font-size:.9rem}.how{display:inline-block;margin-bottom:1.5rem;color:#58a6ff}</style>"
            "<h1>FX Paper Books</h1><p class=s>Candlesticks + performance + plain-English reasons behind every trade · "
            f"updated {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}</p>"
            "<a class=how href='how.html'>📖 New here? Start with “How it works” →</a>" + "".join(cards))
    with open(os.path.join(out_dir, "index.html"), "w") as f:
        f.write(html)
    print(f"  wrote {out_dir}/index.html ({len(cards)} accounts)")


def main(argv=None):
    ap = argparse.ArgumentParser(description="FX paper-book candlestick + analytics dashboard")
    ap.add_argument("--account")
    ap.add_argument("-o", "--out")
    ap.add_argument("--out-dir", default="public")
    ap.add_argument("--index", action="store_true", help="build the landing index + how-it-works page")
    ap.add_argument("--all", action="store_true", help="export every account + index + how page")
    ap.add_argument("--bars", type=int, default=180)
    ap.add_argument("--synthetic", action="store_true")
    args = ap.parse_args(argv)

    if args.all or args.index:
        accts = list_accounts()
        if args.all:
            for a in accts:
                export_account(a, args.synthetic, os.path.join(args.out_dir, f"fx_{a}.html"), args.bars)
        build_index(accts, args.out_dir)
        build_how_page(args.out_dir)
    elif args.account:
        out = args.out or os.path.join(args.out_dir, f"fx_{args.account}.html")
        export_account(args.account, args.synthetic, out, args.bars)
    else:
        ap.error("pass --account, --all, or --index")


if __name__ == "__main__":
    main()
