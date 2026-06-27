# Strategy composition & tolerances — what survived honest testing

This note records the composition experiments run on the multi-strategy book, the
**de-biased** result (survivorship-corrected + AUD base), and the honest verdict on
the return target. Every number is from a real-data CI run (`multistrat_report`,
2007→2026, costs on, no-lookahead); synthetic runs are plumbing only (invariant #5).

## Headline: the honest, de-biased, AUD book

With point-in-time S&P 500 membership (`fja05680`: 2,595 snapshots / 1,058 names ever
in the index) + Tiingo delisted prices + delisting returns, and **AUD** as the base
currency (foreign sleeves unhedged):

| stream | CAGR | Sharpe | MaxDD |
|---|---|---|---|
| equity momentum (de-biased) | 4.8% | 0.15 | −37.0% |
| trend | 4.1% | 0.12 | −41.7% |
| **MULTI-STRAT (ERC, vol 0.12, lev 1.5)** | **6.5%** | **0.28** | **−24.5%** |
| SPY in AUD (buy & hold) | 11.6% | 0.48 | −40.2% |

- Up/down capture vs SPY **2.29** (takes 37% of upside, 16% of downside).
- Crisis years **2008 +30.6%, 2020 +1.8%, 2022 +15.2%** (SPY-in-AUD −21.7% / +7.7% / −12.3%).
- Survives the overfitting gauntlet: **Deflated Sharpe 98.8%**, **PBO 0%**, PSR 99.2%,
  regime Sharpe **bull 0.27 / bear +0.31** (both positive). The edge is **real but small**.

## Composition experiments & verdicts

| Config (real data) | CAGR | Sharpe | MaxDD | Verdict |
|---|---|---|---|---|
| US equity-mom + trend, ERC | (biased 8–9% / **de-biased 6.5%**) | 0.28–0.48 | −22 to −25% | **KEEP — the book** |
| + carry (income-yield) | 4.7% | 0.16 | −22% | **CUT — long credit beta, neg every crisis** |
| Global equity (US+ASX+FTSE) + trend | 7.7% (AUD) | 0.36 | −22% | **CUT — weak regional momentum + naive pre-ERC blend** |
| Levered vol 0.165 / lev 2.0 | 11.9% | 0.55 | −26.8% | **REJECT — breaches −25% DD budget** |
| Levered + reactive drawdown stop | 6.8% | 0.29 | −37.1% | **REJECT — stop whipsaws (worse than no stop)** |

- **Carry** (`carry.py`): income-yield carry derived from price data is not term-structure
  carry; it behaves as long credit/EM/REIT beta and loses in every crisis. Off by default.
- **Global equity**: ASX/FTSE momentum is genuinely weak (composite Sharpe ~0.2). It was
  also blended equal-weight *before* ERC, so the combiner couldn't down-weight the weak
  legs. Off by default. (To revisit properly: feed regions as separate ERC streams, and
  give weak markets a *different* premium, not more momentum.)
- **Leverage**: charged honestly (1% over rf on gross>1), but reaching 10% needs ~1.6×
  gross → breaches the −25% drawdown budget. A reactive drawdown stop makes it *worse*
  (whipsaws at crash bottoms). Risk is controlled **ex-ante** (vol target + diversification),
  not by a reactive stop.

## Two corrections that mattered

1. **Survivorship.** De-biasing dropped the US equity sleeve from Sharpe ~0.43 to **0.15**
   and the combined book to **6.5% CAGR** — confirming most of the apparent equity edge was
   bias (as `multistrat.py`'s own docstring warned). The honest base is **~6.5%, not 9–12%.**
2. **Base currency.** Reporting in **AUD** (the real base, invariant #6) flips the crisis
   read: in 2008 the AUD fell ~30% vs USD, so unhedged USD/foreign holdings *gained* in AUD
   (+30.6% combined in 2008). For an Australian investor the book is genuinely crisis-protective.

## Where the genuine edge is

Not in any standalone signal (all weak-to-zero de-biased). It is **structural**:
combining trend's convex crisis-alpha with an equity taker, **risk-sized (ERC) and
vol-targeted**, produces capture 2.29 and a −24.5% drawdown vs SPY's −40%, positive in
every crisis. The honest deliverable is *"most of the market's return at ~60% of its
drawdown, positive through crises"* — a low-drawdown diversifier, **not a 10% engine**.

## Verdict on a 10% CAGR target

**Not honestly achievable on this book.** The de-biased base is ~6.5% (inside the −25%
budget). 10% requires either survivorship bias (now removed) or leverage that breaches the
drawdown budget. Honest levers, ranked:

1. **Idle-capital yield** (`config.cash_yield` → T-bill rate): small, free, buildable now.
2. **A genuinely new uncorrelated paid premium** (real futures/term-structure carry, or a
   defensive/quality sleeve) — research with uncertain payoff; must clear DSR/PBO.
3. **A modest vol bump to ~0.13–0.14** inside the −25% budget — a few tenths of a percent,
   not the path to 10%.
4. **Per-market strategies** (value/trend where momentum is weak) — promising but the #1
   overfitting risk; only with a-priori rationale + out-of-sample validation.

Bottom line: ship the **US-momentum + trend, ERC, vol 0.12, AUD** book as a low-drawdown,
crisis-protective ~6.5% product, and treat 10% as a research aspiration contingent on a new,
validated, uncorrelated edge — never on leverage or bias.

## Edge hunt — round 2 (value, BAB)

Two candidate new premia were built and CI-tested de-biased (AUD); both **failed to
deliver a clean, validated win**:

| Candidate | de-biased combined result | Verdict |
|---|---|---|
| **Value** (long-term reversal, separate ERC stream) | Sharpe 0.28→**0.29**, CAGR 6.5→6.6%, capture 2.29→2.01 | **Wash.** Value's own premium was weak 2007–2026 ("value winter") and it shares too much equity beta with momentum to diversify. |
| **Low-risk / BAB** (long low-beta, short high-beta, single names) | standalone **−100% MaxDD / 424% vol** → combined book contaminated (vol 18.9% vs 12% target); DSR fell to 94.1% | **Broken / discarded.** Shorting high-beta single names from the delisted PIT universe = shorting illiquid penny stocks that spike 10×. Numerically hardened (per-name cap, vol floor, daily-loss guard) but still unsound. |

The BAB lesson is real: a single-name long/short sleeve on a delisted-inclusive universe
needs **liquidity/price filters + beta-and-dollar-neutral construction** (real infra), not
the ETF-sleeve sizing. Left opt-in/off until built properly.

**Conclusion after four edge attempts** (carry, global-equity, value, BAB): there is **no
easy uncorrelated edge** to bolt on with the current data (price-only, no fundamentals/
options/futures curves) and simple sizing. The honest, validated book stays **US-momentum
+ trend, ERC, vol 0.12, AUD → ~6.5% CAGR, Sharpe 0.28, −24.5% MaxDD, DSR 98.8%, positive
every crisis**. Pushing materially higher needs genuinely new data or real factor infra —
not another quick sleeve.

### BAB follow-up + the 4-stream book (round 3)

Adding a $5 penny-stock liquidity filter cut the BAB sleeve's vol (424%→38%) but it **still
ruins** standalone (−100% MaxDD, Sharpe −0.21) — a single extreme up-day on the short leg
still wipes it. The full **4-stream book (momentum + trend + value + BAB)** came out Sharpe
**0.22** — *worse* than the clean 2-stream **0.28**. Fifth failed stream-add. Verdict:
single-name L/S BAB is not safely buildable on this universe without a real risk model;
adding weak/wash streams dilutes via ERC.

### How to improve Sharpe (the levers that remain)

New streams have failed 5×, so Sharpe must come from the **existing** book:
- **Already done:** the momentum sleeve already constant-vol-targets (`strategy.vol_target`),
  so "risk-managed momentum" (Barroso-Santa-Clara) is largely captured — confirming the weak
  0.15 is a *signal* limit, not a vol-management gap.
- **Buildable, modest, low-overfit:** (a) **turnover reduction** — a no-trade / rank-hysteresis
  band in `signals.select_portfolio` so names aren't churned on marginal rank flips (net-of-cost
  Sharpe); (b) **covariance shrinkage** (Ledoit-Wolf) in `multistrat.combine`'s ERC for steadier
  risk weights.
- **Honest ceiling:** the de-biased signals are genuinely weak (momentum 0.15, trend 0.12,
  value 0.13); ERC already extracts the diversification (combined 0.28 ≈ √-uplift). ~0.28 is
  near the limit for this data — materially higher Sharpe needs better data, not more code.

## The honest path to a 10% CAGR target — asset allocation, not alpha

A higher CAGR is not an alpha problem (the active book's honest ceiling is ~6.5% / Sharpe
0.28). It is an **asset-allocation choice**: hold equity beta for the return, and use the
active book — which is genuinely uncorrelated to equities — to cut the drawdown. De-biased
frontier (AUD, 2007–2026, no leverage, no bias; blends of SPY-in-AUD with the active book):

| equity / active | CAGR | Sharpe | MaxDD |
|---|---|---|---|
| 0% / 100% active | 6.5% | 0.28 | −24.5% |
| 30% / 70% | 8.4% | 0.42 | **−21.0%** |
| 50% / 50% | 9.5% | 0.46 | −23.7% |
| **70% / 30%** | **10.4%** | **0.48** | **−26.4%** |
| 100% equity | 11.6% | 0.48 | −40.2% |

- **~70% equity / 30% active → 10.4% CAGR honestly**, at −26.4% MaxDD vs pure equity's
  −40.2% — the active book cuts ~14 points of drawdown for ~1 point of CAGR.
- The **30/70 blend raises Sharpe to 0.42 AND lowers MaxDD to −21%** vs the active book alone
  — diversification cuts both ways (equity diversifies the active book; the active book
  diversifies the equity holder).
- This is the original brief realised: **equities = upside taker, active book = downside
  mitigator.** Pick the blend that matches your drawdown tolerance; 10% CAGR sits at ~70/30.

The report prints this frontier on every run (`multistrat_report`), so the return/drawdown
tradeoff is always explicit and the target is a deliberate risk choice — never leverage or bias.
