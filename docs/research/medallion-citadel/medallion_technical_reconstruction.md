# Medallion-style statistical arbitrage: a quantitative reconstruction from the literature

As of 13 October 2026. Nothing about Medallion's internals is public. Every statement about Medallion below is either (a) a published, sourced number (fund returns, trade counts, holding periods and leverage from a US Senate report, and Zuckerman's data via Cornell), or (b) an inference labelled "consistent with". All derived numbers show their arithmetic, and the arithmetic was re-run in Python on 2026-10-13. "Target system" means the retail 12-1 momentum book this feeds: top 10 names (`top_n = 10`) per sleeve, monthly rebalance, sleeves ASX/US/FTSE/TSX, plus a small FX/crypto book.

## 1. Anchor: what is actually on the public record about Medallion (measured), and what is not

### Takeaway
The public record supports four facts: very high, steady gross returns (66% average gross, Sharpe about 2 on annual data); a negative market beta; a fund capped at about US$5–10bn; and very high trade counts (26–39 million trades a year) on mostly short holding periods (87% of positions held under 3 months), run at leverage of up to 20:1. Mercer's "right 50.75% of the time" is second-hand (Zuckerman, via Cornell). It describes trades, not independent bets.

### Cited Findings
- Medallion gross returns averaged **66.07%** a year from 1988 to 2018, with SD 31.66%. Net returns averaged 39.20% (SD 20.34%). The compound gross return was 63.3%: $100 grows to $398.7m. There were no negative years in 31; after the first two years the lowest annual gross return was 31.5%. Data are reproduced from Zuckerman (2019), Appendix 1 — [Cornell (Dec 2019), "Medallion Fund: The Ultimate Counterexample?", SSRN 3504766](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3504766) ([PDF mirror](https://community.portfolio123.com/uploads/short-url/wZJco8G9Xd3diZqEN2YNc3RdaH.pdf)). Caveat: Cornell's Table 1 is titled "1998 to 2018" and the text says "$100 invested at the start of 1998", but the rows and abstract run from 1988. These look like typos.
- Fees: 5% management plus a 20% performance fee (1988–2000), 36% (2001) and **44% (2002–2018)**. Fund size: $20m (1988) → $1.1bn (1998) → $5.2bn (2004–2009) → **$10bn (2010–2013, 2017–2018)**, with $9.5bn in 2014–2016 — [Cornell (2019)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3504766).
- "As the fund grew from $20 million to $10 billion ... the returns did not fall off." The best gross years were 2000 (128.1%), 2007 (136.1%) and 2008 (152.1%) — [Cornell (2019)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3504766).
- Cornell: SD 31.7% around a mean of 66.1%, "implying a Sharpe ratio of exceeding 2.0". Beta on the CRSP market is **approximately −1.0**. SMB and HML loadings are negative and insignificant. "The reported gross returns are after trading costs." — [Cornell (2019)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3504766).
- "According to Robert Mercer ... Medallion was right on only about **50.75%** of its trades ... taken over millions of trades that percentage allowed the firm to make billions." The strategy involved "constantly opening and covering thousands of short-term positions, both long and short" — [Cornell (2019), citing Zuckerman (2019)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3504766).
- The US Senate Permanent Subcommittee on Investigations report (22 July 2014, updated 30 Sept 2014) gives these figures:
  - RenTec "calculated that it used the basket option accounts to conduct an average of between **26 and 39 million trades per year**". A RenTec representative estimated 100,000–150,000 trades per day with each bank. A 2008 Barclays memo said: "This fund [Medallion] executes 500k trades / day."
  - Another passage of the same report says 29–36 million trades a year.
  - Source: [Senate PSI basket options report](https://www.hsgac.senate.gov/wp-content/uploads/imo/media/doc/REPORT-Abuse%20of%20Structured%20Financial%20Products%20(Basket%20Options)%20(7-22-14,%20updated%209-30-14).pdf) (read via [mirror](http://math.nyu.edu/faculty/avellane/basket.pdf)).
- An SEC examination (2010) found that from April 2008 to March 2009 RenTec placed "approximately **129 million trade orders**, involving nearly **10,000** different financial instruments across **18 countries**" — [Senate PSI report](http://math.nyu.edu/faculty/avellane/basket.pdf).
- From a RenTec chart covering Jan 2007 to Mar 2009: "RenTec's Medallion funds held positions for **less than three months 87%** of the time ... over six months less than 1% of the time". RenTec told the Subcommittee it "viewed the rate of change as critical to the success" of its strategy — [Senate PSI report](http://math.nyu.edu/faculty/avellane/basket.pdf).
- Leverage:
  - The basket options gave leverage "of as much as **20:1**, versus the much lower federal leverage limit of 2:1". Deutsche Bank offered RenTec up to 18:1 and Barclays up to 20:1.
  - RenTec's own 2012 letter to the IRS (quoted in the report) says its fund under a joint-back-office broker-dealer arrangement "had an average leverage ratio of **4.6:1** during [2005–2006], with a peak leverage ratio of 6.4:1".
  - Source: [Senate PSI report](http://math.nyu.edu/faculty/avellane/basket.pdf).
- Of more than $35bn in basket-option trading profits (Deutsche Bank from 1998, Barclays from 2002), "at least $34 billion came from options exercised after more than one year". Most came from assets held under a year but claimed as long-term capital gains. In other words, the structure converted short-term gains into long-term ones for tax — [Senate PSI report](http://math.nyu.edu/faculty/avellane/basket.pdf).
- Secondary only (not checked against the book): Zuckerman estimates Medallion averaged about **12.5× leverage** (up to 20×), traded about 8,000 securities, and held a typical trade about **two days** — [Medium summary of Zuckerman](https://t1mproject.medium.com/how-the-medallion-fund-sustained-66-p-a-for-30-years-and-generated-100-billion-f2a254c43eb7) (search-result level).
- Renaissance's outside-investor funds (RIEF, RIDA) do not follow Medallion's strategy and have had "relatively mundane" returns. Cornell takes this to suggest "a scale limit on whatever strategies have generated Medallion's returns" — [Cornell (2019)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3504766).

### Inferences
- **Annual-data Sharpe.** (66.07% − assumed 4% rf) / 31.66% = **1.96** gross. On net returns: (39.20 − 4) / 20.34 = **1.73**. These come from 31 annual observations, so they are noisy. Monthly-data Sharpe is not public.
- **The trades are not independent bets.** Section 2 shows that 50.75% on equal-size independent bets gives a per-bet Sharpe of 0.0150. A Sharpe of 2 needs only (2 / 0.0150)² ≈ **17,800 independent bets per year**. Taking the trade counts at face value (26–39m a year) would give a Sharpe of 0.0150 × √26e6 = **76** to 0.0150 × √39e6 = **94**. That is absurd. So Medallion's effective breadth is consistent with roughly 1/1,500–1/2,200 of its trade count. Most "trades" are probably child orders of larger orders, hedges, and correlated positions. This is an inference.
- **The best years were crisis years** (2000, 2007, 2008), and beta is about −1. Both are consistent with a large liquidity-provision / short-horizon contrarian component. Khandani & Lo (Section 4) note that market-making profits rise with volatility, and Kakushadze (Section 3) finds that alpha returns scale with volatility^0.76.
- **The capacity cap is a design choice.** It was held around $5.2bn (2003–09) and $10bn (2010–18), and outside capital was removed. This is consistent with a capacity-limited short-horizon book run near its profit-maximising size rather than at maximum AUM (see Section 7's square-root arithmetic).
- **Tax drag mattered even to Medallion.** It went to great lengths (the basket options) to turn short-term gains into long-term ones. The target system's own finding is that none of 50 round-trips cleared 12 months (per [CLAUDE.md](/Users/matthewfanning/Trading-Algo/CLAUDE.md)). That puts it on the same axis, without a structure to fix it.

### Gaps
- No public monthly or daily Medallion return series exists, so no Sharpe on higher-frequency data, no drawdown path and no volatility of returns within a year.
- The 50.75% figure is second-hand (Mercer → Zuckerman → Cornell). It is not defined: per trade or per position? Equal-sized? Dollar-weighted?
- The 12.5× leverage and two-day holding period are from a secondary summary of Zuckerman. I did not check them against the book.
- Leonard Baum (of Baum-Welch) is widely reported as an early partner of Simons. I did not verify this against a primary source this session, and nothing public says whether HMMs are used in production.

## 2. The Fundamental Law: hit rate × breadth → Sharpe, and what a monthly 12-name momentum book can reach

### Takeaway
On equal-size independent bets, a 50.75% hit rate is an IC of 0.015. That gives Sharpe 0.15 at 100 bets a year, 1.5 at 10,000 and 15 at 1,000,000. A monthly, mostly long-only, 10-name-per-sleeve momentum book has effective breadth in the low hundreds at best. To reach Sharpe 1 it would need an IC of about 0.04–0.14 before transfer-coefficient losses, and about 0.06–0.43 after. Measured monthly ICs for 12-month momentum are about 0.03–0.055.

There is a bigger problem. IC varies over time (σ_IC ≈ 0.13–0.18 for momentum), and that, not breadth, caps the information ratio. Ding's measurements put the realistic gross long-short IR for US momentum at about 0.56–1.49 a year.

### Cited Findings
- Grinold's (1989) law: **IR = IC·√N**, where N (breadth) should count only *independent* forecasts. Grinold himself warned that "an observed information ratio above 1.5 is rare indeed". Clarke et al. (2002) report a practitioner rule of thumb: "the theoretical information ratio ... should be cut in half" — [Ding (2010), "The Fundamental Law of Active Management: Time Series Dynamics and Cross-Sectional Properties"](https://math.nyu.edu/inmemoriam/avellaneda/FundamentalLawFT.pdf).
- For binary ±$1 bets with hit rate p: E(R) = N(2p − 1), Var(R) = 4Np(1 − p), so **IR = √N·(2p − 1)/√(4p(1 − p))**. Worked values: p = 0.52 → IR 0.64; p = 0.55 → IR 1.60 ("not that much better than a coin toss"). The worked values imply N = 256 bets. The model assumes repeated, independent, equal-payoff bets — [Graham Capital (Bethke, Nov 2024), "How Good is Good Enough?"](https://www.grahamcapital.com/quant-post/how-good-is-good-enough-some-math-behind-sharpe-information-and-hit-ratios/).
- Grinold's formula overstates achievable IR. A monthly IC of 0.03 over 1,000 stocks gives annual IR 0.03 × √1000 × √12 = **3.29**, "beyond even the most optimistic portfolio manager's dreams" — [Ding (2010)](https://math.nyu.edu/inmemoriam/avellaneda/FundamentalLawFT.pdf).
- Qian & Hua (2004): **IR ≈ mean(IC)/σ_IC**, where σ_IC (the time-series volatility of IC) is "strategy risk". Ding's own result: "the variation in IC (IC volatility over time) has a much bigger impact to portfolio IR than the breadth N for a typical investment universe". Empirically, factor IC runs 0.02–0.05 and σ_IC about 0.1. σ_IC was 4–10 times more important than the 1/√N term. "Play often" helps when N is small; when N is already large, what matters is playing "precisely (low σ_IC)" and "well (high IC)" — [Ding (2010)](https://math.nyu.edu/inmemoriam/avellaneda/FundamentalLawFT.pdf).
- **Measured IC of 12-month momentum** (US, monthly, data ending 2009:08; Ding Table 3):

  | Universe | Mean IC | σ_IC | Monthly IR (Qian–Hua) |
  |---|---|---|---|
  | Russell 1000 | 0.029 | 0.179 | 0.16 |
  | Russell 2000 | 0.055 | 0.128 | 0.43 |
  | Russell 3000 | 0.049 | 0.137 | 0.36 |

  Ding concludes Grinold's IR "needs to be cut by much more than half to be realistic" — [Ding (2010)](https://math.nyu.edu/inmemoriam/avellaneda/FundamentalLawFT.pdf).
- **Transfer coefficient (Clarke, de Silva & Thorley 2002, FAJ).** The generalised law is **IR = TC·IC·√BR**, where TC is the correlation between the ideal unconstrained active weights and the weights actually held. In practice TC runs 0.3–0.8, and the long-only constraint reduces it more than any other single constraint — [ResearchGate entry for CdST (2002)](https://www.researchgate.net/publication/228182902_Portfolio_Constraints_and_the_Fundamental_Law_of_Active_Management) (search-summary level).
- **Measured TC values** (Clarke, de Silva & Sapra 2004, JPM): S&P 500 stocks, 4% ex-ante tracking error, value signal.
  - All constraints imposed: **TC = 0.332**.
  - Industry or sector constraint relaxed: 0.347 / 0.34; both relaxed: 0.422.
  - Position limits relaxed: 0.298 (a slight decline).
  - Market-cap constraint relaxed: 0.471.
  - **Long-only constraint relaxed: 0.678**, the largest gain.
  - Even modest shorting helps: 110/10 is optimal at low tracking error and 150/50 above 3% tracking error.
  - Source: [CFA Digest summary of Clarke, de Silva & Sapra (2004)](https://www.hillsdaleinv.com/uploads/Toward_More_Information-Efficient_Portfolios,_Roger_G._Clarke,_Harindra_de_Silva,_Steven_Sapra,_The_Journal_of_Portfolio_Management,_Fall_2004,_Pages_54-63.pdf). The digest dates the original TC paper to "FAJ, 2000"; other sources say 2002.
- Ding argues the Clarke et al. TC "is not able to capture the impact of constraints to portfolio IR in the presence of IC variation" — [Ding (2010)](https://math.nyu.edu/inmemoriam/avellaneda/FundamentalLawFT.pdf).
- With correlated forecasts, effective breadth shrinks. Buckle (2004) gives a measurable definition of breadth for correlated bets. The standard equal-correlation result is N_eff = N/(1 + (N − 1)ρ) — [Buckle (2004), J. Asset Management, via EconPapers](https://econpapers.repec.org/RePEc:pal:assmgt:v:4:y:2004:i:6:d:10.1057_palgrave.jam.2240118) (formula form per search summary, not read in full).
- Target-system parameters (local repo):
  - Universes: ASX 56, US 90 stocks + 36 ETFs, FTSE 70, TSX 55 — [universes.py](/Users/matthewfanning/Trading-Algo/trading_algo/universes.py).
  - `top_n = 10`, `lookback_days = 252`, `skip_days = 21`, `rebalance = "ME"` (month-end) — [config.py](/Users/matthewfanning/Trading-Algo/trading_algo/config.py).

### Inferences
- **50.75% worked through.**
  - IC = 2p − 1 = 2(0.5075) − 1 = **0.015**.
  - Per-bet SD = √(4 × 0.5075 × 0.4925) = 0.99989, so per-bet Sharpe = 0.015 / 0.99989 = **0.01500**.
  - Annual Sharpe = 0.0150 × √N:

    | Independent bets per year (N) | Annual Sharpe |
    |---|---|
    | 100 | **0.15** |
    | 10,000 | **1.50** |
    | 1,000,000 | **15.0** |

  - Breadth needed: Sharpe 1 needs N = (1/0.0150)² ≈ **4,444**; Sharpe 2 needs ≈ **17,774**.
- **Breadth of the target book. There are three honest ways to count it:**
  1. *Naive count of positions × rebalances:* 12 names × 12 months = 144 a year per sleeve; × 4 sleeves = 576. This overcounts, because 12-1 momentum barely changes month to month (11 of 12 months overlap).
  2. *Turnover-adjusted:* a 12-1 momentum decile book turns over about 34.5% a month one-sided (Novy-Marx & Velikov, Section 4). That is ~0.345 × 10 ≈ 3.5 new positions a month per sleeve, so **~40–50 new bets a year per sleeve** and ~160–200 across four sleeves.
  3. *Grinold's forecast count:* about one independent forecast per name per year (because the signal overlaps), so BR ≈ universe size ≈ 56 + 126 + 70 + 55 = **~307** across four sleeves. But the cross-section is driven by a common momentum factor.
- **IC needed for Sharpe 1** (IC = 1/(TC·√BR)):

  | Breadth (BR) | TC = 1 (hit rate) | TC = 0.678 | TC = 0.332 |
  |---|---|---|---|
  | 576 | 0.042 (52.1%) | 0.061 | 0.126 |
  | 200 | 0.071 (53.5%) | 0.104 | 0.213 |
  | 144 | 0.083 (54.2%) | 0.123 | 0.251 |
  | 50 | 0.141 (57.1%) | 0.209 | 0.426 |

  Measured momentum ICs are about 0.03–0.055 (Ding). So Sharpe 1 is only within reach at the most generous breadth count and with little TC loss. That is optimistic for a book that is mostly long-only.
- **Qian–Hua ceiling for momentum.** Using Ding's numbers, annual IR = (IC/σ_IC) × √12:

  | Universe | Calculation | Annual IR (long-short, gross) |
  |---|---|---|
  | Russell 1000 | 0.162 × 3.464 | **0.56** |
  | Russell 3000 | 0.358 × 3.464 | **1.24** |
  | Russell 2000 | 0.430 × 3.464 | **1.49** |

  These are gross of costs, long-short and full-universe, so they are an upper bound for a long-only top-10 book. Multiplying by TC 0.33–0.68 gives roughly **0.19–0.38** for large-cap-like universes. This is an inference: TC and Qian–Hua are not strictly multiplicative, per Ding.
- **Medallion and the target are opposite on every term of IR = TC·IC·√BR.** Medallion is consistent with a tiny IC (~0.015 per trade), huge effective breadth (tens of thousands of independent bets a year) and a TC near 1 (long/short, levered, hedged). The target has moderate IC, small breadth and TC well below 1. Its biggest gap is breadth × TC, not IC.

### Gaps
- No published measurement of momentum IC or σ_IC for the ASX, FTSE or TSX large-cap universes at the target's size. Ding's figures are US, 1978–2009.
- No published TC for a "top-10 inverse-vol long-only" construction. The CdS&S figures are for an optimised S&P 500 book with a tracking-error constraint.
- Grinold & Kahn's book text was not read directly. Grinold (1989) and CdST (2002) are cited via Ding and the CFA digest.

## 3. Combining many weak signals: ensemble alphas, alpha decay, and how correlation limits effective breadth

### Takeaway
Industrial stat-arb is consistent with mining very many faint, short-lived alphas and trading their combination, with internal crossing of trades, rather than trading any single signal. Kakushadze's 101 real alphas have:
- median Sharpe about 2.2 before costs
- median holding period about 2.1 days
- median gross edge of only about 0.40¢ per share
- average pairwise correlation of 15.9%

That correlation caps the gain from combining them at about 1/√ρ ≈ 2.5×. Across markets, a single signal (momentum) is highly correlated: about 0.65 for one market against the rest. So four regional momentum sleeves give only about 1.5 effective sleeves.

### Cited Findings
- **101 Formulaic Alphas** (Kakushadze, Wilmott July 2016; arXiv Dec 2015). These are real production alphas (WorldQuant), measured Jan 4, 2010 – Dec 31, 2013, all **before** transaction costs:
  - average holding period ~**0.6–6.4 days** (median 2.10)
  - annualised Sharpe median **2.224** (range 1.238–4.162)
  - daily turnover median 47.5%
  - **cents-per-share median 0.3969¢**
  - annualised return median 5.44%
  - average (median) pairwise correlation **15.9% (14.3%)**
  - Source: [Kakushadze (2016), arXiv 1601.00991](https://arxiv.org/pdf/1601.00991)
- Alpha returns scale with volatility: ln(return) ≈ −3.509 + **0.761**·ln(vol), R² 0.737. There is "no significant dependence on turnover", and "turnover has poor explanatory power for alpha correlations" — [Kakushadze (2016)](https://arxiv.org/pdf/1601.00991).
- Quoted directly:
  - "ever fainter and more ephemeral alphas"
  - alpha counts "in hundreds of thousands and even millions"
  - these are combined into "a unified 'mega-alpha' ... with a bonus of automatic internal crossing of trades (and thereby crucial-for-profitability savings on trading costs ...)"
  - "the alpha sample covariance matrix is badly singular"

  Source: [Kakushadze (2016)](https://arxiv.org/pdf/1601.00991).
- Kakushadze & Yu (2017, J. Asset Management) give an explicit algorithm for optimally weighting N alphas that scales **linearly in N**. It avoids O(N³)/O(N²) operations, matrix inversion and PCA — [arXiv 1603.05937](https://arxiv.org/abs/1603.05937).
- **Short-term signal composite** (Blitz, Hanauer, Honarvar, Huisman & van Vliet, FAJ 2023). The universe is MSCI World (~1,750 stocks), Dec 1985 – Dec 2021.
  - *Individual signals* (short-term reversal, short-term momentum, analyst revisions, short-term risk, monthly seasonality): **6–8%** Fama-French six-factor gross alpha, but turnover of **1,300–2,000% a year**. At ~25 bps costs, net alpha is **below −2%**.
  - *Composite:* gross six-factor alpha **>12%**, and net alpha **>6%** a year with a "buy 10 / hold 50" rule. It works long-only and is robust to multi-day implementation lags.
  - Sources: [Robeco summary](https://www.robeco.com/en-us/insights/2022/05/beyond-fama-french-alpha-from-short-term-signals); [SSRN 4115411](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4115411).
- **Cross-market correlation of momentum** (Asness, Moskowitz & Pedersen, JF 2013; quarterly returns, 1972–2011):
  - Each equity market's stock-momentum strategy correlates **0.65** with the average of the other markets (US, UK, Europe, Japan). Stock momentum vs non-stock momentum: 0.37.
  - Correlation among momentum strategies rose from **0.46 (1972–91) to 0.77 (1992–2011)**.
  - Stock value vs stock momentum: −0.53.
  - A global 50/50 value-momentum combination had Sharpe **1.45**.
  - Source: [AMP (2013)](https://w4.stern.nyu.edu/facdir/lpederse/papers/ValMomEverywhere.pdf).
- **Alpha decay after publication** (McLean & Pontiff, JF 2016): across 97 published predictors, portfolio returns are **26% lower out-of-sample** and **58% lower post-publication** — [McLean & Pontiff (2016)](https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.12365).
- Isichenko (Wiley, Aug 2021), *Quantitative Portfolio Management: The Art and Science of Statistical Arbitrage*, written by a former stat-arb practitioner, covers combining multiple forecasts "using secondary machine learning and dimensionality reduction" and "forecast combination and its diversification limits" — [publisher/Google Books description](https://books.google.com/books/about/Quantitative_Portfolio_Management.html?id=s8E5EAAAQBAJ).
- Isichenko's companion paper on trading costs (arXiv, Oct 2021):
  - "Market efficiency implies a close balance between active alphas and trading costs, so even small changes to trading optimization can make a big difference."
  - Optimal trading has a **no-trade zone** whose width Δ ∼ c^(1/2) in slippage cost c for stable forecasts (vs c^(1/3) in stochastic settings).
  - Source: [Isichenko, "Costly Trading", arXiv 2110.15239](https://arxiv.org/abs/2110.15239).

### Inferences
- **The correlation ceiling.** For N equal-Sharpe signals with pairwise correlation ρ, the equal-weight combination has Sharpe = s·√(N/(1 + (N − 1)ρ)), which tends to s/√ρ as N → ∞.
  - For Kakushadze's set: N_eff = 101/(1 + 100 × 0.159) = **5.98**, so the multiplier is √5.98 = 2.44. The ceiling 1/√0.159 = **2.51** is almost reached already at N = 101.
  - With median s = 2.224, the combined gross Sharpe is ≈ 2.224 × 2.44 ≈ **5.4**, before costs.
  - Lesson: after a few dozen signals, **reducing correlation matters more than adding signals**.
- **The target's four regional momentum sleeves are one signal, four times.** Inverting AMP's "0.65 vs average of other three" (assuming equal vols and equal pairwise correlations), pairwise ρ comes from ρ·√(3/(1 + 2ρ)) = 0.65, so **ρ ≈ 0.54**. Then N_eff = 4/(1 + 3 × 0.54) = **1.52** sleeves, and the diversification multiplier is √1.52 ≈ **1.23** (vs 2.0 if independent).

  | Pairwise ρ | N_eff (of 4 sleeves) | Multiplier |
  |---|---|---|
  | 0.2 | 2.5 | 1.58 |
  | 0.4 | 1.8 | 1.35 |
  | 0.54 | 1.52 | 1.23 |

  AMP's markets are US/UK/Europe/Japan; ASX and TSX were not studied.
- **Per-trade edge vs cost.** 0.40¢ a share on a $40 stock (the price is my assumption) is ~**1 bp** of traded value. Frazzini, Israel & Moskowitz (Section 7) measure an average institutional market impact of ~10 bps. So an individual alpha's gross edge is about an order of magnitude below a typical execution cost. This is consistent with Kakushadze's point that alphas are only tradeable as a netted, internally crossed "mega-alpha". It is also why a Medallion-style shop is a combination-plus-execution business rather than a single-signal one.
- **Two lessons for a small book.** First, the Blitz et al. composite shows that combining several cheap, short-horizon signals plus hysteresis turned negative-net signals into >6% net. Second, AMP's −0.53 value-momentum correlation says the cheapest diversification for a momentum-only book is a *different, negatively correlated* signal, not more regions of the same signal.
- **McLean–Pontiff's 58% post-publication decay** is a reasonable prior haircut on any published-anomaly Sharpe used in sizing (Section 6).

### Gaps
- Isichenko's book text was not accessible this session. Its quantitative claims (e.g. typical forecast R², combination limits) are not cited here.
- Kakushadze's alphas are gross of costs. The paper does not report the combined mega-alpha's net Sharpe.
- No public measurement of momentum correlation across ASX, FTSE, TSX and US for the target's sample.

## 4. Short-horizon statistical arbitrage: Avellaneda & Lee, Khandani & Lo, Lehmann / Lo–MacKinlay, and short-term reversal after costs

### Takeaway
Residual mean-reversion stat-arb earned Sharpe ~1.4 net of 10 bp round-trip costs in 1997–2007. It decayed to ~0.9 after 2003 and was negative in 2007. The simplest contrarian strategy's raw daily return fell about 10× from 1995 to 2007 (1.38% to 0.13% a day), so leverage had to rise from ~2:1 to ~9:1 to hold returns steady. That leverage is what made August 2007 lethal.

Standalone short-term reversal does not survive realistic costs:
- −1.28% a month net (Novy-Marx & Velikov, value-weighted, to 2012)
- below −2% a year net per signal (Blitz et al., to 2021)

It survives only with large-cap universes, hysteresis trading rules and signal combination: +30–50 bps a week (US, 1990–2009) or >6% a year for a multi-signal composite.

### Cited Findings
- **Avellaneda & Lee** (Quantitative Finance 10(7), 2010; SSRN 2008):
  - *Method:* model the residuals of stock returns (after PCA or sector-ETF factors) as Ornstein–Uhlenbeck processes, estimated on a **60-day** window. Trade only stocks whose mean-reversion time is under half the window (κ > 252/30 = 8.4).
  - *Rules:* enter at |s-score| > **1.25**; close longs at s = −0.50 and shorts at s = +0.75. Cost assumption: **5 bps per trade (10 bps round trip)**. Leverage **2+2** ($2 long and $2 short per $1 of equity). Universe: 1,417 stocks on 1 Jan 2007.
  - *Results:*
    - PCA strategies: Sharpe **1.44** for 1997–2007, but "only **0.9**" for 2003–2007.
    - ETF strategies: **1.1** for 1997–2007, with similar post-2002 decay.
    - ETF signals in "trading time" (volume-adjusted): **1.51** for 2003–2007.
    - 15-PCA by year: 2003 0.9, 2004 2.2, 2005 1.2, 2006 1.0, **2007 −0.7**.
    - Using factors that explain 75% of variance led "invariably to steady losses" ("noise trading"), because costs dominate the small leftover residual variance.
  - Sources: [SSRN 1153505](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1153505); [full text](https://traders.studentorg.berkeley.edu/papers/Statistical%20arbitrage%20in%20the%20US%20equities%20market.pdf).
- In August 2007 "all the mean-reversion strategies experienced a large, sudden drawdown followed by a recovery in about 10 days". The drawdown at 2+2 leverage was **5%** for PCA and **10%** for ETF strategies, tracking Khandani–Lo "after adjusting for leverage (KL used 4+4)" — [Avellaneda & Lee (2010)](https://traders.studentorg.berkeley.edu/papers/Statistical%20arbitrage%20in%20the%20US%20equities%20market.pdf).
- **The Lehmann (1990) / Lo–MacKinlay (1990) contrarian strategy.** Weights are w_it = −(1/N)(R_{i,t−k} − R_{m,t−k}) with k = 1 day: buy yesterday's losers and sell yesterday's winners in proportion to their deviation from the market — [Khandani & Lo (Sept 2007)](https://web.mit.edu/Alo/www/Papers/august07.pdf).
- **Khandani & Lo**, all CRSP stocks priced $5–$2,000, unleveraged, **ignoring transaction costs**:
  - *Average daily return:*

    | Year | Avg daily return |
    |---|---|
    | 1995 | **1.38%** |
    | 1998 | 0.57% |
    | 2000 | 0.44% |
    | 2003 | 0.21% |
    | 2006 | 0.15% |
    | 2007 (to Aug 31) | **0.13%** |

  - *Annualised Sharpe (0% rf, √250):* 53.87 in 1995 and **2.79** in 2007. In 1995 this required daily rebalancing of 4,781 stocks: "the transactions costs involved ... would have been formidable".
  - *By size:* smallest-decile daily return in 1995 was 3.57%, vs 0.04% for the largest.
  - *Cause of the decline:* "increased competition, changes in market structure, improvements in trading technology ..., the growth in assets devoted to this type of strategy, and the corresponding decline in U.S. equity-market volatility". Also: "most quantitative equity market-neutral strategies have a significant market-making component to their returns", and market-making profits are positively correlated with volatility.
  - Source: [Khandani & Lo (2007)](https://web.mit.edu/Alo/www/Papers/august07.pdf).
- **Leverage needed to keep the 1998 expected return** (Khandani & Lo Table 6): 2.57:1 (1999) → 5.53:1 (2003) → 7.76:1 (2006) → **8.96:1** (2007) — "almost 9:1". At the 2006 leverage of ~8:1, the contrarian strategy would have returned −4.64% (Aug 7), −11.33% (Aug 8) and −11.43% (Aug 9), losing "a little over a quarter of the assets it started with three days before". The unleveraged strategy then rebounded +5.92% on Aug 10 — [Khandani & Lo (2007)](https://web.mit.edu/Alo/www/Papers/august07.pdf).
- **The unwind hypothesis:** losses were "initiated by the rapid unwinding of one or more sizable quantitative equity market-neutral portfolios ... likely the result of a sudden liquidation by a multi-strategy fund or proprietary-trading desk". These then triggered stop-loss and de-leveraging policies elsewhere — [Khandani & Lo (2007)](https://web.mit.edu/Alo/www/Papers/august07.pdf).
- **Short-term reversal after costs, US 1990–2009** (de Groot, Huij & Zhou, J. Banking & Finance 2012). Costs are from the Keim–Madhavan model and Nomura estimates.
  - The standard weekly reversal decile (1,500 largest US stocks, daily rebalanced) earns gross **92.9 bps a week**, but "once transaction costs are taken into account the profitability ... completely diminishes and even becomes negative".
  - Restricting to the 500 largest and holding until a stock leaves the top/bottom 50% "more than halve[s]" turnover and costs. Net becomes **>30 bps a week**, and **>50 bps a week** for the 100 largest.
  - Capacity was documented at USD 150m.
  - Europe: standard strategies earn >80 bps a week gross but are "highly negative" net. The 100 largest with "smart" construction give up to 20 bps a week net.
  - Source: [de Groot, Huij & Zhou](https://www.efmaefm.org/0efmameetings/efma%20annual%20meetings/2011-Braga/papers/0259.pdf).
- **Value-weighted decile strategies, c. 1963–2012** (Novy-Marx & Velikov, RFS 2016), per month:

  | Strategy | Gross | One-sided turnover | Costs | Net |
  |---|---|---|---|---|
  | Short-run reversal | 0.37% | **90.87%** | **1.65%** | **−1.28%** |
  | Momentum | 1.33% | 34.52% | 0.65% | 0.68% |
  | Industry momentum | 0.93% | 90.13% | 1.22% | −0.29% |

  - Most anomalies with one-sided monthly turnover **under 50%** survive costs; "few of the strategies with higher turnover do".
  - Costs average 20–57 bp a month.
  - Source: [Novy-Marx & Velikov, NBER w20721](https://www.nber.org/system/files/working_papers/w20721/w20721.pdf).
- Frazzini, Israel & Moskowitz (2012) found that, of size, value, momentum and short-term reversal, "short-term reversals [are] the most constrained by trading costs" — [AQR working paper page](https://www.aqr.com/Insights/Research/Working-Paper/Trading-Costs-of-Asset-Pricing-Anomalies).
- Blitz et al. (2023): each short-term signal's 6–8% gross alpha becomes below −2% net at ~25 bps. The composite with buy-10/hold-50 gives >6% net (MSCI World, 1985–2021) — [Robeco summary](https://www.robeco.com/en-us/insights/2022/05/beyond-fama-french-alpha-from-short-term-signals).

### Inferences
- **The leverage spiral, in Khandani–Lo's arithmetic.** Leverage needed = 2 × E[R_1998]/E[R_t] = 2 × 0.57/0.13 ≈ **8.8** (their table shows 8.96 from unrounded returns). A ~4× fall in raw edge was offset by ~4× more leverage, so the same tail shock hits 4× harder. This is the clearest published picture of why Medallion-scale returns from short-horizon edges need leverage, and why that leverage is fragile when crowded.
- **How fast short-horizon edges decay.** The contrarian edge took roughly 12 years to fall ~10×, and Avellaneda–Lee's PCA Sharpe fell by about a third within 5 years. Medallion's returns did not decay over 1988–2018, which is consistent with continual replacement of its alphas. RenTec's "rate of change ... critical to the success" comment fits this (inference).
- **Cost per unit of edge, by holding period** (Novy-Marx & Velikov):
  - Momentum (~3-month average hold: 1/0.345 ≈ 2.9 months) loses **49%** of its gross to costs (0.65/1.33).
  - Short-run reversal (~1-month hold: 1/0.909) loses **446%** of its gross (1.65/0.37).
  - At a 1–6-day horizon (Kakushadze), only near-zero-cost, internally crossed execution works.

  So the *holding-period-vs-cost* line is steep. The target's monthly momentum sits where costs consume about half the gross for an institutional value-weighted book.
- **What short-term reversal earned after costs in recent decades:**
  - *Standalone, standard construction:* negative (US to 2012; global to 2021).
  - *Cost-aware large-cap construction:* positive, at 30–50 bps a week in the US for 1990–2009.
  - *As part of a multi-signal composite:* >6% a year net, globally, 1985–2021.
  - No source here isolates post-2010 standalone reversal returns.

### Gaps
- I did not read Lehmann (1990) and Lo & MacKinlay (1990) directly. Their original return figures, and the share of contrarian profit that Lo & MacKinlay attribute to lead-lag (cross-autocorrelation) effects, are not verified here.
- The later published Khandani & Lo (J. Financial Markets, 2011) and the "factors and transactions data" follow-up were not read. Numbers above are from the September 2007 draft.
- No source gives post-2010 net returns for standalone daily stat-arb (Avellaneda–Lee-style) strategies.

## 5. Hidden Markov models and regime switching vs a simple trend filter

### Takeaway
Out of sample, a two-state HMM used as a risk-on/risk-off switch on equity indices modestly improves Sharpe and roughly halves maximum drawdown versus buy-and-hold (S&P 500, 1990–2023: Sharpe 0.48 → 0.54, max drawdown −55% → −29%). But it trades a lot (141–290% turnover a year) and lags turns by about two to four weeks. More persistent "jump models" do better (Sharpe 0.68 on the same test). The literature suggests an HMM is close kin to a well-tuned trend filter (an EMA is optimal under a two-state Markov model), so its edge over a good trend rule is modest.

### Cited Findings
- **Shu, Yu & Mulvey (arXiv v3 Sept 2024)**, out-of-sample "0/1" strategy, 1990–2023, **10 bps one-way costs and a 1-day trading delay**:

  | Index | Metric | Buy & hold | HMM | Jump model |
  |---|---|---|---|---|
  | S&P 500 | Sharpe | 0.48 | 0.54 | **0.68** |
  | S&P 500 | Max drawdown | −55.2% | −28.9% | −26.6% |
  | S&P 500 | Turnover | 0% | **141%** | 44% |
  | S&P 500 | Average exposure | 100% | 72% | 80% |
  | DAX | Sharpe | 0.30 | 0.35 | 0.44 |
  | Nikkei | Sharpe | 0.12 | 0.19 | 0.31 |

  Other details:
  - HMM turnover on DAX and Nikkei was 246% and 290%.
  - Regime shifts per year (1982–2023): the unsmoothed HMM averaged **8.5** (2.0 with 20-day smoothing); jump models ranged from 9.7 (penalty λ = 0) to 0.4 (λ = 150).
  - Detection latency was about half a month. The authors quote Nystrup et al. (2018a): "the median [latency] in detecting regime changes is 25 (calendar) days".
  - Source: [Shu, Yu & Mulvey, arXiv 2402.05272](https://arxiv.org/pdf/2402.05272).
- Same paper: if exposure were cut at random to an average level l, Sharpe would fall by √l. So part of the volatility reduction is mechanical de-risking, not regime skill. Hyperparameters were chosen by time-series cross-validation that "directly optimizes strategy performance" — [Shu, Yu & Mulvey (2024)](https://arxiv.org/pdf/2402.05272).
- Nystrup et al. (2015) built a two-state (high/low-volatility) HMM allocating between stocks and bonds that beat static allocations on a risk-adjusted basis. Nystrup et al. (2018) found that model-predictive control with HMM forecasts beat a static allocation after transaction costs — [Nystrup PhD thesis / search summary](https://backend.orbit.dtu.dk/ws/files/152802375/phd465_Nystrup_P.pdf) (search-summary level).
- Zakamulin & Giner (J. Asset Management, 2024): "a theoretically optimal trading rule based on two-state regime-switching models outperforms the popular 10-month Simple Moving Average and 12-month Momentum rules across a universe of international markets". A related result: when returns follow a two-state Markov process, an exponential moving average is the optimal trend-following rule — [Springer](https://link.springer.com/article/10.1057/s41260-024-00357-0) / [SSRN 4092437](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4092437) (abstract/search-snippet level only; full text not accessible).
- Dueker & Neely (J. Banking & Finance, 2007), on FX: Markov-switching rules' out-of-sample excess returns "modestly exceed those of standard technical rules", and a portfolio of both beats either alone — [ScienceDirect abstract](https://www.sciencedirect.com/science/article/abs/pii/S037842660600166X) (search-snippet level).

### Inferences
- **Lag in practice.** The target trades monthly. A regime detector with ~15–25 days of latency therefore often flips at most one rebalance earlier than a 10-month moving-average or 12-month trend filter. The gap is in timeliness of de-risking within the month, not in a different signal.
- **The HMM's measured edge mostly comes from forcing persistence.** The ranking was jump model > HMM > buy-and-hold, and the jump model's gain came from fewer switches (44% vs 141% turnover). This is the same mechanism as hysteresis bands in Section 4. For a costs-on monthly book, a persistence/hysteresis rule on an existing trend filter probably captures most of what an HMM would add (inference).
- **For Medallion, nothing public links HMMs to its production signals.** The Baum connection (Section 1 gap) is historical. "Consistent with" is the most that can be said: short-horizon regime and volatility conditioning would fit the volatility-scaling of returns seen in Kakushadze and Khandani–Lo.

### Gaps
- Shu et al. did not compare their models directly against a 10-month moving-average or 12-month momentum filter. The only head-to-head evidence found (Zakamulin & Giner; Dueker & Neely) was read at abstract level only.
- No evidence was found on HMM regime filters applied to the *return stream of a cross-sectional momentum strategy* (as opposed to an index). This is the relevant case for momentum-crash protection.

## 6. Kelly and fractional Kelly: growth vs drawdown, and why estimation error argues for heavy fractions

### Takeaway
At c × Kelly, growth is c(2 − c) of the maximum and the chance of *ever* falling to fraction x of capital is x^(2/c − 1):
- Full Kelly: P(ever losing half) = **50%**.
- Half Kelly: **12.5%**, while keeping **75%** of the growth.

Betting 2× the true Kelly gives zero growth. Estimated edges are biased high (data-mining, decay, crowding). So betting full Kelly on an estimate that is twice the truth is exactly the zero-growth case. That is the quantitative case for half-Kelly or less. Medallion's published returns are consistent with roughly 1/8 Kelly on its annual Sharpe, though it is capacity-bound rather than Kelly-bound.

### Cited Findings
- **Continuous-time Kelly formulas** (Thorp, 2006, *Handbook of Asset and Liability Management*, ch. 9):
  - Optimal leverage f* = (m − r)/s².
  - At f = c·f*: growth g = (m − r)²(c − c²/2)/s² + r, and the SD of growth is c(m − r)/s.
  - With r = 0: g(c·f*)/g(f*) = **c(2 − c)**.
  - Source: [Thorp (2006)](https://gwern.net/doc/statistics/decision/2006-thorp.pdf).
- **Thorp's S&P 500 example:** m = 0.11, s = 0.15, r = 0.06 gives f* = **2.22**, g = 0.1155 and growth SD 0.333 — [Thorp (2006)](https://gwern.net/doc/statistics/decision/2006-thorp.pdf).
- **Drawdown probabilities:** Prob(V/V₀ ≤ x for some t) = **x^(2/c − 1)**. "The chance of ever losing half the starting capital is 1/2 for f = f* but only 1/8 for f = f*/2." Half Kelly "has 3/4 the growth rate but much less chance of a big loss". The chance of doubling before halving is 2/3 at full Kelly and 8/9 at half Kelly. "Most people strongly prefer the increased safety and psychological comfort of 'half Kelly'" — [Thorp (2006)](https://gwern.net/doc/statistics/decision/2006-thorp.pdf).
- **Estimation error** (Thorp's Fig. 5 discussion):
  - If the true mean m_t = 0.5 × the estimate m_e, then betting f = f*_e gives **g = 0**, and f = 1.5 f*_e gives g = −0.75 ("we will be ruined").
  - "To the extent m_e is an uncertain estimate of m_t, it is wise to assume m_t < m_e."
  - Estimates of forecast excess return "are more likely to be too high than too low", because of regression to the mean, non-stationarity and data mining, and because "Systems that do work attract capital, which tends to push exceptional m_t down towards average values."
  - "Overbetting is much more harmful than underbetting."
  - Source: [Thorp (2006)](https://gwern.net/doc/statistics/decision/2006-thorp.pdf).
- MacLean, Thorp & Ziemba: Kelly's long-run dominance comes with "negative short to medium term behavior" because log utility has almost zero Arrow-Pratt risk aversion, and "can lead to considerable losses a small percent of the time". Fractional Kelly and drawdown constraints trade expected final wealth for lower risk — [MacLean, Thorp & Ziemba, "Good and Bad Properties of the Kelly Criterion"](https://www.researchgate.net/publication/300558392_Good_and_Bad_Properties_of_the_Kelly_Criterion) (search-summary level).

### Inferences
- **Kelly in Sharpe terms.** Writing μ = m − r: f* = μ/σ² = SR/σ, and maximum excess log-growth = SR²/2. Worked example for a sleeve with true SR 0.5 and σ 15% (continuous-time, constant parameters, no costs):

  | Fraction | Leverage | Excess log-growth a year | P(ever −50%) |
  |---|---|---|---|
  | Full Kelly | 0.5/0.15 = **3.33×** | 0.25/2 = **12.5%** | **50%** |
  | Half Kelly | **1.67×** | 0.75 × 12.5% = **9.4%** | 0.5³ = **12.5%** |
  | Quarter Kelly | 0.83× | 0.4375 × 12.5% = **5.5%** | 0.5⁷ = **0.8%** |

- **Estimation-error case, using McLean–Pontiff's decay.** If a backtested SR of 0.5 is really ~0.25 after publication decay (58%) and multiple-testing haircuts, the "Kelly" leverage computed from the backtest (3.33×) is **2× the true Kelly** (true f* = 0.25/0.15 = 1.67×). That gives **zero expected growth** with large swings, per Thorp. Half-Kelly on the estimate equals full Kelly on the truth. This is the arithmetic behind "half-Kelly or less".
- **Medallion-implied Kelly fraction** (inference, heavy caveats):
  - Annual-data gross SR ≈ 1.96, so full-Kelly excess log-growth would be 1.96²/2 ≈ 1.9 (≈ 2.0 at SR 2).
  - Realised gross log-growth is ln(1.633) ≈ 0.490; minus ~0.04 rf gives ≈ 0.45.
  - Solving c(2 − c) × 2.0 = 0.45 gives **c ≈ 0.12**, roughly **1/8 Kelly**.
  - Caveats: the annual Sharpe comes from 31 points, returns are not lognormal, and the binding constraint is capacity/market impact (Section 7), not a Kelly choice. Leverage of 4.6–20:1 (Section 1) applies to a book whose unlevered volatility is unknown.
- **For the target system:** its `ultra` profile runs 3× leveraged momentum with the breaker OFF (per [CLAUDE.md](/Users/matthewfanning/Trading-Algo/CLAUDE.md)). At σ 15%, 3× is full Kelly only if the true SR is ≥ 3 × 0.15 = **0.45**. If the true SR is 0.2–0.3, 3× is 1.5–2.25× Kelly: zero or negative expected growth. This is arithmetic on assumed inputs, not a measurement.

### Gaps
- Thorp's formulas assume continuous rebalancing, no jumps and constant parameters. Momentum's crash risk (fat left tail) makes the x^(2/c − 1) drawdown probabilities optimistic. I found no closed form with jumps in the sources read.
- I did not read the "optimal betting under parameter uncertainty" literature (e.g. shrinkage of the Kelly fraction by the estimate's standard error) this session.

## 7. Market impact (square-root law) and why capacity caps returns

### Takeaway
Price impact of a metaorder ≈ Y·σ_daily·√(Q/V), with Y of order 1. The exponent is 0.4–0.7 (Almgren ≈ 0.6; AQR's live data ≈ 0.35, modelled as square-root). With square-root costs, net P&L = A·T·(a − k√A) is maximised where impact eats **2/3** of the gross edge. Profit-maximising AUM is about **44%** of break-even AUM, and return on capital falls as √AUM. That is why a Medallion-type book is capped (about $5–10bn) and why short-horizon reversal is the most capacity-constrained style. At retail size, impact is a fraction of a basis point, so fixed commission floors and spreads dominate instead.

### Cited Findings
- **The square-root law** (Tóth, Lempérière, Deremble, de Lataillade, Kockelkoren & Bouchaud, *Physical Review X* 2011):
  - Δ(Q) = Y·σ·√(Q/V), where σ is daily volatility and V daily volume, and "The numerical constant Y is of order unity".
  - The exponent δ ranges 0.4–0.7 across studies: Almgren et al. ≈ 0.6 on 700,000 Citigroup US orders; Madrid ≈ 0.5; London ≈ 0.7.
  - CFM's own ~500,000 futures metaorders (Jun 2007 – Dec 2010) give δ ≈ 0.5 for small-tick and ≈ 0.6 for large-tick contracts, for Q/V from "a few 10⁻⁴ to a few %".
  - Source: [Tóth et al. (2011), arXiv 1105.1694](https://arxiv.org/pdf/1105.1694).
- Bouchaud (Substack): impact "only depends on the total volume traded Q, and barely on execution schedule", provided participation is not too large. Its "apparent universality across a wide variety of markets is surprising" — [Bouchaud, "The Square-Root Law of Market Impact"](https://bouchaud.substack.com/p/the-square-root-law-of-market-impact).
- Almgren, Thum, Hauptmann & Li (2005) rejected a square-root model for *temporary* impact as a function of trade rate in favour of a **3/5 power law**, and used linear permanent impact — [search summary of Almgren et al. 2005](https://www.semanticscholar.org/paper/Direct-Estimation-of-Equity-Market-Impact-Almgren-Thum/00777edc168f26633de9f5b9ff4c4f74bd9790e3) (snippet level; δ ≈ 0.6 also confirmed by Tóth et al. above).
- **Live institutional trading costs** (Frazzini, Israel & Moskowitz, "Trading Costs", 2018 draft):
  - Data: **$1.7 trillion** of live executions, ~700 million fills, 21 developed markets, 19 years.
  - Log-log slope of impact on %daily-volume is **0.35** (R² 95%); they model it as square-root because "what is important is that the price impact function is concave and not linear".
  - Mean market impact **9.97 bps** (median 6.18; value-weighted 15.14). Large caps 8.90 bps vs small caps 18.95 bps.
  - About 85% of impact is permanent: only 1.26 bps of the under-9 bps reverses within 24 hours.
  - Costs are "an order of magnitude smaller than previous studies suggest".
  - Source: [FIM (2018)](https://spinup-000d1a-wp-offload-media.s3.amazonaws.com/faculty/wp-content/uploads/sites/3/2021/08/Trading-Cost.pdf).
- **Capacity of momentum** (FIM, "Trading Costs of Asset Pricing Anomalies", 2012): momentum (UMD) earns a net **4.34% a year** (t = 8.42) at an implied fund size of **$3.7bn**, with break-even fund size **$52bn**. Value and momentum are more scalable than size, and short-term reversal is the most constrained — [AQR working paper page](https://www.aqr.com/Insights/Research/Working-Paper/Trading-Costs-of-Asset-Pricing-Anomalies) (search-summary level).
- Medallion's size was capped around $5.2bn (2004–09) and $10bn (2010–18), and outside investors were removed. Gross returns "did not fall off" as the fund grew — [Cornell (2019)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3504766).
- Target-system cost parameters (local repo), stamp duty on buys only:

  | Region | Commission | Floor | Slippage per side | Stamp duty |
  |---|---|---|---|---|
  | ASX | 8 bps | A$5 | 10 bps | 0 |
  | US | 2 bps | US$1 | 5 bps | 0 |
  | FTSE | 5 bps | £1 | 8 bps | 50 bps |
  | TSX | 3 bps | C$1 | 8 bps | 0 |

  Source: [regions.py](/Users/matthewfanning/Trading-Algo/trading_algo/regions.py).

### Inferences
- **Capacity derivation (square-root costs).** Let a be the gross edge per $ traded, T annual turnover, A AUM, and cost per $ traded = k√A (trade size scales with A).
  - Annual P&L = T·A·(a − k√A).
  - Setting d/dA = T(a − 1.5k√A) = 0 gives √A* = 2a/(3k). At A*, cost per $ = **2a/3**, net edge = **a/3**, and return on capital = T·a/3.
  - Break-even: √A_be = a/k, so A_be = 2.25·A*, i.e. **A*/A_be = (2/3)² = 0.44**.
  - Above A*, total profit *falls* as AUM grows. Below it, return on capital falls by √(A₂/A₁): 4× the AUM means 2× the impact per trade.
  - Capping at A* and returning outside capital is the profit-maximising response. Medallion's cap and its removal of outside investors are consistent with this, though RenTec's actual reasons are not public.
- **Why short horizons cap capacity.** Capacity scales with A_be = (a/k)². A short-horizon book has small a (Kakushadze: ~1 bp a trade) and high T. A higher T lifts profit at the optimum, but the small a makes A* small. A momentum book has a larger a per trade and lower T, hence FIM's $52bn break-even vs reversal being "most constrained".
- **Retail scale: impact is negligible and the commission floor dominates.**
  - Impact: a A$2,500 order (A$100k book × 25% sleeve / 10 names) in a stock trading A$20m a day has Q/V = 1.25×10⁻⁴. With Y = 1 and σ_d = 2%, impact ≈ 0.02 × √1.25e-4 ≈ **2.2 bps**. At Q/V = 10⁻⁵ it is 0.6 bps; at 10⁻⁶, 0.2 bps.
  - Fixed cost: the ASX A$5 floor on a A$2,500 order is **20 bps a side**, ~10× the impact.
  - Q/V below ~10⁻⁴ is outside Tóth et al.'s tested range, so the small-order impact figures are extrapolations; the spread is a floor.
  - Implication: unlike Medallion, the target is *not* capacity-bound. It is fixed-cost-bound. That makes its turnover cost-per-trade a step function of order size, and argues for fewer, larger trades (hysteresis/buy-hold bands; Novy-Marx & Velikov's most effective single mitigation) rather than more frequent rebalancing.

### Gaps
- The FIM (2012) break-even numbers are from the AQR page summary. I did not read the 2012 PDF tables (US vs global, size/value/reversal break-evens) directly.
- No public estimate of Medallion's own impact costs. Cornell notes only that gross returns are after trading costs.
- No square-root law calibration specific to the ASX, LSE or TSX was found. Y and δ for those venues are assumed from the cross-market universality claims.

## 8. Overfitting and data-snooping defences (brief, since the target already uses them)

### Takeaway
Serious shops discount backtests for the number of trials and for non-normality (Deflated Sharpe, PBO). They demand t > 3 rather than t > 2 for new factors. They also expect real-world decay: 26% out-of-sample and 58% post-publication on average. Practitioner rules of thumb ("cut the Fundamental-Law IR in half"; "IR above 1.5 is rare") point the same way.

### Cited Findings
- Harvey, Liu & Zhu (RFS 29(1), Jan 2016): given extensive data mining, "a new factor needs to clear a much higher hurdle, with a t-statistic greater than **3.0**". This corresponds to p ≈ 0.27% — [HLZ (2016)](https://people.duke.edu/~charvey/Research/Published_Papers/P118_and_the_cross.PDF) (via search summary).
- Bailey & López de Prado (JPM 40(5), 2014): the Deflated Sharpe Ratio "corrects for two leading sources of performance inflation: Selection bias under multiple testing and non-Normally distributed returns" — [SSRN 2460551](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551).
- McLean & Pontiff (JF 2016): returns are 26% lower out-of-sample and 58% lower post-publication across 97 predictors — [McLean & Pontiff (2016)](https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.12365).
- Avellaneda & Lee kept "estimation and trading rules ... simple to avoid data-mining": one 60-day window and uniform thresholds across all stocks, with thresholds chosen on 2000–2004 data — [Avellaneda & Lee (2010)](https://traders.studentorg.berkeley.edu/papers/Statistical%20arbitrage%20in%20the%20US%20equities%20market.pdf).
- Thorp: systems "may be partly or entirely based on data mining so m_t may be substantially less than m_e" — [Thorp (2006)](https://gwern.net/doc/statistics/decision/2006-thorp.pdf).

### Inferences
- **Practical combined haircut.** Applying McLean–Pontiff's 58% to a published-anomaly Sharpe, plus the Qian–Hua σ_IC ceiling (Section 2), gives a realistic prior for a single published signal well below its backtest. That prior is what should feed the Kelly fraction (Section 6), not the backtest Sharpe.
- **Why Medallion's record survives these tests.** 31 years without a losing year at Sharpe ~2 is not a backtest-selection artefact; the Deflated Sharpe concern is about choosing among *trials*. The open question is attribution, not overfitting: Cornell found "no adequate rational market explanation".

### Gaps
- Probability of Backtest Overfitting (Bailey, Borwein, López de Prado & Zhu) was not re-sourced this session. The target system already implements it per [CLAUDE.md](/Users/matthewfanning/Trading-Algo/CLAUDE.md).
- No published estimate of how many alphas or trials a Medallion-type shop tests per production signal. Kakushadze says only that alpha counts run to "hundreds of thousands and even millions".
