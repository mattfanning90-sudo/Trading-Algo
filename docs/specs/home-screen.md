---
title: Home screen on files (B1)
slug: home-screen
status: draft            # draft → agreed → in-progress → done (or dropped)
created: 2026-10-10
last-updated: 2026-10-10
owner: Matt Fanning
---

# Home screen on files (B1)

Step 1 of `docs/superpowers/specs/2026-10-09-mac-dashboard-design.md` (v2, §5 B1).

## Context
Matt wants to open an app on his Mac each morning and know in seconds **"am I up,
and is anything wrong?"** The books run on GitHub, so the app must fetch their
latest saved state from `main`, never trust the local copy (which was two weeks
stale on 2026-10-09).

B1 is the thinnest useful version: no database, no background helper, no AI. It
reads the books' saved JSON files, reuses the existing calculations
(`dashboard/overview.py` for totals, `metrics.py` for drawdown, `verify.py` for
problems), and adds a new Midnight-style home screen beside the existing terminal.

**Measured 2026-10-10 from GitHub's run history (500+ scheduled runs since 6 Aug)**,
which sets the "job missed" rules below:
- Daily share job (`Paper Trade & Publish Dashboard`, due 21:30 UTC): starts a
  median **80 min** late, 90% within **208 min**, worst **482 min**.
- Daily FX job (`FX Paper Trading (live)`, due 23:00 UTC): median **76 min**, 90%
  within **193 min**, worst **471 min**.
- Hourly DAYTRADER job: scheduled 23×/weekday but GitHub ran it only **2–6 times a
  day** since early September (largest gap **796 min**). Runs take ~3 minutes, so
  this is GitHub skipping runs, not a queue.

**Approach (draft — confirm):**
- **Placement:** the launcher and everything it needs before the readers load
  (sync, job health, seen-file, paths) live in a new `trading_algo/home/` package.
  They can't live in `trading_algo/dashboard/`: that package's `__init__.py` runs
  `from .api import build_snapshot`, which imports `paper_trade`, and
  `paper_trade.STATE_DIR` / `fx_book.STATE_DIR` are fixed at import time — so the
  launcher must set the state directories before anything in `dashboard` loads.
- `home/sync.py` — keeps a private copy of the public repo in
  `~/Library/Application Support/Momentum/repo` (full history, file contents
  fetched on demand), unpacks `state/*.json` for `origin/main` into
  `snapshots/<sha>/`, then points a `current` link at it in one step. Offline: keeps
  the last snapshot and reports its age.
- `dashboard/home.py` — builds `/api/home` from the current snapshot: group totals
  (from `overview.build_overview`), period changes per book, freshness, distance
  from each book's safety breaker (the breaker's own stored-peak figure), audit
  findings, job health, and one attention verdict.
- `home/jobs.py` — reads the latest scheduled runs from GitHub's public API
  (stdlib only, cached), and classifies each job using the measured delays above.
- `home/seen.py` — a small JSON file remembering when each problem was first
  seen and when Matt last opened the app, so only *new* problems turn amber.
- `verify.py` — additive: findings about one sleeve or symbol carry
  `detail["subject"]`, so "new vs known" can tell them apart.
- `static/home.html|css|js` — the Midnight home screen; existing terminal unchanged.
- `home/__main__.py` (`python -m trading_algo.home`) — the launcher: sync, point `MOMENTUM_STATE_DIR` /
  `FX_STATE_DIR` at `current` **before** the readers are imported, start the server,
  open a native Mac window (pywebview) at `/home`, or the browser if pywebview isn't
  installed.

## Goals
- **G-1**: In five seconds Matt can say whether his core books are up or down over
  the chosen period and whether anything needs him.
- **G-2**: Every number shows how old it is; stale data is never shown as current.
- **G-3**: The screen is quiet unless something is broken or new: red only for a
  halted book, an audit ERROR, or a job that failed or is missing beyond its measured
  grace period; amber only for problems new since Matt last looked.
- **G-4**: One source for every number: totals and distance-from-peak from
  `overview` (the breaker's own figure), problems from `verify`, any drawdown curve
  from `metrics`; nothing recalculated in JavaScript.
- **G-5**: It opens as a Mac window and is usable by keyboard and by colour-blind
  readers.

## Non-goals
- **NG-1**: Database, background helper, scheduled sync (project A).
- **NG-2**: Explainers, news, AI summaries (project C); bets (project D).
- **NG-3**: Menu-bar item, notifications, Dock badge (later; design Q-10).
- **NG-4**: A packaged `.app` bundle (later); B1 starts from a command or a simple
  launcher.
- **NG-5**: Light mode.
- **NG-6**: Any write to the live books or change to trading code. `verify.py`
  gains a field on its findings; nothing that decides or places trades changes.
- **NG-7**: Fixing GitHub's skipped DAYTRADER runs (recorded for measurement truth).
- **NG-8**: Changing which books count as "core" — B1 shows whatever
  `overview.build_overview` reports, so measurement-truth Task 22 flows through
  automatically.

## Acceptance criteria
| ID    | Criterion (observable behaviour) | Verified by | Status |
|-------|----------------------------------|-------------|--------|
| AC-1  | Sync against a throwaway local git remote unpacks only `state/*.json` of the remote's `main` into `snapshots/<sha>/` and repoints `current` atomically; a second sync with no new commit changes nothing; the last 10 snapshots are kept. | `tests/test_home_sync.py` | ✅ pass — 8 sync tests; real sync from github.com in ~3 s |
| AC-2  | Sync with the remote unreachable keeps the previous `current` and reports `offline` with the snapshot's age; it never deletes the last good snapshot. | `tests/test_home_sync.py` | ✅ pass — test_unreachable_remote_keeps_the_last_snapshot |
| AC-3  | Sync never touches the repo checkout it runs from (its files and `git status` are unchanged). | `tests/test_home_sync.py` | ✅ pass — status, every ref and HEAD unchanged |
| AC-4  | `/api/home` group totals equal `overview.build_overview()` totals for the same snapshot, to the cent. | `tests/test_home_api.py` | ✅ pass — totals equal overview; independent cross-check 2430.0 |
| AC-5  | Period changes: **1D** = each book's last completed session (daily books: last mark vs the one before; the hourly book: last mark vs its last mark of the previous UTC day); **1W / 1M / ALL** = latest mark vs the last mark on or before the period start (ALL = initial capital). Totals add only measured books and say how many ("5 of 6 measured"). Hand-checked fixture values. | `tests/test_home_api.py` | ✅ pass — hand-checked fixture values |
| AC-6  | A book with no mark in the window is "not measurable", never counted as 0. | `tests/test_home_api.py` | ✅ pass |
| AC-7  | Each book carries its as-of time and a freshness state. Daily books: **fresh** if the last mark is ≤ 1 business day old, **late** at 2, **stale** at 3+. The hourly book: on a weekday, **stale** once more than 14 h have passed since the later of its last mark and that day's UTC midnight (so Monday morning isn't stale before the first run); at weekends, **fresh** if its last mark is from Friday or later. | `tests/test_home_api.py` | ✅ pass — 8-case freshness table |
| AC-8  | Distance from the breaker uses **the breaker's own figure** — equity against the book's stored peak, as `overview` already reports it — never a second drawdown calculation (the single-source guard stays green); a book is "near the breaker" once that fall reaches 60% of the book's breaker level as the engine defines it (draft — confirm, Q-1); books without a breaker never are. | `tests/test_home_api.py`, `tests/test_metrics_single_source.py` | ✅ pass — single-source guard green |
| AC-9  | Job health from the run list: daily jobs are **late** until 8.5 h after the scheduled time and **missed** after; DAYTRADER is **missed** only with no run for 14 h on a weekday; a failed latest run is **failed**; if GitHub can't be reached or rate-limits, the job is **unknown since HH:MM** — never OK. | `tests/test_home_jobs.py` (recorded API fixtures, no network) | ✅ pass — 12 tests; live: 100 runs fetched, all three jobs judged |
| AC-10 | Attention verdict: **act now** if any book is halted, any audit ERROR (new or not), any job failed or missed; **look soon** if any WARN finding is new since the last open, a known finding has become more severe, or a book is near the breaker; otherwise **clear**. INFO findings are never raised; known findings are counted, not raised. | `tests/test_home_attention.py` | ✅ pass — plus stale books raised (ruling, 2026-10-10) |
| AC-11 | "New" is keyed by (book, code, subject): a WARN/ERROR finding is new during the app session in which it is first observed (or in which it became more severe), and known in every later session; a finding whose wording changes daily ("77 days" → "78 days") stays known. On the first run, or after the seen-file is lost, every current finding is recorded as known — nothing turns amber. | `tests/test_home_seen.py`, `tests/test_home_attention.py` | ✅ pass — 7 seen tests |
| AC-12 | Findings about one sleeve or symbol carry `detail["subject"]`; all existing verify tests still pass. | `tests/test_verify.py` | ✅ pass — 39 verify tests |
| AC-13 | The launcher sets the state-directory variables to `current` before importing any reader, serves `/home` and the existing terminal at `/`, and opens a native window when pywebview is installed, else the default browser. | `tests/test_home_app.py` + manual launch | ⚠️ browser path verified (env-ordering + serve tests, headless real launch); native window untested — pywebview not installed |
| AC-14 | Home screen: status shown as shape + word; every money figure has a sign and ▲/▼; hero = change for the chosen period; balance secondary; sidebar lists core and experimental books separately with their change for the chosen period; keyboard: ↑/↓ sidebar, ⌘1–9 books, ⌘0 overview, ⌘R refresh, Esc; visible focus ring. | Manual checklist + screenshot | ⚠️ unverified — needs Matt's visual + keyboard check |
| AC-15 | States render without errors: offline, first run (no snapshot yet), a book that can't be valued, GitHub unknown. | Manual checklist with fixtures | ⚠️ first-run/offline proven by tests; on-screen rendering not yet looked at |
| AC-16 | 5-second test: Matt answers "up or down?" and "anything wrong?" correctly on two fixtures (a quiet day, a red day) within 5 seconds each. | Manual (Matt) | ☐ Matt |
| AC-17 | Full suite green. | `pytest -q` | ✅ pass — 1258 passed |

## Constraints & invariants
- **#1–#4** (no lookahead, costs, one weight function, whole shares) — not touched;
  B1 only reads saved state.
- **#5** — fixtures are copies of real state files; no synthetic number is shown as
  performance.
- **#6 Local currency** — every money figure is labelled; totals are A$ as
  `overview` already reports them.
- **Measurement single source** — no drawdown, Sharpe or annualisation outside
  `metrics.py`; the existing guard test must stay green.
- **Read-only** — B1 writes only under `~/Library/Application Support/Momentum/`;
  tests point that path at a temp directory and never use the network.
- Dependencies: none new for the core; pywebview is optional (browser fallback).

## Migration & rollback
- New stored data: the snapshot folders and the seen-file, both in App Support and
  both disposable (the seen-file only affects which problems show as new). Each
  carries a `"v": 1`; an unreadable or newer seen-file is renamed aside and treated
  as empty, never crashes the app.
- `verify.py`'s new `detail["subject"]` is additive; old readers ignore it.
- Rollback: delete the App Support folder and revert the branch; nothing else holds
  B1 state.

## Verification plan
```bash
pytest -q tests/test_home_sync.py tests/test_home_api.py tests/test_home_jobs.py \
          tests/test_home_attention.py tests/test_home_app.py tests/test_verify.py \
          tests/test_metrics_single_source.py
pytest -q
python -m trading_algo.home                      # syncs, then opens the home screen
```

## Open questions
- **Q-1** "Near the breaker" at 60% of the breaker level (−15% for a −25% breaker) —
  draft; the worst real drawdowns today are ~−9.5% (DAYTRADER) and −7.0% (FX·Matt).
- **Q-2** Grace rules in AC-9 come from 2026-08-06 → 2026-10-09 run history; re-measure
  after a month of use.
- **Q-4** A warning that clears and later returns stays "known" (the seen-file never forgets). Should a recurrence turn amber again? Measure how often findings flicker on/off before choosing a rule (raised by the final review, 2026-10-10).
- **Q-3** Should DAYTRADER's skipped runs (2–6 of 23 a day) show on the home screen
  as a known problem? Proposed: yes, as a quiet line until measurement truth decides.

## Decision log
| Date | Decision | Who |
|------|----------|-----|
| 2026-10-10 | Grace periods measured from GitHub run history instead of guessed. | Claude (measured) |
| 2026-10-10 | A book 3+ trading days behind its schedule is a look-soon item; one session late stays quiet (found live: EXPERIMENTAL behind since 6 Oct). | Claude (ruling) |
| 2026-10-10 | Sync downloads only the JSON blobs (batched fetch by id) — `git archive` pulls every missing file in a partial clone. | Claude (ruling, measured) |
