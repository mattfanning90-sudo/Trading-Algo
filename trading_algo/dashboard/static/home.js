// Momentum home screen: "am I up, and is anything wrong?".
// Every number comes from /api/home; this file only lays it out.
"use strict";

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

let data = null;
let period = "ALL";
let selected = null;                      // a book key, or null for the overview

const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const fmt = (v) => Math.abs(v).toLocaleString("en-AU",
  {minimumFractionDigits: 2, maximumFractionDigits: 2});
const dir = (v) => (v > 0 ? "up" : v < 0 ? "down" : "flat");
const arrow = (v) => (v > 0 ? "▲" : v < 0 ? "▼" : "▶");
const signed = (v) => `${v > 0 ? "+" : v < 0 ? "−" : ""}A$${fmt(v)}`;
const pct = (v) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v * 100).toFixed(2)}%`;
const when = (iso) => (iso ? new Date(iso).toLocaleString("en-AU",
  {weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit"})
  : "never");
const books = () => (data ? data.books : []);
const bookKeys = () => books().map((b) => b.key);

function hero(p, words) {
  if (!p || !p.measurable) return `<div class="hero muted">No change to show ${esc(words)}</div>`;
  const verb = p.change > 0 ? "Up" : p.change < 0 ? "Down" : "Flat";
  return `<div class="hero ${dir(p.change)}">${arrow(p.change)} ${verb} A$${fmt(p.change)}
    <small>${pct(p.pct)}</small></div><div class="balance">${esc(words)}</div>`;
}

const partial = (p) => (p && p.measurable && p.measured < p.of
  ? ` · ${p.measured} of ${p.of} books measured` : "");

function chips() {
  return `<div class="chips" role="group" aria-label="Period">${PERIODS.map((p) =>
    `<button class="chip" data-period="${p}" aria-pressed="${p === period}">${p}</button>`).join("")}</div>`;
}

function chart(series, title) {
  let pts = series;
  const days = CHART_DAYS[period];
  if (days !== Infinity && pts.length) {
    const end = new Date(pts[pts.length - 1][0]);
    const start = new Date(end.getTime() - days * 86400000);
    pts = pts.filter(([d]) => new Date(d) >= start);
  }
  if (pts.length < 2) {
    return `<div class="panel chart"><h3>${esc(title)}</h3><span class="muted">Not enough history yet.</span></div>`;
  }
  const vals = pts.map(([, v]) => v);
  const lo = Math.min(0, ...vals);
  const hi = Math.max(0, ...vals);
  const span = hi - lo || 1;
  const W = 600;
  const H = 150;
  const x = (i) => (i / (pts.length - 1)) * W;
  const y = (v) => H - ((v - lo) / span) * H;
  const line = pts.map(([, v], i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const zero = y(0);
  const area = `0,${zero.toFixed(1)} ${line} ${W},${zero.toFixed(1)}`;
  const iLo = vals.indexOf(Math.min(...vals));
  const iHi = vals.indexOf(Math.max(...vals));
  return `<div class="panel chart"><h3>${esc(title)}</h3>
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${esc(title)}">
      <defs><clipPath id="above"><rect x="0" y="0" width="${W}" height="${zero.toFixed(1)}"/></clipPath>
            <clipPath id="below"><rect x="0" y="${zero.toFixed(1)}" width="${W}" height="${(H - zero).toFixed(1)}"/></clipPath></defs>
      <polygon points="${area}" fill="var(--up)" opacity=".18" clip-path="url(#above)"/>
      <polygon points="${area}" fill="var(--down)" opacity=".18" clip-path="url(#below)"/>
      <line x1="0" x2="${W}" y1="${zero.toFixed(1)}" y2="${zero.toFixed(1)}" stroke="var(--muted)" stroke-dasharray="3 4" stroke-width=".8"/>
      <polyline points="${line}" fill="none" stroke="var(--text)" stroke-width="1.4"/>
    </svg>
    <div class="caption"><span>${esc(pts[0][0])}</span>
      <span>low ${signed(vals[iLo])} (${esc(pts[iLo][0])}) · high ${signed(vals[iHi])} (${esc(pts[iHi][0])}) · dashed = break-even</span>
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

function attentionPanel(items, known, notes) {
  let html = `<div class="panel"><h3>Anything wrong?</h3>`;
  html += items.length ? list(items)
    : `<span class="muted">Nothing needs you${data.last_opened ? ` — nothing new since you last looked (${esc(when(data.last_opened))})` : ""}.</span>`;
  if (known.length || notes.length) {
    html += `<details><summary>${known.length} known warnings, ${notes.length} notes</summary>${list(known)}${list(notes)}</details>`;
  }
  return `${html}</div>`;
}

function jobsPanel() {
  const cls = (s) => (s === "failed" || s === "missed" ? "bad"
    : s === "unknown" || s === "late" ? "unk" : "");
  return `<div class="panel"><h3>Scheduled robots</h3><div class="jobs">${data.jobs.map((j) =>
    `<span class="${cls(j.state)}">${esc(j.summary)}</span>`).join("")}</div></div>`;
}

function banner() {
  const s = data.snapshot || {};
  return s.offline
    ? `<div class="banner" role="alert">Couldn't reach GitHub — showing books as saved ${esc(when(s.committed_at))}.</div>`
    : "";
}

function footer() {
  const s = data.snapshot || {};
  const ages = books().filter((b) => b.group === "CORE" && b.as_of).map((b) => b.as_of).sort();
  return `<p class="foot">Books saved on GitHub ${esc(when(s.committed_at))}${ages.length
    ? ` · oldest core book updated ${esc(when(ages[0]))}` : ""} · ⌘R to refresh</p>`;
}

function renderOverview() {
  const v = VERDICT[data.verdict] || VERDICT.nodata;
  const core = data.groups.find((g) => g.name === "CORE") || data.groups[0];
  let html = banner();
  html += `<span class="pill ${esc(data.verdict)}" role="status">${v.shape} ${v.word}${data.jobs_unknown ? " · GitHub status unknown" : ""}</span>`;
  if (core) {
    const p = core.periods[period];
    html += hero(p, SINCE[period]);
    html += `<div class="balance">Core books worth A$${fmt(core.aum)} of A$${fmt(core.initial)} put in${partial(p)}</div>`;
  }
  html += chips();
  if (core) html += chart(core.series, "Combined core profit and loss (A$)");
  html += attentionPanel(data.attention, data.known, data.notes);
  for (const g of data.groups.filter((x) => x !== core)) {
    html += `<div class="panel"><h3>${esc(g.name)} books — separate, not in the core total</h3>
      Worth A$${fmt(g.aum)} · ${changeText(g.periods[period])} ${esc(SINCE[period])}</div>`;
  }
  return html + jobsPanel() + footer();
}

function changeText(p) {
  if (!p || !p.measurable) return `<span class="muted">not measurable</span>`;
  return `<span class="${dir(p.change)}">${arrow(p.change)} ${signed(p.change)} (${pct(p.pct)})</span>`;
}

function renderBook(b) {
  const p = b.periods[period];
  const state = b.halted ? ["act", "■ Halted by its safety breaker"]
    : b.near_breaker ? ["look", "▲ Near its safety breaker"]
    : b.freshness !== "fresh" ? ["look", `▲ Data ${b.freshness}`]
    : ["clear", "● Running normally"];
  const breaker = b.breaker != null ? ` · breaker trips at −${(b.breaker * 100).toFixed(0)}%` : " · no breaker";
  const mine = data.attention.concat(data.known).filter((i) => i.book === b.key);
  let html = banner();
  html += `<span class="pill ${state[0]}" role="status">${esc(state[1])}</span>`;
  html += `<h1 style="margin:10px 0 0;font-size:18px">${esc(b.name)}</h1>`;
  html += hero(p, SINCE[period]);
  html += `<div class="balance">Worth A$${fmt(b.equity)} of A$${fmt(b.initial)} put in · ${esc(b.status)}${breaker}</div>`;
  html += chips();
  html += chart(b.series, `${b.name} profit and loss (A$)`);
  html += `<div class="panel"><h3>Problems in this book</h3>${mine.length ? list(mine) : `<span class="muted">None.</span>`}</div>`;
  html += `<p class="foot">Last update ${esc(when(b.as_of))} · <a href="/" target="_blank" rel="noopener">Open in terminal ↗</a></p>`;
  return html;
}

function renderSidebar() {
  let html = `<button class="row" data-key="" aria-current="${selected === null}">Overview</button>`;
  for (const g of data.groups) {
    html += `<h2>${esc(g.name === "CORE" ? "Core books" : `${g.name} (separate)`)}</h2>`;
    for (const b of books().filter((x) => x.group === g.name)) {
      const p = b.periods[period];
      const flag = b.freshness === "fresh" ? "" : `<span class="fresh">${esc(b.freshness)}</span>`;
      const chg = p && p.measurable
        ? `<span class="chg ${dir(p.change)}">${arrow(p.change)} ${signed(p.change)}</span>`
        : `<span class="chg muted">—</span>`;
      html += `<button class="row" data-key="${esc(b.key)}" aria-current="${selected === b.key}">${esc(b.name)} ${flag}${chg}</button>`;
    }
  }
  html += `<h2>Detail</h2><a class="row" href="/" target="_blank" rel="noopener">Open terminal ↗</a>`;
  const nav = document.getElementById("sidebar");
  nav.innerHTML = html;
  nav.querySelectorAll("button.row").forEach((el) =>
    el.addEventListener("click", () => select(el.dataset.key || null)));
}

function render() {
  if (!data) return;
  renderSidebar();
  const b = books().find((x) => x.key === selected);
  const main = document.getElementById("main");
  main.innerHTML = b ? renderBook(b) : renderOverview();
  main.querySelectorAll(".chip").forEach((el) =>
    el.addEventListener("click", () => { period = el.dataset.period; render(); }));
}

function select(key) {
  selected = key;
  render();
}

async function refresh(syncNow = false) {
  try {
    const res = await fetch(`/api/home${syncNow ? "?sync=1" : ""}`, {cache: "no-store"});
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    data = await res.json();
    render();
  } catch (err) {
    document.getElementById("main").innerHTML =
      `<div class="banner" role="alert">The home screen couldn't load its data (${esc(err.message)}). ⌘R to retry.</div>`;
  }
}

document.addEventListener("keydown", (e) => {
  const cmd = e.metaKey || e.ctrlKey;
  if (cmd && e.key.toLowerCase() === "r") { e.preventDefault(); refresh(true); return; }
  if (cmd && /^[0-9]$/.test(e.key)) {
    e.preventDefault();
    const n = Number(e.key);
    select(n === 0 ? null : bookKeys()[n - 1] ?? selected);
    return;
  }
  if (e.key === "Escape") { select(null); return; }
  if ((e.key === "ArrowDown" || e.key === "ArrowUp") && document.activeElement?.closest("#sidebar")) {
    e.preventDefault();
    const rows = [...document.querySelectorAll("#sidebar button.row")];
    const i = rows.indexOf(document.activeElement);
    const next = rows[Math.max(0, Math.min(rows.length - 1, i + (e.key === "ArrowDown" ? 1 : -1)))];
    if (next) next.focus();
  }
});

refresh(false);
setInterval(() => refresh(false), 5 * 60 * 1000);
