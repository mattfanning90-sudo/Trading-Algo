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
from . import news
from .agents import AgentPool
from .fx_book import list_accounts, load_state
from .fx_config import ANNUALIZATION, FX_RISK_FREE, profile
from .fx_strategy import min_history, target_weights_history
from .pairs import get_pair
from .validation import (_norm_ppf, deflated_sharpe_ratio, pbo,
                         probabilistic_sharpe_ratio)

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
    "Attribution": "Where the P&L came from — each pair's running contribution (Δweight × move-since × equity), and the split between long vs short positions and trending vs ranging regimes.",
    "Profit factor": "Gross gains ÷ gross losses across days. >1 means up-days outweigh down-days; 1.5+ is healthy. Below 1 = losing.",
    "Expectancy": "Average return per day — what you make on a typical day, good and bad blended. The honest 'edge per bet'.",
    "Conviction": "Today's ensemble tilt per pair, −1 (max short) to +1 (max long): how strongly the blended agents lean right now. Green = long, red = short, brighter = stronger.",
    "DSR": "Deflated Sharpe Ratio — like PSR, but it also penalises you for how many strategies/agents were tried (the more you test, the higher a Sharpe you'd expect by luck alone). Clears 95% = a genuinely strong result.",
    "PBO": "Probability of Backtest Overfitting — across the agents, how often the in-sample best one underperforms out-of-sample. High = 'the winner is probably luck'. Low is good.",
    "Catalyst": "A high-impact scheduled economic release (CPI, a rate decision, jobs…) that landed today on a currency you traded. Shown only when one actually occurred — and it's a POSSIBLE driver (correlation), never proof the release caused your move.",
}


def _panel(symbols, synthetic, need_bars: int | None = None):
    if synthetic:
        return fx_data.synthetic_panel(symbols)
    # Fetch exactly the strategy's warm-up + display need (~1.6 calendar days per
    # business day). The old fixed 550-day window under-fetched min_history, which
    # silently truncated the ensemble warm-up on real data.
    days = int((need_bars or 340) * 1.6) + 20
    start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
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
        # Annualise by the curve's ACTUAL bar spacing — an hourly book (daytrader)
        # must not be annualised as if its bars were daily.
        med = s.index.to_series().diff().median()
        secs = med.total_seconds() if pd.notna(med) and med.total_seconds() > 0 else 86400.0
        ppy = min(ANNUALIZATION if secs >= 43200 else 365.25 * 86400.0 / secs, 24 * 365.25)
        out["sharpe"] = round(float((r.mean() * ppy - FX_RISK_FREE)
                                    / (r.std() * np.sqrt(ppy))), 2)
        out["vol"] = round(float(r.std() * np.sqrt(ppy)), 4)
        out["max_dd"] = round(float((s / s.cummax() - 1.0).min()), 4)
        out["win_rate"] = round(float((r > 0).mean()), 3)
    return out


def _signal_parts(panel, p):
    """ONE shared pass of the canonical weight engine for the whole page.

    Attribution, the agent scorecard and the PBO matrix all need the per-agent
    signal history — computing it once here (instead of once per consumer) is the
    dashboard's single biggest performance lever.
    """
    pool = AgentPool(max_workers=1)
    _, signals, tilts = target_weights_history(panel, p, pool=pool, return_parts=True)
    rets = fx_data.closes(panel).pct_change(fill_method=None)
    return signals, tilts, rets


def _agent_attribution(signals, tilts, rets, bars) -> dict:
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
        # [:10] → date part, so intraday-keyed trades (the daytrader book) still
        # land on the daily candles for markers, outcomes and click-to-zoom.
        tday = str(t["date"])[:10]
        i = pos_of.get(tday)
        fwd, outcome = None, "open"
        entry = t.get("price")
        if i is not None and entry and i + _OUTCOME_BARS < len(closes_arr):
            s = 1.0 if t["side"] == "BUY" else -1.0
            fwd = round(float(s * (closes_arr[i + _OUTCOME_BARS] / entry - 1.0)), 4)
            outcome = "win" if fwd > 0 else "loss"
        why = _beginner_explanation(t.get("side"), t.get("target_weight"),
                                    t.get("agents"), t.get("indicators"), sym)
        out_trades.append({"time": tday, "side": t["side"], "price": t.get("price"),
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


def _attribution_rollup(txn: dict) -> dict:
    """Aggregate the blotter's per-trade contribution (Δw × move-since × equity)
    by pair, by long/short side, and by regime — 'where the money is made/lost'."""
    by_pair: dict[str, dict] = {}
    by_side = {"long": 0.0, "short": 0.0}
    by_regime: dict[str, float] = {}
    for r in txn.get("rows", []):
        pnl = r.get("pnl")
        if pnl is None:
            continue
        p = by_pair.setdefault(r["pair"], {"pnl": 0.0, "cost": 0.0, "trades": 0})
        p["pnl"] += pnl
        p["cost"] += r.get("cost", 0.0) or 0.0
        p["trades"] += 1
        tgt = r.get("target") or 0.0
        side = "long" if (tgt > 0 or (tgt == 0 and (r.get("dweight") or 0) < 0)) else "short"
        by_side[side] += pnl
        reg = r.get("regime") or "—"
        by_regime[reg] = by_regime.get(reg, 0.0) + pnl
    by_pair = {k: {"pnl": round(v["pnl"], 2), "cost": round(v["cost"], 2),
                   "trades": v["trades"]}
               for k, v in sorted(by_pair.items(), key=lambda kv: -abs(kv[1]["pnl"]))}
    return {"by_pair": by_pair,
            "by_side": {k: round(v, 2) for k, v in by_side.items()},
            "by_regime": {k: round(v, 2) for k, v in by_regime.items()}}


def _max_streak(mask) -> int:
    best = cur = 0
    for v in mask:
        cur = cur + 1 if bool(v) else 0
        best = max(best, cur)
    return int(best)


def _trade_stats(state: dict) -> dict:
    """Trade-quality stats from the daily equity curve + turnover from the trades."""
    trades = state.get("trades", [])
    out = {"trades": len(trades),
           "turnover": round(sum(abs(float(t.get("delta_weight") or 0.0)) for t in trades), 2)}
    eqh = state.get("equity_history", [])
    if len(eqh) < 3:
        return out
    r = pd.Series([float(e) for _, e in eqh], dtype=float).pct_change().dropna()
    gains, losses = r[r > 0], r[r < 0]
    out.update({
        "days": int(len(r)),
        "profit_factor": round(float(gains.sum() / abs(losses.sum())), 2) if losses.sum() else None,
        "avg_win": round(float(gains.mean()), 4) if len(gains) else None,
        "avg_loss": round(float(losses.mean()), 4) if len(losses) else None,
        "expectancy": round(float(r.mean()), 4),
        "best": round(float(r.max()), 4), "worst": round(float(r.min()), 4),
        "win_streak": _max_streak(r > 0), "loss_streak": _max_streak(r < 0),
    })
    return out


def _conviction(state: dict) -> list:
    """Today's per-pair ensemble tilt (−1..+1) for a one-glance conviction heatmap."""
    out = []
    for sym, d in (state.get("decisions", {}) or {}).items():
        if not isinstance(d, dict):
            continue
        out.append({"pair": sym, "tilt": round(float(d.get("tilt", 0.0) or 0.0), 3),
                    "weight": round(float(d.get("weight", 0.0) or 0.0), 3),
                    "regime": d.get("regime")})
    out.sort(key=lambda x: -abs(x["tilt"]))
    return out


def _agent_daily_matrix(signals, rets, bars) -> pd.DataFrame:
    """T×agents matrix of each agent's standalone daily return (for PBO).
    Reuses the shared `_signal_parts` pass — no second weight-engine run."""
    if not signals:
        return pd.DataFrame()
    names = list(next(iter(signals.values())).columns)
    cols = {name: pd.DataFrame({s: signals[s][name].shift(1) * rets[s] for s in signals})
            .mean(axis=1).tail(bars).fillna(0.0) for name in names}
    return pd.DataFrame(cols)


def _advanced_significance(signals, rets, returns_values, bars=180) -> dict:
    """Deflated Sharpe (penalised for how many agents/strategies were tried) and
    Probability of Backtest Overfitting across the agent set — completes PSR."""
    out = {"dsr": None, "dsr_trials": None, "pbo": None}
    try:
        mat = _agent_daily_matrix(signals, rets, bars)
    except Exception:
        mat = pd.DataFrame()
    r = np.asarray(returns_values, dtype=float)
    r = r[np.isfinite(r)]
    n_trials = int(mat.shape[1]) if not mat.empty else 1
    if len(r) >= 3:
        out["dsr"] = round(float(deflated_sharpe_ratio(r, max(n_trials, 1))), 3)
        out["dsr_trials"] = max(n_trials, 1)
    if not mat.empty and mat.shape[1] >= 2 and mat.shape[0] >= 4:
        try:
            out["pbo"] = round(float(pbo(mat.values)), 3)
        except Exception:
            out["pbo"] = None
    return out


def _daily_summary(state: dict) -> dict | None:
    """Turn the book's stored daily P&L snapshot into a narratable summary: the
    drivers/detractors (with each pair's actual move + the agents/regime that held
    it) and the day's broad currency strength, derived from the cross-rates moved —
    NOT from news (we describe what the market *did*, not an invented *why*)."""
    dy = state.get("daily")
    if not dy or not dy.get("date"):
        return None
    dec = state.get("decisions", {}) or {}
    by = dy.get("by_pair", [])

    # Broad currency strength from the day's moves: a pair up = base stronger vs
    # quote. Average each currency's appreciation across the pairs it appears in.
    strg: dict[str, float] = {}
    cnt: dict[str, int] = {}
    for c in by:
        try:
            pr = get_pair(c["pair"])
        except KeyError:
            continue
        mv = float(c.get("move") or 0.0)
        strg[pr.base] = strg.get(pr.base, 0.0) + mv
        strg[pr.quote] = strg.get(pr.quote, 0.0) - mv
        cnt[pr.base] = cnt.get(pr.base, 0) + 1
        cnt[pr.quote] = cnt.get(pr.quote, 0) + 1
    strength = {k: round(v / max(cnt.get(k, 1), 1), 5) for k, v in strg.items()}

    drivers = []
    for c in by:
        d = dec.get(c["pair"]) or {}
        drivers.append({**c, "regime": d.get("regime"), "agents": d.get("agents")})
    drivers.sort(key=lambda c: -abs(c.get("contrib") or 0.0))
    return {
        "date": dy["date"], "net_pct": dy.get("net_pct"), "net_aud": dy.get("net_aud"),
        "pnl_pct": dy.get("pnl_pct"), "carry_pct": dy.get("carry_pct"),
        "cost_pct": dy.get("cost_pct"), "halted": dy.get("halted"),
        "currency": state.get("currency", "AUD"),
        "drivers": drivers,
        "strength": dict(sorted(strength.items(), key=lambda kv: -kv[1])),
    }


def _with_catalysts(daily: dict | None) -> dict | None:
    """Attach scheduled-news catalysts to the daily summary — but ONLY real,
    high-impact releases that hit a currency actually traded that day. Empty (and
    silent) without a NEWS_API_KEY / network / matching event. Correlation only."""
    if not daily or not daily.get("date"):
        return daily
    curs = set()
    for c in daily.get("drivers", [])[:6]:
        try:
            pr = get_pair(c["pair"])
        except KeyError:
            continue
        curs |= {pr.base, pr.quote} & news.FIAT
    daily["catalysts"] = news.economic_events(sorted(curs), daily["date"]) if curs else []
    return daily


def build_payload(account, synthetic=False, bars=180):
    state = load_state(account)
    symbols = state.get("symbols", [])
    p = profile(state.get("profile", "balanced"))
    # Bounded compute: everything below only needs the display window plus the
    # indicator/ensemble warm-up (min_history + bars) — the same fast-trim
    # property compute_targets(fast=True) relies on. Fetch and trim to exactly
    # that, so page-build time stays flat no matter how much history accumulates.
    need = min_history(p) + bars + 10
    panel = _panel(symbols, synthetic, need_bars=need)
    panel = {s: df.tail(need) for s, df in panel.items()}
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

    txn = _transactions(state, panel)
    risk = _risk_costs(state, p)
    book_rets = (pd.Series([float(v) for _, v in eqh], dtype=float)
                 .pct_change().dropna().values) if len(eqh) >= 2 else []
    # ONE weight-engine pass shared by attribution, the scorecard and PBO.
    signals, tilts, rets = _signal_parts(panel, p)
    risk.update(_advanced_significance(signals, rets, book_rets, bars))  # DSR + PBO alongside PSR

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
        "transactions": txn,
        "risk": risk,
        "daily": _with_catalysts(_daily_summary(state)),
        "pnl_attribution": _attribution_rollup(txn),
        "trade_stats": _trade_stats(state),
        "conviction": _conviction(state),
        "attribution": _agent_attribution(signals, tilts, rets, bars),
        "glossary": GLOSSARY, "agent_roles": _AGENT_ROLES,
        "pairs": pairs, "data": data,
        "books": sorted(list_accounts()),
    }


# ---------------------------------------------------------------------------
# Per-book HTML
# ---------------------------------------------------------------------------
_PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FX Paper · __ACCOUNT__</title>
<script src="__LWC__"></script>
<style>
:root{--bg:#0d1117;--panel:#161b22;--bd:#2b313b;--fg:#e6edf3;--mut:#8b949e;
--up:#26a69a;--dn:#ef5350;--accent:#58a6ff;--amber:#f5a623;
--mono:ui-monospace,"JetBrains Mono","SF Mono",Menlo,Consolas,"Liberation Mono",monospace}
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
.card{border:1px solid var(--bd);border-radius:12px;background:var(--panel);padding:1rem;
  display:flex;flex-direction:column;min-width:0;box-shadow:0 1px 2px rgba(0,0,0,.35);
  transition:border-color .15s ease}
.card:hover{border-color:#3d4654}
.card h2{margin:0 0 .6rem;font-size:.82rem;letter-spacing:.02em;color:#c9d1d9}
.acc-amber{color:var(--amber);border-bottom-color:var(--amber)}
.flag{border-left:2px solid var(--amber)}
.card>.fill{flex:1;min-height:0}
#eqchart{flex:1;min-height:300px}#chart{min-height:440px}
#ddchart,#costchart{flex:1;min-height:200px}
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
/* 12-col equal-height grid: rows sum to 12 so there are no trailing gaps */
.cards{display:grid;grid-template-columns:repeat(12,1fr);gap:1rem;align-items:stretch}
.c12{grid-column:span 12}.c8{grid-column:span 8}.c6{grid-column:span 6}.c4{grid-column:span 4}
.span2{grid-column:span 8}
@media(max-width:900px){.c8,.c6,.c4,.span2{grid-column:span 12}}
.kpis{display:flex;gap:1rem;flex-wrap:wrap;margin-top:.8rem}
.kpi{flex:1 1 120px;border:1px solid var(--bd);border-radius:10px;background:var(--panel);padding:.6rem .8rem}
.kpi .v{font-size:1.1rem;font-weight:600}.kpi .k{color:var(--mut);font-size:.66rem;text-transform:uppercase;margin-top:.15rem}
/* terminal numerics: monospace + tabular figures (pro-desk feel, no jitter) */
.stat .v,.kpi .v,.metrics,.val,.row,table.txn,#riskstats .v{font-variant-numeric:tabular-nums;
  font-feature-settings:"tnum" 1;font-family:var(--mono)}
.stat .v,.kpi .v,#riskstats .v{letter-spacing:-.01em}
section{padding:1rem 1.5rem 1.25rem}
.plain{margin:0 0 1rem;padding:.7rem .9rem;border:1px solid var(--bd);border-left:3px solid var(--accent);
  border-radius:8px;background:#10151c;color:#c9d1d9;font-size:.86rem;line-height:1.55;max-width:1000px}
.plain b{color:var(--fg)}.plain .q{color:var(--amber);font-weight:600}
.heat{display:flex;flex-wrap:wrap;gap:.4rem}
.hc{padding:.4rem .55rem;border-radius:8px;border:1px solid var(--bd);font-size:.72rem;
  font-family:var(--mono);min-width:82px;text-align:center;line-height:1.35}
.hc b{font-size:.85rem}
/* verdict banner */
.verdict{display:flex;align-items:center;gap:.7rem;padding:.6rem 1.5rem;font-size:.86rem;
  border-bottom:1px solid var(--bd);line-height:1.4}
.verdict .vchip{font-weight:600;white-space:nowrap;font-size:.78rem;font-family:var(--mono)}
.v-amber{background:#1d160a;color:#f0d8a8}.v-green{background:#0c1a12;color:#a6e9c9}.v-red{background:#1d0f0f;color:#f3b6b0}
/* period selector + chart crosshair readout + sparkline */
.periods{font-weight:400;font-size:.7rem}
.pbtn{background:transparent;border:1px solid var(--bd);color:var(--mut);border-radius:6px;
  padding:.1rem .45rem;margin-left:.25rem;cursor:pointer;font-size:.7rem;font-family:var(--mono)}
.pbtn.on{border-color:var(--accent);color:var(--accent)}
.cread{font-size:.74rem;color:var(--mut);min-height:1.15em;margin:-.1rem 0 .35rem;font-family:var(--mono)}
.cread b{color:var(--fg)}.spark{display:block;margin-top:.25rem}
/* colourblind-safe mode: blue/orange replaces green/red (persisted) */
body.cb{--up:#4c9be8;--dn:#f5a623}
/* interaction affordances */
[data-pair]{cursor:pointer}
.row [data-pair]:hover,.hc:hover{color:var(--accent);border-color:var(--accent)}
table.txn th.sortable{cursor:pointer;user-select:none}
table.txn th.sortable:hover{color:var(--fg)}
.navr{margin-left:auto;display:flex;gap:.6rem;align-items:center}
.navr .bk{color:var(--mut)}.navr .bk.cur{color:var(--accent);border-bottom:1px solid var(--accent)}
.chipbtn{background:transparent;border:1px solid var(--bd);color:var(--mut);border-radius:999px;
  padding:.15rem .6rem;cursor:pointer;font-size:.75rem}
.chipbtn:hover{border-color:var(--accent);color:var(--accent)}
#ago{font-size:.75rem;color:var(--mut);font-family:var(--mono)}
/* daily summary */
.dhead{font-size:1.05rem;margin:.1rem 0 .2rem}.dhead b{font-family:var(--mono)}
.dbreak{color:var(--mut);font-size:.78rem;font-family:var(--mono);margin-bottom:.7rem}
.dgrid{display:grid;grid-template-columns:1fr 1fr;gap:1rem}@media(max-width:760px){.dgrid{grid-template-columns:1fr}}
.dcol h3{margin:.2rem 0 .4rem;font-size:.78rem;color:var(--mut);text-transform:uppercase}
.drow{display:flex;gap:.5rem;align-items:baseline;font-size:.82rem;padding:.3rem 0;border-bottom:1px solid #21262d;line-height:1.4}
.drow .amt{font-family:var(--mono);min-width:74px;text-align:right}
.dwhy{color:var(--mut);font-size:.78rem}.dmkt{margin-top:.7rem;font-size:.82rem;line-height:1.5}
</style></head><body>
<div class="nav"><a href="index.html">← All books</a><a href="how.html">📖 How it works — start here</a>
  <span class="navr"><span id="books"></span><span id="ago"></span>
    <button class="chipbtn" id="cbtoggle" title="Colourblind-safe colours (blue/orange instead of green/red)">◑ colours</button></span></div>
<header>
  <h1>FX Paper Book · <span style="color:var(--accent)">__ACCOUNT__</span>
    <span class="badge">__PROFILE__</span>__HALT__</h1>
  <div class="sub">base __CCY__ · updated __UPDATED__ · candlesticks, performance vs buy-and-hold, and a plain-English reason + outcome for every trade. Hover any underlined word for its meaning.</div>
  <div class="stats" id="stats"></div>
</header>
<div class="verdict" id="verdict"></div>

<div class="subnav">
  <a href="#today">Today</a>
  <a href="#overview">Overview</a>
  <a href="#risk">Risk &amp; costs</a>
  <a href="#attrib">Attribution</a>
  <a href="#pairs">Pair explorer</a>
  <a href="#txns">Transactions</a>
</div>

<section id="today">
  <div class="band">Daily summary <span class="h">what drove today's profit &amp; loss</span></div>
  <p class="plain"><span class="q">In plain English:</span> a once-a-day debrief — <b>how much you made or lost</b>,
    <b>which positions drove it</b> and how each one moved, and the day's <b>market backdrop</b> (which currencies
    were broadly strong or weak). When a real <b>high-impact news release</b> hit a currency you traded, it's flagged
    as a <b>possible catalyst</b> — but only if one actually happened, and always as correlation, never invented.</p>
  <div class="card" id="dailycard"></div>
</section>

<section id="overview">
  <div class="band">Overview <span class="h">equity vs buy-and-hold · performance · positions</span></div>
  <p class="plain"><span class="q">In plain English:</span> the big chart is <b>how much your account is worth over time</b>
    (blue) versus the lazy alternative of just buying a bit of everything and holding it (grey) — both start at 100,
    so if blue is above grey, the strategy is adding something. Beside it: your <b>scorecard</b>, <b>what you hold
    right now</b>, and which <b>"agents"</b> (small built-in strategies) have been helping lately. This is paper
    money — practice, not real funds.</p>
  <div class="cards">
    <div class="card c8"><h2><span class="tip" data-tip="__T_BENCH__">Equity vs buy-and-hold</span> <span class="muted" style="font-weight:400">(both start at 100)</span> <span class="periods" id="eqperiod"></span></h2>
      <div class="cread" id="eqread"></div><div id="eqchart"></div></div>
    <div class="card c4"><h2>Performance <span class="muted" style="font-weight:400">vs buy &amp; hold</span></h2><div id="metrics" class="metrics"></div></div>
    <div class="card c6"><h2>Open positions <span class="muted" style="font-weight:400">(signed % of equity)</span></h2><div id="positionscard"></div></div>
    <div class="card c6"><h2><span class="tip" data-tip="__T_SCORE__">Agent scorecard</span> <span class="muted" style="font-weight:400">(this window)</span></h2><div id="agentcard"></div></div>
  </div>
</section>

<section id="risk">
  <div class="band">Risk, costs &amp; significance <span class="h">is the edge real after costs &amp; luck?</span></div>
  <p class="plain"><span class="q">In plain English:</span> before trusting any gain, ask <b>"is this skill or just luck,
    and what did it cost?"</b> With only a few weeks of data a small profit is almost always noise — the
    <b>"is it luck?"</b> box estimates the odds it's real and how long you'd need to wait to know. The
    <b>drawdown</b> chart shows your worst dips, the <b>cost</b> chart shows how much the spread (the dealer's
    cut on every trade) is quietly eating, and <b>exposure</b> shows which currencies you're really betting on
    once the pairs are unpacked.</p>
  <div class="cards">
    <div class="card c8 flag"><h2>Costs &amp; <span class="tip acc-amber" data-tip="__T_PSR__">is it luck?</span></h2>
      <div class="stats" id="riskstats"></div>
      <div id="sigtext" class="why" style="margin-top:.7rem"></div></div>
    <div class="card c4"><h2><span class="tip" data-tip="__T_EXP__">Net currency exposure</span></h2><div id="exposurecard"></div></div>
    <div class="card c6"><h2><span class="tip" data-tip="__T_DD__">Drawdown (underwater)</span></h2><div id="ddchart"></div></div>
    <div class="card c6"><h2><span class="tip" data-tip="__T_WEDGE__">Costs vs gross P&amp;L</span></h2><div id="costchart"></div></div>
  </div>
</section>

<section id="attrib">
  <div class="band">Attribution &amp; signals <span class="h">where the P&amp;L comes from · what the system thinks now</span></div>
  <p class="plain"><span class="q">In plain English:</span> <b>where did the money come from?</b> The heatmap shows
    what the system wants <b>right now</b> for each pair — green = bet it rises (long), red = bet it falls (short),
    brighter = stronger conviction. Below, the P&amp;L is split by pair, by up-bets vs down-bets, and by market mood
    (trending vs choppy). <b>Trade quality</b> sums up whether the wins outweigh the losses (profit factor &gt; 1 =
    yes) and how much you're trading (turnover).</p>
  <div class="cards">
    <div class="card c12"><h2><span class="tip" data-tip="__T_CONV__">Conviction heatmap</span> <span class="muted" style="font-weight:400">today's ensemble tilt per pair (−1…+1)</span></h2>
      <div id="conviction" class="heat"></div></div>
    <div class="card c6"><h2><span class="tip" data-tip="__T_ATTR__">P&amp;L by pair</span> <span class="muted" style="font-weight:400">contribution-since</span></h2>
      <div id="pnlpair"></div></div>
    <div class="card c6"><h2>Trade quality <span class="muted" style="font-weight:400">daily-return stats + turnover</span></h2>
      <div class="stats" id="tradestats"></div>
      <div id="sideregime" style="margin-top:.8rem"></div></div>
  </div>
</section>

<section id="pairs">
  <div class="band">Pair explorer <span class="h">candlesticks · today's read · the reason for every trade</span></div>
  <p class="plain"><span class="q">In plain English:</span> pick one instrument and look closely. Each
    <b>candle</b> is one day's price (green = finished up, red = down); the orange and blue lines are short- and
    long-term <b>averages</b> the agents watch. Arrows mark where we <b>bought or sold</b>, and the journal gives a
    <b>plain-English reason for every trade</b> plus whether it worked — built so you can learn how each call was made.</p>
  <div class="tabs" id="tabs"></div>
  <div class="wrap">
    <div><div class="cread" id="pairread"></div><div id="chart"></div></div>
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
  <p class="plain"><span class="q">In plain English:</span> the full receipt — <b>every trade</b> with the price
    we got, the <b>bid/ask</b> (sell/buy prices) and the <b>spread</b> between them, the dollar <b>size</b>, the
    <b>cost</b> you paid, and how it's done <b>since</b>. Hover any column heading for what it means; type a pair in
    the box to filter.</p>
  <div class="card">
    <h2>Full blotter <span class="muted" style="font-weight:400" id="txnsub"></span>
      <button class="chipbtn" id="csvbtn" style="float:right">⬇ CSV</button></h2>
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
// LWC time normaliser: intraday keys ("YYYY-MM-DD HH:MM", e.g. the daytrader
// book's hourly equity) become UNIX timestamps; daily date strings pass through.
const toT = s => (typeof s==='string'&&s.includes(' '))?Math.floor(Date.parse(s.replace(' ','T')+':00Z')/1000):s;
const fmtT = t => typeof t==='number'?new Date(t*1000).toISOString().slice(0,16).replace('T',' ')
  :(t&&t.year?`${t.year}-${String(t.month).padStart(2,'0')}-${String(t.day).padStart(2,'0')}`:t);
let chart;

function sparkline(curve,w=110,h=26){
  const v=(curve||[]).map(p=>p.value); if(v.length<2) return '';
  const mn=Math.min(...v),mx=Math.max(...v),rng=(mx-mn)||1;
  const pts=v.map((y,i)=>`${(i/(v.length-1)*w).toFixed(1)},${(h-((y-mn)/rng)*h).toFixed(1)}`).join(' ');
  const up=v[v.length-1]>=v[0],col=up?'var(--up)':'var(--dn)';
  return `<svg class=spark width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">`+
    `<polyline points="${pts}" fill="none" stroke="${col}" stroke-width="1.5"/></svg>`;
}

(function(){
  const m=DASH.book_metrics||{};
  const items=[["Equity",DASH.equity.toLocaleString()+" "+DASH.currency,null],
    ["Return",pct(DASH.ret),"Benchmark"],["Sharpe",(m.sharpe??"–"),"Sharpe"],
    ["Max drawdown",(m.max_dd!=null?pct(m.max_dd):"–"),"Max drawdown"],
    ["Gross lev.",DASH.gross+"x","Gross leverage"],["Trades",DASH.trades_total,null]];
  document.getElementById('stats').innerHTML=items.map(([k,v,g])=>
    `<div class=stat><div class=v>${v}</div><div class=k>${g?tip(k,g):k}</div></div>`).join('');
  // sparkline under the Equity tile
  const eqStat=document.querySelector('#stats .stat');
  if(eqStat){const s=sparkline(DASH.book_curve); if(s)eqStat.insertAdjacentHTML('beforeend',s);}
})();

(function(){
  const el=document.getElementById('verdict'); if(!el) return;
  const rk=DASH.risk||{}, ret=DASH.ret, n=rk.n_obs||0, retTxt=pct(ret);
  let chip,cls,msg;
  if(DASH.halted){chip='⛔ Risk-halted';cls='v-red';
    msg=`The drawdown breaker tripped — the book is flat and cooling off. Return ${retTxt}.`;}
  else if(rk.psr==null||n<10){chip='🟡 Too early to tell';cls='v-amber';
    msg=`${retTxt} over ${n} day${n===1?'':'s'} — far too little data to mean anything yet. Treat it as noise, not skill.`;}
  else if(rk.psr>=0.95){chip=ret>=0?'🟢 Edge (so far)':'🔴 Losing edge';cls=ret>=0?'v-green':'v-red';
    msg=`${retTxt} with PSR ${(rk.psr*100).toFixed(0)}% over ${n} days — statistically distinguishable from luck. Keep watching; one window isn't proof.`;}
  else{const months=rk.min_track_days?Math.round(rk.min_track_days/21):null;chip='🟡 Not proven';cls='v-amber';
    msg=`${retTxt} over ${n} days · PSR ${(rk.psr*100).toFixed(0)}% — still indistinguishable from luck${months?`; ~${months} months of data needed to tell`:''}. Treat as noise for now.`;}
  el.className='verdict '+cls;
  el.innerHTML=`<span class=vchip>${chip}</span><span>${msg}</span>`;
})();

(function(){
  const el=document.getElementById('dailycard'); if(!el) return;
  const d=DASH.daily;
  if(!d){el.innerHTML='<div class=muted>Fills in after the first full trading day.</div>';return;}
  const ccy=d.currency, gain=(d.net_aud||0)>=0;
  const aud=v=>(v<0?'-':'')+Math.abs(v).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});
  const head=`<div class=dhead>On <b>${d.date}</b>, the book <b style="color:${gain?'var(--up)':'var(--dn)'}">${gain?'made':'lost'} ${aud(Math.abs(d.net_aud))} ${ccy}</b> (${pct(d.net_pct)}).</div>`;
  const brk=`<div class=dbreak>market P&amp;L ${pct(d.pnl_pct)} · carry ${pct(d.carry_pct)} · costs ${pct(d.cost_pct)}${d.halted?' · ⛔ risk-halted':''}</div>`;
  const agentsTop=ag=>{if(!ag) return ''; const e=Object.entries(ag).sort((a,b)=>Math.abs(b[1])-Math.abs(a[1])).slice(0,2).map(x=>x[0]); return e.length?` · held by ${e.join(' + ')}`:'';};
  const row=c=>{const help=c.contrib>=0;
    const why=`${c.pair} ${c.move>=0?'rose':'fell'} ${Math.abs(c.move*100).toFixed(2)}%${c.regime?` (${c.regime})`:''}${agentsTop(c.agents)}`;
    return `<div class=drow><span class="amt ${help?'win':'loss'}">${pct(c.contrib)}</span>`+
      `<span><b>${(c.weight>=0?'LONG':'SHORT')} ${c.pair}</b> <span class=dwhy>— ${why}</span></span></div>`;};
  const drv=(d.drivers||[]).filter(c=>Math.abs(c.contrib)>1e-9);
  const winners=drv.filter(c=>c.contrib>0).slice(0,5), losers=drv.filter(c=>c.contrib<0).slice(0,5);
  const col=(t,rows)=>`<div class=dcol><h3>${t}</h3>${rows.length?rows.map(row).join(''):'<div class=muted>none</div>'}</div>`;
  const st=Object.entries(d.strength||{});
  let mkt='';
  if(st.length>=2){const top=st[0],bot=st[st.length-1];
    mkt=`<div class=dmkt><b>Market backdrop:</b> the <b>${top[0]}</b> was broadly the day's strongest (avg ${pct(top[1])} across its pairs) and the <b>${bot[0]}</b> the weakest (${pct(bot[1])}). <span class=muted>Derived from how the cross-rates moved.</span></div>`;}
  // Scheduled-news catalysts — shown ONLY when a real high-impact release hit a
  // currency traded today. Correlation, never claimed causation.
  let cat='';
  const ev=d.catalysts||[];
  if(ev.length){
    const items=ev.map(e=>{const p=[e.actual!=null?`actual ${e.actual}`:'',e.estimate!=null?`est ${e.estimate}`:'',e.previous!=null?`prev ${e.previous}`:''].filter(Boolean).join(' · ');
      return `<div class=drow><span class=dwhy><b>${e.currency}</b> · ${e.event||'release'}${p?` (${p})`:''}</span></div>`;}).join('');
    cat=`<div class=dmkt><b>Possible catalysts</b> ${tip('— correlation, not proof','Catalyst')}: high-impact scheduled releases today on currencies you traded — a <i>possible</i> reason for the moves above.${items}</div>`;
  }
  el.innerHTML=head+brk+`<div class=dgrid>${col('Drivers — helped',winners)}${col('Detractors — hurt',losers)}</div>`+mkt+cat;
})();

function bars(obj, hi){
  if(!obj||!Object.keys(obj).length) return '<div class="muted">no data</div>';
  const max=Math.max(0.0001,...Object.values(obj).map(v=>Math.abs(v)));
  return Object.entries(obj).map(([n,v])=>{
    const w=Math.min(Math.abs(v)/max,1)*50,left=v>=0?50:50-w,col=v>=0?'var(--up)':'var(--dn)';
    const em=(hi&&hi.includes(n))?'font-weight:700;color:var(--fg)':'';
    // pair names are click-through links into the Pair explorer
    const isPair=(DASH.pairs||[]).includes(n);
    const nm=ROLES[n]?`<span class="tip" data-tip="${ROLES[n].replace(/"/g,'&quot;')}">${n}</span>`
             :(isPair?`<span data-pair="${n}" title="open in Pair explorer">${n}</span>`:n);
    return `<div class=row><div class=name style="${em}">${nm}</div>`+
      `<div class=bar><i style="left:${left}%;width:${w}%;background:${col}"></i>`+
      `<i style="left:50%;width:1px;background:#555"></i></div>`+
      `<div class=val style="color:${col}">${pct(v)}</div></div>`;}).join('');
}

let curSym=null;
function goPair(sym){
  if(!(DASH.pairs||[]).includes(sym)) return;
  showPair(sym);
  document.getElementById('pairs').scrollIntoView({behavior:'smooth'});
}
document.addEventListener('click',e=>{
  const t=e.target.closest('[data-pair]'); if(t) goPair(t.dataset.pair);});
document.addEventListener('keydown',e=>{
  if(e.target.tagName==='INPUT'||!curSym||!(DASH.pairs||[]).length) return;
  const i=DASH.pairs.indexOf(curSym);
  if(e.key==='ArrowRight') showPair(DASH.pairs[(i+1)%DASH.pairs.length]);
  else if(e.key==='ArrowLeft') showPair(DASH.pairs[(i-1+DASH.pairs.length)%DASH.pairs.length]);});

(function(){
  // header: sibling-book switcher, live "updated ago", colourblind-safe toggle
  const bk=document.getElementById('books');
  if(bk)bk.innerHTML=(DASH.books||[]).map(b=>b===DASH.account
    ?`<span class="bk cur">${b}</span>`:`<a class=bk href="fx_${b}.html">${b}</a>`).join(' ');
  const ago=document.getElementById('ago');
  const upd=Date.parse((DASH.updated||'').replace(' UTC','Z').replace(' ','T'));
  function tick(){if(!ago||!upd)return;const m=Math.max(0,Math.round((Date.now()-upd)/60000));
    ago.textContent=m<60?`updated ${m}m ago`:`updated ${Math.round(m/60)}h ago`;
    ago.style.color=m>1800?'var(--dn)':'var(--mut)';}   // stale >30h = red
  tick(); setInterval(tick,30000);
  const cb=document.getElementById('cbtoggle');
  if(localStorage.getItem('cb')==='1')document.body.classList.add('cb');
  if(cb)cb.onclick=()=>{const on=document.body.classList.toggle('cb');
    localStorage.setItem('cb',on?'1':'0');};
})();

(function(){
  document.getElementById('agentcard').innerHTML=bars(DASH.attribution,["ensemble","buy&hold"]);
  const pos=DASH.positions||[];
  document.getElementById('positionscard').innerHTML=pos.length?bars(Object.fromEntries(pos.map(p=>[p.sym,p.w]))):'<div class="muted">Flat — no open positions right now.</div>';
})();

(function(){
  // Equity chart + period selector (1W/1M/3M/ALL) + crosshair readout + metrics.
  const el=document.getElementById('eqchart'), mEl=document.getElementById('metrics');
  const BOOK=DASH.book_curve||[], BENCH=DASH.bench_curve||[], RF=0.035, ANN=252;
  const ROWS=[["Return","total_return",true,"Benchmark"],["Sharpe","sharpe",false,"Sharpe"],
    ["Volatility","vol",true,"Volatility"],["Max drawdown","max_dd",true,"Max drawdown"],["Win rate","win_rate",true,"Win rate"]];
  function compute(series){
    const out={}; if(series.length<2) return out;
    const v=series.map(p=>p.value); out.total_return=v[v.length-1]/v[0]-1;
    const r=[]; for(let i=1;i<v.length;i++) r.push(v[i]/v[i-1]-1);
    if(r.length>=5){const mean=r.reduce((a,b)=>a+b,0)/r.length;
      const sd=Math.sqrt(r.reduce((a,b)=>a+(b-mean)**2,0)/r.length);
      if(sd>0){out.sharpe=+((mean*ANN-RF)/(sd*Math.sqrt(ANN))).toFixed(2); out.vol=+(sd*Math.sqrt(ANN)).toFixed(4);}
      let peak=-1e18,dd=0; for(const x of v){peak=Math.max(peak,x); dd=Math.min(dd,x/peak-1);} out.max_dd=+dd.toFixed(4);
      out.win_rate=+(r.filter(x=>x>0).length/r.length).toFixed(3);}
    return out;
  }
  function renderMetrics(b,k){const cell=(m,key,isP)=>{const x=m[key];return x==null?"–":(isP?pct(x):x);};
    mEl.innerHTML=`<div class=hd></div><div class=hd>Book</div><div class=hd>Buy&amp;Hold</div>`+
      ROWS.map(([l,key,isP,g])=>`<div>${tip(l,g)}</div><div>${cell(b,key,isP)}</div><div class=muted>${cell(k,key,isP)}</div>`).join('');}
  if(!BENCH.length&&!BOOK.length){el.innerHTML='<p class=muted style="padding:1rem">This fills in as the book trades over the coming days.</p>';renderMetrics({},{});return;}
  const toDate=t=>new Date(String(t).replace(' ','T'));
  const cut=(s,days)=>{if(!days||!s.length) return s.slice(); const co=toDate(s[s.length-1].time).getTime()-days*864e5; return s.filter(p=>toDate(p.time).getTime()>=co);};
  const rebase=s=>{if(!s.length) return []; const b=s[0].value||1; return s.map(p=>({time:p.time,value:+(100*p.value/b).toFixed(4)}));};
  const c=LightweightCharts.createChart(el,{layout:{background:{color:'#161b22'},textColor:'#e6edf3'},
    grid:{vertLines:{color:'#21262d'},horzLines:{color:'#21262d'}},rightPriceScale:{borderColor:'#30363d'},timeScale:{borderColor:'#30363d'},autoSize:true,crosshair:{mode:0}});
  const benchS=c.addLineSeries({color:'#8b949e',lineWidth:1,title:'Buy&Hold'});
  const bookS=c.addLineSeries({color:'#58a6ff',lineWidth:2,title:'Book'});
  const lwc=s=>s.map(p=>({time:toT(p.time),value:p.value}));
  function apply(days){const bk=rebase(cut(BOOK,days)),bh=rebase(cut(BENCH,days));
    benchS.setData(lwc(bh)); bookS.setData(lwc(bk)); c.timeScale().fitContent(); renderMetrics(compute(bk),compute(bh));}
  const periods=[["1W",7],["1M",30],["3M",90],["ALL",0]], pe=document.getElementById('eqperiod');
  pe.innerHTML=periods.map(([l,d])=>`<button class="pbtn${d===0?' on':''}" data-d="${d}">${l}</button>`).join('');
  pe.querySelectorAll('.pbtn').forEach(btn=>btn.onclick=()=>{pe.querySelectorAll('.pbtn').forEach(b=>b.classList.remove('on'));btn.classList.add('on');apply(+btn.dataset.d||null);});
  const rd=document.getElementById('eqread');
  c.subscribeCrosshairMove(prm=>{
    if(!prm.time||!prm.seriesData){rd.innerHTML='';return;}
    const t=fmtT(prm.time);
    const bv=prm.seriesData.get(bookS),kv=prm.seriesData.get(benchS);
    rd.innerHTML=`<span class=muted>${t}</span> · Book <b>${bv?bv.value.toFixed(2):'–'}</b> · B&amp;H <span class=muted>${kv?kv.value.toFixed(2):'–'}</span>`;});
  apply(null);
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
  if(rk.dsr!=null||rk.pbo!=null){
    const dsrTxt=rk.dsr!=null?`${tip('Deflated Sharpe','DSR')} ${(rk.dsr*100).toFixed(0)}% <span class=muted>(penalised for ~${rk.dsr_trials} strategies tried)</span>`:'';
    const pboTxt=rk.pbo!=null?`${tip('PBO','PBO')} ${(rk.pbo*100).toFixed(0)}% <span class=muted>overfit risk</span>`:'';
    sig+=`<div style="margin-top:.5rem">${[dsrTxt,pboTxt].filter(Boolean).join(' · ')}</div>`;}
  const st=document.getElementById('sigtext'); if(st)st.innerHTML=sig;
  const exp=rk.exposure||{}, ec=document.getElementById('exposurecard');
  if(ec)ec.innerHTML=Object.keys(exp).length?bars(exp):'<div class=muted>Flat.</div>';
  const mk=el=>LightweightCharts.createChart(el,{layout:{background:{color:'#161b22'},textColor:'#e6edf3'},grid:{vertLines:{color:'#21262d'},horzLines:{color:'#21262d'}},rightPriceScale:{borderColor:'#30363d'},timeScale:{borderColor:'#30363d'},autoSize:true});
  const dd=rk.drawdown||[], de=document.getElementById('ddchart');
  if(de&&dd.length){const ch=mk(de);ch.addAreaSeries({lineColor:'#ef5350',topColor:'rgba(239,83,80,.0)',bottomColor:'rgba(239,83,80,.35)',lineWidth:2}).setData(dd.map(d=>({time:toT(d.time),value:+(d.value*100).toFixed(3)})));ch.timeScale().fitContent();}
  else if(de)de.innerHTML='<p class=muted style="padding:1rem">Fills in as the book trades.</p>';
  const cc=rk.cost_curve||[], ce=document.getElementById('costchart');
  if(ce&&cc.length>1){const ch=mk(ce);
    ch.addLineSeries({color:'#8b949e',lineWidth:1,title:'Gross (pre-cost)'}).setData(cc.map(d=>({time:toT(d.time),value:d.gross})));
    ch.addLineSeries({color:'#58a6ff',lineWidth:2,title:'Net'}).setData(cc.map(d=>({time:toT(d.time),value:d.net})));
    ch.timeScale().fitContent();}
  else if(ce)ce.innerHTML='<p class=muted style="padding:1rem">The gap between gross &amp; net = cumulative spread cost. Fills in as the book trades.</p>';
})();

(function(){
  // Conviction heatmap
  const conv=DASH.conviction||[], ch=document.getElementById('conviction');
  const heatColor=t=>{const a=Math.min(Math.abs(t),1)*0.55+0.08;
    return t>=0?`rgba(38,166,154,${a})`:`rgba(239,83,80,${a})`;};
  if(ch)ch.innerHTML=conv.length?conv.map(c=>`<div class=hc data-pair="${c.pair}" style="background:${heatColor(c.tilt)}" title="${c.regime||''} — click to open in Pair explorer">${c.pair}<br><b>${(c.tilt>=0?'+':'')+c.tilt}</b>${c.regime?` <span class=muted>${c.regime[0]}</span>`:''}</div>`).join(''):'<div class=muted>No live read yet — fills in on the next run.</div>';
  // P&L by pair
  const at=DASH.pnl_attribution||{}, pp=document.getElementById('pnlpair');
  const byPair=Object.fromEntries(Object.entries(at.by_pair||{}).map(([k,v])=>[k,v.pnl]));
  if(pp)pp.innerHTML=Object.keys(byPair).length?bars(byPair):'<div class=muted>No closed contribution yet.</div>';
  // side + regime split
  const sr=document.getElementById('sideregime');
  if(sr){const blk=(lbl,obj)=>`<div class=muted style="font-size:.7rem;margin:.4rem 0 .2rem">${lbl} (${DASH.currency})</div>`+(Object.keys(obj||{}).length?bars(obj):'<div class=muted>–</div>');
    sr.innerHTML=blk('Long vs short',at.by_side)+blk('By regime',at.by_regime);}
  // trade-quality tiles
  const ts=DASH.trade_stats||{};
  const items=[["Profit factor",ts.profit_factor??'–',"Profit factor"],["Expectancy/day",ts.expectancy!=null?pct(ts.expectancy):'–',"Expectancy"],
    ["Avg win",ts.avg_win!=null?pct(ts.avg_win):'–',null],["Avg loss",ts.avg_loss!=null?pct(ts.avg_loss):'–',null],
    ["Win streak",ts.win_streak??'–',null],["Turnover",ts.turnover??'–',"Gross leverage"]];
  const tsEl=document.getElementById('tradestats');
  if(tsEl)tsEl.innerHTML=items.map(([k,v,g])=>`<div class=stat><div class=v>${v}</div><div class=k>${g?tip(k,g):k}</div></div>`).join('');
})();

(function(){
  const T=DASH.transactions||{rows:[],totals:{}};
  const el=document.getElementById('txntable'),sub=document.getElementById('txnsub');
  if(!T.rows||!T.rows.length){el.innerHTML='<tbody><tr><td class=l>This fills in as the book trades.</td></tr></tbody>';return;}
  if(sub)sub.textContent=`(${T.shown} of ${T.count} shown, newest first)`;
  const cols=[["Time",null,"time"],["Pair",null,"pair"],["Side",null,"side"],
    ["Δw→tgt","Delta weight","dweight"],["Mid","Mid price","price"],
    ["Bid","Bid","bid"],["Ask","Ask","ask"],["Spread bps","Spread","spread_bps"],
    ["Notional","Notional","notional"],["Cost","Transaction cost","cost"],
    ["Last",null,"last"],["Move",null,"move"],["P&L since","P&L since","pnl"]];
  const money=v=>v==null?"–":(v<0?"-":"")+Math.abs(v).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});
  let sortK=null,sortDir=-1;   // click a header to sort; click again to flip
  const head=()=>'<thead><tr>'+cols.map(([lbl,g,k])=>{
    const arrow=k===sortK?(sortDir<0?' ▼':' ▲'):'';
    return `<th class="sortable${(lbl==='Pair'||lbl==='Time')?' l':''}" data-k="${k}">${g?tip(lbl,g):lbl}${arrow}</th>`;}).join('')+'</tr></thead>';
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
  let curFilter='';
  function render(){
    let rows=curFilter?T.rows.filter(r=>r.pair.toLowerCase().includes(curFilter)):T.rows.slice();
    if(sortK)rows.sort((a,b)=>{const x=a[sortK],y=b[sortK];
      if(x==null&&y==null)return 0; if(x==null)return 1; if(y==null)return -1;
      return (typeof x==='string'?String(x).localeCompare(String(y)):x-y)*sortDir;});
    el.innerHTML=head()+'<tbody>'+rows.map(rowHTML).join('')+'</tbody>'+foot;
    el.querySelectorAll('th.sortable').forEach(th=>th.onclick=()=>{
      const k=th.dataset.k;
      sortDir=(k===sortK)?-sortDir:-1; sortK=k; render();});
  }
  render();
  document.getElementById('txnsearch').addEventListener('input',e=>{curFilter=e.target.value.trim().toLowerCase();render();});
  // one-click CSV export of the full blotter (client-side, no server)
  const csvBtn=document.getElementById('csvbtn');
  if(csvBtn)csvBtn.onclick=()=>{
    const kk=cols.map(c=>c[2]);
    const csv=[kk.join(',')].concat(T.rows.map(r=>kk.map(k=>{
      const v=r[k]; return v==null?'':String(v).includes(',')?`"${v}"`:v;}).join(','))).join('\n');
    const a=document.createElement('a');
    a.href=URL.createObjectURL(new Blob([csv],{type:'text/csv'}));
    a.download=`transactions_${DASH.account}.csv`; a.click(); URL.revokeObjectURL(a.href);};
})();

function showPair(sym){
  curSym=sym;
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
  const pr=document.getElementById('pairread');
  chart.subscribeCrosshairMove(prm=>{
    const o=prm.time&&prm.seriesData?prm.seriesData.get(cs):null;
    if(!o){if(pr)pr.innerHTML='';return;}
    const t=fmtT(prm.time);
    const up=o.close>=o.open;
    if(pr)pr.innerHTML=`<span class=muted>${t}</span> · O ${fmt(o.open)} H ${fmt(o.high)} L ${fmt(o.low)} <span style="color:${up?'var(--up)':'var(--dn)'}">C <b>${fmt(o.close)}</b></span>`;});
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
        "__T_CONV__": g["Conviction"], "__T_ATTR__": g["Attribution"],
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

<h2>What every panel on your dashboard means (in plain English)</h2>
<p class="muted">Read this once and the dashboard will make sense top to bottom. Each panel also has hover-help on
the underlined words.</p>

<div class="step"><b>Equity vs buy-and-hold.</b> Your account's value over time (blue) next to the lazy
alternative — buying a little of everything and just holding (grey). Both start at 100 so they're comparable from
day one. <i>How to read it:</i> blue above grey = the strategy beat doing nothing; below = it didn't. <span
class="muted">Why it matters: beating "buy and hold" is the real bar — if a model can't, it's adding cost, not value.</span></div>

<div class="step"><b>Performance scorecard.</b> The headline numbers. <b>Return</b> = total % change. <b>Sharpe</b> =
return per unit of "bumpiness" (above ~1 is good, above 2 excellent). <b>Max drawdown</b> = the worst peak-to-trough
fall — your biggest scare. <b>Win rate</b> = share of up days. <span class="muted">Why: a high return with a huge
drawdown can be worse than a steady smaller one.</span></div>

<div class="step"><b>Open positions.</b> What you're holding right now, as a % of your account, with a + for a bet
that the price <i>rises</i> (long) and − for a bet it <i>falls</i> (short). <span class="muted">Why: your actual
risk is what you hold now, not what you held last week.</span></div>

<div class="step"><b>Agent scorecard.</b> How each mini-strategy ("agent") would have done on its own over this
window, plus the blended "ensemble" and passive "buy &amp; hold" for reference. <span class="muted">Why: shows which
kind of edge (trend, reversion, …) is working lately — but one good window is not proof.</span></div>

<div class="step"><b>Is it luck? — PSR, Deflated Sharpe, PBO.</b> The honesty panel. <b>PSR</b> (Probabilistic
Sharpe) = the chance your edge is real rather than luck, given how few days of data you have; it also tells you roughly
how many months you'd need before a result counts. <b>Deflated Sharpe</b> is stricter — it also docks you for how many
strategies were tried (try enough and one looks great by fluke). <b>PBO</b> (Probability of Backtest Overfitting) =
how often the "best" agent in testing flops in practice; lower is better. <span class="muted">Why: this is the
difference between "we found something" and "we fooled ourselves." Right now, honestly, the numbers say: too early
to tell — treat gains as noise.</span></div>

<div class="step"><b>Drawdown (underwater).</b> How far below your previous high-water mark you are, every day.
Flat at the top = at a new high; deep dips = the painful stretches. <span class="muted">Why: it shows the pain you'd
have actually lived through, not just the end result.</span></div>

<div class="step"><b>Costs vs gross P&amp;L (the "cost wedge").</b> Two lines: what you'd have made <i>before</i>
trading costs (grey) and <i>after</i> (blue). The gap between them is the <b>spread</b> — the dealer's cut you pay on
every trade — adding up. "Cost drag" is that total as a share of your gross profit. <span class="muted">Why: many
strategies look great before costs and lose after; believe the after line.</span></div>

<div class="step"><b>Net currency exposure.</b> Your pairs unpacked into the actual currencies you're long or
short (being long EUR/USD means long euros <i>and</i> short US dollars). <span class="muted">Why: several pairs can
secretly stack into one big bet — e.g. short US dollars everywhere — and this reveals it.</span></div>

<div class="step"><b>Realized vol vs target.</b> How much your account is actually swinging (annualised) compared
with the risk level the profile aims for. <span class="muted">Why: far below target = under-using your risk budget;
far above = the safety sizing isn't keeping up.</span></div>

<div class="step"><b>Conviction heatmap.</b> One coloured tile per pair showing what the system wants <i>right
now</i>, from −1 (max short, bright red) to +1 (max long, bright green). <span class="muted">Why: a single glance at
today's strongest bets and where the agents disagree (pale tiles).</span></div>

<div class="step"><b>P&amp;L attribution.</b> Where the money came from: broken down by pair, by up-bets vs
down-bets (long/short), and by market mood (trending vs choppy). <span class="muted">Why: tells you <i>what</i> is
actually working, so a lucky single pair doesn't get mistaken for a real edge.</span></div>

<div class="step"><b>Trade quality.</b> <b>Profit factor</b> = total gains ÷ total losses (above 1 = winning).
<b>Expectancy</b> = what you make on an average day. <b>Streaks</b> = longest run of up/down days. <b>Turnover</b> =
how much you trade (more trading = more cost). <span class="muted">Why: win rate alone lies; these show if the wins
are big enough to matter.</span></div>

<div class="step"><b>Transactions blotter.</b> The full receipt of every trade: the <b>mid</b> price we used, the
<b>bid</b> (price to sell) and <b>ask</b> (price to buy), the <b>spread</b> between them in basis points (1 bp =
0.01%), the dollar <b>notional</b> (size), the <b>cost</b> paid, and <b>P&amp;L since</b> (how that trade has done
since). <span class="muted">Why: total transparency — nothing about a trade is hidden.</span></div>

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
