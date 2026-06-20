"""Static candlestick + analytics dashboard for the FX paper books (GitHub Pages).

Per book it renders, all self-contained (data embedded; charts via lightweight-
charts CDN):
  • Performance: equity curve vs a 1/N buy-and-hold benchmark, drawdown, and a
    metrics panel (Sharpe, vol, max drawdown, win rate, turnover).
  • Candlesticks per instrument with EMA overlays + BUY/SELL markers.
  • An agent scorecard — how each agent (and the ensemble vs buy-and-hold) would
    have done over the window — so you can see *which* edge is working.
  • A trade journal: the rationale behind every trade *and its outcome* (the
    signed move over the next few bars, ✅/❌), so you learn whether the reasoning
    paid off.
  • Hover tooltips that define each metric (a built-in glossary).

Everything except the raw candles is computed at export time, so no change to the
live trading logic is needed.

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

from . import fx_data
from . import indicators as ind
from .agents import AgentPool
from .fx_book import list_accounts, load_state
from .fx_config import ANNUALIZATION, FX_RISK_FREE, profile
from .fx_strategy import target_weights_history
from .pairs import get_pair

_LWC = "https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"
_OUTCOME_BARS = 10   # horizon for judging whether a trade "worked"

# Plain-language definitions surfaced as hover tooltips (the learning glossary).
GLOSSARY = {
    "Sharpe": "Return per unit of risk (annual return above cash, divided by volatility). ~1 good, >2 excellent.",
    "Volatility": "How much the equity swings, annualised. Higher = bumpier.",
    "Max drawdown": "The worst peak-to-trough drop — your maximum pain.",
    "Win rate": "Share of days the book was up.",
    "Turnover": "How much the book trades; more turnover = more cost.",
    "Benchmark": "1/N buy-and-hold: hold every instrument equally, no model. The bar to beat.",
    "Agent scorecard": "How each agent's raw signal would have done alone over this window (no leverage). Ensemble = the blend; buy&hold = passive.",
    "Regime": "Trending (ADX high) vs ranging (ADX low) — which agents should be in charge.",
    "Tilt": "The ensemble's net conviction for the pair, -1 (max short) to +1 (max long).",
    "Outcome": "Signed price move over the next %d bars after the trade — did the call work?" % _OUTCOME_BARS,
}


def _panel(symbols, synthetic):
    if synthetic:
        return fx_data.synthetic_panel(symbols)
    start = (datetime.now(timezone.utc) - timedelta(days=550)).strftime("%Y-%m-%d")
    return fx_data.load_panel(symbols, start, use_cache=True)


def _curve_metrics(dates, values) -> dict:
    """Sharpe / vol / max-dd / win-rate / total return from an equity curve."""
    if not values or len(values) < 2:
        return {}
    s = pd.Series(values, index=pd.to_datetime(dates), dtype=float)
    total = float(s.iloc[-1] / s.iloc[0] - 1.0)
    out = {"total_return": round(total, 4)}
    r = s.pct_change().dropna()
    if len(r) >= 5 and r.std() > 0:
        out["sharpe"] = round(float((r.mean() * ANNUALIZATION - FX_RISK_FREE)
                                    / (r.std() * np.sqrt(ANNUALIZATION))), 2)
        out["vol"] = round(float(r.std() * np.sqrt(ANNUALIZATION)), 4)
        out["max_dd"] = round(float((s / s.cummax() - 1.0).min()), 4)
        out["win_rate"] = round(float((r > 0).mean()), 3)
    return out


def _agent_attribution(panel, p, bars) -> dict:
    """Cumulative return of each agent's raw signal over the window (equal-weight
    across pairs, no leverage), plus the ensemble and 1/N buy-and-hold."""
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

    # Trade outcomes: signed move over the next N bars after each trade.
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
            sgn = 1.0 if t["side"] == "BUY" else -1.0
            fwd = round(float(sgn * (closes_arr[i + _OUTCOME_BARS] / entry - 1.0)), 4)
            outcome = "win" if fwd > 0 else "loss"
        out_trades.append({"time": t["date"], "side": t["side"], "price": t.get("price"),
                           "weight": t.get("target_weight"), "regime": t.get("regime"),
                           "why": t.get("why"), "agents": t.get("agents"),
                           "fwd_return": fwd, "outcome": outcome})
    return {"candles": candles, "ema_fast": line(ef), "ema_slow": line(es),
            "trades": out_trades, "decision": decision}


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
    # Book equity curve, indexed to 100 at inception.
    eqh = state.get("equity_history", [])
    book_curve, book_metrics = [], {}
    if eqh:
        base = eqh[0][1] or state["initial_capital"]
        book_curve = [{"time": d, "value": round(100.0 * v / base, 4)} for d, v in eqh]
        book_metrics = _curve_metrics([d for d, _ in eqh], [v for _, v in eqh])

    # 1/N buy-and-hold benchmark over the window, indexed to 100.
    closes_df = fx_data.closes(panel)
    bench_curve, bench_metrics = [], {}
    if not closes_df.empty:
        w = closes_df.tail(bars)
        bh_ret = w.pct_change(fill_method=None).mean(axis=1).fillna(0.0)
        bh_eq = 100.0 * (1 + bh_ret).cumprod()
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
        "book_curve": book_curve, "book_metrics": book_metrics,
        "bench_curve": bench_curve, "bench_metrics": bench_metrics,
        "attribution": _agent_attribution(panel, p, bars),
        "glossary": GLOSSARY,
        "pairs": pairs, "data": data,
    }


# ---------------------------------------------------------------------------
# HTML
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
header{padding:1.1rem 1.5rem;border-bottom:1px solid var(--bd)}
h1{margin:0;font-size:1.15rem}.sub{color:var(--mut);font-size:.82rem;margin-top:.25rem}
.stats{display:flex;gap:1.4rem;flex-wrap:wrap;margin-top:.7rem}
.stat .v{font-size:1.05rem;font-weight:600}.stat .k{color:var(--mut);font-size:.7rem;text-transform:uppercase;cursor:help;border-bottom:1px dotted var(--mut)}
.pos{color:var(--up)}.neg{color:var(--dn)}
.section{padding:1rem 1.5rem}.grid2{display:grid;grid-template-columns:1fr 320px;gap:1rem}
@media(max-width:900px){.grid2{grid-template-columns:1fr}}
.card{border:1px solid var(--bd);border-radius:12px;background:var(--panel);padding:1rem}
.card h2{margin:0 0 .6rem;font-size:.9rem}.card h2 .h{cursor:help;border-bottom:1px dotted var(--mut)}
#eqchart{height:240px}#chart{height:430px}
.tabs{display:flex;gap:.4rem;flex-wrap:wrap;padding:0 1.5rem}
.tab{padding:.35rem .7rem;border:1px solid var(--bd);border-radius:999px;background:var(--panel);
color:var(--fg);cursor:pointer;font-size:.85rem}.tab.on{border-color:var(--accent);color:var(--accent)}
.wrap{display:grid;grid-template-columns:1fr 380px;gap:1rem;padding:1rem 1.5rem}
@media(max-width:900px){.wrap{grid-template-columns:1fr}}
.side{display:flex;flex-direction:column;gap:1rem;min-width:0}
.muted{color:var(--mut)}.why{font-size:.85rem;line-height:1.45}
.row{display:flex;align-items:center;gap:.5rem;margin:.25rem 0;font-size:.78rem}
.row .name{width:90px;color:var(--mut)}.bar{flex:1;height:9px;background:#21262d;border-radius:4px;position:relative}
.bar i{position:absolute;top:0;bottom:0;border-radius:4px}.val{width:60px;text-align:right}
.metrics{display:grid;grid-template-columns:auto 1fr 1fr;gap:.3rem .8rem;font-size:.82rem;align-items:center}
.metrics .hd{color:var(--mut);font-size:.7rem;text-transform:uppercase}
.metrics .lbl{color:var(--mut);cursor:help;border-bottom:1px dotted var(--mut)}
.journal{max-height:520px;overflow:auto}
.j{border:1px solid var(--bd);border-radius:10px;padding:.6rem .7rem;margin-bottom:.6rem;cursor:pointer}
.j:hover{border-color:var(--accent)}.j .hd{display:flex;justify-content:space-between;font-size:.82rem;gap:.5rem}
.badge{font-size:.68rem;padding:.05rem .4rem;border-radius:6px;border:1px solid var(--bd);color:var(--mut)}
.B{color:var(--up)}.S{color:var(--dn)}.win{color:var(--up)}.loss{color:var(--dn)}
.foot{padding:1rem 1.5rem;color:var(--mut);font-size:.75rem}
</style></head><body>
<header>
  <h1>FX Paper Book · <span style="color:var(--accent)">__ACCOUNT__</span>
    <span class="badge">__PROFILE__</span>__HALT__</h1>
  <div class="sub">base __CCY__ · updated __UPDATED__ · candlesticks, performance vs buy-and-hold, and the reasoning + outcome of every trade</div>
  <div class="stats" id="stats"></div>
</header>

<div class="section grid2">
  <div class="card"><h2><span class="h" title="__T_BENCH__">Equity vs buy-and-hold</span> <span class="muted" style="font-weight:400">(indexed to 100)</span></h2><div id="eqchart"></div></div>
  <div class="card"><h2>Performance</h2><div id="metrics" class="metrics"></div>
    <div id="agentcard" style="margin-top:1rem"></div></div>
</div>

<div class="tabs" id="tabs"></div>
<div class="wrap">
  <div id="chart"></div>
  <div class="side">
    <div class="card"><h2><span class="h" title="__T_TILT__">Today's read</span> · <span id="curpair"></span></h2>
      <div id="decision" class="why muted"></div>
      <div id="agents" style="margin-top:.6rem"></div></div>
    <div class="card"><h2>Trade journal — why &amp; <span class="h" title="__T_OUT__">outcome</span></h2>
      <div id="journal" class="journal"></div></div>
  </div>
</div>
<div class="foot">Paper money. Agents: trend · breakout · mean-reversion · momentum · carry · deep-learning,
blended by a Hedge ensemble, sized by volatility targeting. Hover any underlined label for its definition.
Out-of-sample testing found no statistically significant edge — treat this as a learning tool, not a forecast.</div>
<script>
const DASH = __DATA__;
const G = DASH.glossary || {};
const pct = v => v==null? "–" : (v>=0?"+":"")+(v*100).toFixed(2)+"%";
const fmt = v => v==null? "–" : (Math.abs(v)>=100? v.toFixed(2) : v.toPrecision(5));
let chart, current;

// header stats
(function(){
  const m=DASH.book_metrics||{}, s=DASH.stats=document.getElementById('stats');
  const items=[["Equity",DASH.equity.toLocaleString()+" "+DASH.currency,null],
    ["Return",pct(DASH.ret),"Benchmark"],
    ["Sharpe",(m.sharpe??"–"),"Sharpe"],["Max drawdown",(m.max_dd!=null?pct(m.max_dd):"–"),"Max drawdown"],
    ["Gross lev.",DASH.gross+"x",null],["Trades",DASH.trades_total,null]];
  s.innerHTML=items.map(([k,v,g])=>`<div class=stat><div class=v>${v}</div>`+
    `<div class=k ${g?`title="${G[g]||''}"`:''}>${k}</div></div>`).join('');
})();

function bars(obj, hi){
  if(!obj||!Object.keys(obj).length) return '<div class="muted">no data</div>';
  const max=Math.max(0.0001,...Object.values(obj).map(v=>Math.abs(v)));
  return Object.entries(obj).map(([n,v])=>{
    const w=Math.min(Math.abs(v)/max,1)*50,left=v>=0?50:50-w,col=v>=0?'var(--up)':'var(--dn)';
    const em=(hi&&hi.includes(n))?'font-weight:700;color:var(--fg)':'';
    return `<div class=row><div class=name style="${em}">${n}</div>`+
      `<div class=bar><i style="left:${left}%;width:${w}%;background:${col}"></i>`+
      `<i style="left:50%;width:1px;background:#555"></i></div>`+
      `<div class=val style="color:${col}">${pct(v)}</div></div>`;}).join('');
}
// agent vote bars (signals in [-1,1], not returns)
function votes(agents){
  if(!agents) return '<div class="muted">flat / no read</div>';
  const o={}; Object.entries(agents).forEach(([n,v])=>o[n]=v/Math.max(1,Math.abs(v))*Math.abs(v));
  return bars(agents);
}

// performance metrics + agent scorecard
(function(){
  const b=DASH.book_metrics||{}, k=DASH.bench_metrics||{};
  const rows=[["Return","total_return",true],["Sharpe","sharpe",false],
    ["Volatility","vol",true],["Max drawdown","max_dd",true],["Win rate","win_rate",true]];
  const cell=(m,key,isPct)=>{const v=m[key]; if(v==null)return "–";
    return isPct?pct(v):v;};
  document.getElementById('metrics').innerHTML =
    `<div class=hd></div><div class=hd>Book</div><div class=hd>Buy&amp;Hold</div>`+
    rows.map(([lbl,key,isPct])=>`<div class="lbl" title="${G[lbl]||''}">${lbl}</div>`+
      `<div>${cell(b,key,isPct)}</div><div class=muted>${cell(k,key,isPct)}</div>`).join('');
  document.getElementById('agentcard').innerHTML =
    `<div style="font-size:.8rem;margin-bottom:.4rem" class="lbl" title="${G['Agent scorecard']}">Agent scorecard (this window)</div>`+
    bars(DASH.attribution,["ensemble","buy&hold"]);
})();

// equity vs benchmark line chart
(function(){
  const el=document.getElementById('eqchart');
  if(!(DASH.bench_curve||[]).length && !(DASH.book_curve||[]).length){el.innerHTML='<p class=muted style="padding:1rem">Curve builds as the book runs.</p>';return;}
  const c=LightweightCharts.createChart(el,{layout:{background:{color:'#161b22'},textColor:'#e6edf3'},
    grid:{vertLines:{color:'#21262d'},horzLines:{color:'#21262d'}},
    rightPriceScale:{borderColor:'#30363d'},timeScale:{borderColor:'#30363d'},autoSize:true});
  if((DASH.bench_curve||[]).length) c.addLineSeries({color:'#8b949e',lineWidth:1,title:'Buy&Hold'}).setData(DASH.bench_curve);
  if((DASH.book_curve||[]).length) c.addLineSeries({color:'#58a6ff',lineWidth:2,title:'Book'}).setData(DASH.book_curve);
  c.timeScale().fitContent();
})();

function showPair(sym){
  current=sym;
  document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('on',t.dataset.s===sym));
  document.getElementById('curpair').textContent=sym;
  const d=DASH.data[sym];
  document.getElementById('chart').innerHTML='';
  chart=LightweightCharts.createChart(document.getElementById('chart'),{
    layout:{background:{color:'#161b22'},textColor:'#e6edf3'},
    grid:{vertLines:{color:'#21262d'},horzLines:{color:'#21262d'}},
    rightPriceScale:{borderColor:'#30363d'},timeScale:{borderColor:'#30363d'},autoSize:true});
  const cs=chart.addCandlestickSeries({upColor:'#26a69a',downColor:'#ef5350',
    wickUpColor:'#26a69a',wickDownColor:'#ef5350',borderVisible:false});
  cs.setData(d.candles);
  chart.addLineSeries({color:'#f5a623',lineWidth:1,priceLineVisible:false}).setData(d.ema_fast);
  chart.addLineSeries({color:'#58a6ff',lineWidth:1,priceLineVisible:false}).setData(d.ema_slow);
  cs.setMarkers(d.trades.map(t=>({time:t.time,
    position:t.side==='BUY'?'belowBar':'aboveBar',color:t.side==='BUY'?'#26a69a':'#ef5350',
    shape:t.side==='BUY'?'arrowUp':'arrowDown',
    text:t.side+(t.weight!=null?' '+Math.round(t.weight*100)+'%':'')})));
  chart.timeScale().fitContent();
  const dec=d.decision||{};
  document.getElementById('decision').innerHTML = dec.text || 'No active position — agents flat or conflicted here.';
  document.getElementById('agents').innerHTML = votes(dec.agents);
  const j=document.getElementById('journal');
  if(!d.trades.length){j.innerHTML='<div class="muted">No trades for '+sym+' yet.</div>';return;}
  j.innerHTML=d.trades.slice().reverse().map(t=>{
    const cls=t.side==='BUY'?'B':'S';
    const oc=t.outcome==='win'?`<span class=win>✅ ${pct(t.fwd_return)}</span>`:
      t.outcome==='loss'?`<span class=loss>❌ ${pct(t.fwd_return)}</span>`:`<span class=muted>⏳ open</span>`;
    return `<div class="j" data-t="${t.time}"><div class=hd>`+
      `<span class="${cls}">${t.side} ${sym} @ ${fmt(t.price)}</span><span>${oc}</span></div>`+
      `<div class=hd style="margin-top:.2rem"><span class=badge>${t.time}${t.regime?' · '+t.regime:''}</span></div>`+
      `<div class="why" style="margin-top:.35rem">${t.why||'(no rationale recorded)'}</div>`+
      `<div style="margin-top:.4rem">${votes(t.agents)}</div></div>`;}).join('');
  j.querySelectorAll('.j').forEach(el=>el.onclick=()=>{
    const times=d.candles.map(c=>c.time),i=times.indexOf(el.dataset.t);
    if(i>=0)chart.timeScale().setVisibleRange({from:times[Math.max(0,i-30)],to:times[Math.min(times.length-1,i+8)]});});
}
const tabs=document.getElementById('tabs');
DASH.pairs.forEach(s=>{const b=document.createElement('div');b.className='tab';b.dataset.s=s;b.textContent=s;b.onclick=()=>showPair(s);tabs.appendChild(b);});
if(DASH.pairs.length)showPair(DASH.pairs[0]);
else document.getElementById('chart').innerHTML='<p style="padding:2rem;color:#8b949e">No data yet.</p>';
</script></body></html>"""


def render(payload: dict) -> str:
    ret = payload["ret"]
    g = payload["glossary"]
    repl = {
        "__ACCOUNT__": payload["account"], "__PROFILE__": payload["profile"],
        "__CCY__": payload["currency"], "__UPDATED__": payload["updated"],
        "__HALT__": ' <span class="badge" style="color:#ef5350">RISK-HALTED</span>'
                    if payload["halted"] else "",
        "__LWC__": _LWC,
        "__T_BENCH__": g["Benchmark"], "__T_TILT__": g["Tilt"], "__T_OUT__": g["Outcome"],
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
        print(f"  wrote {out_path} ({len(payload['pairs'])} pairs, "
              f"{payload['trades_total']} trades)")
    return html


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
            "margin:0;padding:3rem;max-width:720px}h1{margin:0 0 .25rem}.s{color:#8b949e;margin:0 0 2rem}"
            "a.card{display:block;margin:1rem 0;padding:1.25rem 1.5rem;border:1px solid #30363d;"
            "border-radius:14px;background:#161b22;color:#e6edf3;text-decoration:none}"
            "a.card:hover{border-color:#58a6ff}.name{font-size:1.2rem;font-weight:600;color:#58a6ff}"
            ".amt{color:#8b949e;font-size:.9rem}</style>"
            "<h1>FX Paper Books</h1><p class=s>Candlesticks + performance + the reasoning behind every trade · "
            f"updated {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}</p>" + "".join(cards))
    with open(os.path.join(out_dir, "index.html"), "w") as f:
        f.write(html)
    print(f"  wrote {out_dir}/index.html ({len(cards)} accounts)")


def main(argv=None):
    ap = argparse.ArgumentParser(description="FX paper-book candlestick + analytics dashboard")
    ap.add_argument("--account")
    ap.add_argument("-o", "--out")
    ap.add_argument("--out-dir", default="public")
    ap.add_argument("--index", action="store_true", help="build the landing index for all accounts")
    ap.add_argument("--all", action="store_true", help="export every account + index")
    ap.add_argument("--bars", type=int, default=180)
    ap.add_argument("--synthetic", action="store_true")
    args = ap.parse_args(argv)

    if args.all or args.index:
        accts = list_accounts()
        if args.all:
            for a in accts:
                export_account(a, args.synthetic, os.path.join(args.out_dir, f"fx_{a}.html"), args.bars)
        build_index(accts, args.out_dir)
    elif args.account:
        out = args.out or os.path.join(args.out_dir, f"fx_{args.account}.html")
        export_account(args.account, args.synthetic, out, args.bars)
    else:
        ap.error("pass --account, --all, or --index")


if __name__ == "__main__":
    main()
