// Momentum home screen: "am I up, and is anything wrong?".
// Every number comes from /api/home; this file only lays it out.
"use strict";

const {SINCE, VERDICT, esc, fmt, dir, arrow, signed, pct, when} = window.MB;

let data = null;
let period = "ALL";
let selected = null;                      // a book key, or null for the overview
let tab = "overview";                     // the selected book's view
let chartView = "value";                  // Overview chart: "value" | "below"
const bookData = {};                      // key -> {summary, positions, error}
let marksTimer = null;

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

function attentionPanel(items, known, notes) {
  let html = `<div class="panel"><h3>Anything wrong?</h3>`;
  html += items.length ? MB.list(items)
    : `<span class="muted">Nothing needs you${data.last_opened ? ` — nothing new since you last looked (${esc(when(data.last_opened))})` : ""}.</span>`;
  if (known.length || notes.length) {
    html += `<details><summary>${known.length} known warnings, ${notes.length} notes</summary>${MB.list(known)}${MB.list(notes)}</details>`;
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
  html += MB.chips(period);
  if (core) html += MB.chart(core.series, "Combined core profit and loss (A$)", period);
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

function tabsBar(b) {
  const t = (id, label) => `<button role="tab" class="tab" data-tab="${id}" data-focus="tab:${id}"
    aria-selected="${tab === id}" tabindex="${tab === id ? 0 : -1}">${label}</button>`;
  const k = encodeURIComponent(b.key);
  const link = (id, label, t2) => `<a class="ext" data-link="${id}" data-focus="link:${id}"
    href="index.html?account=${k}&tab=${t2}" target="_blank" rel="noopener">${label} ↗</a>`;
  return `<div class="tabbar"><div role="tablist" aria-label="Book views">${t("overview", "Overview")}${t("positions", "Positions")}</div>
    <div class="links">${link("backtest", "Backtest", "BACKTEST")}${link("method", "Method", "METHOD")}${
      b.kind === "fx" ? link("swarm", "Swarm", "SWARM") : ""}${link("classic", "Classic view", "OVERVIEW")}</div></div>`;
}

function bookBody(b) {
  const c = bookData[b.key] || {};
  if (c.error) {
    return `<div class="banner" role="alert">Couldn't load this book (${esc(c.error)}). ⌘R to retry.</div>`;
  }
  if (tab === "positions") {
    return c.positions ? MB.screens.bookPositions.render(c.positions) : `<p class="muted">Loading positions…</p>`;
  }
  return c.summary ? MB.screens.bookOverview.render(c.summary, {period, chartView}) : `<p class="muted">Loading…</p>`;
}

function renderBook(b) {
  const st = (bookData[b.key] || {}).summary?.status;
  let html = banner();
  html += st ? MB.pill(st.level, st.shape, st.word) : "";
  html += `<h1 class="book-title">${esc(b.name)}</h1>`;
  html += tabsBar(b) + `<div class="book-body">${bookBody(b)}</div>`;
  return `${html}<p class="foot">Last update ${esc(when(b.as_of))}</p>`;
}

async function getJSON(url) {
  const res = await fetch(url, {cache: "no-store"});
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function loadBook(key, force = false) {
  const c = bookData[key] || (bookData[key] = {});
  try {
    if (force || !c.summary) c.summary = await getJSON(`/api/summary/${encodeURIComponent(key)}`);
    if (tab === "positions" && (force || !c.positions)) {
      c.positions = await getJSON(`/api/positions/${encodeURIComponent(key)}`);
    }
    c.error = null;
  } catch (err) {
    c.error = err.message;
  }
  if (selected === key) render();
  watchMarks(key);
}

// Equity books: today's prices arrive from a background fetch (~1 min). Ask
// once, poll every 5 s for up to ~3 min, then reload when they're ready.
function watchMarks(key) {
  clearTimeout(marksTimer);
  const c = bookData[key];
  if (!c || !c.summary || !["idle", "pending"].includes(c.summary.market)) return;
  let tries = 0;
  const tick = async () => {
    if (selected !== key || tries++ > 36) return;
    try {
      const m = await getJSON(`/api/marks/${encodeURIComponent(key)}`);
      if (["ready", "offline", "error"].includes(m.status)) { await loadBook(key, true); return; }
    } catch (err) {
      return;
    }
    marksTimer = setTimeout(tick, 5000);
  };
  tick();
}

function setTab(t) {
  tab = t;
  render();
  if (selected) loadBook(selected);
}

function renderSidebar() {
  let html = `<button class="row" data-key="" data-focus="row:" aria-current="${selected === null}">Overview</button>`;
  for (const g of data.groups) {
    html += `<h2>${esc(g.name === "CORE" ? "Core books" : `${g.name} (separate)`)}</h2>`;
    for (const b of books().filter((x) => x.group === g.name)) {
      const p = b.periods[period];
      const flag = b.freshness === "fresh" ? "" : `<span class="fresh">${esc(b.freshness)}</span>`;
      const chg = p && p.measurable
        ? `<span class="chg ${dir(p.change)}">${arrow(p.change)} ${signed(p.change)}</span>`
        : `<span class="chg muted">—</span>`;
      html += `<button class="row" data-key="${esc(b.key)}" data-focus="row:${esc(b.key)}" aria-current="${selected === b.key}">${esc(b.name)} ${flag}${chg}</button>`;
    }
  }
  html += `<h2>Detail</h2><a class="row" href="index.html" target="_blank" rel="noopener">Open terminal ↗</a>`;
  const nav = document.getElementById("sidebar");
  nav.innerHTML = html;
  nav.querySelectorAll("button.row").forEach((el) =>
    el.addEventListener("click", () => select(el.dataset.key || null)));
}

function render() {
  if (!data) return;
  const focus = document.activeElement?.dataset?.focus;
  renderSidebar();
  const b = books().find((x) => x.key === selected);
  const main = document.getElementById("main");
  main.innerHTML = b ? renderBook(b) : renderOverview();
  main.querySelectorAll("[data-period]").forEach((el) =>
    el.addEventListener("click", () => { period = el.dataset.period; render(); }));
  main.querySelectorAll("[data-view]").forEach((el) =>
    el.addEventListener("click", () => { chartView = el.dataset.view; render(); }));
  main.querySelectorAll('[role="tab"]').forEach((el) =>
    el.addEventListener("click", () => setTab(el.dataset.tab)));
  if (focus) document.querySelector(`[data-focus="${CSS.escape(focus)}"]`)?.focus();
}

function select(key) {
  selected = key;
  render();
  if (key) loadBook(key);
}

async function refresh(syncNow = false) {
  try {
    const res = await fetch(`/api/home${syncNow ? "?sync=1" : ""}`, {cache: "no-store"});
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    data = await res.json();
    render();
    if (selected) loadBook(selected, true);
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
  if ((e.key === "ArrowLeft" || e.key === "ArrowRight")
      && document.activeElement?.getAttribute("role") === "tab") {
    e.preventDefault();
    setTab(tab === "overview" ? "positions" : "overview");
    document.querySelector(`[data-focus="tab:${tab}"]`)?.focus();
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
