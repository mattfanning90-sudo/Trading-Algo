# Measurement Truth (Design)

**Status:** Design agreed, pending implementation plan
**Date:** 2026-09-24
**Scope:** The equity stack's measurement surface — what the two engines compute,
what the books record, and what is published. Plus point-in-time index membership
for all four regions. The FX subsystem gets two cheap changes and is otherwise
deferred to its own block (§15). No change to expected return is in scope (§11).
**Source:** `docs/FULL_SYSTEM_REVIEW_2026-09.md`, findings F1, F2, F5–F7, F9–F10,
F14–F15, F19–F22, and the semantic-layer gap measured during brainstorming.

## 1. What this is

One block of work whose only goal is that **a number in this repo means what it
says**. Nothing here is expected to raise returns. Its output is the ability to
tell a strategy result from a measurement artefact, which every later decision
depends on and which today is not possible.

The review found three distinct ways the numbers are untrue, and they need
different fixes:

| class | example | fix shape |
|---|---|---|
| the two engines disagree with each other | backtest credits dividends and cash interest; the books receive neither | make both compute the same thing |
| both engines disagree with reality | both fill at the close of the bar that produced the signal | change the convention in both |
| the published number is stale | the dashboard's backtest tab was generated 2026-07-24 | regenerate, and keep regenerating |

A fourth, cutting across all three: **the same measure is implemented many times
and the copies disagree.** That is the semantic-layer gap (§4).

## 2. Evidence this design is built on

All verified against the code at `11f876e` during the review and the
brainstorming that followed. Each is reproducible.

- **The benchmark is a price index; the strategy is total return.**
  `portfolio_backtest.py:116-123` builds the benchmark from `^AXJO`, `^GSPC`,
  `^FTSE`, `^GSPTSE`; `data.py:117` downloads strategy prices with
  `auto_adjust=True`. Against dividend-reinvesting index funds the strategy
  trails by ~2.7pp CAGR and does not win on Sharpe (0.62 vs 0.65).
- **Paper books never receive dividends.** `paper_trade.py:283-289` marks
  positions at the latest close; no code path credits a dividend to cash, and the
  trade ledger carries only `BUY` and `SELL`. Every ex-dividend price drop is
  booked as a loss and the cash never arrives.
- **Cash interest is backtest-only.** Commit `57676c4` added
  `fees.idle_cash_credit` to `backtest.py`; `paper_trade.py` accrues nothing. On
  `full`, which is 72.8% cash, that alone is ~255bps/yr of divergence against a
  200bps tracking budget.
- **Both engines fill at the signal's own close.** `backtest.py` stages the
  target and applies it at the next bar's close;`paper_trade.py:523` fills at the
  same close the decision used. Worth ~0.38–0.40pp/yr of CAGR on ASX and US.
- **The backtest ignores the per-order commission floor.** `fees.py:48-56`
  charges bps only; `fees.commission` (which applies `min_commission`) is called
  only from `paper_trade.py:653`. July finding H3, still open.
- **Two FTSE names are quoted in USD and scaled as pence.** Verified live:
  `CPG.L` and `IHG.L` report `currency USD`; `RR.L` and `HSBA.L` report `GBp`.
  `regions.py:111` applies `price_scale=0.01` to every FTSE name. The `full`
  book bought 593 `IHG.L` at "£1.63" (2026-06-11) and 724 `CPG.L` at "£0.32"
  (2026-07-01) against real prices near £120 and £24.
- **The drawdown breaker latches.** `paper_trade.py:905-920` raises `peak` and
  never lowers it after a halt; same shape at `forex/fx_backtest.py:176-184` and
  `forex/fx_book.py:579-587`. A book still below its old peak after the cooldown
  re-trips immediately. The `matt` FX backtest shows 381 halts and 3,804 flat
  bars. `tests/test_backtest.py:36` only asserts `halts >= 1`.
- **Whole shares always round down.** `paper_trade.py:476` uses `int()`, removing
  16–47% of the US sleeve's target monthly on $400–$930 names.
- **Micro mode buys nothing.** `paper_trade.py:460-469` tests affordability
  against the whole balance but buys with a fraction of it; the `small` book has
  placed two trades in its life, none since 2026-07-01.
- **The tracking diagnosis compares incomparable things.**
  `_print_tracking_diagnosis` calls `strategy.compute_targets(px, ix, params)`
  with no `eligible=` argument while the live path passes the data-quality gate's
  result, and it evaluates the target at today's prices against a book built at
  the last rebalance. This produced the September audit's "FTSE holds 54.6%
  against an 80.1% target"; the gated, correctly-dated target was 56.8%.
- **No region sets `constituents_file`** (`regions.py:36`), so every backtest
  selects from today's survivors. Zero of 125 US, 56 ASX and 55 TSX names stopped
  printing in 14.7 years.
- **Point-in-time membership never reaches the live path.** `grep` for
  `membership|members_asof|constituents` over `paper_trade.py` and `engine.py`
  returns nothing; it is read only by `backtest.py`, `portfolio_backtest.py`,
  `sweep.py`, `walkforward.py`, `data_quality.py`, `delisting.py`. **This is the
  fact that lets wave two run without freezing a book.**
- **The regression gate cannot see a dropped cost.** `ci_regression.TOL` allows
  0.02 absolute on CAGR; cumulative cost drag on the synthetic book is smaller
  than that, so deleting transaction costs entirely would pass.
- **41 commits are unmerged.** The schedulers fire only on the default branch
  (`day-paper.yml:10`), so the live books run pre-remediation code.

### The semantic-layer gap, counted

| measure | independent implementations |
|---|---|
| Sharpe | 6, incl. a JavaScript copy at `forex/dashboard.py:1522` with the cash rate hardcoded separately from Python's |
| max drawdown | 4 reported (`metrics.py:43`, `tearsheet.py:32-35`, `fx_pnl`, the FX dashboard) plus 2 inside the breakers |
| annualisation | `252` hardcoded in 12 places against `marks.periods_per_year`, which is calendar-aware and already the FX standard |

Consequences already observed: the repo's own Sharpe study measured the two
Python conventions disagreeing by 0.45 on one series, enough to flip the FTSE
sleeve's sign; the FX page prints two different Sharpes for one curve; the
daytrader backtest tab annualises 60-minute bars at 252.

## 3. Decisions (locked during brainstorming)

| # | Decision | Rationale |
|---|---|---|
| D1 | The block's goal is measurement truth, not return | Cannot tell a strategy result from an artefact today |
| D2 | Point-in-time membership for **all four** regions | Largest single distortion; owner accepted the data cost |
| D3 | Fill convention becomes **next day's close**, both engines | Unambiguously executable; ~0.4pp/yr cost accepted |
| D4 | **Restart all paper books clean** after the fixes | One record under one set of rules; promotion clock restarts |
| D5 | Extend `attribution.py`; do not add a reconciler module | It already owns divergence, tracking error and cost drag |
| D6 | Add a measurement semantic layer with AST enforcement | Same mechanism that has kept the weight function single-sourced |
| D7 | Two waves split on the data dependency | Membership never touches a book, so wave two cannot block wave one |
| D8 | FX books get two cheap changes only (§15) | Full FX treatment is its own block |

## 4. The semantic layer

**One module owns every number a human reads.** `metrics.py` already half does
this; it becomes the definition and gains the rest.

- **Conventions become arguments, never literals.** `periods_per_year` is
  required and sourced from `marks.periods_per_year`. This alone fixes the
  hourly-annualised-at-252 defect.
- **A measure returns a labelled record, not a bare float:** value, convention
  (whether a risk-free rate was subtracted and which), `periods_per_year`, sample
  size, standard error. A consumer then cannot print a Sharpe without saying
  which Sharpe — Sharpe-study decision #8, still open.
- **The JavaScript re-implementation is deleted.** `forex/dashboard.py`'s page
  consumes values computed in Python, as the terminal dashboard already does.
- **Enforcement:** a test walking the syntax tree that fails on any independent
  Sharpe, drawdown or annualisation computation outside the module — the same
  trick `tests/test_consistency.py` uses for the weight primitives.

**Boundary.** Anything reaching a human routes through the layer: dashboard,
tearsheet, reports, CLI output, docs, vault. Optimisation internals keep their
inline maths (`nn.sharpe_net_loss` needs its own gradient; `evolve`'s fitness is
a selection score) — but any of their values that are *displayed* are re-derived
through the layer for display.

## 5. The reconciliation bridge

Extends `attribution.py`. A line-by-line walk from the backtest's return to the
paper book's return over a common window, each line named, each in base currency
and in bps/yr:

dividends · cash interest · fill convention (reusing `tca.implementation_shortfall`)
· commission floor vs bps · stamp duty · exposure gap (gated target vs whole-share
executed) · rebalance-date mismatch · FX translation · residual.

**The acceptance criterion is an identity, not a threshold:**

```
backtest_return − Σ(named lines) − paper_return ≈ 0     (tolerance: 1bp)
```

Same shape as the identity `fx_book` already maintains
(`equity − start == price_pnl + carry − cost`). A line that cannot be explained
lands in `residual` rather than being absorbed.

Two fixes ride along, without which the bridge is useless: the diagnosis must
pass the eligibility gate, and must evaluate the target as of the book's last
rebalance date rather than today.

Ships as `paper_trade --reconcile`; joins `monthly-report.yml`.

**No target figure is set for the residual.** The honest sequence is: fix the
known causes, measure what remains, then decide whether it is acceptable.

## 6. Wave one, staged

Strict dependency order — each stage changes the input to the next, so doing
reporting early means doing it twice.

**Stage 1 — instruments.** The semantic layer (§4), the bridge (§5), the two
diagnosis fixes, and a cost-sensitive check added to `ci_regression`. Comes first
because it is what proves every later stage.

**Stage 2 — conventions.** Next-day-close fills in both engines; dividends
credited to the paper ledger; cash interest paid to the paper books; the
per-order commission floor charged in the backtest.

**Stage 3 — mechanism.** Breaker high-water mark resets after cooldown; FTSE
names handled by their quoted currency rather than a blanket `price_scale`;
whole shares round to nearest; micro mode's affordability test matches the slice
it buys with; a ticker that fails to load is reported, not silently dropped; a
suspected split (a close-to-close ratio matching a known split factor) halts the
sleeve and alerts, without adjusting share counts (§10).

**The breaker fix applies to all four engines** — `paper_trade.py`,
`backtest.py`, `forex/fx_backtest.py` and `forex/fx_book.py` — even though FX is
otherwise deferred. It is one defect with four copies, the FX books run daily,
and leaving a permanent off-switch armed in a live book to respect a scope
boundary would be perverse.

**Stage 4 — regeneration.** Total-return benchmark; then every published number
rebuilt by current code: backtest cache, dashboard, tearsheets, docs, vault. The
permutation result (p = 0.47) and the 2026-08-27 phantom liquidation are written
into the audit record, since neither is there today.

**Stage 5 — deploy and restart.** Merge to `main`; confirm the schedulers run the
new code; archive the current books under `state/archive/` with a README naming
why; reopen clean.

The regression baseline is re-baselined **once**, after stage 3, with the bridge
report attached to that commit as the evidence for each moved number.

## 7. Wave two — survivorship

Runs from day one alongside wave one; cannot disturb a book (§2, the `grep`
result).

1. Source point-in-time membership per region (`date,ticker` CSV or parquet).
2. Set `Region.constituents_file` for each; the mechanism in `constituents.py`
   already exists and `members_asof` is correctly point-in-time
   (`bisect_right` on snapshot dates).
3. Enable `DELISTING_REPLACEMENT_RETURN`, which is gated behind the PIT path.
4. Re-run; measure the bias as the difference between the PIT and non-PIT runs;
   relabel every number.

**Off-ramp.** If a region's data cannot be sourced at sensible cost, that region
falls back to measuring and publishing a *bound* on the bias (the equal-weight
universe vs total-return index gap, already computed: ASX +9.4pp/yr, TSX +5.4,
US +2.5, FTSE ≈0). The block does not stall waiting for data.

## 8. Books: archive and restart

- Current state files and DB rows are copied to `state/archive/2026-09-pre-truth/`
  with a README recording what was wrong with them (phantom liquidation, USD-priced
  FTSE fills, unformable long/short leg, old fill convention).
- Books reopen at stage 5 with the same capital and allocations.
- `full` reopens with TSX included, since `ALLOCATIONS` funds it at 25% and a
  fresh `--init` picks that up — which the running book could not.
- The promotion clock (`MIN_PROMOTION_REBALANCES = 6`) restarts from zero. This
  is the accepted cost of D4.

## 9. Invariants preserved

1. **No lookahead.** Moving to next-day-close fills strengthens it. The property
   test in `tests/test_property_invariants.py` must still pass unchanged.
2. **Costs always on.** Stage 2 *adds* a cost (the commission floor) to the
   backtest. Cash interest is a credit and is labelled as one wherever reported.
3. **One weight function.** Untouched; `compute_targets` is not modified by this
   block. The AST test must still pass.
4. **Whole shares.** Preserved; only the rounding direction changes.
5. **Synthetic is a plumbing test.** Unchanged.
6. **Each sleeve trades in its local currency.** The FTSE currency fix serves
   this invariant rather than bending it.

## 10. Non-goals (YAGNI)

Explicitly **out of scope**, to keep a measurement fix distinguishable from a
strategy change when the numbers move:

- `avg_correlation`, graded de-risking, buy/hold bands, rebalance tranching —
  the **next** block, and the one with actual return upside.
- The FX subsystem's own measurement defects (§14).
- Live-broker readiness: order idempotency, broker reconciliation, kill switch,
  price collars.
- Corporate actions beyond dividends. Splits are a real latent corruption
  (review F22) but no held name has split since June; this block adds *detection*
  (halt the sleeve and alert on a suspected split) and defers *adjustment*.
- The ML layer, the swarm, and the dashboard's duplicate renderer.

## 11. Risks & open questions

| risk | mitigation |
|---|---|
| Every published number moves, most down | Expected and stated up front; the bridge attributes each move to a named cause |
| Re-baselining a behavioural gate while changing behaviour hides a real regression | Gate gets a cost-sensitive check first; re-baselined once, at a defined point, with evidence attached |
| PIT data unobtainable or expensive for some region | Per-region off-ramp to a measured bound (§7) |
| Restarting the books resets the promotion clock | Accepted (D4); stage 5 sits as early as dependencies allow |
| 41 unmerged commits grow further before stage 5 | Stage 5 is a defined gate, not an afterthought |

**Open questions, to answer during implementation rather than now:**

- Which dividend source? `yfinance` exposes a dividend series per ticker; whether
  it is reliable enough for ASX and LSE names is unmeasured.
- What cash rate per currency? The credit currently applies a flat AUD 3.5% to
  USD, GBP and CAD sleeves (review F12/U12). Whether to plumb a per-currency
  short-rate series or to state the simplification is a judgment for the owner.
- Does IBKR offer fractional shares for this account type? If so, stage 3's
  rounding change becomes a larger and better fix.

## 12. Phasing (for the implementation plan)

| phase | content | gate to pass |
|---|---|---|
| P1 | Semantic layer + AST test | No independent metric implementation outside the module; all existing tests green |
| P2 | Bridge + diagnosis fixes + cost-sensitive regression check | Bridge identity holds to 1bp on the current books |
| P3 | Conventions (fill, dividends, interest, commission floor) | Bridge shows the dividend and interest lines at zero |
| P4 | Mechanism (breaker ×4 engines, FTSE currency, rounding, micro mode, ticker reporting, split detection) | New tests per defect; re-baseline the regression gate here |
| P5 | Regeneration (benchmark, caches, docs, vault, audit record) | No published number older than its code |
| P6 | Deploy, archive, restart | Schedulers on new code; books reopened; first clean run green |
| W2 | Survivorship, in parallel from P1 | PIT vs non-PIT delta measured and published per region |

## 13. Implementation constraints

The owner's instruction, and it is load-bearing for this block specifically: a
measurement fix that sprawls cannot be told apart from a rewrite, and then nobody
can say which change moved which number. Operationally:

- **One defect, one change.** No drive-by refactoring of code a fix passes
  through. If something adjacent is wrong, it becomes a finding, not a diff.
- **Fix the concept, not the call sites.** The semantic layer (§4) is this
  principle applied: six Sharpe implementations are not six bugs, they are one
  missing definition. Prefer removing a special case to adding one.
- **Every change carries its test and its number.** The test is written first
  (repo norm); the number is the bridge line it moved, before and after. A fix
  with no measured movement is not finished.
- **A fix that needs more than about thirty lines is a design signal.** Stop,
  say so, and reconsider the abstraction rather than pushing through.
- **No new module unless no existing one can host it** (already D5). `metrics.py`
  and `attribution.py` are the homes for this block.
- **Diffs stay readable in one screen.** The owner reads every diff; that is the
  review mechanism, and it only works if each commit is one idea.

## 14. Testing strategy

**The bridge is the master test.** Each stage names the line it should move,
predicts the direction, and the report either shows it or the stage is not done.

Per-defect tests, written before the fix (TDD, per the repo's norm):

- A London name quoted in USD is converted or rejected, never divided by 100.
- A book still below its old high-water mark after a cooldown is allowed to trade.
- Dividends and cash interest appear identically in both engines over one window.
- A sleeve whose target is computed with the eligibility gate matches what the
  book executed, to whole-share rounding.
- The syntax-tree test of §4.
- The regression gate fails when a transaction cost is removed.

`ci_regression` is re-baselined once (P4) and never silently.

## 15. The next block: FX

Recorded here so it is not lost. The FX subsystem needs a full treatment of its
own, covering at least:

- The **fixed-dollar crypto spread** (`forex/pairs.py:80-82`: BTC at 120 units
  of price), which is 96% of the FX backtest's cost and was ~24% per trade when
  Bitcoin traded at $250. Already named as the prerequisite for any ML retry.
- The **breaker latch** in `fx_backtest.py` / `fx_book.py` (shared with §6 stage 3).
- The **backtest/live divergences**: the backtest trades FX on weekend rows the
  live book freezes (27.8% of FX-leg turnover), and charges one day of carry per
  bar regardless of bar length.
- The **never-expiring price cache** (`forex/fx_data.py:66-76`), restored across
  CI runs, which freezes the panel the nightly refresh and monthly breeder read.
- **Churn**: two thirds of trades merely resize; `daytrader` flips direction 678
  times in 593 bars with hysteresis and minimum-hold at zero on every profile.
- **Whether the four books should exist at this size**, given every one
  back-tests between −2.1 and −6.3 Sharpe and `daytrader`'s live loss is 78%
  trading cost.

Until that block runs, this one does two cheap things plus the shared breaker fix:

1. **The FX books leave the headline assets figure.** `dashboard/overview.py:83`
   currently forces `group = "CORE"` for every non-equity book, which is why they
   sum into the headline. They get their own reporting group, exactly as the
   `EXPERIMENTAL` equity books already do, so they still appear with their own
   total but no longer inflate the core number.
2. **Their dashboard states plainly** that their backtests are negative and under
   review, with the figures.
3. **The breaker latch is fixed in the FX engines too** (§6 stage 3).
