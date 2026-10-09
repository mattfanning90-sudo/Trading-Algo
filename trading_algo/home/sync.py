"""Bring `main`'s book state onto this Mac, read-only.

The books run on GitHub and commit `state/` to `main`; the local checkout is
not trusted (it was two weeks stale on 2026-10-09). This keeps a private bare
copy of the repo in the app's own folder — full history, file contents fetched
only when needed — unpacks `state/*.json` for the newest `main` commit into
`snapshots/<sha>/`, then switches the `current` link to it in one step, so a
reader never sees half of one snapshot and half of another.

Only the top-level JSON files are unpacked. The SQLite stores are left out on
purpose: opening one runs `PRAGMA journal_mode=WAL` and `CREATE TABLE` (writes),
and every reader falls back to the JSON the engines dual-write anyway.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .paths import REPO_URL, AppPaths

KEEP = 10
_FAILURES = (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError)


@dataclass(frozen=True)
class SyncResult:
    sha: str | None            # the snapshot `current` points at afterwards
    committed_at: str | None   # when that commit was made (ISO-8601)
    changed: bool              # did `current` move?
    offline: bool              # could the sync not complete?
    error: str | None = None


def _env() -> dict:
    # Never wait on a credential prompt: the repo is public, and a hung prompt
    # would freeze the launcher until the timeout.
    return {**os.environ, "GIT_TERMINAL_PROMPT": "0"}


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True,
                          text=True, timeout=300, env=_env()).stdout.strip()


def current_sha(paths: AppPaths) -> str | None:
    try:
        return Path(os.readlink(paths.current)).name
    except OSError:
        return None


def _committed_at(paths: AppPaths, sha: str | None) -> str | None:
    if not sha:
        return None
    try:
        return json.loads((paths.snapshots / sha / ".meta.json").read_text())["committed_at"]
    except (OSError, ValueError, KeyError):
        return None


def last_result(paths: AppPaths, note: str) -> SyncResult:
    """Describe the current snapshot without touching the network."""
    sha = current_sha(paths)
    return SyncResult(sha, _committed_at(paths, sha), False, False, note)


def _unpack(paths: AppPaths, sha: str, committed_at: str) -> None:
    dest = paths.snapshots / sha
    if dest.exists():
        return
    # Only the top-level JSON files' contents are downloaded — never the committed
    # SQLite stores (fx_books.db is ~7 MB) on every new commit. `git archive`
    # can't be used for this: in a partial clone it downloads EVERY missing file
    # under the tree even when given a single path (measured 2026-10-10). So:
    # list the blob ids, fetch exactly those in one request, read them locally.
    wanted: list[tuple[str, str]] = []                    # (file name, blob id)
    for line in _git("-C", str(paths.repo), "ls-tree", sha, "state/").splitlines():
        meta, path = line.split("\t", 1)
        _mode, kind, oid = meta.split()
        if kind == "blob" and path.endswith(".json") and path.count("/") == 1:
            wanted.append((path.split("/", 1)[1], oid))
    if wanted:
        try:
            _git("-C", str(paths.repo), "fetch", "--quiet", "--no-tags",
                 "--filter=blob:none", "origin", *(oid for _, oid in wanted))
        except subprocess.CalledProcessError:
            pass       # e.g. a full clone already holds them; reading below decides
    tmp = paths.snapshots / f".tmp-{sha}"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    for name, oid in wanted:
        blob = subprocess.run(["git", "-C", str(paths.repo), "cat-file", "blob", oid],
                              check=True, capture_output=True, timeout=300,
                              env=_env()).stdout
        (tmp / name).write_bytes(blob)
    (tmp / ".meta.json").write_text(json.dumps(
        {"v": 1, "sha": sha, "committed_at": committed_at}))
    os.replace(tmp, dest)


def _point_current(paths: AppPaths, sha: str) -> None:
    link = paths.root / ".current.tmp"
    if link.is_symlink() or link.exists():
        link.unlink()
    os.symlink(paths.snapshots / sha, link)
    os.replace(link, paths.current)          # atomic on one filesystem


def _prune(paths: AppPaths, keep_sha: str) -> None:
    snaps = sorted((p for p in paths.snapshots.iterdir()
                    if p.is_dir() and not p.name.startswith(".")),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for old in snaps[KEEP:]:
        if old.name != keep_sha:
            shutil.rmtree(old, ignore_errors=True)


def sync(paths: AppPaths, remote: str = REPO_URL) -> SyncResult:
    paths.snapshots.mkdir(parents=True, exist_ok=True)
    before = current_sha(paths)
    try:
        if not (paths.repo / "HEAD").exists():
            shutil.rmtree(paths.repo, ignore_errors=True)   # a half-made clone
            _git("clone", "--bare", "--filter=blob:none", "--quiet", remote,
                 str(paths.repo))
        # Fetch through `origin` (the partial clone's promisor remote) so the
        # blob filter applies and later on-demand downloads know where to go.
        _git("-C", str(paths.repo), "remote", "set-url", "origin", remote)
        _git("-C", str(paths.repo), "fetch", "--quiet", "origin",
             "+refs/heads/main:refs/heads/main")
        sha = _git("-C", str(paths.repo), "rev-parse", "refs/heads/main")
        committed_at = _git("-C", str(paths.repo), "show", "-s", "--format=%cI", sha)
        _unpack(paths, sha, committed_at)
    except _FAILURES as exc:
        return SyncResult(before, _committed_at(paths, before), False, True,
                          str(exc)[:300])
    if sha != before:
        _point_current(paths, sha)
    _prune(paths, sha)
    return SyncResult(sha, committed_at, sha != before, False)
