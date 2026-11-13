# Our system, measured through the Medallion / Citadel lens

> Re-measured 2026-11-13 on the post-measurement-truth code; every conclusion holds. Before/after table at the top of `docs/MEDALLION_CITADEL_RESEARCH.md`. The figures below are the 2026-10-13 run.

Measured 2026-10-13 by the coordinating session on the REAL equity panel (Yahoo,
`auto_adjust=True` total-return prices, 2012-01-03 → 2026-10-12, the repo's own
loader `data.load_region` and signal `signals.momentum_score`). Scripts:
`scripts/medallion_lens/` (`measure_lens.py`, `null_check.py`, `null_robust.py`,
`us_split.py`, `alloc_test.py`, `corr_check.py`). Raw output:
`scripts/medallion_lens/lens_results.json`.

**Standing caveats that travel with every number below**
- Today's constituents only — **survivorship-biased** (no region has a
  point-in-time membership file; docs/FULL_SYSTEM_REVIEW_2026-09.md §3.4).
- Sections 1–4 are **gross, pre-cost, filter-free signal diagnostics** (pure
  top-10 by 12-1 momentum, no regime/trend filter, no vol targeting). They are NOT
  strategy performance (repo invariant #2). Section 5 uses the repo backtest with
  costs ON.
- One signal tested (12-1) plus one variant (residual momentum) × 4 regions, no
  parameter tuning.

## 1. Signal quality per bet (Medallion's "right 50.75% of the time")

Monthly decision = last trading day of each month; holding return = next bar's
close → the next decision's next-bar close (the repo's next-close fill
convention). IC = Spearman rank correlation of score vs that month's return
across all names with both. "Hit" = share of the top-10 that beat the
cross-sectional median. "Spread" = top-10 mean minus universe mean.

| Sleeve | Months | Names | Mean IC | IC t (Newey-West, lag 3) | Months IC>0 | Top-10 hit rate | Hit t vs 50% (NW) | Top-10 spread /month | Spread t (NW) | Bottom-10 vs universe /month |
|---|---|---|---|---|---|---|---|---|---|---|
| US | 164 | 124 | 0.021 | 1.29 | 60% | 50.9% | 0.58 | +0.96% | 2.76 | **+0.26%** |
| ASX | 165 | 54 | 0.031 | 1.64 | 57% | 51.6% | 1.26 | +0.96% | 3.32 | −0.26% |
| FTSE | 164 | 63 | 0.040 | 2.10 | 55% | 52.3% | 1.72 | +0.45% | 1.92 | −0.33% |
| TSX | 164 | 52 | 0.030 | 1.49 | 57% | 51.2% | 0.80 | +0.49% | 1.72 | −0.03% |

- Per-bet hit rates (50.9–52.3%) are in the same territory as Mercer's
  reported 50.75% for Medallion. **The per-bet edge is not what we lack.** None
  of the hit rates is individually significant at t>2 over 164 months.
- In the US the **losers beat the universe** (+0.26%/month): a short leg would
  have lost money. Consistent with the review's measured US long/short momentum
  Sharpe 0.21 vs long-only 0.83. Survivorship flatters past losers (the ones that
  kept losing left the index and are not in today's list), so this is partly an
  artefact — direction of bias: makes losers look better.

### 1a. Two nulls, and why they disagree (computed — the repo's rule)

**Within-month signal shuffle** (the null docs/PERMUTATION_TESTING.md sanctions
for portfolio Sharpe) — 500 shuffles: null mean IC ≈ 0.000, null hit ≈ 49.5%.
Real IC beats it at p = 0.002 (US). BUT the shuffle's IC standard deviation per
month is 0.09 (US; = 1/√(N−1), textbook for independent names) while the REAL
IC's is 0.23 — 2.6× wider. Shuffling destroys the factor structure of the score,
so for the IC statistic this null is far too narrow and its p-values are
overconfident. (For a portfolio SHARPE the shuffle's dispersion is the correct
√(1/T); PERMUTATION_TESTING's sd 0.267 ≈ 1/√14 confirms that. This criticism is
about the IC statistic only.)

**Time-pairing null** (new): pair month i's scores with a random month j's
returns, |i−j| > 12, same names. Keeps the factor structure of both scores and
returns; breaks only the time link. 500 draws:

| Sleeve | Real IC | Null mean IC | Null sd | p (IC) | Real spread | Null spread | p (spread) |
|---|---|---|---|---|---|---|---|
| US | 0.021 | **+0.016** | 0.014 | **0.354** | 0.96% | **+0.62%** | 0.090 |
| ASX | 0.031 | −0.002 | 0.015 | 0.012 | 0.96% | +0.40% | 0.012 |
| FTSE | 0.040 | −0.008 | 0.016 | <0.002 | 0.45% | −0.09% | 0.014 |
| TSX | 0.030 | −0.003 | 0.021 | 0.058 | 0.49% | +0.06% | 0.060 |

**Reading.** In the US, a ranking from an *unrelated* month earns most of the
same IC and two-thirds of the spread. That is persistent cross-sectional drift
(Conrad & Kaul 1998's "cross-sectional variation in mean returns"), and in a
survivor list long-run winners are partly *selected in hindsight*. So the US
sleeve's measured edge is mostly not momentum TIMING; p = 0.354.
PERMUTATION_TESTING.md §7's US p = 0.039 (signal shuffle, gross) cannot
separate the two explanations; the time-pairing null can. ASX and FTSE
momentum survives the stricter null; TSX is borderline. Caveat: the
time-pairing null is conservative — genuine momentum that persists for >12
months is counted as null.

### 1b. Robustness of the US result, and what drives it

Time-pairing null re-run with the minimum gap between paired months at 12, 24
and 36 months (300 draws each): US p = 0.297 / 0.347 / 0.297 — robust. ASX p =
0.003 / 0.013 / 0.027; FTSE 0.000 at all three; TSX 0.050 / 0.020 / 0.027.

The US list is two lists (`universes.py:27-47`): 89 single stocks described as
"S&P 500 large caps" (today's largest names: AAPL, MSFT, NVDA, AMZN, … — chosen
by TODAY's size, i.e. by what won) plus 36 ETFs "(broad, sector, factor,
thematic, fixed income, commodity)" incl. TLT, IEF, GLD, HYG. Split (400
time-pairing draws, gap > 12):

| US subset | Names | IC | IC t | Top-10 spread/mo | Spread t | Time-null IC | p | Time-null spread | p |
|---|---|---|---|---|---|---|---|---|---|
| stocks only | 89 | **0.006** | **0.35** | 0.73% | 2.31 | +0.005 | 0.48 | +0.45% | 0.14 |
| all, as traded | 125 | 0.021 | 1.29 | 0.96% | 2.76 | +0.015 | 0.31 | +0.57% | 0.09 |

ETFs are 7.5% of the pure top-10 picks on average. **Among US single stocks the
12-1 ranking has no measurable predictive power (IC 0.006, t 0.35).** The
positive top-10 spread is concentrated at the extreme top and is mostly matched
by the time-pairing null — consistent with a universe selected with hindsight
(today's mega-caps are, by construction, 2012–2026's biggest persistent
winners). Most of the US IC comes from ranking ETFs across asset classes (stocks
vs bonds vs commodities), whose mean returns differed persistently over the
sample. Two explanations cannot be separated without point-in-time membership:
(a) survivorship/hindsight universe selection; (b) a genuine era of persistent
mega-cap winners that momentum would have bought live. Either way it is not
momentum TIMING. Rank correlation of the score with each name's full-sample mean
return ("answer key"): US 0.29, ASX 0.22, FTSE 0.18, TSX 0.16.

## 2. Breadth — Grinold's IR ≈ IC × √breadth

Effective independent bets per year = 12 / var(monthly IC) (realised ICIR ÷ mean
IC, squared). Under the shuffle null this reproduces the textbook N×12 (US:
1,477 vs 1,483), so the method is sound; the real shortfall is correlation.

| Sleeve | Names ranked | Naive bets/yr (N×12) | Effective bets/yr | Effective independent names / month | IC-IR (annual) | Top-10 spread IR (annual, gross) |
|---|---|---|---|---|---|---|
| US | 124 | 1,483 | 220 | 19.4 | 0.32 | 0.73 |
| ASX | 54 | 653 | 224 | 19.7 | 0.46 | 0.83 |
| FTSE | 63 | 755 | 212 | 18.6 | 0.59 | 0.48 |
| TSX | 52 | 624 | 154 | 13.8 | 0.37 | 0.45 |

- **Every region collapses to 14–20 independent bets a month, whether it ranks
  52 names or 124.** A bigger list from the same market buys almost no breadth;
  the cross-section has ~20 independent dimensions (sectors, factors).
- Cross-sleeve: the four sleeves are ~0.41 correlated (§6), so the effective
  number of independent sleeves is 4/(1+3×0.41) ≈ 1.8. Total effective breadth
  is therefore roughly 1.8 × ~200 ≈ **350–400 independent bets per year** for the
  whole equity book (approximation).

## 3. Signal decay — how long one bet lasts

Mean IC of the k-th month ahead (k = 1..12), NW t in brackets:

| k | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| US | .021(1.3) | .022(1.3) | .017(1.0) | .013(0.8) | .012(0.7) | .023(1.2) | .017(0.9) | .018(1.0) | .009(0.5) | .007(0.4) | .000(0.0) | .006(0.3) |
| ASX | .031(1.6) | .025(1.4) | .023(1.2) | .016(0.8) | .016(0.8) | .020(1.1) | .019(1.0) | .021(1.2) | .023(1.2) | .025(1.4) | .004(0.2) | .011(0.6) |
| FTSE | .040(2.1) | .041(2.3) | .029(1.6) | .029(1.6) | .035(2.1) | .030(1.8) | .030(1.7) | .022(1.2) | .013(0.8) | .014(0.8) | .015(0.9) | .016(0.9) |
| TSX | .030(1.5) | .028(1.4) | .033(1.6) | .032(1.4) | .024(1.0) | .033(1.4) | .029(1.2) | .021(0.9) | .010(0.4) | .006(0.3) | .003(0.2) | .004(0.2) |

The signal is SLOW: roughly flat for 6–8 months, fading to ~0 by month 10–12 in
every region. A monthly rebalance trades a signal whose information lasts about
two-thirds of a year — consistent with the review's corrected finding (§15) that
quarterly rebalancing, averaged over phase, beats monthly. Relevant to tax: the
signal has mostly expired by the 12 months an Australian CGT discount needs.

## 4. Residual momentum — Citadel-style "trade what's left after hedging"

Blitz-Huij-Martens construction, simplified to ONE factor (the equal-weight
universe as "market"; the papers use Fama-French 3): regress each name's last 36
monthly returns on the market, sum the residuals of months t−11..t−1, divide by
their standard deviation, rank. Common sample 140 months (2015→2026). Gross,
filter-free, top-10 equal-weight.

| Sleeve | Signal | IC | IC t | Spread/mo | Spread t | Name turnover/mo | Ann. return | Ann. vol | Gross excess SR | Beta to universe | Max DD |
|---|---|---|---|---|---|---|---|---|---|---|---|
| US | raw | .013 | 0.72 | 0.88% | 2.25 | 27% | 26.9% | 23.6% | 0.99 | 1.15 | −21.8% |
| US | residual | .002 | 0.15 | 0.41% | 1.68 | 44% | 21.3% | 17.3% | 1.02 | 0.96 | −22.3% |
| ASX | raw | .020 | 1.00 | 0.93% | 2.90 | 23% | 28.0% | 19.9% | 1.23 | 1.00 | −21.1% |
| ASX | residual | .037 | 2.15 | 0.56% | 2.08 | 30% | 23.1% | 17.3% | 1.13 | 0.94 | −17.1% |
| FTSE | raw | .023 | 1.13 | 0.30% | 1.13 | 26% | 11.5% | 16.2% | 0.49 | 0.82 | −33.9% |
| FTSE | residual | .023 | 1.26 | 0.44% | 1.92 | 33% | 13.1% | 14.3% | 0.67 | 0.79 | −23.5% |
| TSX | raw | .020 | 0.91 | 0.45% | 1.37 | 24% | 21.1% | 17.9% | 0.98 | 0.91 | −17.9% |
| TSX | residual | .025 | 1.24 | 0.38% | 1.43 | 32% | 20.1% | 16.4% | 1.01 | 0.88 | −13.7% |

Mixed. Residual momentum consistently LOWERS volatility (all four), beta (all
four) and max drawdown (3 of 4), at HIGHER turnover (+7 to +17 pp of names per
month — expensive for FTSE's stamp duty). It does NOT consistently raise IC or
spread: better in ASX, worse in the US, similar elsewhere. Gross Sharpe-like
ratio: up in FTSE (0.49→0.67) and slightly in US/TSX, down in ASX. Nowhere near
the "roughly doubles" of the long-short, Fama-French-3, broad-universe
literature. Rank correlation with raw momentum 0.73–0.76. Verdict: a risk-shape
improvement, not a free return upgrade on our panel; would need a costs-on
backtest before any adoption.

## 5. Beta / alpha of each REAL sleeve (repo backtest, costs ON)

`backtest.run_backtest(prices, index, region)` with defaults (incl. idle-cash
credit at 3.5%). Monthly excess returns (minus 3.5%/12) regressed on the market.
Two markets: the regional price index (no dividends — flatters alpha) and the
equal-weight basket of the sleeve's own (survivor, total-return) universe — the
same survivors on both sides, so survivorship largely cancels. Alpha t is
Newey-West (lag 3). From 2013-04-30.

| Sleeve | Excess SR | CAGR | β (index) | α/yr vs index | t | β (own universe) | α/yr vs own universe | t | R² | Appraisal ratio (α / residual vol) |
|---|---|---|---|---|---|---|---|---|---|---|
| US | 0.73 | 9.6% | 0.27 | +3.5% | 2.00 | 0.30 | +2.1% | 1.20 | 0.23 | 0.28 |
| ASX | 0.44 | 6.4% | 0.25 | +2.5% | 1.91 | 0.25 | −0.6% | −0.48 | 0.27 | −0.11 |
| FTSE | 0.17 | 4.5% | 0.23 | +0.9% | 0.59 | 0.23 | −0.1% | −0.06 | 0.21 | −0.02 |
| TSX | 0.89 | 9.4% | 0.18 | +4.9% | 2.69 | 0.20 | +3.1% | 1.72 | 0.12 | 0.52 |

The own-universe basket itself earned excess Sharpe 1.00 (US), 1.03 (ASX), 0.40
(FTSE), 1.16 (TSX) — vs price-index Sharpes 0.66 / **0.12** / 0.08 / 0.40. ASX's
price index 0.12 vs today's-ASX-survivors 1.03 is the survivorship bias made
visible.

**Citadel reading.** Citadel's CRO: "equity portfolios have to be beta
neutral... We're looking for idiosyncratic winners and losers" (Risk.net 2023,
verified). Hedge each sleeve against its own universe and what is left is
−0.6% to +3.1% a year, **none significant (best t = 1.72, TSX)**. The sleeves'
low beta (0.18–0.30) is mostly the regime filter and vol targeting holding
~60% cash, not market-neutral alpha.

## 6. Cross-sleeve correlation — the daily matrix is a time-zone artefact

Same sample (2013-05 → 2026-09), local-currency sleeve returns:

| Pair | Daily | Daily, lead-lag corrected (Dimson, lags −1..+1) | Weekly | Monthly |
|---|---|---|---|---|
| US–ASX | **0.06** | 0.42 | 0.38 | 0.38 |
| US–FTSE | 0.31 | 0.41 | 0.40 | 0.30 |
| US–TSX | 0.50 | 0.50 | 0.50 | 0.23 |
| ASX–FTSE | 0.20 | 0.41 | 0.35 | 0.28 |
| ASX–TSX | 0.09 | 0.42 | 0.39 | 0.29 |
| FTSE–TSX | 0.38 | 0.50 | 0.44 | 0.39 |

ASX closes before the US opens, so a US shock reaches ASX the NEXT calendar day
and same-day daily correlation misses it. Corrected (Dimson or weekly), ASX is
~0.4 correlated with everything. Mean pairwise ρ ≈ 0.41 (weekly), not the 0.26
in docs/SHARPE_RESEARCH.md §6. Consequences: (a) §6's statement that the
portfolio diversifies "because ASX–US correlation is 0.058" is an artefact; (b)
its frictionless benchmark SR 1.010 becomes 0.674 × √(4/(1+3×0.41)) ≈ 0.90, so
the "0.204 Sharpe leak" is ≈ 0.10 — about half was the correlation estimate.
(US–TSX ≈ 0.50 holds on three of four estimators; the monthly 0.23 is the
outlier, SE ≈ 0.08.)

## 7. Risk budgets vs equal dollars (Citadel's "volatility allocation")

Inverse-vol sleeve weights from trailing 36-month sleeve vol (causal) vs equal
capital, local-currency monthly, 139 months (2015-04 → 2026-10):
equal-capital SR 0.645 vs inverse-vol SR 0.615; **ΔSR −0.030, 95% block-bootstrap
CI [−0.110, +0.046]**, P(Δ>0) = 0.22. Average inverse-vol weights US 0.195, ASX
0.25, FTSE 0.277, TSX 0.279. Null: our sleeves already carry similar risk, and
risk-budgeting would cut the best-Sharpe sleeve (US). Do not copy.

## 8. Cost per bet vs edge per bet (pure top-10, lower bound)

Round-trip cost per replaced name = 2×(commission+slippage) + stamp duty
(`regions.py`): US 14 bps, ASX 36, FTSE 76, TSX 22. Name turnover 23–27%/month.

| Sleeve | Cost/month (name turnover × round trip) | Gross top-10 spread/month | Cost as % of edge |
|---|---|---|---|
| US | 3.7 bps | 96 bps | 4% |
| ASX | 8.1 bps | 96 bps | 8% |
| FTSE | 19.8 bps | 45 bps | **44%** |
| TSX | 5.4 bps | 49 bps | 11% |

Lower bound: ignores inverse-vol re-weighting turnover. Agrees with
SHARPE_RESEARCH §8 (costs take 41% of FTSE's gross Sharpe). Outside FTSE, cost
per bet is not the binding constraint; breadth and statistical reliability are.

## 9. Code facts the comparison rests on (read directly)

- One signal: `signals.py:15-18` `momentum_score` = `prices.shift(p.skip_days) /
  prices.shift(p.lookback_days) - 1.0`. Value proxy exists but `use_value: bool =
  False` (`config.py`).
- Top-N selection re-picks every month with no hold band: `signals.py:98`
  `picks = metric.nlargest(min(p.top_n, len(metric))).index`.
- Vol targeting assumes `avg_correlation: float = 0.6` (`config.py`), measured
  0.18–0.36 within sleeves by the review.
- Live execution sends plain market orders whenever it runs:
  `execution_ibkr.py:173` `trade = ib.placeOrder(contract, MarketOrder(action,
  qty))` — while paper and backtest assume a fill at the NEXT CLOSE
  (`paper_trade.py:12-13` "it fills at the close of the next session the
  sleeve's data shows, never at the close that produced it"). A
  market-on-close order is the live instrument that matches the simulated fill.
- FX week edge is a fixed hour: `forex/sessions.py:55` `_FX_EDGE_HOUR = 22` — the
  FX week turns at 17:00 New York, i.e. 21:00 UTC during US daylight time, so the
  edge is an hour late ~8 months a year (only matters for intraday books).
- Fixed equal sleeve allocations: `config.py` `ALLOCATIONS = {"ASX": 0.25, "US":
  0.25, "FTSE": 0.25, "TSX": 0.25}`; one global `MAX_DRAWDOWN_STOP = 0.25`.

## 10. What the repo ALREADY records (so the report marks new vs existing)

Read directly by the coordinator. Do not re-propose these as new; cite them.

- `docs/EFFECTIVE_PUBLIC_STRATEGIES.md` §12 — ranked unbuilt candidates:
  (1) volatility-responsive momentum scaler (Daniel-Moskowitz), (2) fundamental
  quality/profitability tilt (needs a fundamentals feed), (3) a managed-futures /
  time-series-momentum diversifier sleeve, (4) bond & commodity carry. "What NOT
  to chase": HFT/latency/market-making (`docs/HFT_REALITY.md`), short-horizon
  reversal/pairs as a funded book, naked short vol, factor timing.
- `docs/FULL_SYSTEM_REVIEW_2026-09.md` §5.3 upgrades: fix `avg_correlation`
  0.6 → measured 0.18–0.36; graded de-risking instead of binary regime switch;
  buy/hold band; tranche the rebalance; point-in-time membership; nearest-share
  or fractional shares. §5.2 lists residual momentum as unbuilt ("about 2x
  risk-adjusted profit"). §15 corrections: the regime filter is NOT a drag (wins
  on Calmar everywhere); quarterly BEATS monthly when averaged over phase.
- `docs/HANDOFF_2026-09-24.md` / measurement-truth block — Matt's LOCKED
  decisions: goal is measurement truth; point-in-time membership for all four
  regions (cost accepted); fill convention = next day's close; archive and
  restart all paper books clean.
- `docs/SHARPE_RESEARCH.md` §6 (diversification — uses the DAILY correlation
  matrix this note shows is a time-zone artefact), §7 (leverage: equity stack
  charges no margin debit; `ultra` 3x compounds to ~0.09 Sharpe), §8 (costs take
  41% of FTSE gross Sharpe → low-turnover FTSE variant), §10 (names Grinold's law
  but does not measure breadth).
- `docs/MONTE_CARLO_RESEARCH.md` §7 — one global 25% drawdown stop means four
  different things (P(trip on luck) TSX 0.9%, ASX 1.6%, US 5.4%, FTSE 19.1%);
  supports a per-sleeve breaker; explicitly NOT reweighting FTSE.
- `docs/PERMUTATION_TESTING.md` §5–§7 — signal shuffle is the sanctioned null
  for cross-sectional work; US p = 0.039 gross. This note's time-pairing null
  adds a second question that shuffle cannot answer (timing vs persistent drift).
- Already tested and REJECTED (memory + docs): training the neural model longer;
  forward shadow confirmation as a promotion gate; FX holding-policy knobs;
  inverting agent signals; FX carry signal on `swap_long_pips` (it is a
  hardcoded constant). The swarm's permutation test p = 0.4726 — nothing to
  promote. FX books: no edge under the review's corrections.
- Out of bounds without asking Matt: any self-tuning loop touching strategy
  knobs; the ML loop is scoped to FX models + roster only.
