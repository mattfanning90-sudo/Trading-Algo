---
title: Full System Review 2026-09
type: review
tags: [trading-algo, review, performance, architecture]
created: 2026-09-20
up: ["[[Multi-Region Momentum]]"]
---

# 🔍 Full System Review, September 2026

> [!abstract] The two-sentence version
> The engineering is unusually careful and most of the safety rails work. The strategy does not yet beat an index fund, mostly because it is only 40% invested, and the two changes most likely to fix that are one number and one function that nobody has built.

The full document, with every finding, its evidence and its verification status, is `docs/FULL_SYSTEM_REVIEW_2026-09.md` in the repo. This note is the plain-English digest.

## The numbers

**CAGR** is compound annual growth. **Sharpe** is return above cash divided by how much the ride wobbled; 0.5 is respectable, 1.0 is excellent. **Drawdown** is the worst fall from a peak. All of these are upper bounds, because the backtest picks from today's list of stocks and never sees the ones that failed ([[Survivorship Bias]]).

| 2012 to 2026, in AUD | CAGR | Sharpe | Worst drawdown |
|---|---|---|---|
| Strategy, as the dashboard still shows it | 6.1% | 0.32 | −11% |
| Strategy, once interest on idle cash is counted | 8.6% | 0.62 | −10% |
| The yardstick the repo uses (price indices, no dividends) | 9.6% | 0.50 | −30% |
| A fair yardstick (index funds with dividends reinvested) | 11.3% | 0.65 | −30% |

The strategy is compared against a benchmark that leaves out dividends while the strategy's own data includes them ([[Total Return vs Price Index]]). Against a fair benchmark, buying index funds would have ended with about A$156,000 more over the period, and would have matched the strategy on Sharpe. What the strategy genuinely delivers is a worst loss one third the size.

## Why it trails: it is a 40% invested book

Two mechanisms leave 56 to 66% of the money in cash on an average day ([[Cash Drag and Exposure]]):

- **The regime filter.** When a region's index is below its 200-day average the whole sleeve holds cash. That was true 23 to 35% of all days. Over 2012 to 2026, a mostly rising market with two short crashes, switching the filter off would have added 2 to 3 points a year and roughly doubled the worst drawdown. In a 2008-type year it would have earned its keep. The sample cannot tell you which.
- **The sizing formula.** It assumes the ten stocks move together with a correlation of 0.6. They actually measure 0.18 to 0.36. So the book thinks it is riskier than it is, holds back, and lands at 8 to 10% volatility against its 12% target even when fully invested. Changing that one number lifts US returns from 9.6% to 11.5% a year at the same Sharpe.

Two of the three eligibility filters do nothing measurable. Monthly rebalancing matters a lot; quarterly is much worse.

## Where the strategies are not being allowed to play out

| Book | What happened |
|---|---|
| ASX sleeve of `full` | 0% invested since June. Three months by design (index below trend), two rebalances lost to bugs since fixed on the branch. |
| US sleeve of `full` | Loses 16 to 47% of its intended position every month because shares are rounded down to whole numbers on $400 to $930 stocks. |
| `experimental` (market-neutral) | Was 64% net short in July because one $593 share of SMH was its entire long leg; flat since August. A A$10k long/short book cannot afford both sides. |
| `small` (A$1k) | Two trades in its life, none since July. |
| `daytrader` (hourly FX) | Runs five times a day, not 23. Has paid A$289 in costs against A$90 of price losses. The only book whose loss is statistically real. |
| All four FX books | Back-test at Sharpe −2 to −6, mostly from two artefacts (a fixed-dollar Bitcoin spread and a drawdown stop that never resets), and show no edge underneath. Still traded daily. |

And the biggest one: **none of the September fixes are deployed.** The schedulers run `main`; every fix lives on an unmerged branch.

## Things that would be wrong on a real account

- Two London stocks (CPG.L, IHG.L) come from Yahoo priced in dollars; the code divides by 100 as if they were pence, so the book "bought" 593 IHG shares at £1.63 (real price about £120).
- Paper books never receive dividends and now never receive the cash interest the backtest earns. That is 3 to 5 points a year of backtest-versus-paper gap unrelated to the strategy.
- A stock split would book a 50% loss that never happened.
- The [[Drawdown Breaker]] never forgets the account's best-ever value, so one 20% loss becomes a permanent off switch.
- The live broker path has no order ids and no reconciliation, so re-running it would send every order twice.

## The FX and machine-learning side

The learning machinery is built to a high standard: proper purged walk-forward, a verified gradient, a promotion floor that refuses ungraded models, a Deflated Sharpe gate on the genetic swarm. The inputs are the problem. Sixteen price series with 19 price-derived features cannot carry the signal the published literature says needs macro data, fundamentals or thousands of stocks. Three independent instruments agree: the cost-aware objective measured a null, no genome has ever cleared the champion gate, and a permutation test says the swarm's search is indistinguishable from searching shuffled data (p = 0.47, meaning luck does as well about half the time). The nightly workflow also cannot load a graded model even if one existed.

## Code

Dead code is under one percent, about 170 lines; the codebase is tight. The weight is in duplication and size: two renderers for the same FX books, 988 lines of HTML inside a Python file, three dependency lists that disagree and no lockfile, and a 3,816-line JavaScript file with no tests.

## What to do, in order

1. Merge the branch so the fixes reach the books.
2. Fix the two dollar-priced London names and the breaker latch.
3. Set the correlation assumption to the measured value and re-run.
4. Replace the on/off regime switch with graded de-risking, then measure, including on a crash-heavy window.
5. Credit dividends and cash interest in the paper books so tracking error means something.
6. Pause the FX books until a cost-corrected backtest shows a positive net Sharpe; stop the weekly retrain and monthly breed until the inputs change.
7. Then the hygiene list: lockfile, one state writer, a kill switch, corporate actions, broker reconciliation.

> [!warning] What not to do
> Raise the vol target or add leverage before the sizing and regime fixes land. Lower the Deflated Sharpe threshold. Build more ML on price-only inputs.

## How much of this is verified

Eighteen agents produced 143 findings. An adversarial pass (a skeptic tries to refute each one, a promoter defends it, a judge decides) got through 27 before the account's session limit stopped it: 24 survived, 3 were refuted. I re-checked the 25 highest-impact findings myself in the committed code and state files, and those are the only ones stated as facts. Twelve high-severity claims are still labelled unverified in the full document.

## Related
- [[How It Works]] · [[12-1 Momentum]] · [[Regime & Trend Filters]] · [[Volatility Targeting]] · [[Sharpe Ratio]] · [[Permutation Testing]]
- [[Total Return vs Price Index]] · [[Cash Drag and Exposure]] · [[Drawdown Breaker]] · [[Survivorship Bias]]
