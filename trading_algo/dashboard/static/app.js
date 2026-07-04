/* MOMENTUM/3R terminal — vanilla JS, zero dependencies.
   A 1:1 port of the design-canvas terminal (Momentum 3R Terminal), wired to
   the live /api/* endpoints instead of the design's hardcoded snapshot.
   No frameworks, no external URLs — exports must stay self-contained. */
'use strict';

/* ============================== palette ================================ */
const G = '#7ee787', R = '#ff7b72', DIM = '#61805f', AMB = '#e3b341';
const PALE = '#eaffec', TXT = '#c9e8cc', FAINT = '#3d543f';
const SYM = { AUD: 'A$', USD: '$', GBP: '£', EUR: '€', JPY: '¥' };
const ALLOC_COLORS = { FULL: '#7ee787', SMALL: '#ff7b72', MATT: '#e3b341',
                       PARTNER: '#b08a2e', DAY: '#4a9c55', MULTI: '#2a4a2c' };
const ALLOC_FALLBACK = ['#7ee787', '#e3b341', '#4a9c55', '#b08a2e', '#ff7b72', '#2a4a2c'];
const REGION_COUNTRY = { ASX: 'AUSTRALIA', US: 'UNITED STATES', FTSE: 'LONDON' };
const REGION_SHORT = { FTSE: 'LSE' };   // status-bar / tape labels

/* ============================== state ================================== */
const S = {
  account: null,            // display key: 'ALL' | 'FULL' | 'MATT' | …
  tab: 'OVERVIEW',
  range: '1M',              // equity-curve range chip
  selPair: {},              // per-account selected pair for the big chart
  tf: {},                   // per-account timeframe key
  tfOpen: false,
  ta: { ema: true, boll: false, don: false },
  taPanes: {},              // in-chart oscillator sub-panes: {RSI, MOMENTUM, ADX}
  candleIdx: null,
  meta: null,
  overview: null,
  pages: {},                // key -> account payload
  backtests: {},            // key -> backtest payload
  errors: {},               // key -> error string
  candles: null,            // optional real OHLC dropped in as candles.json
  isExport: !!(window.__EXPORT_ACCOUNT__ || window.__EXPORT_ALL__),
  exportAll: !!window.__EXPORT_ALL__,   // --site export: every book baked
  zoom: null,               // whole-terminal zoom (seeded from viewport)
};

/* ============================== utils ================================== */
const esc = s => String(s == null ? '' : s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;');

const num = (v, dp = 2) => (+v).toLocaleString('en-US',
  { minimumFractionDigits: dp, maximumFractionDigits: dp });

/* signed with the design's true minus sign */
const sgn = (v, body) => (v < 0 ? '−' : '+') + body;
const sgnPct = (v, dp = 1) => sgn(v, num(Math.abs(v) * 100, dp) + '%');
const sgnNum = (v, dp = 0) => sgn(v, num(Math.abs(v), dp));
const pct0 = v => num(v * 100, 0) + '%';
const cSign = v => (v < 0 ? R : G);

/* split 100510.34 -> ['100,510', '.34'] for the big KPI numerals */
const moneySplit = v => {
  const neg = v < 0 ? '−' : '';
  const a = Math.abs(+v);
  const int = Math.floor(a);
  const dec = (a - int).toFixed(2).slice(1);
  return [neg + int.toLocaleString('en-US'), dec];
};

const mdy = iso => {   // '2026-07-01' -> 'JUL 01'
  if (!iso) return '';
  const m = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
  const p = String(iso).slice(0, 10).split('-');
  return p.length === 3 ? m[+p[1] - 1] + ' ' + p[2] : iso;
};
const mmdd = iso => String(iso || '').slice(5, 10);

const toPts = (vals, w, h, pad) => {
  if (!vals.length) return [];
  const min = Math.min(...vals), max = Math.max(...vals);
  const n = Math.max(vals.length - 1, 1), rng = (max - min) || 1;
  return vals.map((v, i) => ((i / n) * w).toFixed(1) + ',' +
    (pad + (1 - (v - min) / rng) * (h - 2 * pad)).toFixed(1));
};

/* price formatting for the FX / multi-asset books */
const CRYPTO_RE = /^(BTC|ETH|SOL|XRP|ADA|DOGE|BNB|LTC|DOT|AVAX)/;
const FX_RE = /^(EUR|GBP|USD|AUD|NZD|CAD|CHF|JPY)[A-Z]{3}$/;
const isCrypto = p => CRYPTO_RE.test(p);
const isFxPair = p => FX_RE.test(p) && !isCrypto(p);
const pairPrice = (pair, v) => {
  if (v == null || !isFinite(v)) return '—';
  if (isFxPair(pair)) return num(v, v >= 20 ? 2 : 4);
  if (isCrypto(pair)) return '$' + num(v, v >= 1000 ? 0 : 2);
  return '$' + num(v, 2);
};
/* local-currency price/fill in the equity books */
const px2 = (sym, v) => sym + num(v, 2);
const pxFill = (sym, v) => sym + num(v, v < 3 ? 4 : 2);
const money0 = (sym, v) => sym + num(v, 0);

/* ====================== seeded synthetic generators ==================== */
/* one xorshift-style mixer for every deterministic synthetic series */
const mix32 = seed => {
  let h = seed | 0;
  return () => {
    h = Math.imul(h ^ (h >>> 15), 2246822507); h = Math.imul(h ^ (h >>> 13), 3266489909);
    return ((h ^= h >>> 16) >>> 0) / 4294967296;
  };
};
/* Port of the design's deterministic generators: hover series + OHLC bars
   anchored to the real last close. Real data (payload history / candles.json)
   always wins; synthetic is labelled SYNTHETIC in the UI. */
const _seriesCache = {};
function synthSeries(ticker, cur, entry, buyIdx) {
  const ck = ticker + '|' + cur;
  if (_seriesCache[ck]) return _seriesCache[ck];
  if (Object.keys(_seriesCache).length > 240) for (const k in _seriesCache) delete _seriesCache[k];
  let h = 2166136261;
  for (const ch of ticker) { h ^= ch.charCodeAt(0); h = Math.imul(h, 16777619); }
  const rnd = mix32(h);
  const n = 64;
  const w = [1];
  for (let i = 1; i <= n; i++) w.push(w[i - 1] * (1 + (rnd() - 0.485) * 0.028));
  const scale = cur / w[n];
  let s = w.map(v => v * scale);
  const off = entry - s[buyIdx];
  s = s.map((v, i) => v + off * Math.exp(-((i - buyIdx) ** 2) / 60));
  s[n] = cur;
  _seriesCache[ck] = s;
  return s;
}

const _candleCache = {};
function synthCandles(pair, close, volAnn, nBars, tfKey = '', sigScale = 1) {
  const key = pair + '|' + close + '|' + nBars + '|' + tfKey;
  if (_candleCache[key]) return _candleCache[key];
  /* keys include the live close, so cap the cache or it grows every tick */
  if (Object.keys(_candleCache).length > 240) for (const k in _candleCache) delete _candleCache[k];
  const real = S.candles && S.candles[pair];
  if (real && Array.isArray(real) && real.length > 5) {
    const bars = real.slice(-nBars).map(b => Array.isArray(b)
      ? { o: +b[1], h: +b[2], l: +b[3], c: +b[4] }
      : { o: +b.o, h: +b.h, l: +b.l, c: +b.c });
    _candleCache[key] = { bars, real: true };
    return _candleCache[key];
  }
  let h = 5381;
  for (const ch of pair) h = (h * 33) ^ ch.charCodeAt(0);
  const rnd = mix32(h);
  const sig = Math.max(volAnn, 0.02) / 16 * sigScale;
  const closes = [close];
  for (let i = 1; i < nBars; i++) closes.unshift(closes[0] / (1 + (rnd() - 0.5) * 2.2 * sig));
  const bars = closes.map((c, i) => {
    const o = i === 0 ? c * (1 + (rnd() - 0.5) * sig) : closes[i - 1];
    const hi = Math.max(o, c) * (1 + rnd() * sig * 0.8);
    const lo = Math.min(o, c) * (1 - rnd() * sig * 0.8);
    return { o, h: hi, l: lo, c };
  });
  _candleCache[key] = { bars, real: false };
  return _candleCache[key];
}

/* candle geometry shared by the row popovers and the big chart */
function candlePaths(bars, W, Y) {
  const n = bars.length;
  const bw = W / n, bodyW = Math.max(bw * 0.62, 2);
  let wickUp = '', bodyUp = '', wickDn = '', bodyDn = '';
  bars.forEach((b, i) => {
    const cx = (i + 0.5) * bw, x = cx - bodyW / 2;
    const yO = Y(b.o), yC = Y(b.c);
    const top = Math.min(yO, yC), hgt = Math.max(Math.abs(yO - yC), 0.8);
    const wick = 'M' + cx.toFixed(1) + ',' + Y(b.h).toFixed(1) + 'L' + cx.toFixed(1) + ',' + Y(b.l).toFixed(1);
    const body = 'M' + x.toFixed(1) + ',' + top.toFixed(1) + 'h' + bodyW.toFixed(1) +
                 'v' + hgt.toFixed(1) + 'h-' + bodyW.toFixed(1) + 'Z';
    if (b.c >= b.o) { wickUp += wick; bodyUp += body; } else { wickDn += wick; bodyDn += body; }
  });
  return { wickUp, bodyUp, wickDn, bodyDn, bw };
}

/* ============================== data layer ============================= */
async function loadJSON(url) {
  const r = await fetch(url, { cache: 'no-store' });
  let body = null;
  try { body = await r.json(); } catch (e) { /* non-JSON error */ }
  if (!r.ok) throw new Error((body && body.error) || ('HTTP ' + r.status));
  return body;
}

async function ensurePage(key, force = false) {
  if (key === 'ALL') {
    if (S.overview && !force) return;
    try { S.overview = await loadJSON('/api/overview'); delete S.errors.ALL; }
    catch (e) { S.errors.ALL = e.message; }
    return;
  }
  if (S.pages[key] && !force) return;
  try { S.pages[key] = await loadJSON('/api/account/' + key); delete S.errors[key]; }
  catch (e) { S.errors[key] = e.message; }
}

async function ensureBacktest(key) {
  const cur = S.backtests[key];
  if (cur && !cur._error) return;             // cache successes, retry errors
  try { S.backtests[key] = await loadJSON('/api/backtest/' + key); }
  catch (e) { S.backtests[key] = { available: false, _error: true }; }
}

function accounts() { return (S.meta && S.meta.accounts) || []; }
function accountEntry(key) {
  return accounts().find(a => a.key === key) || null;
}

/* ============================== chrome ================================= */
function clockStr() { return new Date().toISOString().slice(11, 19); }

function connLabel() {
  return S.isExport ? 'EXPORT' : (S.stale ? 'RECONNECTING' : 'LIVE');
}
function liveChipStyle() {
  return S.stale && !S.isExport
    ? 'background:rgba(227,179,65,.08);border:1px solid #4a3a1a;color:#e3b341;padding:3px 10px;border-radius:2px'
    : 'background:#12200f;border:1px solid #2a4a2c;color:#7ee787;padding:3px 10px;border-radius:2px';
}
function updateLiveChip() {
  const chip = document.getElementById('livechip');
  const conn = document.getElementById('conn');
  if (chip) chip.style.cssText = liveChipStyle();
  if (conn) conn.textContent = connLabel();
}

function headerHTML(page) {
  const isAll = S.account === 'ALL';
  const accs = accounts();
  const chips = [];
  if ((!S.isExport || S.exportAll) && accs.length > 1) {
    chips.push({ key: 'ALL', label: 'ALL ACCOUNTS' });
  }
  for (const a of accs) chips.push({ key: a.key, label: a.label });
  const chipHtml = chips.map(a => {
    const on = S.account === a.key;
    return `<span class="hv-dim" data-act="acct" data-arg="${esc(a.key)}" style="font-size:9px;letter-spacing:.1em;padding:4px 10px;border:1px solid ${on ? '#2a4a2c' : '#262626'};color:${on ? PALE : DIM};background:${on ? '#12200f' : 'transparent'};border-radius:2px;cursor:pointer;user-select:none">${esc(a.label)}</span>`;
  }).join('');

  const tabs = ['OVERVIEW', 'POSITIONS', 'BACKTEST', 'METHOD'].map(label => {
    const on = S.tab === label;
    return `<span class="hv-dim" data-act="tab" data-arg="${label}" style="padding:13px 18px;color:${on ? PALE : DIM};background:${on ? '#12200f' : 'transparent'};border-right:1px solid #262626;border-bottom:2px solid ${on ? G : 'transparent'};cursor:pointer;user-select:none">${label}</span>`;
  }).join('');

  const mid = isAll
    ? `<div style="padding:13px 18px;font-size:11px;letter-spacing:.08em;color:#eaffec;border-right:1px solid #262626;background:#12200f;border-bottom:2px solid #7ee787">ACCOUNTS OVERVIEW</div>
       <div style="padding:13px 18px;font-size:10px;letter-spacing:.08em;color:#61805f">${(S.overview ? S.overview.accounts.length : accs.length)} PAPER BOOKS · REPORTED IN AUD</div>`
    : `<div style="display:flex;font-size:11px;letter-spacing:.08em">${tabs}</div>`;

  const base = (S.meta && S.meta.base_currency) || 'AUD';
  return `
  <div style="display:flex;align-items:center;gap:0;background:#0d0d0d;border-bottom:1px solid #262626;flex:none">
    <div style="display:flex;align-items:center;gap:10px;padding:12px 18px;border-right:1px solid #262626">
      <span style="width:9px;height:9px;background:#7ee787;border-radius:1px;box-shadow:0 0 8px rgba(126,231,135,.8)"></span>
      <span style="font-size:14px;font-weight:600;color:#eaffec;letter-spacing:.1em">MOMENTUM/3R</span>
    </div>
    <div style="display:flex;align-items:center;gap:4px;padding:0 14px;border-right:1px solid #262626;align-self:stretch;flex-wrap:wrap">${chipHtml}</div>
    ${mid}
    <div style="margin-left:auto;display:flex;align-items:center;gap:14px;padding:0 18px;font-size:10px;white-space:nowrap">
      <span style="display:inline-flex;align-items:center;gap:6px;color:#61805f">TEXT
        <span class="hv-dim" data-act="zoom" data-arg="out" title="Smaller" style="border:1px solid #262626;padding:2px 7px;border-radius:2px;cursor:pointer;user-select:none;color:#c9e8cc">A−</span>
        <span style="color:#eaffec;min-width:30px;text-align:center" title="Text size">${Math.round(zoomLevel() * 100)}%</span>
        <span class="hv-dim" data-act="zoom" data-arg="in" title="Larger" style="border:1px solid #262626;padding:2px 7px;border-radius:2px;cursor:pointer;user-select:none;color:#c9e8cc">A+</span>
      </span>
      <span style="color:#61805f">ACCT <span style="color:#eaffec">${esc(S.account || '')}</span></span>
      <span style="color:#61805f">MODE <span style="color:#e3b341">PAPER</span></span>
      ${page && page.synthetic ? '<span style="color:#e3b341;border:1px solid #4a3a1a;background:rgba(227,179,65,.08);padding:3px 8px;border-radius:2px">SYNTHETIC DATA</span>' : ''}
      <span style="color:#61805f">BASE <span style="color:#eaffec">${esc(base)}</span></span>
      <span id="livechip" style="${liveChipStyle()}"><span id="clock">${clockStr()}</span> UTC · <span id="conn">${connLabel()}</span></span>
    </div>
  </div>`;
}

/* ---- whole-terminal zoom (fixed-px screens, so we scale the lot) ---- */
const ZOOM_MIN = 0.8, ZOOM_MAX = 2.2, ZOOM_STEP = 0.1;

function defaultZoom() {
  const w = window.innerWidth || 1440;
  if (w >= 3200) return 1.6;       // 32"+ at native / low scaling
  if (w >= 2560) return 1.4;       // 1440p and up
  if (w >= 1920) return 1.2;
  if (w >= 1600) return 1.1;
  return 1.0;
}

function zoomLevel() {
  if (S.zoom == null) {
    let saved = null;
    try { saved = parseFloat(localStorage.getItem('m3r_zoom')); } catch (e) { /* private mode */ }
    S.zoom = saved && isFinite(saved) ? saved : defaultZoom();
  }
  return S.zoom;
}

function applyZoom() {
  document.documentElement.style.setProperty('--m3r-zoom', zoomLevel().toFixed(2));
}

function stepZoom(dir) {
  const z = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN,
    +(zoomLevel() + (dir === 'in' ? ZOOM_STEP : -ZOOM_STEP)).toFixed(2)));
  S.zoom = z;
  try { localStorage.setItem('m3r_zoom', String(z)); } catch (e) { /* private mode */ }
  applyZoom();
  render();                       // refresh the % readout
}

/* ---- equity (FULL) ticker tape ---- */
function equityTapeHTML(page) {
  if (!page) return '';
  const fx = page.fx || {};
  const items = [];
  if (fx.USD) items.push(`<span style="color:#61805f">AUD/USD <span style="color:#c9e8cc">${num(1 / fx.USD, 4)}</span></span>`);
  if (fx.GBP) items.push(`<span style="color:#61805f">GBP/AUD <span style="color:#c9e8cc">${num(fx.GBP, 4)}</span></span>`);
  if (fx.USD) items.push(`<span style="color:#61805f">USD/AUD <span style="color:#c9e8cc">${num(fx.USD, 4)}</span></span>`);
  items.push(`<span style="color:#2e2e2e">│</span>`);
  for (const ix of page.index_state || []) {
    items.push(`<span style="color:#61805f">${esc(ix.symbol)} <span style="color:${ix.risk_on ? G : R}">${ix.risk_on ? 'ABOVE' : 'BELOW'} ${(S.meta ? S.meta.params.index_trend_ma : 200)}D</span></span>`);
  }
  items.push(`<span style="color:#2e2e2e">│</span>`);
  items.push(`<span style="color:#61805f">PEAK <span style="color:#c9e8cc">A$${num(page.peak_equity, 2)}</span> · OFF-PEAK <span style="color:${page.off_peak < 0 ? AMB : TXT}">${sgnPct(page.off_peak, 2)}</span></span>`);
  items.push(page.risk_halted
    ? `<span style="color:#61805f">BREAKER <span style="color:${R}">TRIPPED</span> @ −${num(page.breaker * 100, 0)}%</span>`
    : `<span style="color:#61805f">BREAKER <span style="color:${G}">ARMED</span> @ −${num(page.breaker * 100, 0)}%</span>`);
  const sched = (S.meta && S.meta.schedule) || [];
  const closes = sched.map(s => `${REGION_SHORT[s.region] || s.region} ${s.close_hhmm}`).join(' · ');
  items.push(`<span style="margin-left:auto;color:#61805f">NEXT REBAL <span style="color:#c9e8cc">${esc(page.next_rebalance || '')}</span>${closes ? ` · <span style="color:#c9e8cc">${esc(closes)} UTC</span>` : ''}</span>`);
  return `<div style="display:flex;align-items:center;gap:22px;padding:7px 18px;background:#090909;border-bottom:1px solid #262626;font-size:10px;overflow:hidden;white-space:nowrap;flex:none">${items.join('\n')}</div>`;
}

/* ---- agent / small account ticker tape ---- */
function acctTapeHTML(items, right) {
  const cells = items.map(t =>
    `<span style="color:#61805f">${esc(t.k)} <span style="color:${t.c || TXT}">${esc(t.v)}</span></span>`).join('\n');
  return `<div style="display:flex;align-items:center;gap:22px;padding:7px 18px;background:#090909;border-bottom:1px solid #262626;font-size:10px;overflow:hidden;white-space:nowrap;flex:none">${cells}
    <span style="margin-left:auto;color:#61805f">${right}</span></div>`;
}

function statusBarHTML(page) {
  const sched = (S.meta && S.meta.schedule) || [];
  const nxt = sched[0];
  const wake = nxt ? `NEXT WAKE ${nxt.close_hhmm} UTC (${REGION_SHORT[nxt.region] || nxt.region} CLOSE)` : 'SCHEDULE UNAVAILABLE';
  let mark = '';
  if (page) mark = page.as_of || page.last_bar_date || '';
  const tests = S.meta && S.meta.tests_total;
  return `
  <div style="display:flex;align-items:center;gap:20px;padding:7px 18px;background:#0d0d0d;border-top:1px solid #262626;font-size:9px;color:#61805f;letter-spacing:.06em;flex:none">
    <span><span style="color:#7ee787">●</span> ENGINE IDLE — ${esc(wake)}</span>
    ${mark ? `<span>LAST MARK ${esc(mark)}</span>` : ''}
    ${tests ? `<span>${tests} TESTS</span>` : ''}
    ${S.exportAll ? '<span style="margin-left:auto"><a href="books.html" style="color:#61805f;text-decoration:none" class="hv-dim">ALL PAGES →</a></span><span>' : '<span style="margin-left:auto">'}SIGNALS ≤ T · EXECUTION T+1 · NO LOOKAHEAD</span>
  </div>`;
}

function errPanel(key) {
  const err = S.errors[key] || 'unknown error';
  return `<div class="err-panel">⚠ DATA UNAVAILABLE — ${esc(err)}<br>
  <span class="hint">Equity books mark positions to the latest market data; without network access start the server with --synthetic for an offline pipeline test, or re-run once data is reachable. FX books and the accounts overview read state files and stay live.</span></div>`;
}

/* ========================= equity view-model =========================== */
function prepEquity(page) {
  const rows = [];
  for (const s of page.sleeves || []) {
    const sym = SYM[s.currency] || s.currency;
    for (const p of s.positions || []) {
      rows.push({ ...p, region: s.key, currency: s.currency, sym });
    }
  }
  rows.sort((a, b) => b.weight - a.weight);
  const maxW = rows.length ? Math.max(...rows.map(r => r.weight)) : 1;

  const blotter = page.blotter || [];
  const lastDate = blotter.length ? blotter[blotter.length - 1].date : null;
  const feed = blotter.filter(t => t.date === lastDate).slice().reverse();

  /* latest BUY per ticker → entry marker date on the popover */
  const lastBuy = {};
  for (const t of blotter) if (t.side === 'BUY') lastBuy[t.ticker] = t.date;

  const curve = (page.equity_curve || []).map(p => ({ date: p.date, v: p.equity }));
  return { rows, maxW, feed, lastDate, lastBuy, curve };
}

function rangeFilter(curve) {
  if (!curve.length) return curve;
  const days = S.range === '1M' ? 31 : S.range === '3M' ? 93 : null;
  if (!days) return curve;
  const end = new Date(curve[curve.length - 1].date);
  const cut = new Date(end.getTime() - days * 86400e3).toISOString().slice(0, 10);
  const out = curve.filter(p => p.date >= cut);
  return out.length > 1 ? out : curve;
}

function ddSeries(vals) {
  let peak = -Infinity;
  return vals.map(v => { peak = Math.max(peak, v); return peak ? v / peak - 1 : 0; });
}

function axisDates(dates, n) {
  if (dates.length <= n) return dates.map(mmdd);
  const out = [];
  for (let i = 0; i < n; i++) out.push(mmdd(dates[Math.round(i * (dates.length - 1) / (n - 1))]));
  return out;
}

const rangeChips = () => ['1M', '3M', 'ALL'].map(k => {
  const on = S.range === k;
  return `<span class="hv-dim" data-act="range" data-arg="${k}" style="padding:2px 8px;cursor:pointer;user-select:none;${on ? 'background:#12200f;color:#7ee787;border:1px solid #2a4a2c' : 'color:#61805f;border:1px solid #262626'}">${k}</span>`;
}).join('');

/* popover container: side-flip calc + chrome shared by all row popovers */
function popShell(rect, need, left, width, pad, inner) {
  const side = (rect.top >= need || rect.top > (window.innerHeight - rect.bottom))
    ? 'bottom:calc(100% + 7px)' : 'top:calc(100% + 6px)';
  return `<div class="pop" style="position:absolute;${side};left:${left}px;width:${width}px;background:#0d0d0d;border:1px solid #2a4a2c;border-radius:3px;box-shadow:0 12px 36px rgba(0,0,0,.75),0 0 22px rgba(126,231,135,.05);z-index:60;padding:${pad};pointer-events:none">${inner}</div>`;
}

/* row attributes shared by every hoverable position row */
const hovAttrs = (kind, key) =>
  `data-hovkind="${kind}" data-hov="${esc(key)}"`;

/* ========================= FULL · OVERVIEW ============================= */
function equityOverviewHTML(page) {
  const M = prepEquity(page);
  const k = page.kpis;
  const [eqInt, eqDec] = moneySplit(k.total_equity);
  const filt = rangeFilter(M.curve);
  const vals = filt.map(p => p.v);
  const dates = filt.map(p => p.date);
  const p140 = toPts(vals, 600, 140, 10);
  const eqPts140 = p140.join(' ');
  const eqArea140 = vals.length ? '0,140 ' + eqPts140 + ' 600,140' : '';
  const eqLastY = p140.length ? p140[p140.length - 1].split(',')[1] : 70;
  const eqSpark = toPts(vals, 120, 24, 2).join(' ');
  const dd = ddSeries(vals);
  const ddMin = Math.min(...dd, -1e-9);
  const ddPts = dd.map((d, i) => ((i / Math.max(dd.length - 1, 1)) * 600).toFixed(1) + ',' + (2 + (d / ddMin) * 40).toFixed(1)).join(' ');
  const ddArea = dd.length ? '0,2 ' + ddPts + ' 600,2' : '';
  const axis = axisDates(dates, 5);

  /* per-region position counts, in sleeve order */
  const posCounts = (page.sleeves || []).map(s => `${s.key} ${(s.positions || []).length}`).join(' · ');
  const stampGbp = (page.stamp_duty || []).find(s => s.currency === 'GBP');
  const feesSub = stampGbp ? `INCL £${num(stampGbp.amount, 0)} UK STAMP DUTY` : 'COMMISSIONS + SLIPPAGE, ALL SLEEVES';
  const sinceDate = M.curve.length ? M.curve[0].date : '';

  /* sleeve cards */
  const sleeveCurves = page.sleeve_curves || [];
  const sleeveCards = (page.sleeves || []).map((s, i, arr) => {
    const sym = SYM[s.currency] || s.currency;
    const nPos = (s.positions || []).length;
    const investedPct = 1 - s.cash_pct;
    const sVals = sleeveCurves.map(r => r[s.key]).filter(v => v != null);
    const mret = s.month_return;
    const stroke = nPos === 0 ? DIM : (mret != null && mret < 0 ? AMB : G);
    const dash = nPos === 0 ? ' stroke-dasharray="3 2"' : '';
    const spark = toPts(sVals, 120, 26, 3).join(' ');
    const eqTxt = s.currency === page.base_currency
      ? `A$${num(s.equity_local, 2)}`
      : `${money0(sym, s.equity_local)} <span style="font-size:11px;color:#61805f">→ A$${num(s.equity_base, 0)}</span>`;
    const sub = nPos === 0
      ? `CASH 100% · 0 POS · REBAL ${esc(String(s.last_rebalance_month || '').slice(5) || '—')}`
      : `${nPos} POS · ${pct0(investedPct)} INVESTED${mret != null ? ` · ${sgnPct(mret, 1)} M` : ''}`;
    const chip = s.regime === 'RISK_OFF'
      ? `<span style="font-size:9px;color:#ff7b72;border:1px solid #4a2a28;background:rgba(255,123,114,.06);padding:2px 7px">RISK_OFF → CASH</span>`
      : `<span style="font-size:9px;color:#7ee787;border:1px solid #2a4a2c;background:rgba(126,231,135,.06);padding:2px 7px">RISK_ON</span>`;
    const barColor = nPos === 0 ? G : stroke;
    return `
      <div style="padding:12px 18px;${i < arr.length - 1 ? 'border-bottom:1px solid #262626' : ''}">
        <div style="display:flex;justify-content:space-between;align-items:center"><span style="font-size:12px;color:#eaffec">${esc(s.key)} <span style="color:#61805f;font-size:10px">· ${esc(s.currency)} · ${num(s.weight * 100, 1)}%</span></span>${chip}</div>
        <div style="display:flex;align-items:flex-end;justify-content:space-between;margin-top:8px"><div><div style="font-size:16px;color:#eaffec;font-weight:600">${eqTxt}</div><div style="font-size:9px;color:#61805f;margin-top:2px">${sub}</div></div><svg viewBox="0 0 120 26" preserveAspectRatio="none" style="width:110px;height:26px"><polyline points="${spark}" fill="none" stroke="${stroke}" stroke-width="1.2"${dash}></polyline></svg></div>
        <div style="height:3px;background:#1a1a1a;margin-top:8px"><div style="height:3px;width:${(investedPct * 100).toFixed(0)}%;background:${barColor}"></div></div>
      </div>`;
  }).join('');

  /* open book rows */
  const bookRows = M.rows.map(p => `
    <div class="hv-row" ${hovAttrs('eq', p.region + ':' + p.ticker)} style="position:relative;display:grid;grid-template-columns:1.1fr .6fr .55fr .75fr .8fr 1fr .65fr .65fr;padding:5px 18px;font-size:11px;border-bottom:1px solid #121212;align-items:center;cursor:crosshair">
      <span style="color:#eaffec;text-decoration:underline;text-decoration-style:dotted;text-decoration-color:#3d543f;text-underline-offset:3px">${esc(p.ticker)}</span><span style="color:#61805f">${esc(p.region)}</span><span style="color:#9db5a0">${p.shares}</span><span style="color:#9db5a0">${px2(p.sym, p.price)}</span><span style="color:#c9e8cc">${money0(p.sym, p.value_local)}</span>
      <span style="display:flex;align-items:center;gap:7px"><span style="width:56px;height:3px;background:#1a1a1a;display:inline-block"><span style="display:block;height:3px;background:#7ee787;width:${(p.weight / M.maxW * 100).toFixed(0)}%"></span></span><span style="color:#61805f;font-size:10px">${num(p.weight * 100, 1)}%</span></span>
      <span style="color:${cSign(p.day_change)}">${sgnPct(p.day_change, 1)}</span><span style="color:${cSign(p.unrealized_pct)}">${sgnPct(p.unrealized_pct, 1)}</span>
    </div>`).join('');

  /* trade feed */
  const feedRows = M.feed.map(t => {
    const sym = SYM[t.currency] || t.currency;
    return `
    <div style="display:flex;align-items:center;gap:9px;padding:5px 18px;font-size:10.5px;border-bottom:1px solid #121212">
      <span style="color:#3d543f">${mmdd(t.date)}</span>
      <span style="width:32px;font-weight:600;color:${t.side === 'BUY' ? G : R}">${t.side}</span>
      <span style="color:#eaffec;width:58px">${esc(t.ticker)}</span>
      <span style="color:#9db5a0">${t.shares} @ ${pxFill(sym, t.fill)}</span>
      <span style="color:#61805f;margin-left:auto">${sym}${num((t.commission || 0) + (t.stamp_duty || 0), 2)}</span>
    </div>`;
  }).join('');

  return `
  <div data-screen="overview">
    <div style="display:grid;grid-template-columns:1.5fr 1fr 1fr 1fr 1fr 1fr 1fr;border-bottom:1px solid #262626">
      <div style="padding:14px 18px;border-right:1px solid #262626;background:#0d0d0d">
        <div style="font-size:9px;color:#61805f;letter-spacing:.14em">TOTAL EQUITY · ${esc(page.base_currency)}</div>
        <div style="font-size:26px;font-weight:600;color:#eaffec;margin-top:5px;letter-spacing:-.01em">${eqInt}<span style="font-size:15px;color:#61805f">${eqDec}</span></div>
        <svg viewBox="0 0 120 24" preserveAspectRatio="none" style="width:100%;height:24px;margin-top:4px;display:block"><polyline points="${eqSpark}" fill="none" stroke="#7ee787" stroke-width="1.2"></polyline></svg>
      </div>
      <div style="padding:14px 16px;border-right:1px solid #262626"><div style="font-size:9px;color:#61805f;letter-spacing:.14em">TOTAL RETURN</div><div style="font-size:20px;font-weight:600;color:${cSign(k.total_return)};margin-top:8px">${sgnPct(k.total_return, 2)}</div><div style="font-size:9px;color:#3d543f;margin-top:4px">SINCE ${esc(sinceDate)}</div></div>
      <div style="padding:14px 16px;border-right:1px solid #262626"><div style="font-size:9px;color:#61805f;letter-spacing:.14em">DAY CHANGE</div><div style="font-size:20px;font-weight:600;color:${cSign(k.day_change)};margin-top:8px">${sgnPct(k.day_change, 2)}</div><div style="font-size:9px;color:#3d543f;margin-top:4px">${sgn(k.day_change_base, 'A$' + num(Math.abs(k.day_change_base), 2))}</div></div>
      <div style="padding:14px 16px;border-right:1px solid #262626"><div style="font-size:9px;color:#61805f;letter-spacing:.14em">NET P&amp;L</div><div style="font-size:20px;font-weight:600;color:${cSign(k.net_pnl_base)};margin-top:8px">${sgnNum(k.net_pnl_base, 2)}</div><div style="font-size:9px;color:#3d543f;margin-top:4px">REAL ${sgnNum(k.realized_base, 0)} · OPEN ${sgnNum(k.unrealized_base, 0)}</div></div>
      <div style="padding:14px 16px;border-right:1px solid #262626"><div style="font-size:9px;color:#61805f;letter-spacing:.14em">EXPOSURE</div><div style="font-size:20px;font-weight:600;color:#e3b341;margin-top:8px">${pct0(k.gross_exposure)}</div><div style="font-size:9px;color:#3d543f;margin-top:4px">${pct0(k.cash_pct)} CASH · VOL-TGT ${pct0(k.target_vol)}</div></div>
      <div style="padding:14px 16px;border-right:1px solid #262626"><div style="font-size:9px;color:#61805f;letter-spacing:.14em">POSITIONS</div><div style="font-size:20px;font-weight:600;color:#eaffec;margin-top:8px">${k.n_positions}</div><div style="font-size:9px;color:#3d543f;margin-top:4px">${esc(posCounts)}</div></div>
      <div style="padding:14px 16px"><div style="font-size:9px;color:#61805f;letter-spacing:.14em">FEES PAID</div><div style="font-size:20px;font-weight:600;color:#eaffec;margin-top:8px">A$${num(k.fees_base, 0)}</div><div style="font-size:9px;color:#3d543f;margin-top:4px">${feesSub}</div></div>
    </div>

    <div style="display:grid;grid-template-columns:2.1fr 1fr;border-bottom:1px solid #262626">
      <div style="border-right:1px solid #262626">
        <div style="padding:12px 18px 0">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
            <div style="display:flex;gap:14px;font-size:9px;letter-spacing:.12em"><span style="color:#eaffec">■ EQUITY CURVE · ${esc(page.base_currency)}</span><span style="color:#61805f">MIN ${vals.length ? num(Math.min(...vals), 2) : '—'}</span><span style="color:#61805f">MAX ${vals.length ? num(Math.max(...vals), 2) : '—'}</span></div>
            <div style="display:flex;gap:2px;font-size:9px">${rangeChips()}</div>
          </div>
          <svg viewBox="0 0 600 140" preserveAspectRatio="none" style="width:100%;height:150px;display:block">
            <line x1="0" y1="35" x2="600" y2="35" stroke="#1a1a1a" stroke-width="1"></line>
            <line x1="0" y1="70" x2="600" y2="70" stroke="#1a1a1a" stroke-width="1"></line>
            <line x1="0" y1="105" x2="600" y2="105" stroke="#1a1a1a" stroke-width="1"></line>
            <polygon points="${eqArea140}" fill="rgba(126,231,135,0.08)"></polygon>
            <polyline points="${eqPts140}" fill="none" stroke="#7ee787" stroke-width="1.6" stroke-linejoin="round"></polyline>
            ${vals.length ? `<circle cx="600" cy="${eqLastY}" r="3" fill="#7ee787"></circle>` : ''}
          </svg>
        </div>
        <div style="padding:8px 18px 14px;border-top:1px solid #1a1a1a;margin-top:10px">
          <div style="font-size:9px;color:#61805f;letter-spacing:.12em;margin:6px 0">DRAWDOWN FROM PEAK</div>
          <svg viewBox="0 0 600 44" preserveAspectRatio="none" style="width:100%;height:44px;display:block">
            <line x1="0" y1="1" x2="600" y2="1" stroke="#262626" stroke-width="1"></line>
            <polygon points="${ddArea}" fill="rgba(255,123,114,0.18)"></polygon>
            <polyline points="${ddPts}" fill="none" stroke="#ff7b72" stroke-width="1.2"></polyline>
          </svg>
          <div style="display:flex;justify-content:space-between;font-size:9px;color:#3d543f;margin-top:5px">${axis.map(d => `<span>${d}</span>`).join('')}</div>
        </div>
      </div>
      <div style="display:grid;grid-template-rows:repeat(${(page.sleeves || []).length || 1},1fr)">${sleeveCards}</div>
    </div>

    <div style="display:grid;grid-template-columns:2.1fr 1fr">
      <div style="border-right:1px solid #262626">
        <div style="display:flex;justify-content:space-between;align-items:center;padding:10px 18px;border-bottom:1px solid #1a1a1a"><span style="font-size:9px;color:#eaffec;letter-spacing:.14em">■ OPEN BOOK · ${M.rows.length} POSITIONS</span><span style="font-size:9px;color:#61805f">HOVER A TICKER FOR PRICE HISTORY</span></div>
        <div style="display:grid;grid-template-columns:1.1fr .6fr .55fr .75fr .8fr 1fr .65fr .65fr;padding:7px 18px;font-size:9px;color:#61805f;letter-spacing:.12em;border-bottom:1px solid #1a1a1a"><span>TICKER</span><span>REGION</span><span>QTY</span><span>PRICE</span><span>VALUE</span><span>WEIGHT</span><span>DAY</span><span>UNRL</span></div>
        ${bookRows || '<div style="padding:22px 18px;font-size:11px;color:#61805f">— NO OPEN POSITIONS.</div>'}
      </div>
      <div style="display:flex;flex-direction:column">
        <div style="padding:10px 18px;border-bottom:1px solid #1a1a1a;font-size:9px;color:#eaffec;letter-spacing:.14em">■ TRADE FEED${M.lastDate ? ` · ${mdy(M.lastDate)} REBALANCE` : ''}</div>
        <div>${feedRows}</div>
        <div style="margin-top:auto;padding:12px 18px;border-top:1px solid #262626;background:#0d0d0d">
          <div style="font-size:9px;color:#61805f;letter-spacing:.14em;margin-bottom:8px">TOTAL FINANCIAL POSITION · ${esc(page.base_currency)}</div>
          <div style="display:flex;justify-content:space-between;font-size:11px;padding:2px 0"><span style="color:#61805f">INVESTED</span><span style="color:#c9e8cc">${num(k.invested_base, 0)}</span></div>
          <div style="display:flex;justify-content:space-between;font-size:11px;padding:2px 0"><span style="color:#61805f">CASH</span><span style="color:#c9e8cc">${num(k.cash_base, 0)}</span></div>
          <div style="display:flex;justify-content:space-between;font-size:11px;padding:2px 0"><span style="color:#61805f">OPEN P&amp;L</span><span style="color:${cSign(k.unrealized_base)}">${sgnNum(k.unrealized_base, 0)}</span></div>
          <div style="display:flex;justify-content:space-between;font-size:11px;padding:2px 0"><span style="color:#61805f">FEES TO DATE</span><span style="color:#ff7b72">−${num(k.fees_base, 0)}</span></div>
          <div style="display:flex;justify-content:space-between;font-size:11px;padding:4px 0 0;border-top:1px solid #262626;margin-top:5px"><span style="color:#eaffec">EQUITY</span><span style="color:#eaffec;font-weight:600">${num(k.total_equity, 2)}</span></div>
        </div>
      </div>
    </div>
  </div>`;
}

/* ===================== equity position popover ========================= */
/* Built lazily on mouseenter (keeps table scroll intact). Returns HTML. */
function equityPopHTML(page, region, ticker, rect) {
  const M = prepEquity(page);
  const p = M.rows.find(r => r.region === region && r.ticker === ticker);
  if (!p) return '';
  const hist = (page.history || {})[ticker];
  let s, real, buyIdx;
  const dates = hist ? hist.dates : [];
  if (hist && hist.closes && hist.closes.length > 10) {
    s = hist.closes; real = true;
    const bd = M.lastBuy[ticker];
    buyIdx = bd ? Math.max(dates.findIndex(d => d >= bd), 0) : 0;
    if (buyIdx < 0) buyIdx = 0;
  } else {
    s = synthSeries(ticker, p.price, p.avg_cost, 49); real = false; buyIdx = 49;
  }
  const lo = Math.min(...s), hi = Math.max(...s);
  const X = i => (i / (s.length - 1)) * 360;
  const Y = v => 8 + (1 - (v - lo) / (hi - lo || 1)) * 88;
  const pts = s.map((v, i) => X(i).toFixed(1) + ',' + Y(v).toFixed(1)).join(' ');
  const up = s[s.length - 1] >= s[0];
  const dp = p.price < 3 ? 4 : 2;
  const fmt = v => p.sym + num(v, dp);
  const chgPct = (s[s.length - 1] / s[0] - 1) * 100;
  const entry = p.avg_cost;
  const buyDate = mdy(M.lastBuy[ticker] || '');
  /* month labels under the sparkline */
  const months = [];
  if (dates.length) {
    const seen = new Set();
    for (const d of dates) {
      const m = mdy(d).split(' ')[0];
      if (!seen.has(m)) { seen.add(m); months.push(m); }
    }
  }
  const monthLabels = (months.length ? months : ['', '', '']).map(m => `<span>${m}</span>`).join('');
  const pnlColor = cSign(p.unrealized_pct);
  return popShell(rect, 350, 230, 392, '12px 14px', `
    <div style="display:flex;align-items:baseline;gap:10px;margin-bottom:2px">
      <span style="font-size:13px;font-weight:600;color:#eaffec">${esc(ticker)}</span>
      <span style="font-size:9px;color:#61805f;letter-spacing:.1em">${esc(region)} · 90-DAY PRICE</span>
      <span style="font-size:8px;letter-spacing:.12em;border:1px solid #262626;padding:1px 6px;color:${real ? G : AMB}">${real ? 'LIVE DATA' : 'SYNTHETIC'}</span>
      <span style="margin-left:auto;font-size:13px;font-weight:600;color:#eaffec">${px2(p.sym, p.price)}</span>
      <span style="font-size:11px;color:${chgPct >= 0 ? G : R}">${sgn(chgPct, num(Math.abs(chgPct), 1))}% 90D</span>
    </div>
    <svg viewBox="0 0 360 104" style="width:100%;height:104px;display:block">
      <line x1="0" y1="26" x2="360" y2="26" stroke="#1a1a1a" stroke-width="1"></line>
      <line x1="0" y1="52" x2="360" y2="52" stroke="#1a1a1a" stroke-width="1"></line>
      <line x1="0" y1="78" x2="360" y2="78" stroke="#1a1a1a" stroke-width="1"></line>
      <polygon points="0,104 ${pts} 360,104" fill="${up ? 'rgba(126,231,135,0.08)' : 'rgba(255,123,114,0.08)'}"></polygon>
      <polyline points="${pts}" fill="none" stroke="${up ? G : R}" stroke-width="1.4" stroke-linejoin="round"></polyline>
      <line x1="0" y1="${Y(entry).toFixed(1)}" x2="360" y2="${Y(entry).toFixed(1)}" stroke="#e3b341" stroke-width="1" stroke-dasharray="4 3" opacity="0.75"></line>
      <circle cx="${X(buyIdx).toFixed(1)}" cy="${Y(entry).toFixed(1)}" r="3.2" fill="#e3b341" stroke="#060606" stroke-width="1.2"></circle>
    </svg>
    <div style="display:flex;justify-content:space-between;font-size:9px;color:#3d543f;margin-top:2px">${monthLabels}</div>
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:8px;margin-top:9px;padding-top:9px;border-top:1px solid #1a1a1a">
      <div><div style="font-size:8px;color:#61805f;letter-spacing:.12em">90D HIGH</div><div style="font-size:11px;color:#c9e8cc;margin-top:2px">${fmt(hi)}</div></div>
      <div><div style="font-size:8px;color:#61805f;letter-spacing:.12em">90D LOW</div><div style="font-size:11px;color:#c9e8cc;margin-top:2px">${fmt(lo)}</div></div>
      <div><div style="font-size:8px;color:#61805f;letter-spacing:.12em">ENTRY <span style="color:#e3b341">◆</span></div><div style="font-size:11px;color:#e3b341;margin-top:2px">${fmt(entry)}${buyDate ? ' · ' + buyDate : ''}</div></div>
      <div><div style="font-size:8px;color:#61805f;letter-spacing:.12em">UNRL P&amp;L</div><div style="font-size:11px;color:${pnlColor};margin-top:2px">${sgnPct(p.unrealized_pct, 1)}</div></div>
    </div>`);
}

/* closed-trades rows shared by the FULL and SMALL position screens; each
   row formats in its own currency (books can mix regions). */
function closedRowsHTML(closed, opts = {}) {
  const rows = (closed && closed.rows) || [];
  const maxRet = rows.length ? Math.max(...rows.map(r => Math.abs(r.return_pct)), 1e-9) : 1;
  return rows.map(c => {
    const sym = SYM[c.currency] || c.currency;
    const nc = cSign(c.net);
    const retCell = opts.bar === false
      ? `<span style="color:${nc}">${sgnPct(c.return_pct, 1)}</span>`
      : `<span style="display:flex;align-items:center;gap:6px"><span style="color:${nc}">${sgnPct(c.return_pct, 1)}</span><span style="width:34px;height:3px;background:#1a1a1a;display:inline-block"><span style="display:block;height:3px;background:${nc};width:${Math.min(Math.abs(c.return_pct) / maxRet * 100, 100).toFixed(0)}%"></span></span></span>`;
    return `
    <div class="hv-row" style="display:grid;grid-template-columns:.65fr .9fr .55fr .45fr 1.15fr .5fr .75fr .7fr .85fr .8fr .65fr;padding:6px 18px;font-size:10.5px;border-bottom:1px solid #121212;align-items:center">
      <span style="color:#3d543f">${esc(c.date)}</span>
      <span style="color:#eaffec;font-weight:600">${esc(c.ticker)} <span style="font-weight:400;color:#3d543f;font-size:9px">${esc(c.note)}</span></span>
      <span style="color:#61805f">${esc(c.region)}</span>
      <span style="color:#9db5a0">${c.qty}</span>
      <span style="color:#9db5a0">${pxFill(sym, c.entry)} <span style="color:#3d543f">→</span> ${pxFill(sym, c.exit)}</span>
      <span style="color:#61805f">${c.held_days}D</span>
      <span style="color:${cSign(c.gross)}">${sgn(c.gross, sym + num(Math.abs(c.gross), 2))}</span>
      <span style="color:#e3b341">${sym}${num(c.costs, 2)}</span>
      <span style="color:${nc};font-weight:600">${sgn(c.net, sym + num(Math.abs(c.net), 2))}</span>
      <span style="color:${nc}">${sgn(c.net_base, 'A$' + num(Math.abs(c.net_base), 2))}</span>
      ${retCell}
    </div>`;
  }).join('');
}

/* ========================= FULL · POSITIONS ============================ */
function equityPositionsHTML(page) {
  const M = prepEquity(page);
  const ma = S.meta ? S.meta.params.index_trend_ma : 200;

  const sleeveBlocks = (page.sleeves || []).map(s => {
    const sym = SYM[s.currency] || s.currency;
    const country = REGION_COUNTRY[s.key] || String(s.key).toUpperCase();
    const rows = M.rows.filter(r => r.region === s.key);
    const eqTxt = s.currency === page.base_currency
      ? `A$${num(s.equity_local, 2)}`
      : `${money0(sym, s.equity_local)} → A$${num(s.equity_base, 0)}`;
    const chip = s.regime === 'RISK_OFF'
      ? `<span style="font-size:9px;color:#ff7b72;border:1px solid #4a2a28;padding:2px 8px">RISK_OFF → CASH</span>`
      : `<span style="font-size:9px;color:#7ee787;border:1px solid #2a4a2c;padding:2px 8px">RISK_ON</span>`;
    const body = rows.length === 0
      ? `<div style="padding:22px 18px;font-size:11px;color:#61805f">— NO OPEN POSITIONS.${s.regime === 'RISK_OFF' ? ` ${esc(s.index_ticker)} IS BELOW ITS ${ma}-DAY MA, SO THIS BOOK HOLDS 100% CASH UNTIL THE REGIME TURNS RISK-ON.` : ''}</div>`
      : `<div style="display:grid;grid-template-columns:1fr .55fr .8fr .8fr .85fr .9fr .9fr .65fr .7fr .75fr;padding:7px 18px;font-size:9px;color:#61805f;letter-spacing:.12em;border-bottom:1px solid #1a1a1a"><span>TICKER</span><span>QTY</span><span>AVG COST</span><span>PRICE</span><span>VALUE LOC</span><span>VALUE AUD</span><span>WEIGHT</span><span>DAY</span><span>UNRL %</span><span>UNRL AUD</span></div>` +
        rows.map(p => `
        <div class="hv-row" ${hovAttrs('eq', p.region + ':' + p.ticker)} style="position:relative;display:grid;grid-template-columns:1fr .55fr .8fr .8fr .85fr .9fr .9fr .65fr .7fr .75fr;padding:6px 18px;font-size:11px;border-bottom:1px solid #121212;align-items:center;cursor:crosshair">
          <span style="color:#eaffec;text-decoration:underline;text-decoration-style:dotted;text-decoration-color:#3d543f;text-underline-offset:3px">${esc(p.ticker)}</span><span style="color:#9db5a0">${p.shares}</span><span style="color:#e3b341">${pxFill(p.sym, p.avg_cost)}</span><span style="color:#9db5a0">${px2(p.sym, p.price)}</span><span style="color:#c9e8cc">${money0(p.sym, p.value_local)}</span><span style="color:#c9e8cc">A$${num(p.value_base, 0)}</span>
          <span style="display:flex;align-items:center;gap:7px"><span style="width:48px;height:3px;background:#1a1a1a;display:inline-block"><span style="display:block;height:3px;background:#7ee787;width:${(p.weight / M.maxW * 100).toFixed(0)}%"></span></span><span style="color:#61805f;font-size:10px">${num(p.weight * 100, 1)}%</span></span>
          <span style="color:${cSign(p.day_change)}">${sgnPct(p.day_change, 1)}</span><span style="color:${cSign(p.unrealized_pct)}">${sgnPct(p.unrealized_pct, 1)}</span><span style="color:${cSign(p.unrealized_base)}">${sgn(p.unrealized_base, 'A$' + num(Math.abs(p.unrealized_base), 0))}</span>
        </div>`).join('');
    return `
    <div style="border-bottom:1px solid #262626">
      <div style="display:flex;align-items:center;gap:16px;padding:12px 18px;background:#0d0d0d;border-bottom:1px solid #1a1a1a">
        <span style="font-size:13px;font-weight:600;color:#eaffec;letter-spacing:.08em">${esc(s.key)} · ${esc(country)}</span>
        ${chip}
        <span style="font-size:10px;color:#61805f">EQUITY <span style="color:#c9e8cc">${eqTxt}</span></span>
        <span style="font-size:10px;color:#61805f">CASH <span style="color:#c9e8cc">${num(s.cash_pct * 100, 1)}%</span></span>
        <span style="font-size:10px;color:#61805f">FX <span style="color:#c9e8cc">${num(s.fx_rate, 4)}</span></span>
        <span style="margin-left:auto;font-size:10px;color:#61805f">LAST REBALANCE <span style="color:#c9e8cc">${esc(s.last_rebalance_month || '—')}</span></span>
      </div>
      ${body}
    </div>`;
  }).join('');

  const blotterRows = (page.blotter || []).map(t => {
    const sym = SYM[t.currency] || t.currency;
    return `
    <div class="hv-row" style="display:grid;grid-template-columns:.7fr .6fr .5fr 1fr .55fr .8fr .9fr .7fr .7fr;padding:4px 18px;font-size:10.5px;border-bottom:1px solid #121212">
      <span style="color:#3d543f">${esc(t.date)}</span><span style="color:#61805f">${esc(t.region)}</span><span style="font-weight:600;color:${t.side === 'BUY' ? G : R}">${t.side}</span><span style="color:#eaffec">${esc(t.ticker)}</span><span style="color:#9db5a0">${t.shares}</span><span style="color:#9db5a0">${pxFill(sym, t.fill)}</span><span style="color:#c9e8cc">${money0(sym, t.value)}</span><span style="color:#61805f">${sym}${num(t.commission || 0, 2)}</span><span style="color:${t.stamp_duty ? AMB : FAINT}">${t.stamp_duty ? sym + num(t.stamp_duty, 2) : '—'}</span>
    </div>`;
  }).join('');

  const closed = page.closed || { rows: [], wins: 0, count: 0, net_base: 0, by_currency: [] };
  /* per-region nets for the header strip */
  const regionNet = {};
  for (const r of closed.rows) {
    regionNet[r.region] = regionNet[r.region] || { sym: SYM[r.currency] || r.currency, v: 0 };
    regionNet[r.region].v += r.net;
  }
  const regionNetHtml = Object.entries(regionNet).map(([reg, o]) =>
    `<span style="font-size:10px;color:#61805f">${esc(reg)} <span style="color:${cSign(o.v)}">${sgn(o.v, o.sym + num(Math.abs(o.v), 2))}</span></span>`).join('\n');

  const closedRows = closedRowsHTML(closed);

  return `
  <div data-screen="positions">
    ${sleeveBlocks}
    <div style="display:flex;justify-content:space-between;align-items:center;padding:12px 18px;background:#0d0d0d;border-bottom:1px solid #1a1a1a">
      <span style="font-size:9px;color:#eaffec;letter-spacing:.14em">■ TRADE BLOTTER · ALL ${(page.blotter || []).length} FILLS</span>
      <span style="font-size:9px;color:#61805f">COMMISSIONS + UK STAMP DUTY (50BPS ON FTSE BUYS) ITEMISED</span>
    </div>
    <div style="display:grid;grid-template-columns:.7fr .6fr .5fr 1fr .55fr .8fr .9fr .7fr .7fr;padding:7px 18px;font-size:9px;color:#61805f;letter-spacing:.12em;border-bottom:1px solid #1a1a1a"><span>DATE</span><span>REGION</span><span>SIDE</span><span>TICKER</span><span>QTY</span><span>FILL</span><span>VALUE</span><span>COMM</span><span>STAMP</span></div>
    ${blotterRows}
    <div style="display:flex;align-items:center;gap:18px;padding:12px 18px;background:#0d0d0d;border-top:1px solid #262626;border-bottom:1px solid #1a1a1a">
      <span style="font-size:9px;color:#eaffec;letter-spacing:.14em">■ CLOSED TRADES · REALIZED P&amp;L (FIFO, FROM FILLS)</span>
      ${regionNetHtml}
      <span style="font-size:10px;color:#61805f">NET <span style="color:${cSign(closed.net_base)}">${sgn(closed.net_base, 'A$' + num(Math.abs(closed.net_base), 2))}</span></span>
      <span style="font-size:10px;color:#61805f">WIN RATE <span style="color:#c9e8cc">${closed.wins} / ${closed.count}</span></span>
      <span style="margin-left:auto;font-size:9px;color:#3d543f">INCLUDES COMMISSIONS + UK STAMP DUTY · FILLS ALREADY CARRY MODELLED SPREAD/SLIPPAGE</span>
    </div>
    <div style="display:grid;grid-template-columns:.65fr .9fr .55fr .45fr 1.15fr .5fr .75fr .7fr .85fr .8fr .65fr;padding:7px 18px;font-size:9px;color:#61805f;letter-spacing:.12em;border-bottom:1px solid #1a1a1a"><span>CLOSED</span><span>TICKER</span><span>REGION</span><span>QTY</span><span>ENTRY → EXIT</span><span>HELD</span><span>GROSS</span><span>COSTS</span><span>NET LOCAL</span><span>NET AUD</span><span>RETURN</span></div>
    ${closedRows || '<div style="padding:22px 18px;font-size:11px;color:#61805f">— NO CLOSED ROUND-TRIPS YET.</div>'}
  </div>`;
}

/* ========================= BACKTEST (equity) =========================== */
function _illustrativeBt() {
  const brnd = mix32(88);
  const months = 174;
  const strat = [1], bench = [1];
  for (let i = 1; i <= months; i++) {
    const yr = 2012 + i / 12;
    let shock = 0;
    if (yr > 2015.5 && yr < 2016.1) shock = -0.012;
    if (yr > 2020.1 && yr < 2020.4) shock = -0.05;
    if (yr > 2022.0 && yr < 2022.8) shock = -0.018;
    bench.push(bench[i - 1] * (1 + 0.0065 + shock * 1.6 + (brnd() - 0.5) * 0.035));
    strat.push(strat[i - 1] * (1 + 0.0112 + shock * 0.7 + (brnd() - 0.5) * 0.030));
  }
  const dates = strat.map((_, i) => `${2012 + Math.floor(i / 12)}-${String(i % 12 + 1).padStart(2, '0')}-01`);
  return { strat: strat.map((v, i) => [dates[i], v * 100000]), bench: bench.map((v, i) => [dates[i], v * 100000]) };
}

function _btCurvesHTML(stratCurve, benchCurve) {
  const sv = stratCurve.map(p => p[1]), bv = benchCurve.map(p => p[1]);
  const lo = Math.min(...sv, ...bv), hi = Math.max(...sv, ...bv);
  const logY = v => 12 + (1 - (Math.log(v) - Math.log(lo)) / ((Math.log(hi) - Math.log(lo)) || 1)) * 216;
  const n = stratCurve.length - 1 || 1, nb = benchCurve.length - 1 || 1;
  const btPts = sv.map((v, i) => ((i / n) * 1200).toFixed(1) + ',' + logY(v).toFixed(1)).join(' ');
  const btBench = bv.map((v, i) => ((i / nb) * 1200).toFixed(1) + ',' + logY(v).toFixed(1)).join(' ');
  const dd = ddSeries(sv);
  const ddMin = Math.min(...dd, -1e-9);
  const btDd = dd.map((d, i) => ((i / n) * 1200).toFixed(1) + ',' + (2 + (d / ddMin) * 56).toFixed(1)).join(' ');
  const years = [];
  const seen = new Set();
  for (const [d] of stratCurve) {
    const y = d.slice(0, 4);
    if (!seen.has(y) && +y % 2 === 0) { seen.add(y); years.push(y); }
  }
  return { btPts, btBench, btArea: '0,240 ' + btPts + ' 1200,240', btDd, btDdArea: '0,2 ' + btDd + ' 1200,2', years };
}

function costFootnote() {
  const regions = (S.meta && S.meta.regions) || [];
  if (!regions.length) return 'COSTS MODELLED: COMMISSION FLOORS, SLIPPAGE AND STAMP DUTY WHERE IT APPLIES.';
  const floors = regions.map(r => (SYM[r.currency] || r.currency) + num(r.min_commission, 0)).join(' / ');
  const slip = regions.map(r => num(r.slippage_bps, 0)).join('/');
  const stamp = regions.filter(r => r.stamp_duty_bps > 0)
    .map(r => `${num(r.stamp_duty_bps, 0)}BPS ${r.key} STAMP DUTY ON BUYS`).join(', ');
  return `COSTS MODELLED: COMMISSION FLOORS (${floors}), SLIPPAGE (${slip} BPS)${stamp ? ' AND ' + stamp : ''}.`;
}

const kpiCell = (label, val, color, sub, last) => `
  <div style="padding:14px 18px;${last ? '' : 'border-right:1px solid #262626'}"><div style="font-size:9px;color:#61805f;letter-spacing:.14em">${label}</div><div style="font-size:22px;font-weight:600;color:${color};margin-top:6px">${val}</div><div style="font-size:9px;color:#3d543f;margin-top:3px">${sub}</div></div>`;

function backtestHTML(page) {
  const bt = S.backtests[S.account];
  if (!bt) return '<div class="boot">LOADING BACKTEST…</div>';
  const real = bt.available && bt.kind === 'equity';

  let banner, curves, m, bm, sleeves, sweep, period;
  if (real) {
    curves = _btCurvesHTML(bt.curve, bt.benchmark);
    m = bt.metrics || {}; bm = bt.benchmark_metrics || {};
    sleeves = bt.sleeves || [];
    sweep = bt.sweep || null;
    period = `${String(bt.start).slice(0, 7)} → ${String(bt.end).slice(0, 7)}`;
    banner = bt.synthetic
      ? `<span style="font-weight:600">⚠ SYNTHETIC DATA</span><span style="color:#8a7433">PIPELINE TEST ONLY — NEVER PERFORMANCE. RE-RUN THE EXPORT WITH REAL DATA.</span>`
      : bt.point_in_time
        ? `<span style="font-weight:600">POINT-IN-TIME UNIVERSE</span><span style="color:#8a7433">SURVIVORSHIP-CORRECTED CONSTITUENTS.</span>`
        : `<span style="font-weight:600">⚠ SURVIVORSHIP-BIASED SAMPLE</span><span style="color:#8a7433">UNIVERSES ARE TODAY'S CONSTITUENTS — TREAT AS AN UPPER BOUND. RUN --POINT-IN-TIME WITH A CONSTITUENTS FILE FOR THE CORRECTED CURVE.</span>`;
  } else {
    const ill = _illustrativeBt();
    curves = _btCurvesHTML(ill.strat, ill.bench);
    m = null; bm = null; sleeves = null; sweep = null;
    period = '2012 → 2026 · ILLUSTRATIVE';
    banner = `<span style="font-weight:600">⚠ ILLUSTRATIVE CURVES — NO CACHED BACKTEST</span><span style="color:#8a7433">RUN python -m trading_algo.dashboard.backtest_store (ON A MACHINE WITH MARKET DATA) TO WIRE REAL RESULTS INTO THIS TAB.</span>`;
  }

  const fp = (v, dp = 1) => v == null ? '—' : num(v * 100, dp) + '%';
  const fn = (v, dp = 2) => v == null ? '—' : num(v, dp);
  const kpis = real ? [
    kpiCell('CAGR', fp(m.cagr), G, bm ? `BENCH ${fp(bm.cagr)}` : ''),
    kpiCell('SHARPE', fn(m.sharpe), PALE, bm ? `BENCH ${fn(bm.sharpe)}` : ''),
    kpiCell('SORTINO', fn(m.sortino), PALE, 'DOWNSIDE-ONLY VOL'),
    kpiCell('MAX DRAWDOWN', m.max_drawdown == null ? '—' : sgnPct(m.max_drawdown, 1), R, bm ? `BENCH ${bm.max_drawdown == null ? '—' : sgnPct(bm.max_drawdown, 1)}` : ''),
    kpiCell('CALMAR', fn(m.calmar), PALE, 'CAGR / MAXDD'),
    kpiCell('REALISED VOL', fp(m.ann_vol), PALE, `TARGET ${pct0(S.meta ? S.meta.params.target_vol : 0.12)}`, true),
  ].join('') : [
    kpiCell('CAGR', '—', DIM, 'NO CACHE'),
    kpiCell('SHARPE', '—', DIM, 'NO CACHE'),
    kpiCell('SORTINO', '—', DIM, 'NO CACHE'),
    kpiCell('MAX DRAWDOWN', '—', DIM, 'NO CACHE'),
    kpiCell('CALMAR', '—', DIM, 'NO CACHE'),
    kpiCell('REALISED VOL', '—', DIM, `TARGET ${pct0(S.meta ? S.meta.params.target_vol : 0.12)}`, true),
  ].join('');

  let sleeveRows = '';
  if (sleeves && sleeves.length) {
    sleeveRows = sleeves.map(s => `
      <div style="display:grid;grid-template-columns:1fr .8fr .8fr .8fr .9fr .8fr;padding:8px 18px;font-size:11px;border-bottom:1px solid #121212"><span style="color:#eaffec">${esc(s.key)}</span><span style="color:#7ee787">${fp(s.cagr)}</span><span style="color:#c9e8cc">${fn(s.sharpe)}</span><span style="color:#c9e8cc">${fn(s.sortino)}</span><span style="color:#ff7b72">${s.max_drawdown == null ? '—' : sgnPct(s.max_drawdown, 1)}</span><span style="color:#c9e8cc">${fn(s.calmar)}</span></div>`).join('');
    sleeveRows += `
      <div style="display:grid;grid-template-columns:1fr .8fr .8fr .8fr .9fr .8fr;padding:8px 18px;font-size:11px;border-bottom:1px solid #121212;background:#0d0d0d"><span style="color:#eaffec;font-weight:600">COMBINED · AUD</span><span style="color:#7ee787">${fp(m.cagr)}</span><span style="color:#c9e8cc">${fn(m.sharpe)}</span><span style="color:#c9e8cc">${fn(m.sortino)}</span><span style="color:#ff7b72">${m.max_drawdown == null ? '—' : sgnPct(m.max_drawdown, 1)}</span><span style="color:#c9e8cc">${fn(m.calmar)}</span></div>`;
  } else {
    sleeveRows = `<div style="padding:22px 18px;font-size:10.5px;color:#61805f;line-height:1.8">PER-SLEEVE METRICS APPEAR HERE ONCE THE BACKTEST CACHE EXISTS.<br><span style="color:#3d543f">python -m trading_algo.dashboard.backtest_store</span></div>`;
  }

  let sweepHtml;
  if (sweep && sweep.values) {
    const flat = sweep.values.flat();
    const lo = Math.min(...flat), hi = Math.max(...flat);
    const cells = [];
    sweep.top_ns.forEach((tn, i) => {
      cells.push(`<span style="display:grid;place-items:center;padding:12px 4px;color:${DIM};font-size:9px;letter-spacing:.06em">TOP_N ${tn}</span>`);
      sweep.values[i].forEach(v => {
        const t = (v - lo) / ((hi - lo) || 1);
        cells.push(`<span style="display:grid;place-items:center;padding:12px 4px;background:rgba(126,231,135,${(0.05 + t * 0.28).toFixed(2)});color:${t > 0.8 ? PALE : '#9db5a0'};font-size:11px;letter-spacing:.06em">${num(v, 2)}</span>`);
      });
    });
    /* tone follows sweep.py's three verdicts: ROBUST / MODERATE / PEAKY */
    const v = sweep.verdict || '';
    const vTone = /ROBUST/.test(v) ? G : /PEAKY/.test(v) ? R : AMB;
    const vBorder = /ROBUST/.test(v) ? '#2a4a2c' : /PEAKY/.test(v) ? '#4a2a28' : '#4a3a1a';
    const vShort = v.split(' — ')[0] || '—';
    sweepHtml = `
      <div style="display:flex;justify-content:space-between;align-items:center;padding:10px 18px;border-bottom:1px solid #1a1a1a"><span style="font-size:9px;color:#eaffec;letter-spacing:.14em">■ ROBUSTNESS SWEEP · SHARPE</span><span title="${esc(v)}" style="font-size:9px;color:${vTone};border:1px solid ${vBorder};background:rgba(126,231,135,.04);padding:2px 8px">VERDICT: ${esc(vShort)}</span></div>
      <div style="padding:14px 18px">
        <div style="display:grid;grid-template-columns:70px repeat(${sweep.lookbacks.length},1fr);gap:3px;font-size:10px">
          <span></span>${sweep.lookbacks.map(l => `<span style="color:#61805f;text-align:center;font-size:9px;letter-spacing:.1em">LOOKBACK ${esc(String(l).toUpperCase())}</span>`).join('')}
          ${cells.join('')}
        </div>
        <div style="font-size:10px;color:#61805f;line-height:1.7;margin-top:12px">THE EDGE SHOULD HOLD ACROSS THE TOP_N × LOOKBACK GRID — A BROAD PLATEAU, NOT AN ISOLATED PEAK. AN ISOLATED PEAK WOULD MEAN THE PARAMETERS WERE CURVE-FIT.</div>
      </div>`;
  } else {
    sweepHtml = `
      <div style="display:flex;justify-content:space-between;align-items:center;padding:10px 18px;border-bottom:1px solid #1a1a1a"><span style="font-size:9px;color:#eaffec;letter-spacing:.14em">■ ROBUSTNESS SWEEP · SHARPE</span><span style="font-size:9px;color:#61805f;border:1px solid #262626;padding:2px 8px">NOT CACHED</span></div>
      <div style="padding:14px 18px;font-size:10px;color:#61805f;line-height:1.8">RUN THE SWEEP AND RE-EXPORT TO SEE THE TOP_N × LOOKBACK SHARPE GRID HERE:<br><span style="color:#3d543f">python -m trading_algo.dashboard.backtest_store --sweep</span><br><br>A BROAD PLATEAU MEANS THE PARAMETERS ARE ROBUST; AN ISOLATED PEAK MEANS THEY WERE CURVE-FIT.</div>`;
  }

  return `
  <div data-screen="backtest">
    <div style="display:flex;align-items:center;gap:12px;padding:8px 18px;background:rgba(227,179,65,.06);border-bottom:1px solid #3d3418;font-size:10px;color:#e3b341">
      ${banner}
      <span style="margin-left:auto;color:#8a7433">${esc(period)} · MONTHLY REBALANCE · COSTS ON</span>
    </div>
    <div style="display:grid;grid-template-columns:repeat(6,1fr);border-bottom:1px solid #262626">${kpis}</div>
    <div style="padding:14px 18px;border-bottom:1px solid #262626">
      <div style="display:flex;gap:18px;font-size:9px;letter-spacing:.12em;margin-bottom:10px">
        <span style="color:#eaffec">■ GROWTH OF A$${num((real && bt.initial_capital) || 100000, 0)} · AUD, NET OF COSTS${real ? '' : ' · ILLUSTRATIVE'}</span>
        <span style="color:#7ee787">— MOMENTUM/3R</span>
        <span style="color:#61805f">— EQUAL-WEIGHT INDEX BENCHMARK</span>
      </div>
      <svg viewBox="0 0 1200 240" preserveAspectRatio="none" style="width:100%;height:250px;display:block">
        <line x1="0" y1="60" x2="1200" y2="60" stroke="#1a1a1a" stroke-width="1"></line>
        <line x1="0" y1="120" x2="1200" y2="120" stroke="#1a1a1a" stroke-width="1"></line>
        <line x1="0" y1="180" x2="1200" y2="180" stroke="#1a1a1a" stroke-width="1"></line>
        <polygon points="${curves.btArea}" fill="rgba(126,231,135,0.06)"></polygon>
        <polyline points="${curves.btBench}" fill="none" stroke="#61805f" stroke-width="1.3"></polyline>
        <polyline points="${curves.btPts}" fill="none" stroke="#7ee787" stroke-width="1.7" stroke-linejoin="round"></polyline>
      </svg>
      <div style="display:flex;justify-content:space-between;font-size:9px;color:#3d543f;margin-top:5px">${curves.years.map(y => `<span>${y}</span>`).join('')}</div>
      <div style="font-size:9px;color:#61805f;letter-spacing:.12em;margin:12px 0 6px">DRAWDOWN</div>
      <svg viewBox="0 0 1200 60" preserveAspectRatio="none" style="width:100%;height:60px;display:block">
        <line x1="0" y1="1" x2="1200" y2="1" stroke="#262626" stroke-width="1"></line>
        <polygon points="${curves.btDdArea}" fill="rgba(255,123,114,0.16)"></polygon>
        <polyline points="${curves.btDd}" fill="none" stroke="#ff7b72" stroke-width="1.1"></polyline>
      </svg>
    </div>
    <div style="display:grid;grid-template-columns:1.4fr 1fr">
      <div style="border-right:1px solid #262626">
        <div style="padding:10px 18px;border-bottom:1px solid #1a1a1a;font-size:9px;color:#eaffec;letter-spacing:.14em">■ PER-SLEEVE METRICS · LOCAL CURRENCY, NET OF COSTS</div>
        <div style="display:grid;grid-template-columns:1fr .8fr .8fr .8fr .9fr .8fr;padding:7px 18px;font-size:9px;color:#61805f;letter-spacing:.12em;border-bottom:1px solid #1a1a1a"><span>SLEEVE</span><span>CAGR</span><span>SHARPE</span><span>SORTINO</span><span>MAX DD</span><span>CALMAR</span></div>
        ${sleeveRows}
        <div style="padding:12px 18px;font-size:10px;color:#61805f;line-height:1.7">COMBINED VOL SITS BELOW EVERY INDIVIDUAL SLEEVE — THE DIVERSIFICATION BENEFIT OF MULTIPLE REGIONAL BOOKS. ${esc(costFootnote())}</div>
      </div>
      <div>${sweepHtml}</div>
    </div>
  </div>`;
}

/* ============================ METHOD (equity) ========================== */
function methodHTML() {
  const p = (S.meta && S.meta.params) || {};
  const risk = (S.meta && S.meta.risk) || {};
  const regions = (S.meta && S.meta.regions) || [];
  const tests = S.meta && S.meta.tests_total;

  const pipeline = [
    { n: '01', mod: 'SIGNALS.PY', title: 'MOMENTUM SCORE', formula: `P(t−${p.skip_days ?? 21}) / P(t−${p.lookback_days ?? 252}) − 1`, desc: '12-MONTH RETURN, SKIPPING THE LAST MONTH TO AVOID SHORT-TERM REVERSAL.' },
    { n: '02', mod: 'SIGNALS.PY', title: 'TREND FILTER', formula: `PRICE > ${p.stock_trend_ma ?? 200}-DAY MA`, desc: 'ONLY NAMES ACTUALLY RISING — NOT JUST FALLING SLOWER THAN OTHERS.' },
    { n: '03', mod: 'SIGNALS.PY', title: 'REGIME FILTER', formula: `INDEX > ${p.index_trend_ma ?? 200}-DAY MA`, desc: 'INDEX BELOW ITS MA → SLEEVE GOES 100% CASH. THE CRASH PROTECTION.' },
    { n: '04', mod: 'SIGNALS.PY', title: 'SELECT TOP N', formula: `TOP ${p.top_n ?? 10} BY MOMENTUM`, desc: 'AMONG ELIGIBLE NAMES (MOMENTUM > 0, TREND OK, REGIME RISK-ON).' },
    { n: '05', mod: 'STRATEGY.PY', title: 'INVERSE-VOL WEIGHTS', formula: `wᵢ ∝ 1/VOLᵢ · CAP ${pct0(p.max_weight ?? 0.15)}`, desc: 'CALM NAMES GET MORE CAPITAL; NO SINGLE NAME DOMINATES.' },
    { n: '06', mod: 'STRATEGY.PY', title: 'VOL TARGETING', formula: `SCALE → ${pct0(p.target_vol ?? 0.12)} VOL · GROSS ≤ ${pct0(p.max_gross ?? 1)}`, desc: 'STEADY RISK, NOT STEADY CAPITAL. TURBULENT MARKETS → MORE CASH.' },
  ].map(st => `
    <div style="border:1px solid #262626;background:#0d0d0d;padding:12px 14px;display:flex;flex-direction:column;gap:8px">
      <div style="display:flex;justify-content:space-between;align-items:baseline"><span style="font-size:15px;color:#3d543f;font-weight:600">${st.n}</span><span style="font-size:8px;color:#61805f;letter-spacing:.1em">${st.mod}</span></div>
      <div style="font-size:11px;color:#eaffec;font-weight:600;letter-spacing:.04em">${st.title}</div>
      <div style="font-size:10px;color:#7ee787;background:#121212;border:1px solid #1a1a1a;padding:6px 8px;line-height:1.5">${st.formula}</div>
      <div style="font-size:9.5px;color:#61805f;line-height:1.6">${st.desc}</div>
    </div>`).join('');

  const costRows = regions.map(r => {
    const sym = SYM[r.currency] || r.currency;
    return `
    <div style="display:grid;grid-template-columns:.8fr 1fr .6fr .8fr .9fr;padding:7px 18px;font-size:11px;border-bottom:1px solid #121212"><span style="color:#eaffec">${esc(r.key)}</span><span style="color:#9db5a0">${num(r.commission_bps, 0)} BPS</span><span style="color:#9db5a0">${sym}${num(r.min_commission, 0)}</span><span style="color:#9db5a0">${num(r.slippage_bps, 0)} BPS</span><span style="color:${r.stamp_duty_bps ? AMB : FAINT}">${r.stamp_duty_bps ? num(r.stamp_duty_bps, 0) + ' BPS ON BUYS' : '—'}</span></div>`;
  }).join('');

  const invariants = [
    { text: 'NO LOOKAHEAD — SIGNALS AT T USE DATA ≤ T; TRADES EXECUTE T+1.', test: 'TEST_SIGNALS · TEST_BACKTEST' },
    { text: 'COSTS ALWAYS ON — COMMISSION + SLIPPAGE EVERY REBALANCE, UK STAMP DUTY ON BUYS.', test: 'TEST_FEES' },
    { text: 'ONE WEIGHT FUNCTION — BACKTEST AND PAPER BOTH CALL COMPUTE_TARGETS().', test: 'TEST_CONSISTENCY' },
    { text: 'WHOLE SHARES ONLY IN PAPER TRADING; COMMISSION FLOORS RESPECTED.', test: 'TEST_PAPER_TRADE' },
    { text: 'SYNTHETIC RESULTS ARE PLUMBING TESTS — NEVER PRESENTED AS PERFORMANCE.', test: 'TEST_DATA_SOURCES' },
  ].map(iv => `
    <div style="display:flex;gap:10px;padding:9px 18px;border-bottom:1px solid #121212;font-size:10.5px;line-height:1.6"><span style="color:#7ee787">✓</span><span style="color:#9db5a0">${iv.text} <span style="color:#3d543f">${iv.test}</span></span></div>`).join('');

  return `
  <div data-screen="method">
    <div style="display:grid;grid-template-columns:1fr 1fr;border-bottom:1px solid #262626">
      <div style="padding:18px;border-right:1px solid #262626">
        <div style="font-size:9px;color:#eaffec;letter-spacing:.14em;margin-bottom:6px">■ THE IDEA IN ONE PARAGRAPH</div>
        <p style="font-size:12px;line-height:1.9;color:#9db5a0;margin:0">EACH MONTH, IN EACH REGION (FTSE / US / ASX), RANK EVERY STOCK BY ITS <span style="color:#7ee787">12-MONTH-MINUS-1 MOMENTUM</span>. BUY THE STRONGEST NAMES — BUT ONLY THOSE IN AN UPTREND, AND ONLY WHILE THE REGIONAL INDEX ITSELF IS IN AN UPTREND; OTHERWISE HOLD CASH. SIZE BY <span style="color:#7ee787">INVERSE VOLATILITY</span>, SCALE TO A <span style="color:#7ee787">${pct0(p.target_vol ?? 0.12)} VOL TARGET</span>, REBALANCE MONTHLY. THREE BOOKS IN PARALLEL, EACH IN ITS OWN CURRENCY, REPORTED IN AUD.</p>
      </div>
      <div style="padding:18px">
        <div style="font-size:9px;color:#eaffec;letter-spacing:.14em;margin-bottom:6px">■ EXECUTION CYCLE</div>
        <div style="display:flex;align-items:stretch;gap:8px;margin-top:10px">
          <div style="flex:1;border:1px solid #2a4a2c;padding:10px 12px"><div style="font-size:10px;color:#7ee787;font-weight:600">MONTH-END T</div><div style="font-size:10px;color:#61805f;margin-top:4px;line-height:1.6">DECIDE WEIGHTS FROM DATA ≤ T</div></div>
          <div style="display:grid;place-items:center;color:#3d543f">→</div>
          <div style="flex:1;border:1px solid #2a4a2c;padding:10px 12px"><div style="font-size:10px;color:#7ee787;font-weight:600">EXECUTE T+1</div><div style="font-size:10px;color:#61805f;margin-top:4px;line-height:1.6">TURNOVER COSTS + UK STAMP DUTY ON BUYS</div></div>
          <div style="display:grid;place-items:center;color:#3d543f">→</div>
          <div style="flex:1;border:1px solid #2a4a2c;padding:10px 12px"><div style="font-size:10px;color:#7ee787;font-weight:600">HOLD &amp; DRIFT</div><div style="font-size:10px;color:#61805f;margin-top:4px;line-height:1.6">WITH THE MARKET UNTIL NEXT MONTH-END</div></div>
        </div>
      </div>
    </div>
    <div style="padding:14px 18px;border-bottom:1px solid #262626">
      <div style="font-size:9px;color:#eaffec;letter-spacing:.14em;margin-bottom:12px">■ THE PER-SLEEVE PIPELINE · SIGNALS.PY → STRATEGY.COMPUTE_TARGETS()</div>
      <div style="display:grid;grid-template-columns:repeat(6,1fr);gap:10px">${pipeline}</div>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr">
      <div style="border-right:1px solid #262626">
        <div style="padding:10px 18px;border-bottom:1px solid #1a1a1a;font-size:9px;color:#eaffec;letter-spacing:.14em">■ COST MODEL · REGIONS.PY</div>
        <div style="display:grid;grid-template-columns:.8fr 1fr .6fr .8fr .9fr;padding:7px 18px;font-size:9px;color:#61805f;letter-spacing:.1em;border-bottom:1px solid #1a1a1a"><span>REGION</span><span>COMMISSION</span><span>MIN</span><span>SLIPPAGE</span><span>STAMP DUTY</span></div>
        ${costRows}
        <div style="padding:12px 18px;font-size:10px;color:#61805f;line-height:1.7">MONTHLY TURNOVER RUNS ~25–35%, SO THE EDGE SURVIVES COMMISSIONS, SLIPPAGE AND UK STAMP DUTY. LSE PENCE ARE SCALED TO POUNDS (PRICE_SCALE = 0.01).</div>
      </div>
      <div style="border-right:1px solid #262626">
        <div style="padding:10px 18px;border-bottom:1px solid #1a1a1a;font-size:9px;color:#eaffec;letter-spacing:.14em">■ RISK CONTROLS · CONFIG.PY</div>
        <div style="padding:12px 18px;border-bottom:1px solid #121212"><div style="display:flex;justify-content:space-between;font-size:11px"><span style="color:#eaffec">DRAWDOWN CIRCUIT BREAKER</span><span style="color:#ff7b72">−${num((risk.max_drawdown_stop ?? 0.25) * 100, 0)}%</span></div><div style="font-size:10px;color:#61805f;line-height:1.7;margin-top:4px">FALL &gt;${num((risk.max_drawdown_stop ?? 0.25) * 100, 0)}% FROM PEAK → LIQUIDATE TO CASH, SIT OUT ~${risk.drawdown_cooldown_days ?? 21} TRADING DAYS. A CATASTROPHE BACKSTOP ON TOP OF THE ${p.index_trend_ma ?? 200}-DAY REGIME FILTER.</div></div>
        <div style="padding:12px 18px;border-bottom:1px solid #121212"><div style="display:flex;justify-content:space-between;font-size:11px"><span style="color:#eaffec">MIN-VIABLE-SIZE GATE</span><span style="color:#e3b341">A$${num(risk.min_viable_equity_base ?? 500, 0)}</span></div><div style="font-size:10px;color:#61805f;line-height:1.7;margin-top:4px">A SLEEVE BELOW THIS HOLDS CASH INSTEAD OF BLEEDING COMMISSION FLOORS — THE LESSON THE $1K ACCOUNT TAUGHT.</div></div>
        <div style="padding:12px 18px"><div style="display:flex;justify-content:space-between;font-size:11px"><span style="color:#eaffec">POSITION CAPS</span><span style="color:#7ee787">${pct0(p.max_weight ?? 0.15)} / ${pct0(p.max_gross ?? 1)}</span></div><div style="font-size:10px;color:#61805f;line-height:1.7;margin-top:4px">SINGLE-NAME CAP ${pct0(p.max_weight ?? 0.15)}; GROSS EXPOSURE ≤ ${pct0(p.max_gross ?? 1)} — NEVER LEVERED. ENFORCED INSIDE COMPUTE_TARGETS().</div></div>
      </div>
      <div>
        <div style="padding:10px 18px;border-bottom:1px solid #1a1a1a;font-size:9px;color:#eaffec;letter-spacing:.14em">■ INVARIANTS${tests ? ` · ENFORCED BY ${tests} TESTS` : ''}</div>
        ${invariants}
      </div>
    </div>
  </div>`;
}

/* ========================= ALL ACCOUNTS =============================== */
function allAccountsHTML() {
  const ov = S.overview;
  if (!ov) return '<div class="boot">LOADING OVERVIEW…</div>';
  const T = ov.totals;
  const [aumInt, aumDec] = moneySplit(T.aum);

  /* design order: big equity books, then FX books, micro books last */
  const cards = ov.accounts.slice().sort((a, b) => {
    const rank = c => (c.kind === 'equity' && c.equity < 5000) ? 2 : (c.kind === 'fx' ? 1 : 0);
    return rank(a) - rank(b);
  });
  const colorOf = (key, i) => ALLOC_COLORS[key] || ALLOC_FALLBACK[i % ALLOC_FALLBACK.length];

  const segs = cards.map((c, i) => `<div style="width:${(c.share * 100).toFixed(1)}%;background:${colorOf(c.key, i)}" title="${esc(c.label)} ${num(c.share * 100, 1)}%"></div>`).join('');
  const legend = cards.map((c, i) => `<span style="display:flex;align-items:center;gap:5px"><span style="width:8px;height:8px;background:${colorOf(c.key, i)};display:inline-block"></span>${esc(c.label)} ${num(c.share * 100, 1)}%</span>`).join('');

  const toneColor = t => t === 'bad' ? R : t === 'warn' ? AMB : G;
  const cardHtml = cards.map(c => {
    const spark = toPts(c.spark, 120, 30, 3).join(' ');
    const up = c.spark.length > 1 ? c.spark[c.spark.length - 1] >= c.spark[0] : true;
    return `
    <div class="hv-acct" data-act="acct" data-arg="${esc(c.key)}" style="padding:16px 18px;border-right:1px solid #262626;display:flex;flex-direction:column;gap:10px;cursor:pointer">
      <div><div style="display:flex;justify-content:space-between;align-items:baseline"><span style="font-size:13px;font-weight:600;color:#eaffec;letter-spacing:.06em">${esc(c.label)}</span><span style="font-size:9px;color:${toneColor(c.status_tone)}">${esc(c.status)}</span></div><div style="font-size:9px;color:#61805f;margin-top:3px;letter-spacing:.08em">${esc(c.sub)}</div></div>
      <div style="font-size:21px;font-weight:600;color:#eaffec;letter-spacing:-.01em">A$${num(c.equity, 2)}</div>
      <div style="display:flex;gap:14px;font-size:11px"><span style="color:${cSign(c.ret)}">${sgnPct(c.ret, 2)}</span><span style="color:${cSign(c.day)}">${sgnPct(c.day, 2)} DAY</span></div>
      <svg viewBox="0 0 120 30" preserveAspectRatio="none" style="width:100%;height:30px;display:block"><polyline points="${spark}" fill="none" stroke="${up ? G : R}" stroke-width="1.3"></polyline></svg>
      <div style="display:flex;justify-content:space-between;font-size:9px;color:#3d543f"><span>${esc(c.n_line)}</span><span>${num(c.share * 100, 1)}% OF AUM</span></div>
      <div class="hv-open" style="margin-top:auto;font-size:9px;letter-spacing:.12em;color:#7ee787;border:1px solid #2a4a2c;padding:5px 0;text-align:center">OPEN BOOK →</div>
    </div>`;
  }).join('');

  const best = T.best, worst = T.worst;
  return `
  <div data-screen="all">
    <div style="display:grid;grid-template-columns:1.5fr 1fr 1fr 1fr 1fr 1fr;border-bottom:1px solid #262626">
      <div style="padding:14px 18px;border-right:1px solid #262626;background:#0d0d0d"><div style="font-size:9px;color:#61805f;letter-spacing:.14em">TOTAL AUM · AUD</div><div style="font-size:26px;font-weight:600;color:#eaffec;margin-top:5px;letter-spacing:-.01em">${aumInt}<span style="font-size:15px;color:#61805f">${aumDec}</span></div><div style="font-size:9px;color:#3d543f;margin-top:4px">ACROSS ${T.books} PAPER BOOKS</div></div>
      <div style="padding:14px 16px;border-right:1px solid #262626"><div style="font-size:9px;color:#61805f;letter-spacing:.14em">NET P&amp;L</div><div style="font-size:20px;font-weight:600;color:${cSign(T.net_pnl)};margin-top:8px">${sgnNum(T.net_pnl, 2)}</div><div style="font-size:9px;color:#3d543f;margin-top:4px">${sgnPct(T.net_pnl_pct, 2)} ON ${num(T.initial, 0)}</div></div>
      <div style="padding:14px 16px;border-right:1px solid #262626"><div style="font-size:9px;color:#61805f;letter-spacing:.14em">DAY CHANGE</div><div style="font-size:20px;font-weight:600;color:${cSign(T.day_aud)};margin-top:8px">${sgnNum(T.day_aud, 2)}</div><div style="font-size:9px;color:#3d543f;margin-top:4px">${sgnPct(T.day_pct, 2)} · ${T.books_red} OF ${T.books} BOOKS RED</div></div>
      <div style="padding:14px 16px;border-right:1px solid #262626"><div style="font-size:9px;color:#61805f;letter-spacing:.14em">BEST BOOK</div><div style="font-size:20px;font-weight:600;color:#7ee787;margin-top:8px">${esc(best ? best.name : '—')}</div><div style="font-size:9px;color:#3d543f;margin-top:4px">${best ? `${sgnPct(best.ret, 2)} SINCE ${mmdd(best.since)}` : ''}</div></div>
      <div style="padding:14px 16px;border-right:1px solid #262626"><div style="font-size:9px;color:#61805f;letter-spacing:.14em">WORST BOOK</div><div style="font-size:20px;font-weight:600;color:#ff7b72;margin-top:8px">${esc(worst ? worst.name : '—')}</div><div style="font-size:9px;color:#3d543f;margin-top:4px">${worst ? `${sgnPct(worst.ret, 2)} SINCE ${mmdd(worst.since)}` : ''}</div></div>
      <div style="padding:14px 16px"><div style="font-size:9px;color:#61805f;letter-spacing:.14em">RISK HALTS</div><div style="font-size:20px;font-weight:600;color:${T.halts ? R : G};margin-top:8px">${T.halts} / ${T.books}</div><div style="font-size:9px;color:#3d543f;margin-top:4px">${T.halts ? 'BREAKER(S) TRIPPED' : 'ALL BREAKERS ARMED'}</div></div>
    </div>
    <div style="padding:12px 18px;border-bottom:1px solid #262626">
      <div style="font-size:9px;color:#61805f;letter-spacing:.14em;margin-bottom:8px">CAPITAL ALLOCATION</div>
      <div style="display:flex;height:14px;border:1px solid #262626">${segs}</div>
      <div style="display:flex;gap:18px;margin-top:7px;font-size:9px;color:#61805f;flex-wrap:wrap">${legend}</div>
    </div>
    <div style="display:grid;grid-template-columns:repeat(${cards.length || 1},1fr)">${cardHtml}</div>
  </div>`;
}

/* ========================= AGENT BOOKS (FX) ============================ */
const AGENT_NAMES = ['TREND', 'BREAKOUT', 'MOMENTUM', 'MEANREV', 'CARRY', 'NEURAL'];

/* Plain-English explainers for the TA overlays & indicator panes — what the
   tool is, what to look for on the chart, and how to actually trade it. Shown
   on hover over each TA / pane button. */
const TA_HELP = {
  ema: {
    title: 'EMA — trend direction',
    what: 'Two smoothed average-price lines. The FAST line (amber) reacts quickly to recent price; the SLOW line (pale) is the bigger, slower trend.',
    look: 'Fast ABOVE slow → uptrend. Fast BELOW slow → downtrend. The moment they cross is the signal: fast crossing up through slow is bullish (a "golden cross"), crossing down is bearish (a "death cross"). The wider the gap between the lines, the stronger the trend.',
    apply: 'Trade WITH the cross. In an uptrend, price dipping back to the fast line is often a lower-risk spot to join rather than chasing. This is the Trend agent’s core input — it votes with the EMA direction, scaled by how strong the trend is.',
  },
  boll: {
    title: 'Bollinger Bands — stretch & squeeze',
    what: 'A middle average with an upper and lower band set 2 standard deviations away. The bands WIDEN when the market is volatile and PINCH tight when it’s quiet.',
    look: 'Price riding the upper band = strong (it can stay there in a real trend). Price poking OUTSIDE a band then snapping back = a stretched move likely to revert. Bands squeezing very tight = a "squeeze" that often precedes a big breakout.',
    apply: 'In a calm, sideways market, fade the extremes — buy near the lower band, sell near the upper (the Mean-reversion agent’s logic). In a strong trend, don’t fight a band-ride. A squeeze is your cue to get ready for a breakout — direction unknown until price picks one.',
  },
  don: {
    title: 'Donchian Channel — breakout levels',
    what: 'The highest high and lowest low over the last N bars — literally the recent range’s ceiling and floor.',
    look: 'Price breaking ABOVE the upper channel = breakout to new highs, buyers in control. Breaking BELOW the lower channel = breakdown to new lows. Sitting inside the channel = rangebound, no breakout yet.',
    apply: 'Classic breakout trading: go long when price closes above the channel top, short below the bottom, and use the opposite band as a natural stop/exit. This is the Breakout agent’s trigger — it fires a full ±1 vote right at a fresh break.',
  },
  RSI: {
    title: 'RSI — overbought / oversold',
    what: 'A 0–100 gauge of how strong recent up-moves are versus down-moves. The dashed lines mark 70 and 30.',
    look: 'Above 70 = "overbought" (buying may be overdone). Below 30 = "oversold" (selling overdone). Around 50 = neutral. Watch for divergence: price makes a new high but RSI doesn’t — a warning the move is tiring.',
    apply: 'In a range, 70/30 are fade signals — sell overbought, buy oversold. In a STRONG trend RSI can sit overbought for a long time, so use it to time entries, not to call the exact top or bottom. A cross back through 50 is a simple momentum-shift cue.',
  },
  MOMENTUM: {
    title: 'Momentum — speed of the move',
    what: 'How far price has moved over a lookback window, as a % — the SPEED of the move, not just its direction. The centre line is zero.',
    look: 'Above zero = price higher than N bars ago (up-momentum); below zero = down-momentum. The line RISING = accelerating; flattening = losing steam. The zero-line cross is the momentum flip.',
    apply: 'Favour longs while momentum is positive and rising, shorts while negative and falling — winners tend to keep winning (the Momentum agent’s edge). Momentum fading near an extreme, with RSI or Bollinger agreeing, hints at a pause or reversal.',
  },
  ADX: {
    title: 'ADX — trend strength (the regime gate)',
    what: 'A 0–60+ measure of TREND STRENGTH only. It says nothing about direction — just whether a real trend exists or the market is chopping sideways.',
    look: 'Above 25 = a genuine trend is in force. Below 20 = weak / ranging (chop, where trend signals fail). Rising ADX = trend strengthening; falling = trend fading.',
    apply: 'This is the switch that picks which agents to trust. ADX high → follow Trend, Breakout and Momentum. ADX low → switch to Mean-reversion and fade the extremes instead. Don’t chase breakouts when ADX is under 20 — they tend to fail in chop.',
  },
};

/* news events whose currency is one of a pair's two legs (crypto pairs keep
   their USD leg), most-recent first */
function newsForPair(page, pair) {
  const news = (page && page.news) || [];
  if (!news.length || !pair || pair.length !== 6) return [];
  const legs = new Set([pair.slice(0, 3).toUpperCase(), pair.slice(3).toUpperCase()]);
  return news.filter(e => legs.has(String(e.currency).toUpperCase()));
}

/* ISO dates for `n` daily bars ending at `anchor` (index n-1 = anchor),
   stepping back over weekdays only — the chart's synthetic bars are one
   trading day apart, anchored to the book's last mark. */
function tradingDaysBack(anchor, n) {
  const out = new Array(n);
  let d = new Date((String(anchor).slice(0, 10) || '2026-01-01') + 'T00:00:00Z');
  if (isNaN(d)) d = new Date();
  for (let i = n - 1; i >= 0; i--) {
    out[i] = d.toISOString().slice(0, 10);
    do { d = new Date(d.getTime() - 86400e3); } while (d.getUTCDay() === 0 || d.getUTCDay() === 6);
  }
  return out;
}

function taTipHTML(key) {
  const h = TA_HELP[key];
  if (!h) return '';
  return `
  <div class="tip" style="position:absolute;top:calc(100% + 7px);left:0;width:360px;background:#0a110b;border:1px solid #2a4a2c;border-radius:3px;box-shadow:0 12px 36px rgba(0,0,0,.8),0 0 22px rgba(126,231,135,.05);z-index:80;padding:12px 14px;pointer-events:none;white-space:normal;text-transform:none;letter-spacing:normal">
    <div style="font-size:12px;font-weight:600;color:#eaffec;letter-spacing:.02em;margin-bottom:8px">${esc(h.title)}</div>
    <div style="font-size:10.5px;line-height:1.7;color:#9db5a0;margin-bottom:8px">${esc(h.what)}</div>
    <div style="font-size:8px;color:#61805f;letter-spacing:.12em;margin-bottom:3px">WHAT TO LOOK FOR</div>
    <div style="font-size:10.5px;line-height:1.7;color:#c9e8cc;margin-bottom:8px">${esc(h.look)}</div>
    <div style="font-size:8px;color:#61805f;letter-spacing:.12em;margin-bottom:3px">HOW TO TRADE IT</div>
    <div style="font-size:10.5px;line-height:1.7;color:#c9e8cc">${esc(h.apply)}</div>
  </div>`;
}

/* indicative macro tables for the FUNDAMENTALS panel (ported from the design;
   the panel is labelled INDICATIVE in the UI) */
const CBD = {
  USD: { cb: 'FEDERAL RESERVE', rate: 4.50, gdp: '+2.1%', cpi: '2.8%', jobs: '4.1% U-3' },
  EUR: { cb: 'ECB', rate: 2.15, gdp: '+0.9%', cpi: '2.1%', jobs: '6.3% UNEMP' },
  GBP: { cb: 'BANK OF ENGLAND', rate: 4.25, gdp: '+1.1%', cpi: '3.0%', jobs: '4.4% UNEMP' },
  JPY: { cb: 'BANK OF JAPAN', rate: 0.50, gdp: '+0.6%', cpi: '2.5%', jobs: '2.5% UNEMP' },
  AUD: { cb: 'RBA', rate: 3.85, gdp: '+1.8%', cpi: '2.9%', jobs: '4.0% UNEMP' },
  CAD: { cb: 'BANK OF CANADA', rate: 2.75, gdp: '+1.4%', cpi: '2.2%', jobs: '6.6% UNEMP' },
  CHF: { cb: 'SNB', rate: 0.00, gdp: '+1.2%', cpi: '0.6%', jobs: '2.4% UNEMP' },
  NZD: { cb: 'RBNZ', rate: 3.25, gdp: '+1.3%', cpi: '2.7%', jobs: '5.1% UNEMP' },
};
const EV = {
  USD: ['JUL 15 · US CPI', 'JUL 29 · FOMC DECISION'], EUR: ['JUL 24 · ECB MEETING', 'AUG 01 · EZ FLASH CPI'],
  GBP: ['JUL 16 · UK CPI', 'AUG 07 · BOE DECISION'], JPY: ['JUL 31 · BOJ MEETING'],
  AUD: ['JUL 28 · AU Q2 CPI', 'AUG 11 · RBA DECISION'], CAD: ['JUL 30 · BOC DECISION'],
  CHF: ['SEP 25 · SNB QUARTERLY'], NZD: ['AUG 20 · RBNZ DECISION'],
};
const CRY = {
  BTCUSD: 'BITCOIN HAS NO CENTRAL BANK — IT TRADES ON ETF FLOWS, THE HALVING CYCLE AND GLOBAL LIQUIDITY. WHEN REAL YIELDS FALL, COINS RALLY.',
  ETHUSD: 'ETHEREUM TRADES ON ETF FLOWS, ITS ~3% STAKING YIELD AND NETWORK ACTIVITY — WITH USD LIQUIDITY AS THE MACRO LEVER.',
  SOLUSD: 'SOLANA IS HIGH-BETA CRYPTO — NETWORK ACTIVITY AND RISK APPETITE DRIVE IT HARDER THAN BTC IN BOTH DIRECTIONS.',
};
const EQF = {
  AAPL: 'EARNINGS · IPHONE CYCLE · BUYBACKS', MSFT: 'EARNINGS · AI CAPEX · CLOUD GROWTH', NVDA: 'AI CHIP DEMAND · EXPORT RULES',
  SPY: 'S&P 500 EARNINGS + FED POLICY', QQQ: 'MEGACAP TECH EARNINGS + RATES',
  TLT: '20Y+ TREASURIES — RATE CUTS HELP, INFLATION HURTS', IEF: '7–10Y TREASURIES — TRACKS THE FED PATH',
  AGG: 'BROAD BONDS — RATES + CREDIT SPREADS', SHY: '1–3Y TREASURIES — NEAR-CASH, TRACKS FED FUNDS',
};

const TF_MAP = {
  'LIVE': { n: 60, s: 0.05, label: '60 × 1-MINUTE BARS · STREAMING', desc: '1-MIN' },
  '1D':   { n: 24, s: 0.18, label: '24 × HOURLY BARS', desc: '60-MIN' },
  '1W':   { n: 42, s: 0.4, label: '42 × 4-HOUR BARS', desc: '4-HR' },
  '1M':   { n: 22, s: 1, label: '22 × DAILY BARS', desc: 'DAILY' },
  '6M':   { n: 126, s: 1, label: '126 × DAILY BARS', desc: 'DAILY' },
  '1Y':   { n: 52, s: 2.2, label: '52 × WEEKLY BARS', desc: 'WEEKLY' },
  '5Y':   { n: 60, s: 4.5, label: '60 × MONTHLY BARS', desc: 'MONTHLY' },
};

function agentSelPair(page) {
  const pairs = page.rows.map(r => r.pair);
  let sel = S.selPair[page.key];
  if (!pairs.includes(sel)) sel = pairs[0];
  return sel;
}
function agentTf(page) {
  let tf = S.tf[page.key];
  if (!TF_MAP[tf]) tf = page.bar === '60m' ? '1D' : '1M';
  return tf;
}

function agentKpisHTML(page) {
  const isIntraday = page.bar === '60m';
  const unit = page.rows.some(r => !isFxPair(r.pair) && !isCrypto(r.pair)) ? 'SYMBOLS' : 'PAIRS';
  const topLong = page.rows.find(r => r.weight > 0);
  const topShort = page.rows.find(r => r.weight < 0);
  let grossSub;
  if (page.profile === 'conservative') grossSub = 'CONSERVATIVE SIZING';
  else if (topLong && topShort) grossSub = `LONG ${topLong.pair}, SHORT ${topShort.pair}`;
  else if (topLong) grossSub = `NET LONG · TOP ${topLong.pair}`;
  else if (topShort) grossSub = `NET SHORT · TOP ${topShort.pair}`;
  else grossSub = 'FLAT';
  const dayLabel = isIntraday ? 'LAST BAR' : 'DAY CHANGE';
  const costPct = page.daily && page.daily.cost_pct;
  const daySub = (page.day_aud == null ? '—'
    : sgn(page.day_aud, 'A$' + num(Math.abs(page.day_aud), 2)))
    + (costPct && Math.abs(costPct) >= 0.00005 ? ` · COSTS ${sgnPct(costPct, 2)}` : '');
  const kpis = [
    { label: 'TOTAL EQUITY · AUD', val: num(page.equity, 2), color: PALE, sub: `INITIAL ${num(page.initial, 2)}` },
    { label: 'TOTAL RETURN', val: sgnPct(page.total_return, 2), color: cSign(page.total_return), sub: `SINCE ${esc(page.since)}` },
    { label: dayLabel, val: page.day_pct == null ? '—' : sgnPct(page.day_pct, 2), color: cSign(page.day_pct || 0), sub: daySub },
    { label: 'OFF PEAK', val: sgnPct(page.off_peak, 2), color: page.off_peak < -0.02 ? AMB : PALE, sub: `PEAK A$${num(page.peak, 2)}` },
    { label: 'GROSS / NET', val: `${pct0(page.gross)} / ${sgn(page.net, num(Math.abs(page.net) * 100, 0) + '%')}`, color: page.gross > 1 ? AMB : PALE, sub: grossSub },
    { label: `ACTIVE ${unit}`, val: String(page.n_long + page.n_short), color: PALE, sub: `${page.n_long} LONG · ${page.n_short} SHORT` },
    page.risk_halted
      ? { label: 'RISK HALT', val: 'HALTED', color: R, sub: `COOLDOWN ${page.halt_cooldown} BARS` }
      : { label: 'RISK HALT', val: 'CLEAR', color: G, sub: `BREAKER ARMED @ −${num(page.breaker * 100, 0)}%` },
  ];
  return `<div style="display:grid;grid-template-columns:1.5fr 1fr 1fr 1fr 1fr 1fr 1fr;border-bottom:1px solid #262626">` +
    kpis.map((k, i) => `<div style="padding:14px 16px;${i < kpis.length - 1 ? 'border-right:1px solid #262626' : ''}"><div style="font-size:9px;color:#61805f;letter-spacing:.14em">${esc(k.label)}</div><div style="font-size:20px;font-weight:600;color:${k.color};margin-top:8px">${esc(k.val)}</div><div style="font-size:9px;color:#3d543f;margin-top:4px">${esc(k.sub)}</div></div>`).join('') + '</div>';
}

function agentTapeHTML(page) {
  const items = [];
  for (const t of (page.tape || []).slice(0, 7)) {
    const arrow = t.move == null || Math.abs(t.move) < 1e-6 ? ' —'
      : (t.move > 0 ? ' ▴' : ' ▾');
    const c = t.move == null || Math.abs(t.move) < 1e-6 ? TXT : (t.move > 0 ? G : R);
    items.push({ k: t.k, v: pairPrice(t.k, t.price) + arrow, c });
  }
  const rc = page.regime_counts || { trending: 0, ranging: 0 };
  const total = rc.trending + rc.ranging;
  if (total) {
    items.push({ k: 'REGIME', v: rc.ranging === 0 ? `${rc.trending}/${total} TRENDING` : `${rc.trending} TRENDING · ${rc.ranging} RANGING`, c: TXT });
  }
  if (page.profile === 'conservative') items.push({ k: 'PROFILE', v: 'CONSERVATIVE — HALF-RISK SIZING', c: TXT });
  if (page.bar === '60m') {
    const fp = S.meta && S.meta.fx_profiles && S.meta.fx_profiles[page.account];
    items.push({ k: 'BAR', v: fp ? `60M · SIGNALS EMA${fp.ema_fast}/${fp.ema_slow} · ${fp.roc_window}-BAR MOMENTUM` : '60M BARS', c: TXT });
  }
  const d = page.daily || {};
  if (d.net_pct != null && d.pnl_pct != null && d.carry_pct != null && d.cost_pct != null && !isFxPair(page.rows.length ? page.rows[0].pair : '')) {
    const fxPart = d.net_pct - d.pnl_pct - d.carry_pct - d.cost_pct;
    if (Math.abs(fxPart) > 0.0002) {
      items.push({ k: 'DAY DECOMP', v: `PRICE ${num(d.pnl_pct * 100, 2)} · CARRY ${num(d.carry_pct * 100, 2)} · COST ${num(d.cost_pct * 100, 2)} · FX ${num(fxPart * 100, 2)}`, c: TXT });
    }
  }
  const nextMark = page.bar === '60m' ? 'NEXT 60M BAR' : 'DAILY BAR CLOSE';
  const right = `PEAK <span style="color:#c9e8cc">A$${num(page.peak, 2)}</span> · BREAKER <span style="color:${page.risk_halted ? R : G}">${page.risk_halted ? 'TRIPPED' : 'ARMED'}</span> @ −${num(page.breaker * 100, 0)}% · NEXT MARK <span style="color:#c9e8cc">${nextMark}</span>`;
  return acctTapeHTML(items, right);
}

function agentCurveAttrHTML(page) {
  const vals = (page.equity_history || []).map(h => h[1]);
  const upCurve = vals.length > 1 ? vals[vals.length - 1] >= vals[0] : true;
  const stroke = upCurve ? G : R;
  const p = toPts(vals, 600, 140, 10);
  const eqStart = page.equity_history.length ? page.equity_history[0][0] : '';
  const eqEnd = page.equity_history.length ? page.equity_history[page.equity_history.length - 1][0] : '';
  const rangeTxt = vals.length
    ? `MIN ${num(Math.min(...vals), 2)} · MAX ${num(Math.max(...vals), 2)}${page.bar === '60m' ? ' · HOURLY MARKS' : ''}` : '';

  /* attribution vs ensemble-tilt panel (the design shows tilt for matt) */
  let bars, title;
  const unit = page.rows.some(r => !isFxPair(r.pair) && !isCrypto(r.pair)) ? 'SYMBOL' : 'PAIR';
  const attr = (page.attribution || []).filter(a => Math.abs(a.contrib || 0) > 1e-9);
  if (attr.length) {
    const maxC = Math.max(...attr.map(a => Math.abs(a.contrib || 0)), 1e-9);
    bars = attr.map(a => ({
      label: a.pair,
      v: Math.max(-1, Math.min(1, (a.contrib || 0) / maxC * 0.85)),
      val: sgn(a.contrib || 0, num(Math.abs(a.contrib || 0) * 100, 3) + '%'),
    }));
    title = `${page.bar === '60m' ? 'BAR' : 'DAY'} ATTRIBUTION · % CONTRIB BY ${unit}`;
  } else {
    bars = page.rows.map(r => ({
      label: r.pair,
      v: Math.max(-1, Math.min(1, r.tilt)),
      val: sgn(r.tilt, num(Math.abs(r.tilt), 2)),
    }));
    title = `ENSEMBLE TILT · BY ${unit}`;
  }
  const barHtml = bars.map(b => {
    const color = b.v > 0.001 ? G : (b.v < -0.001 ? R : FAINT);
    const left = b.v >= 0 ? '50%' : (50 - Math.abs(b.v) * 50) + '%';
    const width = Math.abs(b.v) * 50 + '%';
    return `
    <div style="display:flex;align-items:center;gap:9px;padding:4px 0;font-size:10px">
      <span style="color:#c9e8cc;width:64px">${esc(b.label)}</span>
      <span style="position:relative;flex:1;height:5px;background:#1a1a1a;display:inline-block"><span style="position:absolute;left:50%;top:-2px;width:1px;height:9px;background:#2e2e2e"></span><span style="position:absolute;top:0;height:5px;left:${left};width:${width};background:${color}"></span></span>
      <span style="color:${color};width:56px;text-align:right">${b.val}</span>
    </div>`;
  }).join('');

  return `
  <div style="display:grid;grid-template-columns:2.1fr 1fr;border-bottom:1px solid #262626">
    <div style="padding:12px 18px;border-right:1px solid #262626">
      <div style="display:flex;gap:14px;font-size:9px;letter-spacing:.12em;margin-bottom:8px"><span style="color:#eaffec">■ EQUITY CURVE · AUD</span><span style="color:#61805f">${rangeTxt}</span></div>
      <svg viewBox="0 0 600 140" preserveAspectRatio="none" style="width:100%;height:150px;display:block">
        <line x1="0" y1="35" x2="600" y2="35" stroke="#1a1a1a" stroke-width="1"></line>
        <line x1="0" y1="70" x2="600" y2="70" stroke="#1a1a1a" stroke-width="1"></line>
        <line x1="0" y1="105" x2="600" y2="105" stroke="#1a1a1a" stroke-width="1"></line>
        <polygon points="${vals.length ? '0,140 ' + p.join(' ') + ' 600,140' : ''}" fill="${upCurve ? 'rgba(126,231,135,0.08)' : 'rgba(255,123,114,0.08)'}"></polygon>
        <polyline points="${p.join(' ')}" fill="none" stroke="${stroke}" stroke-width="1.6" stroke-linejoin="round"></polyline>
      </svg>
      <div style="display:flex;justify-content:space-between;font-size:9px;color:#3d543f;margin-top:5px"><span>${esc(mmdd(eqStart) || eqStart)}</span><span>${esc(String(eqEnd).slice(5))}</span></div>
    </div>
    <div>
      <div style="padding:10px 18px;border-bottom:1px solid #1a1a1a;font-size:9px;color:#eaffec;letter-spacing:.14em">■ ${title}</div>
      <div style="padding:8px 18px 12px">${barHtml}</div>
    </div>
  </div>`;
}

/* =================== agent pair chart (with TA + hover) ================ */
let _chartCtx = null;   // context for the mousemove-driven candle hover

function chartSectionHTML(page) {
  const selPair = agentSelPair(page);
  const row = page.rows.find(r => r.pair === selPair);
  if (!row) return '';
  const tf = agentTf(page);
  const T = TF_MAP[tf];
  const closeN = +row.price || 1;
  const volN = (+row.ann_vol || 0.02);
  const { bars: cBars, real: cReal } = synthCandles(selPair, closeN, volN, T.n, tf, T.s);
  const n2 = cBars.length;
  let cHi = -Infinity, cLo = Infinity;
  for (const b of cBars) { cHi = Math.max(cHi, b.h); cLo = Math.min(cLo, b.l); }
  const CY = v => Math.max(2, Math.min(238, 10 + (1 - (v - cLo) / (cHi - cLo || 1)) * 220));
  const cp = candlePaths(cBars, 1200, CY);
  const cbw = cp.bw;
  const cdp = closeN >= 1000 ? 0 : (closeN < 3 ? 4 : 2);
  const dollar = !isFxPair(selPair);
  const cFmt = v => (dollar ? '$' : '') + (+v).toLocaleString('en-US', { minimumFractionDigits: cdp, maximumFractionDigits: cdp });

  /* ---- TA overlays ---- */
  const ta = S.ta;
  const panes = S.taPanes || {};
  const closes = cBars.map(b => b.c);
  const cxAt = i => ((i + 0.5) * cbw).toFixed(1);
  const lineOf = (arr, from) => arr.map((v, j) => cxAt(from + j) + ',' + CY(v).toFixed(1)).join(' ');
  const emaOf = (vals, p) => { const k = 2 / (p + 1); const out = [vals[0]]; for (let i = 1; i < vals.length; i++) out.push(out[i - 1] + (vals[i] - out[i - 1]) * k); return out; };
  const pF = Math.max(3, Math.min(20, Math.floor(n2 / 3)));
  const pS = Math.max(pF * 2, Math.min(100, Math.floor(n2 * 0.8)));
  const wB = Math.max(5, Math.min(20, Math.floor(n2 / 2)));
  const wD = Math.max(5, Math.min(55, Math.floor(n2 / 2)));
  let taEmaFast = '', taEmaSlow = '', taBollUp = '', taBollDn = '', taBollFill = '', taDonHi = '', taDonLo = '';
  const legendParts = [];
  if (ta.ema) {
    taEmaFast = lineOf(emaOf(closes, pF), 0);
    taEmaSlow = lineOf(emaOf(closes, pS), 0);
    legendParts.push('EMA ' + pF + ' AMBER · EMA ' + pS + ' PALE');
  }
  if (ta.boll) {
    const up = [], dn = [];
    for (let i = wB - 1; i < n2; i++) {
      const win = closes.slice(i - wB + 1, i + 1);
      const m = win.reduce((a, b) => a + b, 0) / wB;
      const sd = Math.sqrt(win.reduce((a, b) => a + (b - m) ** 2, 0) / wB);
      up.push(m + 2 * sd); dn.push(m - 2 * sd);
    }
    taBollUp = lineOf(up, wB - 1);
    taBollDn = lineOf(dn, wB - 1);
    taBollFill = taBollUp + ' ' + dn.map((v, j) => cxAt(wB - 1 + (dn.length - 1 - j)) + ',' + CY(dn[dn.length - 1 - j]).toFixed(1)).join(' ');
    legendParts.push('BOLL ' + wB + '/±2σ DASHED');
  }
  if (ta.don) {
    const hiA = [], loA = [];
    for (let i = wD - 1; i < n2; i++) {
      let mh = -Infinity, ml = Infinity;
      for (let j = i - wD + 1; j <= i; j++) { mh = Math.max(mh, cBars[j].h); ml = Math.min(ml, cBars[j].l); }
      hiA.push(mh); loA.push(ml);
    }
    taDonHi = lineOf(hiA, wD - 1);
    taDonLo = lineOf(loA, wD - 1);
    legendParts.push('DONCHIAN ' + wD + ' OCHRE');
  }
  const taChips = [
    { key: 'ema', label: 'EMA ' + pF + '/' + pS, dot: '#e3b341' },
    { key: 'boll', label: 'BOLLINGER ±2σ', dot: '#9db5a0' },
    { key: 'don', label: 'DONCHIAN ' + wD, dot: '#8a7433' },
  ].map(c => `<span class="hv-dim" data-act="ta" data-arg="${c.key}" data-tip="${c.key}" style="position:relative;display:inline-flex;align-items:center;gap:5px;font-size:9px;letter-spacing:.06em;padding:3px 9px;border:1px solid ${ta[c.key] ? '#2a4a2c' : '#262626'};color:${ta[c.key] ? PALE : DIM};background:${ta[c.key] ? '#12200f' : 'transparent'};cursor:pointer;user-select:none"><span style="width:7px;height:2px;background:${c.dot};display:inline-block"></span>${c.label}<span style="color:#3d543f;margin-left:1px">ⓘ</span></span>`).join('\n');
  const paneChips = ['RSI', 'MOMENTUM', 'ADX'].map(k => {
    const on = !!panes[k];
    return `<span class="hv-dim" data-act="pane" data-arg="${k}" data-tip="${k}" style="position:relative;font-size:9px;letter-spacing:.06em;padding:3px 9px;border:1px solid ${on ? '#2a4a2c' : '#262626'};color:${on ? PALE : DIM};background:${on ? '#12200f' : 'transparent'};cursor:pointer;user-select:none">${k} <span style="color:#3d543f">ⓘ</span></span>`;
  }).join('\n');

  /* ---- in-chart indicator sub-panes (docked under price, shared x-axis) ---- */
  const paneSub = (name) => {
    if (name === 'RSI') {
      const p = Math.max(5, Math.min(14, Math.floor(n2 / 3)));
      let g = 0, l = 0;
      const rsis = [];
      for (let i = 1; i < n2; i++) {
        const ch = closes[i] - closes[i - 1];
        g = (g * (p - 1) + Math.max(ch, 0)) / p;
        l = (l * (p - 1) + Math.max(-ch, 0)) / p;
        rsis.push(l === 0 ? 100 : 100 - 100 / (1 + g / l));
      }
      const last = rsis[rsis.length - 1];
      const PY = v => 8 + (1 - v / 100) * 64;
      return _paneHTML('RSI(' + p + ')', last.toFixed(1),
        (last >= 70 ? 'OVERBOUGHT' : last <= 30 ? 'OVERSOLD' : 'NEUTRAL') + ' · 70 ┄ · 30 ┄',
        PY(70).toFixed(1), PY(50).toFixed(1), PY(30).toFixed(1),
        rsis.map((v, j) => cxAt(1 + j) + ',' + PY(v).toFixed(1)).join(' '), '',
        last >= 70 ? R : last <= 30 ? G : '#c9e8cc');
    }
    if (name === 'MOMENTUM') {
      const w = Math.max(3, Math.min(20, Math.floor(n2 / 3)));
      const rocs = [];
      for (let i = w; i < n2; i++) rocs.push((closes[i] / closes[i - w] - 1) * 100);
      const m = Math.max(...rocs.map(Math.abs), 0.1);
      const PY = v => 40 - (v / m) * 30;
      const pts = rocs.map((v, j) => cxAt(w + j) + ',' + PY(v).toFixed(1)).join(' ');
      const last = rocs[rocs.length - 1];
      return _paneHTML('MOMENTUM · ROC(' + w + ') %', sgn(last, Math.abs(last).toFixed(2) + '%'),
        (last >= 0 ? 'UP-MOMENTUM' : 'DOWN-MOMENTUM') + ' · ZERO LINE CENTRE',
        PY(m * 0.66).toFixed(1), '40', PY(-m * 0.66).toFixed(1), pts,
        cxAt(w) + ',40 ' + pts + ' ' + cxAt(n2 - 1) + ',40', last >= 0 ? G : R);
    }
    if (name === 'ADX') {
      const p = Math.max(5, Math.min(14, Math.floor(n2 / 3)));
      let atr = 0, pd = 0, nd = 0, adx = 0;
      const adxs = [];
      for (let i = 1; i < n2; i++) {
        const b = cBars[i], pb = cBars[i - 1];
        const tr = Math.max(b.h - b.l, Math.abs(b.h - pb.c), Math.abs(b.l - pb.c));
        const up = b.h - pb.h, dn = pb.l - b.l;
        atr = (atr * (p - 1) + tr) / p;
        pd = (pd * (p - 1) + (up > dn && up > 0 ? up : 0)) / p;
        nd = (nd * (p - 1) + (dn > up && dn > 0 ? dn : 0)) / p;
        const pdi = atr ? 100 * pd / atr : 0, ndi = atr ? 100 * nd / atr : 0;
        const dx = (pdi + ndi) ? 100 * Math.abs(pdi - ndi) / (pdi + ndi) : 0;
        adx = (adx * (p - 1) + dx) / p;
        adxs.push(adx);
      }
      const last = adxs[adxs.length - 1];
      const PY = v => 8 + (1 - Math.min(v, 60) / 60) * 64;
      return _paneHTML('ADX(' + p + ') · TREND STRENGTH', last.toFixed(1),
        (last >= 25 ? 'TRENDING' : last < 20 ? 'RANGING' : 'BUILDING') + ' · 25 ┄ · 20 ┄',
        PY(40).toFixed(1), PY(25).toFixed(1), PY(10).toFixed(1),
        adxs.map((v, j) => cxAt(1 + j) + ',' + PY(v).toFixed(1)).join(' '), '',
        last >= 25 ? G : AMB);
    }
    return '';
  };
  const panesHtml = ['RSI', 'MOMENTUM', 'ADX'].filter(k => panes[k]).map(paneSub).join('');

  /* ---- phases (move breakdown) ---- */
  const barWords = { '1-MIN': 'minute', '60-MIN': 'hour', '4-HR': '4-hour stretch', 'DAILY': 'day', 'WEEKLY': 'week', 'MONTHLY': 'month' };
  const barWord = barWords[T.desc] || 'bar';
  const nPh = 4;
  const phLen = Math.floor(n2 / nPh);
  const phases = [];
  const phTot = (closes[n2 - 1] / cBars[0].o - 1) * 100;
  for (let k = 0; k < nPh; k++) {
    const s0 = k * phLen, s1 = k === nPh - 1 ? n2 - 1 : (k + 1) * phLen - 1;
    const st = cBars[s0].o, en = closes[s1];
    const chg = (en / st - 1) * 100;
    let sumAbs = 0;
    for (let j = s0; j <= s1; j++) sumAbs += Math.abs(cBars[j].c / cBars[j].o - 1) * 100;
    const typ = (sumAbs / (s1 - s0 + 1)) * Math.sqrt(s1 - s0 + 1);
    const strong = Math.abs(chg) > typ, flat = Math.abs(chg) < typ * 0.35;
    let text;
    if (flat) {
      text = 'Price chopped sideways (' + sgn(chg, Math.abs(chg).toFixed(1) + '%') + ') — small bodies, long wicks, buyers and sellers cancelling out. In a "ranging" stretch like this the Mean-reversion agent gets the loudest vote: fade the extremes, expect a snap back to average.';
    } else if (chg >= 0) {
      text = 'Price climbed ' + chg.toFixed(1) + '%' + (strong ? ' in a decisive run of green candles closing near their highs.' : ' in an uneven grind higher.') + ' Rising closes are exactly what the Trend, Momentum and Breakout agents reward — they read this leg as a reason to be long.';
    } else {
      text = 'Price fell ' + Math.abs(chg).toFixed(1) + '%' + (strong ? ' in a decisive slide of red candles.' : ' in a slow bleed lower.') + ' Falling closes flip the Trend and Momentum agents negative — legs like this are why the book leans short here.';
    }
    if (k === nPh - 1) text += ' This is the most recent leg — it sets the ensemble’s current stance.';
    phases.push({
      label: 'PHASE ' + (k + 1) + ' · BARS ' + (s0 + 1) + '–' + (s1 + 1),
      range: cFmt(st) + ' → ' + cFmt(en),
      chg: sgn(chg, Math.abs(chg).toFixed(1) + '%'),
      arrow: flat ? '↔' : (chg >= 0 ? '▲' : '▼'),
      color: flat ? AMB : (chg >= 0 ? G : R),
      text,
    });
  }
  const drivenM = String(row.why || '').match(/Driven by:[^.]*\./);
  const phaseSummary = selPair + ' MOVED ' + sgn(phTot, Math.abs(phTot).toFixed(1) + '%') + ' OVER THIS ' + tf + ' WINDOW · BOOK IS ' + (row.weight >= 0 ? 'LONG' : 'SHORT') + ' ' + Math.abs(row.weight * 100).toFixed(1) + '% · ' + (drivenM ? drivenM[0].toUpperCase() : '');

  /* ---- fundamentals ---- */
  const fu = _fundamentals(page, row, selPair);

  /* ---- chips + dropdown ---- */
  const pairChips = page.rows.map(r => {
    const on = r.pair === selPair;
    return `<span class="hv-dim" data-act="pair" data-arg="${esc(r.pair)}" style="font-size:9px;letter-spacing:.06em;padding:3px 9px;border:1px solid ${on ? '#2a4a2c' : '#262626'};color:${on ? PALE : DIM};background:${on ? '#12200f' : 'transparent'};cursor:pointer;user-select:none">${esc(r.pair)}</span>`;
  }).join('\n');
  const tfMenu = !S.tfOpen ? '' : `
    <div style="position:absolute;top:calc(100% + 4px);right:0;min-width:128px;background:#0d0d0d;border:1px solid #2a4a2c;box-shadow:0 10px 28px rgba(0,0,0,.8);z-index:80">
      ${Object.keys(TF_MAP).map(k => `<div class="hv-menu" data-act="tf" data-arg="${k}" style="display:flex;justify-content:space-between;gap:12px;padding:6px 12px;font-size:9.5px;letter-spacing:.06em;color:${k === tf ? PALE : DIM};background:${k === tf ? '#12200f' : 'transparent'};cursor:pointer"><span>${k}</span><span style="color:#3d543f">${TF_MAP[k].desc}</span></div>`).join('')}
    </div>`;

  /* stash context for the mousemove hover layer */
  _chartCtx = { bars: cBars, cbw, n2, cFmt, barWord, tf };

  /* ---- economic-news markers: vertical lines at the bar a release hit ---- */
  const newsMarks = newsForPair(page, selPair);
  let newsLines = '';
  const newsOnChart = T.desc === 'DAILY' && newsMarks.length;
  if (newsOnChart) {
    const barDates = tradingDaysBack(page.last_bar_date, n2);   // barDates[i] = ISO date of bar i
    const byBar = {};
    for (const ev of newsMarks) {
      let i = barDates.indexOf(ev.date);
      if (i < 0) {                                              // nearest bar on/after the release
        i = barDates.findIndex(d => d >= ev.date);
        if (i < 0) continue;                                    // event outside the window
      }
      (byBar[i] = byBar[i] || []).push(ev);
    }
    newsLines = Object.entries(byBar).map(([i, evs]) => {
      const x = ((+i + 0.5) * cbw).toFixed(1);
      const hi = evs.some(e => e.impact === 'high');
      const col = hi ? '#e3b341' : '#8a7433';
      const tip = evs.map(e => `${e.date}${e.time ? ' ' + e.time : ''} · ${e.currency} ${e.event}` +
        (e.actual != null ? ` (actual ${e.actual}${e.estimate != null ? ' vs est ' + e.estimate : ''})` : '') +
        (e.bias_text ? ` → likely ${e.bias_text}` : '')).join('\n');
      return `<line x1="${x}" y1="0" x2="${x}" y2="240" stroke="${col}" stroke-width="1" stroke-dasharray="2 3" opacity="0.7"><title>${esc(tip)}</title></line>` +
             `<path d="M${x},2 l-4,-0 l4,7 l4,-7 Z" fill="${col}"><title>${esc(tip)}</title></path>`;
    }).join('\n');
  }

  const chartSrc = cReal ? 'LIVE OHLC (candles.json)' : 'SYNTHETIC BARS ANCHORED TO REAL LAST CLOSE — DROP A candles.json TO WIRE REAL OHLC';
  const sideColor = row.weight >= 0 ? G : R;

  return `
  <div style="border-bottom:1px solid #262626">
    <div style="display:flex;align-items:center;gap:14px;padding:10px 18px;border-bottom:1px solid #1a1a1a;flex-wrap:wrap">
      <span style="font-size:9px;color:#eaffec;letter-spacing:.14em">■ PAIR CHART · ${esc(selPair)}</span>
      <span style="font-size:9px;font-weight:600;color:${sideColor}">${row.weight >= 0 ? '▲ LONG ' : '▼ SHORT '}</span>
      <span style="font-size:9px;color:#61805f">${esc(row.regime)} · VOL ${num(volN * 100, 0)}%</span>
      <div style="position:relative;margin-left:auto">
        <span class="hv-dim" data-act="tf-toggle" style="display:inline-flex;align-items:center;gap:6px;font-size:9px;letter-spacing:.08em;padding:4px 10px;border:1px solid #2a4a2c;color:#7ee787;background:#0d0d0d;cursor:pointer;user-select:none">◷ ${tf} <span style="color:#61805f">▾</span></span>
        ${tfMenu}
      </div>
      <div style="display:flex;gap:3px;flex-wrap:wrap">${pairChips}</div>
    </div>
    <div style="display:flex;align-items:center;gap:5px;padding:7px 18px;border-bottom:1px solid #1a1a1a">
      <span style="font-size:8.5px;color:#61805f;letter-spacing:.12em;margin-right:4px">TA</span>
      ${taChips}
      <span style="color:#2e2e2e;margin:0 5px">│</span>
      ${paneChips}
      <span style="margin-left:auto;font-size:8.5px;color:#3d543f;letter-spacing:.06em">${legendParts.join(' · ') || 'ALL OVERLAYS OFF — CLICK TO ADD'}</span>
    </div>
    <div style="padding:12px 18px 6px">
      <div style="position:relative" id="candle-zone" data-candles="1">
      <svg viewBox="0 0 1200 240" preserveAspectRatio="none" style="width:100%;height:250px;display:block">
        <rect id="hov-rect" x="0" y="0" width="0" height="240" fill="rgba(234,255,236,0.06)"></rect>
        <line x1="0" y1="60" x2="1200" y2="60" stroke="#1a1a1a" stroke-width="1"></line>
        <line x1="0" y1="120" x2="1200" y2="120" stroke="#1a1a1a" stroke-width="1"></line>
        <line x1="0" y1="180" x2="1200" y2="180" stroke="#1a1a1a" stroke-width="1"></line>
        <path d="${cp.wickUp}" stroke="#7ee787" stroke-width="1" fill="none"></path>
        <path d="${cp.bodyUp}" fill="#7ee787"></path>
        <path d="${cp.wickDn}" stroke="#ff7b72" stroke-width="1" fill="none"></path>
        <path d="${cp.bodyDn}" fill="#ff7b72"></path>
        <polygon points="${taBollFill}" fill="rgba(201,232,204,0.05)"></polygon>
        <polyline points="${taBollUp}" stroke="#9db5a0" stroke-width="1" stroke-dasharray="3 3" fill="none" opacity="0.8"></polyline>
        <polyline points="${taBollDn}" stroke="#9db5a0" stroke-width="1" stroke-dasharray="3 3" fill="none" opacity="0.8"></polyline>
        <polyline points="${taDonHi}" stroke="#8a7433" stroke-width="1" stroke-dasharray="6 3" fill="none"></polyline>
        <polyline points="${taDonLo}" stroke="#8a7433" stroke-width="1" stroke-dasharray="6 3" fill="none"></polyline>
        <polyline points="${taEmaFast}" stroke="#e3b341" stroke-width="1.4" fill="none"></polyline>
        <polyline points="${taEmaSlow}" stroke="#c9e8cc" stroke-width="1.4" fill="none" opacity="0.85"></polyline>
        <line x1="0" y1="${CY(closeN).toFixed(1)}" x2="1200" y2="${CY(closeN).toFixed(1)}" stroke="#e3b341" stroke-width="1" stroke-dasharray="5 4" opacity="0.7"></line>
        ${newsLines}
      </svg>
      <div id="candle-pop" style="display:none;position:absolute;top:10px;left:0;width:292px;background:#0d0d0d;border:1px solid #2a4a2c;border-radius:3px;box-shadow:0 12px 32px rgba(0,0,0,.8);z-index:70;padding:11px 13px;pointer-events:none"></div>
      </div>
      ${panesHtml}
      <div style="display:flex;gap:16px;font-size:9px;color:#3d543f;letter-spacing:.08em;padding:6px 0 8px;flex-wrap:wrap">
        <span>${T.label}</span>
        <span>HI <span style="color:#61805f">${cFmt(cHi)}</span></span>
        <span>LO <span style="color:#61805f">${cFmt(cLo)}</span></span>
        <span style="color:#e3b341">LAST ${pairPrice(selPair, closeN)} ┄</span>
        <span>BOOK <span style="color:${sideColor}">${sgnPct(row.weight, 1)}</span></span>
        ${newsOnChart ? `<span style="color:#e3b341">◆ ${newsMarks.length} NEWS MARKER${newsMarks.length > 1 ? 'S' : ''} — HOVER A LINE</span>`
          : (newsMarks.length ? '<span style="color:#8a7433">◆ NEWS IN FEED BELOW (markers show on the 1M / 6M daily view)</span>' : '')}
        <span style="margin-left:auto;color:#8a7433">${chartSrc}</span>
      </div>
      ${newsPanelHTML(page, selPair)}
      <div style="border-top:1px solid #1a1a1a;padding:10px 0 12px">
        <div style="display:flex;gap:14px;align-items:baseline;font-size:8.5px;letter-spacing:.1em;margin-bottom:9px"><span style="color:#eaffec;letter-spacing:.14em">■ MOVE BREAKDOWN — WHAT HAPPENED &amp; WHY</span><span style="color:#61805f">${esc(phaseSummary)}</span></div>
        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px">
          ${phases.map(ph => `
          <div style="border:1px solid #262626;background:#0d0d0d;padding:10px 12px">
            <div style="display:flex;justify-content:space-between;align-items:baseline"><span style="font-size:9px;color:#61805f;letter-spacing:.1em">${ph.label}</span><span style="font-size:11px;color:${ph.color};font-weight:600">${ph.arrow} ${ph.chg}</span></div>
            <div style="font-size:9px;color:#3d543f;margin-top:2px;letter-spacing:.06em">${ph.range}</div>
            <div style="font-size:9.5px;color:#9db5a0;line-height:1.7;margin-top:7px">${esc(ph.text)}</div>
          </div>`).join('')}
        </div>
      </div>
      ${fu}
    </div>
  </div>`;
}

function _paneHTML(label, val, hint, y1, y2, y3, pts, area, valColor) {
  const vc = valColor || '#e3b341';
  return `
  <div style="border-top:1px solid #1a1a1a;padding-top:6px;margin-top:2px">
    <div style="display:flex;gap:14px;font-size:8.5px;color:#61805f;letter-spacing:.12em;margin-bottom:4px"><span style="color:#c9e8cc">${label}</span><span>LAST <span style="color:${vc};font-weight:600">${val}</span></span><span style="margin-left:auto">${hint}</span></div>
    <svg viewBox="0 0 1200 80" preserveAspectRatio="none" style="width:100%;height:70px;display:block">
      <line x1="0" y1="${y1}" x2="1200" y2="${y1}" stroke="#2e2e2e" stroke-width="1" stroke-dasharray="4 4"></line>
      <line x1="0" y1="${y2}" x2="1200" y2="${y2}" stroke="#1a1a1a" stroke-width="1"></line>
      <line x1="0" y1="${y3}" x2="1200" y2="${y3}" stroke="#2e2e2e" stroke-width="1" stroke-dasharray="4 4"></line>
      <polygon points="${area}" fill="rgba(201,232,204,0.06)"></polygon>
      <polyline points="${pts}" fill="none" stroke="${vc}" stroke-width="1.3" stroke-linejoin="round"></polyline>
    </svg>
  </div>`;
}

/* ---- KEY NEWS panel: real scheduled economic releases for the book's
   currencies, most-recent first; the pair's own legs are highlighted and
   marked ◆ (those are the lines drawn on the chart above) ---- */
function newsPanelHTML(page, selPair) {
  const news = (page && page.news) || [];
  const legs = selPair && selPair.length === 6
    ? new Set([selPair.slice(0, 3).toUpperCase(), selPair.slice(3).toUpperCase()]) : new Set();

  const hasKey = page && page.news_available;
  if (!news.length) {
    // Only show the empty panel when the feed is genuinely wired but quiet;
    // with no API key at all, stay silent rather than nag.
    if (!hasKey) return '';
    return `
    <div style="border-top:1px solid #1a1a1a;padding:10px 0 12px">
      <div style="font-size:8.5px;color:#eaffec;letter-spacing:.14em;margin-bottom:6px">■ KEY NEWS · ECONOMIC CALENDAR</div>
      <div style="font-size:9.5px;color:#61805f;line-height:1.7">No high-impact scheduled releases for this book’s currencies in the last ${45} days.</div>
    </div>`;
  }

  const biasStyle = b => b === 'positive' ? { c: G, t: '▲ ' } : b === 'negative' ? { c: R, t: '▼ ' }
    : b === 'neutral' ? { c: '#9db5a0', t: '~ ' } : b === 'watch' ? { c: AMB, t: '◷ ' } : { c: '#3d543f', t: '' };
  const rows = news.slice().reverse().slice(0, 14).map(e => {
    const onPair = legs.has(String(e.currency).toUpperCase());
    const dot = e.impact === 'high' ? '#e3b341' : '#8a7433';
    const surprise = (e.actual != null && e.estimate != null)
      ? (parseFloat(e.actual) > parseFloat(e.estimate) ? G
         : parseFloat(e.actual) < parseFloat(e.estimate) ? R : '#c9e8cc') : '#c9e8cc';
    const b = biasStyle(e.bias);
    return `
    <div style="display:grid;grid-template-columns:.7fr .45fr .45fr 1.7fr 1.05fr 1.05fr;gap:8px;padding:5px 0;font-size:10px;border-bottom:1px solid #121212;align-items:baseline${onPair ? ';background:rgba(227,179,65,.05)' : ''}">
      <span style="color:#61805f">${esc(mdy(e.date))}${e.time ? ' <span style="color:#3d543f">' + esc(e.time) + '</span>' : ''}</span>
      <span>${onPair ? '<span style="color:#e3b341">◆</span> ' : ''}<span style="color:${onPair ? '#eaffec' : '#9db5a0'}">${esc(e.currency)}</span></span>
      <span style="display:inline-flex;align-items:center;gap:4px;color:#61805f"><span style="width:6px;height:6px;border-radius:50%;background:${dot};display:inline-block"></span>${e.impact === 'high' ? 'HIGH' : 'MED'}</span>
      <span style="color:#c9e8cc">${esc(e.event || '')}</span>
      <span style="text-align:right;color:#61805f">${e.actual != null ? `A <span style="color:${surprise}">${esc(String(e.actual))}</span>` : ''}${e.estimate != null ? ` <span style="color:#3d543f">/ E ${esc(String(e.estimate))}</span>` : ''}</span>
      <span style="text-align:right;color:${b.c};font-weight:600">${e.bias_text ? b.t + esc(e.bias_text) : '<span style="color:#3d543f">—</span>'}</span>
    </div>`;
  }).join('');

  return `
  <div style="border-top:1px solid #1a1a1a;padding:10px 0 12px">
    <div style="display:flex;gap:12px;align-items:baseline;margin-bottom:8px;flex-wrap:wrap">
      <span style="font-size:8.5px;color:#eaffec;letter-spacing:.14em">■ KEY NEWS · SCHEDULED ECONOMIC RELEASES</span>
      <span style="font-size:8.5px;color:#61805f">◆ ${esc(selPair)} legs — marked on the chart above</span>
      <span style="margin-left:auto;font-size:8.5px;color:#3d543f">LIKELY IMPACT = SURPRISE × INDICATOR TYPE · CORRELATION, NOT PROVEN CAUSE</span>
    </div>
    <div style="display:grid;grid-template-columns:.7fr .45fr .45fr 1.7fr 1.05fr 1.05fr;gap:8px;padding-bottom:4px;font-size:8px;color:#61805f;letter-spacing:.1em;border-bottom:1px solid #1a1a1a"><span>WHEN</span><span>CCY</span><span>IMPACT</span><span>EVENT</span><span style="text-align:right">ACTUAL / EST</span><span style="text-align:right">LIKELY IMPACT</span></div>
    ${rows}
  </div>`;
}

/* ---- fundamentals panel (indicative figures, ported from the design) ---- */
function _fundamentals(page, row, cp) {
  const cw = row.weight;
  const carryVote = row.agents[4] || 0;
  const carryStr = sgn(carryVote, Math.abs(carryVote).toFixed(2));
  let fuRisk, fuRiskColor;
  if (page.bar === '60m') { fuRisk = page.net >= 0 ? 'RISK-ON · INTRADAY' : 'RISK-OFF · INTRADAY'; fuRiskColor = page.net >= 0 ? G : AMB; }
  else if (page.net <= -0.05) { fuRisk = 'RISK-OFF TILT'; fuRiskColor = AMB; }
  else if (page.net >= 0.05) { fuRisk = 'RISK-ON TILT'; fuRiskColor = G; }
  else { fuRisk = 'MIXED'; fuRiskColor = TXT; }

  const baseC = cp.slice(0, 3), quoteC = cp.slice(3);
  let fu;
  if (cp.length === 6 && CBD[baseC] && CBD[quoteC]) {
    const B = CBD[baseC], Q = CBD[quoteC];
    const diff = B.rate - Q.rate;
    const favours = diff >= 0 ? baseC : quoteC;
    fu = {
      rates: [
        { code: baseC, cb: B.cb, rate: B.rate.toFixed(2) + '%', color: B.rate >= Q.rate ? G : TXT },
        { code: quoteC, cb: Q.cb, rate: Q.rate.toFixed(2) + '%', color: Q.rate > B.rate ? G : TXT },
      ],
      diffTxt: 'RATE GAP ' + sgn(diff, Math.abs(diff).toFixed(2) + '%') + ' IN FAVOUR OF ' + favours,
      carryTxt: 'Higher rates attract foreign capital and strengthen a currency — and holding the higher-yielding side EARNS this gap daily. That is the Carry agent’s vote here: ' + carryStr + '.',
      econ: [
        { code: baseC, gdp: B.gdp, cpi: B.cpi, jobs: B.jobs },
        { code: quoteC, gdp: Q.gdp, cpi: Q.cpi, jobs: Q.jobs },
      ],
      econTxt: 'Hot inflation (CPI) pressures a central bank to keep rates high — currency-positive. Weak GDP or rising unemployment pushes toward cuts — currency-negative.',
      geoTxt: 'Political stability, trade balances and risk appetite move flows between ' + baseC + ' and ' + quoteC + '. In risk-off stress money runs to USD, JPY and CHF; AUD and NZD trade like risk assets.',
      events: [...(EV[baseC] || []), ...(EV[quoteC] || [])].slice(0, 3),
      bias: Math.abs(diff) < 0.5 ? 'RATES NEUTRAL' : (diff > 0 ? '▲ RATE GAP FAVOURS ' + baseC + ' — PAIR-POSITIVE' : '▼ RATE GAP FAVOURS ' + quoteC + ' — PAIR-NEGATIVE'),
      biasColor: Math.abs(diff) < 0.5 ? TXT : (diff > 0 ? G : R),
      align: Math.abs(diff) < 0.5 ? '' : ((diff > 0) === (cw >= 0) ? 'ALIGNS WITH THE TECHNICAL BOOK' : 'CONFLICTS WITH THE TECHNICAL BOOK — THE ENSEMBLE IS TRADING THE TREND, NOT THE YIELD'),
    };
  } else if (CRY[cp]) {
    fu = {
      rates: [
        { code: baseC, cb: 'NO CENTRAL BANK', rate: 'FUNDING ±0.01%/8H', color: AMB },
        { code: 'USD', cb: CBD.USD.cb, rate: CBD.USD.rate.toFixed(2) + '%', color: TXT },
      ],
      diffTxt: 'NO YIELD ON ' + baseC + ' — USD RATES ARE ITS OPPORTUNITY COST',
      carryTxt: CRY[cp],
      econ: [
        { code: baseC, gdp: 'ETF FLOWS', cpi: 'LIQUIDITY', jobs: 'ON-CHAIN' },
        { code: 'USD', gdp: CBD.USD.gdp, cpi: CBD.USD.cpi, jobs: CBD.USD.jobs },
      ],
      econTxt: 'Crypto trades on liquidity: falling real yields and rising risk appetite lift coins. US macro data moves it through the USD leg.',
      geoTxt: 'Regulation headlines, ETF approvals and exchange stress dominate. In sharp risk-off, crypto sells off with (and harder than) equities.',
      events: ['JUL 29 · FOMC — LIQUIDITY DRIVER', 'WEEKLY · SPOT ETF FLOW PRINTS', 'JUL 15 · US CPI'],
      bias: 'NO RATE ANCHOR — SENTIMENT-DRIVEN', biasColor: AMB, align: '',
    };
  } else {
    const isBond = ['TLT', 'IEF', 'AGG', 'SHY'].includes(cp);
    fu = {
      rates: [
        { code: cp, cb: EQF[cp] || 'EARNINGS · SECTOR FLOWS', rate: '', color: TXT },
        { code: 'USD', cb: 'FED FUNDS — THE DISCOUNT RATE FOR EVERY ASSET', rate: CBD.USD.rate.toFixed(2) + '%', color: TXT },
      ],
      diffTxt: 'RATES ARE THE GRAVITY — HIGHER YIELDS COMPRESS VALUATIONS',
      carryTxt: isBond
        ? 'Bond ETF — price moves INVERSE to yields. Fed cuts lift it, sticky inflation sinks it. The Fed path is the whole story.'
        : 'Equity — earnings drive the long run; the multiple paid for them is set by rates and risk appetite.',
      econ: [
        { code: 'US', gdp: CBD.USD.gdp, cpi: CBD.USD.cpi, jobs: CBD.USD.jobs },
        { code: cp, gdp: isBond ? 'DURATION' : 'EPS GROWTH', cpi: isBond ? 'REAL YIELD' : 'MARGINS', jobs: isBond ? 'FED PATH' : 'GUIDANCE' },
      ],
      econTxt: 'Strong GDP and jobs support earnings — but hot CPI brings rate-hike risk, which hits valuations. Good news can be bad news.',
      geoTxt: isBond
        ? 'Treasuries are the world’s safe haven: geopolitical stress and growth scares bid them; deficits and inflation scare them.'
        : 'Trade policy, export rules and supply chains hit earnings directly; risk appetite sets what investors pay for them.',
      events: ['JUL 15 · US CPI', 'JUL 29 · FOMC DECISION', 'MID-JUL · Q2 EARNINGS SEASON'],
      bias: 'MACRO-SENSITIVE', biasColor: AMB, align: '',
    };
  }

  return `
  <div style="border-top:1px solid #1a1a1a;padding:10px 0 12px">
    <div style="display:flex;gap:12px;align-items:baseline;margin-bottom:9px;flex-wrap:wrap">
      <span style="font-size:8.5px;color:#eaffec;letter-spacing:.14em">■ FUNDAMENTALS · ${esc(cp)}</span>
      <span style="font-size:8.5px;letter-spacing:.08em;color:${fu.biasColor};border:1px solid #262626;padding:2px 8px">${esc(fu.bias)}</span>
      <span style="font-size:8.5px;color:#61805f">${esc(fu.align)}</span>
      <span style="margin-left:auto;font-size:8.5px;color:#3d543f">INDICATIVE FIGURES — WIRE A MACRO FEED FOR LIVE DATA</span>
    </div>
    <div style="display:grid;grid-template-columns:1.05fr 1.25fr 1.1fr;gap:10px">
      <div style="border:1px solid #262626;background:#0d0d0d;padding:10px 12px">
        <div style="font-size:8.5px;color:#61805f;letter-spacing:.12em;margin-bottom:8px">INTEREST RATES · THE PRIMARY DRIVER</div>
        ${fu.rates.map(r => `<div style="display:flex;align-items:baseline;gap:8px;padding:3px 0;font-size:10.5px"><span style="color:#eaffec;font-weight:600;width:34px">${esc(r.code)}</span><span style="color:#61805f;font-size:9px;flex:1">${esc(r.cb)}</span><span style="color:${r.color};font-weight:600">${esc(r.rate)}</span></div>`).join('')}
        <div style="font-size:10px;color:#c9e8cc;margin-top:6px;padding-top:6px;border-top:1px solid #1a1a1a">${esc(fu.diffTxt)}</div>
        <div style="font-size:9.5px;color:#61805f;line-height:1.65;margin-top:5px">${esc(fu.carryTxt)}</div>
      </div>
      <div style="border:1px solid #262626;background:#0d0d0d;padding:10px 12px">
        <div style="font-size:8.5px;color:#61805f;letter-spacing:.12em;margin-bottom:8px">ECONOMIC INDICATORS · GDP / CPI / JOBS</div>
        <div style="display:grid;grid-template-columns:.5fr 1fr 1fr 1fr;gap:4px;font-size:8.5px;color:#3d543f;letter-spacing:.08em;padding-bottom:4px;border-bottom:1px solid #1a1a1a"><span></span><span>GROWTH</span><span>INFLATION</span><span>JOBS</span></div>
        ${fu.econ.map(r => `<div style="display:grid;grid-template-columns:.5fr 1fr 1fr 1fr;gap:4px;padding:5px 0;font-size:10.5px;border-bottom:1px solid #121212"><span style="color:#eaffec;font-weight:600">${esc(r.code)}</span><span style="color:#c9e8cc">${esc(r.gdp)}</span><span style="color:#c9e8cc">${esc(r.cpi)}</span><span style="color:#c9e8cc">${esc(r.jobs)}</span></div>`).join('')}
        <div style="font-size:9.5px;color:#61805f;line-height:1.65;margin-top:6px">${esc(fu.econTxt)}</div>
      </div>
      <div style="border:1px solid #262626;background:#0d0d0d;padding:10px 12px">
        <div style="font-size:8.5px;color:#61805f;letter-spacing:.12em;margin-bottom:8px">GEOPOLITICS &amp; SENTIMENT</div>
        <div style="display:flex;justify-content:space-between;font-size:10.5px;padding:2px 0"><span style="color:#61805f;font-size:9px">RISK APPETITE</span><span style="color:${fuRiskColor};font-weight:600">${esc(fuRisk)}</span></div>
        <div style="font-size:9.5px;color:#9db5a0;line-height:1.65;margin:6px 0;padding-bottom:6px;border-bottom:1px solid #1a1a1a">${esc(fu.geoTxt)}</div>
        <div style="font-size:8.5px;color:#61805f;letter-spacing:.12em;margin-bottom:4px">WATCH NEXT</div>
        ${fu.events.map(e => `<div style="display:flex;gap:8px;font-size:10px;padding:2px 0"><span style="color:#e3b341">◆</span><span style="color:#c9e8cc">${esc(e)}</span></div>`).join('')}
      </div>
    </div>
  </div>`;
}

/* ---- per-candle hover: updates only the overlay, not the whole page ---- */
function updateCandleHover(e) {
  const zone = document.getElementById('candle-zone');
  const rect0 = document.getElementById('hov-rect');
  const pop = document.getElementById('candle-pop');
  if (!zone || !_chartCtx || !rect0 || !pop) return;
  const { bars, cbw, n2, cFmt, barWord } = _chartCtx;
  if (e == null) {
    S.candleIdx = null;
    rect0.setAttribute('width', '0');
    pop.style.display = 'none';
    return;
  }
  const r = zone.getBoundingClientRect();
  const i = Math.max(0, Math.min(n2 - 1, Math.floor((e.clientX - r.left) / r.width * n2)));
  if (i === S.candleIdx) return;
  S.candleIdx = i;
  const b = bars[i];
  const green = b.c >= b.o;
  const chg = (b.c / b.o - 1) * 100;
  const range = b.h - b.l || 1e-9;
  const bodyR = Math.abs(b.c - b.o) / range;
  const ago = n2 - 1 - i;
  let story;
  if (bodyR < 0.15) {
    story = 'A DOJI — the price opened at ' + cFmt(b.o) + ' and closed almost exactly where it started (' + cFmt(b.c) + '). Buyers and sellers fought to a draw this ' + barWord + '. After a strong run, indecision like this can hint the move is tiring.';
  } else if (green) {
    story = 'GREEN candle — the price opened at ' + cFmt(b.o) + ' and closed HIGHER at ' + cFmt(b.c) + ' (+' + Math.abs(chg).toFixed(2) + '%), so buyers won this ' + barWord + '. The thin wick on top shows it touched ' + cFmt(b.h) + ' before some sellers pushed back; the wick below marks ' + cFmt(b.l) + ', where buyers stepped in. ';
    story += bodyR > 0.7 ? 'A tall solid body like this means strong one-way buying conviction.' : 'A medium body means steady buying pressure rather than a violent move.';
  } else {
    story = 'RED candle — the price opened at ' + cFmt(b.o) + ' and closed LOWER at ' + cFmt(b.c) + ' (−' + Math.abs(chg).toFixed(2) + '%), so sellers won this ' + barWord + '. The wick above shows a failed push up to ' + cFmt(b.h) + '; the wick below marks the low at ' + cFmt(b.l) + '. ';
    story += bodyR > 0.7 ? 'A tall solid red body means heavy one-way selling.' : 'A medium body means steady selling rather than panic.';
  }
  rect0.setAttribute('x', (i * cbw).toFixed(1));
  rect0.setAttribute('width', cbw.toFixed(1));
  pop.style.display = 'block';
  pop.style.left = (((i + 0.5) / n2) * 100).toFixed(1) + '%';
  pop.style.transform = i > n2 * 0.55 ? 'translateX(calc(-100% - 14px))' : 'translateX(14px)';
  const title = 'BAR ' + (i + 1) + ' OF ' + n2 + (ago === 0 ? ' · CURRENT ' + barWord.toUpperCase() : ' · ' + ago + ' ' + barWord.toUpperCase() + (ago > 1 ? 'S' : '') + ' AGO');
  pop.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:7px"><span style="font-size:9px;color:#61805f;letter-spacing:.1em">${title}</span><span style="font-size:11px;font-weight:600;color:${green ? G : R}">${(chg >= 0 ? '▲ +' : '▼ −') + Math.abs(chg).toFixed(2)}%</span></div>
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:6px;margin-bottom:8px;padding-bottom:8px;border-bottom:1px solid #1a1a1a">
      <div><div style="font-size:8px;color:#61805f;letter-spacing:.1em">OPEN</div><div style="font-size:10.5px;color:#c9e8cc;margin-top:1px">${cFmt(b.o)}</div></div>
      <div><div style="font-size:8px;color:#61805f;letter-spacing:.1em">HIGH</div><div style="font-size:10.5px;color:#7ee787;margin-top:1px">${cFmt(b.h)}</div></div>
      <div><div style="font-size:8px;color:#61805f;letter-spacing:.1em">LOW</div><div style="font-size:10.5px;color:#ff7b72;margin-top:1px">${cFmt(b.l)}</div></div>
      <div><div style="font-size:8px;color:#61805f;letter-spacing:.1em">CLOSE</div><div style="font-size:10.5px;color:#eaffec;margin-top:1px">${cFmt(b.c)}</div></div>
    </div>
    <div style="font-size:9.5px;color:#9db5a0;line-height:1.7">${esc(story)}</div>`;
}

/* ====================== AGENT · POSITIONS ============================== */
function agentPositionsHTML(page) {
  const cap = page.per_pair_cap || 0.25;
  const rows = page.rows.map(p => {
    const long = p.weight >= 0;
    const sideColor = long ? G : R;
    const frac = Math.min(Math.abs(p.weight) / cap, 1);
    const mini = p.agents.map(v => {
      const bg = v > 0.02 ? G : (v < -0.02 ? R : '#2e2e2e');
      return `<span style="width:10px;height:${Math.max(Math.abs(v) * 100, 8).toFixed(0)}%;background:${bg};align-self:${v >= 0 ? 'flex-end' : 'flex-start'}"></span>`;
    }).join('');
    return `
    <div class="hv-row" ${hovAttrs('fx', p.pair)} style="position:relative;display:grid;grid-template-columns:.9fr .5fr 1.2fr .5fr .65fr .8fr .5fr 1.1fr;padding:6px 18px;font-size:11px;border-bottom:1px solid #121212;align-items:center;cursor:crosshair">
      <span style="color:#eaffec;text-decoration:underline;text-decoration-style:dotted;text-decoration-color:#3d543f;text-underline-offset:3px">${esc(p.pair)}</span>
      <span style="font-size:9px;font-weight:600;color:${sideColor}">${long ? '▲ LONG' : '▼ SHORT'}</span>
      <span style="display:flex;align-items:center;gap:8px"><span style="position:relative;width:90px;height:5px;background:#1a1a1a;display:inline-block"><span style="position:absolute;left:50%;top:-2px;width:1px;height:9px;background:#2e2e2e"></span><span style="position:absolute;top:0;height:5px;left:${long ? '50%' : (50 - frac * 50) + '%'};width:${frac * 50}%;background:${sideColor}"></span></span><span style="color:#c9e8cc;font-size:10px">${sgnPct(p.weight, 1)}</span></span>
      <span style="color:${cSign(p.tilt)}">${sgn(p.tilt, Math.abs(p.tilt).toFixed(2))}</span>
      <span style="font-size:9px;color:${p.regime === 'TRENDING' ? TXT : AMB}">${esc(p.regime)}</span>
      <span style="color:#9db5a0">${pairPrice(p.pair, p.price)}</span>
      <span style="color:#61805f">${num((p.ann_vol || 0) * 100, 0)}%</span>
      <span style="display:flex;gap:3px;align-items:flex-end;height:14px">${mini}</span>
    </div>`;
  }).join('');

  return `
  <div data-screen="agent-positions">
    <div style="display:flex;justify-content:space-between;align-items:center;padding:10px 18px;border-bottom:1px solid #1a1a1a"><span style="font-size:9px;color:#eaffec;letter-spacing:.14em">■ DECISION BOOK · ${page.rows.length} ${page.rows.some(r => !isFxPair(r.pair) && !isCrypto(r.pair)) ? 'SYMBOLS' : 'PAIRS'} · LONG/SHORT</span><span style="font-size:9px;color:#61805f">HOVER A PAIR FOR THE ENSEMBLE'S REASONING</span></div>
    <div style="display:grid;grid-template-columns:.9fr .5fr 1.2fr .5fr .65fr .8fr .5fr 1.1fr;padding:7px 18px;font-size:9px;color:#61805f;letter-spacing:.12em;border-bottom:1px solid #1a1a1a"><span>PAIR</span><span>SIDE</span><span>WEIGHT</span><span>TILT</span><span>REGIME</span><span>PRICE</span><span>VOL</span><span>AGENTS T·B·M·R·C·N</span></div>
    ${rows || '<div style="padding:22px 18px;font-size:11px;color:#61805f">— NO DECISIONS RECORDED YET. RUN THE FX ENGINE ONCE.</div>'}
  </div>`;
}

/* ---- agent decision popover (candles + why + votes) ---- */
function agentPopHTML(page, pair, rect) {
  const p = page.rows.find(r => r.pair === pair);
  if (!p) return '';
  const long = p.weight >= 0;
  const sideColor = long ? G : R;
  const nBars = page.bar === '60m' ? 60 : 48;
  const closeN = +p.price || 1;
  const { bars, real } = synthCandles(pair, closeN, +p.ann_vol || 0.02, nBars);
  let hi = -Infinity, lo = Infinity;
  for (const b of bars) { hi = Math.max(hi, b.h); lo = Math.min(lo, b.l); }
  const Y = v => 6 + (1 - (v - lo) / (hi - lo || 1)) * 98;
  const cp = candlePaths(bars, 590, Y);
  const fmtP = v => pairPrice(pair, v);
  const votes = AGENT_NAMES.map((name, i) => {
    const v = p.agents[i] || 0;
    const bg = v > 0.02 ? G : (v < -0.02 ? R : FAINT);
    return `
    <div style="display:flex;align-items:center;gap:8px;padding:2px 0;font-size:9.5px">
      <span style="color:#61805f;width:58px">${name}</span>
      <span style="position:relative;flex:1;height:4px;background:#1a1a1a;display:inline-block"><span style="position:absolute;left:50%;top:-2px;width:1px;height:8px;background:#2e2e2e"></span><span style="position:absolute;top:0;height:4px;left:${v >= 0 ? '50%' : (50 - Math.abs(v) * 50) + '%'};width:${Math.abs(v) * 50}%;background:${bg}"></span></span>
      <span style="color:${bg};width:38px;text-align:right">${sgn(v, Math.abs(v).toFixed(2))}</span>
    </div>`;
  }).join('');
  const ind = [
    { k: 'REGIME', v: p.regime }, { k: 'ANN VOL', v: num((p.ann_vol || 0) * 100, 0) + '%' },
    { k: 'WEIGHT', v: sgnPct(p.weight, 1) }, { k: 'TILT', v: sgn(p.tilt, Math.abs(p.tilt).toFixed(2)) },
    { k: 'SIDE', v: long ? 'LONG' : 'SHORT' }, { k: 'PRICE', v: fmtP(closeN) },
  ].map(iv => `<div><div style="font-size:8px;color:#61805f;letter-spacing:.1em">${iv.k}</div><div style="font-size:10.5px;color:#c9e8cc;margin-top:1px">${esc(iv.v)}</div></div>`).join('');
  const barLabel = (page.bar === '60m' ? '60 × 60-MINUTE BARS' : '48 × DAILY BARS') + (real ? ' · LIVE OHLC' : '');
  return popShell(rect, 480, 140, 620, '13px 15px', `
    <div style="display:flex;align-items:baseline;gap:10px;margin-bottom:8px">
      <span style="font-size:13px;font-weight:600;color:#eaffec">${esc(pair)}</span>
      <span style="font-size:10px;font-weight:600;color:${sideColor}">${long ? '▲ LONG' : '▼ SHORT'} ${sgnPct(p.weight, 1)}</span>
      <span style="font-size:9px;color:#61805f;letter-spacing:.1em">NET TILT ${sgn(p.tilt, Math.abs(p.tilt).toFixed(2))} · ${esc(p.regime)}</span>
      <span style="margin-left:auto;font-size:12px;color:#eaffec">${fmtP(closeN)}</span>
    </div>
    <svg viewBox="0 0 590 110" preserveAspectRatio="none" style="width:100%;height:110px;display:block;margin-bottom:2px">
      <line x1="0" y1="28" x2="590" y2="28" stroke="#1a1a1a" stroke-width="1"></line>
      <line x1="0" y1="55" x2="590" y2="55" stroke="#1a1a1a" stroke-width="1"></line>
      <line x1="0" y1="82" x2="590" y2="82" stroke="#1a1a1a" stroke-width="1"></line>
      <path d="${cp.wickUp}" stroke="#7ee787" stroke-width="1" fill="none"></path>
      <path d="${cp.bodyUp}" fill="#7ee787"></path>
      <path d="${cp.wickDn}" stroke="#ff7b72" stroke-width="1" fill="none"></path>
      <path d="${cp.bodyDn}" fill="#ff7b72"></path>
      <line x1="0" y1="${Y(closeN).toFixed(1)}" x2="590" y2="${Y(closeN).toFixed(1)}" stroke="#e3b341" stroke-width="1" stroke-dasharray="4 3" opacity="0.7"></line>
    </svg>
    <div style="display:flex;gap:14px;font-size:8.5px;color:#3d543f;letter-spacing:.08em;margin-bottom:9px"><span>${barLabel}</span><span>HI <span style="color:#61805f">${fmtP(hi)}</span></span><span>LO <span style="color:#61805f">${fmtP(lo)}</span></span><span style="margin-left:auto;color:#e3b341">LAST ${fmtP(closeN)} ┄</span>${real ? '' : '<span style="color:#8a7433">SYNTHETIC BARS — DROP A candles.json TO WIRE REAL OHLC</span>'}</div>
    <div style="display:grid;grid-template-columns:1.25fr 1fr;gap:14px">
      <div style="font-size:10.5px;line-height:1.75;color:#9db5a0;border-right:1px solid #1a1a1a;padding-right:14px">${esc(p.why)}</div>
      <div>
        <div style="font-size:8px;color:#61805f;letter-spacing:.12em;margin-bottom:6px">AGENT VOTES</div>
        ${votes}
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px;margin-top:9px;padding-top:8px;border-top:1px solid #1a1a1a">${ind}</div>
      </div>
    </div>`);
}

/* ====================== AGENT · BACKTEST =============================== */
/* Design placeholders for the stock books — clearly banner-labelled. A real
   cache (state/fx_backtest_{account}.json, kind:'fx') always wins. */
const ABD = {
  matt: { k: ['18.4%', '1.31', '−14.2%', '54%', '1.42×', '~9× / YR'], w: [0.21, 0.19, 0.20, 0.11, 0.14, 0.15], d: 0.0016 },
  partner: { k: ['12.6%', '1.22', '−9.8%', '53%', '1.38×', '~6× / YR'], w: [0.20, 0.18, 0.19, 0.13, 0.16, 0.14], d: 0.0012 },
  daytrader: { k: ['22.1%', '1.78', '−8.6%', '51%', '1.21×', '~140× / YR'], w: [0.16, 0.24, 0.25, 0.15, 0.11, 0.09], d: 0.0019 },
  multiasset: { k: ['11.3%', '1.05', '−12.8%', '55%', '1.31×', '~12× / YR'], w: [0.17, 0.20, 0.19, 0.15, 0.09, 0.20], d: 0.0011 },
};

const ABD_FOLDS = [
  { fold: '1', train: '2023-01 → 2024-03', test: '2024-04 → 2024-09', sharpe: '1.18', hit: '54%', dd: '−5.9%' },
  { fold: '2', train: '2023-07 → 2024-09', test: '2024-10 → 2025-03', sharpe: '1.02', hit: '52%', dd: '−7.1%' },
  { fold: '3', train: '2024-01 → 2025-03', test: '2025-04 → 2025-09', sharpe: '1.35', hit: '55%', dd: '−4.8%' },
  { fold: '4', train: '2024-07 → 2025-09', test: '2025-10 → 2026-03', sharpe: '0.91', hit: '52%', dd: '−8.3%' },
];

function agentBacktestHTML(page) {
  const bt = S.backtests[page.key];
  const real = !!(bt && bt.available && bt.kind === 'fx');
  const abd = real ? null : ABD[page.account] || null;
  const m = real ? (bt.metrics || {}) : null;
  const fp = (v, dp = 1) => v == null ? '—' : num(v * 100, dp) + '%';

  const kVals = real ? [
    fp(m.cagr), m.sharpe == null ? '—' : num(m.sharpe, 2),
    m.max_drawdown == null ? '—' : sgnPct(m.max_drawdown, 1),
    fp(m.hit_rate, 0), m.payoff == null ? '—' : num(m.payoff, 2) + '×',
    m.turnover == null ? '—' : '~' + num(m.turnover, 0) + '× / YR',
  ] : abd ? abd.k : ['—', '—', '—', '—', '—', '—'];
  const period = real ? `${String(bt.start || '').slice(0, 7)} → ${String(bt.end || '').slice(0, 7)}` : 'NET · 2023 → 2026';
  const abKpis = [
    kpiCell('CAGR', kVals[0], kVals[0] === '—' ? DIM : G, period),
    kpiCell('SHARPE', kVals[1], kVals[1] === '—' ? DIM : PALE, page.bar === '60m' ? '60M BARS' : 'DAILY BARS'),
    kpiCell('MAX DRAWDOWN', kVals[2], kVals[2] === '—' ? DIM : R, 'PEAK TO TROUGH'),
    kpiCell('HIT RATE', kVals[3], kVals[3] === '—' ? DIM : PALE, 'PROFITABLE BARS'),
    kpiCell('AVG WIN / LOSS', kVals[4], kVals[4] === '—' ? DIM : PALE, 'PAYOFF RATIO'),
    kpiCell('TURNOVER', kVals[5], kVals[5] === '—' ? DIM : AMB, 'COSTS + CARRY MODELLED', true),
  ].join('');

  /* curves: real cache > seeded illustrative > nothing */
  let abPts = '', abBench = '', xLabels = ['2023', '2024', '2025', '2026'];
  if (real && Array.isArray(bt.curve) && bt.curve.length > 1) {
    const sv = bt.curve.map(r => r[1]);
    const bv = (bt.benchmark || []).map(r => r[1]);
    const lo = Math.min(...sv, ...(bv.length ? bv : sv)), hi = Math.max(...sv, ...(bv.length ? bv : sv));
    const Y = v => 12 + (1 - (Math.log(v) - Math.log(lo)) / ((Math.log(hi) - Math.log(lo)) || 1)) * 196;
    abPts = sv.map((v, i) => ((i / (sv.length - 1)) * 1200).toFixed(1) + ',' + Y(v).toFixed(1)).join(' ');
    abBench = bv.length > 1 ? bv.map((v, i) => ((i / (bv.length - 1)) * 1200).toFixed(1) + ',' + Y(v).toFixed(1)).join(' ') : '';
    const years = [...new Set(bt.curve.map(r => String(r[0]).slice(0, 4)))];
    if (years.length > 1) xLabels = years;
  } else if (abd) {
    const arnd = mix32(page.key.charCodeAt(0) * 7 + 13);
    const abN = 160;
    const abS = [1], abB = [1];
    for (let i = 1; i <= abN; i++) {
      abS.push(abS[i - 1] * (1 + abd.d * 4 + (arnd() - 0.5) * 0.05));
      abB.push(abB[i - 1] * (1 + 0.0018 + (arnd() - 0.5) * 0.045));
    }
    const abLo = Math.min(...abS, ...abB), abHi = Math.max(...abS, ...abB);
    const abY = v => 12 + (1 - (Math.log(v) - Math.log(abLo)) / ((Math.log(abHi) - Math.log(abLo)) || 1)) * 196;
    abPts = abS.map((v, i) => ((i / abN) * 1200).toFixed(1) + ',' + abY(v).toFixed(1)).join(' ');
    abBench = abB.map((v, i) => ((i / abN) * 1200).toFixed(1) + ',' + abY(v).toFixed(1)).join(' ');
  }

  const wSrc = real && bt.weights ? AGENT_NAMES.map(n => +bt.weights[n.toLowerCase()] || 0)
    : abd ? abd.w : AGENT_NAMES.map(() => 0);
  const wMax = Math.max(...wSrc, 0.25);
  const weights = AGENT_NAMES.map((name, i) => `
    <div style="display:flex;align-items:center;gap:9px;padding:4px 0;font-size:10px">
      <span style="color:#c9e8cc;width:70px">${name}</span>
      <span style="flex:1;height:5px;background:#1a1a1a;display:inline-block"><span style="display:block;height:5px;width:${(wSrc[i] / wMax * 100).toFixed(0)}%;background:#7ee787"></span></span>
      <span style="color:#61805f;width:34px;text-align:right">${wSrc[i] ? (wSrc[i] * 100).toFixed(0) + '%' : '—'}</span>
    </div>`).join('');

  const foldRows = (real && Array.isArray(bt.folds) ? bt.folds.map(f => ({
    fold: f.fold, train: f.train, test: f.test,
    sharpe: f.sharpe == null ? '—' : num(+f.sharpe, 2),
    hit: f.hit_rate == null ? (f.hit || '—') : num(+f.hit_rate * 100, 0) + '%',
    dd: f.max_drawdown == null ? (f.dd || '—') : sgnPct(+f.max_drawdown, 1),
  })) : abd ? ABD_FOLDS : []).map(f => `<div style="display:grid;grid-template-columns:.5fr 1.2fr 1.2fr .8fr .6fr .8fr;padding:7px 18px;font-size:11px;border-bottom:1px solid #121212"><span style="color:#eaffec">${esc(f.fold)}</span><span style="color:#61805f">${esc(f.train)}</span><span style="color:#9db5a0">${esc(f.test)}</span><span style="color:#7ee787">${esc(f.sharpe)}</span><span style="color:#c9e8cc">${esc(f.hit)}</span><span style="color:#ff7b72">${esc(f.dd)}</span></div>`).join('')
    || '<div style="padding:14px 18px;font-size:10.5px;color:#61805f">— NO WALK-FORWARD FOLDS RECORDED.</div>';

  const banner = real
    ? `<span style="font-weight:600">WALK-FORWARD VALIDATION · CACHED RESULTS</span><span style="color:#8a7433">state/fx_backtest_${esc(page.account)}.json${bt.generated_at ? ' · ' + esc(String(bt.generated_at).slice(0, 10)) : ''}</span>`
    : bt && bt._error
      ? `<span style="font-weight:600">⚠ BACKTEST ENDPOINT UNREACHABLE</span><span style="color:#8a7433">WILL RETRY WHEN YOU REVISIT THIS TAB.</span>`
      : abd
        ? `<span style="font-weight:600">⚠ WALK-FORWARD VALIDATION · ILLUSTRATIVE NUMBERS</span><span style="color:#8a7433">POPULATE state/fx_backtest_${esc(page.account)}.json (python -m trading_algo.forex.run_backtest / walkforward) — THE LAYOUT IS WIRED, THE FIGURES ARE PLACEHOLDERS.</span>`
        : `<span style="font-weight:600">⚠ NO BACKTEST DATA FOR THIS BOOK</span><span style="color:#8a7433">POPULATE state/fx_backtest_${esc(page.account)}.json (python -m trading_algo.forex.run_backtest / walkforward) TO WIRE THIS TAB.</span>`;

  return `
  <div data-screen="agent-backtest">
    <div style="display:flex;align-items:center;gap:12px;padding:8px 18px;background:rgba(227,179,65,.06);border-bottom:1px solid #3d3418;font-size:10px;color:#e3b341">
      ${banner}
      <span style="margin-left:auto;color:#8a7433">COSTS + CARRY MODELLED · NO LOOKAHEAD</span>
    </div>
    <div style="display:grid;grid-template-columns:repeat(6,1fr);border-bottom:1px solid #262626">${abKpis}</div>
    <div style="display:grid;grid-template-columns:2.1fr 1fr;border-bottom:1px solid #262626">
      <div style="padding:14px 18px;border-right:1px solid #262626">
        <div style="display:flex;gap:18px;font-size:9px;letter-spacing:.12em;margin-bottom:10px"><span style="color:#eaffec">■ GROWTH OF A$10,000 · NET OF COSTS + CARRY${real ? '' : ' · ILLUSTRATIVE'}</span><span style="color:#7ee787">— ENSEMBLE</span><span style="color:#61805f">— BUY &amp; HOLD BASKET</span></div>
        <svg viewBox="0 0 1200 220" preserveAspectRatio="none" style="width:100%;height:230px;display:block">
          <line x1="0" y1="55" x2="1200" y2="55" stroke="#1a1a1a" stroke-width="1"></line>
          <line x1="0" y1="110" x2="1200" y2="110" stroke="#1a1a1a" stroke-width="1"></line>
          <line x1="0" y1="165" x2="1200" y2="165" stroke="#1a1a1a" stroke-width="1"></line>
          ${abPts ? `<polygon points="0,220 ${abPts} 1200,220" fill="rgba(126,231,135,0.06)"></polygon>` : ''}
          <polyline points="${abBench}" fill="none" stroke="#61805f" stroke-width="1.3"></polyline>
          <polyline points="${abPts}" fill="none" stroke="#7ee787" stroke-width="1.7" stroke-linejoin="round"></polyline>
        </svg>
        <div style="display:flex;justify-content:space-between;font-size:9px;color:#3d543f;margin-top:5px">${xLabels.map(y => `<span>${esc(y)}</span>`).join('')}</div>
      </div>
      <div>
        <div style="padding:10px 18px;border-bottom:1px solid #1a1a1a;font-size:9px;color:#eaffec;letter-spacing:.14em">■ AGENT PERFORMANCE WEIGHTS</div>
        <div style="padding:10px 18px 6px">${weights}</div>
        <div style="padding:4px 18px 14px;font-size:10px;color:#61805f;line-height:1.7">THE BLEND IS PERFORMANCE-WEIGHTED (AGENTS.PY): AGENTS THAT HAVE BEEN RIGHT RECENTLY GET A LOUDER VOTE, LOSERS GET TURNED DOWN — RE-SCORED EVERY BAR.</div>
      </div>
    </div>
    <div style="display:grid;grid-template-columns:.5fr 1.2fr 1.2fr .8fr .6fr .8fr;padding:7px 18px;font-size:9px;color:#61805f;letter-spacing:.12em;border-bottom:1px solid #1a1a1a"><span>FOLD</span><span>TRAIN WINDOW</span><span>TEST WINDOW</span><span>TEST SHARPE</span><span>HIT</span><span>MAX DD</span></div>
    ${foldRows}
    <div style="padding:12px 18px;font-size:10px;color:#61805f;line-height:1.7">WALK-FORWARD: TRAIN ON A WINDOW, TEST ON THE UNSEEN NEXT SLICE, ROLL FORWARD. IF THE EDGE ONLY EXISTS IN-SAMPLE, IT DIES HERE — THE FOLDS ABOVE ARE THE HONEST TEST.</div>
  </div>`;
}

/* ====================== AGENT · METHOD ================================= */
function agentMethodHTML(page) {
  const fp = (S.meta && S.meta.fx_profiles && S.meta.fx_profiles[page.account]) || {};
  const cap = pct0(fp.per_pair_cap || page.per_pair_cap || 0.25);
  const brk = num((fp.max_drawdown_stop || page.breaker || 0.2) * 100, 0);
  const books = accounts().filter(a => a.kind === 'fx').map(a => a.account.toUpperCase()).join(', ');
  const amAgents = [
    { name: 'TREND', f: 'EMA20 vs EMA100 · ADX GATE', d: 'Is there a persistent up/down trend? Votes with it, scaled by trend strength.' },
    { name: 'BREAKOUT', f: 'DONCHIAN CHANNEL BREAKS', d: 'Did price punch through the recent high/low band? Fires a full ±1 on a break.' },
    { name: 'MOMENTUM', f: '60-DAY RATE OF CHANGE', d: 'Is price higher than 60 days ago? Winners tend to keep winning.' },
    { name: 'MEANREV', f: 'BOLLINGER Z-SCORE', d: 'In quiet ranges, fades stretches beyond ±2σ back toward the average.' },
    { name: 'CARRY', f: 'RATE DIFFERENTIAL', d: 'Earns the interest-rate gap by holding the higher-yielding currency.' },
    { name: 'NEURAL', f: 'MLP ON INDICATOR FEATURES', d: 'A small neural net trained on the same indicators; votes −1…+1 (nn.py).' },
  ].map(a => `
    <div style="border:1px solid #262626;background:#0d0d0d;padding:12px 14px;display:flex;flex-direction:column;gap:8px">
      <div style="font-size:11px;color:#eaffec;font-weight:600;letter-spacing:.04em">${a.name}</div>
      <div style="font-size:10px;color:#7ee787;background:#121212;border:1px solid #1a1a1a;padding:6px 8px;line-height:1.5">${a.f}</div>
      <div style="font-size:9.5px;color:#61805f;line-height:1.6">${a.d}</div>
    </div>`).join('');

  return `
  <div data-screen="agent-method">
    <div style="display:grid;grid-template-columns:1fr 1fr;border-bottom:1px solid #262626">
      <div style="padding:18px;border-right:1px solid #262626">
        <div style="font-size:9px;color:#eaffec;letter-spacing:.14em;margin-bottom:6px">■ THE IDEA IN ONE PARAGRAPH</div>
        <p style="font-size:12px;line-height:1.9;color:#9db5a0;margin:0">EVERY BAR, SIX INDEPENDENT <span style="color:#7ee787">TECHNICAL AGENTS</span> EACH VOTE −1…+1 ON EVERY PAIR. A <span style="color:#7ee787">PERFORMANCE-WEIGHTED ENSEMBLE</span> BLENDS THE VOTES — AGENTS THAT HAVE BEEN RIGHT LATELY COUNT FOR MORE. A REGIME GATE PICKS WHICH AGENTS APPLY (TREND-FOLLOWERS IN TRENDS, MEAN-REVERSION IN RANGES), THEN A <span style="color:#7ee787">VOL-TARGETING RISK LAYER</span> SIZES EACH POSITION AND CAPS IT AT ${cap} OF EQUITY.</p>
      </div>
      <div style="padding:18px">
        <div style="font-size:9px;color:#eaffec;letter-spacing:.14em;margin-bottom:6px">■ EVERY BAR, IN ORDER</div>
        <div style="display:flex;align-items:stretch;gap:8px;margin-top:10px">
          <div style="flex:1;border:1px solid #2a4a2c;padding:10px 12px"><div style="font-size:10px;color:#7ee787;font-weight:600">1 · VOTE</div><div style="font-size:10px;color:#61805f;margin-top:4px;line-height:1.6">6 AGENTS SCORE EVERY PAIR −1…+1</div></div>
          <div style="display:grid;place-items:center;color:#3d543f">→</div>
          <div style="flex:1;border:1px solid #2a4a2c;padding:10px 12px"><div style="font-size:10px;color:#7ee787;font-weight:600">2 · BLEND</div><div style="font-size:10px;color:#61805f;margin-top:4px;line-height:1.6">PERFORMANCE-WEIGHTED NET TILT PER PAIR</div></div>
          <div style="display:grid;place-items:center;color:#3d543f">→</div>
          <div style="flex:1;border:1px solid #2a4a2c;padding:10px 12px"><div style="font-size:10px;color:#7ee787;font-weight:600">3 · SIZE</div><div style="font-size:10px;color:#61805f;margin-top:4px;line-height:1.6">VOL-TARGET × TILT, CAP ${cap}/PAIR</div></div>
          <div style="display:grid;place-items:center;color:#3d543f">→</div>
          <div style="flex:1;border:1px solid #2a4a2c;padding:10px 12px"><div style="font-size:10px;color:#7ee787;font-weight:600">4 · TRADE</div><div style="font-size:10px;color:#61805f;margin-top:4px;line-height:1.6">DELTA ONLY, WITH SPREAD COSTS + CARRY</div></div>
        </div>
      </div>
    </div>
    <div style="padding:14px 18px;border-bottom:1px solid #262626">
      <div style="font-size:9px;color:#eaffec;letter-spacing:.14em;margin-bottom:12px">■ THE SIX AGENTS · FOREX/AGENTS.PY</div>
      <div style="display:grid;grid-template-columns:repeat(6,1fr);gap:10px">${amAgents}</div>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr">
      <div style="border-right:1px solid #262626">
        <div style="padding:10px 18px;border-bottom:1px solid #1a1a1a;font-size:9px;color:#eaffec;letter-spacing:.14em">■ REGIME GATE</div>
        <div style="padding:12px 18px;font-size:10px;color:#61805f;line-height:1.8">ADX DECIDES THE STATE: <span style="color:#c9e8cc">TRENDING</span> → TREND, BREAKOUT &amp; MOMENTUM AGENTS VOTE; <span style="color:#e3b341">RANGING</span> → MEAN-REVERSION TAKES OVER AND TREND SITS OUT. CARRY AND THE NEURAL AGENT VOTE IN BOTH.</div>
      </div>
      <div style="border-right:1px solid #262626">
        <div style="padding:10px 18px;border-bottom:1px solid #1a1a1a;font-size:9px;color:#eaffec;letter-spacing:.14em">■ RISK LAYER</div>
        <div style="padding:12px 18px;font-size:10px;color:#61805f;line-height:1.8">EACH POSITION IS SIZED SO ITS <span style="color:#c9e8cc">RISK</span> (NOT ITS DOLLARS) IS CONSTANT — WILD PAIRS GET LESS CAPITAL. PER-PAIR CAP <span style="color:#c9e8cc">${cap}</span>, ACCOUNT BREAKER <span style="color:#ff7b72">−${brk}%</span> FROM PEAK → FLAT + COOLDOWN.</div>
      </div>
      <div>
        <div style="padding:10px 18px;border-bottom:1px solid #1a1a1a;font-size:9px;color:#eaffec;letter-spacing:.14em">■ ISOLATED BOOKS</div>
        <div style="padding:12px 18px;font-size:10px;color:#61805f;line-height:1.8">${esc(books || 'EACH ACCOUNT')} ARE SEPARATE PAPER BOOKS WITH THEIR OWN STATE, PROFILE (BALANCED / CONSERVATIVE / INTRADAY) AND UNIVERSE — SAME ENGINE, DIFFERENT KNOBS.</div>
      </div>
    </div>
  </div>`;
}

/* ====================== SMALL (micro) screens ========================== */
function smallOverviewHTML(page) {
  const k = page.kpis;
  const [eqInt, eqDec] = moneySplit(k.total_equity);
  const M = prepEquity(page);
  const sleeve = (page.sleeves || [])[0] || {};
  const sym = SYM[sleeve.currency] || '$';
  const vals = M.curve.map(p => p.v);
  const dates = M.curve.map(p => p.date);
  const p140 = toPts(vals, 600, 140, 10);
  const down = k.total_return < 0;
  const stroke = down ? R : G;
  const closed = page.closed || { rows: [] };
  const lastClosed = closed.rows[closed.rows.length - 1];
  const lastTrade = (page.blotter || [])[Math.max((page.blotter || []).length - 1, 0)];
  const feePct = lastClosed ? lastClosed.costs / (lastClosed.entry * lastClosed.qty) : null;
  const allCash = k.cash_pct >= 0.999;
  const gate = page.min_viable || 500;
  const above = k.total_equity >= gate;
  const lastName = lastClosed ? lastClosed.ticker : (lastTrade ? lastTrade.ticker : '—');
  const lastQty = lastClosed ? lastClosed.qty : (lastTrade ? lastTrade.shares : '');
  const axis = axisDates(dates, 4);

  return `
  <div data-screen="small-overview">
    <div style="display:grid;grid-template-columns:1.5fr 1fr 1fr 1fr 1fr 1fr 1fr;border-bottom:1px solid #262626">
      <div style="padding:14px 18px;border-right:1px solid #262626;background:#0d0d0d"><div style="font-size:9px;color:#61805f;letter-spacing:.14em">TOTAL EQUITY · ${esc(page.base_currency)}</div><div style="font-size:26px;font-weight:600;color:#eaffec;margin-top:5px">${eqInt}<span style="font-size:15px;color:#61805f">${eqDec}</span></div><div style="font-size:9px;color:#3d543f;margin-top:4px">INITIAL ${num(k.initial_capital, 2)} · ${(page.sleeves || []).map(s => s.key).join(' / ')} SLEEVE ONLY</div></div>
      <div style="padding:14px 16px;border-right:1px solid #262626"><div style="font-size:9px;color:#61805f;letter-spacing:.14em">TOTAL RETURN</div><div style="font-size:20px;font-weight:600;color:${cSign(k.total_return)};margin-top:8px">${sgnPct(k.total_return, 2)}</div><div style="font-size:9px;color:#3d543f;margin-top:4px">SINCE ${esc(M.curve.length ? M.curve[0].date : '')}</div></div>
      <div style="padding:14px 16px;border-right:1px solid #262626"><div style="font-size:9px;color:#61805f;letter-spacing:.14em">DAY CHANGE</div><div style="font-size:20px;font-weight:600;color:${cSign(k.day_change)};margin-top:8px">${sgnPct(k.day_change, 2)}</div><div style="font-size:9px;color:#3d543f;margin-top:4px">${allCash ? 'FX-DRIVEN · 100% CASH' : pct0(1 - k.cash_pct) + ' INVESTED'}</div></div>
      <div style="padding:14px 16px;border-right:1px solid #262626"><div style="font-size:9px;color:#61805f;letter-spacing:.14em">OFF PEAK</div><div style="font-size:20px;font-weight:600;color:${page.off_peak < -0.01 ? AMB : PALE};margin-top:8px">${sgnPct(page.off_peak, 2)}</div><div style="font-size:9px;color:#3d543f;margin-top:4px">PEAK A$${num(page.peak_equity, 2)}</div></div>
      <div style="padding:14px 16px;border-right:1px solid #262626"><div style="font-size:9px;color:#61805f;letter-spacing:.14em">POSITIONS</div><div style="font-size:20px;font-weight:600;color:#eaffec;margin-top:8px">${k.n_positions}</div><div style="font-size:9px;color:#3d543f;margin-top:4px">CASH ${sym}${num(sleeve.cash_local || 0, 2)} ${esc(sleeve.currency || '')}</div></div>
      <div style="padding:14px 16px;border-right:1px solid #262626"><div style="font-size:9px;color:#61805f;letter-spacing:.14em">FEES PAID</div><div style="font-size:20px;font-weight:600;color:#ff7b72;margin-top:8px">${(k.fees || []).map(f => (SYM[f.currency] || f.currency) + num(f.amount, 2)).join(' · ') || 'A$0'}</div><div style="font-size:9px;color:#3d543f;margin-top:4px">${feePct != null ? `= ${num(feePct * 100, 1)}% OF THE ONLY POSITION` : 'COMMISSION FLOORS DOMINATE'}</div></div>
      <div style="padding:14px 16px"><div style="font-size:9px;color:#61805f;letter-spacing:.14em">VIABILITY GATE</div><div style="font-size:20px;font-weight:600;color:${above ? G : R};margin-top:8px">${above ? 'ABOVE' : 'BELOW'}</div><div style="font-size:9px;color:#3d543f;margin-top:4px">HOLDS CASH BELOW A$${num(gate, 0)}</div></div>
    </div>
    <div style="display:grid;grid-template-columns:2.1fr 1fr">
      <div style="padding:12px 18px;border-right:1px solid #262626">
        <div style="display:flex;gap:14px;font-size:9px;letter-spacing:.12em;margin-bottom:8px"><span style="color:#eaffec">■ EQUITY CURVE · ${esc(page.base_currency)}</span><span style="color:#61805f">${vals.length ? `MIN ${num(Math.min(...vals), 2)} · MAX ${num(Math.max(...vals), 2)}` : '—'}</span></div>
        <svg viewBox="0 0 600 140" preserveAspectRatio="none" style="width:100%;height:150px;display:block">
          <line x1="0" y1="35" x2="600" y2="35" stroke="#1a1a1a" stroke-width="1"></line>
          <line x1="0" y1="70" x2="600" y2="70" stroke="#1a1a1a" stroke-width="1"></line>
          <line x1="0" y1="105" x2="600" y2="105" stroke="#1a1a1a" stroke-width="1"></line>
          <polygon points="${vals.length ? '0,140 ' + p140.join(' ') + ' 600,140' : ''}" fill="${down ? 'rgba(255,123,114,0.08)' : 'rgba(126,231,135,0.08)'}"></polygon>
          <polyline points="${p140.join(' ')}" fill="none" stroke="${stroke}" stroke-width="1.6" stroke-linejoin="round"></polyline>
        </svg>
        <div style="display:flex;justify-content:space-between;font-size:9px;color:#3d543f;margin-top:5px">${axis.map(d => `<span>${d}</span>`).join('')}</div>
      </div>
      <div>
        <div style="padding:10px 18px;border-bottom:1px solid #1a1a1a;font-size:9px;color:#eaffec;letter-spacing:.14em">■ MICRO-ACCOUNT MODE — THE LESSON THIS BOOK TEACHES</div>
        <div style="padding:12px 18px;border-bottom:1px solid #121212"><div style="font-size:11px;color:#eaffec">WHOLE SHARES ONLY</div><div style="font-size:10px;color:#61805f;line-height:1.7;margin-top:4px">A SMALL BOOK CAN'T HOLD A ${(S.meta ? S.meta.params.top_n : 10)}-NAME BOOK IN WHOLE SHARES — SO IT CONCENTRATES INTO A SINGLE NAME. LAST BOOK: ${lastQty} × ${esc(lastName)}.</div></div>
        <div style="padding:12px 18px;border-bottom:1px solid #121212"><div style="display:flex;justify-content:space-between;font-size:11px"><span style="color:#eaffec">FEE DRAG</span><span style="color:#ff7b72">${feePct != null ? num(feePct * 100, 1) + '% ROUND-TRIP' : 'FLOORS BITE'}</span></div><div style="font-size:10px;color:#61805f;line-height:1.7;margin-top:4px">THE $1 COMMISSION FLOOR IS TRIVIAL ON A $10K TRADE BUT EATS A REAL SLICE OF A TINY POSITION. SMALL ACCOUNTS BLEED THROUGH FLOORS.</div></div>
        <div style="padding:12px 18px"><div style="display:flex;justify-content:space-between;font-size:11px"><span style="color:#eaffec">MIN-VIABLE GATE</span><span style="color:#7ee787">A$${num(gate, 0)}</span></div><div style="font-size:10px;color:#61805f;line-height:1.7;margin-top:4px">BELOW A$${num(gate, 0)} THE SLEEVE STOPS TRADING AND HOLDS CASH RATHER THAN FEEDING THE COMMISSION FLOOR (CONFIG.PY · MIN_VIABLE_EQUITY_BASE).</div></div>
      </div>
    </div>
  </div>`;
}

function smallTapeHTML(page) {
  const k = page.kpis;
  const sleeve = (page.sleeves || [])[0] || {};
  const sym = SYM[sleeve.currency] || '$';
  const lastTrade = (page.blotter || []).slice(-1)[0];
  const gate = page.min_viable || 500;
  const above = k.total_equity >= gate;
  const items = [
    { k: 'SLEEVE', v: `${(page.sleeves || []).map(s => s.key).join(' / ')} ONLY · 100%`, c: TXT },
    { k: 'CASH', v: `${sym}${num(sleeve.cash_local || 0, 2)} ${sleeve.currency || ''}`, c: TXT },
  ];
  if (lastTrade) items.push({ k: 'LAST FILL', v: `${lastTrade.side} ${lastTrade.ticker} ${lastTrade.shares} @ ${pxFill(sym, lastTrade.fill)} · ${mmdd(lastTrade.date)}`, c: lastTrade.side === 'BUY' ? G : R });
  if (sleeve.fx_rate) items.push({ k: `${sleeve.currency}/AUD`, v: num(sleeve.fx_rate, 4), c: TXT });
  items.push({ k: 'VIABILITY GATE', v: above ? `ABOVE A$${num(gate, 0)} — TRADING` : `BELOW A$${num(gate, 0)} — HOLDING CASH`, c: above ? G : R });
  items.push({ k: 'MODE', v: 'MICRO — CONCENTRATES INTO 1 NAME', c: AMB });
  const right = `PEAK <span style="color:#c9e8cc">A$${num(page.peak_equity, 2)}</span> · NEXT REBAL <span style="color:#c9e8cc">${esc(page.next_rebalance || '')}</span>`;
  return acctTapeHTML(items, right);
}

function smallPositionsHTML(page) {
  const M = prepEquity(page);
  const sleeve = (page.sleeves || [])[0] || {};
  const sym = SYM[sleeve.currency] || '$';
  const closed = page.closed || { rows: [], wins: 0, count: 0, net_base: 0 };
  const lastClosed = closed.rows[closed.rows.length - 1];
  const rebalMonth = sleeve.last_rebalance_month || '';
  const rows = M.rows;

  const posBody = rows.length === 0
    ? `<div style="padding:22px 18px;font-size:11px;color:#61805f;border-bottom:1px solid #262626">— NO OPEN POSITIONS.${lastClosed ? ` MICRO-ACCOUNT MODE CONCENTRATED THE BOOK INTO A SINGLE NAME (${esc(lastClosed.ticker)}); THE ${esc(mdy(lastClosed.date).split(' ')[0])} REBALANCE CLOSED IT AND LEFT THE SLEEVE IN CASH.` : ''}</div>`
    : `<div style="display:grid;grid-template-columns:1fr .55fr .8fr .8fr .85fr .9fr .9fr .65fr .7fr .75fr;padding:7px 18px;font-size:9px;color:#61805f;letter-spacing:.12em;border-bottom:1px solid #1a1a1a"><span>TICKER</span><span>QTY</span><span>AVG COST</span><span>PRICE</span><span>VALUE LOC</span><span>VALUE AUD</span><span>WEIGHT</span><span>DAY</span><span>UNRL %</span><span>UNRL AUD</span></div>` +
      rows.map(p => `
      <div class="hv-row" ${hovAttrs('eq', p.region + ':' + p.ticker)} style="position:relative;display:grid;grid-template-columns:1fr .55fr .8fr .8fr .85fr .9fr .9fr .65fr .7fr .75fr;padding:6px 18px;font-size:11px;border-bottom:1px solid #121212;align-items:center;cursor:crosshair">
        <span style="color:#eaffec">${esc(p.ticker)}</span><span style="color:#9db5a0">${p.shares}</span><span style="color:#e3b341">${pxFill(p.sym, p.avg_cost)}</span><span style="color:#9db5a0">${px2(p.sym, p.price)}</span><span style="color:#c9e8cc">${money0(p.sym, p.value_local)}</span><span style="color:#c9e8cc">A$${num(p.value_base, 0)}</span>
        <span style="color:#61805f;font-size:10px">${num(p.weight * 100, 1)}%</span>
        <span style="color:${cSign(p.day_change)}">${sgnPct(p.day_change, 1)}</span><span style="color:${cSign(p.unrealized_pct)}">${sgnPct(p.unrealized_pct, 1)}</span><span style="color:${cSign(p.unrealized_base)}">${sgn(p.unrealized_base, 'A$' + num(Math.abs(p.unrealized_base), 0))}</span>
      </div>`).join('');

  const blotterRows = (page.blotter || []).map(t => `
    <div style="display:grid;grid-template-columns:.7fr .6fr .5fr 1fr .55fr .8fr .9fr .7fr;padding:5px 18px;font-size:10.5px;border-bottom:1px solid #121212"><span style="color:#3d543f">${esc(t.date)}</span><span style="color:#61805f">${esc(t.region)}</span><span style="font-weight:600;color:${t.side === 'BUY' ? G : R}">${t.side}</span><span style="color:#eaffec">${esc(t.ticker)}</span><span style="color:#9db5a0">${t.shares}</span><span style="color:#9db5a0">${px2(sym, t.fill)}</span><span style="color:#c9e8cc">${money0(sym, t.value)}</span><span style="color:#61805f">${sym}${num(t.commission || 0, 2)}</span></div>`).join('');

  const closedRows = closedRowsHTML(closed, { bar: false });

  const eqTxt = sleeve.currency === page.base_currency
    ? `A$${num(sleeve.equity_local || 0, 2)}`
    : `${sym}${num(sleeve.cash_local + (sleeve.invested_local || 0), 2)} → A$${num(sleeve.equity_base || 0, 2)}`;

  return `
  <div data-screen="small-positions">
    <div style="display:flex;align-items:center;gap:16px;padding:12px 18px;background:#0d0d0d;border-bottom:1px solid #1a1a1a">
      <span style="font-size:13px;font-weight:600;color:#eaffec;letter-spacing:.08em">${esc(sleeve.key || '')} · 100% ALLOCATION</span>
      <span style="font-size:10px;color:#61805f">EQUITY <span style="color:#c9e8cc">${eqTxt}</span></span>
      <span style="font-size:10px;color:#61805f">CASH <span style="color:#c9e8cc">${num((sleeve.cash_pct || 1) * 100, 0)}%</span></span>
      <span style="margin-left:auto;font-size:10px;color:#61805f">LAST REBALANCE <span style="color:#c9e8cc">${esc(rebalMonth || '—')}</span></span>
    </div>
    ${posBody}
    <div style="padding:10px 18px;background:#0d0d0d;border-bottom:1px solid #1a1a1a;font-size:9px;color:#eaffec;letter-spacing:.14em">■ TRADE BLOTTER · ${(page.blotter || []).length} FILLS</div>
    <div style="display:grid;grid-template-columns:.7fr .6fr .5fr 1fr .55fr .8fr .9fr .7fr;padding:7px 18px;font-size:9px;color:#61805f;letter-spacing:.12em;border-bottom:1px solid #1a1a1a"><span>DATE</span><span>REGION</span><span>SIDE</span><span>TICKER</span><span>QTY</span><span>FILL</span><span>VALUE</span><span>COMM</span></div>
    ${blotterRows}
    <div style="display:flex;align-items:center;gap:18px;padding:12px 18px;background:#0d0d0d;border-top:1px solid #262626;border-bottom:1px solid #1a1a1a">
      <span style="font-size:9px;color:#eaffec;letter-spacing:.14em">■ CLOSED TRADES · REALIZED P&amp;L</span>
      <span style="font-size:10px;color:#61805f">NET <span style="color:${cSign(closed.net_base)}">${lastClosed ? `${sgn(lastClosed.net, sym + num(Math.abs(lastClosed.net), 2))} → ` : ''}${sgn(closed.net_base, 'A$' + num(Math.abs(closed.net_base), 2))}</span></span>
      <span style="font-size:10px;color:#61805f">WIN RATE <span style="color:#c9e8cc">${closed.wins} / ${closed.count}</span></span>
      <span style="margin-left:auto;font-size:9px;color:#3d543f">FEE DRAG ON A MICRO BOOK, ITEMISED — THE LESSON THIS ACCOUNT TEACHES</span>
    </div>
    <div style="display:grid;grid-template-columns:.65fr .9fr .55fr .45fr 1.15fr .5fr .75fr .7fr .85fr .8fr .65fr;padding:7px 18px;font-size:9px;color:#61805f;letter-spacing:.12em;border-bottom:1px solid #1a1a1a"><span>CLOSED</span><span>TICKER</span><span>REGION</span><span>QTY</span><span>ENTRY → EXIT</span><span>HELD</span><span>GROSS</span><span>COSTS</span><span>NET LOCAL</span><span>NET AUD</span><span>RETURN</span></div>
    ${closedRows || '<div style="padding:22px 18px;font-size:11px;color:#61805f">— NO CLOSED ROUND-TRIPS YET.</div>'}
  </div>`;
}

/* ============================ render root ============================== */
function contentHTML() {
  if (S.account === 'ALL') {
    return S.errors.ALL ? errPanel('ALL') : allAccountsHTML();
  }
  const page = S.pages[S.account];
  if (S.errors[S.account]) return errPanel(S.account);
  if (!page) return '<div class="boot">LOADING…</div>';
  if (page.kind === 'fx') {
    if (S.tab === 'POSITIONS') return agentPositionsHTML(page);
    if (S.tab === 'BACKTEST') return agentBacktestHTML(page);
    if (S.tab === 'METHOD') return agentMethodHTML(page);
    return agentKpisHTML(page) + agentCurveAttrHTML(page) + chartSectionHTML(page);
  }
  /* equity book — micro accounts get the SMALL screens */
  if (page.micro) {
    if (S.tab === 'POSITIONS') return smallPositionsHTML(page);
    if (S.tab === 'BACKTEST') return backtestHTML(page);
    if (S.tab === 'METHOD') return methodHTML();
    return smallOverviewHTML(page);
  }
  if (S.tab === 'POSITIONS') return equityPositionsHTML(page);
  if (S.tab === 'BACKTEST') return backtestHTML(page);
  if (S.tab === 'METHOD') return methodHTML();
  return equityOverviewHTML(page);
}

function tapeHTML() {
  if (S.account === 'ALL') return '';
  const page = S.pages[S.account];
  if (!page) return '';
  if (page.kind === 'fx') return agentTapeHTML(page);
  if (page.micro) return smallTapeHTML(page);
  return equityTapeHTML(page);
}

function render() {
  _chartCtx = null;
  const page = S.account === 'ALL' ? S.overview : S.pages[S.account];
  const app = document.getElementById('app');
  app.innerHTML = `
  <div style="min-height:100vh;background:#060606;color:#c9e8cc;display:flex;flex-direction:column">
    ${headerHTML(page)}
    ${tapeHTML()}
    <div style="flex:1">${contentHTML()}</div>
    ${statusBarHTML(page)}
  </div>`;
}

/* ============================== events ================================= */
async function setAccount(key) {
  S.account = key;
  S.tfOpen = false;
  S.candleIdx = null;
  if (S.account === 'ALL' && S.tab === 'BACKTEST') S.tab = 'OVERVIEW';
  render();                      // paint immediately (loading state)
  await ensurePage(key);
  if (key !== 'ALL' && S.tab === 'BACKTEST') await ensureBacktest(key);
  render();
}

document.addEventListener('click', async e => {
  const el = e.target.closest('[data-act]');
  if (!el) {
    if (S.tfOpen) { S.tfOpen = false; render(); }
    return;
  }
  const act = el.dataset.act, arg = el.dataset.arg;
  if (act === 'acct') { await setAccount(arg); return; }
  if (act === 'tab') {
    S.tab = arg; S.tfOpen = false; S.candleIdx = null;
    render();
    if (arg === 'BACKTEST' && S.account !== 'ALL') { await ensureBacktest(S.account); render(); }
    return;
  }
  if (act === 'range') { S.range = arg; render(); return; }
  if (act === 'tf-toggle') { S.tfOpen = !S.tfOpen; render(); return; }
  if (act === 'tf') { S.tf[S.account] = arg; S.tfOpen = false; S.candleIdx = null; render(); return; }
  if (act === 'pair') { S.selPair[S.account] = arg; S.candleIdx = null; render(); return; }
  if (act === 'ta') { S.ta[arg] = !S.ta[arg]; render(); return; }
  if (act === 'pane') { S.taPanes = { ...S.taPanes, [arg]: !S.taPanes[arg] }; render(); return; }
  if (act === 'zoom') { stepZoom(arg); return; }
});

/* row popovers: attach/detach without re-rendering (keeps scroll) */
let _hovRow = null;
document.addEventListener('mouseover', e => {
  const row = e.target.closest('[data-hov]');
  if (row === _hovRow) return;
  if (_hovRow) { const p = _hovRow.querySelector('.pop'); if (p) p.remove(); }
  _hovRow = row;
  if (!row) return;
  const page = S.pages[S.account];
  if (!page) return;
  const rect = row.getBoundingClientRect();
  let html = '';
  if (row.dataset.hovkind === 'fx') html = agentPopHTML(page, row.dataset.hov, rect);
  else {
    const [region, ticker] = row.dataset.hov.split(':');
    html = equityPopHTML(page, region, ticker, rect);
  }
  if (html) row.insertAdjacentHTML('beforeend', html);
});
document.addEventListener('mouseout', e => {
  if (!_hovRow) return;
  const to = e.relatedTarget;
  if (to && _hovRow.contains(to)) return;
  const p = _hovRow.querySelector('.pop');
  if (p) p.remove();
  _hovRow = null;
  flushPending();
});

/* TA / indicator button explainers — plain-English "what to look for + how to
   trade it" tooltips, appended inside the hovered chip. */
let _hovTip = null;
document.addEventListener('mouseover', e => {
  const chip = e.target.closest('[data-tip]');
  if (chip === _hovTip) return;
  if (_hovTip) { const t = _hovTip.querySelector('.tip'); if (t) t.remove(); }
  _hovTip = chip;
  if (!chip) return;
  const html = taTipHTML(chip.dataset.tip);
  if (html) chip.insertAdjacentHTML('beforeend', html);
});
document.addEventListener('mouseout', e => {
  if (!_hovTip) return;
  const to = e.relatedTarget;
  if (to && _hovTip.contains(to)) return;
  const t = _hovTip.querySelector('.tip');
  if (t) t.remove();
  _hovTip = null;
});

/* the pointer leaving the window fires no mousemove/mouseout — clear any
   stuck hover state so polling and rendering resume */
function clearHoverState() {
  if (S.candleIdx != null) updateCandleHover(null);
  if (_hovRow) { const p = _hovRow.querySelector('.pop'); if (p) p.remove(); _hovRow = null; }
  flushPending();
}
window.addEventListener('blur', clearHoverState);
document.documentElement.addEventListener('mouseleave', clearHoverState);

/* big-chart candle hover */
document.addEventListener('mousemove', e => {
  const zone = e.target.closest && e.target.closest('#candle-zone');
  if (zone) updateCandleHover(e);
  else if (S.candleIdx != null) { updateCandleHover(null); flushPending(); }
});

/* clock — update the span only */
setInterval(() => {
  const el = document.getElementById('clock');
  if (el) el.textContent = clockStr();
}, 1000);

/* poll the active account every 5s. Fresh data always lands in S; the
   re-render is deferred while the user is mid-hover (flushed on mouseout)
   so popovers aren't wiped from under the cursor. */
const POLL_MS = 5000;
const interacting = () => !!(_hovRow || S.tfOpen || S.candleIdx != null);
const stripTs = o => { if (!o || typeof o !== 'object') return o; const c = { ...o }; delete c.generated_at; return c; };
const samePayload = (a, b) => JSON.stringify(stripTs(a)) === JSON.stringify(stripTs(b));

function flushPending() {
  if (S.pendingRender && !interacting()) { S.pendingRender = false; render(); }
}

async function poll() {
  if (S.isExport) return;
  const key = S.account;
  if (!key) return;
  if (S.meta == null) {                     // meta failed at boot — self-heal
    try { S.meta = await loadJSON('/api/meta'); S.pendingRender = true; }
    catch (e) { /* retry next tick */ }
  }
  try {
    const fresh = await loadJSON(key === 'ALL' ? '/api/overview' : '/api/account/' + key);
    const cur = key === 'ALL' ? S.overview : S.pages[key];
    const changed = !samePayload(fresh, cur);
    if (key === 'ALL') S.overview = fresh; else S.pages[key] = fresh;
    delete S.errors[key];
    S.stale = false;
    if ((changed || S.pendingRender) && S.account === key) {
      if (interacting()) S.pendingRender = true;
      else { S.pendingRender = false; render(); }
    }
  } catch (err) {
    S.stale = true;                          // keep last-good data, flag it
    const had = key === 'ALL' ? S.overview : S.pages[key];
    if (!had && !S.errors[key]) { S.errors[key] = err.message; if (!interacting()) render(); }
  }
  updateLiveChip();
}

/* ============================== boot =================================== */
async function boot() {
  applyZoom();                               // before first paint, no flash

  try { S.meta = await loadJSON('/api/meta'); }
  catch (e) { S.meta = null; }               // poll() retries until it loads

  /* optional real OHLC dropped into static/ as candles.json */
  try {
    const r = await fetch('candles.json', { cache: 'no-store' });
    if (r.ok) { const d = await r.json(); if (d && typeof d === 'object') S.candles = d; }
  } catch (e) { /* optional */ }

  const accs = accounts();
  if (S.exportAll) {
    S.account = accs.length > 1 ? 'ALL' : (accs[0] ? accs[0].key : 'ALL');
  } else if (S.isExport) {
    S.account = window.__EXPORT_ACCOUNT__;
  } else if (accs.some(a => a.key === 'FULL')) {
    S.account = 'FULL';
  } else if (accs.length) {
    S.account = accs[0].key;
  } else {
    S.account = 'ALL';
  }
  await setAccount(S.account);
  setInterval(poll, POLL_MS);
}

boot();
