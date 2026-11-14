# Momentum for Mac — design

**Status:** v2 — revised after a five-reviewer expert review (§10). Progress
(2026-10-11): Step 0 done; Step 1 (B1) built 2026-10-10 and awaiting Matt's checks;
the modern versions of the remaining screens are the next design (Matt,
2026-10-11).
**Date:** 2026-10-09 (conversation began 2026-09-24)
**Owner:** Matt Fanning
**Branch:** `feat/mac-dashboard`, rebased onto `feat/measurement-truth`

This is the umbrella design. It records what was agreed, the measured facts behind
each decision, the expert review and what it changed, and how the work splits into
projects. Each project gets its own spec in `docs/specs/` and its own plan before
any code.

## Resume here (updated 2026-11-14)
- **2026-11-14:** Phase 1a + 1b built and reviewed (book Overview + Positions,
  `docs/specs/book-screens.md`). Matt retired the Mac app as the delivery vehicle:
  the planned Railway website (`docs/superpowers/specs/2026-11-14-railway-hosting-design.md`
  on branch `feat/railway-hosting`) serves these same screens. The Mac launcher stays
  as it is. **Still to do:** Matt's 5-second test (AC-19). Phase 1c (the phone / Pages
  copy) is now covered by the Railway design's nightly public export, not a separate
  plan here.
- *(Older notes below, from 2026-10-13.)*
- **Where:** worktree `~/Trading-Algo-mac-dashboard`, branch `feat/mac-dashboard`
  (on top of `feat/measurement-truth`, rebased 2026-10-13). Neither branch is on GitHub.
- **Done:** B1 home screen (`python -m trading_algo.home`; spec
  `docs/specs/home-screen.md`); knowledge reorganised in three layers
  (`docs/README.md`).
- **Waiting on Matt:** review the phase-1 plan
  `docs/superpowers/plans/2026-10-14-book-screens-phase1.md` (15 tasks, 1a + 1b)
  and choose how it's executed; merge of PR #102 (fix backlog F20–F26); visual and keyboard checks of the home screen
  (AC-14/15/16); open questions Q-1, Q-3, Q-4 in `docs/specs/home-screen.md`.
- **Done 2026-10-13:** PR #100 (SMALL fix) merged into `main` as `cd44ff2`.
- **Next:** the phase-1 spec (`docs/specs/book-screens.md`) was adversarially
  reviewed 2026-10-14 and revised (saved values only, ranked main problem, phone
  split out as 1c). Next is its implementation plan for 1a + 1b, then build. Rebased onto `feat/measurement-truth` 2026-10-13
  (suite 1500 passed). D0a (equity decision record) is unblocked — it also feeds
  the "does the ranking pick winners?" line the Overview needs.

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
- Learning is a goal in itself: an explainer earns its place by teaching, not only
  by changing a decision.

## 2. Decisions

| Date | Decision |
|------|----------|
| 2026-09-24 | Scope: a new modern home screen **plus** a switch to the existing terminal. |
| 2026-09-24 | Home screen's first job: "Am I up, and is anything wrong?" |
| 2026-10-09 | The app fetches book state from GitHub (`main`), never trusts the local checkout. |
| 2026-10-09 | Visual style: **Midnight**. Layout: **Mac sidebar**. |
| 2026-10-09 | Explainers at full depth: own records + market news + Claude summary. |
| 2026-10-09 | Claude calls run via the local `claude -p` on Matt's subscription login, not an API key. |
| 2026-10-09 | Architecture: extend the existing Python app (stdlib server, plain JS/CSS, no build step). |
| 2026-10-09 | A local **Postgres** database, kept updated from `main`. |
| 2026-10-09 | Standing rule: every stored-format change ships with up/down migration, a pre-change backup, and a tested restore. |
| 2026-10-09 | Bets: what, how calculated, timeframe, played out vs cut short, right/wrong; scaffold for self-learning. |
| 2026-10-09 | After review: **measurement truth stays #1.** Mac work lives on top of `feat/measurement-truth`, touches no trading code until its Task 10 lands, and runs in parallel. |
| 2026-10-09 | After review: **full vision, thin-first order** — each step ships and is used before the next (§5). |
| 2026-10-09 | After review: D0's backfill is kept, **replayed on the code that actually ran each decision**. |
| 2026-10-09 | After review: the SMALL micro-mode bug (§3) is fixed now, as its own small change, independent of this project. |

## 3. Measured facts this design rests on

Measured 2026-10-09 unless noted. ✔ = verified in code/data by Claude; reviewer
findings were re-checked before being recorded here.

**Data source and freshness**
- ✔ The local checkout is stale (`paper_state_full.json` last mark 2026-09-23 here vs
  2026-10-09 on `origin/main`). The books run on GitHub Actions and commit `state/` to
  `main`: `paper-trade.yml` `cron: "30 21 * * 1-5"`, `fx-paper.yml` `cron: "0 23 * * 1-5"`,
  `day-paper.yml` `cron: "7 0-22 * * 1-5"`.
- ✔ The repo is public; the app needs no login to fetch. (Paper positions are
  world-readable today — noted, out of scope.)
- ✔ Already in place and **not replaced** by this app: the dashboard is rebuilt and
  published to GitHub Pages after every run (`bash scripts/build_site.sh`, "Deploy to
  GitHub Pages"), and ERROR findings push to the phone via `ALERT_WEBHOOK_URL` (secret
  set 2026-09-19).
- ✔ The audit's own lesson: *"The audit has been right and unread. `never-traded:
  sleeve ASX` fired every day for 52 days"* (`verify.py`, `alert_on_errors`). The risk
  this app must avoid is a surface that isn't looked at, or that cries wolf.

**History**
- ✔ 818 commits touch `state/` on `main` since 2026-06-10 (606 hourly day-trading).
  `fx_state_daytrader.json` alone is 3,065 KB.
- ✔ History gets rewritten. Before the 2026-09 correction, FULL's history carried a
  phantom −A$25,448 on 27 Aug; anything computed from it then would now be wrong.
- ✔ `feat/measurement-truth` will **archive every book and restart them clean**
  (Task 26), **take the FX books out of the headline total** (Task 22) and **split
  decision date from fill date** in `paper_trade.py:814-873` (Task 10). Anything
  calibrated on today's history is calibrated on books about to be archived.

**What moved the money**
- ✔ **Currency translation dominates FULL.** From 29 Jun to 8 Sep FULL's US and FTSE
  sleeves went from 8 and 10 positions to **zero** (liquidated by the false breaker
  trip on 27 Aug) while the A$ strengthened: USD 1.4514 → 1.3843 and GBP 1.9268 →
  1.8772 (A$ per unit, from each commit's `fx_snapshot`). The US sleeve was US$23,809
  → US$23,831 locally. About **−A$2,450 of FULL's −A$2,658 in that chapter was
  currency; about −A$200 was the stocks.**
- ✔ FULL is ~89% of the combined CORE P&L variance; FULL's ASX third (A$33,333) has
  never traded.
- ✔ Combined CORE P&L daily σ is A$224; a zig-zag at ~3σ finds four chapters
  (+A$1,239 · −A$3,901 · +A$2,789 · −A$970, open), stable from 2.5σ to 3.5σ. **Caveat
  that travels with these numbers:** they include the 27 Aug phantom liquidation and
  are dominated by currency on idle cash in one book.
- ✔ `sleeve_history` is in A$ (`paper_trade.py` writes `round(v[2], 2)` where
  `v = (eq_local, currency, eq_base)`).
- ✔ FX `cumulative.by_pair` reconciles exactly to equity (reviewer: within 1e-12 over 54
  commits) **from 2026-07-28 only**; earlier FX commits lack it.

**Status and attention**
- ✔ The overview's "day" is "since the previous bar" — ~5 hours for the 60-minute
  DAYTRADER book.
- ✔ 7 of 8 books carry an amber status today (6 "OFF PEAK", 1 "GATE ARMED") because
  `overview.py` marks any book >1% below peak as warn
  (`elif off_peak < -0.01: … "warn"`); the audit has 0 ERROR / 10 recurring WARN.
- ✔ Midnight's muted text passes contrast (6.3:1), but its green and red differ only
  1.4:1 in brightness — status by colour alone fails colour-blind users.
- ✔ The existing terminal has no keyboard or accessibility support (no `aria-`,
  `tabindex`, `keydown`, `:focus`).

**Decision records**
- ✔ FX trades store `agents`, `indicators`, `regime`, `target_weight` and `why`.
  Equity trades store only prices and costs.
- ✔ `rebalance_sleeve` reshapes the weight formula's targets before trading (micro
  mode, long/short lot fitting, whole-share rounding, frozen names, dust): a record of
  the formula's output is not a record of the bet.
- ✔ **Live bug (SMALL):** micro mode tests affordability against the whole sleeve
  (`px[t] <= equity / 1.05`, ≈ US$647) but gives each of up to 3 picks only
  `0.97 / len(picks)` (≈ US$220), so `int((equity * w) / price)` is 0 shares for names
  priced ~US$220–647. SMALL has 2 trades ever (SLV, June/July), holds nothing, and its
  1 Oct rebalance produced targets but no trades. Mechanism verified in code; not yet
  reproduced on live prices.
- ✔ The equity run fails *unsafe* in CI: the run step is `bash -e` across all four
  accounts, and "Commit updated paper state" has no `if: always()` — one exception
  loses every equity book's day.

**Claude and tooling**
- ✔ API billing is separate from the subscription; `claude -p` uses the subscription
  login unless `ANTHROPIC_API_KEY` is set (*"In non-interactive mode (`-p`), the key is
  always used when present"*). Installed Claude Code 2.1.142 supports `--tools`,
  `--json-schema`, `--strict-mcp-config`, `--effort`, `--disable-slash-commands`; it
  does **not** have `--safe-mode`; `--bare` is unusable (no OAuth).
- ✔ `~/CLAUDE.md` exists, so a `claude -p` run anywhere under `$HOME` loads it, along
  with the user's plugins and `effortLevel: xhigh`.
- ✔ Postgres 16.13 (Homebrew, keg `postgresql@16`) runs on `/tmp:5432`; `pg_dump` is
  in `/opt/homebrew/bin`, which the existing launchd template's PATH does not include.
- ✔ `paper_trade.STATE_DIR` and `fx_book.STATE_DIR` are fixed at import time; opening
  `paper_books.db` runs `PRAGMA journal_mode=WAL` and `CREATE TABLE` (writes).

## 4. Architecture

```
GitHub main (public)
 ├─ state/  ← committed by the three Actions jobs
 └─ Actions run history (public API)
        │  ONE writer: the background agent (launch/↻ in the app = "kick the agent")
        ▼
~/Library/Application Support/Momentum/
 ├─ repo/              blobless partial clone (full history, contents on demand), sparse on state/
 ├─ snapshots/<sha>/   JSON only (no *.db), unpacked to .tmp-<sha> then renamed
 ├─ current → snapshots/<sha>      stable symlink; readers' STATE_DIRs point here
 ├─ backups/books/  backups/pg/    separate retention
 ├─ status.json       agent heartbeat (last success / error per task)
 └─ logs/             rotated
        │                                         │
        ▼                                         ▼
existing readers via `current`            Postgres 16 "momentum_mirror"
(overview, verify, fx_api, terminal)       ├─ derived tables: rebuilt from git; no dumps needed
                                           └─ local tables: first-seen, job runs, news evidence,
                                              summaries — the backup rule applies in full
                       └──────────────┬───────────────┘
                                      ▼
     stdlib server (one process) ─► native window: Home (new) · Terminal (existing, 2nd window)
```

Principles:
- **One source of truth per number.** Existing readers read the current snapshot;
  derived history is rebuilt from the latest ledger on each sync, never kept as a
  second copy that can disagree after a repair.
- **GitHub is the source of truth.** Nothing is written back.
- **One consistent as-of.** The `current` pointer moves only after that commit's
  ingest commits; every row carries its commit; the screen never mixes two commits.
- **Fail loud, degrade gracefully.** Postgres down → current numbers with "History
  unavailable"; offline → last snapshot with "Offline: data from <time>"; agent dead →
  red "Background sync not running". Never a blank screen; never stale-as-current.

## 5. Projects, in build order

Measurement truth stays #1 and continues on its own branch. These projects run in
parallel, read-only, and **nothing here touches trading code until measurement-truth
Task 10 has landed.**

| Step | Project | Needs | Status (2026-10-11) |
|------|---------|-------|--------|
| 0 | **SMALL micro-mode fix** — own branch from `main`, failing test first | — | done — PR #100; also on measurement truth as 38f9ff5 |
| 1 | **B1 — home screen on files** (no Postgres, no background agent yet) | measurement-truth Task 22 grouping | built 2026-10-10 (`docs/specs/home-screen.md`); FX books now report in their own group (6b9e1ff) |
| 2 | **A — sync agent + Postgres** (local history: first-seen, job runs) | B1 in use | not started |
| 3 | **D0a — equity decision record (recording)** | measurement-truth Task 10 | unblocked — Task 10 (next-close fills) has landed |
| 4 | **C — explainers** (currency layer, records, news, AI summary) | A | not started |
| 5 | **D0b — backfill at historical code**, then **D — bets + learning scaffold** | A, D0a | not started |

Each step ships and is used before the next starts. Specs are written per step.

### B1 — Home screen on files
- Reads a fresh `git archive` of `main`'s `state/*.json` into App Support; a simple
  launcher, no Postgres, no agent. Grouping per measurement-truth Task 22.
- **Hierarchy (top to bottom):** status pill (shape + word) → **hero = change for the
  selected period** ("Down A$1,012 since start · −0.77%") → balance as a secondary
  line ("Worth A$129,987.70 of A$131,000") → "Anything wrong?" items in primary text.
- **Period chips 1D · 1W · 1M · ALL drive the hero, chart and sidebar.** **1D = each
  book's last completed session**, with a per-book as-of time; partial totals say so
  ("5 of 6 books measured"). Row states: Moved · No change · No update scheduled
  (weekend; crypto not marked since Friday) · Not measurable · Late/missing.
- **One attention rule** (act now / look soon / quiet) drives the pill, the sidebar
  and — later — Dock badge and menu bar. **Amber means "new to you"**; quiet books get
  no dot; off-peak is a neutral chip that turns amber only near the breaker.
  - 🔴 Act now: risk-halted book; audit ERROR; a job failed, or missed *after its grace
    period* (GitHub delays scheduled runs; daily jobs ~2–3 h, day-paper two consecutive
    misses — tuned on real Actions history). Every red row says what happened and links
    to it. Before the grace period: neutral "Running late (due 09:00)".
  - 🟠 Look soon: a warning **new since you last opened the app**, keyed by
    (book, check, subject) — not message text — and dated by the first commit that
    carried it, so rebuilds don't make everything new; re-raised when it worsens.
  - Quiet: "N known warnings" collapsed.
- **Freshness, not "synced":** the footer dot means data freshness against each job's
  schedule; it shows the oldest CORE book's last update and the next one expected.
- **Accessibility from day one:** ▲/▼ and signs on every P&L figure; status by shape
  + word; chart line neutral with green/red areas above/below break-even and a min/max
  caption; keyboard (↑/↓ sidebar, ⌘0 Overview, ⌘1–8 books, ⌘R sync, Esc), visible
  focus ring ≥3:1, selected row ≥3:1; honour Reduce Motion / Increase Contrast / Reduce
  Transparency; body 13px.
- **States:** first run, offline, history unavailable, version-guard refusal,
  agent not running (once A exists).
- **Success measures (behavioural):** opened ≥5×/week for 4 weeks; incident →
  acknowledgement ≤24 h (baseline: 52 days for the unread ASX finding); red that leads
  to no action ≈ 0; amber ≤ ~2/week; counter-metric: measurement-truth pace doesn't drop.

### A — Sync agent + Postgres
- Blobless partial clone; ingest walks `git rev-list <high-water>..origin/main --
  state/`, one transaction per commit, high-water advanced in the same transaction;
  retries after wake (30/60/120 s).
- **Single writer:** only the agent fetches, ingests, migrates and summarises
  (`fcntl.flock` + `pg_advisory_lock`); stale git locks cleared only when safe.
- **Derived tables** (marks, trades, sleeve values, FX rates from `fx_snapshot`,
  positions per commit, `by_pair` snapshots) are dropped and rebuilt from the latest
  ledger — no dumps. **Local tables** (first-seen, job runs, news evidence, summaries,
  explanation cache) get the full migration/backup regime (§6).
- Job status: one `GET /actions/runs` per poll, `gh auth token` when available, honour
  `x-ratelimit-reset`; each job is OK / failed / **unknown since HH:MM** (never green).
- Agent health: `status.json` heartbeat; a macOS notification only on a state change
  (3 consecutive failures, newest verified backup >36 h, no sync for 2 h on a weekday
  while awake); optional push via the existing `ALERT_WEBHOOK_URL`.
- launchd PATH includes the Postgres tools chosen from the live server's
  `server_version_num` (`/opt/homebrew/opt/postgresql@<major>/bin`).

### D0a — Equity decision record (recording)
- Spec: `docs/specs/equity-decision-record.md` (to be re-agreed after Task 10).
- Records **two layers per rebalance:** the *signal* (every candidate's score, rank,
  vol, trend, data-quality and filter status, raw and final weights, regime, params)
  from inside the one weight formula, and the *execution* (micro picks, long/short
  fit, desired shares, frozen and dust skips, each divergence with its reason)
  returned by `rebalance_sleeve`.
- **Fails safe:** recorder errors are caught and alerted; trading, state and exit code
  unchanged; records appended after `save_state`, inside the account lock; an audit
  check flags "rebalanced but no record". Halt liquidations recorded as their own kind.

### C — Explainers
- **Finding moves:** per book on returns with an A$ materiality floor, robust σ
  (median-based), on bar dates; the open chapter is shown as last pivot → running
  extreme plus a pending leg. Books that held no positions in a chapter are labelled
  "currency only" and get no stock explainer.
- **Layer 1 — where it came from (fact):** book → sleeve → **currency translation vs
  local price** → ticker (mark-to-market within the window, not realised-since-entry).
  Always reconciles, with an explicit "not explained: A$X" line. FX pairs exact from
  28 Jul; earlier periods use the `fx_pnl` lot approximation, labelled as such.
- **Layer 2 — what the system did (fact):** decisions, halts and resumes, sleeves to
  cash with the recorded reason, FX agents' `why`.
- **Layer 3 — context (not proven cause):** news searched for the instruments *and
  currencies* that actually drove the move (AUD/USD when translation dominates);
  windows on bar dates; every link opened and date-checked by the app.
- **Layer 4 — AI interpretation:** the explainer **leads with a fixed-template sentence
  from layer 1** ("Down A$3,901, 29 Jun→7 Sep; 63% currency on idle cash"), not AI
  prose. The AI output has two fields ("what happened" / "what was in the news"),
  numbered citations, and is regenerated if it links news to a move with causal words.
- **Trust badges state trust, not source:** "Fact · your books" / "Context · not proven
  cause" / "AI interpretation · check sources", in neutral colours.
- **Cache keyed by a hash of the data used**, re-checked on every sync, so a history
  rewrite (like the 27 Aug phantom) invalidates the explanation.
- Claude calls: on demand or cached; never unbounded (§7).

### D0b + D — Backfill, bets and the learning scaffold
- **D0b:** past decisions found from the git history of each book (per commit:
  `last_rebalance_date`, `last_status`, `last_flat_reason`, `risk_halted`), replayed on
  the code at the commit that ran them, with that commit's as-of bars; books read
  read-only; headline validity = **precision** (every name the book opened is in the
  reconstructed targets), with missing names sorted into sizing / frozen / unexplained.
- **Bets:** same-sign episodes within a horizon merge into one bet; each bet's horizon
  comes from the agents that drove it (Donchian/ROC for breakout and momentum,
  Bollinger for mean reversion, 1 bar for the neural agent, none for carry); holds
  counted in the book's own bars; open bets shown as running, never dropped; units
  from the bar size ("hour 3 of ~57" on DAYTRADER).
- **Equity bets:** judged by excess return vs the sleeve's universe and by per-rebalance
  rank correlation (score vs next-period return), not absolute direction.
- **"Had we let it play out":** never summed per bet (it double-counts: 52 of 58 of
  FX·Matt's cut-short bets were re-taken on the same side within their horizon —
  reviewer-measured, not yet re-checked). The
  valid version is a whole-book re-run with a longer minimum hold via the existing
  `policy_sweep`. Per bet, show the pair (actual · if held · hindsight) only, never on
  Home, never in a total; add the label "wrong — early exit limited the loss".
- **Learning scaffold:** outcomes keyed by (bet, method version, source commit) with
  two timestamps (true-as-of, recorded-at); purged walk-forward with embargo reused
  from `ml_backtest`; shared cluster IDs for bets duplicated across books (Matt and
  Partner share 69 of 75 — reviewer-measured); data versioned by agent roster; effective N and confidence
  intervals shown. Bounded as before: FX models and roster only, never strategy knobs.

## 6. Migration and rollback (applies to every project)

- **Postgres local tables:** numbered `NNNN_name.up.sql` / `.down.sql`, checksummed in
  `schema_migrations`, one transaction each, expand-then-contract.
- **Backups:** `pg_dump` with the server's own major version, to a temp file, verified
  (exit code, size > 0, `pg_restore --list`) before being kept; pre-migration dump
  required before any migration; pruned by count only after a new verified dump;
  weekly automated restore into a scratch database; a 16-dump → 17-restore drill.
  Never auto-create the database when dumps exist — report "database missing —
  restore <file>". Restore into a new name, check row counts, then swap.
- **Book state (D0):** format unchanged; new files carry a per-line `"v"`;
  `.gitattributes` `state/decisions_*.jsonl merge=union` (proven against the bots'
  `rebase -X theirs` in a throwaway repo); git is the backup (pre-merge SHA +
  checksums manifest).
- **Restore runbook (book state):** disable `paper-trade`, wait for runs to finish,
  restore JSON **and** `paper_books.db`, delete `*.db-wal`/`*.db-shm`, verify against
  the restored directory, commit, re-enable, dispatch a run. Rehearsed against a local
  bare remote with a simulated bot push. Rollback is complete when the first run after
  the revert finishes green.
- **App code:** versioned builds, previous 3 kept; install by stage → `bootout` → swap
  → `bootstrap`; refuse a database newer than the build understands.

## 7. Packaging and the Claude calls

- v1 (B1): a simple launcher over a fresh `git archive` of `state/` — no py2app yet.
- Later: py2app standalone from a tagged `main` commit; the agent runs the sync entry
  point inside the installed `.app`.
- **`claude -p` isolation:** working directory under `$TMPDIR` (outside `$HOME`, so
  `~/CLAUDE.md` doesn't load); `--strict-mcp-config --mcp-config '{"mcpServers":{}}'`,
  `--disable-slash-commands`, `--tools` limited per step, pinned `--model` and
  `--effort`, `--no-session-persistence`, `--json-schema`; `ANTHROPIC_API_KEY` removed;
  preflight `claude auth status` must report the subscription login; subprocess
  timeout with process-group kill; a persisted daily call cap; back off on failure;
  stop until reset on a usage-limit error. Q-5 testing covers screen locked, after
  sleep/wake, after a `claude` update, and after long idle.

## 8. Testing

- Sync/ingest against a throwaway local git remote; fixtures from real state files;
  idempotent re-ingest; atomic snapshot switch; ingest/snapshot SHA agreement.
- Semantics: last-completed-session 1D, "not measurable" never flat, first-seen keyed
  by identity not text, grace periods, unknown ≠ green.
- Analytics: currency translation + local price + residual reconciles to the cent;
  explanation cache invalidates on a history rewrite (fixture: the 27 Aug phantom).
  No golden tests pinned to pre-restart history.
- Migrations up → down → up; verified-dump and restore drills; CI Postgres service.
- Invariants: D0 recorded final weights equal `targets_at` output; recording on/off
  produces identical books; recorder failure leaves trading unchanged; outcomes never
  visible before `knowable_at`.
- Claude and news: a stub `claude` executable; offline link fixtures; one manual smoke
  test per release.
- UI: 5-second test with Matt on two fixtures (a red day, a quiet day); keyboard and
  contrast checklist; offline / Postgres-down / agent-down / first-run states.

## 9. Open questions

- **Q-1** Does FMP's free plan include the economic calendar? (Scheduled releases are
  optional; Claude-sourced headlines remain.)
- **Q-3** "Near the breaker" threshold — measure on real drawdown history.
- **Q-4** Backup retention (proposed: 14 nightly; pre-migration dumps 90 days).
- **Q-5** `claude -p` under launchd — test the cases in §7.
- **Q-6** Price source for per-ticker marks (existing loaders; Yahoo can 403; Tiingo).
- **Q-7** Which Claude model and effort the summaries pin.
- **Q-9** Grace periods per job — tune on real Actions run history.
- **Q-10** Menu-bar item ("▼ A$708 · ● Clear") — in B1 or later?
- ~~Q-2~~ Settled: `sleeve_history` is in A$.
- ~~Q-8~~ Superseded: equity bets judged by excess return and rank correlation (§5 D).

## 10. Expert review (2026-10-09)

Five independent reviewers (architecture, operations, quantitative validity, product
design, product strategy) attacked v1. Every finding recorded in §3 was re-checked by
Claude in code or data before inclusion.

**Changed the plan**
- Sequencing collided with measurement truth (Tasks 10, 22, 26) → measurement truth
  stays #1; this work rebased onto it; D0a after Task 10 (Matt, 2026-10-09).
- D0 recorded the signal, not the bet → two-layer record (signal + execution).
- D0 failed unsafe in CI → fail-safe criteria, append after `save_state`, audit check.
- Backfill date logic wrong on real data (27 Aug is a liquidation; Oct rebalance was
  6 Oct, not 1 Oct) and blamed the wrong cause → dates from git history, replay at
  historical code (Matt, 2026-10-09).
- Attribution lacked currency translation (~90% of FULL's chapter-2 loss) → currency
  layer, FX rates table, currency-aware news.
- "Had we let it play out" double-counted → whole-book policy re-run; no sums.
- History rewrites would poison a forever-cache → cache keyed by data hash.
- Home screen answered "how much" not "am I up", and would be amber daily → hero =
  change; one attention rule; amber = new to you; shapes + words; grace periods.
- Shallow clone contradicted history replay; readers write into snapshots; no single
  writer; backups unverifiable; agent failures silent; `claude -p` not isolated →
  §4, §5 A, §6, §7.
- Postgres carried a backup regime for rebuildable data → derived vs local tables.
- Full vision kept, but **thin-first**: B1 on files first, measured by use (Matt,
  2026-10-09).

**Found along the way**
- Live SMALL micro-mode bug → separate fix now (Matt, 2026-10-09).

**Corrected or not adopted**
- `--safe-mode` (suggested for `claude -p` isolation) is not in the installed Claude
  Code 2.1.142; isolation uses the flags in §7 instead.
- "Cut explainers, news and Claude entirely" (strategy reviewer): not adopted —
  learning is an explicit goal — but the thin-first order and behavioural success
  measures were adopted.

## 11. Modern screens — the next phase (in design)

**Decided by Matt (2026-10-11):**
- Every old terminal screen gets a modern Midnight version, **in phases, most-used
  first**: (1) a book's Overview + Positions, (2) Backtest + Method, (3) Swarm +
  All accounts — then the old terminal retires.
- The same screens serve **both** the Mac app and the GitHub Pages site (phone).
- Built as **one small file per screen** sharing Midnight building blocks — not a
  restyle of the 3,816-line `static/app.js`, and no web framework.

**Part 1 — how they're built and fed (approved by Matt 2026-10-13):**
- Files: one per screen (book Overview, Positions, Backtest, Method, Swarm, All
  accounts, plus the existing home screen), sharing building blocks (cards, period
  chips, charts, tables, status pill, money formatting) extracted from `home.js`.
- Navigation stays in the app: the home sidebar opens a book's tabs in place
  (Overview · Positions · Backtest · Method, + Swarm for FX books).
- Data: each screen requests the same JSON the terminal already uses
  (`/api/account/<book>`, `/api/backtest/<book>`, `/api/swarm/<book>`,
  `/api/overview`, `/api/meta`) plus `/api/home`. Screens never do money maths.
- Phone: `dashboard/export.py --site` already bakes those answers into the page
  behind a small `fetch()` stand-in; the new screens use the same mechanism.
  Differences on the phone: job status is judged at publish time, and the phone
  cannot tell new problems from old ones (Q-1 in `docs/specs/book-screens.md`,
  2026-10-13: no stored first-seen record, so it says so and never shows "All
  clear" while warnings exist — this replaces the earlier "new = last 24 hours").
- Rollout: a "Classic view" link keeps the old terminal one click away until each
  screen is replaced; each phase publishes as it lands.
- Constraint: Pages publishes only from `main`, so the phone gets each phase when
  this branch merges (after measurement truth); the Mac app gets it immediately.
- **Addition (2026-10-13), found while grounding part 3:** the summary text and the
  "Is it working?" panel cannot come from the existing answers alone. Several of
  their inputs are served by no screen today: the FX permutation result
  (`state/permtest_matt.json`, read only by `forex/champions.py`), average win and
  loss, hold length against the signal horizon (`verify.check_horizon`), and how
  old each backtest is. Working them out in the page would break "screens never do
  money maths". So there is **one new server builder,
  `dashboard/book_summary.py`, served at `/api/summary/<book>`** (named
  `book_verdict` until the spec, which widened it to the tiles and Positions
  "why" lines too) and baked into the
  Pages export like the others. It writes sentences from fixed rules over numbers
  (no Claude call; Claude explainers are project C), and every item carries its
  source and date. An input that is missing gives "not measured yet", never a
  guess.

**Part 2 — how a book's screens look (Matt, 2026-10-13; v2 mockup approved — "i like it"):**
- Overview direction **B (numbers first)**, but with **coherent text**: a short
  plain-English summary on top and a one-line caption under every number.
- Add an **"Is it working?"** panel: *going right*, *going wrong*, and *is the
  model any good?* — every figure sourced, small samples and stale results
  labelled (e.g. the equity backtest saved 19 Sep predates the idle-cash credit).
  What it can show today: closed-trade win rate and average win/loss, costs vs
  result, gap to the benchmark, cash share, hold length vs signal horizon (from
  `verify`), backtest CAGR/Sharpe, the FX permutation test. What it can't yet:
  whether the equity ranking picks winners (needs D0a) and per-agent accuracy for
  FX (needs the agent scorecard, project D) — the panel says so rather than
  guessing.
- Positions: grouped by region, each saying whether it's investing or waiting in
  cash and why (no change requested).
- Mockups (throwaway, real data 13 Oct): `.superpowers/brainstorm/*/content/`
  `book-screens.html` (A/B + Positions) and `book-overview-v2.html` (B + text +
  "is it working?" for FULL and FX·Matt).
- Correction recorded: the first mockup said the strategy "only wanted about 29%
  invested" — 29% was the *actual* invested share; the US targets were 31–37%
  gross in Aug–Oct (vol targeting).

**Part 3 — testing and rollout (approved by Matt 2026-10-13):**
- *Server answers (CI):* `book_verdict` is tested on copies of real state files,
  like `test_home_api.py`. There is one test per sentence rule, showing it appears
  only when its condition holds: for example "costs are bigger than the gain" needs
  fees > net result. A missing input reads "not measured yet". A small sample is
  labelled (under 30 closed trades). A backtest always shows its saved date, and
  one older than 14 days adds "may not reflect recent changes". Hold length is
  said in days only for daily-bar books, otherwise in bars.
- *Screen files (CI):* every new screen file must parse (`node --check`, as now
  for `home.js`). The existing "no money maths in the page" check is widened from
  `home.js` to every screen file and the shared building blocks.
- *Real browser (local, skipped where unavailable):* each screen is opened in a
  headless browser — one that runs without a window — against fixture data, at Mac
  width and phone width (390 px). It must load with no script errors, show its key
  text and need no sideways scroll. The exported Pages file is opened the same way
  and must make no network requests. This uses Playwright, which is installed on
  Matt's Mac but not in CI, so CI skips these tests the way it skips the `node`
  check when `node` is missing.
- *Nothing old breaks:* the existing dashboard and export tests stay green, and the
  old terminal stays reachable through "Classic view" on every new screen.
- *Matt's checks per phase:* a 5-second test on FULL and FX·Matt ("what's the one
  thing this screen tells you?"), a keyboard pass, and a look on the phone once
  Pages has it.
- *Rollout:* the Mac app switches the moment a phase is committed; the home
  sidebar opens the new book screens. Pages gets each phase when the branch merges
  to `main`. There the new screens publish *alongside* the current page, linked from
  it, and become the front page only after Matt has used them for a week.
- *Rollback:* phase 1 only reads. It adds no stored file and changes no stored
  format, so no migration is needed and the rollback is reverting the commit (or
  clicking "Classic view"). The migration-and-backup rule applies again the moment
  a phase stores anything.
- *Before any code:* rebase this branch onto `feat/measurement-truth`.

**Findings → fix backlog (Matt, 2026-10-13):** "log the findings and put them into a
fix backlog". Measured on the live books and filed as F20–F26 in
`product/backlog/backlog.json` (branch `docs/fix-backlog`, commit `6796786`, not yet
pushed). The book screens' "going wrong" lines name the item that tracks each. One
result cuts against the obvious fix: on matt, partner and multiasset the positions
held longer did *not* earn more, and a September sweep of the existing min-hold and
hysteresis knobs found nothing out of sample. So F20 is a re-test after the FX
cost-model artefacts are fixed, not a switch to flip.

**Phase 1 spec:** `docs/specs/book-screens.md` (draft, 2026-10-13).
Adversarially reviewed and revised 2026-10-14 (four reviewers; see the spec's
"Adversarial review" section). Phase 1 now ships as 1a (server answers), 1b (Mac
screens) and 1c (phone, own plan). The Overview uses saved values only: the live
re-mark took about 50 s per book and disagreed with the home screen.
