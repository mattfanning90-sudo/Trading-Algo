# Forensic audit — July 2026

Audit of `main` @ `992752f` (after merging #57, #74, #65, #78, #79, #77).

**Scope**: code, architecture, algorithms, test strategy, built-but-not-wired
surface, and dead code. **Baseline**: `pytest -q` → **673 passed** (635 test
functions; parametrisation expands them), ruff clean, mypy clean on the 14
money-path files.

## Method

Two passes, cross-checked against each other:

1. **Mechanical.** An AST pass over every `.py` counting `Name`/`Attribute`
   loads — including same-file references, so a helper called only by its own
   module's `main()` is not miscounted as dead — kept separate from prose
   mentions in `.md`/`.yml`/`.js`/comments. Plus a computed import graph
   (fan-in/fan-out, cycles, cross-subsystem edges) and a smoke test of all 26
   entrypoints.
2. **Specialist review.** Ten parallel auditors over disjoint slices, each
   required to supply reproducible evidence.

**Every finding below was independently re-verified before being written down.**
Two agent claims were corrected in the process (see *Corrections*), and two of my
own early conclusions were wrong and are retracted. That matters more than the
count of findings: an audit that reports a confident falsehood is worse than one
that reports less.

---

## Verdict

This is a well-built system with a **reporting-integrity problem, not a
construction problem**. The architecture is genuinely clean (zero import cycles;
the FX subsystem touches the equity core through 5 leaf-utility edges). The test
suite is real — **634 of 635 test functions carry a substantive assertion**. The
signal maths is correctly causal.

What is wrong is concentrated in two places:

- **The backtest is optimistic in two specific, fixable ways** — it fills at the
  decision bar's close, and it never charges the per-region commission floor.
  Both inflate reported performance, and neither is caught by the existing gates.
- **A large, well-tested surface is built but not wired**, and in several places
  a *safety* mechanism is the unwired part. The worst instance has already caused
  104 bad fills in the committed live state.

Three defects were serious enough to fix in this pass rather than only report:
the FX market-hours gate (C1), `--init` destroying a live book (C2), and
whole-share rounding breaking the market-neutral book (C3). Everything else is
recorded for a decision.

---

## CRITICAL

### C1. The FX market-hours gate is wired only into the loop path nothing uses — 104 closed-market fills in live state

`forex/engine.py:36` defines `fx_market_open()`. Its **only** call site is
`engine.py:77`, inside `run_loop`. `run_once` (line 49) never consults it — and
all three workflows (`day-paper.yml`, `fx-paper.yml`, plus the FX leg) invoke
`--once`.

Verified by running the newly-merged verifier against committed state:

```
$ python -m trading_algo.verify
fx:daytrader   ✗ closed-market-trade: 63 trades ... across 7 symbols
fx:matt        ✗ closed-market-trade: 16 trades ... across 6 symbols
fx:multiasset  ✗ closed-market-trade: 14 trades ... across 9 symbols
fx:partner     ✗ closed-market-trade: 11 trades ... across 5 symbols
```

**104 fills against a forward-filled price no venue was quoting**, plus 70
`dead-price-turnover` position changes made at an unchanged price — spread paid,
no exposure gained.

The `daytrader` book shows what this costs: **gross turnover 85.1× equity over
159 bars, implied spread cost 2.57% against a net return of −2.57%.** The entire
loss is the spread.

### ✅ C1 — FIXED

The fix turned out to be larger, and better, than the "call `fx_market_open()` in
`run_once`" I first proposed. Three things made the naive version wrong:

1. **`engine.run_once` is not the convergence point.** `fx_book.main` (i.e.
   `python -m trading_algo.forex.paper`) calls `fx_book.run_once`/`run_all`
   *directly*, bypassing `engine` entirely. Gating the engine would have left
   that path open.
2. **A wall-clock gate makes tests time-dependent.** Three tests in
   `test_fx_currency.py` call `run_once` non-synthetically; a clock-based gate
   would pass or fail by the day of the week the suite happened to run.
3. **The gate must agree with the auditor.** If `fx_book` and
   `verify.check_market_hours` each carried their own boundaries, the gate could
   permit a fill the audit then flags forever.

What shipped instead:

- **`forex/sessions.py`** — one leaf module owning the session boundaries
  (`bar_is_tradable`, `parse_bar`, `is_crypto`). Both `fx_book` (which prevents)
  and `verify` (which audits) import it, so they cannot drift. `verify._parse`
  now delegates to it too, and `engine.fx_market_open` is a thin wall-clock
  convenience over it for the `--loop` idle message.
- **The gate keys off the BAR's timestamp, not the clock** — so it protects
  `--once` and `--loop` alike, is deterministic under test, and refuses a stale
  bar whenever it is replayed.
- **It gates per SYMBOL and holds rather than flattens.** Crypto keeps trading
  (genuinely 24/7); a shut FX pair keeps its existing weight instead of being
  "sold" at a price nobody was quoting. Marking still happens — valuation on a
  stale close is fine, transacting on it is not.

`tests/test_fx_sessions.py` (14 tests) pins it, including the two that matter
most: a control proving an *open* bar still fills (so the closed-bar test cannot
pass for the wrong reason), and two asserting the gate and the verifier agree on
both the FX and the weekend-crypto case.

**Residual, deliberately not fixed**: weekend and FX-week boundaries are
modelled; per-exchange intraday *cash equity* hours are not. An equity ETF in the
`multiasset` book can still be traded on a bar inside the FX week but outside its
own session. Closing that needs per-instrument calendars — a design decision, not
a fix — and it is documented in `sessions.py`'s docstring.

### C2. `paper_trade --init` silently destroyed a live book — ✅ FIXED

`init_account` built a fresh state with `"trades": []` and **no existence check**,
then saved it. Since every P&L number is *derived* from that ledger, re-running
the line CLAUDE.md documents verbatim —

```
python -m trading_algo.paper_trade --account full --init --capital 100000
```

— wiped the entire record of a funded book.

The asymmetry is what makes this an oversight rather than a choice:
`forex.fx_book.init_account` has always guarded it
(`if account_exists(account) and not force: … use --force to reset`). The equity
side never did. The CI workflow protects *itself* with a
`if [ ! -f state/paper_state_$name.json ]` shell test — so the footgun was aimed
squarely at a human following the docs.

**Fixed** by mirroring the FX behaviour: `init_account` now raises unless
`force=True`, with a message that explains what would be lost, and `--force` is
wired through the CLI. `tests/test_paper_trade.py` pins it — the refused `--init`
must leave the ledger byte-identical, and `--force` must still reset.

### C3. Whole-share rounding broke the "market-neutral" book — ✅ FIXED

`profiles.py` configures `experimental` as `top_n=6, short_n=6`,
`max_gross=2.0`, labelled **"MARKET-NEUTRAL LONG/SHORT · PURE ALPHA"**, and keeps
the drawdown breaker on the assumption that beta is hedged out.

The live book holds **1 long (SMH) against 6 shorts** — so five of six longs
rounded to **zero shares**.

`signals.select_long_short` does hedge properly: both legs are normalised to ±1,
and it already refuses to trade when there aren't enough names
(`not enough names to hedge — stay flat`). But that check happens in *weight*
space. `paper_trade` then does `int((equity * w) / price)`, and `int()` truncates
toward zero — so on a small sleeve an expensive name rounds to nothing. Lose
enough of one leg and a book advertised as market-neutral is running a
directional short bet with a breaker sized for a hedged one.

**Fixed** by applying the *same* can't-hedge-then-stay-flat rule to the book
actually executable: after rounding, if residual net exposure
`|L−S| / (L+S)` exceeds `config.LS_MAX_NET_EXPOSURE` (0.20, `None` disables),
the sleeve holds cash, prints the long/short notionals, and raises an
`ls_not_neutral` alert. Three tests pin it, including a control proving a
*hedgeable* book still trades and one proving long-only books are untouched.

Note this will flatten the live `experimental` book on its next run — correct, in
my view: it is currently carrying unintended directional risk. Set
`LS_MAX_NET_EXPOSURE = None` to keep the old behaviour deliberately.

### C4. The dashboard showed "BREAKER ARMED" on the book with no breaker — ✅ FIXED

`profiles.py` gives `ultra` (3× gross, 35% vol target, all timing filters off)
`max_drawdown_stop=None` — the circuit breaker is **deliberately disabled** so the
book "runs hot on purpose".

The payload side is correct and was already tested: `api.build_snapshot("ultra")`
returns `breaker: None`, pinned by `test_dashboard_api_book.py` (which arrived
with #77). But `static/app.js` then rendered
`num(page.breaker * 100, 0)` with **no null branch**, and in JavaScript
`null * 100 === 0`. So the most leveraged book on the platform displayed:

```
BREAKER ARMED @ −0%          (in green)
```

— claiming a safety net that does not exist, on precisely the book where its
absence matters most. #77 fixed the Python half and left the render.

**Fixed** at all **three** render sites. I had found two by reading; the guard
test I wrote then failed and surfaced a third (`app.js:1307`, a compact
single-expression variant) — which is the argument for writing the test before
declaring the fix done.

`app.js` has no JS harness, so the guard asserts at the source level (the
technique `test_fx_marks.py` already uses to pin formulas out of a module): every
`page.breaker * 100` must sit downstream of a `page.breaker == null` check. I
mutation-tested it — replacing the guard makes it fail, restoring it makes it
pass. It is a guard against regression, not a substitute for a rendering test;
**zero automated coverage of `static/app.js` remains an open finding**, and it is
the layer where the confirmed misreports live.

---

## HIGH

### H2. The backtest fills at the decision bar's close — one bar of implementation lag was deleted

Commit `91e6a22` changed `elif yday in weight_schedule:` to
`elif today in weight_schedule:` in `backtest.py`. Its rationale says keying off
`yday` "delayed it to D_{k+2}". That reading is wrong: applying on D_{k+2} and
earning the D_{k+1}→D_{k+2} return **is** t+1 execution — the implied fill is at
close(D_{k+1}), one bar after the D_k signal.

Under the current code, a target decided as-of D_k earns `rets[D_{k+1}]` =
`close(D_{k+1})/close(D_k) − 1`. Earning that return requires holding the new
weights from **close(D_k)** — the very bar the signal was computed from. That is
zero implementation lag.

Verified empirically on synthetic US data, all 92 rebalance dates:

```
rebalance asof 2013-02-28  ->  weights first held on 2013-03-01
   weights_hist[2013-03-01] == weight_schedule[2013-02-28] : True
   new_w . rets[2013-03-01] = +0.000726   actual = +0.000320   (diff = cost)
```

The surrounding comment claims "True t+1". It is not. Invariant #1's *information*
half holds; its *execution* half does not.

**Why no gate caught it**: `test_consistency`'s lag assertion accepts 1, 2 **or**
3 bars, and the F16 regression tolerance is ~10× the effect size of this change.

### H3. No backtest path applies the per-region commission floor

`fees.turnover_cost` — the single backtest cost entrypoint — uses
`round_trip_cost_rate`, which is pure basis points. `fees.commission` (which
applies `max(min_commission, notional·bps)`) is called **only** by
`paper_trade.py:431`.

So paper trading pays the floor and the backtest does not. Quantified at the
actual funded sleeve size (100k AUD split three ways, `top_n=10`):

| region | trade notional | backtest | paper | understated |
|---|---|---|---|---|
| ASX | 3,333 | 2.67 | 5.00 | **47%** |
| ASX | 1,000 | 0.80 | 5.00 | **84%** |
| US | 3,333 | 0.67 | 1.00 | **33%** |
| US | 1,000 | 0.20 | 1.00 | **80%** |
| FTSE | 1,000 | 0.50 | 1.00 | **50%** |

Break-even notional is 6,250 AUD (ASX), 5,000 USD (US), 2,000 GBP (FTSE) — above
typical per-name trade size, so the floor is the *dominant* commission term at
this account size and the backtest ignores it entirely.

This is an invariant #2 gap: costs are on, but systematically understated.
CLAUDE.md invariant #4 says the floor is "respected" — only in paper.

### H4. Micro mode is a second sizing path, live on a scheduled account

`paper_trade.py:392`: when a long-only sleeve's equity is below
`MICRO_THRESHOLD = 5,000`, paper trading **discards** the vol-targeted weights
`compute_targets` produced and substitutes `0.97/len(picks)` across up to 3
affordable names. `backtest.py` has no equivalent.

The `small` account is funded with **1,000 USD** in `paper-trade.yml`, so it is
permanently in this mode. Its realised book is a 3-name equal-weight
concentration that no backtest models, reported on the dashboard beside books
whose weights did come from `compute_targets`.

Invariant #3 is technically satisfied (targets *are* computed) but then
overridden. Test coverage is `test_micro_account_does_not_crash` — a smoke test —
and `test_property_invariants.py:230` explicitly keeps equity **above**
`MICRO_THRESHOLD` "so the book holds the full book, not the micro-mode
concentration", i.e. the property tests route around it.

### H5. Short borrow cost is deferred on a precondition that has already fired

`config.py:264`: *"Borrow/short-financing cost is intentionally deferred until a
short book exists."*

A short book exists and is running daily in CI. From committed state:

```
experimental / US:  1 long (SMH), 6 SHORT (ACN −3, BSX −12, INTU −1,
                                            NFLX −9, NOW −3, ZTS −6)
```

`profiles.py` runs `experimental` at `max_gross 2.0` (long/short) and `ultra` at
`max_gross 3.0`, both as live paper accounts. Neither backtest nor paper charges
any financing or borrow cost, so both books' P&L is overstated by an unmodelled
real cost. The deferral note is now stale, not wrong-in-principle.

### H6. A missing FX pair silently zeroes a sleeve's contribution to the reported AUD portfolio

Reported by the invariants auditor and consistent with `fx.align_fx`'s structure:
an absent FX pair produces a zero contribution rather than an error, so a sleeve
can vanish from the AUD total with no warning. Reported as **likely** rather than
certain — I confirmed the code path but did not construct the failing download.

### H7. `MetaLabeler` is trained on every ML run and never used

`ml_agent.py:111` defines it; `forex/__init__.py:24` exports it; `README.md:209`
and `docs/FX_DEEP_RESEARCH.md:143` say it "sizes the ensemble's side".

`MetaLabeler(` **never appears anywhere** — not in production, not in tests.
`train.py:71-76` trains a bundle and writes `meta_label.json`; nothing ever reads
that file. The `fx-paper` workflow with `--ml` retrains it every run.

Either wire it or delete it and correct both documents — but it should not keep
consuming a daily CI run while the docs claim it shapes position sizing.

### H8. The Deflated-Sharpe overfitting gate is inert

The F17 experiment ledger is written only by `run_backtest`, to paths that
`.gitignore` excludes, on ephemeral runners. The sweep and tune searches — the
things that actually generate trials — never write it. So `n_trials` is
effectively 1 and the Deflated Sharpe applies **no** deflation.

An overfitting gate that never penalises is worse than none, because it lends
false confidence. Related: the F8 purged walk-forward reportedly retains 96.6% of
in-sample rows per fold, so its DSR/PBO is not out-of-sample evidence — flagged
for follow-up, not independently re-derived.

### H9. The IBKR live-execution layer has zero production callers

`execution_ibkr.py` (169 LOC, mypy-gated, mutmut-gated, ~91% covered) is
reachable only from tests and a README snippet. Consequently the F10 promotion
gate (`promotion.require_live_ok`) is unreachable outside tests, and F11
execution TCA reports "No fills with a decision price yet" on every live book
(0 of 49 committed trades carry a decision price).

Read charitably this is *fail-safe*: no code path can accidentally place a real
order. That is the right default. But it means the promotion gate protecting live
trading has never run in anger.

---

## MEDIUM

### M10. `--point-in-time` misreports on the portfolio path

No region sets `constituents_file`, so `constituents.get_membership()` returns
`None` for ASX, US, FTSE and TSX alike.

- `run_single` is **honest**: it warns (`⚠ no constituents file … falling back to
  current universe`) and `backtest.py:194` records
  `"point_in_time": membership is not None` — the *achieved* state.
- `portfolio_backtest.py:131` records `"point_in_time": point_in_time` — the
  *requested flag*.

So `python -m trading_algo.run_backtest --point-in-time` (the documented headline
command, which runs the portfolio path) prints:

```
Universe: point-in-time constituents (survivorship-bias corrected)
```

and stamps the run manifest `point_in_time=True`, while every sleeve ran on the
static universe. `delisting.py` is also unreachable for the same reason.

**Fix**: have `portfolio_backtest` report the achieved state and warn, exactly as
`run_single` does. One line plus a warning.

### M11. `fx_books.db` — one binary SQLite file, two workflows, `-X theirs`

All four FX accounts live in **one** file (`fx_book.py:54`). `save_state` dual-writes
that DB *and* a per-account JSON; `load_state` reads the **DB first**, treating it
as authoritative.

`day-paper.yml` and `fx-paper.yml` both run `forex.paper --init`, both
`git add -A state/`, and sit in **different** `concurrency:` groups — so GitHub
will run them simultaneously. On overlap the loser rebases; the binary `.db`
cannot merge, so the retry path runs `git rebase -X theirs`, taking one whole
side and silently discarding the other run's book writes. The per-account JSONs
merge cleanly and drift **ahead** of the authoritative DB.

Current crons leave a 53-minute gap, so this needs a manual dispatch or an
overrunning run. Reachable, not routine.

**Fix**: one shared `concurrency:` group across all three state workflows, or stop
committing the binary DB.

### M12. `engine --once` never consults the market calendar

The equity analogue of C1: the timezone-aware close-aware scheduling in
`calendars.py` exists only on the `--loop` path, which nothing automates.
`calendars.is_market_open` / `is_after_close` are called only by their test.

### M13. Five shipped, tested risk features are off by default

ADV pre-trade cap (`ADV_CAP_PCT=None`), market-impact cost (`IMPACT_COEF=None`),
delisting correction, the data-source fallback registry (nothing ever registers a
source, so `_try_fallback` always returns `None`), and paper allocation rebalance.
`data.load_volume` has zero callers, so the ADV/impact features cannot be
activated without new plumbing — `config.py` nonetheless claims the cap "is
applied inside strategy.compute_targets so backtest and paper size identically",
which paper never receives.

### M14. Sleeve Sharpe/Sortino deduct an AUD cash rate from USD and GBP returns

An invariant #6 leak: the AUD 3.5% risk-free is subtracted from local-currency
sleeve returns, and the label asserts otherwise.

### M15. The FX BACKTEST tab can never leave placeholder mode

`state/fx_backtest_{account}.json` is **read** by `backtest_store.py:39` and
written by nothing in the repo. The tab's own banner instructs the reader to
"POPULATE state/fx_backtest_{account}.json (python -m trading_algo.forex.run_backtest
/ walkforward)" — neither command writes that file, so the remediation is
impossible and the tab is permanently in fallback.

**To the code's credit** the fallback figures are clearly labelled — an amber
`⚠ WALK-FORWARD VALIDATION · ILLUSTRATIVE NUMBERS … THE FIGURES ARE PLACEHOLDERS`
banner, and `load_backtest` correctly returns `{"available": False}`. This is
honest design, *not* fabricated metrics.

The residual defect is the juxtaposition: `app.js:2033` renders
`COSTS + CARRY MODELLED · NO LOOKAHEAD` **unconditionally** in the same banner
row — a methodological-rigour claim sitting beside invented figures.

### M16. `paper-trade.yml` throws away its own dashboard exports

It runs four full dashboard exports plus a bespoke landing page into `public/`,
which is gitignored and never uploaded, and its index links point at filenames
the real site never produces. Every weekday run discards the output.

### M17. Risk alerts only ever reach a log

`notifications.register_channel` is called only by tests; the sole registered
channel prints to a log. The abstraction exists precisely so unattended risk
events (drawdown halts, FX unavailability) are not lost in a log.

### M18. The FX "FX-unavailable" honesty flag is plumbed but rendered in only one place

Added by #77. `fx_pnl._factor` returns `(1.0, False)` when either end of a hold
lacks an `aud_per_quote` stamp — i.e. the AUD P&L is computed with **no FX
translation**, and the result carries `fx_known: False` to say so. That is good
design.

`dashboard/fx_api.py` attaches `fx_known` to **three** payloads (open marks
~line 109, the position view ~line 148, and the closed round-trips ~line 171,
alongside `net` and `return_pct`). `app.js` renders the `·FX?` marker in exactly
**one** place — line 2045, the open-lots row.

So a *realised* round-trip whose FX translation was unavailable is displayed as a
definitive AUD `net` and return percentage, with nothing marking it. The flag
already reaches the client; only the render is missing.

**Fix**: render the marker (or dim the figure) wherever `fx_known` is false —
most importantly in the closed-trades ledger, where the number reads as settled.

### M19. Two FIFO lot engines now exist, by design

`pnl.py` (`_open`/`_close`/`apply_fill`/`build_lots`) and `forex/fx_pnl.py`
(`_open`/`_close`/`apply_delta`/`build_lots`) are structural mirrors.

This duplication is **justified**, not careless: equity matches whole-share `int`
quantities in one currency with exact `== 0` lot exhaustion, while FX matches
signed `float` weights with a `DUST` epsilon and routes gross through
`marks.trade_mark` for cross-currency translation plus an `fx_known` flag.
Same shape, genuinely different domain.

The risk is drift in the *shared* semantics both must honour — oldest-first
matching, sign-aware gross, net after entry+exit costs. Nothing currently pins
those two engines against each other.

**Suggested**: one shared property test asserting both engines agree on a
common scenario set (partial close, full close, flip through zero, short
round-trip) rather than merging them.

### M20. Duplicated metric maths

`s / s.cummax() - 1` is hand-inlined at `metrics.py:33`, `paper_trade.py:720`,
`forex/fx_book.py:457`, `forex/dashboard.py:273` and `:573`. Annualisation is
worse: `attribution.py:28`, `forex/fx_config.py:25`, `forex/indicators.py:51`,
six inline `252` literals in `metrics.py`, and once more in browser JavaScript
with a hardcoded 0.035 risk-free.

Credit: the **hard** statistics are properly shared — `validation.py` owns
PSR/DSR/PBO and `forex/validation.py` re-exports it. Only the trivial formulas
got copied.

---

## LOW

- **L19. The regime gate can fail open.** `precompute` builds `risk_on` via
  `.reindex(prices.index).ffill()`; a leading NaN survives, becomes `object`
  dtype, and `bool(nan) is True`. Reproduced: full 0.96 gross book taken with
  the regime unknown. **Latent only** — every production loader derives prices
  and the index from the same frame, and both reachable NaN cases (MA warm-up,
  a NaN hole) fail *closed*. Cheap insurance:
  `.fillna(False).astype(bool)`.
- **L20. One test asserts nothing.** `test_paper_trade.py:82`
  `test_force_rebalance_resets_months` — its only claim is the comment
  `# second run should not raise`; it never checks that months were reset.
  Also 5 loose `>= 0` / `is not None` assertions. It is the **only** one of 635
  test functions without a real assertion — including all of #77's new
  `test_fx_pnl.py` — so this dimension is a credit to the suite.
- **L21. Test-count drift.** Every doc states a different wrong number; the
  dashboard computes a fourth. (CLAUDE.md corrected to ~600 in this pass.)
- **L22. `tools/build_vault_notes.py` treats any `argv[1]` as an output
  directory** — no argument parsing, so `--help` creates a directory named
  `--help`.
- **L23. `build_site.sh` publishes a degraded site silently.** Every export is
  suffixed `|| echo "skip …"`; a failed book is simply absent, with no banner
  distinguishing "failed" from "doesn't exist".
- **L24. `tearsheet.py`, `attribution.py`, `research.py`, `sweep`, `tune`** have
  no automated trigger. Three alternative data feeds (oanda, alpaca, openbb) are
  fully implemented behind a resolver no automated caller ever selects. The
  ccxt crypto stack is configured for nothing.
- **L25. `verify.py` can never fail CI** — it runs without `--strict` and is
  additionally `| tee`'d (GitHub's `bash -e` has no `pipefail`). The workflow
  comment says "Report-only: never blocks the run", so this is intentional; the
  open item is flipping it once C1 is fixed. Also uses deprecated
  `datetime.utcnow()`, hidden by `filterwarnings = ignore::DeprecationWarning`.

---

## What is genuinely good

Worth recording, because an audit that only lists faults misrepresents the system.

- **No lookahead in the signal path.** Every op in `signals.py` is causal
  (`shift`, `rolling`, `pct_change(fill_method=None)`) — no full-sample
  normalisation, no `bfill`. The recent panel hoist (`0877386`) is clean: the
  invariants auditor proved truncation-invariance across 8 book shapes with
  per-date weights matching to 0.0e+00, and the panel/params mismatch guards
  raise loudly rather than silently dropping a filter.
- **Zero import cycles**, and layering is correct: `dashboard → {core, forex}`
  as the top layer; `forex → core` is just 5 edges, all to shared leaf utilities
  (`metrics`, `notifications`, `storage`, `config`, `validation`). CLAUDE.md's
  claim that the FX subsystem is independent is **true, with evidence**.
- **P&L is derived, not stored.** `pnl.apply_fill` replays actual fills with
  signed FIFO; there is deliberately no parallel `cost_basis` counter to drift.
- **Every documented CLI runs.** All 26 entrypoints exit 0.
- **`verify.py` (#79) immediately earned its place** — it is what surfaced C1.
- **Invariant #5 is well handled**: synthetic flags propagate into the dashboard
  cache and banners.

---

## Dead code removed in this pass

Each verified as zero code references repo-wide (see *Method*):
`calendars.session_date`, `Region.to_local`, `forex.features.direction_labels`,
`forex.indicators.macd`, `forex.nn.StandardScaler.fit_transform`,
`ALPACA_UNIVERSE`, `OPENBB_UNIVERSE`, and the superseded long-only FIFO helpers
`pnl.add_lot` / `pnl.consume` (the live short-aware path is `pnl.apply_fill`).

Also: `docs/explainer/index.html` corrected — it showed the two deleted FIFO
helpers as the *live* P&L path; `.coverage` untracked; `public/` gitignored.

## Deletion candidates left for an owner decision

Dead code, but each is the **input** to a documented-but-unwired feature.
Deleting them forecloses finishing it:

| target | blocks |
|---|---|
| `data.load_volume` | ADV pre-trade cap (F15) + market-impact cost (F6) |
| `forex.crypto_data.fetch_funding` | crypto funding carry |
| `execution_ibkr.py` (whole module) | live trading (see H9) |
| `tearsheet.py` | monthly reporting its docstring promises |
| `MetaLabeler` + `meta_label.json` | meta-labeling (see H7) |

Plus 11 symbols whose only caller is a test: `calendars.is_market_open` /
`is_after_close`, `config.cooldown_steps`, `data.register_fallback`,
`data.synthetic_volume`, `forex.indicators.StreamingEMA` / `StreamingATR`,
`MLP.predict_proba`, `Pair.is_jpy`, `forex.walkforward.fit_final_model`,
`manifest.validate_manifest`.

---

## Corrections made during this audit

Recorded because they bear on how much weight to give the rest.

1. **I wrongly reported that invariant #1 "genuinely holds".** It holds on the
   information side; the **execution** side lost its one-bar lag (H2). I had
   checked `signals.py` and `strategy.py` for causal ops but not the fill offset
   in `backtest.py`'s loop.
2. **I wrongly reported #61/#65/#12 as "already absorbed, zero diff".** That was
   a shallow-clone artifact. After unshallowing: #61 is 1 commit / 2 files with
   1 conflict; #12 is 53 commits / 6,684 insertions with 7 conflicts.
3. **I wrongly reported `tools/build_obsidian_vault.py --help` as passing.** It
   "passed" by writing a vault into a directory named `--help` (L22).
4. **An agent claimed the public site "ships hardcoded fabricated performance
   metrics".** Overstated — the placeholders carry a prominent warning banner.
   Corrected to M15.
5. **An agent classified `StandardScaler.fit_transform` as test-only.** It has
   zero references at all; it was deleted, not kept.
6. **My first mechanical pass produced false positives** (`indicators.true_range`,
   `fxconv.usd_per`) because it ignored same-file references. The analyser was
   rebuilt before anything was deleted.

## Recommended order of work

1. **C1** — market-hours gate in `run_once`. Small, and it is actively corrupting
   live state.
2. **H2 + H3** — restore the execution lag; apply the commission floor in the
   backtest. Then tighten `test_consistency`'s lag assertion (it accepts 1–3
   bars) and the F16 tolerance so neither can regress silently.
3. **M10** — make `portfolio_backtest` report the achieved PIT state.
4. **H5** — model borrow cost, or stop running the long/short book.
5. **M11** — one concurrency group for the state workflows.
6. **H4** — decide whether micro mode is legitimate; if so, model it in the
   backtest and stop routing the property tests around it.
7. **H7 / H8 / H9** — wire or delete, and correct the docs either way.
