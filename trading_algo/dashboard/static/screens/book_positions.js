// One book's Positions: per region (equity) or asset group (FX), whether it's
// investing or waiting and why, then each holding. All text is the server's
// (dashboard/book_positions.py); holdings show at once from the saved book.
"use strict";
(function (MB) {
  const {esc} = MB;

  function row(r) {
    const now = r.now ? `<br><span class="muted">${esc(r.now)}</span>` : "";
    const note = r.note ? `<div class="muted">${esc(r.note)}</div>` : "";
    const gain = r.gain ? `${esc(r.gain)}${r.gain_pct ? ` <small>${esc(r.gain_pct)}</small>` : ""}` : "—";
    return `<tr><td>${esc(r.symbol)}${r.side === "SHORT" ? ' <span class="muted">short</span>' : ""}</td>
      <td>${esc(r.qty)}</td><td>${esc(r.bought)}${now}</td><td>${esc(r.worth || "—")}</td>
      <td class="${esc(r.dir || "")}">${gain}${note}</td></tr>`;
  }

  function group(g) {
    const rows = g.rows || [];
    const table = rows.length ? `<table class="tbl"><thead><tr><th scope="col">Holding</th>
      <th scope="col">Size</th><th scope="col">Bought → now</th><th scope="col">Value</th>
      <th scope="col">Gain</th></tr></thead><tbody>${rows.map(row).join("")}</tbody></table>` : "";
    return `<section class="panel group ${g.level === "act" ? "attn" : ""}">
      <h3>${esc(g.name)}${g.cash ? ` <span class="muted">· ${esc(g.cash)}</span>` : ""}</h3>
      <p class="why">${g.level === "act" ? "■ " : ""}${esc(g.why)}</p>
      ${g.caveat ? `<p class="caveat">${esc(g.caveat)}</p>` : ""}${table}</section>`;
  }

  function render(p) {
    return `<p class="prices muted">${esc(p.count)} · ${esc((p.prices || {}).text || "")}</p>
      ${(p.notes || []).map((n) => `<p class="note">${esc(n)}</p>`).join("")}
      ${(p.groups || []).map(group).join("")}`;
  }

  MB.screens.bookPositions = {render};
})(window.MB);
