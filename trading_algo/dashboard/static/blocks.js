// Momentum screens: shared building blocks on one namespace (MB) and no other
// top-level names, so a later export can inline every script into one page
// without collisions. Money on the book screens arrives as server-written
// text; numbers here only place points on a chart or format the home
// screen's own figures (dashboard/home.py computes them).
"use strict";
(function (root) {
  const PERIODS = ["1D", "1W", "1M", "ALL"];
  const SINCE = {"1D": "in the last session", "1W": "over the last week",
                 "1M": "over the last month", "ALL": "since you started"};
  const CHART_DAYS = {"1D": 7, "1W": 7, "1M": 31, "ALL": Infinity};
  const VERDICT = {
    act: {shape: "■", word: "Action needed"},
    look: {shape: "▲", word: "Look soon"},
    clear: {shape: "●", word: "All clear"},
    nodata: {shape: "◆", word: "No data yet"},
  };
  let chartSeq = 0;
  const ARROWS = {up: "▲", down: "▼", flat: "▶"};

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
    (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const fmt = (v) => Math.abs(v).toLocaleString("en-AU",
    {minimumFractionDigits: 2, maximumFractionDigits: 2});
  const dir = (v) => (v > 0 ? "up" : v < 0 ? "down" : "flat");
  const arrow = (v) => ARROWS[dir(v)];
  const arrowFor = (d) => ARROWS[d] || "";
  const signed = (v) => `${v > 0 ? "+" : v < 0 ? "−" : ""}A$${fmt(v)}`;
  const pct = (v) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v * 100).toFixed(2)}%`;
  const when = (iso) => (iso ? new Date(iso).toLocaleString("en-AU",
    {weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit"})
    : "never");

  function chips(period) {
    return `<div class="chips" role="group" aria-label="Period">${PERIODS.map((p) =>
      `<button class="chip" data-period="${p}" data-focus="chip:${p}" aria-pressed="${p === period}">${p}</button>`).join("")}</div>`;
  }

  function windowed(series, period) {
    const days = CHART_DAYS[period];
    if (days === Infinity || !series.length) return series;
    const end = new Date(series[series.length - 1][0]);
    const start = new Date(end.getTime() - days * 86400000);
    return series.filter(([d]) => new Date(d) >= start);
  }

  // A line over time with green/red areas around zero. opts.bench draws a
  // second series in long dashes; break-even is dotted, so the two differ by
  // pattern as well as colour. opts.desc is the text alternative.
  function chart(series, title, period, opts = {}) {
    const pts = windowed(series || [], period);
    const uid = ++chartSeq;
    if (pts.length < 2) {
      return `<div class="panel chart"><h3>${esc(title)}</h3><span class="muted">Not enough history yet.</span></div>`;
    }
    const first = pts[0][0];
    const bench = opts.bench ? windowed(opts.bench, period).filter(([d]) => d >= first && d <= pts[pts.length - 1][0]) : [];
    const vals = pts.map(([, v]) => v).concat(bench.map(([, v]) => v));
    const lo = Math.min(0, ...vals);
    const hi = Math.max(0, ...vals);
    const span = hi - lo || 1;
    const W = 600;
    const H = 150;
    const t0 = new Date(first).getTime();
    const tN = new Date(pts[pts.length - 1][0]).getTime();
    const x = (d) => ((new Date(d).getTime() - t0) / ((tN - t0) || 1)) * W;
    const y = (v) => H - ((v - lo) / span) * H;
    const path = (s) => s.map(([d, v]) => `${x(d).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
    const line = path(pts);
    const zero = y(0);
    const area = `0,${zero.toFixed(1)} ${line} ${W},${zero.toFixed(1)}`;
    const own = pts.map(([, v]) => v);
    const iLo = own.indexOf(Math.min(...own));
    const iHi = own.indexOf(Math.max(...own));
    const show = opts.percent ? pct : signed;
    const desc = opts.desc || title;
    const benchLine = bench.length > 1
      ? `<polyline points="${path(bench)}" fill="none" stroke="var(--muted)" stroke-width="1.2" stroke-dasharray="8 4"/>` : "";
    const legend = bench.length > 1 ? ` · long dashes = ${esc(opts.benchLabel || "the market")}` : "";
    return `<div class="panel chart"><h3>${esc(title)}</h3>
      <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${esc(desc)}">
        <desc>${esc(desc)}</desc>
        <defs><clipPath id="above-${uid}"><rect x="0" y="0" width="${W}" height="${zero.toFixed(1)}"/></clipPath>
              <clipPath id="below-${uid}"><rect x="0" y="${zero.toFixed(1)}" width="${W}" height="${(H - zero).toFixed(1)}"/></clipPath></defs>
        <polygon points="${area}" fill="var(--up)" opacity=".18" clip-path="url(#above-${uid})"/>
        <polygon points="${area}" fill="var(--down)" opacity=".18" clip-path="url(#below-${uid})"/>
        <line x1="0" x2="${W}" y1="${zero.toFixed(1)}" y2="${zero.toFixed(1)}" stroke="var(--muted)" stroke-dasharray="1 4" stroke-width="1"/>
        ${benchLine}
        <polyline points="${line}" fill="none" stroke="var(--text)" stroke-width="1.4"/>
      </svg>
      <div class="caption"><span>${esc(pts[0][0])}</span>
        <span>low ${show(own[iLo])} (${esc(pts[iLo][0])}) · high ${show(own[iHi])} (${esc(pts[iHi][0])}) · dotted = break-even${legend}</span>
        <span>${esc(pts[pts.length - 1][0])}</span></div></div>`;
  }

  function list(items) {
    const tags = {act: `<span class="tag act">■ Act now</span>`,
                  look: `<span class="tag look">▲ Look soon</span>`,
                  known: `<span class="tag muted">Known</span>`,
                  note: `<span class="tag muted">Note</span>`};
    return `<ul class="items">${items.map((i) => {
      const link = i.url ? ` <a href="${esc(i.url)}" target="_blank" rel="noopener">Open on GitHub ↗</a>` : "";
      return `<li>${tags[i.level] || ""}${i.book ? `<strong>${esc(i.book)}</strong> · ` : ""}${esc(i.text)}${link}</li>`;
    }).join("")}</ul>`;
  }

  const pill = (level, shape, word) =>
    `<span class="pill ${esc(level)}" role="status">${esc(shape)} ${esc(word)}</span>`;

  root.MB = {PERIODS, SINCE, CHART_DAYS, VERDICT, esc, fmt, dir, arrow, arrowFor, signed,
             pct, when, chips, chart, list, pill, screens: {}};
})(window);
