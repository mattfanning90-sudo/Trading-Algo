// One book's Overview: status is in the shell; this draws result → main problem
// → summary → tiles → "Is it working?" → chart → months → last closed. Every
// word and money figure is the server's (dashboard/book_summary.py).
"use strict";
(function (MB) {
  const {esc, arrowFor} = MB;

  function fix(i) {
    return i.backlog ? ` <span class="fix">Tracked as fix ${esc(i.backlog)}</span>` : "";
  }

  function item(i, isMain) {
    const tag = i.level === "act" ? `<span class="tag act">■</span>`
      : i.level === "look" ? `<span class="tag look">▲</span>` : "";
    const cav = i.caveat ? `<div class="caveat">${esc(i.caveat)}</div>` : "";
    const mp = isMain ? `<span class="mp-label">Main problem</span> ` : "";
    return `<li${isMain ? ` class="main-problem"` : ""} data-item="${esc(i.uid)}">${mp}${tag}${esc(i.text)}.${fix(i)}${cav}<div class="src">Source: ${esc(i.source)}</div></li>`;
  }

  function col(title, cls, items, empty) {
    return `<div class="vcol"><h4 class="${cls}">${title}</h4>${items.length
      ? `<ul class="items">${items.map((i) => item(i)).join("")}</ul>` : `<p class="muted">${esc(empty)}</p>`}</div>`;
  }

  function tiles(ts) {
    return `<div class="tiles">${ts.map((t) => `<div class="tile ${esc(t.dir || "")}">
      <div class="tl">${esc(t.label)}</div><div class="tv">${esc(t.value)}</div>
      <div class="tc">${esc(t.caption)}</div></div>`).join("")}</div>`;
  }

  function months(ms) {
    if (!ms.length) return `<span class="muted">No full month yet.</span>`;
    return `<div class="months">${ms.map((m) => `<div class="month ${esc(m.dir)}">
      <span class="ml">${esc(m.label)}</span><span class="mv">${arrowFor(m.dir)} ${esc(m.text)}</span></div>`).join("")}</div>`;
  }

  function closed(rows) {
    if (!rows.length) return `<span class="muted">No closed positions yet.</span>`;
    return `<table class="tbl"><thead><tr><th scope="col">Closed</th><th scope="col">Holding</th>
      <th scope="col">Held</th><th scope="col">Result</th></tr></thead><tbody>${rows.map((r) =>
      `<tr><td>${esc(r.date)}</td><td>${esc(r.symbol)}</td><td>${esc(r.held)}</td>
       <td class="${esc(r.dir)}">${esc(r.net)}</td></tr>`).join("")}</tbody></table>`;
  }

  function chartBlock(s, ctx) {
    const views = [["value", "Value"], ["below", "Below its best"]];
    const toggle = `<div class="chips" role="group" aria-label="Chart">${views.map(([v, l]) =>
      `<button class="chip" data-view="${v}" data-focus="view:${v}" aria-pressed="${ctx.chartView === v}">${l}</button>`).join("")}</div>`;
    const c = s.chart;
    const body = ctx.chartView === "below"
      ? MB.chart(c.below_best, "Below its best (fall from the highest saved value)", ctx.period,
                 {percent: true, desc: c.below_desc})
      : MB.chart(c.value, "Profit and loss since the start (A$)", ctx.period,
                 {bench: c.benchmark, desc: c.value_desc, benchLabel: "the market (leaves out dividends)"});
    return MB.chips(ctx.period) + toggle + body;
  }

  function render(s, ctx) {
    const mp = s.main_problem;
    const dup = s.attention.some((i) => i.uid === mp.uid);
    let html = "";
    if (s.attention.length) html += `<ul class="items attn">${s.attention.map((i) => item(i, dup && i.uid === mp.uid)).join("")}</ul>`;
    html += `<div class="result ${esc(s.result.dir || "")}">${arrowFor(s.result.dir)} ${esc(s.result.text)}</div>`;
    if (s.result.caveat) html += `<div class="caveat">${esc(s.result.caveat)}</div>`;
    if (!dup) html += `<div class="main-problem"><span class="mp-label">Main problem</span> ${esc(mp.text)}${fix(mp)}</div>`;
    html += `<div class="story">${s.summary.map((x) =>
      `<span data-item="${esc(x.item_id)}">${esc(x.text)}</span>`).join(" ")}</div>`;
    html += tiles(s.tiles);
    html += `<div class="panel"><h3>Is it working?</h3><div class="vgrid">
      ${col("✓ Going right", "right", s.panel.right, s.right_empty || "")}
      ${col("✗ Holding it back", "back", s.panel.wrong, "Nothing standing out.")}
      ${col("◆ Is the model any good?", "model", s.panel.model, "")}</div></div>`;
    html += chartBlock(s, ctx);
    html += `<div class="two"><div class="panel"><h3>Month by month</h3>${months(s.months)}</div>
      <div class="panel"><h3>Last closed positions</h3>${closed(s.closed)}</div></div>`;
    if (s.errors.length) html += `<p class="note">Couldn't measure: ${esc(s.errors.join(", "))}.</p>`;
    return html;
  }

  MB.screens.bookOverview = {render};
})(window.MB);
