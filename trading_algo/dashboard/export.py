"""Export the dashboard to a single self-contained HTML file.

Inlines styles.css + app.js into index.html and bakes in a state snapshot behind
a tiny fetch() shim, so the result opens in ANY browser with no server and no
network — the charts, sorting and tabs all work. Handy for sharing a
point-in-time view of an account (email it, drop it in a report, etc.).

    python -m trading_algo.dashboard.export --account full --synthetic -o dashboard.html
"""
from __future__ import annotations

import argparse
import json
import os

from . import api

STATIC = os.path.join(os.path.dirname(__file__), "static")
_CSS_LINK = '<link rel="stylesheet" href="styles.css" />'
_JS_TAG = '<script src="app.js"></script>'


def render_html(snapshot: dict) -> str:
    """Produce a standalone HTML document for the given state snapshot."""
    with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as f:
        html = f.read()
    with open(os.path.join(STATIC, "styles.css"), encoding="utf-8") as f:
        css = f.read()
    with open(os.path.join(STATIC, "app.js"), encoding="utf-8") as f:
        js = f.read()

    if _CSS_LINK not in html or _JS_TAG not in html:
        raise RuntimeError("index.html markup changed; update export.py inliner")

    # Guard against any "</script>" sequence prematurely closing inline scripts.
    js = js.replace("</script", "<\\/script")
    snap_json = json.dumps(snapshot).replace("</", "<\\/")

    shim = (
        "<script>\n"
        "/* Baked-in snapshot + fetch shim: /api/state resolves with no server. */\n"
        f"window.__SNAPSHOT__ = {snap_json};\n"
        "(function () {\n"
        "  const real = window.fetch ? window.fetch.bind(window) : null;\n"
        "  window.fetch = function (url, opts) {\n"
        "    if (String(url).indexOf('/api/state') !== -1) {\n"
        "      return Promise.resolve(new Response(JSON.stringify(window.__SNAPSHOT__),\n"
        "        { status: 200, headers: { 'Content-Type': 'application/json' } }));\n"
        "    }\n"
        "    return real ? real(url, opts) : Promise.reject(new Error('offline'));\n"
        "  };\n"
        "})();\n"
        "</script>"
    )

    html = html.replace(_CSS_LINK, f"<style>\n{css}\n</style>")
    html = html.replace(_JS_TAG, f"{shim}\n<script>\n{js}\n</script>")
    return html


def export(account: str, synthetic: bool, out_path: str) -> str:
    html = render_html(api.build_snapshot(account, synthetic))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Export the dashboard to a standalone HTML file")
    ap.add_argument("--account", default="full")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("-o", "--out", default="dashboard.html")
    args = ap.parse_args(argv)
    path = export(args.account, args.synthetic, args.out)
    size_kb = os.path.getsize(path) // 1024
    print(f"Wrote {path} ({size_kb} KB) — open it in any browser, no server needed.")


if __name__ == "__main__":
    main()
