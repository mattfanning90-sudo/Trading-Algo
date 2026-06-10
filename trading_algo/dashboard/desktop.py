"""Native desktop launcher for the dashboard.

Runs the stdlib dashboard server on a private port in a background thread and
shows it in a native OS WebView window via pywebview (WKWebView on macOS). This
is the entry point the packaged Mac `.app` calls. Cross-platform, but the `.app`
bundle is produced by `packaging/setup_app.py` (py2app) on macOS.

    python -m trading_algo.dashboard.desktop --account full --synthetic
"""
from __future__ import annotations

import argparse
import threading

from . import server


def launch(account: str = "full", synthetic: bool = False,
           host: str = "127.0.0.1", title: str = "Momentum Dashboard") -> None:
    # Import the native-window dependency first so the failure is a clear,
    # actionable message rather than a half-started server.
    try:
        import webview  # pywebview
    except ImportError as exc:
        raise SystemExit(
            "pywebview is required for the desktop app.\n"
            "  pip install pywebview pyobjc-framework-WebKit   # macOS"
        ) from exc

    httpd = server.create_server(account, synthetic, host, 0)  # 0 = free port
    port = httpd.server_address[1]
    url = f"http://{host}:{port}"

    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    webview.create_window(title, url, width=1480, height=920, min_size=(1080, 720))
    try:
        webview.start()          # blocks until the window is closed
    finally:
        httpd.shutdown()
        httpd.server_close()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Momentum Dashboard (native window)")
    ap.add_argument("--account", default="full")
    ap.add_argument("--synthetic", action="store_true")
    args = ap.parse_args(argv)
    launch(account=args.account, synthetic=args.synthetic)


if __name__ == "__main__":
    main()
