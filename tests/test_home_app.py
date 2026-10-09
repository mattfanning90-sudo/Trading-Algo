"""The launcher points the book readers at the snapshot BEFORE they load —
they fix their state directory at import time."""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def run_py(code: str, tmp: Path) -> subprocess.CompletedProcess:
    # Keep the real environment (user site-packages hang off HOME); only the
    # app folder is redirected into the test's temp dir.
    env = {**os.environ, "MOMENTUM_HOME_DIR": str(tmp / "app")}
    return subprocess.run([sys.executable, "-c", textwrap.dedent(code)],
                          capture_output=True, text=True, cwd=REPO_ROOT, env=env)


def test_launcher_points_readers_before_they_load(tmp_path):
    done = run_py(f"""
        import sys
        from pathlib import Path
        from trading_algo.home import __main__ as launcher
        from trading_algo.home.paths import AppPaths
        assert "trading_algo.paper_trade" not in sys.modules, "a reader loaded too early"
        assert "trading_algo.dashboard" not in sys.modules, "dashboard loaded too early"
        paths = AppPaths(Path({str(tmp_path / 'app')!r}))
        launcher.point_readers_at(paths)
        from trading_algo import paper_trade
        from trading_algo.forex import fx_book
        assert paper_trade.STATE_DIR == str(paths.current), paper_trade.STATE_DIR
        assert fx_book.STATE_DIR == str(paths.current), fx_book.STATE_DIR
        print("ok")
    """, tmp_path)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "ok"


def test_make_app_serves_a_home_payload_with_no_snapshot(tmp_path):
    """First run, offline: the app still opens and says 'No data yet'."""
    done = run_py(f"""
        import json, threading, urllib.error, urllib.request
        from pathlib import Path
        from trading_algo.home import __main__ as launcher, jobs, sync
        from trading_algo.home.paths import AppPaths
        def offline(timeout=10.0):                          # no network in tests
            raise urllib.error.URLError("offline")
        jobs.fetch_runs = offline
        paths = AppPaths(Path({str(tmp_path / 'app')!r}))
        paths.snapshots.mkdir(parents=True)
        launcher.point_readers_at(paths)
        httpd = launcher.make_app(paths, sync.last_result(paths, "test"))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        port = httpd.server_address[1]
        body = json.load(urllib.request.urlopen(f"http://127.0.0.1:{{port}}/api/home"))
        httpd.shutdown()
        print(body["verdict"])
    """, tmp_path)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip().splitlines()[-1] == "nodata"
