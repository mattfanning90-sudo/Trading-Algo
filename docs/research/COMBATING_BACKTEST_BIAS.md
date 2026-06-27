# Combating bias in quantitative trading backtests

A synthesized, cited research note on the best ways to detect and defeat the
biases that make a backtest lie — focused on this project's case (a long-only,
multi-region monthly cross-sectional momentum book vs an index-blend benchmark).

> Method & honesty note: this was produced by a fan-out web-research pass (five
> angles), with each claim confidence-rated. Several **primary PDFs returned HTTP
> 403** to the fetcher, so some exact figures were corroborated via secondary
> summaries and are marked **[verify]** — trust the direction, double-check the
> precise number against the source before quoting in print.

---

## TL;DR — the best way to combat bias

There is no single trick; it's a *process*. In rough priority order:

1. **Use survivorship-free, point-in-time data** (delisted names + as-of index
   membership + as-reported fundamentals). This is the largest, most mechanical
   bias and the only one you fix with data rather than discipline.
2. **Enforce no-lookahead**: decide on data ≤ t, execute at t+1; never normalize
   or scale using the full sample.
3. **Model costs realistically and always-on** (commission, half-spread,
   slippage, market impact, borrow, stamp duty). High-turnover edges die here.
4. **Count your trials and deflate for them.** The more configs you test, the
   higher the *chance-best* Sharpe — correct with the **Deflated Sharpe Ratio**
   and estimate the **Probability of Backtest Overfitting (PBO)**.
5. **Validate out-of-sample the right way**: a single locked holdout, walk-forward,
   and **purged + embargoed** cross-validation (never naive k-fold on time series).
   Prefer parameter **plateaus, not peaks**.
6. **Demand an economic rationale and out-of-*market* confirmation** (other
   countries/asset classes), and **expect live < backtest** — haircut the Sharpe.

The uncomfortable corollary for *this* project's current goal ("beat the index
by 2%"): searching configurations to clear +2% on a **survivorship-biased,
in-sample** backtest is, by these standards, *manufacturing* an edge, not
discovering one. The result must be deflated, confirmed point-in-time and
out-of-sample, and expected to shrink live (see §7).

---

## 1. Survivorship & universe bias — fix it with data

| Claim | Conf. | Source |
|---|---|---|
| Survivorship bias = backtesting only on securities that still exist; it inflates returns because dropouts are disproportionately losers. | High | Bogleheads wiki |
| US **mutual-fund** survivorship bias ≈ **0.9–1.4%/yr** (Malkiel 1.4%; Elton-Gruber-Blake ~0.9%). | High | Malkiel 1995 *JF*; EGB 1996 |
| **Hedge-fund** survivorship bias is far larger, ≈ **2–4.4%/yr**. | High (range); Med (exact 4.4%) **[verify]** | Malkiel & Saha 2005 *FAJ* |
| Backtesting on *today's* equity index vs point-in-time constituents inflates returns ≈ **1–4%/yr**, worse for high-turnover indices. | Med | QuantifiedStrategies; SSRN WP |
| Omitting **delisting returns** biases upward; the missing returns are sharply negative. Correct with ≈ **−30%** (NYSE/AMEX) / **−55%** (Nasdaq) replacement returns. | High (mechanism, −55%); Med (−30%) **[verify]** | Shumway 1997 *JF*; Shumway & Warther 1999 *JF* |
| Delisting bias is big enough to *flip conclusions* — corrected, the Nasdaq size effect disappears. | High | Shumway & Warther 1999 |
| **Backfill / instant-history bias**: funds enter a DB after a good run and pre-history is backfilled; fix with the "listing-date method" (drop pre-listing returns). | High | Jorion & Schwarz 2019 *RFS*; Fung & Hsieh 2002 |

**Data sources practitioners use:** CRSP (delisting returns; academic standard);
**Norgate Data** (retail point-in-time index constituents + delisted stocks);
**S&P Compustat Point-in-Time** (as-reported fundamentals, active+inactive, from
1987); Bloomberg/FactSet PIT.

> This project: default universes are *today's* liquid names → survivorship-biased
> (an **upper bound**). `constituents.py` already implements the PIT mechanism;
> it needs real membership files (e.g. Norgate for ASX) to be trustworthy.

## 2. Look-ahead bias — fix it with discipline

| Claim | Conf. | Source |
|---|---|---|
| Look-ahead bias = using, at time t, information not actually available until after t — the most easily-introduced backtest error. | High | AnalystPrep/CFA; M. Harris |
| Classic source: **restated/revised data** (earnings revisions, macro revisions). Backtest on the *as-of* value, not the final revision. | High | hedgefundalpha |
| Remedy: **point-in-time data** + correct **signal/execution timing** — signal from data ≤ t, execute next bar (t+1), never the same close the signal used. | High (PIT); Med (timing framing) | EagleAlpha; Forex EA Store |
| Full-sample normalization / feature scaling and naive k-fold **leak** the future; purge/embargo to prevent it (see §5). | High | López de Prado 2018 |
| Iterative tweaking against the same data is itself leakage/selection bias (quantified by PBO/DSR, see §4). | High | López de Prado 2018 |

> This project: the no-lookahead invariant (signal t, execute t+1; causal
> rolling features) is enforced in `signals`/`strategy`/`backtest` and covered by
> tests — this bias is well-controlled.

## 3. Costs & slippage — the edge killer

| Claim | Conf. | Source |
|---|---|---|
| Ignoring/underestimating costs significantly inflates simulated profit and invalidates the backtest. | High | BSIC; insightbig |
| Convention: charge **half the bid-ask spread per side**; slippage ~0.1% (liquid) to >1% (thin). | Med | hyper-quant; luxalgo |
| Market impact ≈ **square-root law**: impact ≈ Y·σ·√(Q/V), Y ~ O(1), broadly universal. | High | Tóth/Bouchaud et al. (arXiv 2411.13965) |
| **Almgren–Chriss**: permanent + temporary impact; optimal execution trade-off (impact vs timing risk). | High | Almgren & Chriss 2000 |
| **Short borrow** fees accrue daily; GC ~0.25–1%/yr, hard-to-borrow 5–100%+; static assumptions understate short cost. | High | IBKR; S3 Partners |
| **UK SDRT = 0.5% on purchases of UK shares only** (asymmetric, buys only). | High | GOV.UK; LSE |
| Counter-intuitively, modest **per-trade commissions** often dominate the real drag for high-turnover strategies (they hit every trade). | Med | BSIC; ResearchGate 2024 |
| Conservative posture: **over-estimate costs / "double your best guess"** and confirm the edge survives. | Med | hyper-quant; enlightenedstocktrading |

> This project: costs are always-on (commission floor + slippage + UK stamp duty
> on FTSE buys), which is exactly why the real backtest underperformed — the
> momentum edge is thin and turnover (24–39%/mo) eats it. The fix is **lower
> turnover** (quarterly rebalance, no-trade bands), consistent with §6 momentum
> findings.

## 4. Overfitting / multiple testing — the statistics

| Claim | Conf. | Source |
|---|---|---|
| **Deflated Sharpe Ratio (DSR)** corrects an observed Sharpe for #trials, non-normality (skew/kurtosis) and track length at once. | High | Bailey & López de Prado 2014 *JPM* |
| Building block **PSR** = Z[(SR−SR\*)·√(T−1) / √(1 − γ₃·SR + ((γ₄−1)/4)·SR²)]. | High | Bailey & LdP; Wikipedia DSR |
| DSR sets SR\* = **E[max SR] across N trials** (an extreme-value/Gumbel term that grows with N): E[maxSR] ≈ σ_SR·[(1−γ)Z⁻¹(1−1/N) + γ·Z⁻¹(1−1/(Ne))], γ≈0.5772. Common cutoff DSR>0.95. | High (formula); Med (0.95 cutoff) | Bailey & LdP 2014 |
| Non-normality changes the **significance** of a Sharpe, not its point estimate (fat/negative-skew tails widen the SR distribution). | High | Bailey & LdP 2014 |
| **"Pseudo-mathematics"**: high in-sample Sharpe is *easy to manufacture* by trying enough configs — overfitting is the rule, not the exception. | High | Bailey, Borwein, LdP, Zhu 2014 *AMS Notices* |
| **Minimum Backtest Length**: with **5 years** of data, **>~45 independent trials** ≈ guarantees an IS Sharpe of 1 whose true OOS Sharpe is 0. | Med **[verify]** | Bailey et al. 2014 |
| **PBO via CSCV**: enumerate C(S,S/2) IS/OOS splits, find the IS-best each split, record its OOS rank; **PBO = fraction of splits where the IS-best lands below the OOS median**. PBO≈0.5 ⇒ selection is pure overfitting. | High | Bailey, Borwein, LdP, Zhu 2015 *JCF* |
| A new factor needs **|t| > ~3.0, not 2.0** (the "factor zoo" of ~316 tested factors ⇒ ~16 false hits at 5%). | High (t>3); Med (exact cutoffs) | Harvey, Liu & Zhu 2016 *RFS* |
| Sharpe **haircut is non-linear**: marginal SRs are penalized heavily (toward 0), top SRs only modestly — the flat "halve it" rule is wrong. | High | Harvey & Liu 2015 "Backtesting" |
| **White's Reality Check** (2000) and **Hansen's SPA** (2005) test whether the *best* of a whole rule-universe beats a benchmark after data-snooping (SPA is more powerful). | High | White 2000 *Econometrica*; Hansen 2005 *JBES* |

## 5. Validation done right

| Claim | Conf. | Source |
|---|---|---|
| Naive **k-fold CV fails** on financial series (non-IID, serial correlation, non-stationarity); shuffling injects look-ahead. | High | López de Prado 2018 |
| Leakage comes from **overlapping labels** spanning train & test windows. | High | López de Prado 2018 |
| **Purging** removes train obs whose label window overlaps the test window. | High | López de Prado 2018 |
| **Embargoing** also drops train obs just *after* each test fold (serial-correlation leak); embargo is a small % of bars (~1%) **[verify exact %]**. | Med | López de Prado 2018 |
| **CPCV** generates *many* OOS paths (a distribution of Sharpes), not one. | High | López de Prado 2018 |
| **Walk-forward** reduces but doesn't remove overfitting and is still a *single path*; repeated tuning re-introduces overfitting. | High | multiple; ScienceDirect 2024 |
| Keep a **single, locked, never-reused holdout**; expect IS→OOS Sharpe to degrade. | High (principle) | Bailey et al. 2015 |
| Prefer parameter **plateaus over peaks** (e.g. keep configs within ~90% of best, pick from the stable region). | High (principle); Med (90% rule) | Robot Wealth; Harbourfront |

> This project: `sweep.py` already embodies "flat surface, not a peak." Missing:
> purged/embargoed CV, a locked holdout, DSR/PBO reporting.

## 6. Process safeguards & momentum-specific evidence

| Claim | Conf. | Source |
|---|---|---|
| **Log the trial count**; deflate the Sharpe for it. | High | Bailey et al. 2014 |
| OOS across **time, geography and asset class** + an economic rationale is the strongest defense against data mining. | High (claim is advocacy) | Asness/AQR |
| Caveat: requiring a *published economic theory* may **not** actually improve OOS robustness. | Med (contested) | arXiv 2212.10317 |
| Factor decay: predictors are **~26% weaker OOS (pre-pub)** and **~58% weaker post-publication**; worst for the best-looking, hardest-to-trade signals. | High | McLean & Pontiff 2016 *JF* |
| **~65% of anomalies fail** to replicate with NYSE breakpoints/value-weighting; ~82% under |t|≥2.78. | High | Hou, Xue & Zhang 2020 *RFS* |
| **Momentum (12-1)** earned ≈ **1%/month** (winners−losers). | High (1%/mo); Med (1.31% pt est.) **[verify]** | Jegadeesh & Titman 1993 |
| **Value & momentum everywhere** across 8 markets/asset classes; the two are negatively correlated (~−0.5/−0.6 **[verify]**) so combining lifts Sharpe. | High (claim); Med (corr) | Asness, Moskowitz & Pedersen 2013 |
| **Momentum crashes** in post-bear rebounds (Mar–May 2009: losers +163% vs winners +8% **[verify]**); a **vol-/forecast-scaled "dynamic" momentum ≈ doubles** alpha & Sharpe. | High (crashes & dynamic-doubling); figures **[verify]** | Daniel & Moskowitz 2016 *JFE* |
| Whether momentum **survives costs** is contested: Lesmond-Schill-Zhou (2004) say costs "eliminate the profits"; Korajczyk-Sadka (2004) find break-even fund sizes of ~$5B+ for well-built long-only momentum — costs bite, but design (low turnover, liquid names) matters. | High (contested); Med ($5B) **[verify]** | LSZ 2004; KS 2004 |
| Practitioner rule: **expect live < backtest**; haircut the Sharpe (non-linearly) and plan on the OOS number. | High | Harvey & Liu 2015; McLean & Pontiff 2016 |

---

## 7. How this maps to *this* project

**Already doing well** (keep):
- No-lookahead (t/t+1, causal features, tested). §2
- Costs always-on incl. UK stamp duty. §3
- Regime de-risking (mitigates momentum crashes). §6
- Vol targeting (aligned with "dynamic momentum doubles Sharpe"). §6
- Sweep for plateaus, not peaks. §5
- Out-of-market structure (FTSE/US/ASX) = a built-in robustness argument. §6
- PIT *mechanism* exists. §1

**Highest-value additions to combat bias** (ranked):
1. **Real point-in-time constituents** (Norgate/CRSP-style) so the backtest stops
   being an upper bound. §1
2. **Deflated Sharpe Ratio + PBO** in the reporting (we log trials in `tune.py`;
   feed N into DSR; compute PBO via CSCV). §4
3. **Lower turnover** (quarterly rebalance / no-trade bands) — directly targets
   the cost drag that sank the real backtest. §3, §6
4. **A locked holdout + purged/embargoed CV / walk-forward**, not just one split. §5
5. **Haircut expectations**: whatever active return we "find", expect live to be
   materially lower; require |t|>3-style conviction and an economic story. §4, §6

**Direct implication for the "beat the index by 2%" goal:** clearing +2% on a
single, survivorship-biased, in-sample search is exactly the failure mode §4
warns about. The honest version of the goal is: a config that beats the blend by
~2% **after** (a) point-in-time data, (b) a deflated/haircut Sharpe that stays
positive, (c) confirmation on a locked OOS window, and (d) low enough turnover
that costs don't reclaim it. Anything less is a number, not an edge.

---

## Sources (primary)

- Bailey & López de Prado (2014), *The Deflated Sharpe Ratio*, JPM — SSRN 2460551
- Bailey, Borwein, López de Prado & Zhu (2014), *Pseudo-Mathematics and Financial Charlatanism*, Notices of the AMS 61(5) — ams.org/notices/201405/rnoti-p458.pdf
- Bailey, Borwein, López de Prado & Zhu (2015), *The Probability of Backtest Overfitting*, J. Computational Finance — SSRN 2326253
- Harvey, Liu & Zhu (2016), *…and the Cross-Section of Expected Returns*, RFS — SSRN 2249314
- Harvey & Liu (2015), *Backtesting*, JPM — SSRN 2345489
- White (2000), *A Reality Check for Data Snooping*, Econometrica 68(5)
- Hansen (2005), *A Test for Superior Predictive Ability*, JBES 23(4)
- López de Prado (2018), *Advances in Financial Machine Learning*, Wiley (Ch. 7 — CV)
- Shumway (1997) & Shumway & Warther (1999), delisting bias, JF
- Malkiel (1995) JF; Malkiel & Saha (2005) FAJ; Elton, Gruber & Blake (1996) — survivorship
- Jorion & Schwarz (2019), backfill bias, RFS
- McLean & Pontiff (2016), *Does Academic Research Destroy Stock Return Predictability?*, JF — SSRN 2156623
- Hou, Xue & Zhang (2020), *Replicating Anomalies*, RFS — NBER w23394
- Jegadeesh & Titman (1993), momentum, JF
- Asness, Moskowitz & Pedersen (2013), *Value and Momentum Everywhere*, JF
- Daniel & Moskowitz (2016), *Momentum Crashes*, JFE — NBER w20439
- Korajczyk & Sadka (2004) JF; Lesmond, Schill & Zhou (2004) JFE — momentum & costs
- Almgren & Chriss (2000), *Optimal Execution of Portfolio Transactions*
- Tóth et al., square-root impact law — arXiv 2411.13965

*Confidence and [verify] flags above reflect that several primary PDFs were
inaccessible to the automated fetcher; figures so marked should be confirmed
against the source.*
