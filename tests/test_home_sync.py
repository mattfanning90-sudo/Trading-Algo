"""The home screen's sync: main's state/*.json into a private snapshot."""
import json
import os
import subprocess
from pathlib import Path

import pytest

from trading_algo.home import sync
from trading_algo.home.paths import AppPaths

REPO_ROOT = Path(__file__).resolve().parents[1]
GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
       "-c", "commit.gpgsign=false"]


def _commit(src: Path, files: dict[str, str], msg: str) -> str:
    for rel, text in files.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    subprocess.run([*GIT, "-C", str(src), "add", "-A"], check=True)
    subprocess.run([*GIT, "-C", str(src), "commit", "-q", "-m", msg], check=True)
    return subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture()
def remote(tmp_path):
    src = tmp_path / "remote"
    subprocess.run(["git", "init", "-q", "-b", "main", str(src)], check=True)
    # Let the local "server" honour partial clones, as GitHub does — otherwise
    # git silently makes a full clone and the on-demand path is never tested.
    for key in ("uploadpack.allowFilter", "uploadpack.allowAnySHA1InWant"):
        subprocess.run(["git", "-C", str(src), "config", key, "true"], check=True)
    _commit(src, {"state/paper_state_full.json": '{"a": 1}',
                  "state/fx_books.db": "binary",
                  "state/archive/old/paper_state_full.json": '{"old": 1}',
                  "README.md": "x"}, "first")
    return src


@pytest.fixture()
def app(tmp_path):
    return AppPaths(tmp_path / "app")


def test_sync_unpacks_only_top_level_json(remote, app):
    res = sync.sync(app, remote=f"file://{remote}")
    assert not res.offline and res.changed and res.sha
    snap = app.snapshots / res.sha
    assert sorted(p.name for p in snap.iterdir()) == [".meta.json", "paper_state_full.json"]
    assert Path(os.readlink(app.current)) == snap
    assert json.loads((app.current / "paper_state_full.json").read_text()) == {"a": 1}
    meta = json.loads((snap / ".meta.json").read_text())
    assert meta["v"] == 1 and meta["sha"] == res.sha and meta["committed_at"]


def test_second_sync_without_new_commit_changes_nothing(remote, app):
    first = sync.sync(app, remote=f"file://{remote}")
    again = sync.sync(app, remote=f"file://{remote}")
    assert again.sha == first.sha and not again.changed and not again.offline


def test_new_commit_moves_current_and_keeps_the_old_snapshot(remote, app):
    first = sync.sync(app, remote=f"file://{remote}")
    sha2 = _commit(remote, {"state/paper_state_full.json": '{"a": 2}'}, "second")
    res = sync.sync(app, remote=f"file://{remote}")
    assert res.sha == sha2 and res.changed
    assert json.loads((app.current / "paper_state_full.json").read_text()) == {"a": 2}
    assert (app.snapshots / first.sha).is_dir()


def test_keeps_only_the_last_ten_snapshots(remote, app):
    for i in range(12):
        _commit(remote, {"state/paper_state_full.json": f'{{"a": {i}}}'}, f"c{i}")
        sync.sync(app, remote=f"file://{remote}")
    dirs = [p for p in app.snapshots.iterdir() if p.is_dir() and not p.name.startswith(".")]
    assert len(dirs) == sync.KEEP


def test_unreachable_remote_keeps_the_last_snapshot(remote, app, tmp_path):
    first = sync.sync(app, remote=f"file://{remote}")
    remote.rename(tmp_path / "gone")
    res = sync.sync(app, remote=f"file://{remote}")
    assert res.offline and res.error and not res.changed
    assert res.sha == first.sha and res.committed_at
    assert (app.current / "paper_state_full.json").exists()


def test_sync_never_touches_the_checkout_it_runs_from(remote, app):
    """Working tree, index, every branch/tag and HEAD of the repo the tests run
    in are unchanged — a fetch that lost its `-C` would move Matt's branches."""
    def snapshot():
        run = lambda *a: subprocess.run(["git", "-C", str(REPO_ROOT), *a],
                                        capture_output=True, text=True, check=True).stdout
        return (run("status", "--porcelain"),
                run("for-each-ref", "--format=%(refname) %(objectname)"),
                run("rev-parse", "HEAD"))
    before = snapshot()
    sync.sync(app, remote=f"file://{remote}")
    assert snapshot() == before


def test_clone_is_partial_and_skips_non_json_contents(remote, app):
    """Only the JSON the snapshot needs is downloaded: README.md and the SQLite
    file's contents stay missing from the private copy."""
    sync.sync(app, remote=f"file://{remote}")
    listed = subprocess.run(["git", "-C", str(app.repo), "rev-list", "--objects",
                             "--missing=print", "--all"],
                            capture_output=True, text=True, check=True).stdout
    missing = [line for line in listed.splitlines() if line.startswith("?")]
    assert len(missing) >= 2          # README.md and state/fx_books.db at least


def test_last_result_describes_current_without_network(remote, app):
    first = sync.sync(app, remote=f"file://{remote}")
    res = sync.last_result(app, "sync skipped")
    assert res.sha == first.sha and res.committed_at == first.committed_at
    assert res.error == "sync skipped" and not res.offline
