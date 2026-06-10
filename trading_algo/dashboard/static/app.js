/* ===========================================================================
   Multi-Region Momentum — Trading Terminal frontend
   Dependency-free vanilla ES2020+. All charts hand-drawn (Canvas 2D / SVG).
   Polls GET /api/state every 5s; falls back to SAMPLE_STATE on first load
   and whenever the fetch fails (keeps last good data, flags "reconnecting…").
   =========================================================================== */
'use strict';

/* ------------------------------- Constants ------------------------------- */
const POLL_MS = 5000;

// Region accent colours — kept in sync with CSS custom properties.
const REGION_COLORS = { ASX: '#34e0d0', US: '#6ea8ff', FTSE: '#c792ff' };
const REGION_ORDER = ['ASX', 'US', 'FTSE'];

// Currency symbols; values are always *labelled* with the ccy code too.
const CCY_SYMBOL = { AUD: '$', USD: '$', GBP: '£', EUR: '€' };

/* ============================================================================
   SAMPLE_STATE — realistic, fully populated fallback so the layout is never
   blank. 3 sleeves (one RISK_OFF), ~14 positions, ~10 trades (2 FTSE w/ stamp
   duty), ~120 points of equity + sleeve curves with an upward-drift-with-dip.
   ============================================================================ */
const SAMPLE_STATE = (() => {
  // --- Build ~120 daily curve points with a believable shape -------------
  const N = 120;
  const start = new Date('2025-08-01T00:00:00Z');
  const equity_curve = [];
  const sleeve_curves = [];
  const init = 100000;

  // Three sleeves each start at 1/3 and drift differently.
  let asx = init / 3, us = init / 3, ftse = init / 3;
  // Pseudo-random but deterministic walk (seeded LCG) for repeatability.
  let seed = 1337;
  const rnd = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };

  for (let i = 0; i < N; i++) {
    const d = new Date(start.getTime() + i * 86400000);
    const date = d.toISOString().slice(0, 10);
    const t = i / (N - 1);

    // Macro drift with a mid-period dip (a soft "V" around day ~55-75).
    const dip = -0.05 * Math.exp(-Math.pow((i - 62) / 14, 2)); // gaussian dip
    const driftAsx = 0.00050 + 0.00035 * Math.sin(t * 6.2);
    const driftUs = 0.00075 + 0.00040 * Math.sin(t * 5.0 + 1.0);
    const driftFtse = 0.00020 + 0.00025 * Math.sin(t * 4.0 + 2.0);

    asx *= 1 + driftAsx + dip * 0.02 + (rnd() - 0.5) * 0.006;
    us *= 1 + driftUs + dip * 0.03 + (rnd() - 0.5) * 0.008;
    ftse *= 1 + driftFtse + dip * 0.018 + (rnd() - 0.5) * 0.005;

    const total = asx + us + ftse;
    equity_curve.push({ date, equity: round2(total) });
    sleeve_curves.push({ date, ASX: round2(asx), US: round2(us), FTSE: round2(ftse) });
  }

  const lastDate = equity_curve[equity_curve.length - 1].date;
  const totalEquity = equity_curve[equity_curve.length - 1].equity;
  const prevEquity = equity_curve[equity_curve.length - 2].equity;

  // --- Positions per sleeve ---------------------------------------------
  const fx = { AUD: 1.0, USD: 1.52, GBP: 1.92 };

  const asxPos = [
    pos('BHP.AX', 38, 44.10, fx.AUD),
    pos('CSL.AX', 4, 285.40, fx.AUD),
    pos('WES.AX', 22, 68.75, fx.AUD),
    pos('FMG.AX', 95, 21.30, fx.AUD),
    pos('GMG.AX', 30, 34.55, fx.AUD),
  ];
  const usPos = [
    pos('NVDA', 9, 138.20, fx.USD),
    pos('GILD', 11, 92.16, fx.USD),
    pos('LLY', 1, 812.40, fx.USD),
    pos('XLE', 18, 92.80, fx.USD),
    pos('AVGO', 6, 178.50, fx.USD),
  ];
  const ftsePos = [
    pos('SHEL.L', 60, 27.40, fx.GBP),
    pos('AZN.L', 12, 112.30, fx.GBP),
    pos('BP.L', 0, 40.74, fx.GBP),   // flat / exited (kept to show 0-weight handling)
    pos('RIO.L', 22, 51.10, fx.GBP),
  ].filter(p => p.shares > 0);

  const sleeves = [
    buildSleeve('ASX', 'Australia', 'AUD', 'RISK_ON', asxPos, fx.AUD, '2025-12'),
    buildSleeve('US', 'United States', 'USD', 'RISK_ON', usPos, fx.USD, '2025-12'),
    buildSleeve('FTSE', 'United Kingdom', 'GBP', 'RISK_OFF', ftsePos, fx.GBP, '2025-12'),
  ];

  // Normalise sleeve equity_base so the three sum to the curve total, and
  // compute each sleeve's actual weight of the total book.
  const baseSum = sleeves.reduce((s, x) => s + x.equity_base, 0);
  sleeves.forEach(s => { s.weight = s.equity_base / baseSum; });

  const nPositions = sleeves.reduce((n, s) => n + s.positions.length, 0);

  // --- Recent trades (newest first handled at render time) ---------------
  const recent_trades = [
    trade('2025-12-01', 'US', 'NVDA', 'BUY', 9, 138.20, 1.0, 0, 'USD'),
    trade('2025-12-01', 'US', 'INTC', 'SELL', 40, 24.10, 1.0, 0, 'USD'),
    trade('2025-12-01', 'ASX', 'BHP.AX', 'BUY', 38, 44.10, 6.0, 0, 'AUD'),
    trade('2025-12-01', 'ASX', 'WBC.AX', 'SELL', 50, 31.40, 6.0, 0, 'AUD'),
    trade('2025-12-01', 'FTSE', 'SHEL.L', 'BUY', 60, 27.40, 1.0, 8.22, 'GBP'),
    trade('2025-12-01', 'FTSE', 'BP.L', 'SELL', 37, 40.74, 1.0, 0, 'GBP'),
    trade('2025-11-03', 'FTSE', 'AZN.L', 'BUY', 12, 110.10, 1.0, 6.61, 'GBP'),
    trade('2025-11-03', 'US', 'GILD', 'BUY', 11, 90.40, 1.0, 0, 'USD'),
    trade('2025-11-03', 'ASX', 'FMG.AX', 'BUY', 95, 20.85, 6.0, 0, 'AUD'),
    trade('2025-11-03', 'ASX', 'CSL.AX', 'BUY', 4, 280.00, 6.0, 0, 'AUD'),
  ];

  return {
    account: 'full',
    base_currency: 'AUD',
    as_of: lastDate,
    generated_at: new Date().toISOString(),
    synthetic: true,
    kpis: {
      total_equity: totalEquity,
      initial_capital: init,
      total_return: totalEquity / init - 1,
      day_change: totalEquity / prevEquity - 1,
      n_trades: recent_trades.length,
      n_positions: nPositions,
      cash_pct: sleeves.reduce((c, s) => c + s.cash_local * fx[s.currency], 0) / baseSum,
      fees: [
        { currency: 'AUD', amount: 24.0 },
        { currency: 'USD', amount: 3.0 },
        { currency: 'GBP', amount: 14.83 },
      ],
    },
    allocations: { ASX: 0.3333, US: 0.3333, FTSE: 0.3333 },
    fx,
    equity_curve,
    sleeve_curves,
    sleeves,
    recent_trades,
  };

  // ---- local helpers for sample construction ----
  function pos(ticker, shares, price, fxRate) {
    const value_local = round2(shares * price);
    return { ticker, shares, price, value_local, value_base: round2(value_local * fxRate), weight: 0 };
  }
  function buildSleeve(key, name, ccy, regime, positions, fxRate, month) {
    const invested_local = round2(positions.reduce((s, p) => s + p.value_local, 0));
    // RISK_OFF sleeves hold a lot of cash; RISK_ON modest cash buffer.
    const cash_local = regime === 'RISK_OFF'
      ? round2(invested_local * 1.4 + 4200)
      : round2(invested_local * 0.32 + 1500);
    const equity_local = round2(cash_local + invested_local);
    const equity_base = round2(equity_local * fxRate);
    const sleeveWeights = positions.map(p => p.value_local / equity_local);
    positions.forEach((p, i) => { p.weight = sleeveWeights[i]; });
    return {
      key, name, currency: ccy, regime,
      cash_local, invested_local, equity_local, equity_base,
      weight: 0, cash_pct: cash_local / equity_local,
      last_rebalance_month: month, positions,
    };
  }
  function trade(date, region, ticker, side, shares, fill, commission, stamp_duty, currency) {
    return { date, region, ticker, side, shares, fill, commission, stamp_duty, currency, value: round2(shares * fill) };
  }
  function round2(x) { return Math.round(x * 100) / 100; }
})();

/* ============================================================================
   Number / date formatting helpers
   ============================================================================ */
const _fmtMoney = new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const _fmtInt = new Intl.NumberFormat('en-US');

function money(value, ccy, { symbol = true, code = true } = {}) {
  if (value == null || isNaN(value)) return '—';
  const sym = symbol ? (CCY_SYMBOL[ccy] || '') : '';
  const num = _fmtMoney.format(value);
  const tail = code && ccy ? ` ${ccy}` : '';
  return `${sym}${num}${tail}`;
}
function intFmt(value) { return value == null ? '—' : _fmtInt.format(value); }

// Sign-aware percentage. `digits` defaults to 2.
function pct(frac, { digits = 2, sign = false } = {}) {
  if (frac == null || isNaN(frac)) return '—';
  const v = frac * 100;
  const s = sign && v > 0 ? '+' : '';
  return `${s}${v.toFixed(digits)}%`;
}

function fmtDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso.length <= 10 ? iso + 'T00:00:00Z' : iso);
  if (isNaN(d)) return iso;
  return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: '2-digit', timeZone: 'UTC' });
}
function fmtDateShort(iso) {
  const d = new Date(iso + 'T00:00:00Z');
  if (isNaN(d)) return iso;
  return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', timeZone: 'UTC' });
}

function el(id) { return document.getElementById(id); }
function setText(id, text) { const n = el(id); if (n) n.textContent = text; }
function signClass(v) { return v > 0 ? 'val-pos' : v < 0 ? 'val-neg' : ''; }
function clamp01(x) { return Math.max(0, Math.min(1, x)); }

/* ============================================================================
   App state + polling controller
   ============================================================================ */
const App = {
  state: null,            // last good state rendered
  overlay: false,         // sleeve overlay toggle on equity chart
  sort: { key: 'value', dir: -1 }, // positions table sort (-1 desc)
  lastUpdate: null,       // Date of last successful render
  connected: false,
  seenTrades: new Set(),  // keys of trades already shown (for "new" animation)
  tickTimer: null,
  pollTimer: null,
};

async function poll() {
  try {
    const res = await fetch('/api/state', { cache: 'no-store' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    App.connected = true;
    App.lastUpdate = new Date();
    render(data);
  } catch (err) {
    // Keep last good data; just flip the status indicator to "reconnecting…".
    App.connected = false;
    updateStatus();
  }
}

function startPolling() {
  poll();                                   // immediate first attempt
  App.pollTimer = setInterval(poll, POLL_MS);
  // Lightweight 1s tick keeps the "updated Ns ago" label fresh.
  App.tickTimer = setInterval(updateStatus, 1000);
}

/* ============================================================================
   Master render — diff-free full re-render (the dataset is small)
   ============================================================================ */
function render(data) {
  App.state = data;
  renderTopbar(data);
  renderKpis(data);
  renderEquityChart();           // reads App.state + App.overlay
  renderDonut(data);
  renderSleeves(data);
  renderPositions(data);
  renderTrades(data);
  renderFees(data);
  updateStatus();
}

/* -------------------------------- Topbar --------------------------------- */
function renderTopbar(d) {
  setText('accountPill', `account · ${d.account ?? '—'}`);
  setText('ccyPill', `BASE · ${d.base_currency ?? '—'}`);
  el('syntheticPill').classList.toggle('hidden', !d.synthetic);
  setText('asOfPill', `as of ${fmtDate(d.as_of)}`);
}

function updateStatus() {
  const statusEl = el('statusEl');
  const dot = el('statusDot');
  const txt = el('statusText');
  if (!statusEl) return;

  if (!App.connected) {
    statusEl.classList.add('is-stale');
    txt.textContent = 'reconnecting…';
    return;
  }
  statusEl.classList.remove('is-stale');
  const secs = App.lastUpdate ? Math.max(0, Math.round((Date.now() - App.lastUpdate.getTime()) / 1000)) : 0;
  txt.textContent = `Live · updated ${secs}s ago`;
}

/* ---------------------------------- KPIs --------------------------------- */
function renderKpis(d) {
  const k = d.kpis || {};
  const ccy = d.base_currency;

  setText('kpiEquity', money(k.total_equity, ccy));
  setText('kpiEquitySub', `initial ${money(k.initial_capital, ccy)}`);

  const ret = el('kpiReturn');
  ret.textContent = pct(k.total_return, { sign: true });
  ret.className = 'kpi__value ' + signClass(k.total_return);

  const day = el('kpiDay');
  const arrow = (k.day_change || 0) > 0 ? '▲' : (k.day_change || 0) < 0 ? '▼' : '·';
  day.textContent = `${arrow} ${pct(Math.abs(k.day_change ?? 0))}`;
  day.className = 'kpi__value ' + signClass(k.day_change);

  setText('kpiPositions', intFmt(k.n_positions));
  setText('kpiTrades', intFmt(k.n_trades));

  setText('kpiCash', pct(k.cash_pct, { digits: 1 }));
  el('kpiCashBar').style.width = `${clamp01(k.cash_pct ?? 0) * 100}%`;
}

/* ============================================================================
   Equity chart — hand-drawn Canvas line + gradient area, gridlines, axes,
   crosshair + tooltip, optional per-sleeve overlay lines.
   ============================================================================ */
const equityChart = {
  canvas: null, ctx: null, dpr: 1,
  geom: null,            // cached plot geometry for hit-testing
  hoverX: null,          // device-independent x within plot for crosshair
};

function setupCanvas() {
  equityChart.canvas = el('equityCanvas');
  equityChart.ctx = equityChart.canvas.getContext('2d');

  // Resize handler keeps the canvas crisp on HiDPI and on layout changes.
  const ro = new ResizeObserver(() => { renderEquityChart(); });
  ro.observe(el('equityCanvas').parentElement);

  // Crosshair / tooltip interaction.
  const cv = equityChart.canvas;
  cv.addEventListener('mousemove', onChartMove);
  cv.addEventListener('mouseleave', () => {
    equityChart.hoverX = null;
    el('equityTooltip').classList.add('hidden');
    renderEquityChart();
  });
}

function onChartMove(ev) {
  const rect = equityChart.canvas.getBoundingClientRect();
  equityChart.hoverX = ev.clientX - rect.left;   // CSS px relative to canvas
  renderEquityChart();
}

function renderEquityChart() {
  const d = App.state;
  if (!d || !equityChart.ctx) return;
  const curve = d.equity_curve || [];
  if (!curve.length) return;

  const cv = equityChart.canvas;
  const ctx = equityChart.ctx;
  const dpr = window.devicePixelRatio || 1;
  const cssW = cv.parentElement.clientWidth;
  const cssH = cv.parentElement.clientHeight;

  // Backing-store sizing for crisp rendering.
  cv.width = Math.round(cssW * dpr);
  cv.height = Math.round(cssH * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);

  const padL = 64, padR = 16, padT = 14, padB = 28;
  const plotW = cssW - padL - padR;
  const plotH = cssH - padT - padB;
  if (plotW <= 0 || plotH <= 0) return;

  // ---- Determine y-range across whichever series are visible ----
  const showOverlay = App.overlay && Array.isArray(d.sleeve_curves) && d.sleeve_curves.length;
  let vals = curve.map(p => p.equity);
  if (showOverlay) {
    for (const r of d.sleeve_curves) for (const k of REGION_ORDER) if (r[k] != null) vals.push(r[k]);
  }
  let min = Math.min(...vals), max = Math.max(...vals);
  const padFrac = (max - min) * 0.08 || max * 0.02 || 1;
  min -= padFrac; max += padFrac;
  const niceMin = min, niceMax = max;

  const n = curve.length;
  const xAt = i => padL + (n === 1 ? plotW / 2 : (i / (n - 1)) * plotW);
  const yAt = v => padT + (1 - (v - niceMin) / (niceMax - niceMin)) * plotH;

  // ---- Gridlines + y-axis money labels ----
  ctx.font = '11px ' + getMono();
  ctx.textBaseline = 'middle';
  const ticks = 5;
  for (let i = 0; i <= ticks; i++) {
    const v = niceMin + (i / ticks) * (niceMax - niceMin);
    const y = yAt(v);
    ctx.strokeStyle = 'rgba(140,160,220,0.08)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(padL, y + 0.5); ctx.lineTo(cssW - padR, y + 0.5); ctx.stroke();
    ctx.fillStyle = 'rgba(150,166,196,0.7)';
    ctx.textAlign = 'right';
    ctx.fillText(compactMoney(v), padL - 10, y);
  }

  // ---- x-axis date labels (~5 evenly spaced) ----
  ctx.fillStyle = 'rgba(95,107,138,0.9)';
  ctx.textAlign = 'center';
  const xLabels = 5;
  for (let i = 0; i <= xLabels; i++) {
    const idx = Math.round((i / xLabels) * (n - 1));
    const x = xAt(idx);
    ctx.fillText(fmtDateShort(curve[idx].date), x, cssH - padB + 14);
  }

  // ---- Overlay sleeve lines (drawn first, beneath the main line) ----
  if (showOverlay) {
    for (const key of REGION_ORDER) {
      ctx.strokeStyle = REGION_COLORS[key];
      ctx.globalAlpha = 0.85;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      d.sleeve_curves.forEach((r, i) => {
        const v = r[key];
        if (v == null) return;
        const x = xAt(i), y = yAt(v);
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      });
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
  }

  // ---- Main equity area (gradient fill) ----
  const grad = ctx.createLinearGradient(0, padT, 0, padT + plotH);
  grad.addColorStop(0, 'rgba(110,168,255,0.34)');
  grad.addColorStop(1, 'rgba(110,168,255,0.0)');
  ctx.beginPath();
  curve.forEach((p, i) => { const x = xAt(i), y = yAt(p.equity); i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y); });
  ctx.lineTo(xAt(n - 1), padT + plotH);
  ctx.lineTo(xAt(0), padT + plotH);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();

  // ---- Main equity line ----
  ctx.beginPath();
  curve.forEach((p, i) => { const x = xAt(i), y = yAt(p.equity); i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y); });
  ctx.strokeStyle = '#8ab6ff';
  ctx.lineWidth = 2;
  ctx.shadowColor = 'rgba(110,168,255,0.5)';
  ctx.shadowBlur = 8;
  ctx.stroke();
  ctx.shadowBlur = 0;

  // Cache geometry for crosshair hit-testing.
  equityChart.geom = { padL, padR, padT, padB, plotW, plotH, n, xAt, yAt, curve, cssW, cssH, showOverlay };

  // ---- Crosshair + tooltip ----
  drawCrosshair();
  renderEquityLegend(d, showOverlay);
  setText('equityRange', `${curve.length} points · ${d.base_currency}`);
}

function drawCrosshair() {
  const g = equityChart.geom;
  const ctx = equityChart.ctx;
  const d = App.state;
  const tip = el('equityTooltip');
  if (!g || equityChart.hoverX == null) { tip.classList.add('hidden'); return; }

  // Find nearest data index to the hovered x.
  const rel = (equityChart.hoverX - g.padL) / g.plotW;
  let idx = Math.round(rel * (g.n - 1));
  idx = Math.max(0, Math.min(g.n - 1, idx));
  const x = g.xAt(idx);
  const p = g.curve[idx];

  // Vertical crosshair line.
  ctx.save();
  ctx.strokeStyle = 'rgba(200,215,255,0.25)';
  ctx.lineWidth = 1;
  ctx.setLineDash([4, 4]);
  ctx.beginPath();
  ctx.moveTo(x + 0.5, g.padT); ctx.lineTo(x + 0.5, g.padT + g.plotH); ctx.stroke();
  ctx.setLineDash([]);

  // Marker dot on the equity line.
  const y = g.yAt(p.equity);
  ctx.fillStyle = '#cfe0ff';
  ctx.beginPath(); ctx.arc(x, y, 4, 0, Math.PI * 2); ctx.fill();
  ctx.strokeStyle = 'rgba(110,168,255,0.9)'; ctx.lineWidth = 2; ctx.stroke();
  ctx.restore();

  // Build tooltip HTML.
  let rows = `<div class="tt-row"><span class="tt-key"><span class="swatch" style="background:#8ab6ff"></span>Equity</span>` +
    `<span class="tt-val">${money(p.equity, d.base_currency, { code: false })}</span></div>`;
  if (g.showOverlay && d.sleeve_curves[idx]) {
    for (const key of REGION_ORDER) {
      const v = d.sleeve_curves[idx][key];
      if (v == null) continue;
      rows += `<div class="tt-row"><span class="tt-key"><span class="swatch" style="background:${REGION_COLORS[key]}"></span>${key}</span>` +
        `<span class="tt-val">${money(v, d.base_currency, { code: false })}</span></div>`;
    }
  }
  tip.innerHTML = `<div class="tt-date">${fmtDate(p.date)}</div>${rows}`;
  tip.classList.remove('hidden');
  // Position the tooltip; clamp so it stays inside the plot horizontally.
  const clampedX = Math.max(g.padL + 60, Math.min(g.cssW - g.padR - 60, x));
  tip.style.left = clampedX + 'px';
  tip.style.top = (y) + 'px';
}

function renderEquityLegend(d, showOverlay) {
  const wrap = el('equityLegend');
  let html = `<span class="legend__item"><span class="swatch" style="background:#8ab6ff"></span>Combined (${d.base_currency})</span>`;
  if (showOverlay) {
    for (const key of REGION_ORDER) {
      html += `<span class="legend__item"><span class="swatch" style="background:${REGION_COLORS[key]}"></span>${key}</span>`;
    }
  }
  wrap.innerHTML = html;
}

// Compact money for axis labels: 99.9k, 1.2M etc.
function compactMoney(v) {
  const abs = Math.abs(v);
  if (abs >= 1e6) return (v / 1e6).toFixed(2) + 'M';
  if (abs >= 1e3) return (v / 1e3).toFixed(1) + 'k';
  return v.toFixed(0);
}
function getMono() { return 'ui-monospace, "SF Mono", Menlo, Consolas, monospace'; }

/* ============================================================================
   Allocation donut — inline SVG arcs, legend with actual/target + drift.
   ============================================================================ */
function renderDonut(d) {
  const sleeves = d.sleeves || [];
  const alloc = d.allocations || {};
  const size = 196, cx = size / 2, cy = size / 2, r = 76, sw = 18;
  const C = 2 * Math.PI * r;

  // Order by REGION_ORDER for stable colours.
  const ordered = REGION_ORDER
    .map(k => sleeves.find(s => s.key === k))
    .filter(Boolean);

  let offset = 0;
  const arcs = ordered.map(s => {
    const frac = clamp01(s.weight || 0);
    const len = frac * C;
    const dash = `${len} ${C - len}`;
    const dashoffset = -offset;
    offset += len;
    return `<circle class="donut-arc" cx="${cx}" cy="${cy}" r="${r}" fill="none"
              stroke="${REGION_COLORS[s.key]}" stroke-width="${sw}"
              stroke-dasharray="${dash}" stroke-dashoffset="${dashoffset}"
              stroke-linecap="butt"
              transform="rotate(-90 ${cx} ${cy})">
              <title>${s.key}: ${pct(s.weight)}</title></circle>`;
  }).join('');

  const totalEquity = d.kpis ? d.kpis.total_equity : ordered.reduce((a, s) => a + s.equity_base, 0);
  el('donutSvg').innerHTML = `
    <svg viewBox="0 0 ${size} ${size}" role="img" aria-label="Allocation donut">
      <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="rgba(255,255,255,0.06)" stroke-width="${sw}"></circle>
      ${arcs}
      <text class="donut-center__big" x="${cx}" y="${cy - 2}" text-anchor="middle">${compactMoney(totalEquity)}</text>
      <text class="donut-center__small" x="${cx}" y="${cy + 16}" text-anchor="middle">${d.base_currency} total</text>
    </svg>`;

  // Legend with actual vs target and a drift chip.
  const legend = ordered.map(s => {
    const target = alloc[s.key] ?? 0;
    const drift = (s.weight || 0) - target;
    const driftCls = Math.abs(drift) < 0.005 ? 'drift-flat' : drift > 0 ? 'drift-up' : 'drift-down';
    const driftTxt = (drift >= 0 ? '+' : '') + (drift * 100).toFixed(1) + 'pp';
    return `<li class="dleg">
      <span class="dleg__name"><span class="swatch" style="background:${REGION_COLORS[s.key]}"></span>${s.key}</span>
      <span class="dleg__nums">${pct(s.weight, { digits: 1 })} <span class="muted">/ tgt ${pct(target, { digits: 1 })}</span></span>
      <span class="dleg__drift ${driftCls}">${driftTxt}</span>
    </li>`;
  }).join('');
  el('donutLegend').innerHTML = legend;
}

/* ============================================================================
   Per-sleeve cards: regime badge, equity, liquidity split, sparkline, top 5.
   ============================================================================ */
function renderSleeves(d) {
  const sleeves = (d.sleeves || []).slice().sort(
    (a, b) => REGION_ORDER.indexOf(a.key) - REGION_ORDER.indexOf(b.key));
  const wrap = el('sleeveCards');

  wrap.innerHTML = sleeves.map(s => {
    const color = REGION_COLORS[s.key] || 'var(--accent)';
    const regimeOn = s.regime === 'RISK_ON';
    const regimeBadge = regimeOn
      ? `<span class="regime regime--on">RISK_ON</span>`
      : `<span class="regime regime--off">CASH · RISK_OFF</span>`;

    const cashPct = clamp01(s.cash_pct ?? 0);
    const invPct = 1 - cashPct;

    // Top ~5 positions by weight.
    const top = (s.positions || []).slice().sort((a, b) => (b.weight || 0) - (a.weight || 0)).slice(0, 5);
    const posList = top.length
      ? top.map(p => `<li><span class="pos-tk">${p.ticker}</span><span class="pos-wt">${pct(p.weight, { digits: 1 })}</span></li>`).join('')
      : `<li><span class="muted">no holdings</span></li>`;

    const spark = sparklineSVG(d.sleeve_curves, s.key, color);

    return `<article class="sleeve glass" style="--region:${color}">
      <div class="sleeve__head">
        <div>
          <div class="sleeve__name">${s.name} <span class="sleeve__ccy">${s.currency}</span></div>
          <div class="sleeve__equity">${money(s.equity_base, d.base_currency, { code: true })}
            <small>· local ${money(s.equity_local, s.currency)}</small></div>
        </div>
        ${regimeBadge}
      </div>

      ${spark}

      <div class="liq">
        <div class="liq__bar">
          <span class="liq__cash" style="width:${(cashPct * 100).toFixed(1)}%"></span>
          <span class="liq__inv" style="width:${(invPct * 100).toFixed(1)}%"></span>
        </div>
        <div class="liq__legend">
          <span>cash ${pct(cashPct, { digits: 0 })}</span>
          <span>invested ${pct(invPct, { digits: 0 })}</span>
        </div>
      </div>

      <div>
        <div class="sleeve__pos-head">Top holdings · rebal ${s.last_rebalance_month || '—'}</div>
        <ul class="sleeve__pos">${posList}</ul>
      </div>
    </article>`;
  }).join('');
}

// SVG sparkline for one sleeve series; auto-scales to its own min/max.
function sparklineSVG(sleeveCurves, key, color) {
  if (!Array.isArray(sleeveCurves) || !sleeveCurves.length) return '';
  const vals = sleeveCurves.map(r => r[key]).filter(v => v != null);
  if (vals.length < 2) return '';
  const w = 280, h = 44, pad = 3;
  const min = Math.min(...vals), max = Math.max(...vals);
  const span = (max - min) || 1;
  const xAt = i => pad + (i / (vals.length - 1)) * (w - 2 * pad);
  const yAt = v => pad + (1 - (v - min) / span) * (h - 2 * pad);

  let line = '', area = `M ${xAt(0)} ${h - pad}`;
  vals.forEach((v, i) => {
    const cmd = i === 0 ? 'M' : 'L';
    line += `${cmd} ${xAt(i).toFixed(1)} ${yAt(v).toFixed(1)} `;
    area += ` L ${xAt(i).toFixed(1)} ${yAt(v).toFixed(1)}`;
  });
  area += ` L ${xAt(vals.length - 1)} ${h - pad} Z`;
  const gid = `sg-${key}`;
  const up = vals[vals.length - 1] >= vals[0];

  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">
    <defs><linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="${color}" stop-opacity="0.30"/>
      <stop offset="1" stop-color="${color}" stop-opacity="0"/>
    </linearGradient></defs>
    <path d="${area}" fill="url(#${gid})"/>
    <path d="${line}" fill="none" stroke="${color}" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round" opacity="${up ? 1 : 0.85}"/>
  </svg>`;
}

/* ============================================================================
   Positions table — flattened across sleeves, sortable, default value desc.
   ============================================================================ */
function flattenPositions(d) {
  const rows = [];
  for (const s of d.sleeves || []) {
    for (const p of s.positions || []) {
      rows.push({
        region: s.key, currency: s.currency,
        ticker: p.ticker, shares: p.shares, price: p.price,
        value: p.value_base, weight: p.weight,
      });
    }
  }
  return rows;
}

function renderPositions(d) {
  const rows = flattenPositions(d);
  setText('positionsCount', `${rows.length} holdings`);

  // Sort.
  const { key, dir } = App.sort;
  rows.sort((a, b) => {
    let av = a[key], bv = b[key];
    if (typeof av === 'string') return dir * av.localeCompare(bv);
    return dir * ((av ?? 0) - (bv ?? 0));
  });

  const maxW = Math.max(...rows.map(r => r.weight || 0), 0.0001);
  const body = el('positionsBody');
  body.innerHTML = rows.map(r => {
    const wPctOfMax = clamp01((r.weight || 0) / maxW) * 100;
    return `<tr>
      <td class="col-region"><span class="rtag rtag--${r.region}">${r.region}</span></td>
      <td class="col-tk">${r.ticker}</td>
      <td class="num">${intFmt(r.shares)}</td>
      <td class="num">${money(r.price, r.currency, { code: false })}</td>
      <td class="num">${money(r.value, d.base_currency, { code: false })}</td>
      <td class="num"><span class="wbar">
        <span>${pct(r.weight, { digits: 1 })}</span>
        <span class="wbar__track"><span class="wbar__fill" style="width:${wPctOfMax.toFixed(0)}%"></span></span>
      </span></td>
    </tr>`;
  }).join('');

  // Reflect current sort state on the headers.
  document.querySelectorAll('#positionsTable th.sortable').forEach(th => {
    th.classList.remove('sort-asc', 'sort-desc');
    if (th.dataset.sort === key) th.classList.add(dir === 1 ? 'sort-asc' : 'sort-desc');
  });
}

function setupTableSort() {
  document.querySelectorAll('#positionsTable th.sortable').forEach(th => {
    th.addEventListener('click', () => {
      const key = th.dataset.sort;
      if (App.sort.key === key) {
        App.sort.dir *= -1;               // toggle direction
      } else {
        App.sort.key = key;
        // Text columns default ascending; numeric default descending.
        App.sort.dir = (key === 'region' || key === 'ticker') ? 1 : -1;
      }
      if (App.state) renderPositions(App.state);
    });
  });
}

/* ============================================================================
   Live trades feed — newest first, BUY/SELL pills, FTSE stamp duty, new flash.
   ============================================================================ */
function tradeKey(t) { return `${t.date}|${t.region}|${t.ticker}|${t.side}|${t.shares}|${t.fill}`; }

function renderTrades(d) {
  const trades = (d.recent_trades || []).slice();
  // Newest first: sort by date desc, preserving original order within a date.
  trades.forEach((t, i) => t.__i = i);
  trades.sort((a, b) => (b.date < a.date ? -1 : b.date > a.date ? 1 : a.__i - b.__i));

  const feed = el('tradesFeed');
  const firstRender = App.seenTrades.size === 0;

  feed.innerHTML = trades.map(t => {
    const key = tradeKey(t);
    const isNew = !firstRender && !App.seenTrades.has(key);
    const sideCls = t.side === 'BUY' ? 'side--BUY' : 'side--SELL';
    const stamp = (t.region === 'FTSE' && t.stamp_duty > 0)
      ? ` · <span class="stamp">stamp ${money(t.stamp_duty, t.currency, { code: false })}</span>` : '';
    const comm = t.commission != null ? ` · comm ${money(t.commission, t.currency, { code: false })}` : '';
    return `<li class="trade ${isNew ? 'is-new' : ''}">
      <span class="side ${sideCls}">${t.side}</span>
      <span class="rtag rtag--${t.region}">${t.region}</span>
      <span class="trade__main">
        <div class="trade__tk">${t.ticker}</div>
        <div class="trade__detail">${intFmt(t.shares)} @ ${money(t.fill, t.currency, { code: false })}${comm}${stamp}</div>
      </span>
      <span class="trade__right">
        <div class="trade__val">${money(t.value, t.currency)}</div>
        <div class="trade__date">${fmtDateShort(t.date)}</div>
      </span>
    </li>`;
  }).join('');

  // Remember which trades we have shown so only genuinely new ones animate.
  trades.forEach(t => App.seenTrades.add(tradeKey(t)));
}

/* ============================================================================
   Fees panel — totals grouped by currency, proportional bars.
   ============================================================================ */
function renderFees(d) {
  const fees = (d.kpis && d.kpis.fees) || [];
  const max = Math.max(...fees.map(f => f.amount || 0), 0.0001);
  el('feesPanel').innerHTML = fees.length
    ? fees.map(f => `<div class="fee-row">
        <div class="fee-row__top">
          <span class="fee-row__ccy">${f.currency}</span>
          <span class="fee-row__amt">${money(f.amount, f.currency)}</span>
        </div>
        <div class="fee-row__bar"><span class="fee-row__fill" style="width:${(clamp01(f.amount / max) * 100).toFixed(0)}%"></span></div>
      </div>`).join('')
    : `<div class="muted">No fees recorded.</div>`;
}

/* ============================================================================
   Bootstrap
   ============================================================================ */
function init() {
  setupCanvas();
  setupTableSort();

  // Overlay toggle re-renders the equity chart.
  el('overlayToggle').addEventListener('change', (e) => {
    App.overlay = e.target.checked;
    renderEquityChart();
  });

  // Render the embedded sample immediately so the screen is never blank,
  // then begin polling the real API.
  render(SAMPLE_STATE);
  App.connected = false;          // sample is not "live" until /api/state answers
  updateStatus();
  startPolling();

  // Keep canvas crisp on window resize.
  window.addEventListener('resize', () => renderEquityChart());
}

document.addEventListener('DOMContentLoaded', init);
