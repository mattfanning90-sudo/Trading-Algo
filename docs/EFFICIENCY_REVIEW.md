# Architecture Review — Efficiency

*Whole-repo review focused on **where the system spends time and network**, with
every claim measured on this machine (synthetic US sleeve, 5,219 rows × 126
names, 2006–2026) rather than estimated. Complements
[`CTO_ARCHITECTURE_BENCHMARK.md`](CTO_ARCHITECTURE_BENCHMARK.md), which reviews
correctness and research integrity; this one reviews **cost**.*

---

## 1. Verdict

The architecture is sound — the slowness is not structural, it is **four
recompute loops in specific functions**. Nothing here requires rethinking the
Region record, the single-weight-function invariant, or the sleeve/portfolio
split. Every finding below is a local change behind an unchanged public API, and
the biggest one has **a working reference implementation already in this repo**
(the FX subsystem's `fx_strategy.target_weights_history` / `min_history`).

Measured today, one US sleeve backtest takes **25.2 s**, and **~88 % of that is
work recomputed from scratch on every rebalance date**. Two prototyped fixes take
it to **3.28 s — a 7.7× speedup with `equity`, `returns` and `metrics` all
comparing identical** to the current implementation. The same two fixes compound
into the sweep, the purged-CV gate, the tuner and CI, where the equity backtest
path accounts for roughly 450 s of the suite's ~9-minute runtime.

| Finding | Cost today | After | Confidence |
|---|---|---|---|
| **E1** `compute_targets` rebuilds full-history indicators per rebalance | 13.6 s | 0.56 s | **Measured, output identical** |
| **E2** `data_quality.assess` rescans full history per column per rebalance | 8.6 s | 0.60 s | **Measured, output identical** |
| — *E1 + E2, end to end* | *25.2 s* | *3.28 s* | **Measured, bit-identical** |
| **E3** sweep / purged-CV redo param-independent work 20× | ~504 s | 63.8 s | **Measured** (mostly inherited from E1+E2) |
| **E4** `advd.loc[:today]` inside the daily loop is O(N²) | dormant | — | Read; dormant by default |
| **E5** live path fetches with `use_cache=False`, per account × per region | ~24 full downloads/day | 3 | Read; call sites listed |
| **E6** dashboard has no snapshot cache — every HTTP request re-downloads | per request | per TTL | Read |
| **E7** residual: the 5,219-bar daily loop is now ~90 % of a backtest | 3.0 s | — | **Measured**; optional, higher risk |

---

## 2. What is already right

Worth stating so the recommendations read as targeted, not sweeping:

* **The FX subsystem already solves the main problem.**
  `forex/fx_strategy.target_weights_history()` computes the entire weight matrix
  in one vectorized pass and `fx_backtest` then loops over rows; `min_history(p)`
  trims the live panel to the bars that can still affect the latest weight, so
  live latency is flat as history accumulates. The equity side has neither.
* **Data caching exists and is correctly keyed** — `data._cache_path` hashes the
  actual ticker *set*, so editing a universe without changing its length still
  invalidates. The problem is that the live path opts out of it.
* **The dashboard backtest tab is already cached** to JSON by
  `dashboard/backtest_store.py` — exactly the right call.
* **Shared statistics are genuinely shared** — `forex/validation.py` re-exports
  `trading_algo.validation` rather than forking DSR/PBO; `RiskParams` is common
  to `StrategyParams` and `FXParams`. No duplication to collapse there.
* **Things that look slow but are not.** The `portfolio_backtest` combine loop
  measures **0.08 s** for 3,500 days × 3 sleeves; `constituents.members_asof` is
  a `bisect`; `fees` and `metrics` are trivial. Leave them alone.

---

## 3. Compute — the measured hot path

### E1. `compute_targets` recomputes the whole indicator history on every call

`strategy.compute_targets` (`strategy.py:83-104`) does:

```python
scores = sig.momentum_score(prices, p).loc[asof]     # full frame, one row used
vols   = sig.realised_vol(prices, p).loc[asof]       # rolling(63).std() over 20y
trend  = sig.stock_trend_ok(prices, p).loc[asof]     # rolling(200).mean() over 20y
risk_on = sig.index_risk_on(index_prices, p)...loc[asof]
```

Each is an O(rows × names) rolling pass whose result is thrown away except for
one row. `backtest.run_backtest` calls it **once per rebalance date** — 228 times
over 2006–2026 — so the rolling windows are computed 228 times over identical
data.

**Measured:** 228 calls = **13.58 s**. Hoisting the four frames into a panel
computed once and indexing `.loc[asof]` out of it = **0.56 s**, `24×`, and the
228 weight vectors compare `.equals()` identical.

**The fix, without weakening invariant #3.** Do exactly what FX does: add a
`SignalPanel` (or `strategy.precompute(prices, index_prices, p)`) and an internal
`_targets_at(panel, p, asof, eligible, capacity)`. Keep `compute_targets` as the
public entry point, implemented as `precompute(...)` + `_targets_at(...)` — so
paper trading's single-date call is unchanged and there is still exactly **one**
weight formula. The backtest builds the panel once and calls `_targets_at` per
date. Invariant #1 is untouched: the panel is causal by construction (all
rolling/shift ops), and `.loc[asof]` reads one row, so no future data can reach a
decision. `tests/test_consistency.py` and the `ci_regression` gate both keep
proving it.

**Bonus, for the live path.** FX's `min_history(p)` idea ports directly: the
equity signals need at most `max(lookback_days, stock_trend_ma, index_trend_ma,
vol_lookback+1)` (+ `value_lookback_days` when `use_value`) trailing rows to
produce an identical latest row. Trimming there makes each daily paper decision
O(window) instead of O(14 years of history).

### E2. The data-quality gate rescans all history, per column, per rebalance

`data_quality.assess` (`data_quality.py:75-80`) slices `window = prices.loc[:asof]`
— the whole frame — then loops over all 126 columns doing `col.dropna()` on an
average of ~2,600 rows. But every check it then performs looks only at the last
**20** rows (`GAP_WINDOW`) or the last **6** valid closes (`STALE_DAYS + 1`). The
one check that genuinely needs full history is `len(valid) < 2`.

**Measured:** 228 calls = **8.62 s**. A version that (a) precomputes
`prices.notna().cumsum()` once for the history check and (b) reads a bounded
trailing slice as a NumPy array for the rest = **0.60 s**, `14×`, with an
identical excluded-set on every date.

Two details worth keeping when implementing:

* Keep a per-column fallback to full history when the trailing slice happens to
  hold fewer than 2 valid prints — that is what makes the output *identical*
  rather than merely equivalent, for sparse series.
* The gate is **strategy-parameter-independent** — `assess(prices, region, asof)`
  reads only `region.jump_threshold`. That is what makes E3 possible.

### E3. The sweep and the purged-CV gate redo param-independent work 20×

`sweep.sweep_region` and `walkforward.cv_returns_matrix` both run 20 backtests
(5 `top_n` × 4 `lookback_days`) over the **same** `prices` frame. Today each of
those 20 repeats all of E1 and E2 from scratch. But:

* `top_n` affects **none** of the four signal frames — only the selection step.
  So the 20 configs need **4** panels (one per `lookback_days`), not 20.
* The quality gate depends on neither knob, so it needs **1** pass, not 20.

**Measured, and with an honest correction.** A 20-config `sweep_region` over this
frame projects to **~504 s** today. With E1 + E2 in place and the panel/quality
work shared across the grid, the same 20 configs run in **63.8 s** — but note
that 63.8 / 20 ≈ 3.2 s, essentially the same as a *single* post-fix backtest
(3.28 s). In other words **almost all of the sweep's gain comes from E1 + E2
themselves; the extra sharing across the grid buys only a few seconds**, because
once the indicators are hoisted the per-config cost is dominated by the daily
simulation loop (E7), which cannot be shared.

So E3 is real but modest: implement it as an optional `panel=` / `quality=`
argument on `run_backtest` if it falls out of E1 cleanly, and do not spend design
effort on it otherwise.

This is nonetheless where CI's wall-clock lives — the full suite (572 tests) runs
in ~9 minutes, and the top of the profile is almost entirely equity-backtest
driven:

```
117.74s  tests/test_tune.py::test_report_prints_deflated_sharpe_with_grid…
 60.14s  tests/test_backtest_regression.py::test_metrics_are_deterministic
 56.79s  tests/test_pit_impact.py::test_pit_impact_reports_static_pit_and_delta
 53.27s  tests/test_walkforward_equity.py::test_purged_cv_report_runs_the_gate
 52.72s  tests/test_walkforward_equity.py::test_cv_matrix_shape_and_metadata
 30.09s  tests/test_backtest_regression.py::…matches_baseline  (setup)
 17.44s  tests/test_property_invariants.py::test_no_lookahead_tail_perturbation…
```

By contrast the slowest FX test is 6.21 s. E1 + E2 alone should take the suite
from ~9 minutes to roughly 2–3, and unlock a *wider* robustness grid at today's
cost — which is the point of the sweep, since a broader plateau is stronger
evidence than a narrow one.

### E4. A dormant O(N²) in the market-impact path

`backtest.py:127-131`, inside the **daily** loop:

```python
a = advd.loc[:today]                    # re-slices from row 0 every day
...
v = (vols_frame.loc[:today].iloc[-1]
     if vols_frame is not None and len(vols_frame.loc[:today]) else None)
```

Both slice the full frame from the start on every one of ~5,200 bars, and
`vols_frame.loc[:today]` is built **twice** per bar (once for the length test,
once for the value). This is quadratic in history length and is only reachable
when `IMPACT_COEF` is set — both `IMPACT_COEF` and `ADV_CAP_PCT` default to
`None`, so it is dormant today. It should be fixed *before* anyone turns F6 on,
not after: replace with a positional index (`advd.iloc[i]` / `vols_frame.iloc[i]`
against the shared date index) — the loop already knows `i`.

### E7. What is left afterwards — the daily simulation loop

Worth stating because it bounds the payoff of everything above. After E1 + E2,
a profile of the 3.28 s backtest is **flat**: no single call dominates. It is
5,219 iterations of the daily loop, each doing a handful of small pandas
operations (`rets.loc[today]`, `.reindex`, `.fillna`, two Series multiplies, the
drift renormalisation) whose per-call overhead swamps the arithmetic. Roughly
90 % of the post-fix backtest is this loop.

Two observations:

* **One free fix.** `backtest.py:145` and `backtest.py:174` both compute
  `float((current_w * day_rets).sum())` — the same dot product, twice per bar.
  Compute it once.
* **The real fix is a rewrite, and it is not obviously worth it.** Converting the
  loop to NumPy arrays over a fixed column order (the way `fx_backtest` handles
  its panel) would likely get another 3–5×, but it touches the exact code path
  invariant #1 lives in, for a gain measured in seconds. Only take it on if
  backtest latency becomes an actual constraint — and if so, do it behind the
  `ci_regression --check` baseline, which is precisely the gate that would catch
  a mistake.

---

## 4. Network and the live path

This band matters more than compute for daily operations, and it is also the
root cause of the FX/price-fetch failures the code already defends against with
carry-forward logic.

### E5. The live path deliberately bypasses the price cache

```python
# paper_trade.py:186
return data.load_region(region, cfg.START, use_cache=False)
# paper_trade.py:162
tbl = fx.load_fx(currencies, cfg.START, base=cfg.BASE_CURRENCY, use_cache=False)
```

`use_cache=False` is right in intent — a daily run must not trade on yesterday's
bars — but it is implemented as "re-download 14 years of daily closes for the
entire universe, every time." And `latest_region_data` is called **per account,
per region**. The scheduled workflow runs 4 accounts (`full`, `small`, `ultra`,
`experimental`), then exports dashboards for all of them:

```
4 accounts × 3 regions   = 12 full universe downloads   (engine)
+ dashboard export       = 12 more                       (build_snapshot)
```

Roughly **24 full-history downloads per scheduled run** to obtain what is
ultimately *one* fresh bar per region. Two fixes, either of which is worth doing
alone:

1. **Process-level memoisation.** A tiny `@lru_cache`-style memo on
   `(region.key, start, synthetic)` inside `latest_region_data` collapses
   4 accounts × 3 regions to 3 fetches per process, with no semantic change — all
   four accounts in one run *should* see the same bars.
2. **Incremental cache.** Serve history from the parquet cache and download only
   from the last cached bar forward, then append. This is the real fix: it makes
   the daily run O(1 bar) instead of O(14 years), and it removes the
   rate-limit exposure that `fx_snapshot`'s NaN carry-forward exists to survive.
   Note the current cache key includes `end`, so a call with a different `end`
   misses entirely — incremental caching wants a key on `(region, ticker set)`
   with the date range served by slicing.

### E6. The dashboard rebuilds — and re-downloads — on every request

`server.py:132` calls `api.build_snapshot(acct, synthetic)` per HTTP hit, and
`build_snapshot` calls `latest_region_data` once per region plus an FX fetch. A
browser refresh triggers a full multi-region market-data download; the
`ThreadingHTTPServer` means several tabs do it concurrently.

The state itself only changes when the engine runs. A **60-second TTL cache**
keyed on `(account, synthetic)` around `build_snapshot`, invalidated on the state
file's mtime, is a dozen lines and removes essentially all of it. `export.py`'s
`build_payloads_site` — which loops `build_snapshot` over every discovered
account — gets the same benefit for free via E5's memo.

---

## 5. Structural observations

Not performance, but relevant to "maximal efficiency" in the engineering sense.

**S1. The two subsystems have diverged on one pattern, and the FX side is
right.** FX: precompute the full history, slice the last row, trim the live
panel. Equity: recompute per date. Porting the FX pattern (E1) does not just
speed the equity sleeve up — it makes the two subsystems structurally parallel,
which is what keeps a shared `RiskParams`/`validation` layer honest as both grow.

**S2. Two different dashboard architectures.** `dashboard/` serves a real SPA
from `static/app.js` (2,473 lines, lintable, cacheable). `forex/dashboard.py` is
1,991 lines — **14 % of the entire codebase in one module** — of which roughly
1,000 are HTML/CSS/JS embedded as Python raw strings (`_PAGE` at line 971, `_HOW`
at line 1747), edited with placeholder substitution. That template content is
invisible to `ruff`, to the type checker, and to any front-end tooling. Moving
those two blobs to `forex/static/*.html` and reading them at render time is a
mechanical change that removes ~1,000 lines from a Python module without altering
a single byte of output.

**S3. Unbounded state, rewritten whole, every run.** `state["trades"]` and
`equity_history` accumulate forever inside one JSON blob that is serialised twice
per run (SQLite blob + JSON dual-write), and `pnl.build_lots` replays the entire
trade log on every rebalance *and* on every dashboard snapshot. All are cheap
today. The point at which they stop being cheap is predictable, and BACKLOG.md's
"drop the JSON dual-write" item is the natural place to also make the trade log a
table rather than a blob member.

---

## 6. Recommended order

Ordered by payoff ÷ risk. The first two are the whole story.

1. **E1 — signal panel** (`strategy.py`, `backtest.py`). ~24× on the dominant
   cost. Public API and invariant #3 unchanged; `test_consistency` and
   `ci_regression --check` are the proof. **Do this first.**
2. **E2 — vectorized quality gate** (`data_quality.py`). 14×, purely internal,
   `assess()` signature unchanged. Together with E1: 25.2 s → 3.28 s, and CI
   from ~9 min to roughly 2–3.
3. **E5 — memoise `latest_region_data`.** A dozen lines; removes ~75 % of the
   daily run's network traffic immediately.
4. **E6 — TTL cache on `build_snapshot`.** Small, and it fixes the worst
   per-request cost in the system.
5. **E4 — positional indexing in the impact loop**, plus the duplicated dot
   product noted in E7. Do E4 before enabling `IMPACT_COEF`, not after.
6. **E3 — optional prebuilt panel/DQ arguments on `run_backtest`**, *only* if it
   falls out of E1 cleanly. Measured payoff beyond E1+E2 is a few seconds.
7. **E5b — incremental price cache.** The largest operational win and the most
   design work; worth its own spec.
8. **S2 — extract the FX dashboard templates.** Maintainability, not speed.
9. **E7 — NumPy daily loop.** Only if backtest latency becomes a real constraint.

Each of 1–6 is independently shippable and independently verifiable against the
existing regression baseline. None of them touches the no-lookahead path, the
cost model, or the one-weight-function rule.

### One documentation correction

`CLAUDE.md` advertises `pytest -q` as "170 tests (80 equity + 90 FX/ML)". The
suite currently collects and passes **572**. Worth updating while in here.
