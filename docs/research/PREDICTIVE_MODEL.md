# Predictive model — is there alpha, and what would we wire in?

Two questions: (1) do algorithmic strategies actually achieve alpha in the real world, and
(2) how would we build a genuine *predictive* model here, and what's missing. The honest
short answer ties them together: **real alpha exists but lives where we have no access
(speed, unique data, scale); a predictive model on our current price-only data would mostly
re-learn the weak factors we already ruled out — so the model is a *data* problem first.**

## 1. Who actually achieves alpha, and how

Persistent net-of-cost alpha is rare and concentrated in three places, none of which is
"a cleverer signal on daily price bars":

| Where alpha really lives | Examples | What it needs (that we lack) |
|---|---|---|
| **Speed / microstructure** — market-making, latency arb, rebates | Citadel Securities, Jane Street, Virtu, Jump, HRT | Co-location, microsecond infra, exchange rebates, order-flow |
| **Unique data + scale stat-arb** — thousands of names, short horizons, alt-data | RenTec Medallion (~39%/yr net, *closed*, capacity-capped ~$10B), Two Sigma, D.E. Shaw, TGS, PDT | Alt-data (satellite, card, web, supply-chain), PhD teams, huge compute, prime-broker leverage, breadth |
| **Capacity-constrained niches** — small/illiquid, event-driven, structural | small quant shops, some CTAs | Ability to trade where big money can't; illiquidity tolerance |

What the evidence says about everyone else (i.e. daily-bar factor strategies like ours):
- **Anomalies decay ~58% after publication** (McLean & Pontiff 2016) — once a signal is known
  and arbitraged, most of its edge is gone.
- **Factor premia are real but small, cyclical, and mostly risk compensation** (Fama-French,
  AQR) — value/momentum/carry/quality/low-vol pay a premium *for bearing risk*, they are not
  free alpha. That is exactly the ~0.28-Sharpe diversified book we have.
- **Our own result matches the literature:** 93 strategies tested, survivors backtested,
  nothing beat ~0.28 Sharpe. That is not a failure of effort — it is the expected outcome of
  price-only factor mining on liquid large-caps.

**Takeaway:** the funds that beat the market do it with speed, data, or scale — not with a
better formula on the data we have. Alpha is an *access* problem, not a cleverness problem.

## 2. What a genuine predictive model looks like here

A predictive model replaces the hand-coded ranking (momentum/value/…) with a *learned* score:
"given features X for each stock at date t, predict its forward return; rank on the prediction."
The pipeline:

```
labels (y)   ─┐
features (X) ─┼─▶  model  ─▶  purged/embargoed walk-forward  ─▶  prediction score
              │     (GBM / NN)      train→predict, no leakage        │
data ─────────┘                                                      ▼
                                            rank_score → compute_targets → ERC → vol-target → book
                                            (the SAME single-source-of-truth we already have)
```

### What we already have (a real head start)
- **A working ML trading pipeline** — the FX subsystem (`trading_algo/forex/`) is already a
  purged-walk-forward, Sharpe-loss neural net with a Hedge ensemble, meta-labeling, and
  **Deflated-Sharpe / PBO** validation (`docs/FX_DEEP_RESEARCH.md`). The scaffolding exists.
- **A prediction→portfolio adapter already wired:** `strategy.compute_targets` accepts a
  `rank_score` — an ML score drops in exactly where momentum/value do, and inherits ERC +
  vol-targeting + costs + the frontier for free.
- **The anti-overfitting gauntlet** (`robust.py`: Deflated/Probabilistic Sharpe, PBO/CSCV) —
  essential, because ML overfits *far* harder than a fixed factor.
- **Survivorship-free labels/features** — the point-in-time + delisted (Tiingo) pipeline means
  we can train without the survivorship leak that would otherwise inflate everything.

### What we'd need to WIRE IN (in priority order)

1. **DATA — the real unlock (everything else is plumbing).** On price-only inputs an ML model
   provably re-derives momentum/reversal/low-vol/beta — the factors we already showed don't
   beat the book. Genuine predictive lift needs inputs the factors can't see:
   - **fundamentals** (earnings, margins, accruals, growth) — e.g. Sharadar/Tiingo fundamentals, SEC EDGAR;
   - **analyst estimates & revisions** (post-earnings drift, revision momentum);
   - **short interest, institutional flows/13F, insider trades;**
   - **options-implied** (IV skew, put/call, term structure — forward-looking);
   - **news/sentiment / alt-data** (the hardest and most differentiated).
   *This is the gating item. Without new data, do not expect new alpha.*
2. **Feature panel builder** — `features.py`: a causal (data ≤ t), cross-sectionally
   standardised panel `X[date, ticker, feature]`. Reuse what exists (momentum, `realised_vol`,
   `rolling_beta`, residual momentum, value) and add the new-data features. No-lookahead is the
   whole ballgame.
3. **Labeling** — `labels.py`: forward N-day return, or **triple-barrier** labels
   (López de Prado) with proper sample weights for overlapping windows. Align to `X` with an
   embargo so a label never peeks past t.
4. **Model + purged/embargoed walk-forward** — port the FX subsystem's purged-CV train/predict
   loop to the equity panel; add a **gradient-boosted-tree** option (LightGBM — the industry
   standard for tabular cross-sectional return prediction; add as an optional dependency).
   Output = per-stock score per rebalance.
5. **Prediction→signal adapter** — feed the model score as `rank_score` into
   `compute_targets`; the rest of the book (ERC, vol-target, costs, frontier) is unchanged.
6. **Overfitting controls dialled up** — every trained model must clear Deflated Sharpe + PBO
   *deflated for the number of models/features/hyperparameters tried*, on purged walk-forward
   out-of-sample. With flexible ML this is not optional; it is the difference between research
   and self-deception.
7. **Later: meta-labeling + sizing** — a second model that decides *whether* to act on the
   primary prediction (raises precision), then Kelly/vol-scaled sizing.

### Honest expectation
- **On price-only data:** an ML model will land near the existing book (~0.28 Sharpe) — it
  can only recombine the same weak factors. Building it would be *rigorous* but not
  *profitable* beyond what we have.
- **With genuinely new data (fundamentals/options/sentiment) + ruthless deflation:** this is
  the one credible path to real alpha we haven't exhausted — but it costs data (often paid),
  carries high overfitting risk, and even done well may yield a *modest* edge, not Medallion.
- **The safe compounding win remains allocation** (70/30 equity/active → ~10.5% CAGR at ~⅔ the
  drawdown). A predictive model is a *research bet on new data*, not a replacement for that.

## Result — the pipeline built + fundamentals wired + honestly tested

Built in-repo: `features.py` (causal panel), `labels.py`, `mlpipeline.py` (purged/embargoed
walk-forward + cross-sectional ridge + label-shuffle **null probe**), `datasources.py`
(leakage-safe as-of merge; **real SEC-EDGAR fundamentals**; IV/sentiment adapters + synthetic),
`mlreport.py` + CI `ml` task.

De-biased run (1,058 PIT names, price features **+ real EDGAR `roe/net_margin/asset_growth`**):
- **Leakage probe clean:** real OOS IC **0.016** vs label-shuffled null **−0.003** → the pipeline
  does not peek (the machinery is trustworthy).
- **No real predictive edge:** IC ≈ 0.016 is noise-level (good equity ML ≈ 0.03–0.05). Adding
  fundamentals did NOT change it — their loadings (~0.002–0.01) sit at the same noise level as
  the price factors.
- The headline long-only Sharpe (~1.0) is a **construction/beta mirage**: 34% vol, always-invested,
  concentrated top-20 on the delisted-inclusive small-cap set over a bull-heavy sample — not the
  12%-vol book, and not alpha. The IC + null probe are what tell the truth; the Sharpe misleads.

**Takeaway:** the honest, leakage-controlled, fundamentals-fed pipeline exists and works — and it
confirms the thesis once more: *price + basic fundamentals carry essentially no cross-sectional
predictive edge on liquid US equities.* The remaining untested bet is genuinely differentiated data
(options-IV skew, clean news/social sentiment) via paid feeds — wired as adapters, ready to test —
and even that must clear this same IC / null-probe / deflation bar before it's believed.

### Minimal first experiment (cheap, honest)
Before any paid data: build `features.py` + `labels.py` from what we already have, port the
FX purged-walk-forward loop, train a LightGBM cross-sectional model on the PIT US universe,
and run its score through `compute_targets` + the validation gauntlet. Expected result: ~the
current book. Value: it stands up the *entire pipeline* so that the day we add fundamentals or
options data, the only new work is features — and we'll have proven the validation is honest
on a known-null case first.

---

## Marginal-edge verdict (surprises + shocks, real de-biased data)

A 5-role review flow (data-scientist → architect → staff → engineer → chief-engineer)
diagnosed why the alt-data read IC≈0 — **stale levels not surprises, a horizon mismatch,
coverage dilution in a pooled ridge** — and set the guards for a *legitimate* pass. We
then built the honest instruments and ran them on the **point-in-time, delisting-adjusted
US universe (real EDGAR + GDELT), 139 OOS months**:

- **New signals only** (sign-flips of existing levels are cosmetic to a linear ridge and were
  not counted): `sue` (seasonal earnings surprise / PEAD, duration-filtered, equity-filing
  guarded, decayed over the drift window), `sentiment_shock` / `buzz_shock` (tone/attention
  changes vs a trailing baseline).
- **The honest test:** price-**residualised** incremental IC per source; a nested price-only
  vs price+alt walk-forward whose **difference** (not the alt book) is bootstrap-CI'd and
  Deflated-Sharpe-deflated; a shuffle-null on the increment; and a synthetic negative control
  that must straddle 0.

**Result — it does NOT pass:**

| measure | value | bar | verdict |
|---|---|---|---|
| SUE incremental IC (21d, PIT) | **−0.014** | > 0 (pre-registered +) | ✗ wrong sign |
| fundamentals block IC | −0.016 | > 0 | ✗ |
| sentiment shocks | **0 measurable dates** | — | ✗ untestable (40-name GDELT cap ∩ PIT ≈ ∅) |
| nested Δ info-ratio | +0.24, **90% CI [−0.28, +0.77]** | CI low > 0 | ✗ straddles 0 |
| DSR of the difference | **74.7%** | ≥ 95% | ✗ |
| incremental shuffle-null | −0.004 | ≈ 0 | ✓ no leak |

**Honest read.** On de-biased data, free filing-dated fundamentals + capped GDELT sentiment
add **no measurable cross-sectional edge** in a monthly linear pipeline. This is a genuine
null, not a broken test — the negative control passed, the shuffle-null collapsed, and the
harness deflated the *increment*. A negative SUE with the pre-registered sign is a fail, **not**
a licence to flip the sign (the chief-engineer guard against sign-snooping).

**What a real pass would need (not p-hacking, actual data/model gaps):**
1. **Earnings ANNOUNCEMENT dates**, not 10-Q filing dates — PEAD drift starts at the
   announcement; `filed` lags it by days-to-weeks, so we enter after the initial drift.
   companyfacts has no announcement date → needs a paid/earnings-calendar feed.
2. **Real, survivorship-clean sentiment** (paid vendor or GDELT bulk GKG), not a 40-name cap
   that misses the point-in-time universe.
3. A **nonlinear learner** (GBM) — the pooled linear ridge bounds what can be extracted; a
   ~0 linear result does not disprove a nonlinear PEAD effect, it bounds *this* model.

Until one of those clears the same CI-lower-bound-> 0 **and** DSR ≥ 95% bar, **no alt-data
source is weighted into any live book** — the ridge already shrinks these columns to zero, and
we do not override it with conviction we have not earned. The durable deliverable is the
*instrument*: a leakage-controlled, increment-deflated, negative-controlled marginal-edge test
that will say "yes" honestly the day the data actually carries signal.

### Keeping it learning — the forward monitor

A null today is not a null forever: each quarter adds fresh filings and the walk-forward gains
OOS months, so the estimate tightens and a real edge — if one emerges — becomes visible. So the
honest test runs **forward on a schedule** (`.github/workflows/altdata-monitor.yml`, monthly) and
appends its numbers (`mlreport --emit-metrics`) to `docs/research/altdata_monitor.jsonl` — a
growing longitudinal record of per-source incremental IC, the increment's CI, and the DSR of the
difference. It **never trades** (a zero-edge signal in the live book would only churn fees); it
watches. The day a source's CI lower bound clears 0 and its DSR clears 95% *on that live-updating
record*, it earns weight — and not one day before.

### The three levers (implemented) — a harder gate, not a softer one

After the null, a data-scientist→architect→staff→engineer→chief-engineer review designed and
honesty-gated three legitimate levers to a *real* edge. All three are now implemented; crucially,
each was built to RAISE the ceiling while the pass bar got **stricter**, not looser:

1. **Earnings ANNOUNCEMENT dates (8-K Item 2.02), not filing dates.** PEAD drift starts at the
   press release; the 10-Q lags it by days-to-weeks. `EdgarFundamentals` now sources 8-K 2.02
   dates (submissions API, paginated to ~2007) and moves the SUE impulse to the announcement —
   using **first-reported** values so no restated number is ever back-dated (the chief's
   magnitude-lookahead guard, with a truncation-invariance test).
2. **Clean sentiment — the measurable part.** The 0-covered-dates PIT failure was a coverage bug
   (`tickers[:max_names]` spent the whole budget on leading un-mappable delisted names); fixed by
   slicing *after* coverability, so sentiment is now measurable. It stays **survivor-conditioned
   corroboration only** — never a pass. A survivorship-*complete* feed still needs GDELT GKG-bulk
   (offline, TB-scale, + a point-in-time name→ticker crosswalk) or a paid vendor. That remains
   deferred and is not dressed up as free.
3. **A pure-NumPy nonlinear learner (GBRT).** A histogram gradient-boosted-trees model (pinned
   depth 3 / 200 rounds / lr 0.05, deterministic) slots into the same purged walk-forward, testing
   an interaction/threshold PEAD effect the linear ridge provably cannot express.

**The gate got stricter.** A source now earns weight only if the SINGLE pre-declared PRIMARY cell —
GBRT + announcement-dated SUE + full-universe 21d increment — clears *all* of: bootstrap-CI lower
bound > 0, **Deflated Sharpe of the difference ≥ 95% deflated across the union (model × α) trial
set**, **PBO/CSCV ≤ 50%** (now wired into `mlreport`, validating the learner/α selection a nonlinear
model makes riskier), the incremental shuffle-null collapse, and the synthetic negative control
(increment straddles 0 under BOTH learners). No sign-flipping; no picking the better of
filed/announcement timing or ridge/GBRT after seeing results. Ridge and covered-sentiment are
corroboration and can never themselves pass. A GBRT null bounds only *this* pinned capacity — we
refuse to sweep deeper to buy a pass. Verdict from the real-data CI run is recorded in
`docs/research/altdata_monitor.jsonl` and read only from there.
