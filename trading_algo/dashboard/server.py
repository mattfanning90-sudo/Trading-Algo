"""Zero-dependency dashboard web server (Python stdlib only).

Serves the terminal SPA at / and the live data under /api/*. No Flask/FastAPI —
keeps the project's "no heavy frameworks" rule and runs fully offline.

Routes
    /api/state              legacy: snapshot of the server's bound account
    /api/meta               config: accounts, strategy params, fees, schedule
    /api/overview           all paper books rolled up (offline-safe)
    /api/account/<KEY>      one book — equity or FX, by display key
    /api/backtest/<KEY>     cached backtest results for that book's kind
"""
from __future__ import annotations

import ipaddress
import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import api, backtest_store, fx_api, meta, overview, registry

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def _debug_enabled() -> bool:
    """Debug-only opt-in that echoes real exception detail back to the client.
    Off by default so 500s never leak internals over the wire."""
    return os.environ.get("DASHBOARD_DEBUG", "").strip().lower() in (
        "1", "true", "yes", "on")


def _is_loopback(host: str) -> bool:
    """True if `host` only reaches this machine (localhost / 127.0.0.0/8 / ::1)."""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def warn_if_public_bind(host: str) -> str | None:
    """The dashboard is read-only but UNAUTHENTICATED (it exposes positions,
    equity and P&L). It is intended for localhost only. If asked to bind to a
    non-loopback interface, print a clear warning to stderr and return it (so
    callers/tests can see it); return None for a loopback bind."""
    if _is_loopback(host):
        return None
    msg = (f"WARNING: binding to non-loopback host '{host}'. The read-only "
           f"dashboard (positions, equity and P&L) will be exposed "
           f"UNAUTHENTICATED to anyone who can reach this address. It is "
           f"intended for localhost (127.0.0.1) only.")
    print(msg, file=sys.stderr)
    return msg
_CTYPES = {".html": "text/html", ".css": "text/css",
           ".js": "application/javascript", ".json": "application/json",
           ".svg": "image/svg+xml", ".ico": "image/x-icon",
           ".woff2": "font/woff2", ".woff": "font/woff"}


def _regime_hint(snapshot: dict) -> str | None:
    """'ASX RISK-OFF' style chip for the overview card, from a fresh snapshot."""
    off = [s["key"] for s in snapshot.get("sleeves", []) if s.get("regime") == "RISK_OFF"]
    if not off:
        return None
    return f"{off[0]} RISK-OFF" if len(off) == 1 else f"{len(off)} SLEEVES RISK-OFF"


def make_handler(account: str, synthetic: bool):
    regime_hints: dict[str, str] = {}      # account -> chip, shared across requests

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

        def _api(self, path: str) -> None:
            requested = account            # which book this request is about
            try:
                if path == "/api/state":
                    self._json(200, self._equity(account))
                elif path == "/api/meta":
                    self._json(200, meta.build_meta())
                elif path == "/api/overview":
                    self._json(200, overview.build_overview(regime_hints))
                elif path.startswith("/api/account/"):
                    key = requested = path[len("/api/account/"):]
                    entry = registry.resolve(key)
                    if entry is None:
                        self._json(404, {"error": f"no account '{key}'"})
                        return
                    requested = entry["account"]
                    if entry["kind"] == "fx":
                        self._json(200, fx_api.build_fx_snapshot(entry["account"]))
                    else:
                        self._json(200, self._equity(entry["account"]))
                elif path.startswith("/api/backtest/"):
                    key = path[len("/api/backtest/"):]
                    entry = registry.resolve(key)
                    if entry is None:
                        self._json(404, {"error": f"no account '{key}'"})
                    else:
                        self._json(200, backtest_store.load_backtest(
                            entry["kind"], entry["account"]))
                else:
                    self._json(404, {"error": "unknown endpoint"})
            except FileNotFoundError:
                self._json(404, {"error": f"no account '{requested}'. "
                                          f"Run paper_trade --init first."})
            except Exception as exc:  # surface, don't crash the server
                # Log the real detail server-side; return a generic message so
                # internal exception text never leaks to the client.
                print(f"[dashboard] error handling {path!r}: {exc!r}",
                      file=sys.stderr)
                traceback.print_exc()
                body = {"error": "internal server error"}
                if _debug_enabled():
                    body["detail"] = repr(exc)
                self._json(500, body)

        def _equity(self, acct: str) -> dict:
            snapshot = api.build_snapshot(acct, synthetic)
            hint = _regime_hint(snapshot)
            if hint:
                regime_hints[acct] = hint
            else:
                regime_hints.pop(acct, None)
            return snapshot

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]

            if path.startswith("/api/"):
                self._api(path)
                return

            # static files (path-traversal safe)
            rel = "index.html" if path in ("/", "") else path.lstrip("/")
            full = os.path.normpath(os.path.join(STATIC_DIR, rel))
            if not full.startswith(STATIC_DIR) or not os.path.isfile(full):
                self._send(404, b"not found", "text/plain; charset=utf-8")
                return
            ext = os.path.splitext(full)[1]
            ctype = _CTYPES.get(ext, "application/octet-stream")
            if ext in (".html", ".css", ".js", ".json", ".svg"):
                ctype += "; charset=utf-8"
            with open(full, "rb") as f:
                self._send(200, f.read(), ctype)

        do_HEAD = do_GET  # noqa: N815

        def log_message(self, *args) -> None:  # keep the console clean
            pass

    return Handler


def create_server(account: str = "main", synthetic: bool = False,
                  host: str = "127.0.0.1", port: int = 8787) -> ThreadingHTTPServer:
    """Bind and return a (not-yet-serving) server. Pass port=0 for an OS-assigned
    free port (read it back from `.server_address[1]`). Used by the desktop app,
    which serves it on a background thread."""
    return ThreadingHTTPServer((host, port), make_handler(account, synthetic))


def serve(account: str = "main", synthetic: bool = False,
          host: str = "127.0.0.1", port: int = 8787) -> None:
    warn_if_public_bind(host)
    httpd = create_server(account, synthetic, host, port)
    mode = "synthetic" if synthetic else "live"
    print(f"📊 Dashboard for account '{account}' ({mode}) → http://{host}:{port}")
    print("   Ctrl-C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
    finally:
        httpd.server_close()
