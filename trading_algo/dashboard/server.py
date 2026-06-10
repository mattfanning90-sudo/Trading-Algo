"""Zero-dependency dashboard web server (Python stdlib only).

Serves the static SPA at / and the live state at /api/state. No Flask/FastAPI —
keeps the project's "no heavy frameworks" rule and runs fully offline.
"""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import api

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
_CTYPES = {".html": "text/html", ".css": "text/css",
           ".js": "application/javascript", ".json": "application/json",
           ".svg": "image/svg+xml", ".ico": "image/x-icon"}


def make_handler(account: str, synthetic: bool):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj).encode(), "application/json; charset=utf-8")

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]

            if path == "/api/state":
                try:
                    self._json(200, api.build_snapshot(account, synthetic))
                except FileNotFoundError:
                    self._json(404, {"error": f"no account '{account}'. "
                                              f"Run paper_trade --init first."})
                except Exception as exc:  # surface, don't crash the server
                    self._json(500, {"error": repr(exc)})
                return

            # static files (path-traversal safe)
            rel = "index.html" if path in ("/", "") else path.lstrip("/")
            full = os.path.normpath(os.path.join(STATIC_DIR, rel))
            if not full.startswith(STATIC_DIR) or not os.path.isfile(full):
                self._send(404, b"not found", "text/plain; charset=utf-8")
                return
            ext = os.path.splitext(full)[1]
            ctype = _CTYPES.get(ext, "application/octet-stream") + "; charset=utf-8"
            with open(full, "rb") as f:
                self._send(200, f.read(), ctype)

        do_HEAD = do_GET  # noqa: N815

        def log_message(self, *args) -> None:  # keep the console clean
            pass

    return Handler


def serve(account: str = "main", synthetic: bool = False,
          host: str = "127.0.0.1", port: int = 8787) -> None:
    httpd = ThreadingHTTPServer((host, port), make_handler(account, synthetic))
    mode = "synthetic" if synthetic else "live"
    print(f"📊 Dashboard for account '{account}' ({mode}) → http://{host}:{port}")
    print("   Ctrl-C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
    finally:
        httpd.server_close()
