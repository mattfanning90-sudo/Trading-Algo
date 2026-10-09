# Momentum for Mac — design

**Status:** design agreed in conversation; awaiting review of this written version
**Date:** 2026-10-09 (conversation began 2026-09-24)
**Owner:** Matt Fanning
**Branch:** `feat/mac-dashboard`

This is the umbrella design. It records what was agreed, the measured facts behind
each decision, and how the work splits into four projects (A–D). Each project gets
its own spec in `docs/specs/` (repo spec schema) and its own plan before any code.

---

## 1. Intent

**What Matt asked for**
- A local application on his MacBook with a premium, modern dashboard.
- A new modern home screen, with a switch to the existing terminal for detail.
- The home screen's first job: **"Am I up, and is anything wrong?"**
- Explainers for the significant moves and *why*: from the books' own records,
  plus market news and a Claude-written summary.
- For each bet: **what the bet was, how it was calculated, its timeframe, whether we
  let it play out, and where it went right or wrong**, scaffolded for self-learning.
- A Postgres copy of the books and data, kept updated from `main`.
- A solid migration plan for every change, with backups for rollback.

**Assumed (not contradicted)**
- Read-only: the app never places, changes or triggers trades.
- Single user (Matt), on his own Mac.
- Reuse the existing data API and maths; no second copy of any formula.

## 2. Decisions

| Date | Decision |
|------|----------|
| 2026-09-24 | Scope: a new modern home screen **plus** a switch to the existing terminal. |
| 2026-09-24 | Home screen's first job: "Am I up, and is anything wrong?" |
| 2026-10-09 | The app fetches book state from GitHub (`main`), never trusts the local checkout. |
| 2026-10-09 | Visual style: **Midnight** (deep navy, soft glass cards, light-weight large numerals). |
| 2026-10-09 | Layout: **Mac sidebar** (books in a left sidebar, main pane = number, chart, issues). |
| 2026-10-09 | Explainers at full depth: own records + market news + Claude summary. |
| 2026-10-09 | Claude summaries run via the local `claude -p` on Matt's subscription login, not an API key. |
| 2026-10-09 | Architecture: extend the existing Python app (stdlib server, plain JS/CSS, no build step). |
| 2026-10-09 | A local **Postgres** mirror of `main`'s book state, kept updated in the background. |
| 2026-10-09 | Standing rule: every stored-format change ships with up/down migration, a pre-change backup, and a tested restore. |
| 2026-10-09 | Add bets: what, how calculated, timeframe, played-out vs cut short, right/wrong; scaffold for self-learning. |
| 2026-10-09 | Split into projects A–D; build the equity decision record (D0) first. |

## 3. Measured facts this design rests on

Measured 2026-10-09 unless noted. Each is a reason for a decision above.

- **The local checkout is stale.** `state/paper_state_full.json` last mark here was
  2026-09-23; on `origin/main` it was 2026-10-09. `fx_state_matt.json`: 2026-09-24 vs
  2026-10-09. The existing `desktop.py` reads the local folder, so building the
  existing `.app` as-is would show two-week-old numbers as current.
- **The books run on GitHub Actions** and commit `state/` to `main`:
  `paper-trade.yml` `cron: "30 21 * * 1-5"`, `fx-paper.yml` `cron: "0 23 * * 1-5"`,
  `day-paper.yml` `cron: "7 0-22 * * 1-5"` (hourly).
- **The repo is public** (`gh repo view`: PUBLIC), so the app can fetch with no login.
  Corollary, noted and out of scope: the paper books' positions and P&L are
  world-readable today.
- **History size.** 818 commits touch `state/` on `main` since 2026-06-10 (606 hourly
  day-trading). `fx_state_daytrader.json` alone is 3,065 KB; storing every raw
  version would be ~1.8 GB of near-duplicates, so the mirror stores **facts per
  commit + only the latest raw JSON**.
- **Postgres is present and running**: PostgreSQL 16.13 (Homebrew), `/tmp:5432`.
- **The overview's "day" is "since the previous bar".** For the 60-minute DAYTRADER
  book that is ~5 hours (`2026-10-08 21:00 → 2026-10-09 02:00`), so the headline
  "today" summed unlike spans. The home screen uses a uniform 24-hour window instead.
- **Attention noise.** 7 of 8 books currently carry an amber status (6 "OFF PEAK",
  1 "GATE ARMED"; only MULTI-ASSET is "CLEAR"), and the audit has 0 ERROR / 10 WARN
  that recur daily. A list of everything would be permanently
  amber. The audit's own docstring: *"a channel that fires on everything gets muted,
  and then the next real error is invisible again."*
- **Significant moves.** Combined CORE P&L (A$131,000 initial capital) has a daily
  standard deviation of **A$224**. A zig-zag at ~3× that (A$655–672) finds four
  chapters: 11 Jun→29 Jun **+A$1,239**; 29 Jun→7 Sep **−A$3,901** (FULL −A$2,658, 68%);
  7 Sep→2 Oct **+A$2,789**; 2 Oct→9 Oct **−A$970** (open). Thresholds ≥1% find only two.
- **What each book records at decision time.**
  - FX trades already store `agents`, `indicators`, `regime`, `target_weight` and a
    `why` sentence — the bet is fully recorded.
  - Equity trades store only `decision`, `fill`, `entry`, `realized`, costs — **no
    score, rank, volatility or regime state**. The numbers exist inside
    `strategy.precompute` (`scores = sig.momentum_score(...)`, `vols =
    sig.realised_vol(...)`) and `strategy.targets_at` ("**The one weight formula**"),
    but are not saved.
- **Attribution sources.** Equity `sleeve_history` is a daily list of per-region values
  in A$ (e.g. `{'date': '2026-10-09', 'ASX': 33333.33, 'US': 35003.4, 'FTSE': 31702.46}`;
  settled, see Q-2). FX `cumulative.by_pair` holds per-pair P&L in AUD since its
  `from` date (FX·Matt: since 2026-07-28); differencing it between commits gives exact
  per-pair window P&L.
- **Signal horizon is already defined** by `verify.signal_horizon` (mean of the
  Donchian and ROC windows; 57 bars on FX·Matt) with a "cut short" cutoff of a quarter
  of the horizon (`HORIZON_FRACTION`). FX·Matt: *"median hold 4.0 bars vs a 57-bar
  signal horizon … 57/75 round-trips closed before their own signal could"* resolve.
- **Claude billing.** Anthropic: *"A paid Claude subscription enhances your chat
  experience, and the Claude API is billed separately."* Claude Code auth precedence
  lists *"Subscription OAuth credentials from `/login`"* as the default for Pro/Max,
  but *"In non-interactive mode (`-p`), the key is always used when present"* — so the
  app must unset `ANTHROPIC_API_KEY` for its own `claude -p` calls. `--bare` is
  unusable here ("OAuth and keychain are never read"). Installed: Claude Code 2.1.142,
  which supports `--tools`, `--json-schema`, `--output-format json`,
  `--no-session-persistence`.
- **News inputs.** `forex/news.py` (economic calendar via Financial Modeling Prep) needs
  `NEWS_API_KEY`; the workflows reference it but it is **not** a repo secret, so that
  path has never run. Whether FMP's free plan includes the calendar is unverified (Q-1).

## 4. Architecture

```
GitHub main (public)
 ├─ state/  ← committed by the three Actions jobs
 └─ Actions run history (public API)
        │  fetch: launch, ↻, every 15 min (background agent)
        ▼
~/Library/Application Support/Momentum/
 ├─ repo/                 private shallow, sparse clone (state/ only)
 ├─ snapshots/<sha>/      unpacked per sync; "current" pointer switched atomically
 ├─ backups/              pg_dump files (pre-migration + nightly)
 └─ logs/
        │                                   │
        ▼                                   ▼
existing readers                     Postgres 16 "momentum_mirror"
(overview, verify, fx_api,           (history, moves, explainers, news evidence,
 terminal) read "current"             bets, outcomes, summaries cache)
        └──────────────┬────────────────────┘
                       ▼
     stdlib server (one process) ─► native window: Home (new) · Terminal (existing)
```

Principles:
- **One source of truth per number.** Existing readers keep reading the current
  snapshot through their existing code; Postgres serves only what needs history.
  No formula is re-implemented in SQL or JavaScript.
- **GitHub is the source of truth;** the mirror is a derived, rebuildable copy.
  Nothing is ever written back.
- **Graceful degradation.** Postgres down → current numbers still show, with
  "History unavailable" banner. Offline → last snapshot with "Offline: showing data
  synced <time>". Never a blank screen; never old numbers presented as current.

## 5. Projects

Build order: **D0 → A → B → C → D**. D0 does not depend on A–C, and every month
without it is a month of equity bets that can only be reconstructed, not recorded.

### D0 — Equity decision record (first)
- At each equity rebalance, save a decision record per candidate: momentum score,
  rank, realised volatility, trend/regime state, eligibility, final target weight,
  and the parameters in force. Read from the **same `SignalPanel` row `targets_at`
  uses** — never recomputed (invariant #3).
- Stored in a separate append-only file per book, `state/decisions_<account>.jsonl`,
  covering every eligible candidate; the book state format is unchanged.
- Backfill (in D0, by Matt's decision): replay past rebalances through the same
  function on point-in-time prices into `state/decisions_<account>.reconstructed.jsonl`,
  labelled **reconstructed**, and reconciled against the trades actually made.
- Spec: `docs/specs/equity-decision-record.md`.
- The equity horizon definition ("until next monthly rebalance", Q-8) belongs to
  project D, next to `verify.signal_horizon`.

### A — Mirror
- Private shallow, sparse clone of the public repo (`state/` only); atomic snapshot
  switch; keep the last 10 snapshots.
- Ingest each new commit into Postgres `momentum_mirror` (Unix socket, Matt's macOS
  role, no password; driver `psycopg`). Tables: commits, books, daily marks, sleeve
  marks, trades (incl. FX `why`), positions per commit, audit findings per sync, job
  runs, `price_daily`, latest raw JSON per file as `jsonb`.
- Backfill all commits since 2026-06-10. `python -m trading_algo.mirror rebuild`
  wipes and replays from git.
- Background launchd agent syncs every 15 min (pattern: the existing vault-sync agent).
- Job status from the public Actions API: latest run of `paper-trade`, `fx-paper`,
  `day-paper`.
- Version guard: refuse to interpret books whose `schema_version` is newer than the
  app's `STATE_SCHEMA_VERSION` (currently 2).

### B — Mac app and home screen
- **Style:** Midnight. **Layout:** Mac sidebar.
- **Sidebar:** Overview; Core (6 books, status dot, return since start);
  Experimental (2 books, separate total, excluded from the headline — existing rule);
  footer: Terminal ↗ (second window), sync status. Clicking a book focuses the main
  pane on that book with "Open in terminal ↗".
- **Headline:** CORE total in A$; period chips **1D · 1W · 1M · ALL**. **1D = last 24
  hours for every book**, ending at the moment of viewing (so a weekend shows the
  books did not move, labelled "markets closed", rather than replaying Friday). A
  book with no mark in the window is listed as "not measurable", never counted as
  flat (existing overview rule).
- **Chart:** combined CORE P&L in A$, break-even line, numbered move bands (C).
- **"Anything wrong?"**
  - 🔴 Act now: risk-halted book; any audit ERROR; latest scheduled job failed or
    missed; a book that skipped its scheduled update.
  - 🟠 Look soon: a warning **new in the last 24 hours** (first seen in the mirror);
    a book near its drawdown breaker (`MAX_DRAWDOWN_STOP = 0.25` for equity; ULTRA has
    none; "near" measured from history, Q-3).
  - Quiet collapsed line: "N known warnings, M notes".
  - Status pill: 🟢 All clear / 🟠 N new things to look at / 🔴 Action needed.
  - Off-peak shows as a chip on each book, not an alarm.
- Every figure carries its currency and as-of time.

### C — Explainers
- **Finding moves:** zig-zag reversal ≥ 3× the daily standard deviation of the series
  being viewed (combined or one book). Closed chapters are explained once and cached;
  the open chapter refreshes at most once a day.
- **Four layers, each with a trust badge:**
  1. *From your records* — where it came from: book → region (`sleeve_history`) or
     pair (`by_pair`) → instrument (realised from the ledger; unrealised from
     `price_daily`). Always reconciles to the real move with an explicit
     "not explained: A$X" line.
  2. *From your records* — what the system did: trades, rebalances, sleeves to cash
     (with recorded reason), breaker trips, FX agents' `why` for the biggest bets.
  3. *Sourced news* — labelled "correlation, not proven cause":
     - headlines: `claude -p --tools "WebSearch,WebFetch" --json-schema …` returns
       dated articles for the top 3–5 instruments/currencies; **the app opens every
       link itself and drops any that fail to load or fall outside the window**;
       survivors stored as evidence.
     - scheduled economic releases via `forex/news.py` once an FMP key exists (optional).
  4. *AI summary* — `claude -p --tools ""` writes a short paragraph from layers 1–3
     only; numbers quoted from layer 1; every news claim linked to a stored article.
- **Usage:** draws on Matt's subscription limits; closed chapters run once.

### D — Bets, outcomes and the learning scaffold
- **Bet** = one position episode (open → close), joined to its decision record
  (FX: recorded; equity: D0 recorded or reconstructed).
- **Shown per bet:** what the bet was; how it was calculated; **timeframe** (FX:
  `verify.signal_horizon`, e.g. 57 bars; equity: to next monthly rebalance); status
  **Running** ("day 3 of ~57") / **Played out** / **Cut short** (< ¼ horizon, with
  reason: signal flip, rebalance, breaker, holding rule); result (net A$ = market move
  − costs); the call (direction over the signal's own horizon); costs; exit reason;
  FX agents for/against; label: *right & paid* / *right but exited early* / *right but
  costs ate it* / *wrong call*.
- **Had we let it play out (hypothetical):** for each cut-short bet, P&L if held to
  horizon end, costs included, vs actual; per-book roll-up. Caveats travel with the
  number: hindsight; per-bet, not a portfolio re-run; and the audit's warning —
  *"Suggestive, not causal — a position survives longer precisely when its signal
  keeps confirming."*
- **Learning scaffold:** `bet` (decision-time facts, immutable), `bet_outcome`
  (computed later, method-versioned, with `knowable_at`), an agent-scorecard view, and
  an export command.
- **Hard limits:** observe-and-record only — nothing feeds back into trading; the
  training view returns only outcomes knowable before a given cutoff (no lookahead);
  any future loop is bounded to **FX models and agent roster**, never strategy knobs,
  equity stays formula-driven, and promotion still passes the DSR/PBO gates. Holding
  period is a strategy knob: the app surfaces evidence; Matt decides.

## 6. Migration and rollback (applies to every project)

- **Postgres:** numbered `migrations/NNNN_name.up.sql` / `.down.sql`; a
  `schema_migrations` table with checksums; each migration in one transaction;
  expand-then-contract so the previous app build can still read the database.
- **Backups:** automatic `pg_dump` immediately before every migration, plus nightly
  from the background agent. Proposed retention: 14 nightly; pre-migration dumps for
  90 days (Q-4). Restore: `python -m trading_algo.mirror restore <file>`. Last resort:
  rebuild from git (loses only locally generated history: summaries, job-run history,
  audit results as seen at the time — which is what backups protect).
- **Book state (D0):** format unchanged — records go to new files with a per-line
  `"v"` version; back up every live book before deploy; rollback = revert the merge
  (old code never reads the new files); restore drill.
- **App code:** versioned builds; keep the previous 3; refuse to open a database newer
  than the build understands.
- **Snapshots:** keep the last 10; the "current" pointer can be moved back.
- **Tested, not just written:** every migration runs up → down → up on a throwaway
  database in the suite; a restore drill from a real backup.

## 7. Packaging and launch — proposal, not yet reviewed in conversation

- py2app **standalone** bundle with its own Python and dependencies, built from a clean
  export of a tagged `main` commit — never from the dev checkout, which may be on a
  half-finished branch.
- One embedded server on a private loopback port; Home at `/`, the existing terminal
  at `/terminal`, opened as a second native window.
- The background agent runs the sync entry point **inside the installed `.app`**, so
  it always runs released code.
- Unsigned personal build (first launch: right-click → Open). FMP key, if added, in
  the macOS Keychain.
- The app's `claude -p` calls run from the app's data directory (not the repo, so the
  repo's `CLAUDE.md` and hooks don't load), with `ANTHROPIC_API_KEY` removed from the
  child environment and `claude` located explicitly (`~/.npm-global/bin/claude`).

## 8. Testing — proposal, not yet reviewed in conversation

- **Sync/ingest:** a throwaway local git remote; fixtures copied from real state files
  (the repo is public); idempotent re-ingest; atomic snapshot switch.
- **Golden tests on real history:** the 3σ rule finds the four chapters above;
  attribution reconciles to the move to the cent, with the residual line.
- **Semantics:** 1D is 24 hours for hourly and daily books; "not measurable" is never
  counted as flat; "new vs known" warnings.
- **Migrations:** up → down → up on a throwaway database; checksum enforcement;
  restore drill. CI gets a Postgres 16 service container; local runs skip cleanly
  when Postgres is absent.
- **Invariants:** D0's recorded weights equal `targets_at` weights
  (`tests/test_consistency.py` extended); bet outcomes never visible before
  `knowable_at`; the training view respects its cutoff.
- **Claude and news:** a stub `claude` executable in tests (no real usage); link
  verification against offline fixtures; one manual smoke test per release.
- **UI:** launch and screenshot the app against fixture data; manual checklist for the
  sidebar, period chips, attention levels and offline/Postgres-down banners.

## 9. Open questions

- **Q-1** Does FMP's free plan include the economic calendar? If not, decide on a paid
  plan or drop scheduled releases (headline news via Claude remains).
- ~~**Q-2** Are `sleeve_history` values in A$ or local currency?~~ **Settled
  2026-10-09:** A$ (base). `paper_trade.py` writes `round(v[2], 2)` where
  `v = (eq_local, currency, eq_base)`.
- **Q-3** "Near the breaker" threshold — measure on real drawdown history before
  choosing.
- **Q-4** Backup retention (proposed 14 nightly / 90-day pre-migration).
- **Q-5** Does `claude -p` behave under launchd (PATH, user-level hooks, login)? Test
  before relying on background summaries.
- **Q-6** Price source for `price_daily` (reuse existing loaders; Yahoo can 403;
  Tiingo as fallback if a local key exists).
- **Q-7** Which Claude model the summaries use (Matt's Claude Code default vs pinned).
- **Q-8** Equity bet horizon: "until the next monthly rebalance" — confirm.
