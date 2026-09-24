# Full system review, September 2026

**Scope:** the whole application at commit `bf678c2` on `feat/dormant-feature-remediation` (readers examined `9d21578`; the six commits since are noted where they matter): code, architecture, machine learning, and investment performance, benchmarked against published best practice, with adversarial verification of the findings.

**How to use it:** section 1 is the two-page answer. Section 0 explains every term. Sections 2 to 9 are the understanding and the benchmarks. Section 10 is the finding list with verification status. Section 12 is what to do, in order. Nothing in the repo was changed by this review; the deletion and change lists are proposals.

**Written:** 2026-09-20, from a review run on 2026-09-19 AEST.

---

## Contents

0. How to read this document, and the words it uses
1. Executive summary
2. How the system works, end to end
3. Investment performance, measured honestly
4. Where the strategies are not being allowed to play out
5. The algorithm against the best
6. Architecture and operations against best practice
7. The machine-learning layer against best practice
8. Code quality, simplicity and dead code
9. Two honest framings: promoter and adversary
10. Findings (three tiers, by how they were verified)
11. What the four prior audits said, and where each item stands
12. Recommendations, in order
13. How this review was produced, and its limits
14. Sources
15. Verification round two, and the corrections it forced

---

## 0. How to read this document, and the words it uses

Every technical term is explained the first time it appears, and the ones that recur are collected here. If a sentence uses a word you do not know, look here first.

| Term | Plain English |
|---|---|
| **12-1 momentum** | A stock's return over the last 12 months, ignoring the most recent month. Buy the strongest; the skipped month avoids the short-term bounce-back that follows a sharp move. |
| **Sleeve** | One region's sub-book, kept in its own currency (ASX in AUD, US in USD, FTSE in GBP, TSX in CAD). |
| **Regime filter** | If a region's stock index is below its own 200-day average price, the whole sleeve holds cash instead of stocks. An on/off switch for the whole book. |
| **Trend filter** | A stock is only eligible if it is above its own 200-day average. |
| **Volatility ("vol")** | How much a return series wobbles, measured as a standard deviation and quoted per year. 12% vol means a typical year's move is about plus or minus 12%. |
| **Vol targeting** | Scale the book up or down so its expected wobble matches a chosen number (12% per year here). |
| **Gross exposure** | The size of all positions added together as a fraction of the account. 0.4 means 40% invested and 60% in cash. Shorts count as positive size. |
| **Net exposure** | Longs minus shorts. A "market-neutral" book aims for zero net. |
| **Leverage** | Borrowing to hold more than 100% gross exposure. |
| **CAGR** | Compound annual growth rate. The smoothed per-year growth of the account. |
| **Sharpe ratio** | Return above cash divided by vol. Reward per unit of risk. Around 0.5 is respectable for a long-only stock strategy, 1.0 is excellent. This repo computes it two ways: subtracting a 3.5% cash rate (the dashboard convention) and not subtracting it (the statistical-test convention). Those differ by about 0.45, so every Sharpe in this document says which it is. |
| **Max drawdown** | The worst peak-to-trough fall in the account's value. |
| **Standard error (SE)** | The plus-or-minus on a measured number caused by having only a limited sample. A Sharpe measured over 3 months has an SE of about 2, so a reading of minus 3 or plus 3 is consistent with zero. |
| **Confidence interval (CI)** | The range a measurement is likely to lie in. If the range includes zero, the number cannot be told apart from nothing. |
| **Backtest** | Replaying the strategy over past prices to see what it would have done. |
| **Lookahead** | Using tomorrow's price to decide today's trade. Makes backtests look better than reality. This repo tests for it. |
| **Survivorship bias** | Testing on today's list of stocks, which by construction excludes the ones that failed. Inflates every backtest number. |
| **Price index vs total return** | A price index tracks share prices only. A total-return series also counts dividends. Comparing a total-return strategy against a price index flatters the strategy. |
| **Turnover** | How much of the book is bought and sold. One-sided monthly turnover of 30% means 30% of the book changes hands each month. |
| **Slippage** | The small amount you lose because your order moves the price. |
| **Stamp duty** | A 0.5% UK tax on share purchases. Paid on buys only. |
| **Whole shares** | You cannot buy 0.4 of a share. On a small account this rounding removes a large slice of the intended position. |
| **Drawdown breaker** | A rule that liquidates to cash after the account falls a set amount from its best-ever value, then waits a cooldown before re-entering. |
| **High-water mark** | The best-ever account value the breaker measures from. |
| **Deflated Sharpe Ratio (DSR)** | A Sharpe corrected for how many strategy variants you tried. If you try 400 things, the best one looks good by luck; DSR asks whether it beat what luck alone would produce. |
| **PBO** | Probability of Backtest Overfitting. How often the variant that won in one half of history loses in the other half. |
| **Purged walk-forward** | Training a model on the past and testing on a later window, with a gap so the test window cannot leak into training. |
| **Permutation test** | Shuffle history into hundreds of fake markets, re-run the search, and see how often luck does as well as the real result. p = 0.47 means about half the time. |
| **Ensemble** | Several signals blended into one. The FX book blends five rule-based "agents" with weights that shift toward whichever has recently done well. |
| **Hedge algorithm** | The specific blending rule used: each agent's weight shrinks exponentially with its recent losses. |
| **Genome / swarm** | A rule variant (which indicator, which window, which threshold) and the population of such variants bred by a genetic search. |
| **Paper book** | A simulated account that records trades at real prices but with no real money. |
| **Reconciliation** | Replaying every recorded trade from the starting cash and checking it lands on the stored holdings, the way an accountant checks a broker statement. |
| **CI** | Continuous integration. The robot that re-runs the tests on every code push. |
| **Ablation** | Switching one component off to measure what it contributed. |
| **Basis point (bp)** | One hundredth of one percent. 50 bp = 0.5%. |
| **AUM** | Assets under management. Here, the paper capital across the books. |
| **Cash drag** | Return lost because capital sat in cash instead of the strategy. |


---

## 1. Executive summary

**Verdict.** The engineering is unusually disciplined for an independent quant repository and most of the safety machinery works. The investment result does not yet justify the machinery. After fourteen years of history and every cost the repo can model, the flagship equity strategy returns less than an index fund on a like-for-like basis and does not beat it on a risk-adjusted basis. Its one genuine product feature is a much shallower worst loss. The FX, crypto and multi-asset books, which are 55% of the code, show no edge by the repo's own three instruments and are still traded daily. The two changes most likely to improve the equity result are one number and one function, both ranked first in the repo's own July strategy survey and both still unbuilt.

**The numbers, in one table.** CAGR is compound annual growth; Sharpe is return above cash per unit of risk; both are survivorship-biased upper bounds because no region has an index-membership file.

| 2012 to 2026-09-18, AUD | CAGR | Sharpe (vs 3.5% cash) | Worst drawdown |
|---|---|---|---|
| Strategy as the dashboard shows it (no cash interest, pre-`57676c4`) | 6.1% | 0.32 | −11% |
| Strategy at HEAD (interest on idle cash now credited) | 8.6% | 0.62 | −10% |
| The repo's benchmark (price indices, no dividends) | 9.6% | 0.50 | −30% |
| A fair benchmark (index funds with dividends reinvested) | 11.3% | 0.65 | −30% |

**Why the strategy trails.** It is a 40%-invested book. The regime filter holds each sleeve in cash 23 to 35% of all days, and when invested the sizing formula assumes the held stocks are 0.6 correlated when they measure 0.18 to 0.36, so it deploys only half the capital. Both are measured in this review with real data (section 3.3). Two of the three stock-level filters do nothing. Costs are honest and small except on FTSE, where stamp duty removes 41% of the gross Sharpe.

**Where the strategies are not being allowed to play out** (section 4): the ASX sleeve has been 0% invested since June, three months of it by design and two rebalances lost to since-fixed bugs; the US sleeve loses 16 to 47% of its target every month to whole-share rounding; the market-neutral book cannot afford its long leg at A$10k and has held nothing since August; the A$1k book cannot trade at all; the "hourly" book runs five times a day; and none of the September fixes are deployed, because they live on an unmerged branch.

**Things that would be wrong on a live account today** (section 10): two FTSE names are priced in dollars but scaled as pence, so the book "bought" 593 shares of IHG at £1.63; paper books never receive dividends and now never receive the cash interest the backtest earns, a 3 to 5 point per year gap that has nothing to do with the strategy; a stock split would book a 50% loss; the drawdown breaker never resets its high-water mark, so one 20% loss becomes a permanent off switch; the live IBKR path has no order ids, no reconciliation and would duplicate every order on a re-run.

**The FX side.** The −92% backtest headline is mostly two artefacts, a fixed-dollar crypto spread and the breaker latch. With both corrected the five agents have a gross Sharpe near zero. The genetic swarm's permutation test says its search is indistinguishable from searching noise (p = 0.47). The neural agent is built to a high standard and is learning nothing from price-only inputs, which is what the published literature predicts for those inputs. The nightly workflow cannot load a graded model in any case.

**Code.** Dead code is under one percent (about 170 lines); the codebase is tight. The weight is in duplication and size: two renderers for the same FX books, 988 lines of HTML inside a Python module, three dependency declarations and no lockfile, three functions with a complexity score of 50 to 68, and a 3,816-line JavaScript file with no tests.

**What I recommend, in order** (section 12): merge the branch so the fixes reach the books; fix the two FTSE tickers and the breaker latch; correct the correlation assumption and re-run; replace the binary regime switch with graded de-risking and measure; credit dividends and cash interest in the paper books so live-versus-backtest tracking means something; stop or shrink the FX books until a cost-corrected backtest shows a positive net Sharpe; and point the next quarter of effort at the equity signal rather than the learning layer.

**Verification status.** Eighteen agents produced 143 de-duplicated findings. The adversarial pass was interrupted on 19 September and **completed on 24 September: all 143 judged, 128 confirmed, 11 refuted, 4 plausible, severity revised on 60.** Section 15 carries the corrections it forced, three of which overturn claims made in this document. Read section 15 before acting on section 10.


---

## 2. How the system works, end to end

The repo is two systems sharing one philosophy. The **equity system** is small and simple at its core (about 390 lines of signal and sizing logic) wrapped in a lot of machinery. The **FX system** is a second, self-contained trading stack under `trading_algo/forex/` with its own books, its own backtester and a machine-learning layer. Both are run unattended by GitHub Actions on a schedule and both write their state back into this git repository, which is the ledger of record.

### 2.1 The equity strategy (the part with a 14-year record)

Once a month, for each region:

1. **Rank** every stock in the region's list by 12-1 momentum: price 21 trading days ago divided by price 252 trading days ago, minus one (`signals.py:18`). This is the textbook definition from Jegadeesh and Titman (1993).
2. **Filter.** A stock must have positive momentum, sit above its own 200-day moving average, and the region's index (S&P 500, ASX 200, FTSE 100, TSX Composite) must be above its 200-day average. If the index is below, the whole sleeve goes to cash. That last rule is the regime filter.
3. **Pick the top 10** survivors and weight them inversely to their volatility (calmer stocks get more money), capped at 15% each.
4. **Size the book** so its estimated volatility is 12% per year. The estimate assumes every pair of held stocks is 0.6 correlated. The book can only be scaled down, never levered above 100% invested (`config.py` sets `max_gross = 1.0`).
5. **Trade the next day** at the close, paying commission, slippage and (for London) stamp duty. In paper trading, positions are rounded down to whole shares and per-name trades below a dust floor are skipped.

All of steps 2 to 4 live in one function, `strategy.compute_targets`, which the backtester and the paper trader both call. A test parses the source code of both callers to make sure neither one re-implements the sizing maths (`tests/test_consistency.py`). That is the repo's most important design decision and it is genuinely well enforced.

The four sleeves are combined into one AUD portfolio at 25% each. The portfolio backtester converts each sleeve's returns into AUD, including the currency move, and compares the result against an equal-weight basket of the four regional indices.

### 2.2 The equity paper books

Four simulated accounts run daily after the US close (cron at 21:30 UTC, in practice landing between 21:50 and 23:47):

| Book | Capital | What it is |
|---|---|---|
| `full` | A$100,000 | The realistic test: ASX, US and FTSE sleeves at a third each. Opened 2026-06-11. |
| `small` | A$1,000 | A deliberate demonstration of how commissions and whole shares destroy a tiny account. |
| `ultra` | A$10,000 | US only, 3x leverage allowed, 35% vol target, regime filter off, breaker off. |
| `experimental` | A$10,000 | US only, long the top 6 and short the bottom 6, meant to be market-neutral. |

Each run downloads fresh prices, refuses to trade or value a sleeve whose latest bar is stale or whose held names have no price (a guard added after the phantom liquidation described in section 10), rebalances on the first run of a new calendar month subject to a 20-day minimum gap, marks everything to market in AUD, checks the 25% drawdown breaker, and saves state to SQLite and a JSON twin. `verify.py` then re-derives every book from its own trade ledger and grades what it finds.

### 2.3 The FX and multi-asset books

Four more paper books trade currency pairs, crypto and, in one case, US stocks and bond ETFs:

| Book | Capital | Universe | Bar | Cadence |
|---|---|---|---|---|
| `matt` | A$5,000 | 7 FX majors + BTC, ETH, SOL | daily | nightly |
| `partner` | A$5,000 | same, conservative profile | daily | nightly |
| `daytrader` | A$10,000 | same | 60-minute | hourly (in theory) |
| `multiasset` | A$10,000 | AAPL, MSFT, NVDA, SPY, QQQ, TLT, IEF, AGG, SHY, AUDUSD | daily | nightly |

Every bar, five rule-based "agents" each vote between minus one and plus one on every instrument: trend (fast vs slow moving average when the market is trending), breakout (price above a 55-bar high), mean reversion (fade the Bollinger band when the market is not trending), momentum (60-bar rate of change) and carry (a fixed per-pair constant). A Hedge ensemble blends the votes, tilting toward agents that have recently done well. A risk layer scales the blend to a 10% vol target with up to 3x gross leverage and per-instrument and per-asset-class caps. A position policy applies a 2% no-churn band. Costs are half the bid-ask spread on every change, plus IBKR commission, plus overnight swap or financing on what is held. Instruments whose venue is closed are frozen rather than sold. The books mark in AUD.

### 2.4 The machine-learning layer

Three things can in principle learn:

- **A neural agent.** A small pure-NumPy network reads 19 price-derived features per instrument and outputs a position. Its training objective is the Sharpe ratio of the whole portfolio's return net of trading cost. It is graded by a purged walk-forward and only allowed into a live book if its out-of-sample net Sharpe is positive. It never has been, so the books run the five rule agents.
- **A genetic swarm.** Every month a breeder generates 424 rule variants ("genomes"), scores them on the first 75% of history and re-scores the 40 finalists on the last 25%. A champion gate requires a Deflated Sharpe of at least 0.95. Nothing has ever passed on any book. A permutation test added this week says the search on the `matt` book is indistinguishable from the same search on shuffled data (p = 0.47).
- **Research tools** (`research.py`, `policy_sweep.py`) that score candidate edges with the same DSR and PBO machinery. Diagnostics, not learning.

### 2.5 Data

Equity prices come from Yahoo Finance with `auto_adjust=True`, which folds dividends into the price series. That makes the strategy's data a total-return series. The regional indices used as the regime switch and as the performance benchmark are price indices, which do not include dividends. Prices are cached as Parquet files with a 20-hour expiry for open-ended requests. A data-quality gate excludes names whose feed looks dead, frozen or split-corrupted. Point-in-time index membership and delisting returns are implemented but no region supplies the membership file, so every backtest runs on today's survivors.

FX and crypto bars come from Yahoo by default (delayed about 15 minutes, and including the bar still being formed), with adapters for ccxt, OANDA, Alpaca, OpenBB and the ECB's daily fixings behind a resolver. Crypto trades seven days a week, so the shared calendar has weekend rows on which FX pairs are carried forward at Friday's close.

### 2.6 Dashboard, reporting and alerts

A zero-dependency web dashboard (Python standard-library server plus a 3,816-line vanilla JavaScript single-page app) shows every book behind an account switcher. An exporter bakes the same app into a single HTML file that GitHub Pages publishes after every scheduled run. A second, independent renderer (`forex/dashboard.py`, 2,087 lines) draws candlestick pages for the FX books. Monthly Markdown tear sheets, transaction-cost analysis, live-vs-backtest attribution and a promotion-gate report run on the first of each month. Alerts go through one function to a webhook (an ntfy.sh topic, set on 2026-09-19).

### 2.7 Infrastructure

Nine GitHub Actions workflows. `ci.yml` runs lint, a test matrix on Python 3.11 and 3.12 (1,121 tests), a type-check on 14 money-path modules, security scans, a 75% branch-coverage gate and a report-only mutation-testing job. Four scheduled workflows advance the books and commit `state/` back to `main` with a rebase-and-push loop. Dependencies are declared as version floors in three partially overlapping files with no lockfile.

### 2.8 Two facts that frame everything else

**The 40 commits on the branch under review are not deployed.** The schedulers only run on `main`. Every remediation recorded as "done" in the September audit (the strict audit gate, the webhook alerts, the cache expiry, the champion wiring, the FX backtest tab, the monthly reports, TSX funding, the long/short lot fix) exists only on `feat/dormant-feature-remediation`. The live books are still running the July code.

**Another session was committing to this branch while the review ran.** The readers examined commit `9d21578`. Since then six commits landed (HEAD is now `bf678c2`): interest on idle cash in the backtester (`57676c4`), after-tax reporting (`1f2e126`), a fix that lets a long/short book concentrate instead of never trading (`b997a9a`), FX cost decomposition (`4605b8d`) and two permutation-test fixes. The idle-cash credit changes every headline number by roughly two percentage points of CAGR and 0.2 to 0.3 of Sharpe. This document therefore quotes two number sets and labels them: **pre-credit** (commit `9d21578`, which is also what the dashboard cache and `origin/main` report) and **HEAD** (`bf678c2`, cash credit on, which is what the next backtest will report).


---

## 3. Investment performance, measured honestly

Three agents independently re-ran the real-data backtest and arrived at the same numbers, so the figures below are cross-checked. All use real Yahoo data 2012-01 to 2026-09-18, costs on. Sharpe here is the dashboard convention (minus a 3.5% cash rate).

### 3.1 The headline, two ways

| Portfolio (4 sleeves, AUD) | CAGR | Vol | Sharpe | Max drawdown | Final value from A$100k |
|---|---|---|---|---|---|
| Strategy, **pre-credit** (commit `9d21578` / the dashboard cache on `main`: no interest on idle cash) | 6.1% | 8.7% | 0.32 | −11.3% | A$243,601 |
| Strategy, **HEAD `bf678c2`** (interest on idle cash credited, commit `57676c4`) | 8.6% | 8.1% | 0.62 | −9.8% | A$346,826 |
| Repo's benchmark: equal-weight **price** indices | 9.6% | 13.1% | 0.50 | −29.9% | A$397,269 |
| Fair benchmark: equal-weight **total-return** index ETFs (STW, SPY, ISF, XIU in AUD) | 11.3% | | 0.65 | −29.6% | A$502,647 |

Three things to take from that table.

**The benchmark in the repo is unfair in the strategy's favour.** The strategy's prices include dividends; the yardstick does not. Against a fair yardstick, buy-and-hold index funds would have ended with about A$156,000 more than the strategy over 14.7 years (on the cash-credit numbers) and beats it on Sharpe too, 0.65 to 0.62. Those two Sharpes are inside each other's error bars (about plus or minus 0.26 on 14 years of daily data), so the honest statement is: the strategy does not beat index funds on a risk-adjusted basis, and trails them by 2.7 points a year on raw return.

**What it does deliver is a much shallower worst loss**: minus 10% against minus 30%. It carries a beta of about 0.4 to the market, meaning it moves about 40% as much. A risk-averse investor might pay 2.7 points a year for that. That is a preference, not an edge.

**Roughly two points of every headline CAGR is interest on cash, not trading.** The cumulative idle-cash credit over the sample is 29 to 34% of NAV per sleeve. The strategy is a 40%-invested book and the working-tree change pays it for the 60% it does not use. That credit is realistic (a broker does pay interest), but it also means the 8.6% figure is not a statement about the momentum signal.

### 3.2 Per sleeve

| Sleeve (HEAD, cash credit on) | CAGR | Sharpe | Max DD | Mean gross exposure | Cost drag over 14.7y |
|---|---|---|---|---|---|
| US | 9.6% | 0.64 | −13.2% | 0.41 | 4.1% |
| ASX | 6.8% | 0.48 | −9.6% | 0.34 | 8.6% |
| FTSE | 4.2% | 0.11 | −15.5% | 0.41 | **24.7%** |
| TSX | 9.2% | 0.74 | −14.3% | 0.44 | 7.0% |

Before the cash credit the same sleeves read US 7.4% / 0.42, ASX 4.3% / 0.14, FTSE 2.0% / −0.15, TSX 7.1% / 0.48.

FTSE is the weak sleeve and the reason is turnover under a 0.5% purchase tax: stamp duty takes about two thirds of its 24.7% cumulative cost. The repo's own Sharpe study reached the same conclusion and recommends a lower-turnover FTSE variant rather than defunding it. Nothing has been built toward that yet.

### 3.3 What each rule costs: the ablation

The performance agent switched each rule off in turn on the working-tree code (cash credit on). Format is CAGR / Sharpe / max drawdown.

| Arm | US | ASX | FTSE | TSX |
|---|---|---|---|---|
| Baseline | 9.6% / 0.64 / −13% | 6.8% / 0.48 / −10% | 4.2% / 0.11 / −16% | 9.2% / 0.74 / −14% |
| Regime filter off | 11.7% / 0.73 / −22% | 9.9% / 0.73 / −20% | 4.0% / 0.09 / −26% | 11.1% / 0.78 / −22% |
| Stock 200-day filter off | 9.8% / 0.66 / −12% | 6.9% / 0.50 / −11% | 4.1% / 0.11 / −14% | 9.5% / 0.77 / −13% |
| Positive-momentum floor off | identical | identical | identical | identical |
| All three filters off | 12.6% / 0.80 / −21% | 11.2% / 0.88 / −16% | 5.1% / 0.20 / −25% | 12.1% / 0.88 / −20% |
| Vol targeting off | 11.1% / 0.51 / −27% | 9.2% / 0.46 / −20% | 5.4% / 0.20 / −18% | 11.5% / 0.68 / −19% |
| Pure top-10, no filters, no vol target, no breaker | 23.1% / 0.87 / −34% | 20.5% / 0.94 / −26% | 8.0% / 0.34 / −41% | 17.8% / 0.89 / −27% |
| Top 20 instead of 10 | 10.2% / 0.72 / −12% | 6.0% / 0.38 / −10% | 3.2% / −0.01 / −16% | 10.4% / 0.96 / −11% |
| Quarterly rebalance | 7.9% / 0.43 / −23% | 4.5% / 0.15 / −21% | 2.9% / −0.04 / −26% | 6.8% / 0.39 / −26% |
| Costs off | 9.9% / 0.67 | 7.4% / 0.56 | 5.9% / 0.32 | 9.7% / 0.81 |
| Assumed correlation 0.3 instead of 0.6 | 11.5% / 0.65 / vol 12.6% | 7.7% / 0.48 / vol 9.0% | 4.5% / 0.14 | 10.6% / 0.73 / vol 9.7% |

What this says, rule by rule:

- **The positive-momentum floor does nothing.** Removing it changes not a single number. It is a no-op because the top 10 by momentum always have positive momentum when the regime is on.
- **The per-stock 200-day filter is worth almost nothing** either way (0.02 to 0.03 Sharpe).
- **The regime filter is pure insurance.** It costs 2 to 3 points of CAGR and 0.04 to 0.25 of Sharpe on three of four sleeves, and roughly halves the worst drawdown. **(Corrected in section 15: it is not a defect. With the gate off the breaker never fires, so the two are not duplicates, and on Calmar the gate wins in every sleeve.)** Over a sample with only two short bear markets (2020, 2022) that trade looks bad. In a 2008-type year it would look very different. The sample cannot tell you which world you are in, and no test in the repo has tried a crash-heavy period.
- **Vol targeting helps Sharpe and hurts return**, and it is mis-calibrated: the assumed 0.6 correlation between held stocks is two to three times what the picks actually show (0.18 to 0.36), so the book runs at 8 to 10% vol instead of 12% even when fully invested. Setting the assumption to 0.3 lifts US CAGR from 9.6% to 11.5% with no Sharpe cost.
- **Monthly rebalancing earns its keep**: quarterly is much worse everywhere, which is consistent with a 12-1 signal decaying over a few months. **(Corrected in section 15: wrong. The test used one arbitrary quarterly phase, the worst of three. Averaged over phase, quarterly beats monthly in all four sleeves and halves FTSE's cost.)**
- **The raw momentum signal is real but modest.** The unfiltered top-10 book has the same Sharpe as a naive equal-weight of the whole universe (0.87 vs 0.88 for the US, 0.94 vs 1.00 for ASX) at higher return and higher vol. Forward one-month top-10-minus-universe spreads have t-statistics of 1.8 to 3.2 over the full sample and 0.6 to 2.0 over the last five years: alive, but statistically marginal recently.

### 3.4 Survivorship: which way and how much

No region supplies an index-membership file, so every backtest picks from today's list. The fingerprint: zero of 125 US, zero of 56 ASX and zero of 55 TSX names stopped trading before 2026. A real index loses 30 to 50% of its members over 14 years. Equal-weighting today's ASX list beats the ASX total-return index by 9.4 points a year, the TSX list by 5.4, the US list by 2.5, and the FTSE list by roughly zero. The one sleeve without a strong survivorship footprint (FTSE) is the one where the raw signal is weakest. So the bias inflates every number above, by several points of CAGR on ASX and TSX and less on the US.

### 3.5 What the live books have proved so far: nothing, except one thing

| Book | Days | Return | Sharpe (raw) | 95% CI | Max DD | Explicit cost, % of capital |
|---|---|---|---|---|---|---|
| paper `full` | 99 | −0.3% | −0.40 | [−4.4, +3.6] | −2.6% | 0.46% |
| paper `ultra` | 78 | −1.6% | −0.20 | [−4.4, +4.0] | −9.9% | 0.48% |
| paper `experimental` | 78 | −5.6% | −2.84 | [−7.1, +1.4] | −8.2% | 0.22% |
| paper `small` | 98 | −4.4% | −2.47 | [−6.4, +1.4] | −6.3% | 0.28% |
| fx `matt` | 91 | −6.0% | −3.05 | [−6.9, +0.8] | −8.6% | 0.31% |
| fx `partner` | 91 | −3.9% | −3.28 | [−7.2, +0.6] | −5.6% | 0.15% |
| **fx `daytrader`** | 78 | −6.3% | **−4.60** | **[−8.9, −0.4]** | −8.6% | **2.89% in 55 days** |
| fx `multiasset` | 78 | +0.8% | +0.55 | [−3.9, +5.0] | −4.4% | 0.15% |

At about 60 daily observations the standard error on a Sharpe is plus or minus 2. Seven of eight intervals contain zero, so seven of eight books carry no information about edge in either direction. The exception is the hourly `daytrader` book, whose loss is 78% trading cost (A$289 of cost against A$90 of price loss). It is losing at a rate luck does not explain, and the reason is the spread it pays by turning over 0.44 times its equity every hour.

### 3.6 The FX backtests

Running the repo's own FX backtester on each live book's configuration (real data, HEAD code, isolated state):

| Book | Sharpe | Max DD | A$ start to end | Cost as % of equity |
|---|---|---|---|---|
| `matt` | −2.06 | −93% | 5,000 to 420 | 267% |
| `partner` | −2.14 | −67% | | |
| `multiasset` | −5.56 | −99.8% | 10,000 to 16 | 654% (commission 462%) |
| `daytrader` | −6.25 | | | |

Two artefacts inflate those catastrophes, and the FX-strategy reader measured them: the crypto spread is modelled as a constant dollar amount (BTC $120), which was 1,600 to 2,300 basis points per trade when Bitcoin was $250 to $450 in 2014 to 2016, and the drawdown breaker never resets its high-water mark, so once a book is 20% below its all-time high it re-trips after every 10-bar cooldown forever (381 halts on `matt`, 3,804 flat bars). Fix the spread to a percentage and the `matt` CAGR moves from −11.8% to −1.2%; also disable the breaker and it is +1.6% with a gross Sharpe of 0.36 and net 0.22. That is not an edge claim. It says the −92% headline is a cost-model artefact interacting with a bug, and that underneath it the five agents have a gross Sharpe near zero (+0.04 on `matt` 2003 to 2026). Only the mean-reversion agent shows a positive gross Sharpe on its own (0.64 gross, 0.40 net, breaker off), and its parameters were chosen before that measurement, so the whole period is in-sample for it.


---

## 4. Where the strategies are not being allowed to play out

This was the central question. The exposure-audit agent computed, from the state files, how much of each book's capital has actually been at work and which rule took the rest away. Then it graded each rule: working as designed, a throttle beyond design intent, or a bug.

### 4.1 Per book

| Book | Time in market | Gross exposure, actual vs intended | What removed the rest |
|---|---|---|---|
| `full` ASX sleeve | **0%** since 2026-06-11 | 0 vs about 0.35 | Regime filter (index below 200-day average) on 3 of 5 decision dates: design. On 2026-08-03 and 2026-09-01 the index was above trend (+2.8%, +3.0%) and the sleeve still went to cash: a missing index print made the whole 200-day window NaN under the code then on `main` (fixed 2026-08-31 by commit 404799e, on this branch only), and the 09-01 rebalance was lost to the phantom breaker trip (section 10). Two rebalances lost to bugs. |
| `full` US sleeve | 79% | 0.15 vs 0.28 to 0.34 | Whole-share rounding on $400 to $930 stocks removes 16 to 47% of the target every month. Throttle. |
| `full` FTSE sleeve | 79% | 0.57 vs 0.42 to 0.57 | Within 3 points of its gated target. The "80% target" in the September audit was computed without the data-quality gate that excludes three frozen names. Working as designed. |
| `small` | 15% | 0.04 vs 0.97 | Micro mode has produced zero trades since July. Bug (July H4, still open). |
| `ultra` | 100% | 0.47 to 1.46 vs 0.66 to 1.54 | 3x leverage cap never approached; the 35% vol target with the 0.6 correlation assumption caps it first. Design, but the same mis-calibration as the main book. Margin debit of US$2,356 is uncharged. |
| `experimental` | 38% | July book was 64% net short (one $593 SMH share against six shorts); flat since 08-03 | The post-rounding neutrality gate correctly refuses to run an unhedged book. But a A$10k long/short book cannot afford both legs in whole shares, so the experiment is structurally dead at this size. Its reported P&L since August is the AUD/USD rate moving a USD cash balance. |
| fx `matt` | continuous | 0.72 mean (cap 3.0); crypto pinned at its 0.10 cap since August | Vol scale sits at 77% of target; 74% of its 281 trades are same-sign resizes; 86% of cost is crypto. |
| fx `partner` | continuous | 0.46 | Conservative profile; vol at 77% of target. |
| fx `multiasset` | continuous | 1.17 | IBKR per-order minimum is 74% of modelled cost. |
| fx `daytrader` | continuous | 1.05 mean, 0.29 now | Advanced only 5 to 7 times per weekday of the 23 scheduled, never at weekends; 2,347 trades of which 678 are sign flips. |

### 4.2 The rules, graded

| Rule | Where | Fired? | Effect | Verdict |
|---|---|---|---|---|
| Regime filter | `signals.py:45` | ASX yes | Whole sleeve in cash 3 of 5 months | Design. But binary; best practice scales exposure smoothly instead (section 5). |
| Vol targeting with correlation 0.6 | `strategy.py` `vol_target`, `config.py:29` | every rebalance | Sets US gross at 28 to 34%; realised vol 8 to 10% vs 12% target | **Mis-calibrated.** Measured pick correlation is 0.18 to 0.36. The book is told to be riskier than it is allowed to be. |
| `max_gross = 1.0`, `max_vol_scale = 1.5` | `config.py:30-31` | never binds | Book can only be scaled down | Design choice, undocumented as such; `HOW_IT_WORKS.md` says the book "hits a 12% target", which the code cannot do. |
| Whole-share `int()` | `paper_trade.py:476` | every rebalance | 16 to 47% of US target lost; 100% of `experimental`'s long leg | Throttle. Rounds down always; a nearest-share rule or fractional shares (IBKR offers them for US stocks) would recover most of it. |
| Micro mode | `paper_trade.py:460-469` | `small` | Zero trades since July | Bug. |
| Long/short neutrality gate | `paper_trade.py:485-504` | `experimental` monthly | Flattens the book | Safety net doing its job on an unsizeable book. |
| Drawdown breaker | `paper_trade.py:792` | once, on a phantom −26% | 13 flat days, one lost rebalance, £46 of stamp duty to re-enter | Bug, since fixed. Separately, the breaker's high-water mark never resets (section 10). |
| `MIN_REBALANCE_GAP_DAYS`, `MIN_VIABLE_EQUITY_BASE`, dust floor | | never | none | Not binding on any book. |
| FX `max_vol_scale`, crypto gross cap 0.10 | `risk.py`, `fx_config.py` | yes | vol at 77 to 84% of target | Design. |
| FX hysteresis, min hold | `fx_config.py:83-88` | all zero on every profile | churn, not throttle | Median hold 4.5 bars against a 57-bar signal horizon. The knobs exist and are off; a sweep found no setting that helped out of sample. |
| Session gate | `sessions.py` | weekends | FX legs frozen | Design. |
| DSR/PBO champion gate | `champions.py` | every month | 0 of 424 promoted | Design, and correct: the permutation test agrees there is nothing to promote. |
| Neural agent floor | `promotion.py` | since 09-17 | refused | Design. But the daily workflow can never load a graded model (section 10), so the gate is also structurally unreachable. |

### 4.3 Is three months enough to judge any of this?

No. With about 60 daily observations the Sharpe standard error is 2.0. A true Sharpe of 1.0 needs roughly four years of daily data to be two standard errors from zero; a true 0.5 needs fifteen. The paper books were always going to be uninformative at this age. What they can show is mechanics, and they have: two books cannot form their intended positions, one sleeve lost two rebalances to bugs, and the hourly book is a cost machine.


---

## 5. The algorithm against the best

The benchmark agent pulled the published numbers for the same window and the same strategy family. Sources are listed in section 14.

### 5.1 Reference points, 2012 to 2026

| Reference | CAGR | Vol | Sharpe | Max DD |
|---|---|---|---|---|
| Kenneth French US top-decile 12-2 momentum, value-weighted, long-only (computed from his data, Jan 2012 to Jul 2026) | 18.7% | 21.6% | 0.83 | −31% |
| US market, same window | 14.9% | | 0.93 | |
| MSCI USA Momentum index, 10 years to Aug 2026 | 15.7% | | 0.76 | −56% (2007-09) |
| MTUM ETF, 10 years to Jun 2026 | 17.6% | 18.7% | | |
| Alpha Architect QMOM (50 names, monthly), 10 years | 13.4% | | | |
| MSCI Australia Momentum, 10 years | 7.7% | | 0.34 | |
| French Europe momentum factor (long-short) | 8.8% | 10.8% | 0.84 | −20% |
| **This repo, US sleeve (HEAD, cash credit on)** | 9.6% | 9.6% | 0.64 | −13% |
| **This repo, ASX sleeve** | 6.8% | 6.9% | 0.48 | −10% |

On a risk-adjusted basis the US sleeve (0.64) is within reach of the published long-only implementations (0.76 to 0.83). On raw return it is about half. The gap is exposure, not signal: the published indices are fully invested and this book averages 34 to 44% invested.

### 5.2 Scorecard: what best-in-class does that this book does not

| Criterion | Best practice | This repo | Grade |
|---|---|---|---|
| Signal definition | Jegadeesh-Titman 12-1; MSCI adds a 6-month leg and divides by 3-year vol | Pure 12-1, inverse-vol at the weighting step | B |
| Breadth | MSCI USA Momentum holds 126 of 525; French decile is 10% of all stocks | Top 10 of 56 to 125 large caps. Top-20 measured: no consistent gain | B |
| Volatility management | Barroso and Santa-Clara: scale by the strategy's own recent variance, "nearly doubles the Sharpe". Daniel and Moskowitz: dynamic scaler "approximately doubles alpha and Sharpe" | Constant-correlation estimator with an assumption (0.6) two to three times the measured value; can only de-lever | C |
| Regime de-risking | Graded, vol-scaled de-risking; hold bonds or cash-plus when out | Binary index-above-200-day switch; flat 23 to 35% of days; cash earns 3.5% since commit `57676c4`, nothing before | C |
| Rebalance timing luck | Jegadeesh-Titman overlapping portfolios; Newfound: tranching cuts timing luck by 1/N | Single month-end date. Shifting it by 0, 5, 10, 15 days moves CAGR by 1.4 to 3.5 points and FTSE Sharpe from 0.11 to −0.41 | D |
| Turnover control | Novy-Marx and Velikov: a buy/hold band is "the single most effective simple cost mitigation" | None: `nlargest(top_n)` re-picks every month; one-sided turnover 161 to 220% a year; FTSE pays 24.7% of NAV in costs | C |
| Cost realism | Commission, slippage, taxes, validated against fills | Always on, asymmetric stamp duty, impact model built but off | A |
| Data integrity | Point-in-time constituents, delisting returns | Mechanism exists, no membership file; 4 dead tickers silently dropped | C |
| Long-only vs long-short | Retail: long-only is right for 2012 to 2026 (US long-short momentum Sharpe 0.21 vs long-only 0.83) | Long-only core | B |
| Signal enhancements | Residual momentum (about 2x risk-adjusted profit), sector caps, quality or value blend | None built; price-only value proxy dormant | D |
| Reported-number freshness | Operator sees numbers from current code on current data | Dashboard BACKTEST tab reads a July file computed before four material code changes; the branch's own cache is stale | D |

### 5.3 The upgrades, in order of evidence per unit of effort

1. **Fix the correlation assumption** (one number in `config.py`). Measured: US CAGR 9.6% to 11.5% at the same Sharpe. Cheapest change in the repo.
2. **Replace the binary regime switch with graded de-risking** (Daniel-Moskowitz or Barroso-Santa-Clara scaling by the strategy's own recent vol). Uses only prices you already have and the existing vol-target plumbing. The repo's own strategy survey ranked this first in July; it is still unbuilt.
3. **Add a buy/hold band** so a name that drops from rank 10 to rank 12 is held rather than sold. Attacks FTSE's stamp-duty bill directly.
4. **Tranche the rebalance** across two to four dates in the month. Removes 1.4 to 3.5 points of pure timing luck from the reported numbers.
5. **Obtain a point-in-time membership file** for at least the US sleeve. Until then every number is an upper bound of unknown size.
6. **Round to nearest share, or use fractional shares** for US names. Recovers most of the 16 to 47% of target the US sleeve loses every month.

## 6. Architecture and operations against best practice

### 6.1 Architecture

The benchmark agent compared the repo with QuantConnect LEAN, Nautilus Trader, vectorbt, Zipline, backtrader, freqtrade, Qlib and hummingbot, and with institutional norms, and asked at every row whether the gap matters for a 10-name monthly book.

| Criterion | Grade | Verdict |
|---|---|---|
| Backtest engine shape | A | A daily walk-forward loop is the right choice at this turnover. An event-driven engine would be over-engineering. |
| Signal / construction / risk / execution separation | B | Clean, modelled on LEAN's five modules. Risk gates live in the paper engine rather than in front of the broker. |
| Backtest-live parity | A | One weight function, AST-enforced. Rare anywhere. |
| Config as data | B | Dataclasses with some validation; module constants unvalidated. |
| Order management | D | No persisted order ids, no open-order check. Acceptable only because live execution is dormant. |
| Broker reconciliation | D | Paper ledger reconciles against itself; nothing compares to a broker. Same caveat. |
| Kill switch | C | Automatic breaker exists; no operator halt. Hand-editing JSON is ignored because SQLite wins on load. |
| Bar storage | B | Parquet cache with a 20-hour expiry for the equity side. The FX cache never expires and CI carries it across runs. |
| State store | B | SQLite plus atomic JSON twin, hand-rolled migration. Authority is split: engine reads the DB, audit and dashboards read JSON. |
| Git as ledger | C | Three workflows commit state under three concurrency groups with a "theirs wins" conflict fallback on a binary DB. 437 runs, zero overlaps so far. Structural, not yet exercised. |
| Lockfile | D | Floors only, three declarations that disagree, actions pinned by tag. |
| Scheduler | C | GitHub cron. Fine for daily bars; the hourly book gets 5 to 9 of its 23 runs. |
| Observability | C | 239 print calls, no logging module; alerts via webhook since 2026-09-19. |
| Run manifests | C | Exist for `run_backtest` only; gitignored, so CI discards them and the trial count that feeds DSR is lost. |
| CI gates | A | Lint, types on money path, coverage floor, security scans, regression baseline. |

Verdict: at this scale the repo is right to lack an event-driven engine, a feature store, Docker and a time-series database. What is not optional even for a 10-name book: a lockfile, one concurrency group for anything that commits state, an operator halt, and, before any real order, broker reconciliation and order idempotency.

### 6.2 Live-trading operations

Graded against the SEC Market Access Rule and MiFID II RTS 6, which is what any firm touching a market electronically must meet, scaled down.

| Control | Grade | State |
|---|---|---|
| Pre-trade checks | C | NaN reject, 1.5x gross cap, 20% per-order cap. No price collar, no duplicate-order check. |
| Post-trade reconciliation vs broker | C | Paper ledger replay is good; no broker path. |
| Kill switch | D | None a human can press. |
| Order idempotency | F | A crash mid-loop and a re-run re-sends every remaining order. |
| Data staleness gates | B | Genuinely good in paper. |
| Scheduling correctness | D | Hourly book delivered 5 to 7 times a day; its cooldown counts runs, not bars. |
| Alerting | B | One channel, wired, no runbooks. |
| Disaster recovery | B | Git plus SQLite plus JSON; recoverable to last commit. |
| Secrets | A | Env only, scanned in CI. |
| Audit-trail immutability | D | The ledger is a bot commit on an unprotected public `main`; force-push is unrestricted. |
| Tax-lot tracking | B | Signed FIFO lots; no split adjustment. |
| **Corporate actions** | **F** | None. Share counts are never adjusted for splits, dividends are never credited to the ledger, a delisted or renamed holding becomes a permanent ghost. |

The corporate-action gap is the one already corrupting paper numbers rather than waiting for a live order (findings in section 10).

## 7. The machine-learning layer against best practice

| Dimension | Grade | One line |
|---|---|---|
| Data and features | D | 19 price-derived daily features on 16 correlated series (about 5 to 6 independent bets). No published evidence such inputs carry a learnable, cost-covering edge; the FX literature that finds predictability uses macro inputs or a monthly horizon. 19% of FX training labels are exactly zero because of weekend forward-fill. |
| Target and objective | B | Vol-scaled target, Sharpe-net-of-cost loss with a verified gradient. Best-practice design, undermined by the inputs. |
| Validation | B | Purge, embargo, scaler fit on training rows, early stopping, seed ensembling: all present and tested. But the grade retrains every 3.4 years while production retrains weekly, so the number the floor gates on describes a different procedure. |
| Multiple-testing control | C | DSR and PBO correct. Swarm hold-out re-derived from the same trailing 25% every month; neural hyper-parameter and seed searches counted nowhere. |
| Deployment gate | C | The floor refuses correctly. But the daily workflow trains with `--no-ml` and the weekly graded artefact is uploaded and never downloaded, so the gated path is unreachable. |
| Monitoring and MLOps | D | No model registry, no drift or skew monitoring, no shadow run. |
| Model and training discipline | A | Small regularised MLP, early stopping, probe-then-refit. The validation curve has no descent phase, which means the model is learning nothing, not that the machinery is wrong. |
| Evolutionary search hygiene | C | Sound design; 78% of one book's population is inert with fitness exactly zero; permutation test says the search finds noise. |

Plain verdict: the learning machinery is above the published bar and the inputs are below it. Gu, Kelly and Xiu's best neural nets explain 0.4% of monthly return variance using 94 characteristics on 30,000 stocks. Sixteen price series cannot get there. The repo's own three instruments (a null on the cost-aware objective, an empty champion roster, p = 0.47) all say the same thing. The next ML effort should be spent on inputs (fundamentals, rates, cross-sectional features, a monthly horizon) or not spent at all.

## 8. Code quality, simplicity and dead code

### 8.1 Measured against published norms

| Metric | Norm | Here |
|---|---|---|
| Function length | Google: reconsider over about 40 lines | 52 functions over 50 lines, 13 over 100, 3 over 200. Longest: `fx_book._run_once_locked` 335 lines, `dashboard/api.build_snapshot` 255, `paper_trade._run_daily_locked` 248 |
| Cyclomatic complexity (number of paths through a function) | McCabe: at most 10 | Average 5.1 (good). 84 of 738 blocks over 10. Three rank F: `_run_once_locked` 68, `_run_daily_locked` 50, `rebalance_sleeve` 50 |
| Module size | About 500 lines | `forex/dashboard.py` 2,087 (988 of them HTML, CSS and JS inside Python strings), `paper_trade.py` 1,223, `fx_book.py` 861 |
| Maintainability index | All modules A | 99 of 101 A. Two rank C: `forex/dashboard.py` (0.0), `paper_trade.py` (5.1) |
| Lint breadth | E, F, W, I, UP, B, SIM, C901 | Only E, F, W. Clean at that gate; 2,994 findings under the full ruleset, including 88 in-function imports, 56 blind `except Exception`, 247 `print` calls and no `logging` |
| Typing | Whole package | 14 money-path files only |
| Dependencies | One declaration, exact lock | Three declarations that disagree, no lock; CI installs `ruff>=0.6` against a declared floor of 0.16.6 |
| Test suite | Fast, hermetic | 1,121 tests, 213 s single-process, no parallelism; two tests are 15% of the wall clock; one test reads the committed live `state/` |
| Duplication | One renderer per view, one state reader | Two live FX renderers for the same book; three readers of the JSON fallback while the engine reads SQLite; 153 `add_argument` calls across 26 modules (21 copies of `--synthetic`) |
| Backtest-live parity | Every economic term shared or parity-tested | Weights: shared and tested bit-for-bit. Costs: explicitly excluded from the parity test. FX carry: backtest charges one day per bar regardless of bar length (23x on 60-minute bars). FX sessions: backtest trades FX on weekend rows the live book freezes (27.8% of FX-leg turnover) |

### 8.2 Dead code: there is very little

The dead-code agent walked the abstract syntax tree of all 96 modules (736 symbols), counted references across code, tests, workflows, scripts and the launchd agent, built an import graph, and checked every config knob, dataclass field, command-line option and environment variable for a reader. The answer: **this is a tight codebase**. Exactly one symbol is unreachable (`crypto_data.fetch_funding`, which a September design spec plans to use), nine are test-only, ruff reports zero unused imports or locals, and every config knob and CLI option is read somewhere. Total deletable code is about 170 source lines plus 40 test lines out of 21,510: under one percent.

The deletion list, for your decision (nothing has been deleted):

| Item | Lines | Evidence | Recommendation |
|---|---|---|---|
| `forex/indicators.py` `StreamingEMA`, `StreamingATR` | 32 | Test-only. `forex/README.md:411` promises a per-tick path that does not exist; the engine evaluates vectorised bar features | Delete both classes and the README line together |
| `forex/walkforward.py` `fit_final_model` | 11 | Zero callers | Delete |
| `forex/nn.py` `predict_proba` | 4 | Zero callers; the meta-labelling path reads `.predict` | Either make the meta path call it (semantically correct for a probability) or delete |
| `config.py` `Cooldown`, `cooldown_steps`, `COOLDOWN_*` | about 35 | The unit-tagged abstraction is constructed once and only `.length` is ever read | Delete the abstraction, keep the integer |
| `forex/sessions.py` `is_crypto` | 4 | Zero callers | Delete |
| `profiles.py` `BookProfile.description` | 1 | Never read | Delete |
| `manifest.py` `validate_manifest` | 17 | Test-only | Delete with its test, or wire into `run_backtest` |
| `forex/crypto_data.py` `fetch_funding` | 17 | Dead, but the carry-regime-monitor spec (2026-09-16) is its only planned consumer | Add to the README dormant register until that spec is built or dropped |
| `forex/research.py` | 191 | Has a `__main__`, listed in `CLAUDE.md`, invoked by nothing but its test | Decide: hand-run tool (keep, document) or superseded by `evolve`/`champions` (delete with test) |
| `packaging/`, `dashboard/desktop.py`, `tests/test_desktop.py`, two pyproject extras | about 110 | One commit on 2026-06-10, never built, dependency drift, bundle cannot locate `state/` | Delete unless you intend to ship a Mac app |
| `app.js` `accountEntry` | about 10 | The only unreferenced function in 3,816 lines | Delete |
| `manifests/portfolio_9d6fbd0e7c5f.json` at the repo root | data | Output of a run before the manifest path moved to `state/manifests` | Delete |
| `.claude/worktrees/self-learning-phase0` | | Orphan worktree, 2 unmerged commits from July, 136k lines behind | Remove the worktree |
| 27 `origin/claude/*` branches, 11 fully merged | | Experiment branches from June and July | Prune the merged ones |
| `outputs/` | 796 KB | An unrelated spreadsheet | Move out of the repo |

Do **not** delete: `dashboard/server.py` `do_HEAD` and `log_message` (HTTP handler overrides the linter flags falsely), and everything in the README's "Deliberately dormant" register, which this review found accurate for every item it lists.

### 8.3 The simplifications that would actually matter

Dead code is not where the weight is. These are:

1. **One FX renderer, not two.** `forex/dashboard.py` and `dashboard/fx_api.py` plus `app.js` render the same books; the terminal page draws synthetic candles for a book whose real bars the other page already has.
2. **HTML out of Python.** 988 lines of markup inside `forex/dashboard.py` string constants is why that module scores a maintainability index of zero.
3. **One dependency declaration and a lockfile.**
4. **Split the three rank-F functions.** `_run_once_locked` (335 lines, complexity 68) is the live FX loop; a bug there is a money bug.
5. **One state reader.** Either the JSON twin is the source of truth or SQLite is. Today the engine trusts the DB and the auditor and dashboards trust the JSON.
6. **Shared CLI scaffolding** for the 21 copies of `--synthetic` and 16 of `--account`.


---

## 9. Two honest framings

I asked one agent to build the strongest case that this system beats best practice and another to build the strongest case that it will not make money. Both were required to cite evidence and to say where their own case was weak. Both are worth reading in full, and both are right about different things.

### 9.1 The promoter's case

The research discipline is better than the field norm. No-lookahead is proved by a property test that perturbs every price after a cut date and asserts the equity curve before it is bit-identical, over forty generated histories. None of vectorbt, backtrader, Zipline or LEAN ships that. The one-weight-function invariant is enforced by parsing the source code of both engines and by a numeric identity test. Costs cannot be switched off, and the auditor treats a free fill as an error. The Deflated Sharpe, PBO and purged walk-forward machinery is real López de Prado methodology in pure NumPy, shared between both subsystems, and the promotion gate refuses live capital without it. The live books are reconciled against their own ledgers after every run. The static-tooling gaps the July benchmark listed (no type checker, no coverage, no property tests, no dependency scanning) have all closed.

On the failures: the empty champion roster, the null on the neural objective and the permutation p of 0.47 are three independent instruments agreeing that there is no edge to promote. A framework that promotes nothing on noise is working. Three months of flat paper books carry no information either way. And the equity strategy, on the cash-credit numbers, earns nearly the same CAGR as the price-index benchmark at a third of the worst loss.

Where the promoter concedes: the "cost model is honest, realised 5.0 bps vs 5.0 modelled" claim in the September audit is circular, because the paper fills are generated by the model (`tca.py` says so). The FX README claims a reused thread pool; the code builds a fresh one per cycle and threading is measured slower than single-thread. "Low-latency" headlines a cron-driven daily book on delayed bars. The August phantom liquidation of the main book is recorded in state and fixed, but absent from both audit documents. The live FX books close trades after 4.5 bars against a 57-bar signal horizon.

### 9.2 The adversary's case

On a like-for-like total-return basis the flagship strategy earns 2.7 points a year less than index funds and does not beat them on Sharpe. Its one genuine product feature is the drawdown. The return goes to two filters the docs describe as protection but never measured: switching off the regime gate lifts CAGR from 8.6% to 10.3% and Sharpe from 0.62 to 0.75 (bootstrap says 82% chance the filter hurt risk-adjusted return, which is suggestive, not proof). The vol target is unreachable by construction, and the repo answered the idle cash by paying interest on it rather than asking whether the cash should exist.

The FX side is worse and the repo largely knows it: all four books back-test at Sharpe minus 2 to minus 6, the docs already record two of them as "not viable at IBKR", and all four still run daily and count in the headline AUM. The daytrader book pays 20% a year in costs at 1,066 times annual turnover on a bar no broker could fill. The drawdown breaker in all four simulators never resets its high-water mark. The ML and swarm layer, about 3,400 lines scheduled monthly and weekly, has by its own gates produced nothing, and today's permutation result is written in no document.

Then the proportion: 21,510 application lines, of which 55% is FX and 43% is dashboards, plus 16,064 lines of tests and 56,792 words of documentation including audits of audits, supporting about 390 lines of equity signal logic and A$121k of paper capital, most of it flat or losing.

Where the adversary concedes: the Sharpe gaps sit inside sampling error, the drawdown halving is a real feature, the FX wipe-outs are partly artefacts of the peak latch and per-order minimums on a shrinking book, and the repo concedes most of the FX and ML nulls in unusual detail. The honesty is real. The problem is what the effort is aimed at.

### 9.3 Where I land

Both are right. The engineering discipline is unusually good and the audit apparatus does surface its own mistakes. But after fourteen years of history and every cost and credit the repo can model, the flagship strategy returns less than an index fund, and the two things most likely to change that (the correlation assumption and the binary regime switch) are one-line and one-function changes that were ranked first in the repo's own strategy survey in July and are still unbuilt. Meanwhile most of the new code since then has gone into the FX books and the learning layer, which the repo's own instruments say have no edge. The recommendation in section 12 follows from that.


---

## 10. Findings

Three tiers, by how they were verified. **Tier 1** findings I re-checked myself in the committed code and state files during this session; each carries the file and line I opened. **Tier 2** findings survived an independent adversarial skeptic agent that tried to refute them. **Tier 3** findings come from the first-pass readers and had not yet been through the adversarial pass when the account's session limit stopped the verifiers; they are reported as claims, not facts, with their source. Every finding ends with a plain-English line.

Severity scale: **critical** = wrong numbers on a live book or a silent broken invariant; **high** = a materially wrong result, a real risk, or a strategy prevented from trading as designed; **medium** = a quality or best-practice gap with real consequence; **low** = cleanup.

### Tier 1: verified directly in this session

**F1. The performance benchmark is a price index while the strategy is total return.** High. `portfolio_backtest.py:116-123` builds the benchmark from `^AXJO`, `^GSPC`, `^FTSE`, `^GSPTSE` (`regions.py:63,82,101,124`), which exclude dividends; `data.py:117` downloads the strategy's prices with `auto_adjust=True`, which includes them. The reported "strategy 6.1% vs benchmark 9.6%" gap is therefore understated by roughly the dividend yield. Against dividend-reinvesting index ETFs the adversary measured 11.3% CAGR and Sharpe 0.65 versus the strategy's 8.6% and 0.62. Reported by five independent agents. *Plain English: the strategy is credited with every dividend it would have received; the thing it is compared against is not. Once both count dividends, an index fund wins.*

**F2. The 12% volatility target cannot be reached, and the reason is a wrong assumption.** High. `strategy.py:67-79`: estimated book vol uses `avg_correlation = 0.6` (`config.py:29`), then `scale = min(target/port_vol, max_vol_scale)` and a de-lever to `max_gross = 1.0` (`config.py:30-31`). Measured correlation of the held names is 0.18 to 0.36, so the estimator overstates risk by about 1.5x, sizes the book to 51 to 65% of capital when invested, and realises 8 to 10% vol against the 12% target. Setting the assumption to 0.3 lifts US CAGR from 9.6% to 11.5% at the same Sharpe. `HOW_IT_WORKS.md:106` says the book "hits a 12% annual target", which the code cannot do. *Plain English: the sizing formula assumes the ten stocks move together much more than they actually do, so it thinks the book is riskier than it is and holds it back. One number in the config is the lever.*

**F3. The regime filter is measured cash drag over 2012 to 2026 on three of four sleeves.** ~~High~~ **Refuted in section 15 — the measurement holds, the conclusion does not.** Ablation (section 3.3): switching it off lifts CAGR 2 to 3 points and Sharpe 0.04 to 0.25 on US, ASX and TSX, and roughly doubles max drawdown. No document in the repo had ablated it. The sample has only two short bear markets, so its insurance value in a 2008-type year is unmeasured. *Plain English: the "go to cash when the index is below its 200-day average" rule cost return in a mostly rising market and would have paid off in a crash the sample does not contain. Best practice scales exposure smoothly rather than switching it off.*

**F4. The positive-momentum floor is a no-op and the per-stock 200-day filter is nearly one.** Medium. Ablation: `abs_momentum_floor = -1` produces identical results on all four sleeves; removing the stock trend filter moves Sharpe by 0.02 to 0.03. *Plain English: two of the three eligibility rules do nothing measurable.*

**F5. Whole-share rounding removes 16 to 47% of the US sleeve's target every month.** High. `paper_trade.py:476` uses `int()`, which always rounds down, on names priced $400 to $930 against a US$17k sleeve. The `full` book's US sleeve runs at 0.15 gross against a 0.28 to 0.34 target. *Plain English: you cannot buy 0.6 of a share of a $900 stock, and the code always rounds toward zero. IBKR offers fractional US shares; nearest-share rounding would also recover most of it.*

**F6. The `experimental` market-neutral book was 64% net short in July and has held nothing since 3 August.** High. Ledger in `state/paper_state_experimental.json`: on 2026-07-02 it opened six shorts (about US$2,700) against one long share of SMH (US$593); on 08-03 it closed everything at a realised loss of US$230; no trade since. The post-rounding neutrality gate at `paper_trade.py:485-504` correctly refuses an unhedged book, but at A$10k the long leg rounds to zero shares. Fix `b997a9a` (concentrate a long/short book) is committed on this branch and will first act at the next monthly rebalance, once deployed. *Plain English: a "market-neutral" book is supposed to be equally long and short. This one was almost all short for a month, then sat in US dollars. Its reported P&L since August is the exchange rate.*

**F7. The A$1,000 `small` book has placed two trades in its life and none since July.** Medium (it is a demonstration book). `state/paper_state_small.json`: BUY 3 SLV 06-12, SELL 3 SLV 07-01, `cash:idle` since 09-02. Micro mode (`paper_trade.py:460-469`) selects names it then cannot buy at a one-third slice. July finding H4, still open. *Plain English: the book meant to show how a tiny account gets eaten by fees is instead showing that it cannot trade at all.*

**F8. The ASX sleeve has been 0% invested since 11 June, and two of its five rebalances were lost to bugs, not to the filter.** High. `state/paper_state_full.json`: ASX cash exactly 33,333.33, zero trades. On 06-11, 07-01 and 09-15 the index was below its 200-day average (design). On 08-03 it was 2.8% above and on 09-01 3.0% above; the first was lost because a single missing index print made the whole 200-day window NaN under the code then on `main` (fixed by `404799e`, on this branch), the second to the phantom breaker trip in F9. The September audit's verdict "not a bug, regime filter working" was right for three months and wrong for two. *Plain English: a third of the main portfolio has been idle for three months. Most of that was the rule doing its job; two months of it were bugs.*

**F9. A phantom 26% drawdown liquidated the entire `full` book on 27 August and kept it in cash until 16 September, and no audit document records it.** ~~High~~ **Partly refuted in section 15: the incident is real, but it is fixed, regression-tested, corrected in state, and it IS recorded in `docs/research/REALISED_TRADE_EVIDENCE.md:177`.** `state/paper_state_full.json` `corrections`: FTSE was marked at cash only on an all-NaN price row, equity read A$74,552 instead of A$99,086, the 25% breaker tripped, sixteen names were sold, and £46 of stamp duty was paid to re-enter. Fixed by PR #93 (`paper_trade.py:292-308`, `:652-682`, the unpriced-holdings guard) and corrected in state with an audit trail. Absent from both `FORENSIC_AUDIT_2026-09.md` and `LIVE_BOOK_AUDIT.md`. *Plain English: a data gap made the book look like it had lost a quarter of its value, the safety stop fired on that illusion, and the book sat out for three weeks. The fix is good; the silence about it is not.*

**F10. The drawdown breaker's high-water mark never resets, in all four simulators.** High. `paper_trade.py:905-920` (`peak = max(peak, combined)`, never lowered after a halt), `forex/fx_backtest.py:176-184`, `forex/fx_book.py:579-587`, and the same shape in `backtest.py`. Once a book is more than the stop below its all-time high it re-trips on the first bar after every cooldown, forever. On the `matt` FX backtest that is 381 halts and 3,804 flat bars. It has not fired on a live book because none has fallen 20% yet. `tests/test_backtest.py:36` only asserts `halts >= 1`. *Plain English: the stop measures from the best value the account ever had and never forgets it. An account still 20% below that value after the cool-off is stopped again immediately. It is a permanent off switch disguised as a pause.*

**F11. Crypto spread is modelled as a fixed dollar amount, which makes the FX backtests wrong by an order of magnitude.** High. `forex/pairs.py:80-82`: BTC spread 120 pips at pip 1.0 = US$120; `marks.py:44-52` charges half of that as a fraction of price, so at Bitcoin $250 in 2015 a trade cost 24%. That is 96% of the FX backtest's total cost. Already named in `COST_AWARE_OBJECTIVE_RESULT.md` as the prerequisite for any retry; still shipped. *Plain English: the cost of trading Bitcoin is assumed to be $120 per unit regardless of whether Bitcoin is $250 or $60,000. The historical backtests are mostly a record of that assumption.*

**F12. The FX price cache never expires, and CI restores it across runs.** High. `forex/fx_data.py:66-76`: if the cache file exists it is read, with no age check; `.github/workflows/fx-paper.yml:70-74` restores `trading_algo/forex/.cache` with `restore-keys: fx-parquet-`. The nightly BACKTEST-tab refresh and the monthly swarm breeder therefore read a panel frozen at the first cached run. The equity cache got a 20-hour expiry in September (`data.py:37`); the FX cache did not. *Plain English: the FX backtests and the monthly breeding run are looking at prices that stopped updating whenever the cache was first written.*

**F13. The neural-agent lane in the nightly workflow can never activate.** ~~Medium~~ **Raised to high and corrected in section 15: true today, but the agent voted on 274 of 281 trades in `matt` before 17 September, from weights that no longer exist.** `.github/workflows/fx-paper.yml:80-93` trains a fresh bundle with `--no-ml` (which never stamps a grade) and then passes `--ml`; `promotion.clears_floor` refuses any ungraded bundle. The weekly job that does grade a bundle saves it as a run artifact that nothing downloads; `models/*.json` is gitignored. Confirmed by the skeptic against the real CI log (trained 01:06:26, refused ten seconds later). *Plain English: every night the system trains a model, forgets to grade it, and then refuses to use it because it has no grade. The one graded model ever produced would have been refused anyway (Sharpe −0.62).*

**F14. Two FTSE names are quoted in US dollars on Yahoo and are scaled as if they were pence.** High for live readiness, medium for the paper numbers. Verified live: `CPG.L` and `IHG.L` report `currency USD` (last 29.97 and 151.85) while `RR.L` and `HSBA.L` report `GBp`. `regions.py:111` multiplies every FTSE price by 0.01. The `full` book bought 593 IHG.L at "£1.63" on 06-11 and 724 CPG.L at "£0.32" on 07-01 (real prices about £120 and £24), and sold both in August. Because paper weights are notional-based the equity effect is limited to the embedded USD/GBP move, but the share counts are 70 to 100x wrong, the momentum ranking for those two names is computed on USD returns, and a live order would be catastrophic. Not in any prior audit. *Plain English: two London stocks come from Yahoo priced in dollars; the code divides by 100 as if they were pence, so it thinks they cost pennies and "buys" hundreds of them.*

**F15. The backtest ignores the per-trade commission floor.** Medium. `fees.py:48-56` charges basis points only; `fees.commission` with the floor is called only by `paper_trade.py:653`. July finding H3, still open. At live sleeve sizes the floor binds on nearly every fill. *Plain English: the backtest assumes commission is a tiny percentage; the real broker charges a minimum per order, which on small orders is far more.*

**F16. The "hourly" day-trading book actually advances five to six times per weekday.** High for that book. `gh run list` for `day-paper.yml`, scheduled runs per day 2026-09-14 to 09-18: 5, 5, 5, 6, 5 of 23 scheduled. GitHub documents that scheduled runs can be delayed or dropped. Its cooldown and minimum-hold counters count runs, not bars. *Plain English: the book designed to decide every hour decides five times a day, and nothing checks.*

**F17. The day-trading book is a cost machine, and its loss is statistically real.** High. `state/fx_state_daytrader.json` `cumulative` from 07-26 to 09-19: cost A$289.29, price P&L −A$90.10, carry −A$2.86; 2,347 trades. Sharpe −4.6 with a 95% interval of [−8.9, −0.4], the only live book whose interval excludes zero. *Plain English: three quarters of what this book has lost is the bid-ask spread it pays by trading 0.44 times its equity every hour on delayed data.*

**F18. All four FX books back-test severely negative by the repo's own backtester and still run daily, counted in the headline AUM.** High. Agent runs on real data, HEAD code, isolated state: `matt` Sharpe −2.06, `partner` −2.14, `multiasset` −5.56, `daytrader` −6.25; `docs/DATA_FEEDS.md` already records `multiasset` −23.98% CAGR and "not viable at IBKR". `fx-paper.yml:22` and `day-paper.yml:18` run them every night and every hour. F10 and F11 inflate the catastrophes, but with both corrected the agents' gross Sharpe is near zero. *Plain English: the currency and crypto books lose in replay, mostly to costs and two bugs, and underneath that show no edge. They keep trading.*

**F19. The 40 commits on this branch are not deployed; the live books run the July code.** High. `git log origin/main..HEAD`; the schedulers fire only on the default branch (`day-paper.yml:10`). Every September remediation, the strict audit gate, the alerts, the cache expiry, the champion wiring, TSX funding and the long/short fix exist only here. *Plain English: everything marked "done" in the September audit is done on a branch the robots do not run.*

**F20. The dashboard's BACKTEST tab reads a July file computed before four material code changes.** Medium. `state/backtest_equity.json` on this branch is `generated_at 2026-07-24`, three sleeves, Sharpe 0.28; `origin/main` has a 2026-09-19 refresh (`8477005`) that this branch does not include; neither includes the cash credit now at HEAD. *Plain English: the number on the screen is two months and several code changes old.*

**F21. Paper books never receive dividends; the backtest does.** Critical for interpreting live-versus-backtest tracking. `paper_trade.py:283-289` marks each position at the latest Yahoo close, which for the most recent bar is the raw price; no code path credits a dividend to `cash` (no dividend handling anywhere in `paper_trade.py`; ledger sides are only BUY and SELL). So on every ex-dividend date the price drop is booked as a loss and the cash never arrives, while the backtest's adjusted series counts the dividend as return. With the cash credit now committed at HEAD (`57676c4`) the backtest also earns 3.5% on idle cash and the paper books do not. On `full`, 73% in cash, that alone is about 255 bp a year of backtest-versus-paper divergence, above the 200 bp tracking budget. *Plain English: two things the backtest is paid for, dividends and interest on cash, the paper books are not. The paper books will trail the backtest by 3 to 5 points a year for reasons that have nothing to do with the strategy.*

**F22. A stock split would permanently corrupt a paper position.** High (latent). Share counts in the ledger are never adjusted and no corporate-action handling exists; after a 2:1 split the name marks at half its value. No held name has split since June, so it has not fired. *Plain English: if a company splits its shares two for one, the book keeps the old share count at the new half price and records a 50% loss that never happened.*

**F23. Two independent FX renderers publish the same books, one with synthetic candles.** Medium. `scripts/build_site.sh:33` runs `forex.dashboard` (real bars) and `:43-53` runs `dashboard.export` (terminal page) for every book; the terminal page draws deterministic synthetic candlesticks labelled SYNTHETIC. *Plain English: two different pages show the same account, and one of them draws a made-up chart next to real money.*

**F24. Three dependency declarations that disagree, and no lockfile.** Medium. No `*.lock`; `requirements.txt` ships pytest, hypothesis and ruff as runtime dependencies; `ci.yml` installs `ruff>=0.6` against a declared floor of 0.16.6; the local interpreter had numpy and yfinance below the declared floors. *Plain English: nothing pins the exact library versions, so today's scheduled run can install different code from the one the tests passed on.*

**F25. Dead code: about 170 source lines, and one abstraction.** Low. Verified test-only or unreferenced: `forex/walkforward.fit_final_model`, `forex/nn.predict_proba` (its only non-test reference is its own error string), `forex/indicators.StreamingEMA` and `StreamingATR` (referenced only by a docstring and a README line promising a per-tick path that does not exist), `forex/sessions.is_crypto`, `manifest.validate_manifest` (referenced only by its module docstring), `config.Cooldown` and `cooldown_steps`, `forex/crypto_data.fetch_funding` (no references at all). Full list and recommendation in section 8.2. *Plain English: the codebase is tight; the deletions are small and the real weight is in duplication, not dead functions.*

### Tier 2: survived an adversarial skeptic

Each of these was attacked by an independent agent told to refute it and to default to "refuted" if uncertain. The skeptic's suggested severity is shown where it differs from the first pass.

| # | Finding | Where | Skeptic's severity |
|---|---|---|---|
| S1 | The neural objective is trained in vol-normalised units but graded and traded in raw units; the docstring's claim that the risk layer scales positions by 1/vol is false for a multi-pair book | `forex/ml_agent.py:156`, `risk.py:64-70` | medium |
| S2 | The "carry" feature and the live Carry agent are a static per-pair constant that never changes; 16 symbols collapse to 11 distinct values and the three cryptos share 0.0 with no other identity feature | `forex/features.py:70`, `agents.py:115` | medium |
| S3 | Inert genomes score the population maximum and shrink the DSR deflation benchmark; fixes unlanded | `forex/evolve.py:99` | medium |
| S4 | The permutation test (p = 0.47 on `matt`) is recorded only in a state file, in no document | `state/permtest_matt.json` | low |
| S5 | The FX backtest and live loop disagree: the backtest trades FX on weekend bars at frozen prices (27.8% of FX-leg turnover, 25% of all cost) and charges one day of carry per bar regardless of bar length | `forex/fx_backtest.py:96,142` | medium |
| S6 | The daytrader book has negative gross edge and pays about 20% a year in spread; its own backtest is flat 78% of the time | `forex/fx_config.py:178` | medium |
| S7 | The trend agents use 55 to 100-bar windows the repo's own notes call under-evidenced; the well-documented 12-month FX momentum signal is not in the roster | `forex/fx_config.py:40` | low |
| S8 | Fill-at-signal-close convention (July H2) is worth +0.38 to +0.40 pp a year of CAGR versus the executable next-close convention, and the paper engine inherits it | `backtest.py:187`, `paper_trade.py:523` | medium |
| S9 | The FX dashboard payload is unsanitised: one NaN mark yields bare NaN tokens and a blank page | `dashboard/fx_api.py:380` | low |
| S10 | Two FX renderers; the terminal page draws synthetic candles for a book whose real bars the other page has | `dashboard/static/app.js:2344` | medium |
| S11 | The macOS packaging is abandoned and latently broken | `packaging/build_mac_app.sh:18` | low |
| S12 | `app.js` grew from 815 to 3,816 lines in nine weeks with no JS test harness and triplicated templates | `dashboard/static/app.js` | medium |
| S13 | The configured Tiingo fallback cannot serve a sleeve run: it never returns the regime index, so `load_region` raises | `data.py:194` | medium |
| S14 | Five dead tickers are silently dropped from the universes (FTSE trades 66 of 70, US 125 of 126); 10 to 15% of each list did not exist at the backtest start | `universes.py:52` | medium |
| S15 | Sizing vol on every FX and crypto leg is 0.83x the calendar truth (a seven-day calendar annualised at 252), so vol targeting runs about 20% hotter than its target | `forex/risk.py:32` | low |
| S16 | The live `matt` and `partner` books trade crypto on delayed Yahoo bars, not the ccxt route the config and docs say they use, because a book's stored source wins | `forex/fx_book.py:406` | low |
| S17 | The mutation-testing job has never executed a single mutant: mutmut crashes on start and `\|\| true` hides it | `ci.yml:152` | medium |
| S18 | The detect-secrets gate is line-number brittle | `ci.yml:116` | low |
| S19 | Three disagreeing dependency lists, no lockfile (also F24) | `requirements.txt` | medium |
| S20 | Each push runs the full suite three times and the synthetic backtest four times per job | `ci.yml:132` | low |
| S21 | The September audit's "cost model verified against actual fills, 5.0 vs 5.0 bps" is circular: the paper fills are generated by the model | `tca.py:11`, `FORENSIC_AUDIT_2026-09.md:67` | medium |
| S22 | The `--ml` lane cannot activate (also F13) | `fx-paper.yml:83` | low |
| S23 | The ML deployment grade retrains every 3.4 years while production retrains weekly | `forex/walkforward.py:156` | medium |
| S24 | Backtest metrics annualise at a fixed 252 whatever the bar, so the daytrader BACKTEST tab understates its loss rate | `metrics.py:30` | (first pass high; not re-graded) |

**Refuted by the skeptic** (kept so you can see what the first pass got wrong): that the backtest breaker and the paper breaker differing in scope is a defect (they are deliberately different, documented, and neither has ever tripped on real data); that the champion gate over-deflates by using 424 raw trials (the repo's own Monte Carlo study measured the effective-N estimators disagreeing by 40x and decided not to switch; the roster would be empty either way); and that the AUD 3.5% cash credit on foreign sleeves is a new defect (it is the July M14 issue extended, real but already recorded, and now committed).

### Tier 3: first-pass claims not yet independently verified

These are the remaining high-severity findings from the readers. Treat each as a hypothesis with a file to open. The verification runs can be resumed from cache once the session limit resets.

| # | Claim | Where | Source |
|---|---|---|---|
| U1 | The Hedge ensemble is effectively winner-take-all with a mis-set learning rate; three of five agents are one bet and mean reversion gets about 70% of the weight | `forex/ensemble.py:37` | fx-strategy |
| U2 | The equity NET P&L tile hides its largest component: FX translation (about −A$1,073 on `full`) appears nowhere on screen | `dashboard/api.py:416` | dashboard |
| U3 | The live dashboard re-downloads every region's full history with the cache off on every 5-second poll (64 s), and polls overlap | `paper_trade.py:187`, `app.js` | dashboard |
| U4 | The 60-minute and daily crypto legs are scored, filled and marked on a still-forming Yahoo bar; the completed bar is never re-scored | `forex/fx_book.py:426` | data-layer |
| U5 | Overlapping `day-paper` and `fx-paper` runs can silently discard a nightly run from the SQLite store via the `-X theirs` fallback (437 runs so far, zero overlaps observed) | `day-paper.yml:148` | infra |
| U6 | The CI regression baseline cannot detect a dropped transaction cost, the exact regression its docstring promises to catch | `ci_regression.py:31` | infra |
| U7 | FX books churn: two thirds of trades resize an existing position; the daytrader flips direction 678 times in 593 bars; hysteresis and minimum-hold knobs exist and are zero on every profile | `forex/fx_config.py:83` | exposure |
| U8 | The IBKR path sizes each region off the whole account's NAV with no allocation share and no FX conversion, cannot open shorts, reads the fill price before the fill exists, and persists nothing | `execution_ibkr.py:102-165` | equity-paper, ops |
| U9 | Live execution cannot see its own pending orders: no client order id, no open-order check, so an after-close re-run duplicates every order | `execution_ibkr.py:113` | architecture |
| U10 | The 16-symbol `DEFAULT_UNIVERSE` is what `main` and this branch trade; the training/traded split that stops the six crosses reaching live books is on an unmerged branch | `forex/pairs.py:131` | fx-ml |
| U11 | Survivorship sized: zero delistings in 14.7 years; 70% of ASX and 71% of TSX names beat their own total-return index | `regions.py:36` | performance |
| U12 | The cash credit uses a flat 3.5% AUD rate on every sleeve; the real US T-bill averaged 1.66%, so US CAGR is overstated by about 1 point | `backtest.py:163` | performance |

There are a further 57 medium and 46 low or info first-pass findings, listed with evidence in the review's working files. The medium ones cluster in five places: the FX backtest-versus-live divergences, dashboard number consistency, test-suite hygiene, documentation that overstates the code (the "low-latency" and "reused thread pool" claims, "cost model verified against fills"), and stale references in the README (it still says 79 tests; there are 1,121).


---

## 11. What the four prior audits said, and where each item stands today

The reconciliation agent re-verified every concrete recommendation from the July forensic audit, the September forensic audit, the CTO benchmark, the efficiency review, the Sharpe study and the backlog against the code as it is now. The one fact that reframes all of them: **every September remediation exists only on this unmerged branch**, so "done" below means "done in branch, not deployed to the books".

| Id | Source | Item | Status | Evidence |
|---|---|---|---|---|
| C1 | Jul | FX session gate | done | `forex/sessions.py`, `fx_book.py:498` |
| C2 | Jul | `--init` destroys a live book | done | Sept audit confirms |
| C3 | Jul | Whole shares break the neutral book | **regressed on `main`**: gate flattens the book monthly; fix `b997a9a` is branch-only | `paper_trade.py:611-627` |
| H2 | Jul | Fill at the decision close | open | `backtest.py:187`; paper fills at the same close (`paper_trade.py:523`) |
| H3 | Jul | Commission floor missing from the backtest | open | `fees.py:48-56` is pure bps; `fees.commission` called only by paper |
| H4 | Jul | Micro mode | open, and now produces zero trades | `paper_trade.py:552-558` |
| H5 | Jul | Borrow and margin on the equity stack | open | `config.py` "still NOT modelled" |
| H7 | Jul | `MetaLabeler` dead | done in branch | `0e21031` |
| H8 | Jul | DSR trial count | partly: champions uses 424; `research.py:115` still uses the grid width | |
| H9 | Jul | IBKR callers | dormant by design | README register |
| M10 | Jul | Point-in-time flag on the portfolio path | open | `portfolio_backtest.py:132` records the requested flag, not the effective one |
| M11 | Jul | State-commit race | open, worse: five workflows, five concurrency groups, all `git add -A state/` with a theirs-wins fallback | `fx-swarm.yml` cron 02:00 on the 1st vs `day-paper.yml` 02:07 |
| M12 | Jul | `engine --once` ignores the calendar | open | `engine.py:47` |
| M14 | Jul | AUD cash rate applied to USD, GBP, CAD sleeves | open, and the in-flight cash credit extends it | `metrics.py:33` single `RISK_FREE` |
| M15 | Jul | FX backtest tab | done in branch | `f00c667` |
| M16 | Jul | `paper-trade.yml` discards its exports | open | no upload step |
| M17 | Jul | Alerts only to log | done in branch | `2f0d665` |
| M18-M20, L19-L25 | Jul | Rendering, FIFO parity test, duplicated maths, regime fail-open, assertion-less test, `argv`, `utcnow` | all open | see the map |
| 4a | CTO | Live execution fixes | done | `execution_ibkr.py:125,143,156` |
| 4b | CTO | mypy, ruff, coverage, matrix, pip-audit | done; lockfile still absent | `ci.yml` |
| 4c | CTO | Cache key, cooldown | done | `data.py:190` |
| 4c | CTO | "Long-lived" agent pool | open, docs still false | `agents.py:153` builds a fresh executor per cycle |
| 4d | CTO | Universe side-table | deferred | |
| E1, E2 | Efficiency | Signal panel, vectorised data quality | done (24x, 14x measured) | `backtest.py:80`, `data_quality.py:125` |
| E4, E5, E6, E7 | Efficiency | Impact indexing, memoised data load, dashboard snapshot cache, duplicate dot product | all open verbatim | |
| 1-10 | Sharpe study | Margin debit, FX jump screen, DSR gate, MinTRL, CI on the dashboard, paper cash interest, geometric Sharpe, excess-return validation, low-turnover FTSE | all open (the cash interest is in flight) | greps empty |
| Phases 0-7 | Sept | The whole remediation | done in branch only; equity paper books still have no synthetic-state guard | `paper_trade.py` has none, `fx_book.py:70` has one |
| SQLite | Backlog | Dashboards read the DB | open | `registry.py:148,157` still glob JSON |
| 3 | Live-book audit | Holding period vs signal horizon | open | median hold 4.5 to 5 bars vs 57 on `matt`, `partner`, `multiasset` |

### What none of them asked

The prior audits were written by the same kind of agent this one was, and shared its blind spots. Things none of them checked, all verified in this pass: that the strategy loses to its own price-only benchmark, and by more against a total-return one; that the regime filter is Sharpe-negative on three of four sleeves over the sample; that four FX books with backtest Sharpes of minus 2 to minus 6 count in the headline AUM; that the "hourly" book runs five to seven times a day; that the A$1k book cannot place a single trade; that four universe tickers are dead on Yahoo; that a phantom breaker trip liquidated the whole main book for three weeks and no audit document records it.


---

## 12. Recommendations, in order

Each item says what to do, why, and how you would know it worked. Nothing here has been implemented; per your working preference these are for agreement first.

### First: make the numbers true

1. **Merge `feat/dormant-feature-remediation` to `main`.** Until then the live books run July code: no strict audit gate, no alerts, no cache expiry, the ASX NaN-window bug, the unhedged long/short book. Check: the next scheduled run's log shows the strict gate and `last_flat_reason`.
2. **Fix the two USD-quoted FTSE names** (F14). Either read the currency from Yahoo's metadata and scale per name, or drop `CPG.L` and `IHG.L` from the universe until the data layer can. Add a data-quality check that a "pence" name whose price is below £1 is suspect. Check: a replay of 2026-06-11 prices IHG.L near £120.
3. **Make the breaker's high-water mark reset** after a cooldown (F10), or measure from the post-halt equity. Add a test that a book still 20% below its old peak after the cooldown is allowed to trade. This also removes most of the FX backtest catastrophe.
4. **Replace the fixed-dollar crypto spread with a fraction of price** (F11). The cost-aware research doc already names this as the prerequisite for any ML retry.
5. **Give the FX price cache the same 20-hour expiry the equity cache has** (F12), and stop restoring it across CI runs, or key it by date.
6. **Use a total-return benchmark** (F1): the index ETFs in AUD, or a total-return index series. Report both, labelled. Every "vs benchmark" number in the dashboard and the tear sheets changes.
7. **Credit dividends and cash interest in the paper books** (F21), or remove the credit from the backtest. The point is that the two must agree, or the 200 bp tracking budget is measuring the wrong thing.

### Second: let the strategy play out

8. **Set `avg_correlation` to the measured value** (about 0.3) or estimate it from the trailing window (F2). Re-run the backtest and the regression baseline. Expected: US CAGR from 9.6% to about 11.5% at the same Sharpe.
9. **Replace the binary regime switch with graded de-risking** (F3): scale exposure by the strategy's own trailing realised volatility (Barroso and Santa-Clara) or a forecast of it (Daniel and Moskowitz). Keep the drawdown breaker as the catastrophe backstop. Then run the ablation again, and also run it on a crash-heavy window (2000 to 2012 on whatever universe you can construct) before deciding.
10. **Delete the positive-momentum floor and consider deleting the per-stock trend filter** (F4). Both are measured no-ops; each is a line of code and a config knob that can confuse a reader.
11. **Round to the nearest share, or use IBKR fractional shares for US names** (F5). Recovers most of the 16 to 47% the US sleeve loses monthly.
12. **Decide what the `small` and `experimental` books are for** (F6, F7). At A$1k and A$10k neither can hold its strategy in whole shares. Either fund them to a size that can, or close them and stop counting them.
13. **Add a buy/hold band** to `compute_targets` so a name that slips from rank 10 to 12 is held, and **tranche the rebalance** across two to four dates. Both are the standard cost and timing-luck mitigations; FTSE is the sleeve that pays for their absence.
14. **Fund a point-in-time membership file** for at least the US sleeve. Until then every number is an upper bound of unknown size.

### Third: stop paying for what does not pay

15. **Pause the FX, crypto and multi-asset books** until a backtest with the corrected spread and breaker shows a positive net Sharpe over a hold-out, and exclude them from the headline AUM meanwhile. The daytrader book in particular should stop: its loss is statistically real and it is 78% cost.
16. **Stop the weekly neural retrain and the monthly swarm breed** until the inputs change. The permutation test and the cost-aware null both say the search is finding noise. If the learning goal stands, the next attempt needs different inputs (rates, macro, cross-sectional features, a monthly horizon), not more machinery.
17. **Make one FX renderer** and move the 988 lines of HTML out of `forex/dashboard.py`.

### Fourth: engineering hygiene

18. **Add a lockfile and one dependency declaration** (F24). Pin GitHub Actions by SHA.
19. **One concurrency group for anything that commits state**, or a single writer workflow.
20. **An operator kill switch**: a file or flag the engine honours that flattens and halts every book.
21. **Corporate-action handling** (F22): at minimum, detect a split (price ratio between consecutive bars matching a Yahoo split event) and halt the sleeve with an alert.
22. **Broker reconciliation and order idempotency** before any real order (U8, U9). These are the two items every regulator requires and the repo lacks.
23. **Split the three rank-F functions** and turn on a broader ruff ruleset (imports at top, no blind excepts, logging instead of print).
24. **The dead-code list** in section 8.2, after your decisions on `research.py`, `fetch_funding` and the Mac packaging.
25. **Record the permutation result and the phantom liquidation** in the audit documents, and correct the documentation that overstates the code ("low-latency", "reused thread pool", "cost model verified against fills").

### What I would not do

Raise the vol target or add leverage before items 8 and 9 land. Lower `DSR_MIN`. Build more ML on price-only inputs. Add a fifth region before the four existing ones have a membership file.


---

## 13. How this review was produced, and its limits

**Method.** Eighteen agents read the repo in parallel with strictly read-only rules: eleven subsystem readers (equity core, equity paper and execution, exposure audit, FX strategy, ML layer, data layer, dashboard and reporting, infrastructure and tests, dead code, performance analysis, prior-audit reconciliation), a system promoter and a system adversary, and five best-practice benchmarkers (momentum literature, architecture, live operations, ML, code quality) with web access to primary sources. They produced 189 raw findings, de-duplicated to 143. Every critical and high finding was then attacked by a correctness skeptic and a materiality skeptic, defended by a promoter, and judged; every medium finding got a combined skeptic and a judge; low and info findings were verified in batches by a judge who opened each cited file. Only findings the judge marked confirmed appear in section 10 as facts; plausible ones are listed separately with what is missing. Refuted ones are listed so you can see what the first pass got wrong. A final critic agent then asked what the whole exercise had missed.

**What was verified by more than one agent.** The headline backtest numbers (8.6% CAGR, Sharpe 0.62 with the cash credit; 0.32 without) were reproduced independently by the performance analyst, the momentum benchmarker, the promoter and the adversary from the same cached real panel. The regime-filter ablation was reproduced by three of them. The exposure figures were computed by the exposure auditor and reproduced in part by the equity-paper reader.

**Limits.** All numbers are survivorship-biased because no membership file exists; treat them as upper bounds. The sample (2012 to 2026) contains only two short bear markets, so the regime filter's insurance value is undermeasured. The cash-credit numbers come from code another session committed as `57676c4` while the review ran; the dashboard cache has not been regenerated with it. Network access to Yahoo was available, so real-data measurements were possible, but the CI runner's cache contents and the live Yahoo bar boundaries at the cron hours could not be inspected from here. Nothing was written to `state/`; every measurement ran with isolated state directories. No code was changed; the deletion list in section 8 is a proposal.

**Concurrent edits.** While the review ran, another session modified `config.py`, `backtest.py`, `fees.py`, `forex/fx_backtest.py`, `forex/permtest.py`, `tax.py`, `CLAUDE.md`, `monthly-report.yml`, the September audit document and the regression baseline. Findings cite the committed code (`git show HEAD:`) and say where an in-flight change addresses them.


---

## 14. Sources

Academic and practitioner references cited by the benchmark agents, all fetched during this review:

- Jegadeesh and Titman, "Returns to Buying Winners and Selling Losers", Journal of Finance 1993.
- Asness, Moskowitz and Pedersen, "Value and Momentum Everywhere", Journal of Finance 2013.
- Daniel and Moskowitz, "Momentum Crashes", Journal of Financial Economics 2016 (NBER w20439).
- Barroso and Santa-Clara, "Momentum Has Its Moments", Journal of Financial Economics 2015.
- Novy-Marx and Velikov, "A Taxonomy of Anomalies and Their Trading Costs", Review of Financial Studies 2016 (NBER w20721).
- Blitz, Huij and Martens, "Residual Momentum", Journal of Empirical Finance 2011.
- Newfound Research, "Rebalance Timing Luck"; Quantpedia, "The Tranching Dilemma".
- Kenneth French Data Library: momentum factor and 10 portfolios formed on prior 12-2 returns (statistics for 2012-01 to 2026-07 computed by the benchmark agent from the CSVs).
- MSCI USA Momentum, MSCI Australia Momentum and MSCI World Momentum factsheets (31 August 2026); MSCI Momentum Indexes methodology; iShares MTUM factsheet (30 June 2026); Alpha Architect QMOM factsheet (30 June 2026).
- Gu, Kelly and Xiu, "Empirical Asset Pricing via Machine Learning", Review of Financial Studies 2020.
- Filippou, Rapach, Taylor and Zhou, "Exchange Rate Prediction with Machine Learning", CEPR DP15305, 2020.
- Neely, Weller and Ulrich, "The Adaptive Markets Hypothesis: Evidence from the Foreign Exchange Market", JFQA 2009.
- Lim, Zohren and Roberts, "Enhancing Time Series Momentum Strategies Using Deep Neural Networks", JFDS 2019.
- Kelly, Malamud and Zhou, "The Virtue of Complexity in Return Prediction", Journal of Finance 2024.
- Allen and Karjalainen, "Using Genetic Algorithms to Find Technical Trading Rules", Journal of Financial Economics 1999.
- Bailey and López de Prado, "The Deflated Sharpe Ratio", 2014; Bailey et al., "The Probability of Backtest Overfitting", 2017; López de Prado, Advances in Financial Machine Learning, 2018.
- Breck et al., "The ML Test Score: A Rubric for ML Production Readiness", IEEE Big Data 2017.
- SEC Market Access Rule 17 CFR 240.15c3-5; Commission Delegated Regulation (EU) 2017/589 (MiFID II RTS 6) Articles 12 to 17; ASIC Market Integrity Rules (Securities Markets) 2017 Part 5.6.
- QuantConnect LEAN Algorithm Framework docs; Nautilus Trader architecture and execution docs; vectorbt; Zipline-reloaded; Qlib; freqtrade; hummingbot kill-switch docs; IBKR TWS API order submission docs; GitHub Actions schedule-event docs; PEP 751.
- Google Python Style Guide; radon complexity and maintainability rank tables; ruff rule documentation.

In-repo documents this review builds on: `docs/FORENSIC_AUDIT_2026-07.md`, `docs/FORENSIC_AUDIT_2026-09.md`, `docs/CTO_ARCHITECTURE_BENCHMARK.md`, `docs/EFFICIENCY_REVIEW.md`, `docs/LIVE_BOOK_AUDIT.md`, `docs/SHARPE_RESEARCH.md`, `docs/MONTE_CARLO_RESEARCH.md`, `docs/PERMUTATION_TESTING.md`, `docs/EFFECTIVE_PUBLIC_STRATEGIES.md`, `docs/research/COST_AWARE_OBJECTIVE_RESULT.md`, `docs/research/REALISED_TRADE_EVIDENCE.md`, `docs/DATA_FEEDS.md`.

---

## 15. Verification round two, and the corrections it forced

The adversarial pass this document promised was interrupted by an account limit on
19 September. It was completed on 24 September, and it changes several things
written above. **Every finding in this document has now been through it: 143
judged, 128 confirmed, 11 refuted, 4 plausible, with severity revised on 60.**

Each finding was attacked by an independent agent instructed to refute it and to
default to "refuted" when uncertain, then judged by a third. Where a verdict
contradicts the body of this document, **the verdict wins and the correction is
below rather than silently applied**, because an audit that quietly edits its own
misses is not an audit.

### The three corrections that matter most

**The regime filter is not a defect, and finding F3 above is wrong.** The
measurement stands and was independently replicated three times: the filter holds
each sleeve in cash on 23 to 35% of days and costs 2 to 3 points of compound
growth and up to 0.25 of Sharpe. The *conclusion* fails on three counts.

| Claim in F3 | What verification found |
|---|---|
| It duplicates the drawdown breaker | With the gate off, the breaker fires **zero** times. Drawdown tops out at −22.3% (US) and −20.0% (ASX), below the 25% trigger. The breaker supplies none of the protection the gate supplies. |
| It lowers risk-adjusted return | True on Sharpe only. On **Calmar**, return per unit of worst drawdown, which is the measure the gate exists to serve, the gate wins in every sleeve: US 0.73 against 0.53, ASX 0.71 against 0.49. |
| The idle cash earns nothing meanwhile | Stale since commit `57676c4`. Idle cash now earns the cash rate. |

Graded de-risking is still worth testing against a binary switch, but as an
improvement to something that works, not as the removal of a drag.

**Quarterly rebalancing is not worse, and the inference in section 3.3 is
backwards.** The ablation used pandas' `QE` alias, which is not "quarterly" but
one specific phase of it, March/June/September/December, and it happens to be the
worst of the three available phases in all four sleeves. Averaged across phase:

| Sleeve | Monthly Sharpe | Quarterly, mean of three phases |
|---|---|---|
| FTSE | 0.110 | **0.220**, with cost drag cut from 24.7% to about 11% |
| US | 0.430 | 0.553 |
| ASX | 0.150 | 0.400 |
| TSX | 0.390 | 0.600 |

Annual rebalancing also beats quarterly in three of four sleeves, which no
signal-decay story permits. So the repo's standing recommendation of a
lower-turnover FTSE variant is the one the evidence supports, and this document's
claim that monthly rebalancing earns its keep was an artefact of one unlucky
phase. **This is now the strongest unexploited lead in the equity book**, and it
belongs in the next block rather than this one.

**The August phantom liquidation is fixed, tested and already recorded, so
finding F9's forward-looking half is wrong.** The incident happened exactly as
described. But the root cause is fixed and pinned by regression tests at
`tests/test_paper_trade.py:367`, the breaker path is now unreachable on an
unpriced book, the state carries a correction restoring both rows, and it *is*
recorded, in `docs/research/REALISED_TRADE_EVIDENCE.md:177`. What survives is
narrow: no audit *narrates* it, and no module reads the `corrections` block.

### One finding that got worse

**The neural agent did trade, and then silently stopped.** Finding F13 said the
machine-learning lane could never activate. That is true today and was not true
before. Verification found a `neural` vote recorded on **274 of 281 trades** in
the `matt` book and 226 in `partner`, cast by model weights that no longer exist
anywhere, with no bundle identity, no training window and no data-source stamp in
any trade record. The agent then vanished from every live decision after
17 September and nothing reported it. Severity rises to high, for the reason the
original finding only implied: with no persisted incumbent, the promotion gate
can never be satisfied in the cloud, so the lane is not merely idle, it is
unfixable in place.

### The full tally

| Verdict | Count |
|---|---|
| Confirmed | 128 |
| Refuted | 11 |
| Plausible, something remains unchecked | 4 |

Confirmed findings by final severity: 16 high, 56 medium, 51 low, 5 informational.
No finding retained a critical grade; the dividend defect, graded critical in the
first pass, was reduced to high on the argument that it corrupts a comparison
rather than a live book's cash.

The eleven refutations are listed in the review's working data. Besides the three
above they include: the backtest and paper drawdown breakers differing in scope
(deliberate, documented, and neither has ever tripped on real data); the champion
gate deflating by 424 raw trials (the repo's own Monte Carlo study measured the
alternatives disagreeing by a factor of forty and declined to switch, and the
roster is empty either way); the market-neutral book's unformable long leg, closed
by commit `b997a9a`; and the severely negative FX backtests, which are
substantially a cost-model artefact rather than a strategy result.

### What this round confirmed that was not known before

Sixteen findings are confirmed at high severity. The one that is both new and
live is not in the body of this document at all, because it happened after it was
written: **six currency crosses entered the live FX books on 22 September** and
now carry 32.4% of `matt`'s gross risk, 34.1% of `partner`'s and 20.7% of
`daytrader`'s. They were added to `DEFAULT_UNIVERSE` to give the neural model more
training rows, and because that one list also decides what the books trade,
`fx_book.py:411-413` merged them into every unlocked book. A cross is an
arithmetic combination of two majors, so this is not new diversification, it is
undisclosed concentration on exposures the books already held. The fix, commit
`f15a118`, splits the training list from the traded list and is merged nowhere.
