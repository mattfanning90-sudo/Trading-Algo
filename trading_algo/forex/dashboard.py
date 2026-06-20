"""Static candlestick dashboard for the FX paper books (for GitHub Pages).

Produces a self-contained HTML page per account: candlestick charts per
instrument with EMA overlays, BUY/SELL markers, a per-agent vote breakdown, and a
**trade journal that explains *why* each trade was made** (the rationale captured
by `explain.decide_and_explain` and stored on every trade). The goal is learning:
you can see the candle, the signals, and the reasoning at the moment of the trade.

Charts use TradingView's lightweight-charts (loaded from a CDN at view time); all
data is embedded in the page, so the published artifact is self-contained.

    python -m trading_algo.forex.dashboard --account matt -o public/fx_matt.html
    python -m trading_algo.forex.dashboard --index --out-dir public   # landing page
    (append --synthetic to build offline)
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone

import pandas as pd

from . import fx_data
from . import indicators as ind
from .fx_book import list_accounts, load_state
from .fx_config import profile
from .pairs import get_pair

_LWC = "https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"


def _panel(symbols, synthetic):
    if synthetic:
        return fx_data.synthetic_panel(symbols)
    start = (datetime.now(timezone.utc) - timedelta(days=550)).strftime("%Y-%m-%d")
    return fx_data.load_panel(symbols, start, use_cache=True)


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
    first = df.index[0].strftime("%Y-%m-%d")
    tr = [t for t in trades if t.get("pair") == sym and t.get("date", "") >= first]
    return {
        "candles": candles,
        "ema_fast": line(ef), "ema_slow": line(es),
        "trades": [{"time": t["date"], "side": t["side"], "price": t.get("price"),
                    "weight": t.get("target_weight"), "regime": t.get("regime"),
                    "why": t.get("why"), "agents": t.get("agents")} for t in tr],
        "decision": decision,
    }


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
    return {
        "account": account, "profile": state.get("profile", "balanced"),
        "currency": state.get("currency", "AUD"),
        "initial": state["initial_capital"], "equity": round(float(eq), 2),
        "ret": eq / state["initial_capital"] - 1.0,
        "trades_total": len(state.get("trades", [])),
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "equity_curve": state.get("equity_history", []),
        "halted": state.get("risk_halted", False),
        "pairs": pairs, "data": data,
    }


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------
_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FX Paper · __ACCOUNT__</title>
<script src="__LWC__"></script>
<style>
:root{--bg:#0d1117;--panel:#161b22;--bd:#30363d;--fg:#e6edf3;--mut:#8b949e;
--up:#26a69a;--dn:#ef5350;--accent:#58a6ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
header{padding:1.25rem 1.5rem;border-bottom:1px solid var(--bd)}
h1{margin:0;font-size:1.2rem}.sub{color:var(--mut);font-size:.85rem;margin-top:.25rem}
.stats{display:flex;gap:1.5rem;flex-wrap:wrap;margin-top:.75rem}
.stat .v{font-size:1.1rem;font-weight:600}.stat .k{color:var(--mut);font-size:.72rem;text-transform:uppercase}
.pos{color:var(--up)}.neg{color:var(--dn)}
.tabs{display:flex;gap:.4rem;flex-wrap:wrap;padding:1rem 1.5rem 0}
.tab{padding:.35rem .7rem;border:1px solid var(--bd);border-radius:999px;background:var(--panel);
color:var(--fg);cursor:pointer;font-size:.85rem}.tab.on{border-color:var(--accent);color:var(--accent)}
.wrap{display:grid;grid-template-columns:1fr 380px;gap:1rem;padding:1rem 1.5rem}
@media(max-width:900px){.wrap{grid-template-columns:1fr}}
#chart{height:460px;border:1px solid var(--bd);border-radius:12px;background:var(--panel)}
.side{display:flex;flex-direction:column;gap:1rem;min-width:0}
.card{border:1px solid var(--bd);border-radius:12px;background:var(--panel);padding:1rem}
.card h2{margin:0 0 .6rem;font-size:.9rem}.muted{color:var(--mut)}
.why{font-size:.85rem;line-height:1.45}
.agentrow{display:flex;align-items:center;gap:.5rem;margin:.25rem 0;font-size:.78rem}
.agentrow .name{width:84px;color:var(--mut)}.bar{flex:1;height:8px;background:#21262d;border-radius:4px;position:relative}
.bar i{position:absolute;top:0;bottom:0;border-radius:4px}.val{width:46px;text-align:right}
.journal{max-height:520px;overflow:auto}
.j{border:1px solid var(--bd);border-radius:10px;padding:.6rem .7rem;margin-bottom:.6rem;cursor:pointer}
.j:hover{border-color:var(--accent)}.j .hd{display:flex;justify-content:space-between;font-size:.82rem}
.badge{font-size:.68rem;padding:.05rem .4rem;border-radius:6px;border:1px solid var(--bd);color:var(--mut)}
.B{color:var(--up)}.S{color:var(--dn)}
.foot{padding:1rem 1.5rem;color:var(--mut);font-size:.75rem}
</style></head><body>
<header>
  <h1>FX Paper Book · <span style="color:var(--accent)">__ACCOUNT__</span>
    <span class="badge">__PROFILE__</span>__HALT__</h1>
  <div class="sub">base __CCY__ · updated __UPDATED__ · candlesticks + the reasoning behind every trade</div>
  <div class="stats">
    <div class="stat"><div class="v">__EQUITY__ __CCY__</div><div class="k">Equity</div></div>
    <div class="stat"><div class="v __RETCLS__">__RET__</div><div class="k">Return</div></div>
    <div class="stat"><div class="v">__INITIAL__ __CCY__</div><div class="k">Capital</div></div>
    <div class="stat"><div class="v">__TRADES__</div><div class="k">Trades</div></div>
  </div>
</header>
<div class="tabs" id="tabs"></div>
<div class="wrap">
  <div id="chart"></div>
  <div class="side">
    <div class="card"><h2>Today's read · <span id="curpair"></span></h2>
      <div id="decision" class="why muted"></div>
      <div id="agents" style="margin-top:.6rem"></div></div>
    <div class="card"><h2>Trade journal — why we traded</h2>
      <div id="journal" class="journal"></div></div>
  </div>
</div>
<div class="foot">Paper money. Signals: trend · breakout · mean-reversion · momentum · carry · deep-learning,
blended by a Hedge ensemble and sized by volatility targeting. Synthetic builds are pipeline tests only.</div>
<script>
const DASH = __DATA__;
const fmt = v => v==null? "–" : (Math.abs(v)>=100? v.toFixed(2) : v.toPrecision(5));
let chart, current;

function agentBars(agents){
  if(!agents) return '<div class="muted">no agent read</div>';
  return Object.entries(agents).map(([n,v])=>{
    const w=Math.min(Math.abs(v),1)*50, left=v>=0?50:50-w, col=v>=0?'var(--up)':'var(--dn)';
    return `<div class=agentrow><div class=name>${n}</div>`+
      `<div class=bar><i style="left:${left}%;width:${w}%;background:${col}"></i>`+
      `<i style="left:50%;width:1px;background:#555"></i></div>`+
      `<div class="val" style="color:${col}">${v>=0?'+':''}${v.toFixed(2)}</div></div>`;
  }).join('');
}

function showPair(sym){
  current=sym;
  document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('on',t.dataset.s===sym));
  document.getElementById('curpair').textContent=sym;
  const d=DASH.data[sym];
  // (re)build chart
  document.getElementById('chart').innerHTML='';
  chart=LightweightCharts.createChart(document.getElementById('chart'),{
    layout:{background:{color:'#161b22'},textColor:'#e6edf3'},
    grid:{vertLines:{color:'#21262d'},horzLines:{color:'#21262d'}},
    rightPriceScale:{borderColor:'#30363d'},timeScale:{borderColor:'#30363d'},
    autoSize:true});
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
  // decision + agent breakdown
  const dec=d.decision||{};
  document.getElementById('decision').innerHTML = dec.text? dec.text :
    'No active position — agents are flat or conflicted here.';
  document.getElementById('agents').innerHTML = agentBars(dec.agents);
  // journal
  const j=document.getElementById('journal');
  if(!d.trades.length){ j.innerHTML='<div class="muted">No trades recorded for '+sym+' yet.</div>'; return; }
  j.innerHTML=d.trades.slice().reverse().map(t=>{
    const cls=t.side==='BUY'?'B':'S';
    return `<div class="j" data-t="${t.time}"><div class=hd>`+
      `<span class="${cls}">${t.side} ${sym} @ ${fmt(t.price)}</span>`+
      `<span class=badge>${t.time}${t.regime?' · '+t.regime:''}</span></div>`+
      `<div class="why" style="margin-top:.35rem">${t.why||'(no rationale recorded)'}</div>`+
      `<div style="margin-top:.4rem">${agentBars(t.agents)}</div></div>`;
  }).join('');
  j.querySelectorAll('.j').forEach(el=>el.onclick=()=>{
    const times=d.candles.map(c=>c.time); const i=times.indexOf(el.dataset.t);
    if(i>=0) chart.timeScale().setVisibleRange({from:times[Math.max(0,i-30)],to:times[Math.min(times.length-1,i+8)]});
  });
}

const tabs=document.getElementById('tabs');
DASH.pairs.forEach(s=>{const b=document.createElement('div');b.className='tab';b.dataset.s=s;
  b.textContent=s;b.onclick=()=>showPair(s);tabs.appendChild(b);});
if(DASH.pairs.length) showPair(DASH.pairs[0]);
else document.getElementById('chart').innerHTML='<p style="padding:2rem;color:#8b949e">No data yet.</p>';
</script></body></html>"""


def render(payload: dict) -> str:
    ret = payload["ret"]
    repl = {
        "__ACCOUNT__": payload["account"], "__PROFILE__": payload["profile"],
        "__CCY__": payload["currency"], "__UPDATED__": payload["updated"],
        "__EQUITY__": f"{payload['equity']:,.2f}", "__INITIAL__": f"{payload['initial']:,.0f}",
        "__RET__": f"{ret:+.2%}", "__RETCLS__": "pos" if ret >= 0 else "neg",
        "__TRADES__": str(payload["trades_total"]),
        "__HALT__": ' <span class="badge" style="color:#ef5350">RISK-HALTED</span>'
                    if payload["halted"] else "",
        "__LWC__": _LWC,
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
            "<h1>FX Paper Books</h1><p class=s>Candlesticks + the reasoning behind every trade · "
            f"updated {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}</p>" + "".join(cards))
    with open(os.path.join(out_dir, "index.html"), "w") as f:
        f.write(html)
    print(f"  wrote {out_dir}/index.html ({len(cards)} accounts)")


def main(argv=None):
    ap = argparse.ArgumentParser(description="FX paper-book candlestick dashboard")
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
