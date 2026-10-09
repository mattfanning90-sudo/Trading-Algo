# Home Screen on Files (B1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Mac home screen that fetches `main`'s saved book state and answers "am I up, and is anything wrong?" — quietly unless something is broken or new.

**Architecture:** A new `trading_algo/home/` package (stdlib only) syncs `state/*.json` from GitHub into a private snapshot, judges the scheduled jobs, and remembers which problems Matt has seen; it then points the existing book readers at the snapshot and starts the existing stdlib server, which gains a `/home` page and a `/api/home` endpoint built by `trading_algo/dashboard/home.py` from `overview`, `verify` and those helpers. The front end is plain HTML/CSS/JS in the Midnight style.

**Tech Stack:** Python 3.11+ stdlib (`subprocess` + system `git`, `urllib`, `tarfile`, `http.server`), the repo's existing `dashboard` modules, vanilla JS/CSS, optional `pywebview` for a native window.

**Spec:** `docs/specs/home-screen.md` (umbrella: `docs/superpowers/specs/2026-10-09-mac-dashboard-design.md`, §5 B1).

## Global Constraints

- Python 3.11+; **no new required dependency**. `pywebview` is optional — without it the launcher opens the default browser.
- **Nothing in `trading_algo/home/` may import `trading_algo.dashboard`, `trading_algo.paper_trade` or `trading_algo.forex.fx_book` at module level.** `trading_algo/dashboard/__init__.py` runs `from .api import build_snapshot`, which imports `paper_trade`, and `paper_trade.STATE_DIR` / `fx_book.STATE_DIR` are fixed at import time from `MOMENTUM_STATE_DIR` / `FX_STATE_DIR`.
- **No drawdown, Sharpe or annualisation outside `trading_algo/metrics.py`** — `tests/test_metrics_single_source.py` must stay green. Distance from a book's peak uses the figure `overview` already computes from the stored peak (the breaker's own figure).
- **Read-only toward the books.** The app writes only under its own folder: `~/Library/Application Support/Momentum/`, overridable with `MOMENTUM_HOME_DIR`. Snapshots hold `state/*.json` only — never `*.db` (opening SQLite runs `PRAGMA journal_mode=WAL` and `CREATE TABLE`, i.e. writes).
- **Tests never touch the network or the real App Support folder**: use `tmp_path`, a throwaway local git remote, and recorded GitHub API fixtures.
- Every money figure on screen carries a currency (`A$`) and every book carries an as-of time.
- All times are handled as timezone-aware UTC in Python; the browser formats them in local time.
- Measured grace periods (from 500+ scheduled runs, 2026-08-06 → 2026-10-09): daily jobs **late** until 8 h 30 m after their slot, then **missed**; DAYTRADER **missed** after 14 h without a run on a weekday.
- "Near the breaker" = the fall from the stored peak reaches **60%** of the book's breaker level (draft, spec Q-1).

## Review Focus

- **A book's saved file is damaged or half-written.** `registry.discover_accounts` silently skips it, so it would vanish from the screen; `verify` reports it as ERROR `unreadable-state`. Expect: act-now item naming it. Test in Task 6 (`test_damaged_book_file_is_act_now`).
- **First launch with no internet** (no snapshot yet). Expect: a "No data yet" state, never "All clear". Test in Task 6 (`test_no_books_is_nodata_not_clear`).
- **GitHub rate-limits the public API (HTTP 403).** Expect: every job "unknown since …", never "ok". Test in Task 4 (`test_http_403_is_unknown_not_ok`).
- **An equity mark labelled with tomorrow's date** (the ASX sleeve can label a run ahead of UTC). Expect: fresh, not an error. Test in Task 6 (`test_mark_dated_ahead_of_now_is_fresh`).
- **A saved mark that is NaN/None** (the overview docstring records four such marks in FULL). Expect: ignored in period maths, never turns a change into NaN. Test in Task 6 (`test_unvaluable_marks_are_skipped`).

---

### Task 1: Audit findings name what they're about

**Files:**
- Modify: `trading_algo/verify.py` (the `no-fx-rate`, `position-mismatch`, `cash-drift`, `missing-position`, `weight-mismatch`, `idle-sleeve`, `pinned-flat` constructors and the four `return Finding(` in `_never_traded_finding`)
- Test: `tests/test_verify.py`

**Interfaces:**
- Consumes: nothing.
- Produces: every per-sleeve finding carries `detail["subject"] = <sleeve key>` (e.g. `"ASX"`); every per-pair finding carries `detail["subject"] = <pair>` (e.g. `"EURUSD"`). Book-level findings keep no subject.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_verify.py`:

```python
def test_sleeve_findings_name_their_sleeve():
    """The home screen tells a new problem from a known one by (book, code,
    subject); a sleeve-level finding must say WHICH sleeve in data, not only in
    its wording (which changes daily: "77 days" -> "78 days")."""
    state = {
        "allocations": {"ASX": 0.33}, "trades": [],
        "equity_history": [["2026-10-09", 100_000.0]],
        "sleeves": {"ASX": {
            "cash": 33_333.33, "currency": "AUD", "positions": {},
            "last_rebalance_month": "2026-10",
            "last_status": {"date": "2026-10-09", "status": "cash:idle",
                            "flat_since": "2026-07-24"}}},
    }
    found = verify.check_liveness("full", state, "equity", datetime(2026, 10, 9))
    by_code = {f.code: f for f in found}
    assert {"idle-sleeve", "pinned-flat", "never-traded"} <= set(by_code)
    for code in ("idle-sleeve", "pinned-flat", "never-traded"):
        assert by_code[code].detail["subject"] == "ASX"


def test_pair_findings_name_their_pair():
    state = {"trades": [{"date": "2026-10-09", "pair": "EURUSD",
                         "target_weight": 0.2}], "positions": {}}
    found = verify.reconcile_fx("matt", state)
    assert [(f.code, f.detail["subject"]) for f in found] == [
        ("missing-position", "EURUSD")]
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python3 -m pytest tests/test_verify.py -k "name_their" -v`
Expected: FAIL with `KeyError: 'subject'`.

- [ ] **Step 3: Add the subject at each constructor**

In `trading_algo/verify.py`:

`reconcile_equity` — `no-fx-rate`: add a detail argument:
```python
            found.append(Finding(
                WARN, account, "no-fx-rate",
                f"sleeve {key}: no {sleeve.get('currency')} rate in fx_snapshot — "
                "cannot reconcile its funding in base currency",
                {"subject": key}))
```
`position-mismatch`: change its detail to
```python
                {"replayed": pos, "stored": stored_pos, "subject": key}))
```
`cash-drift`: change its detail to
```python
                {"replayed": round(cash, 2), "stored": round(stored_cash, 2),
                 "subject": key}))
```
`reconcile_fx` — `missing-position`:
```python
            found.append(Finding(
                ERROR, account, "missing-position",
                f"{pair}: ledger ends at weight {w:+.4f} but the pair is absent "
                "from stored positions",
                {"subject": pair}))
```
`weight-mismatch`: change its detail to
```python
                {"ledger": w, "stored": stored[pair], "subject": pair}))
```
`check_liveness` — `idle-sleeve`:
```python
                found.append(Finding(
                    WARN, account, "idle-sleeve",
                    f"sleeve {key} has been in cash since {flat_since} "
                    f"({days} days, status {status.get('status')!r}) — its "
                    "capital is earning nothing and nothing alerts on it",
                    {"subject": key}))
```
`pinned-flat`:
```python
            found.append(Finding(
                WARN, account, "pinned-flat",
                f"sleeve {key} holds nothing and is already stamped for "
                f"{stamped} — _should_rebalance() is False until the next "
                f"calendar month, so it stays in cash regardless of signal "
                f"(status {status.get('status')!r})",
                {"subject": key}))
```
`_never_traded_finding` — append `{"subject": key}` as the fifth argument to each of its four `return Finding(...)` calls, e.g. the first becomes:
```python
        return Finding(
            INFO, account, "flat-by-design",
            f"{what} — last evaluated {evaluated} and came back flat "
            f"({reason}). Holding cash is the designed behaviour here, not a "
            "fault; the capital is idle by choice",
            {"subject": key})
```

- [ ] **Step 4: Run the verify tests**

Run: `python3 -m pytest tests/test_verify.py -v`
Expected: all PASS (the two new ones included).

- [ ] **Step 5: Commit**

```bash
git add trading_algo/verify.py tests/test_verify.py
git commit -m "feat(verify): sleeve- and pair-level findings name their subject"
```

---

### Task 2: Overview cards carry the breaker's own figures

**Files:**
- Modify: `trading_algo/dashboard/overview.py` (`_card`)
- Test: `tests/test_home_overview_fields.py` (create)

**Interfaces:**
- Consumes: `paper_trade._account_drawdown_stop(state) -> float | None`; `fx_book._params(state) -> FXParams` (`.max_drawdown_stop: float | None`).
- Produces: each card from `overview.build_overview()["accounts"]` gains `"off_peak": float` (equity ÷ stored peak − 1, ≤ 0, rounded to 6 dp — the figure the breaker compares) and `"breaker": float | None` (the book's breaker level as a positive fraction; `None` = no breaker).

- [ ] **Step 1: Write the failing test** — create `tests/test_home_overview_fields.py`:

```python
"""Overview cards expose the breaker's own numbers, so the home screen can say
"near the breaker" without computing a second drawdown."""
import json

import trading_algo.paper_trade as pt
from trading_algo import config as cfg
from trading_algo.dashboard import overview
from trading_algo.forex import fx_book
from trading_algo.forex import fx_config


def _fx(tmp_path, **over):
    state = {"account": "matt", "currency": "AUD", "profile": "balanced",
             "bar": "1d", "symbols": ["EURUSD"], "initial_capital": 5000.0,
             "equity": 4900.0, "peak_equity": 5070.0, "positions": {},
             "last_close": {}, "last_bar_date": "2026-10-09",
             "risk_halted": False, "halt_cooldown": 0, "trades": [],
             "decisions": {},
             "equity_history": [["2026-10-08", 4950.0], ["2026-10-09", 4900.0]]}
    state.update(over)
    (tmp_path / "fx_state_matt.json").write_text(json.dumps(state))


def _cards(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    return {c["key"]: c for c in overview.build_overview()["accounts"]}


def test_fx_card_has_off_peak_and_profile_breaker(tmp_path, monkeypatch):
    _fx(tmp_path)
    card = _cards(tmp_path, monkeypatch)["MATT"]
    assert card["off_peak"] == round(4900.0 / 5070.0 - 1.0, 6)     # -0.033531
    assert card["breaker"] == fx_config.profile("balanced").max_drawdown_stop


def test_equity_card_uses_the_engine_breaker(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "STATE_DIR", str(tmp_path))
    pt.init_account("full", capital=100_000, synthetic=True)
    card = _cards(tmp_path, monkeypatch)["FULL"]
    assert card["breaker"] == cfg.MAX_DRAWDOWN_STOP
    assert card["off_peak"] <= 0.0


def test_book_without_a_breaker_reports_none(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "STATE_DIR", str(tmp_path))
    pt.init_account("full", capital=100_000, synthetic=True)
    path = tmp_path / "paper_state_full.json"
    state = json.loads(path.read_text())
    state["max_drawdown_stop"] = None          # the `ultra` profile does this
    path.write_text(json.dumps(state))
    assert _cards(tmp_path, monkeypatch)["FULL"]["breaker"] is None
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m pytest tests/test_home_overview_fields.py -v`
Expected: FAIL with `KeyError: 'off_peak'`.

- [ ] **Step 3: Implement** — in `trading_algo/dashboard/overview.py`, inside `_card`, directly after the line `halted = bool(state.get("risk_halted", False))` add:

```python
    # The breaker's own level, read from the engine that applies it — never a
    # second copy of the rule. None = this book has no breaker (e.g. `ultra`).
    if kind == "equity":
        breaker = paper_trade._account_drawdown_stop(state)
    else:
        try:
            breaker = fx_book._params(state).max_drawdown_stop
        except KeyError:                 # an unknown profile name
            breaker = None
```
and in the returned dict, after `"halted": halted,` add:
```python
        "off_peak": round(off_peak, 6),
        "breaker": breaker,
```

- [ ] **Step 4: Run the new and existing dashboard tests**

Run: `python3 -m pytest tests/test_home_overview_fields.py tests/test_dashboard_terminal.py tests/test_dashboard_colour_convention.py tests/test_experimental_books.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add trading_algo/dashboard/overview.py tests/test_home_overview_fields.py
git commit -m "feat(overview): cards expose off-peak and the book's breaker level"
```

---

### Task 3: Sync `main`'s book state into a private snapshot

**Files:**
- Create: `trading_algo/home/__init__.py`, `trading_algo/home/paths.py`, `trading_algo/home/sync.py`
- Test: `tests/test_home_sync.py`

**Interfaces:**
- Consumes: the `git` executable.
- Produces:
  - `paths.AppPaths(root: Path)` with properties `.repo`, `.snapshots`, `.current`, `.seen` (all `Path`) and `AppPaths.default() -> AppPaths` (honours `MOMENTUM_HOME_DIR`); constants `paths.REPO_URL`, `paths.API_RUNS`.
  - `sync.SyncResult(sha: str|None, committed_at: str|None, changed: bool, offline: bool, error: str|None = None)` (frozen dataclass).
  - `sync.sync(paths: AppPaths, remote: str = REPO_URL) -> SyncResult`
  - `sync.current_sha(paths) -> str|None`
  - `sync.last_result(paths, note: str) -> SyncResult` (no network; describes the current snapshot)
  - Snapshot layout: `snapshots/<sha>/*.json` + `snapshots/<sha>/.meta.json` (`{"v": 1, "sha", "committed_at"}`); `current` is a symlink to `snapshots/<sha>`.

- [ ] **Step 1: Write the failing tests** — create `tests/test_home_sync.py`:

```python
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
    def status():
        return subprocess.run(["git", "-C", str(REPO_ROOT), "status", "--porcelain"],
                              capture_output=True, text=True, check=True).stdout
    before = status()
    sync.sync(app, remote=f"file://{remote}")
    assert status() == before


def test_last_result_describes_current_without_network(remote, app):
    first = sync.sync(app, remote=f"file://{remote}")
    res = sync.last_result(app, "sync skipped")
    assert res.sha == first.sha and res.committed_at == first.committed_at
    assert res.error == "sync skipped" and not res.offline
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python3 -m pytest tests/test_home_sync.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'trading_algo.home'`.

- [ ] **Step 3: Implement** — create `trading_algo/home/__init__.py`:

```python
"""The Mac home screen: "am I up, and is anything wrong?".

Stdlib only, and nothing here imports `trading_algo.dashboard`,
`trading_algo.paper_trade` or `trading_algo.forex.fx_book` at module level:
those readers fix their state directory at import time, and the launcher must
point them at the synced snapshot first. See `__main__.py`.
"""
```

create `trading_algo/home/paths.py`:

```python
"""Where the Mac home screen keeps its own files (never the repo's state/)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_URL = "https://github.com/mattfanning90-sudo/Trading-Algo.git"
API_RUNS = "https://api.github.com/repos/mattfanning90-sudo/Trading-Algo/actions/runs"


@dataclass(frozen=True)
class AppPaths:
    root: Path

    @property
    def repo(self) -> Path:          # private bare copy of the GitHub repo
        return self.root / "repo"

    @property
    def snapshots(self) -> Path:     # one folder of state/*.json per commit
        return self.root / "snapshots"

    @property
    def current(self) -> Path:       # symlink → the snapshot being shown
        return self.root / "current"

    @property
    def seen(self) -> Path:          # which problems Matt has already seen
        return self.root / "seen.json"

    @classmethod
    def default(cls) -> "AppPaths":
        override = os.environ.get("MOMENTUM_HOME_DIR")
        if override:
            return cls(Path(override))
        return cls(Path.home() / "Library" / "Application Support" / "Momentum")
```

create `trading_algo/home/sync.py`:

```python
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

import io
import json
import os
import shutil
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path

from .paths import REPO_URL, AppPaths

KEEP = 10
_FAILURES = (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError,
             tarfile.TarError)


@dataclass(frozen=True)
class SyncResult:
    sha: str | None            # the snapshot `current` points at afterwards
    committed_at: str | None   # when that commit was made (ISO-8601)
    changed: bool              # did `current` move?
    offline: bool              # could the sync not complete?
    error: str | None = None


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True,
                          text=True, timeout=300).stdout.strip()


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
    raw = subprocess.run(["git", "-C", str(paths.repo), "archive", sha, "state"],
                         check=True, capture_output=True, timeout=300).stdout
    tmp = paths.snapshots / f".tmp-{sha}"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
        for member in tar.getmembers():
            parts = member.name.split("/")
            if (member.isfile() and len(parts) == 2 and parts[0] == "state"
                    and parts[1].endswith(".json")):
                (tmp / parts[1]).write_bytes(tar.extractfile(member).read())
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
        _git("-C", str(paths.repo), "fetch", "--quiet", remote,
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
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/test_home_sync.py -v`
Expected: all 7 PASS. (A `filtering not recognized by server` warning from git on the local remote is expected and harmless.)

- [ ] **Step 5: Commit**

```bash
git add trading_algo/home/__init__.py trading_algo/home/paths.py trading_algo/home/sync.py tests/test_home_sync.py
git commit -m "feat(home): sync main's book state into a private read-only snapshot"
```

---

### Task 4: Judge the scheduled GitHub jobs

**Files:**
- Create: `trading_algo/home/jobs.py`
- Test: `tests/test_home_jobs.py`

**Interfaces:**
- Consumes: `paths.API_RUNS`.
- Produces:
  - `jobs.JOBS: dict[str, tuple[str, str, int|None, int]]` (workflow name → label, cadence, hour, minute; UTC)
  - `jobs.JobStatus(name, label, state, summary, last_run_at=None, url=None)` — `state` ∈ `ok | running | late | missed | failed | unknown`
  - `jobs.classify(runs: list[dict], now: datetime) -> list[JobStatus]` (pure)
  - `jobs.check(now: datetime|None = None, fetch=None, clock=time.monotonic) -> list[dict]` (cached 300 s; `fetch=None` means the module's `fetch_runs`, looked up at call time; dicts via `dataclasses.asdict`)

- [ ] **Step 1: Write the failing tests** — create `tests/test_home_jobs.py`:

```python
"""Job health against grace periods measured from GitHub's run history."""
import urllib.error
from datetime import datetime, timezone

import pytest

from trading_algo.home import jobs

SHARES, FX, DAY = ("Paper Trade & Publish Dashboard", "FX Paper Trading (live)",
                   "Day Trading Paper (hourly)")


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def run(name, started, conclusion="success", status="completed"):
    return {"name": name, "run_started_at": started.isoformat().replace("+00:00", "Z"),
            "status": status, "conclusion": conclusion,
            "html_url": f"https://github.com/x/runs/{name[:3]}"}


def states(runs, now):
    return {s.name: s.state for s in jobs.classify(runs, now)}


def test_daily_job_that_ran_after_its_slot_is_ok():
    # Fri 9 Oct 21:30 slot, started 22:51; viewed Sat morning.
    got = states([run(SHARES, utc(2026, 10, 9, 22, 51))], utc(2026, 10, 10, 8, 0))
    assert got[SHARES] == "ok"


def test_daily_job_within_grace_is_late_not_missed():
    # Mon 12 Oct 21:30 slot, nothing since Friday, viewed 23:00 (1.5 h later).
    got = states([run(SHARES, utc(2026, 10, 9, 22, 51))], utc(2026, 10, 12, 23, 0))
    assert got[SHARES] == "late"


def test_daily_job_past_grace_is_missed():
    # Monday's slot, viewed Tue 06:30 UTC = 9 h later (> 8 h 30 m).
    got = states([run(SHARES, utc(2026, 10, 9, 22, 51))], utc(2026, 10, 13, 6, 30))
    assert got[SHARES] == "missed"


def test_latest_failed_run_is_failed():
    got = states([run(FX, utc(2026, 10, 9, 23, 40), conclusion="failure")],
                 utc(2026, 10, 10, 8, 0))
    assert got[FX] == "failed"


def test_in_progress_run_is_running():
    got = states([run(FX, utc(2026, 10, 9, 23, 40), conclusion=None,
                      status="in_progress")], utc(2026, 10, 9, 23, 45))
    assert got[FX] == "running"


def test_hourly_job_missed_after_14_hours_on_a_weekday():
    # Mon 12 Oct 15:00, last run Friday: 15 h since UTC midnight.
    got = states([run(DAY, utc(2026, 10, 9, 22, 7))], utc(2026, 10, 12, 15, 0))
    assert got[DAY] == "missed"


def test_hourly_job_with_a_run_today_is_ok():
    got = states([run(DAY, utc(2026, 10, 12, 2, 10))], utc(2026, 10, 12, 15, 0))
    assert got[DAY] == "ok"


def test_hourly_job_is_ok_at_weekends():
    got = states([run(DAY, utc(2026, 10, 9, 20, 7))], utc(2026, 10, 11, 12, 0))
    assert got[DAY] == "ok"


@pytest.fixture()
def fresh_cache(monkeypatch):
    monkeypatch.setattr(jobs, "_cache", {"at": 0.0, "runs": None, "ok_at": None})


def test_unreachable_github_is_unknown_never_ok(fresh_cache):
    def boom():
        raise urllib.error.URLError("offline")
    out = jobs.check(now=utc(2026, 10, 10, 8, 0), fetch=boom)
    assert {j["state"] for j in out} == {"unknown"}
    assert all("unknown since" in j["summary"] for j in out)


def test_http_403_is_unknown_not_ok(fresh_cache):
    def limited():
        raise urllib.error.HTTPError("u", 403, "rate limited", {}, None)
    out = jobs.check(now=utc(2026, 10, 10, 8, 0), fetch=limited)
    assert {j["state"] for j in out} == {"unknown"}


def test_check_caches_for_five_minutes(fresh_cache):
    calls = []
    def fetch():
        calls.append(1)
        return [run(SHARES, utc(2026, 10, 9, 22, 51))]
    # call 1: no cache -> fetch, stamp 1000; call 2: age 100 s -> cached;
    # call 3: age 400 s -> refetch, stamp 1400
    ticks = iter([1000.0, 1100.0, 1400.0, 1400.0])
    clock = lambda: next(ticks)
    now = utc(2026, 10, 10, 8, 0)
    jobs.check(now=now, fetch=fetch, clock=clock)
    jobs.check(now=now, fetch=fetch, clock=clock)
    assert len(calls) == 1
    jobs.check(now=now, fetch=fetch, clock=clock)
    assert len(calls) == 2
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python3 -m pytest tests/test_home_jobs.py -v`
Expected: FAIL with `ImportError: cannot import name 'jobs'`.

- [ ] **Step 3: Implement** — create `trading_algo/home/jobs.py`:

```python
"""Are the three scheduled GitHub jobs healthy?

Reads GitHub's public run list (no login: the repo is public) and judges each
job against grace periods MEASURED from its own history (2026-08-06 →
2026-10-09, 500+ scheduled runs), not guessed:

  * the daily jobs start a median ~80 min after their slot and the worst seen
    was ~8 h, so a daily job is "late" until 8 h 30 m after its slot and only
    then "missed";
  * GitHub skips most of DAYTRADER's hourly runs (2-6 of 23 a day since early
    September) with same-day gaps up to 796 min, so it is "missed" only after
    14 h without a run on a weekday.

A job GitHub can't tell us about is "unknown", never "ok".
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from .paths import API_RUNS

DAILY_GRACE = timedelta(hours=8, minutes=30)
HOURLY_GAP = timedelta(hours=14)
CACHE_SECONDS = 300

# workflow name -> (label, cadence, hour, minute), UTC, weekdays only
JOBS: dict[str, tuple[str, str, int | None, int]] = {
    "Paper Trade & Publish Dashboard": ("Shares (daily)", "daily", 21, 30),
    "FX Paper Trading (live)": ("Currencies (daily)", "daily", 23, 0),
    "Day Trading Paper (hourly)": ("DAYTRADER (hourly)", "hourly", None, 7),
}
_FAILED = {"failure", "timed_out", "startup_failure"}


@dataclass(frozen=True)
class JobStatus:
    name: str
    label: str
    state: str          # ok | running | late | missed | failed | unknown
    summary: str
    last_run_at: str | None = None
    url: str | None = None


def _hm(t: datetime) -> str:
    return t.strftime("%a %H:%M UTC")


def _start(run: dict) -> datetime:
    return datetime.fromisoformat(run["run_started_at"].replace("Z", "+00:00"))


def _last_slot(now: datetime, hour: int, minute: int) -> datetime:
    """The most recent weekday HH:MM UTC at or before `now`."""
    slot = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if slot > now:
        slot -= timedelta(days=1)
    while slot.weekday() >= 5:
        slot -= timedelta(days=1)
    return slot


def classify(runs: list[dict], now: datetime) -> list[JobStatus]:
    out: list[JobStatus] = []
    for name, (label, cadence, hour, minute) in JOBS.items():
        mine = sorted((r for r in runs
                       if r.get("name") == name and r.get("run_started_at")),
                      key=_start, reverse=True)
        latest = mine[0] if mine else None
        last_at = _start(latest) if latest else None
        stamp = last_at.isoformat() if last_at else None
        url = latest.get("html_url") if latest else None
        if latest and latest.get("status") != "completed":
            out.append(JobStatus(name, label, "running", f"{label} is running now",
                                 stamp, url))
            continue
        if latest and latest.get("conclusion") in _FAILED:
            out.append(JobStatus(name, label, "failed",
                                 f"{label} failed at {_hm(last_at)}", stamp, url))
            continue
        if cadence == "daily":
            slot = _last_slot(now, hour, minute)
            if last_at and last_at >= slot - timedelta(minutes=5):
                state, text = "ok", f"{label} ran {_hm(last_at)}"
            elif now - slot > DAILY_GRACE:
                state, text = "missed", f"{label} didn't run for its {_hm(slot)} slot"
            else:
                state, text = "late", (f"{label} due {_hm(slot)} — GitHub often "
                                       "starts it late")
        elif now.weekday() >= 5:
            state, text = "ok", f"{label} doesn't run at weekends"
        else:
            midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
            anchor = max(last_at, midnight) if last_at else midnight
            if now - anchor > HOURLY_GAP:
                state, text = "missed", f"{label} hasn't run for over 14 hours"
            elif last_at:
                state, text = "ok", f"{label} last ran {_hm(last_at)}"
            else:
                state, text = "ok", f"{label} hasn't run yet today"
        out.append(JobStatus(name, label, state, text, stamp, url))
    return out


def fetch_runs(timeout: float = 10.0) -> list[dict]:
    req = urllib.request.Request(
        f"{API_RUNS}?branch=main&event=schedule&per_page=100",
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": "momentum-home"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)["workflow_runs"]


_cache: dict = {"at": 0.0, "runs": None, "ok_at": None}


def check(now: datetime | None = None, fetch=None,
          clock=time.monotonic) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    fetch = fetch or fetch_runs           # looked up at call time, so tests can swap it
    fresh = _cache["runs"] is not None and clock() - _cache["at"] <= CACHE_SECONDS
    if not fresh:
        try:
            runs = fetch()
        except (urllib.error.URLError, OSError, ValueError, KeyError):
            since = _hm(_cache["ok_at"] or now)
            return [asdict(JobStatus(name, label, "unknown",
                                     f"{label}: GitHub status unknown since {since}"))
                    for name, (label, _cad, _h, _m) in JOBS.items()]
        _cache.update(runs=runs, at=clock(), ok_at=now)
    return [asdict(s) for s in classify(_cache["runs"], now)]
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/test_home_jobs.py -v`
Expected: all 11 PASS.

- [ ] **Step 5: Commit**

```bash
git add trading_algo/home/jobs.py tests/test_home_jobs.py
git commit -m "feat(home): judge scheduled jobs against measured grace periods"
```

---

### Task 5: Remember which problems Matt has seen

**Files:**
- Create: `trading_algo/home/seen.py`
- Test: `tests/test_home_seen.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `seen.key(f: dict) -> str` — `"{book}|{code}|{subject or ''}"`
  - `seen.begin_session(path: Path, now: datetime) -> str | None` — records `now` as `last_opened`, returns the previous `last_opened` (None on first run)
  - `seen.classify(findings: list[dict], *, path: Path, session_start: str, first_run: bool, now: datetime) -> list[dict]` — returns copies of the findings with `"new": bool`; persists the store. Each finding dict has keys `book, code, level, subject, message`.
  - Store file: `{"v": 1, "last_opened": iso|None, "first_seen": {key: {"at": iso, "level": str, "baseline": bool}}}`

- [ ] **Step 1: Write the failing tests** — create `tests/test_home_seen.py`:

```python
"""'New' means first observed in this app session; losing the file never
floods the screen with amber."""
import json
from datetime import datetime, timedelta, timezone

from trading_algo.home import seen

T0 = datetime(2026, 10, 10, 8, 0, tzinfo=timezone.utc)


def f(code, level="WARN", subject=None, message="m", book="FULL"):
    return {"book": book, "code": code, "level": level, "subject": subject,
            "message": message}


def session(path, now, findings):
    previous = seen.begin_session(path, now)
    return seen.classify(findings, path=path, session_start=now.isoformat(),
                         first_run=previous is None, now=now)


def news(out):
    return {x["code"] for x in out if x["new"]}


def test_first_run_records_everything_as_known(tmp_path):
    out = session(tmp_path / "seen.json", T0, [f("idle-sleeve", subject="ASX")])
    assert news(out) == set()


def test_new_finding_is_new_in_its_session_and_known_after(tmp_path):
    p = tmp_path / "seen.json"
    session(p, T0, [f("idle-sleeve", subject="ASX")])
    s2 = T0 + timedelta(days=1)
    out = session(p, s2, [f("idle-sleeve", subject="ASX"), f("cash-drift", subject="US")])
    assert news(out) == {"cash-drift"}
    # a second refresh in the same session: still new
    again = seen.classify([f("cash-drift", subject="US")], path=p,
                          session_start=s2.isoformat(), first_run=False,
                          now=s2 + timedelta(minutes=5))
    assert news(again) == {"cash-drift"}
    # next session: known
    out3 = session(p, s2 + timedelta(days=1), [f("cash-drift", subject="US")])
    assert news(out3) == set()


def test_changed_wording_stays_known(tmp_path):
    p = tmp_path / "seen.json"
    session(p, T0, [f("idle-sleeve", subject="ASX", message="in cash 77 days")])
    out = session(p, T0 + timedelta(days=1),
                  [f("idle-sleeve", subject="ASX", message="in cash 78 days")])
    assert news(out) == set()


def test_same_code_other_subject_is_new(tmp_path):
    p = tmp_path / "seen.json"
    session(p, T0, [f("idle-sleeve", subject="ASX")])
    out = session(p, T0 + timedelta(days=1), [f("idle-sleeve", subject="FTSE")])
    assert news(out) == {"idle-sleeve"}


def test_escalation_makes_a_known_finding_new(tmp_path):
    p = tmp_path / "seen.json"
    session(p, T0, [f("never-traded", level="WARN", subject="ASX")])
    out = session(p, T0 + timedelta(days=1),
                  [f("never-traded", level="ERROR", subject="ASX")])
    assert news(out) == {"never-traded"}


def test_info_is_never_new(tmp_path):
    p = tmp_path / "seen.json"
    session(p, T0, [])
    out = session(p, T0 + timedelta(days=1), [f("turnover", level="INFO")])
    assert news(out) == set()


def test_unreadable_file_is_set_aside_and_floods_nothing(tmp_path):
    p = tmp_path / "seen.json"
    session(p, T0, [f("idle-sleeve", subject="ASX")])
    p.write_text("{not json")
    out = session(p, T0 + timedelta(days=1), [f("cash-drift", subject="US")])
    assert news(out) == set()
    assert (tmp_path / "seen.json.unreadable").exists()
    assert json.loads(p.read_text())["v"] == 1
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python3 -m pytest tests/test_home_seen.py -v`
Expected: FAIL with `ImportError: cannot import name 'seen'`.

- [ ] **Step 3: Implement** — create `trading_algo/home/seen.py`:

```python
"""Which audit problems Matt has already seen.

A WARN/ERROR finding is NEW during the app session in which it is first
observed (or in which it became more severe) and KNOWN in every later session.
Identity is (book, code, subject) — never the message, whose wording changes
daily ("77 days" -> "78 days"). On the first run, or when the file is missing
or unreadable, everything current is recorded as known: losing this file must
never flood the screen with amber. INFO findings are never tracked.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

VERSION = 1
_TRACKED = ("WARN", "ERROR")
_RANK = {"INFO": 0, "WARN": 1, "ERROR": 2}


def key(f: dict) -> str:
    return f"{f['book']}|{f['code']}|{f.get('subject') or ''}"


def _set_aside(path: Path) -> None:
    try:
        os.replace(path, path.with_name(path.name + ".unreadable"))
    except OSError:
        pass


def _load(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        _set_aside(path)
        return None
    if not isinstance(data, dict) or data.get("v") != VERSION:
        _set_aside(path)
        return None
    data.setdefault("first_seen", {})
    return data


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
    os.replace(tmp, path)


def begin_session(path: Path, now: datetime) -> str | None:
    data = _load(path) or {"v": VERSION, "first_seen": {}}
    previous = data.get("last_opened")
    data["last_opened"] = now.isoformat()
    _save(path, data)
    return previous


def classify(findings: list[dict], *, path: Path, session_start: str,
             first_run: bool, now: datetime) -> list[dict]:
    data = _load(path)
    baseline = first_run or data is None
    if data is None:
        data = {"v": VERSION, "first_seen": {}, "last_opened": session_start}
    first_seen = data["first_seen"]
    stamp = now.isoformat()
    out: list[dict] = []
    for finding in findings:
        g = dict(finding)
        g["new"] = False
        if finding["level"] in _TRACKED:
            k = key(finding)
            prev = first_seen.get(k)
            if prev is None:
                first_seen[k] = {"at": stamp, "level": finding["level"],
                                 "baseline": baseline}
                g["new"] = not baseline
            elif _RANK[finding["level"]] > _RANK.get(prev["level"], 0):
                first_seen[k] = {"at": stamp, "level": finding["level"],
                                 "baseline": False}
                g["new"] = True
            else:
                g["new"] = (not prev.get("baseline")) and prev["at"] >= session_start
        out.append(g)
    _save(path, data)
    return out
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/test_home_seen.py -v`
Expected: all 7 PASS.

- [ ] **Step 5: Commit**

```bash
git add trading_algo/home/seen.py tests/test_home_seen.py
git commit -m "feat(home): remember seen problems so only new ones turn amber"
```

---

### Task 6: The `/api/home` payload

**Files:**
- Create: `trading_algo/dashboard/home.py`
- Test: `tests/test_home_api.py`, `tests/test_home_attention.py`

**Interfaces:**
- Consumes: `overview.build_overview()` (cards with `off_peak`, `breaker` from Task 2), `overview._load(entry)`, `overview._num(v)`, `registry.discover_accounts()`, `verify.verify_all(today=naive_utc)` (findings with `detail["subject"]` from Task 1), `seen.classify(...)` (Task 5).
- Produces:
  - `home.PERIODS = ("1D", "1W", "1M", "ALL")`, `home.NEAR_BREAKER = 0.6`
  - `home.period_change(marks: list[tuple[datetime, float]], initial: float, period: str, hourly: bool) -> dict` — `{"measurable": False}` or `{"measurable": True, "start", "end", "change", "pct"}`
  - `home.freshness(as_of: datetime, now: datetime, hourly: bool) -> str` — `fresh | late | stale`
  - `home.attention(books: list[dict], findings: list[dict], jobs: list[dict]) -> dict` — `{"verdict": "act"|"look"|"clear"|"nodata", "jobs_unknown": bool, "items": [...], "known": [...], "notes": [...]}`; item = `{"level": "act"|"look"|"known"|"note", "book": key|None, "text": str, "url": str|None}`
  - `home.build_home(*, jobs, seen_path, session_start, first_run, last_opened, now, snapshot) -> dict` — the JSON payload: `v, generated_at, snapshot, last_opened, verdict, jobs_unknown, attention, known, notes, groups, books, jobs`. Book: `key, account, name, kind, group, equity, initial, hourly, as_of, freshness, off_peak, breaker, near_breaker, halted, status, periods{P: change}, series[[date, pnl]]` (P&L in A$ = last mark of the day − capital). Group: `name, aum, initial, books, periods{P: {"measurable", "change", "pct", "measured", "of"}}, series[[date, pnl]]`.

- [ ] **Step 1: Write the failing tests** — create `tests/test_home_api.py`:

```python
"""/api/home: every number traced to an existing source, period maths
hand-checked, and the failure cases a person would hit."""
import json
from datetime import datetime, timezone

import pytest

import trading_algo.paper_trade as pt
from trading_algo.dashboard import home, overview
from trading_algo.forex import fx_book

NOW = datetime(2026, 10, 10, 8, 0, tzinfo=timezone.utc)          # a Saturday
SESSION = NOW.isoformat()

DAY_STATE = {
    "account": "daytrader", "currency": "AUD", "profile": "intraday", "bar": "60m",
    "source": "yahoo", "symbols": ["EURUSD"], "initial_capital": 10_000.0,
    "equity": 9_930.0, "peak_equity": 10_100.0, "positions": {}, "last_close": {},
    "last_bar_date": "2026-10-09 02:00", "risk_halted": False, "halt_cooldown": 0,
    "trades": [], "decisions": {},
    "equity_history": [["2026-10-08 15:00", 9_900.0], ["2026-10-08 21:00", 9_950.0],
                       ["2026-10-09 02:00", 9_930.0]],
}


@pytest.fixture()
def books(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    monkeypatch.setenv("MOMENTUM_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("FX_STATE_DIR", str(tmp_path))
    pt.init_account("full", capital=100_000, synthetic=True)
    pt.run_daily("full", synthetic=True)          # a real, evaluated book
    path = tmp_path / "paper_state_full.json"
    state = json.loads(path.read_text())
    state["equity_history"] = [["2026-09-01", 100_000.0], ["2026-09-25", 101_000.0],
                               ["2026-10-01", 102_000.0], ["2026-10-08", 101_500.0],
                               ["2026-10-09", 102_500.0]]
    state["peak_equity_base"] = 103_000.0
    path.write_text(json.dumps(state))
    (tmp_path / "fx_state_daytrader.json").write_text(json.dumps(DAY_STATE))
    return tmp_path


def build(tmp_path, jobs=()):
    return home.build_home(jobs=list(jobs), seen_path=tmp_path / "seen.json",
                           session_start=SESSION, first_run=True, last_opened=None,
                           now=NOW, snapshot={"sha": "abc", "offline": False})


def test_group_totals_equal_overview(books):
    out = build(books)
    ov = overview.build_overview()
    assert [(g["name"], g["aum"]) for g in out["groups"]] == \
           [(g["name"], g["aum"]) for g in ov["groups"]]


def test_daily_book_period_changes(books):
    full = next(b for b in build(books)["books"] if b["key"] == "FULL")
    p = full["periods"]
    assert (p["1D"]["change"], p["1D"]["pct"]) == (1000.0, 0.009852)   # 102.5k vs 101.5k
    assert (p["1W"]["change"], p["1W"]["pct"]) == (500.0, 0.004902)    # vs 1 Oct
    assert (p["1M"]["change"], p["1M"]["pct"]) == (2500.0, 0.025)      # vs 1 Sep
    assert (p["ALL"]["change"], p["ALL"]["pct"]) == (2500.0, 0.025)    # vs capital


def test_hourly_book_1d_is_its_last_session(books):
    day = next(b for b in build(books)["books"] if b["key"] == "DAY")
    assert day["hourly"] is True
    assert (day["periods"]["1D"]["change"], day["periods"]["1D"]["pct"]) == (-20.0, -0.00201)
    assert day["periods"]["1W"] == {"measurable": False}               # history too short
    assert day["periods"]["ALL"]["change"] == -70.0


def test_group_period_counts_only_measured_books(books):
    core = next(g for g in build(books)["groups"] if g["name"] == "CORE")
    assert core["periods"]["1D"] == {"measurable": True, "change": 980.0,
                                     "pct": 0.008793, "measured": 2, "of": 2}
    one_week = core["periods"]["1W"]
    assert (one_week["change"], one_week["measured"], one_week["of"]) == (500.0, 1, 2)


def test_books_carry_as_of_and_freshness(books):
    by = {b["key"]: b for b in build(books)["books"]}
    assert by["FULL"]["as_of"].startswith("2026-10-09")
    assert by["FULL"]["freshness"] == "fresh" and by["DAY"]["freshness"] == "fresh"


def test_near_breaker_uses_the_breaker_figure(books):
    full = next(b for b in build(books)["books"] if b["key"] == "FULL")
    assert full["off_peak"] == round(102_500 / 103_000 - 1, 6)
    assert full["near_breaker"] is False


def test_damaged_book_file_is_act_now(books):
    before = build(books)
    assert not any("could not be parsed" in i["text"] for i in before["attention"])
    (books / "paper_state_broken.json").write_text("{not json")
    out = build(books)
    assert out["verdict"] == "act"
    assert any(i["level"] == "act" and "could not be parsed" in i["text"]
               for i in out["attention"])


def test_no_books_is_nodata_not_clear(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    monkeypatch.setenv("MOMENTUM_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("FX_STATE_DIR", str(tmp_path))
    out = build(tmp_path)
    assert out["verdict"] == "nodata" and out["books"] == []


def test_unvaluable_marks_are_skipped():
    t = lambda d: datetime.fromisoformat(d).replace(tzinfo=timezone.utc)
    marks = [(t("2026-10-07"), 100.0), (t("2026-10-09"), 110.0)]
    assert home.period_change(marks, 100.0, "1D", False)["change"] == 10.0
    assert home._marks({"equity_history": [["2026-10-08", None],
                                           ["2026-10-09", float("nan")],
                                           ["2026-10-10", 5.0]]})[-1][1] == 5.0
    assert len(home._marks({"equity_history": [["2026-10-08", None]]})) == 0


@pytest.mark.parametrize("as_of,now,hourly,expect", [
    ("2026-10-09 00:00", "2026-10-10 08:00", False, "fresh"),   # Fri mark, Sat
    ("2026-10-09 00:00", "2026-10-12 08:00", False, "fresh"),   # 1 weekday
    ("2026-10-09 00:00", "2026-10-13 08:00", False, "late"),    # 2 weekdays
    ("2026-10-09 00:00", "2026-10-14 08:00", False, "stale"),   # 3 weekdays
    ("2026-10-09 22:07", "2026-10-12 03:00", True, "fresh"),    # Monday before 1st run
    ("2026-10-09 22:07", "2026-10-12 15:00", True, "stale"),    # 15 h since midnight
    ("2026-10-09 22:00", "2026-10-10 12:00", True, "fresh"),    # weekend, Friday mark
    ("2026-10-08 22:00", "2026-10-10 12:00", True, "stale"),    # weekend, Thursday mark
])
def test_freshness(as_of, now, hourly, expect):
    t = lambda s: datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    assert home.freshness(t(as_of), t(now), hourly) == expect


def test_mark_dated_ahead_of_now_is_fresh():
    t = lambda s: datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    assert home.freshness(t("2026-10-13 00:00"), t("2026-10-12 22:00"), False) == "fresh"
```

create `tests/test_home_attention.py`:

```python
"""The one verdict: act now / look soon / clear — quiet unless broken or new."""
from trading_algo.dashboard import home


def book(key="FULL", halted=False, near=False, off_peak=-0.01, breaker=0.25):
    return {"key": key, "name": key, "halted": halted, "near_breaker": near,
            "off_peak": off_peak, "breaker": breaker}


def finding(level, new=False, book="FULL", message="m"):
    return {"book": book, "code": "c", "level": level, "subject": None,
            "message": message, "new": new}


def job(state, summary="s"):
    return {"name": "n", "label": "l", "state": state, "summary": summary,
            "url": "https://example/run"}


def verdict(books=(), findings=(), jobs=()):
    return home.attention(list(books), list(findings), list(jobs))


def test_quiet_day_is_clear():
    out = verdict([book()], [finding("WARN"), finding("INFO")], [job("ok")])
    assert out["verdict"] == "clear"
    assert len(out["known"]) == 1 and len(out["notes"]) == 1 and out["items"] == []


def test_halted_book_is_act_now():
    assert verdict([book(halted=True)])["verdict"] == "act"


def test_known_error_is_still_act_now():
    assert verdict([book()], [finding("ERROR", new=False)])["verdict"] == "act"


def test_new_warning_is_look_soon():
    assert verdict([book()], [finding("WARN", new=True)])["verdict"] == "look"


def test_near_breaker_is_look_soon():
    out = verdict([book(near=True, off_peak=-0.16)])
    assert out["verdict"] == "look"
    assert "16.0%" in out["items"][0]["text"] and "25%" in out["items"][0]["text"]


def test_failed_or_missed_job_is_act_now_with_a_link():
    out = verdict([book()], [], [job("missed", "Shares (daily) didn't run")])
    assert out["verdict"] == "act"
    assert out["items"][0]["url"] == "https://example/run"


def test_unknown_jobs_are_flagged_not_green_and_not_alarming():
    out = verdict([book()], [], [job("unknown")])
    assert out["verdict"] == "clear" and out["jobs_unknown"] is True


def test_no_books_and_no_findings_is_nodata():
    assert verdict([], [], [job("ok")])["verdict"] == "nodata"
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python3 -m pytest tests/test_home_api.py tests/test_home_attention.py -v`
Expected: FAIL with `ImportError: cannot import name 'home' from 'trading_algo.dashboard'`.

- [ ] **Step 3: Implement** — create `trading_algo/dashboard/home.py`:

```python
"""The Mac home screen's one API: "am I up, and is anything wrong?".

Every figure is read from an existing source rather than recomputed: totals
and each book's fall from its stored peak (the breaker's own figure) come from
`overview`, problems come from `verify`, job health from
`trading_algo.home.jobs`. What this module adds is only (a) the change between
two saved equity marks over a period and (b) the rules for what deserves
attention. No drawdown, Sharpe or annualisation is computed here
(tests/test_metrics_single_source.py).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .. import verify
from ..home import seen
from . import overview, registry

PERIODS = ("1D", "1W", "1M", "ALL")
_PERIOD_DAYS = {"1W": 7, "1M": 30}
NEAR_BREAKER = 0.6                      # spec Q-1 (draft)
HOURLY_FRESH = timedelta(hours=14)      # measured: longest same-day gap 796 min


def _ts(stamp: str) -> datetime:
    """'2026-10-09' or '2026-10-09 02:00' -> aware UTC datetime."""
    s = str(stamp)[:16]
    fmt = "%Y-%m-%d %H:%M" if " " in s else "%Y-%m-%d"
    return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)


def _marks(state: dict) -> list[tuple[datetime, float]]:
    """Saved equity marks, oldest first; unvaluable (None/NaN) marks dropped."""
    out = []
    for stamp, value in state.get("equity_history") or []:
        v = overview._num(value)
        if v is not None:
            out.append((_ts(stamp), v))
    out.sort(key=lambda m: m[0])
    return out


def period_change(marks: list[tuple[datetime, float]], initial: float,
                  period: str, hourly: bool) -> dict:
    """The latest mark against the period's starting value.

    1D    the last completed session: the previous mark for a daily book; for
          the hourly book, its last mark of an earlier UTC day.
    1W/1M the last mark on or before 7 / 30 days before the latest mark.
    ALL   against the capital put in.
    """
    if not marks:
        return {"measurable": False}
    end_t, end_v = marks[-1]
    start = None
    if period == "ALL":
        start = initial or None
    elif period == "1D":
        if hourly:
            earlier = [v for t, v in marks if t.date() < end_t.date()]
            start = earlier[-1] if earlier else None
        elif len(marks) > 1:
            start = marks[-2][1]
    else:
        cutoff = end_t - timedelta(days=_PERIOD_DAYS[period])
        before = [v for t, v in marks if t <= cutoff]
        start = before[-1] if before else None
    if not start or start <= 0:
        return {"measurable": False}
    return {"measurable": True, "start": round(start, 2), "end": round(end_v, 2),
            "change": round(end_v - start, 2), "pct": round(end_v / start - 1.0, 6)}


def _weekdays_between(a, b) -> int:
    """Weekdays in (a, b] — how many trading days a daily book has missed."""
    n, d = 0, a
    while d < b:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def freshness(as_of: datetime, now: datetime, hourly: bool) -> str:
    if hourly:
        if now.weekday() >= 5:
            friday = now.date() - timedelta(days=now.weekday() - 4)
            return "fresh" if as_of.date() >= friday else "stale"
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return "fresh" if now - max(as_of, midnight) <= HOURLY_FRESH else "stale"
    age = _weekdays_between(as_of.date(), now.date())
    return "fresh" if age <= 1 else "late" if age == 2 else "stale"


def _daily(marks):
    daily: dict[str, float] = {}
    for t, v in marks:
        daily[t.date().isoformat()] = round(v, 2)
    return sorted(daily.items())


def _book(entry: dict, state: dict, card: dict, now: datetime) -> dict:
    marks = _marks(state)
    hourly = entry["kind"] == "fx" and str(state.get("bar") or "1d") != "1d"
    breaker = card.get("breaker")
    off_peak = card.get("off_peak", 0.0)
    return {
        "key": card["key"], "account": entry["account"], "name": card["name"],
        "kind": card["kind"], "group": card["group"], "equity": card["equity"],
        "initial": card["initial"], "hourly": hourly,
        "as_of": marks[-1][0].isoformat() if marks else None,
        "freshness": freshness(marks[-1][0], now, hourly) if marks else "stale",
        "off_peak": off_peak, "breaker": breaker,
        "near_breaker": breaker is not None and off_peak <= -NEAR_BREAKER * breaker,
        "halted": card["halted"], "status": card["status"],
        "periods": {p: period_change(marks, card["initial"], p, hourly) for p in PERIODS},
        # P&L in A$ per day (last mark of the day minus capital) — computed here so
        # the page never does money arithmetic of its own (G-4).
        "series": [[d, round(v - card["initial"], 2)] for d, v in _daily(marks)],
        "_marks": marks,
    }


def _aggregate(members: list[dict], period: str) -> dict:
    measured = [b["periods"][period] for b in members if b["periods"][period]["measurable"]]
    if not measured:
        return {"measurable": False, "measured": 0, "of": len(members)}
    start = sum(m["start"] for m in measured)
    change = sum(m["change"] for m in measured)
    return {"measurable": True, "change": round(change, 2),
            "pct": round(change / start, 6) if start else 0.0,
            "measured": len(measured), "of": len(members)}


def _pnl_series(members: list[dict]) -> list[list]:
    """Combined P&L in A$ per day: each book's last mark of the day minus its
    capital, carried forward; zero before the book started."""
    per_book = [{d: v - b["initial"] for d, v in _daily(b["_marks"])} for b in members]
    days = sorted(set().union(*per_book)) if per_book else []
    last = [0.0] * len(per_book)
    out = []
    for d in days:
        for i, daily in enumerate(per_book):
            if d in daily:
                last[i] = daily[d]
        out.append([d, round(sum(last), 2)])
    return out


def _findings(books: list[dict], now: datetime) -> list[dict]:
    keys = {(b["kind"], b["account"]): b["key"] for b in books}
    today = now.astimezone(timezone.utc).replace(tzinfo=None)
    out = []
    for book_id, found in verify.verify_all(today=today).items():
        kind, account = book_id.split(":", 1)
        for f in found:
            out.append({"book": keys.get((kind, account), account.upper()),
                        "code": f.code, "level": f.level,
                        "subject": (f.detail or {}).get("subject"),
                        "message": f.message})
    return out


def attention(books: list[dict], findings: list[dict], jobs: list[dict]) -> dict:
    act, look, known, notes = [], [], [], []
    for b in books:
        if b["halted"]:
            act.append({"level": "act", "book": b["key"], "url": None,
                        "text": f"{b['name']} is halted by its safety breaker"})
        elif b["near_breaker"]:
            look.append({"level": "look", "book": b["key"], "url": None,
                         "text": (f"{b['name']} is {abs(b['off_peak']):.1%} below its "
                                  f"peak — its safety breaker trips at "
                                  f"{b['breaker']:.0%}")})
    for f in findings:
        item = {"book": f["book"], "text": f["message"], "url": None}
        if f["level"] == "ERROR":
            act.append({"level": "act", **item})
        elif f["level"] == "WARN":
            (look if f["new"] else known).append(
                {"level": "look" if f["new"] else "known", **item})
        else:
            notes.append({"level": "note", **item})
    for j in jobs:
        if j["state"] in ("failed", "missed"):
            act.append({"level": "act", "book": None, "text": j["summary"],
                        "url": j.get("url")})
    if not books and not act:
        return {"verdict": "nodata", "jobs_unknown": any(j["state"] == "unknown" for j in jobs),
                "items": [{"level": "look", "book": None, "url": None,
                           "text": "No book data yet — connect to the internet and refresh (⌘R)"}],
                "known": known, "notes": notes}
    verdict = "act" if act else "look" if look else "clear"
    return {"verdict": verdict,
            "jobs_unknown": any(j["state"] == "unknown" for j in jobs),
            "items": act + look, "known": known, "notes": notes}


def build_home(*, jobs: list[dict], seen_path, session_start: str, first_run: bool,
               last_opened: str | None, now: datetime, snapshot: dict) -> dict:
    ov = overview.build_overview()
    cards = {c["key"]: c for c in ov["accounts"]}
    books = []
    for entry in registry.discover_accounts():
        state = overview._load(entry)
        card = cards.get(entry["key"])
        if state is not None and card is not None:
            books.append(_book(entry, state, card, now))
    groups = []
    for g in ov["groups"]:
        members = [b for b in books if b["group"] == g["name"]]
        groups.append({"name": g["name"], "aum": g["aum"], "initial": g["initial"],
                       "books": len(members),
                       "periods": {p: _aggregate(members, p) for p in PERIODS},
                       "series": _pnl_series(members)})
    findings = seen.classify(_findings(books, now), path=seen_path,
                             session_start=session_start, first_run=first_run, now=now)
    att = attention(books, findings, jobs)
    for b in books:
        del b["_marks"]
    return {"v": 1, "generated_at": now.isoformat(), "snapshot": snapshot,
            "last_opened": last_opened, "verdict": att["verdict"],
            "jobs_unknown": att["jobs_unknown"], "attention": att["items"],
            "known": att["known"], "notes": att["notes"],
            "groups": groups, "books": books, "jobs": jobs}
```

- [ ] **Step 4: Run the tests (and the measurement guard)**

Run: `python3 -m pytest tests/test_home_api.py tests/test_home_attention.py tests/test_metrics_single_source.py -v`
Expected: all PASS. If `test_damaged_book_file_is_act_now` fails because `verify.discover` reads a different directory, confirm the fixture's `MOMENTUM_STATE_DIR` env is set (verify reads the env on every call, `verify.py:_state_dir`).

- [ ] **Step 5: Commit**

```bash
git add trading_algo/dashboard/home.py tests/test_home_api.py tests/test_home_attention.py
git commit -m "feat(home): /api/home payload — periods, freshness, attention verdict"
```

---

### Task 7: Serve `/home` and `/api/home`

**Files:**
- Modify: `trading_algo/dashboard/server.py` (`make_handler`, `do_GET`, `create_server`)
- Test: `tests/test_home_server.py`

**Interfaces:**
- Consumes: a `home` callable `(sync_now: bool = False) -> dict` (built by the launcher in Task 9).
- Produces: `server.create_server(account="main", synthetic=False, host="127.0.0.1", port=8787, home=None)`; `make_handler(account, synthetic, home=None)`. Routes: `GET /home` → `static/home.html`; `GET /api/home[?sync=1]` → `home(sync_now=...)` as JSON, 404 when `home` is None, 500 (generic body) on an exception.

- [ ] **Step 1: Write the failing test** — create `tests/test_home_server.py`:

```python
"""The server serves the home page and its API only when a home callable is wired."""
import json
import threading
import urllib.error
import urllib.request

import pytest

from trading_algo.dashboard import server


@pytest.fixture()
def serve():
    started = []

    def _start(home=None):
        httpd = server.create_server("main", False, "127.0.0.1", 0, home=home)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        started.append(httpd)
        return f"http://127.0.0.1:{httpd.server_address[1]}"
    yield _start
    for h in started:
        h.shutdown()
        h.server_close()


def get(url):
    with urllib.request.urlopen(url) as r:
        return r.status, r.headers.get("Content-Type"), r.read().decode()


def test_home_page_is_served(serve):
    status, ctype, body = get(serve() + "/home")
    assert status == 200 and ctype.startswith("text/html")
    assert 'src="home.js"' in body and 'href="home.css"' in body


def test_api_home_calls_the_wired_builder(serve):
    calls = []

    def home(sync_now=False):
        calls.append(sync_now)
        return {"v": 1, "verdict": "clear"}
    base = serve(home)
    assert json.loads(get(base + "/api/home")[2]) == {"v": 1, "verdict": "clear"}
    get(base + "/api/home?sync=1")
    assert calls == [False, True]


def test_api_home_is_404_when_not_wired(serve):
    with pytest.raises(urllib.error.HTTPError) as err:
        get(serve() + "/api/home")
    assert err.value.code == 404


def test_terminal_still_served_at_root(serve):
    status, ctype, body = get(serve() + "/")
    assert status == 200 and "home.js" not in body
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m pytest tests/test_home_server.py -v`
Expected: FAIL with `TypeError: create_server() got an unexpected keyword argument 'home'`.

- [ ] **Step 3: Implement** — in `trading_algo/dashboard/server.py`:

add to the imports:
```python
from urllib.parse import parse_qs, urlsplit
```
change the handler factory signature:
```python
def make_handler(account: str, synthetic: bool, home=None):
```
at the top of `do_GET`, directly after `path = self.path.split("?", 1)[0]`, add:
```python
            if path == "/api/home":
                if home is None:
                    self._json(404, {"error": "home screen not enabled"})
                    return
                query = parse_qs(urlsplit(self.path).query)
                try:
                    self._json(200, home(sync_now=query.get("sync") == ["1"]))
                except Exception as exc:  # surface server-side, never leak
                    print(f"[dashboard] error building home: {exc!r}", file=sys.stderr)
                    traceback.print_exc()
                    self._json(500, {"error": "internal server error"})
                return
```
change the static mapping line to:
```python
            rel = ("index.html" if path in ("/", "")
                   else "home.html" if path == "/home" else path.lstrip("/"))
```
and `create_server`:
```python
def create_server(account: str = "main", synthetic: bool = False,
                  host: str = "127.0.0.1", port: int = 8787,
                  home=None) -> ThreadingHTTPServer:
    """Bind and return a (not-yet-serving) server. Pass port=0 for an OS-assigned
    free port (read it back from `.server_address[1]`). `home`, if given, is the
    Mac home screen's payload builder, served at /api/home."""
    return ThreadingHTTPServer((host, port), make_handler(account, synthetic, home))
```

Also create the page itself, `trading_algo/dashboard/static/home.html` (final markup; Task 8 adds the CSS and JS it links to):

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Momentum</title>
<link rel="stylesheet" href="home.css">
</head>
<body>
<div class="app">
  <nav id="sidebar" class="sidebar" aria-label="Books"></nav>
  <main id="main" class="main" aria-live="polite"><p class="muted">Loading your books…</p></main>
</div>
<script src="home.js"></script>
</body>
</html>
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/test_home_server.py tests/test_dashboard_terminal.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add trading_algo/dashboard/server.py trading_algo/dashboard/static/home.html tests/test_home_server.py
git commit -m "feat(dashboard): serve /home and /api/home when a builder is wired"
```

---

### Task 8: The Midnight home screen

**Files:**
- Create: `trading_algo/dashboard/static/home.css`, `trading_algo/dashboard/static/home.js` (`home.html` already exists from Task 7 — no change)
- Test: `tests/test_home_ui.py`

**Interfaces:**
- Consumes: the `/api/home` payload (Task 6) and `/api/home?sync=1` (Task 7).
- Produces: the page. No other code depends on it.

- [ ] **Step 1: Write the failing test** — create `tests/test_home_ui.py`:

```python
"""The page's assets exist, parse, and never do money arithmetic of their own."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "trading_algo" / "dashboard" / "static"


def test_assets_exist_and_are_linked():
    html = (STATIC / "home.html").read_text()
    assert 'href="home.css"' in html and 'src="home.js"' in html
    assert (STATIC / "home.css").stat().st_size > 0


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_home_js_parses():
    done = subprocess.run(["node", "--check", str(STATIC / "home.js")],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_js_takes_numbers_from_the_api_not_its_own_maths():
    """G-4: one source per number. The page may format and slice, but must not
    total or divide money (that lives in dashboard/home.py)."""
    js = (STATIC / "home.js").read_text()
    assert not re.search(r"\.reduce\(", js)
    assert "/ start" not in js and "- start" not in js
    assert "- b.initial" not in js and "- g.initial" not in js
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m pytest tests/test_home_ui.py -v`
Expected: FAIL — `home.css` missing.

- [ ] **Step 3: Implement**

`trading_algo/dashboard/static/home.css`:
```css
/* Momentum home screen — Midnight. Colours are named by role so a light theme
   can swap the values later. Status is never colour alone: shape + word. */
:root {
  --bg: #0a0d14; --glow: #1b2a4a; --panel: rgba(255,255,255,.035);
  --line: rgba(255,255,255,.16); --sidebar: #0e121b;
  --text: #e7ecf5; --muted: #8a93a6; --up: #34d399; --down: #fb7185;
  --look: #fbbf24; --act: #fb7185; --clear: #34d399;
  --focus: #9db7ff; --select: #7aa2ff;
}
* { box-sizing: border-box; }
html, body { margin: 0; height: 100%; color: var(--text);
  font: 13px/1.5 -apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui, sans-serif;
  font-variant-numeric: tabular-nums; }
body { background: radial-gradient(1200px 420px at 30% -10%, var(--glow) 0%, transparent 60%), var(--bg); }
a { color: var(--focus); }
.app { display: grid; grid-template-columns: 250px 1fr; height: 100vh; }
.sidebar { background: var(--sidebar); border-right: 1px solid var(--line); padding: 14px 10px;
  overflow-y: auto; display: flex; flex-direction: column; gap: 2px; }
.sidebar h2 { font-size: 11px; letter-spacing: .06em; text-transform: uppercase; color: var(--muted);
  margin: 14px 8px 4px; font-weight: 600; }
.row { display: flex; align-items: center; gap: 8px; width: 100%; padding: 6px 8px; border: 0;
  border-left: 3px solid transparent; border-radius: 7px; background: none; color: var(--text);
  font: inherit; text-align: left; cursor: pointer; text-decoration: none; }
.row:hover { background: rgba(255,255,255,.05); }
.row[aria-current="true"] { background: rgba(122,162,255,.16); border-left-color: var(--select); }
.row .chg { margin-left: auto; white-space: nowrap; }
.row .fresh { color: var(--look); font-size: 11px; }
:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
.main { overflow-y: auto; padding: 22px 28px 40px; }
.pill { display: inline-flex; gap: 8px; align-items: center; padding: 4px 12px; border-radius: 999px;
  font-weight: 600; border: 1px solid currentColor; }
.pill.act { color: var(--act); } .pill.look { color: var(--look); }
.pill.clear { color: var(--clear); } .pill.nodata { color: var(--muted); }
.hero { font-size: 40px; font-weight: 300; letter-spacing: -.02em; margin: 14px 0 2px; }
.hero small { font-size: 20px; }
.up { color: var(--up); } .down { color: var(--down); } .flat, .muted { color: var(--muted); }
.balance { font-size: 15px; color: var(--muted); }
.chips { display: flex; gap: 6px; margin: 16px 0 8px; }
.chip { font: inherit; color: var(--text); background: var(--panel); border: 1px solid var(--line);
  border-radius: 8px; padding: 4px 12px; cursor: pointer; }
.chip[aria-pressed="true"] { background: rgba(122,162,255,.22); border-color: var(--select); }
.panel { background: var(--panel); border: 1px solid var(--line); border-radius: 12px;
  padding: 14px 16px; margin-top: 14px; }
.panel h3 { margin: 0 0 8px; font-size: 13px; }
.items { list-style: none; margin: 0; padding: 0; }
.items li { padding: 6px 0; border-top: 1px solid rgba(255,255,255,.06); }
.items li:first-child { border-top: 0; }
.tag { font-weight: 600; margin-right: 6px; }
.tag.act { color: var(--act); } .tag.look { color: var(--look); }
.chart svg { width: 100%; height: 160px; display: block; }
.caption { color: var(--muted); font-size: 12px; display: flex; justify-content: space-between; gap: 12px; }
.banner { border: 1px solid var(--look); color: var(--look); border-radius: 10px; padding: 8px 12px;
  margin-bottom: 12px; }
.jobs { display: flex; gap: 16px; flex-wrap: wrap; color: var(--muted); }
.jobs .bad { color: var(--act); } .jobs .unk { color: var(--look); }
details summary { cursor: pointer; color: var(--muted); margin-top: 6px; }
.foot { margin-top: 18px; color: var(--muted); font-size: 12px; }
@media (prefers-reduced-motion: reduce) { * { transition: none !important; animation: none !important; } }
@media (prefers-contrast: more) { :root { --muted: #b4bccb; --line: rgba(255,255,255,.4); } }
```

`trading_algo/dashboard/static/home.js`:
```js
// Momentum home screen: "am I up, and is anything wrong?".
// Every number comes from /api/home; this file only lays it out.
"use strict";

const PERIODS = ["1D", "1W", "1M", "ALL"];
const SINCE = {"1D": "in the last session", "1W": "over the last week",
               "1M": "over the last month", "ALL": "since you started"};
const CHART_DAYS = {"1D": 7, "1W": 7, "1M": 31, "ALL": Infinity};
const VERDICT = {
  act: {shape: "■", word: "Action needed"},
  look: {shape: "▲", word: "Look soon"},
  clear: {shape: "●", word: "All clear"},
  nodata: {shape: "◆", word: "No data yet"},
};

let data = null;
let period = "ALL";
let selected = null;                      // a book key, or null for the overview

const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const fmt = (v) => Math.abs(v).toLocaleString("en-AU",
  {minimumFractionDigits: 2, maximumFractionDigits: 2});
const dir = (v) => (v > 0 ? "up" : v < 0 ? "down" : "flat");
const arrow = (v) => (v > 0 ? "▲" : v < 0 ? "▼" : "▶");
const signed = (v) => `${v > 0 ? "+" : v < 0 ? "−" : ""}A$${fmt(v)}`;
const pct = (v) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v * 100).toFixed(2)}%`;
const when = (iso) => (iso ? new Date(iso).toLocaleString("en-AU",
  {weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit"})
  : "never");
const books = () => (data ? data.books : []);
const bookKeys = () => books().map((b) => b.key);

function hero(p, words) {
  if (!p || !p.measurable) return `<div class="hero muted">No change to show ${esc(words)}</div>`;
  const verb = p.change > 0 ? "Up" : p.change < 0 ? "Down" : "Flat";
  return `<div class="hero ${dir(p.change)}">${arrow(p.change)} ${verb} A$${fmt(p.change)}
    <small>${pct(p.pct)}</small></div><div class="balance">${esc(words)}</div>`;
}

const partial = (p) => (p && p.measurable && p.measured < p.of
  ? ` · ${p.measured} of ${p.of} books measured` : "");

function chips() {
  return `<div class="chips" role="group" aria-label="Period">${PERIODS.map((p) =>
    `<button class="chip" data-period="${p}" aria-pressed="${p === period}">${p}</button>`).join("")}</div>`;
}

function chart(series, title) {
  let pts = series;
  const days = CHART_DAYS[period];
  if (days !== Infinity && pts.length) {
    const end = new Date(pts[pts.length - 1][0]);
    const start = new Date(end.getTime() - days * 86400000);
    pts = pts.filter(([d]) => new Date(d) >= start);
  }
  if (pts.length < 2) {
    return `<div class="panel chart"><h3>${esc(title)}</h3><span class="muted">Not enough history yet.</span></div>`;
  }
  const vals = pts.map(([, v]) => v);
  const lo = Math.min(0, ...vals);
  const hi = Math.max(0, ...vals);
  const span = hi - lo || 1;
  const W = 600;
  const H = 150;
  const x = (i) => (i / (pts.length - 1)) * W;
  const y = (v) => H - ((v - lo) / span) * H;
  const line = pts.map(([, v], i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const zero = y(0);
  const area = `0,${zero.toFixed(1)} ${line} ${W},${zero.toFixed(1)}`;
  const iLo = vals.indexOf(Math.min(...vals));
  const iHi = vals.indexOf(Math.max(...vals));
  return `<div class="panel chart"><h3>${esc(title)}</h3>
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${esc(title)}">
      <defs><clipPath id="above"><rect x="0" y="0" width="${W}" height="${zero.toFixed(1)}"/></clipPath>
            <clipPath id="below"><rect x="0" y="${zero.toFixed(1)}" width="${W}" height="${(H - zero).toFixed(1)}"/></clipPath></defs>
      <polygon points="${area}" fill="var(--up)" opacity=".18" clip-path="url(#above)"/>
      <polygon points="${area}" fill="var(--down)" opacity=".18" clip-path="url(#below)"/>
      <line x1="0" x2="${W}" y1="${zero.toFixed(1)}" y2="${zero.toFixed(1)}" stroke="var(--muted)" stroke-dasharray="3 4" stroke-width=".8"/>
      <polyline points="${line}" fill="none" stroke="var(--text)" stroke-width="1.4"/>
    </svg>
    <div class="caption"><span>${esc(pts[0][0])}</span>
      <span>low ${signed(vals[iLo])} (${esc(pts[iLo][0])}) · high ${signed(vals[iHi])} (${esc(pts[iHi][0])}) · dashed = break-even</span>
      <span>${esc(pts[pts.length - 1][0])}</span></div></div>`;
}

function list(items) {
  const tags = {act: `<span class="tag act">■ Act now</span>`,
                look: `<span class="tag look">▲ Look soon</span>`,
                known: `<span class="tag muted">Known</span>`,
                note: `<span class="tag muted">Note</span>`};
  return `<ul class="items">${items.map((i) => {
    const link = i.url ? ` <a href="${esc(i.url)}" target="_blank" rel="noopener">Open on GitHub ↗</a>` : "";
    return `<li>${tags[i.level] || ""}${i.book ? `<strong>${esc(i.book)}</strong> · ` : ""}${esc(i.text)}${link}</li>`;
  }).join("")}</ul>`;
}

function attentionPanel(items, known, notes) {
  let html = `<div class="panel"><h3>Anything wrong?</h3>`;
  html += items.length ? list(items)
    : `<span class="muted">Nothing needs you${data.last_opened ? ` — nothing new since you last looked (${esc(when(data.last_opened))})` : ""}.</span>`;
  if (known.length || notes.length) {
    html += `<details><summary>${known.length} known warnings, ${notes.length} notes</summary>${list(known)}${list(notes)}</details>`;
  }
  return `${html}</div>`;
}

function jobsPanel() {
  const cls = (s) => (s === "failed" || s === "missed" ? "bad"
    : s === "unknown" || s === "late" ? "unk" : "");
  return `<div class="panel"><h3>Scheduled robots</h3><div class="jobs">${data.jobs.map((j) =>
    `<span class="${cls(j.state)}">${esc(j.summary)}</span>`).join("")}</div></div>`;
}

function banner() {
  const s = data.snapshot || {};
  return s.offline
    ? `<div class="banner" role="alert">Couldn't reach GitHub — showing books as saved ${esc(when(s.committed_at))}.</div>`
    : "";
}

function footer() {
  const s = data.snapshot || {};
  const ages = books().filter((b) => b.group === "CORE" && b.as_of).map((b) => b.as_of).sort();
  return `<p class="foot">Books saved on GitHub ${esc(when(s.committed_at))}${ages.length
    ? ` · oldest core book updated ${esc(when(ages[0]))}` : ""} · ⌘R to refresh</p>`;
}

function renderOverview() {
  const v = VERDICT[data.verdict] || VERDICT.nodata;
  const core = data.groups.find((g) => g.name === "CORE") || data.groups[0];
  let html = banner();
  html += `<span class="pill ${esc(data.verdict)}" role="status">${v.shape} ${v.word}${data.jobs_unknown ? " · GitHub status unknown" : ""}</span>`;
  if (core) {
    const p = core.periods[period];
    html += hero(p, SINCE[period]);
    html += `<div class="balance">Core books worth A$${fmt(core.aum)} of A$${fmt(core.initial)} put in${partial(p)}</div>`;
  }
  html += chips();
  if (core) html += chart(core.series, "Combined core profit and loss (A$)");
  html += attentionPanel(data.attention, data.known, data.notes);
  for (const g of data.groups.filter((x) => x !== core)) {
    html += `<div class="panel"><h3>${esc(g.name)} books — separate, not in the core total</h3>
      Worth A$${fmt(g.aum)} · ${changeText(g.periods[period])} ${esc(SINCE[period])}</div>`;
  }
  return html + jobsPanel() + footer();
}

function changeText(p) {
  if (!p || !p.measurable) return `<span class="muted">not measurable</span>`;
  return `<span class="${dir(p.change)}">${arrow(p.change)} ${signed(p.change)} (${pct(p.pct)})</span>`;
}

function renderBook(b) {
  const p = b.periods[period];
  const state = b.halted ? ["act", "■ Halted by its safety breaker"]
    : b.near_breaker ? ["look", "▲ Near its safety breaker"]
    : b.freshness !== "fresh" ? ["look", `▲ Data ${b.freshness}`]
    : ["clear", "● Running normally"];
  const breaker = b.breaker != null ? ` · breaker trips at −${(b.breaker * 100).toFixed(0)}%` : " · no breaker";
  const mine = data.attention.concat(data.known).filter((i) => i.book === b.key);
  let html = banner();
  html += `<span class="pill ${state[0]}" role="status">${esc(state[1])}</span>`;
  html += `<h1 style="margin:10px 0 0;font-size:18px">${esc(b.name)}</h1>`;
  html += hero(p, SINCE[period]);
  html += `<div class="balance">Worth A$${fmt(b.equity)} of A$${fmt(b.initial)} put in · ${esc(b.status)}${breaker}</div>`;
  html += chips();
  html += chart(b.series, `${b.name} profit and loss (A$)`);
  html += `<div class="panel"><h3>Problems in this book</h3>${mine.length ? list(mine) : `<span class="muted">None.</span>`}</div>`;
  html += `<p class="foot">Last update ${esc(when(b.as_of))} · <a href="/" target="_blank" rel="noopener">Open in terminal ↗</a></p>`;
  return html;
}

function renderSidebar() {
  let html = `<button class="row" data-key="" aria-current="${selected === null}">Overview</button>`;
  for (const g of data.groups) {
    html += `<h2>${esc(g.name === "CORE" ? "Core books" : `${g.name} (separate)`)}</h2>`;
    for (const b of books().filter((x) => x.group === g.name)) {
      const p = b.periods[period];
      const flag = b.freshness === "fresh" ? "" : `<span class="fresh">${esc(b.freshness)}</span>`;
      const chg = p && p.measurable
        ? `<span class="chg ${dir(p.change)}">${arrow(p.change)} ${signed(p.change)}</span>`
        : `<span class="chg muted">—</span>`;
      html += `<button class="row" data-key="${esc(b.key)}" aria-current="${selected === b.key}">${esc(b.name)} ${flag}${chg}</button>`;
    }
  }
  html += `<h2>Detail</h2><a class="row" href="/" target="_blank" rel="noopener">Open terminal ↗</a>`;
  const nav = document.getElementById("sidebar");
  nav.innerHTML = html;
  nav.querySelectorAll("button.row").forEach((el) =>
    el.addEventListener("click", () => select(el.dataset.key || null)));
}

function render() {
  if (!data) return;
  renderSidebar();
  const b = books().find((x) => x.key === selected);
  const main = document.getElementById("main");
  main.innerHTML = b ? renderBook(b) : renderOverview();
  main.querySelectorAll(".chip").forEach((el) =>
    el.addEventListener("click", () => { period = el.dataset.period; render(); }));
}

function select(key) {
  selected = key;
  render();
}

async function refresh(syncNow = false) {
  try {
    const res = await fetch(`/api/home${syncNow ? "?sync=1" : ""}`, {cache: "no-store"});
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    data = await res.json();
    render();
  } catch (err) {
    document.getElementById("main").innerHTML =
      `<div class="banner" role="alert">The home screen couldn't load its data (${esc(err.message)}). ⌘R to retry.</div>`;
  }
}

document.addEventListener("keydown", (e) => {
  const cmd = e.metaKey || e.ctrlKey;
  if (cmd && e.key.toLowerCase() === "r") { e.preventDefault(); refresh(true); return; }
  if (cmd && /^[0-9]$/.test(e.key)) {
    e.preventDefault();
    const n = Number(e.key);
    select(n === 0 ? null : bookKeys()[n - 1] ?? selected);
    return;
  }
  if (e.key === "Escape") { select(null); return; }
  if ((e.key === "ArrowDown" || e.key === "ArrowUp") && document.activeElement?.closest("#sidebar")) {
    e.preventDefault();
    const rows = [...document.querySelectorAll("#sidebar button.row")];
    const i = rows.indexOf(document.activeElement);
    const next = rows[Math.max(0, Math.min(rows.length - 1, i + (e.key === "ArrowDown" ? 1 : -1)))];
    if (next) next.focus();
  }
});

refresh(false);
setInterval(() => refresh(false), 5 * 60 * 1000);
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/test_home_ui.py tests/test_home_server.py -v`
Expected: all PASS (`test_home_js_parses` SKIPs where node is absent).

- [ ] **Step 5: Commit**

```bash
git add trading_algo/dashboard/static/home.css trading_algo/dashboard/static/home.js tests/test_home_ui.py
git commit -m "feat(home): Midnight home screen — verdict, period change, chart, problems"
```

---

### Task 9: The launcher

**Files:**
- Create: `trading_algo/home/__main__.py`
- Test: `tests/test_home_app.py`

**Interfaces:**
- Consumes: `sync.sync`, `sync.last_result`, `sync.SyncResult` (Task 3); `jobs.check` (Task 4); `seen.begin_session` (Task 5); `dashboard.home.build_home` (Task 6); `server.create_server(..., home=...)` (Task 7).
- Produces: `python -m trading_algo.home [--no-sync] [--browser] [--port N]`; `point_readers_at(paths: AppPaths) -> None`; `make_app(paths, first: SyncResult, *, port: int = 0) -> ThreadingHTTPServer`.

- [ ] **Step 1: Write the failing tests** — create `tests/test_home_app.py`:

```python
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
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python3 -m pytest tests/test_home_app.py -v`
Expected: FAIL — `ImportError` / `AttributeError: module 'trading_algo.home.__main__' has no attribute 'point_readers_at'`.

- [ ] **Step 3: Implement** — create `trading_algo/home/__main__.py`:

```python
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


def make_app(paths: AppPaths, first: sync.SyncResult, *, port: int = 0):
    """Build the server. Call only after `point_readers_at`."""
    from ..dashboard import home as home_api   # deliberately late (see module doc)
    from ..dashboard import server

    started = datetime.now(timezone.utc)
    previous = seen.begin_session(paths.seen, started)
    state = {"sync": first}
    lock = threading.Lock()

    def build(sync_now: bool = False) -> dict:
        with lock:
            if sync_now:
                state["sync"] = sync.sync(paths)
            return home_api.build_home(
                jobs=jobs.check(), seen_path=paths.seen,
                session_start=started.isoformat(), first_run=previous is None,
                last_opened=previous, now=datetime.now(timezone.utc),
                snapshot=asdict(state["sync"]))

    return server.create_server("full", False, "127.0.0.1", port, home=build)


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
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/test_home_app.py -v`
Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add trading_algo/home/__main__.py tests/test_home_app.py
git commit -m "feat(home): launcher — sync, point readers, open the home screen"
```

---

### Task 10: Verify end to end

**Files:**
- Modify: `docs/specs/home-screen.md` (Status column of verified ACs; `status:` stays `draft` until Matt moves it)

- [ ] **Step 1: Full suite**

Run: `python3 -m pytest -p no:cacheprovider -rfE 2>&1 | tail -3`
Expected: `N passed` with no failures (N = 1123 + the new tests). Any failure is reported by name.

- [ ] **Step 2: Real launch against GitHub (needs network)**

Run: `MOMENTUM_HOME_DIR="$(mktemp -d)" python3 -m trading_algo.home --browser`
Expected: prints `Momentum home screen → http://127.0.0.1:<port>/home` and opens the browser. Check by eye:
- the pill shows a shape + word; the hero shows a signed A$ change with ▲/▼; the balance line is secondary;
- the sidebar lists Core and EXPERIMENTAL books separately, each with its change for the selected chip;
- clicking 1D/1W/1M/ALL changes hero, chart and sidebar together;
- ⌘1…⌘9 select books, ⌘0 / Esc return to the overview, ↑/↓ move focus in the sidebar, a focus ring is visible;
- "Scheduled robots" shows the three jobs; DAYTRADER's skipped runs are reflected honestly;
- Stop with Ctrl-C.

- [ ] **Step 3: Offline and first-run states**

Run: `MOMENTUM_HOME_DIR="$(mktemp -d)" python3 -m trading_algo.home --browser --no-sync`
Expected: no books are shown and "Anything wrong?" says "No book data yet — connect to the internet and refresh (⌘R)". The pill is "◆ No data yet", or "■ Action needed" if a scheduled job is genuinely failed/missed right now — **never "All clear"**. Then press ⌘R: the books appear.

- [ ] **Step 4: Native window (optional)**

Run: `pip install pywebview pyobjc-framework-WebKit && python3 -m trading_algo.home`
Expected: a native "Momentum" window opens at the home screen.

- [ ] **Step 5: Record and commit**

Mark AC-1…AC-15 and AC-17 ✅ (or ❌ with a one-line reason) in `docs/specs/home-screen.md`; AC-16 (Matt's 5-second test) stays ☐ for Matt.

```bash
git add docs/specs/home-screen.md
git commit -m "docs(spec): record B1 acceptance results"
```
