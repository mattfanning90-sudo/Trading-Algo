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
