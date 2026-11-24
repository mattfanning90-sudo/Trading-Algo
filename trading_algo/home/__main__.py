"""`python -m trading_algo.home` — the Mac home screen.

Order matters, and it is why this launcher lives OUTSIDE `trading_algo.dashboard`:
that package's `__init__` imports `paper_trade`, whose STATE_DIR (and
`fx_book`'s) is fixed at import time. So: sync, point both state-directory
variables at the stable `current` link, and only then import anything that
reads books.
"""
from __future__ import annotations

import argparse
import os
import threading
import webbrowser
from dataclasses import asdict
from datetime import datetime, timezone

from . import jobs, seen, sync
from .paths import AppPaths


def point_readers_at(paths: AppPaths) -> None:
    os.environ["MOMENTUM_STATE_DIR"] = str(paths.current)
    os.environ["FX_STATE_DIR"] = str(paths.current)


def make_app(paths: AppPaths, first: sync.SyncResult, *, port: int = 0, live_build=None):
    """Build the server. Call only after `point_readers_at`."""
    from ..dashboard import home as home_api   # deliberately late (see module doc)
    from ..dashboard import server
    from ..dashboard.book_service import BookService

    started = datetime.now(timezone.utc)
    previous = seen.begin_session(paths.seen, started)
    # The first-run baseline applies to the FIRST build only: a problem that
    # appears after ⌘R in that same first session is genuinely new.
    state = {"sync": first, "baseline_pending": previous is None}
    lock = threading.Lock()

    def build(sync_now: bool = False) -> dict:
        with lock:
            if sync_now:
                state["sync"] = sync.sync(paths)
            baseline, state["baseline_pending"] = state["baseline_pending"], False
            return home_api.build_home(
                jobs=jobs.check(), seen_path=paths.seen,
                session_start=started.isoformat(), first_run=baseline,
                last_opened=previous, now=datetime.now(timezone.utc),
                snapshot=asdict(state["sync"]))

    books = BookService(online=lambda: not state["sync"].offline, live_build=live_build)
    return server.create_server("full", False, "127.0.0.1", port, home=build, books=books)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Momentum home screen")
    ap.add_argument("--no-sync", action="store_true",
                    help="show the last snapshot without fetching from GitHub")
    ap.add_argument("--browser", action="store_true",
                    help="open in the browser instead of a Mac window")
    ap.add_argument("--port", type=int, default=0)
    args = ap.parse_args(argv)

    paths = AppPaths.default()
    paths.snapshots.mkdir(parents=True, exist_ok=True)
    first = sync.last_result(paths, "sync skipped") if args.no_sync else sync.sync(paths)
    point_readers_at(paths)
    httpd = make_app(paths, first, port=args.port)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/home"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        webview = None
        if not args.browser:
            try:
                import webview                     # optional: pywebview
            except ImportError:
                webview = None
        if webview is None:
            print(f"Momentum home screen → {url}   (Ctrl-C to stop)")
            webbrowser.open(url)
            threading.Event().wait()
        else:
            webview.create_window("Momentum", url, width=1480, height=920,
                                  min_size=(1080, 720))
            webview.start()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        httpd.server_close()


if __name__ == "__main__":
    main()
