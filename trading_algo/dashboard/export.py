"""Export the dashboard to a single self-contained HTML file.

Inlines styles.css + app.js (and the bundled fonts, as data: URIs) into
index.html and bakes the API payloads behind a tiny fetch() shim, so the result
opens in ANY browser with no server and no network — charts, tabs and hovers
all work. Handy for sharing a point-in-time view of an account.

    python -m trading_algo.dashboard.export --account full --synthetic -o dashboard.html

Works for both equity books (paper_state_*.json) and FX agent books
(fx_state_*.json). The exported file is locked to the one baked account.

`--site` bakes EVERY discovered book plus the ALL-ACCOUNTS overview into one
page with the account switcher fully working — the whole terminal as a single
static file (used as the Pages site's index):

    python -m trading_algo.dashboard.export --site -o index.html
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re

from . import api, backtest_store, fx_api, meta, overview, registry

STATIC = os.path.join(os.path.dirname(__file__), "static")
_CSS_LINK = '<link rel="stylesheet" href="styles.css" />'
_JS_TAG = '<script src="app.js"></script>'


def _inline_fonts(css: str) -> str:
    """Rewrite url(fonts/*.woff2) references (quoted or not) into data: URIs.
    A missing font file is a packaging error — fail loudly rather than
    shipping an export with dead relative URLs."""
    def repl(m: re.Match) -> str:
        rel = m.group(2)
        try:
            with open(os.path.join(STATIC, rel), "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
        except OSError as exc:
            raise RuntimeError(f"export: bundled font missing: {rel}") from exc
        return f"url(data:font/woff2;base64,{b64})"
    return re.sub(r"""url\((['"]?)(fonts/[^)'"]+\.woff2)\1\)""", repl, css)


def render_html(payloads: dict, locked_key: str | None) -> str:
    """Produce a standalone HTML document for the given API payload map."""
    with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as f:
        html = f.read()
    with open(os.path.join(STATIC, "styles.css"), encoding="utf-8") as f:
        css = f.read()
    with open(os.path.join(STATIC, "app.js"), encoding="utf-8") as f:
        js = f.read()

    if _CSS_LINK not in html or _JS_TAG not in html:
        raise RuntimeError("index.html markup changed; update export.py inliner")

    css = _inline_fonts(css)
    # Guard against any "</script>" sequence prematurely closing inline scripts.
    js = js.replace("</script", "<\\/script")
    snap_json = json.dumps(payloads).replace("</", "<\\/")

    shim = (
        "<script>\n"
        "/* Baked-in payloads + fetch shim: the /api/* routes resolve offline. */\n"
        f"window.__SNAPSHOT__ = {snap_json};\n"
        f"window.__EXPORT_ACCOUNT__ = {json.dumps(locked_key)};\n"
        f"window.__EXPORT_ALL__ = {'true' if locked_key is None else 'false'};\n"
        "(function () {\n"
        "  const real = window.fetch ? window.fetch.bind(window) : null;\n"
        "  window.fetch = function (url, opts) {\n"
        "    const path = String(url).replace(/^[a-z]+:\\/\\/[^/]+/, '').split('?')[0];\n"
        "    if (path.indexOf('/api/') === 0) {\n"
        "      const hit = window.__SNAPSHOT__[path];\n"
        "      const body = hit !== undefined ? JSON.stringify(hit) : '{\"error\":\"not baked\"}';\n"
        "      return Promise.resolve(new Response(body,\n"
        "        { status: hit !== undefined ? 200 : 404,\n"
        "          headers: { 'Content-Type': 'application/json' } }));\n"
        "    }\n"
        "    return real ? real(url, opts) : Promise.reject(new Error('offline'));\n"
        "  };\n"
        "})();\n"
        "</script>"
    )

    html = html.replace(_CSS_LINK, f"<style>\n{css}\n</style>")
    html = html.replace(_JS_TAG, f"{shim}\n<script>\n{js}\n</script>")
    return html


def build_payloads(account: str, synthetic: bool) -> tuple[dict, str]:
    """All API payloads needed to render one account standalone."""
    entry = registry.resolve(account)
    if entry is None:
        # No state file discovered — fall back to the legacy equity path so the
        # error surfaces exactly as before (FileNotFoundError from api).
        snapshot = api.build_snapshot(account, synthetic)
        entry = {"account": account, "key": account.upper(), "kind": "equity",
                 "micro": snapshot.get("micro", False),
                 "label": f"{account.upper()} · EQUITIES", "sub": ""}
        page = snapshot
    elif entry["kind"] == "fx":
        page = fx_api.build_fx_snapshot(entry["account"])
    else:
        page = api.build_snapshot(entry["account"], synthetic)

    m = meta.build_meta()
    known = {e["account"] for e in m["accounts"]}
    if entry["account"] in known:
        m["accounts"] = [e for e in m["accounts"] if e["account"] == entry["account"]]
    else:
        m["accounts"] = [{k: entry.get(k, "") for k in
                          ("account", "key", "kind", "micro", "label", "sub")}]

    key = m["accounts"][0]["key"]
    payloads = {
        "/api/meta": m,
        f"/api/account/{key}": page,
        f"/api/backtest/{key}": backtest_store.load_backtest(
            entry["kind"], entry["account"]),
    }
    if entry["kind"] == "equity":
        payloads["/api/state"] = page          # legacy path, kept for compat
    return payloads, key


def build_payloads_site(synthetic: bool) -> dict:
    """Every discovered book + the ALL-ACCOUNTS overview in one payload map.
    A book whose snapshot fails (e.g. no market data for an equity book) is
    dropped from the baked switcher rather than shipping a dead chip."""
    m = meta.build_meta()
    payloads: dict = {"/api/overview": overview.build_overview()}
    baked: list[str] = []
    for entry in registry.discover_accounts():
        key = entry["key"]
        try:
            page = (fx_api.build_fx_snapshot(entry["account"])
                    if entry["kind"] == "fx"
                    else api.build_snapshot(entry["account"], synthetic))
        except Exception as exc:
            print(f"  skip {key}: {exc!r}")
            continue
        payloads[f"/api/account/{key}"] = page
        payloads[f"/api/backtest/{key}"] = backtest_store.load_backtest(
            entry["kind"], entry["account"])
        baked.append(key)
    m["accounts"] = [a for a in m["accounts"] if a["key"] in baked]
    payloads["/api/meta"] = m
    return payloads


def export(account: str, synthetic: bool, out_path: str) -> str:
    payloads, key = build_payloads(account, synthetic)
    html = render_html(payloads, key)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path


def export_site(synthetic: bool, out_path: str) -> str:
    payloads = build_payloads_site(synthetic)
    html = render_html(payloads, None)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Export the dashboard to a standalone HTML file")
    ap.add_argument("--account", default="full")
    ap.add_argument("--site", action="store_true",
                    help="bake every book + the ALL overview into one page")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("-o", "--out", default="dashboard.html")
    args = ap.parse_args(argv)
    path = (export_site(args.synthetic, args.out) if args.site
            else export(args.account, args.synthetic, args.out))
    size_kb = os.path.getsize(path) // 1024
    print(f"Wrote {path} ({size_kb} KB) — open it in any browser, no server needed.")


if __name__ == "__main__":
    main()
