# Forensic audit — September 2026 (dormant features)

**Scope:** every feature built into this repo that was never switched on, plus
what the July audit (`FORENSIC_AUDIT_2026-07.md`) got right, got wrong, or has
since been overtaken. **This document supersedes the July audit where they
disagree.**

**Remediation plan:** `docs/superpowers/plans/2026-09-19-dormant-feature-remediation.md`

## Method

Every claim is grounded in a file:line citation or a command whose output is
quoted. Claims from the July audit were re-verified against current code rather
than inherited — four of them had gone stale. Two findings I reported in the
first pass were **wrong** and are corrected below; they are kept visible rather
than quietly deleted, because an audit that hides its own misses is not an audit.

---

## Corrections to my own first pass

### The ASX sleeve "never trading" is NOT a bug

`^AXJO` closed at **8731.2 against a 200-day MA of 8821.5** — genuinely below
trend. The regime filter de-risking that sleeve to cash is the designed
behaviour. I initially read a *stale local cache* (see "Cache never expires"
below) whose data ended 2026-07-24 and concluded the feed was dead. It was not:
`yfinance` returns ASX prices through 2026-09-18 on demand.

The defect was in the **auditor**, not the book: `never-traded` fired as an
ERROR every day for 57 days against a sleeve doing exactly its job.

### The 63 closed-market FX fills are history, not a live defect

Bucketed by month, every closed-market fill across all four FX books predates
August. The July C1 session gate works.

| book | closed-market fills | when |
|---|---|---|
| daytrader | 63 | July only |
| matt | 16 | June–July only |
| multiasset | 14 | July only |
| partner | 11 | June–July only |

The defect, again, was the auditor: it re-derives each book from its **whole**
trade ledger, so a bug fixed in July re-fires as a fresh ERROR forever.

**Consequence of both:** the audit reported 5 ERRORs, all false positives. That
is why no alert channel and no CI gate had ever been wired to it — the system
had correctly learned to ignore its own alarm.

---

## July audit items that have since gone stale

| July finding | Status now |
|---|---|
| **M15** FX BACKTEST tab can never leave placeholder mode | **Fixed.** `forex/run_backtest.py:194` writes the cache. Nothing *runs* it, so the tab is still placeholder — a scheduling gap, not an impossibility. |
| **H9/F11** "0 of 49 trades carry a decision price" | **Stale.** 46/81 on `full`, 18/23 on `ultra`. `--tca` produces real numbers today. |
| **H8** the Deflated-Sharpe gate is inert | **Partly stale.** The FX swarm gate runs monthly with a real `n_trials=424`. It promotes nothing — see below. |
| **M13** five risk features off by default | **Confirmed, and worse than stated** — see "double-gated" below. |

---

## Verified measurements (evidence, not assertion)

- **The cost model is honest.** `paper_trade --tca` on `full`: realised **5.0bps
  vs 5.0 modelled (US)**, **8.1 vs 8.0 (FTSE)**. The backtest's cost assumptions
  hold up against actual fills.
- **The DSR gate is correctly calibrated, not broken.** On 717-point series at
  `n_trials=424`, `validation.deflated_sharpe_ratio` scores pure noise
  **0.0000**, a modest edge **0.9705**, a large edge **1.0000**. So "0 of 424
  genomes promoted" means *no edge survived deflation* — the honest answer.
  **Do not lower `DSR_MIN` without re-running this measurement.**
- **The promotion gate works and says NO.** `paper_trade --promotion` on `full`:
  `NOT READY — 4 rebalance months` (needs 6).

---

## Dormant features, by why they are off

### Double-gated (config is off AND the data never arrives)

- **ADV pre-trade cap (F15)** and **market-impact cost (F6)**. `ADV_CAP_PCT` /
  `IMPACT_COEF` are `None`, *and* no caller anywhere passes `volume=` to
  `backtest.run_backtest`. `data.load_volume` had zero callers (it does work —
  verified against live Yahoo). Setting the config alone changes nothing.
  `config.py` additionally **misdescribed** the cap's scope: it is applied in
  `backtest.py`, never in `compute_targets`, so paper trading cannot receive it.
- **Point-in-time constituents.** No region sets `constituents_file`, so
  `--point-in-time` silently falls back to today's universe.
- **Delisting correction.** `None`, *and* gated behind the point-in-time path.

### Built, reachable, never invoked

- **The champion/challenger loop.** No workflow passes `--champions`, and all
  four rosters are empty. Two independent reasons it contributes nothing.
- **`verify --strict`** — inert twice over (never passed; `| tee` swallowed the
  exit code). **Fixed in Phase 0.**
- **Live execution** (`execution_ibkr.py`, `crypto_exec.py`) — zero production
  callers. Deliberately dormant is the right default for live-order code.
- **`--tca` / `--attribution` / `--promotion` / `--purged-cv` / `research.py` /
  `tearsheet.py` / `state_repair.py`** — no automated trigger.
- **`engine --loop`** — the calendar-aware daemon nothing runs, which is why
  `calendars.is_market_open` is test-only.

### Shipped switched off

`use_value`, `PAPER_ALLOCATION_REBALANCE`, `DATA_FALLBACK_SOURCE`,
`NOTIFY_CHANNEL="log"`, and the oanda/alpaca/openbb adapters.

### Infrastructure

- **Cache never expires.** `load_prices` returns a cached parquet forever once
  written — no freshness check. Local files from 24 July were still being served
  on 19 September, so every local backtest ran on 8-week-old prices. CI is
  unaffected (no equity cache there).
- **`NEWS_API_KEY`** is referenced by three workflows and is **not configured**.
- **`TIINGO_API_SECRET`** is configured and referenced by **no code**.
- **`backtest.yml`** last ran 2026-07-24 and **failed**; the dashboard's
  BACKTEST tab has been stale since.
- **`fx-train.yml`** still triggers on a long-merged dev branch.

---

## Remediation status

| Phase | What | Status |
|---|---|---|
| 0 | Make the audit trustworthy | ✅ **done** — 5 errors → 0; `--strict` armed |
| 1 | Connect the alert channel | ✅ **done** (one manual step left) |
| 2 | Stop silent data staleness | ✅ **done** |
| 3 | Finish the champion/challenger loop | ✅ **done** |
| 4 | Populate the FX backtest tab | ✅ **done** |
| 5 | Publish the existing reports | ✅ **done** |
| 6 | Capacity realism (ADV + impact) | ⬜ |
| 7 | Housekeeping | ⬜ |

### Phase 0 — done

`1eb460b` age out historical realism findings to INFO ·
`cd668a2` grade a flat sleeve on evidence (`regime-off` INFO / `data-quality`
ERROR / never-evaluated ERROR / unexplained WARN) ·
`a947044` split the audit into a report half that can never fail the run and a
terminal strict gate that runs after the state commit and the Pages publish.

Live audit went **5 errors → 0 errors**; `verify --strict` exits 0 against the
books and 1 against a book with a never-evaluated funded sleeve.

The remaining ASX finding is now an honest WARN ("evaluated 2026-09-15 and chose
cash, but no reason was recorded") and self-heals to INFO at the next rebalance,
when `paper_trade` persists `last_flat_reason`.

### Phase 1 — done, bar one manual step

`2f0d665` adds a `webhook` notification channel (stdlib `urllib`, no new
dependency) and points `config.NOTIFY_CHANNEL` at it. `ALERT_WEBHOOK_URL` is
wired at **job** level in all three scheduled workflows, so drawdown-breaker
alerts raised during the trading run reach it too — not only the audit step.

Design points worth keeping: no URL configured is a **silent no-op** (so
selecting the channel globally is safe on a laptop), and the **log happens
first**, because `notify()` swallows channel exceptions — posting first would
let a dead endpoint take the local trace down with it. Verified end-to-end
against a local HTTP server; no book data was sent to any external endpoint.

**Remaining manual step:** `gh secret set ALERT_WEBHOOK_URL`. Until that exists
the channel no-ops and nothing is delivered. F12's AC3 stays unmet until then.

### Phase 2 — done

`fb71896` gives the price cache a 20h TTL, scoped to **open-ended** requests
(`end=None`). A closed backtest window is immutable history and still caches
forever, or every backtest would re-download the universe. Verified against the
real poisoned cache **with nothing deleted**: ASX went from serving 2026-07-24
to 2026-09-18.

`1ec0117` alerts `stale_panel` when a region's newest bar is more than
`MAX_PANEL_STALENESS_DAYS` old. This is the failure the per-name gate
structurally cannot see: `data_quality` judges names against each other, so a
region where *every* name freezes on the same day is internally consistent and
looks healthy. A warning rather than an exception — three other sleeves may be
fine, and halting the book over one feed is a worse failure than the one being
reported.

Verified silent on ASX/US/FTSE loaded live, and firing on the exact 57-day
frozen panel that caused this audit's first misdiagnosis.

Still open (F14): the fallback registry itself. `DATA_FALLBACK_SOURCE` is None
and nothing ever calls `register_fallback`, so `_try_fallback` always returns
None. Detection without redundancy — you now learn the feed died, but nothing
takes over.

### Phase 3 — done

**The measurement came first, and it settled the question.**
`5a215db` adds `scripts/measure_champion_gate.py`, which mirrors
`champions.promote` exactly (same hold-out split, same `n_trials`, same
population-wide `sr_variance`) so its numbers **are** the gate's numbers.

| book | hold-out SR max | real DSR max | null p90 | PBO | passed |
|---|---|---|---|---|---|
| matt | +0.087 | 0.0000 | 0.0000 | 0.194 | 0/40 |
| partner | +0.055 | 0.0000 | 0.0000 | 0.329 | 0/40 |
| multiasset | +0.027 | 0.2152 | **0.2958** | 0.437 | 0/40 |
| daytrader | +0.074 | 0.0000 | 0.0000 | 0.111 | 0/40 |

**The gate is correct. `DSR_MIN` is not the binding constraint — do not lower
it.** Hold-out Sharpes of +0.03 to +0.09 are indistinguishable from zero once
deflated for 424 trials, and on `multiasset` random noise scores *higher* than
the best bred genome. No PBO exceeds its ceiling, so nothing was cohort-binned:
the genomes simply have no edge. Promoting zero is the honest answer.

The pipeline is not stuck shut — on a synthetic panel the same code scores max
DSR 1.0000 with 3 of 40 clearing the bar.

`f536e9f` wires `--champions` into the live cycle. Safe today precisely because
every roster is empty: `champions_agents()` returns the core five, and an
isolated A/B produced byte-identical equity, gross, pairs and trades on all four
books. It goes live only when a genome earns promotion.

**Task 3.3 needed no work** — `forex/swarm_view.summary()` already reports the
verdict honestly, distinguishing `none_cleared` from `cohort_overfit` with the
real PBO and `DSR_MIN`, and is already test-covered. Planned work that turns out
to exist is recorded, not rebuilt.

### Phases 4 and 5 — done

`f00c667` refreshes the FX BACKTEST cache on scheduled runs, so the tab stops
showing "ILLUSTRATIVE NUMBERS … PLACEHOLDERS". **Be ready for what it says:**
`matt` over 2003-12 → 2026-09 is CAGR **−8.24%**, Sharpe **−1.93**, max drawdown
**−92.1%**. The placeholders flattered the book. This agrees with the Phase 3
finding — the FX agents have no measurable edge — and the tab will now say so.

`89c0396` adds `monthly-report.yml`, the trigger F5/F11/F3/F10 never had.
`trading_algo.tearsheet` even ships its own `main()` and argparse; nothing had
ever called it.

Two things that surfaced the moment the reports ran:

- **F3 tracking error on `full` is 733bps against a 200bps budget — over.** It
  already raises a `tracking_error` alert, which until Phase 1 nobody received.
- **F10 says NOT READY** for `full` (4 rebalance months of the 6 required). The
  gate works and is correctly withholding.

---

## New finding: `--synthetic` does not isolate state

Found the hard way during Phase 3. Running
`python -m trading_algo.forex.engine --once --synthetic` **overwrote the live
`state/fx_state_*.json` and `fx_books.db`** with synthetic results — four live
paper books clobbered by a pipeline test. Recovered with `git checkout --
state/` (the state is tracked, which is what saved it).

This is a straight violation of **invariant 5**: synthetic results are pipeline
tests only, yet the synthetic path writes to the live state directory. `--init`
already refuses to destroy a live book (July's C2); `--synthetic` has no such
protection.

**Until fixed, always isolate:**
`FX_STATE_DIR=/tmp/x MOMENTUM_STATE_DIR=/tmp/x python -m trading_algo.forex.engine --once --synthetic`

Candidate fix: make `--synthetic` default its state dir to a scratch path, or
refuse to write over a state file whose book was opened non-synthetically.
**Not yet fixed — logged for Phase 7.**

---

## A trap worth recording

Three tests failed locally and passed in CI. Cause: local `pandas` was **3.0.2**
while `requirements.txt` demands **>=3.0.5**, and `bdate_range` returns a
different length across that boundary. **Check the local environment satisfies
`requirements.txt` before trusting — or reporting — a local test failure.**
