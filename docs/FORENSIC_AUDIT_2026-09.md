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
| 1 | Connect the alert channel | ⬜ |
| 2 | Stop silent data staleness | ⬜ |
| 3 | Finish the champion/challenger loop | ⬜ |
| 4 | Populate the FX backtest tab | ⬜ |
| 5 | Publish the existing reports | ⬜ |
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

---

## A trap worth recording

Three tests failed locally and passed in CI. Cause: local `pandas` was **3.0.2**
while `requirements.txt` demands **>=3.0.5**, and `bdate_range` returns a
different length across that boundary. **Check the local environment satisfies
`requirements.txt` before trusting — or reporting — a local test failure.**
