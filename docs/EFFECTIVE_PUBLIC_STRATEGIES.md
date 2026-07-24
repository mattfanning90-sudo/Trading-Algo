# Truly Effective Public Trading Strategies — an honest survey (and what maps onto this system)

This is a survey of the trading strategies that are *publicly documented* and have a
credible claim to working, judged the way this repo judges everything: multi-decade,
multi-market, out-of-sample, and **net of costs**. It separates the handful of real,
surviving edges from the much larger pile of pretty backtests, and then maps the
survivors onto this system. The short version of the punchline is at the bottom of
§12, and you will not like how boring it is.

> **Honesty note.** There is no public algorithm that reliably prints money. Every
> edge below is a *risk premium* or a *behavioural bias* that (a) decays after it is
> published, (b) shrinks after realistic trading costs, and (c) has a capacity
> ceiling. "Effective" here does **not** mean "high backtest Sharpe" — it means the
> edge has survived out-of-sample, across many markets, for decades, after costs.
> Anything that only clears the first of those bars is a data-mining artefact until
> proven otherwise. This document is deliberately anti-hype: it is easier to lose
> money believing a dead edge is alive than to miss a live one.

The companion doc [`docs/research/COMBATING_BACKTEST_BIAS.md`](research/COMBATING_BACKTEST_BIAS.md)
is the *methodology*; this doc is the *map of edges* that methodology has been used
to vet. Read them together.

---

## 0. The only three questions that matter — decay, costs, capacity

Before any strategy earns a paragraph, it has to pass three filters. Most of the
public "factor zoo" dies at the first one.

**Decay (does it survive publication?).** McLean & Pontiff tracked 97 published
cross-sectional predictors and found returns **~26% lower out-of-sample and ~58%
lower post-publication** — i.e. roughly a third of the edge was pure data-mining and
another third was arbitraged away once the paper was out
([McLean & Pontiff 2016, *J. Finance*](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2156623)).
Hou, Xue & Zhang re-ran **452 anomalies** with microcaps controlled (NYSE breakpoints,
value-weighted) and **65% failed to clear even |t| > 1.96**; at the multiple-testing
hurdle |t| > 2.78, **82% failed**, and 96% of the "trading frictions" category failed
([Hou, Xue & Zhang 2020, *RFS* / NBER w23394](https://www.nber.org/papers/w23394)).
The correct prior for a newly discovered edge is *dead*.

**Multiple testing (is the Sharpe even real?).** If you try N configurations, the best
one looks good by luck. Harvey, Liu & Zhu argue a *new* factor needs roughly **t > 3.0**,
not the textbook 1.96, precisely because the literature has run thousands of tests.
This repo already enforces the operational version of that: the **Deflated Sharpe
Ratio** deflates an observed Sharpe by how many trials were run, and **PBO**
(Probability of Backtest Overfitting) measures how often the in-sample winner lands
below the out-of-sample median
([Bailey & López de Prado 2014, DSR](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551);
[Bailey et al. 2017, PBO](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf)).
See [`trading_algo/validation.py`](../trading_algo/validation.py) — `deflated_sharpe_ratio`,
`pbo`, `overfitting_gate`.

**Costs & capacity (does it survive *your* trading?).** Novy-Marx & Velikov showed
anomalies with **one-sided monthly turnover under ~50%** mostly keep a significant
*net* spread; higher-turnover ones mostly do not, and the single most effective cheap
fix is a **buy/hold band** (don't trade names you'd only marginally rebalance)
([Novy-Marx & Velikov 2016, *RFS* / NBER w20721](https://www.nber.org/papers/w20721)).
The counter-nuance, honestly stated: using ~$1T of live institutional fills, Frazzini,
Israel & Moskowitz found **real-world costs are less than a tenth of academic
estimates**, so value and momentum are far more scalable than paper studies claim —
but **short-term reversal is the least scalable** of all
([Frazzini, Israel & Moskowitz 2018](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2294498)).
Net: low-turnover style premia survive costs; high-turnover mean-reversion is where
costs kill you, and it is exactly where you're competing with people who trade for
less than you do.

**The filter, stated once:** an edge worth funding is *low-to-moderate turnover*,
*multi-market*, *decades-long*, has an *economic story* (a risk premium or a
persistent behavioural bias, not just a correlation), and **still clears DSR/PBO after
costs**. Hold every candidate below to that.

---

## 1. Cross-sectional momentum — the strongest survivor, with a crash tail

**Evidence.** Buy the recent winners, sell/underweight the recent losers, ranked
cross-sectionally over ~12 months skipping the most recent month. It is the most
replicated anomaly in finance: Jegadeesh & Titman (1993) in US stocks, and
**"Value and Momentum Everywhere"** shows it in individual stocks *and* country
indices, bonds, commodities and currencies, with a common global factor structure —
and crucially, **value and momentum are negatively correlated**, so a book that runs
both is more than the sum of its parts
([Asness, Moskowitz & Pedersen 2013, *J. Finance*](https://pages.stern.nyu.edu/~lpederse/papers/ValMomEverywhere.pdf)).
It survives the cost filter: momentum is one of the two most scalable anomalies in the
Frazzini-Israel-Moskowitz live-cost study.

**The catch.** Momentum has a fat left tail. Returns are **negatively skewed and
crash** — infrequent, violent losses that cluster in *panic states* (after market
declines, high volatility) and hit during sharp rebounds, because the short/loser leg
behaves like a written option. The good news: the crashes are **partly forecastable**,
and a *dynamic* momentum strategy that scales exposure by momentum's own conditional
volatility roughly **doubles the Sharpe** of static momentum
([Daniel & Moskowitz 2016, *JFE* / NBER w20439](https://www.nber.org/papers/w20439)).

**Maps to.** This is the core of the equity engine — `momentum_score` (12-1) in
[`trading_algo/signals.py`](../trading_algo/signals.py), routed through the one
`compute_targets` in [`trading_algo/strategy.py`](../trading_algo/strategy.py). The
skip-month, the trend/regime filters and the drawdown breaker are all partial crash
mitigants. The one well-evidenced upgrade not yet built is the *volatility-responsive*
scaler (§12).

## 2. Time-series (absolute) momentum / trend-following — the diversifier and crisis alpha

**Evidence.** Distinct from the cross-sectional version: go long a market if *its own*
past 12-month return is positive, short if negative, sized to a vol target
(Moskowitz, Ooi & Pedersen 2012). The out-of-sample record is extraordinary in breadth:
across **67 markets in 4 asset classes back to 1880**, time-series momentum delivered a
**positive average return in every decade**, uncorrelated with stocks/bonds, and
notably it made money in **8 of the 10 worst drawdowns for a 60/40 portfolio** — the
"crisis alpha" property
([Hurst, Ooi & Pedersen 2017, *JPM*](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2993026)).

**The catch.** It has *decayed* since ~2010 — more managers, lower vol, some quiet
years — and it is a *convexity/diversification* trade, not a standalone return engine.
Size it for the crisis-alpha and low-correlation benefit, not for a headline Sharpe,
and expect long flat stretches.

**Maps to.** Present only in *pieces*: as trend *filters* in equities (`stock_trend_ok`,
`index_risk_on`) and as a `TrendAgent`/`BreakoutAgent` in the FX book
([`trading_algo/forex/agents.py`](../trading_algo/forex/agents.py)). A diversified,
cross-asset TS-momentum *sleeve* (equity/bond/commodity/FX via futures or ETFs) is the
single best-evidenced **unbuilt** diversifier for this portfolio (§12).

## 3. Carry — you get paid to hold, and occasionally get run over

**Evidence.** Hold the high-yielding asset, fund it with the low-yielding one; the
yield differential itself is a return you earn even if prices don't move. "Carry"
generalises the FX carry trade to **global equities, bonds, commodities, credit and
options** and predicts returns both cross-sectionally and in time series
([Koijen, Moskowitz, Pedersen & Vrugt 2018, *JFE*](https://pages.stern.nyu.edu/~lpederse/papers/Carry.pdf)).

**The catch.** FX carry in particular has **strong negative skew** — "picking up
pennies in front of a steamroller." It loads on **global FX volatility**: carry earns
a premium precisely because it loses badly in volatility spikes / liquidity crises,
which is *why* the premium exists
([Menkhoff, Sarno, Schmeling & Schrimpf 2012, *J. Finance*](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1342968)).
It is a compensation-for-crash-risk trade; treat the Sharpe as conditional on not
being in the crash.

**Maps to.** **Already live** in the FX subsystem as a first-class `CarryAgent`
([`trading_algo/forex/agents.py`](../trading_algo/forex/agents.py)) plus per-pair
`swap_long_pips`/`swap_short_pips` financing in `pairs.py`, and the FX vol-targeting
risk layer is exactly the right container for a negatively-skewed premium. The unbuilt
extension is *bond/commodity* carry, which needs futures data.

## 4. Value — real, decayed, and currently contested

**Evidence.** Buy cheap (high book-to-price, earnings yield, etc.), sell expensive.
Half of the "everywhere" result above; the Fama-French value factor (HML) is one of
the field's foundational premia and shows up across countries and asset classes.

**The catch.** HML **underperformed growth for over a decade**, with a ~55% drawdown
into mid-2020, prompting the "value is dead" debate. The measured, honest read: it is
*not* obviously dead, but the classic **book-value definition misses intangibles**
(software, brand, R&D), and part of value's pain was value stocks getting *relatively
cheaper*, not the premium disappearing
([Arnott, Harvey, Kalesnik & Linnainmaa 2020, "Reports of Value's Death…"](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3488748)).
It is real but the *definition* matters and it is the premium most exposed to the
crowding/"smart-beta-gone-wrong" failure mode
([Arnott et al., "How Can Smart Beta Go Horribly Wrong?"](https://www.researchgate.net/publication/320220749_How_Can_'Smart_Beta'_Go_Horribly_Wrong)).

**Maps to.** A price-only value proxy is already in the equity book — `value_score`
(long-term reversal: cumulative return over a ~3y window ending 1y ago, negated) in
[`trading_algo/signals.py`](../trading_algo/signals.py), blendable with momentum via
`use_value`/`value_weight`. A *fundamental* value/quality signal (§5) is the natural
upgrade but needs a fundamentals feed.

## 5. Quality / profitability — the boring survivor

**Evidence.** Profitable, well-run firms out-earn unprofitable ones. Novy-Marx showed
**gross profits / assets** has "roughly the same power as book-to-market" in the
cross-section, holds in 19 developed markets, and — critically — **stabilises value**:
controlling for profitability dramatically improves value strategies, especially in
large, liquid names
([Novy-Marx 2013, *JFE* / NBER w15940](https://www.nber.org/papers/w15940)).

**The catch.** Needs *fundamental* data (income statement / balance sheet), not just
prices. It is low-turnover (fundamentals change slowly), which is a feature for costs,
but it is the reason it isn't already in a price-only pipeline.

**Maps to.** **Not yet built** for equities. This is the strongest evidence-to-effort
*add* for the equity sleeves (§12), because it is low-turnover (survives costs),
diversifying, and specifically complements the momentum book you already run.

## 6. Low-volatility / betting-against-beta — real, but crowded and leverage-dependent

**Evidence.** Low-beta / low-volatility stocks earn higher *risk-adjusted* returns than
high-beta ones — the opposite of what CAPM predicts. The BAB factor (long leveraged
low-beta, short high-beta) had a US **Sharpe of ~0.78 from 1926-2012** and shows up in
20 international equity markets and across asset classes; the story is that
**leverage-constrained investors overpay for high-beta** because they can't lever safe
assets ([Frazzini & Pedersen 2014, *JFE* / NBER w16601](https://www.nber.org/papers/w16601)).

**The catch.** The pure factor **requires leverage and shorting** to isolate; the
long-only "min-vol" version is a diluted, and by now **crowded**, expression. It is a
real premium wearing a "boring low-risk" costume that has attracted a lot of capital.

**Maps to.** **Not built** and arguably not worth building as a standalone here: the
long-only version is diluted and crowded, and the vol-targeting/inverse-vol machinery
you already have captures some of the same "prefer lower-vol exposure" instinct. Low
priority.

## 7. Short-horizon mean-reversion & statistical arbitrage — mostly eaten by HFT

**Evidence.** Prices over-shoot and snap back over days to weeks; pairs/relative-value
arbitrage (long the cheap of a co-moving pair, short the rich) is the classic
expression. Gatev, Goetzmann & Rouwenhorst reported **~12%/yr from top pairs over
1962-1997** ([NBER w7032](https://www.nber.org/papers/w7032)).

**The catch.** This is the graveyard. Profitability **deteriorated sharply after the
mid-2000s**; the simple distance-based rule "no longer delivers robust returns," and
what survives is adaptive, execution-sensitive, and regime-dependent. It is the
**most cost-sensitive and least scalable** category (short-term reversal was the worst
in the Frazzini-Israel-Moskowitz live-cost study), and you are directly competing with
lower-latency, lower-cost HFT market-makers. See [`docs/HFT_REALITY.md`](HFT_REALITY.md)
for why this system structurally cannot win the latency version.

**Maps to.** Present in the FX subsystem the *right* way — as **research probes judged
by DSR/PBO**, not as a funded book: `_ou_meanrev`, `_statarb`, and the `MeanReversionAgent`
in [`trading_algo/forex/research.py`](../trading_algo/forex/research.py) /
`agents.py`. The honest expected verdict, which `research.format_report` states
outright, is that no candidate clears the deflated bar. Keep it as a probe; do not fund
it as a return engine.

## 8. The variance risk premium (short vol) — a genuine premium you can blow up on

**Evidence.** Option-implied volatility is, on average, higher than the volatility that
subsequently realises; selling that insurance (short variance / covered strategies)
harvests the gap. It is **pervasive across equities, bonds, commodities and currencies**,
with reported standalone Sharpes around 0.5-1.0 for a diversified composite. It is one
of the more genuinely additive premia because its return source (investors overpaying
for downside insurance) is structurally different from the trend/value/carry complex.

**The catch.** The payoff is short a deep option: small steady gains, then a
**crisis loss where variance runs 50-100× normal**. It is the definitional
"picking up pennies" trade, and un-hedged short vol has repeatedly wiped out funds. It
also needs options infrastructure and active margin management.

**Maps to.** **Not built**, and correctly so for now: it needs an options venue,
tail-hedge plumbing, and margin controls this system doesn't have. It belongs in
"someday, with a proper risk harness," not the near-term roadmap (§12).

## 9. Machine learning in the cross-section — modest, real, and cost-fragile

**Evidence.** Given the same predictors, flexible models (gradient-boosted trees,
neural nets) beat linear regressions out-of-sample by capturing nonlinear
interactions, "in some cases doubling" the economic performance of linear factor
strategies — and, tellingly, **all methods agree the dominant signals are variations of
momentum, liquidity and volatility**
([Gu, Kelly & Xiu 2020, *RFS* / NBER w25398](https://www.nber.org/papers/w25398)).
So ML is a better *combiner* of known edges, not a discoverer of new physics.

**The catch.** The measured out-of-sample R² is *tiny* (that's normal and fine); the
gains are fragile to **turnover and transaction costs**, and ML is the easiest place in
all of finance to overfit. It only counts if it is validated with purged walk-forward +
deflation, exactly as this repo does.

**Maps to.** **Already built, and built correctly.** The FX subsystem has a pure-NumPy
MLP trained with a **differentiable Sharpe loss** (output a position, not a forecast),
seed-ensembled, meta-labelled, validated with **purged/embargoed walk-forward + DSR/PBO**
— [`trading_algo/forex/nn.py`](../trading_algo/forex/nn.py),
[`ml_agent.py`](../trading_algo/forex/ml_agent.py),
[`forex/walkforward.py`](../trading_algo/forex/walkforward.py). This is the
state-of-practice honest way to use ML here; the design rationale is
[`docs/FX_DEEP_RESEARCH.md`](FX_DEEP_RESEARCH.md).

## 10. The meta-overlays that reliably help — vol targeting, diversification, dynamic scaling

These aren't strategies; they're the *wrappers* that turn a raw edge into a fundable
book, and their evidence is as strong as any factor above.

- **Volatility targeting.** Scaling positions to a constant risk target **raises the
  Sharpe of "risk assets"** (equities, credit) and, more reliably, **cuts the left tail
  and max drawdown** across asset classes, because big losses cluster in high-vol
  regimes when a vol-target is already small
  ([Harvey et al. 2018, *JPM*](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3175538)).
  **Already the risk layer** of both books (`vol_target` in `strategy.py`, `size_book`
  in `forex/risk.py`).
- **Diversify across *uncorrelated* premia.** Value⊥momentum, trend's crisis-alpha, and
  carry's crash-risk are low-to-negatively correlated; combining them is the closest
  thing to a free lunch, and it is the whole logic of the multi-sleeve portfolio layer.
- **Dynamic de-risking.** Momentum's crash is forecastable (§1); scaling by conditional
  volatility is the cheap, well-evidenced upgrade. Partially present (regime filter +
  static drawdown breaker); the volatility-responsive version is unbuilt (§12).
- **What *doesn't* reliably help: factor timing.** Trying to rotate into whichever
  factor is "due" is far harder than the marketing implies and invites crowding losses
  (the "smart beta gone wrong" failure mode). Diversify across premia; don't time them.

---

## 11. The scoreboard — what actually survives

Ordered roughly by strength-of-evidence-per-unit-effort for a retail-scale, multi-region
system like this one.

| Strategy | Evidence base | Survives costs? | Retail-accessible? | Capacity | In this repo? |
|---|---|---|---|---|---|
| **Cross-sectional momentum** | Very strong, global, multi-asset | Yes (low-mod turnover) | Yes | High | ✅ Core equity engine |
| **Time-series momentum / trend** | Very strong, 140yr, crisis alpha | Yes | Yes (futures/ETFs) | High | ◑ Filters + FX agent only |
| **Carry** | Strong, multi-asset | Yes (but crash-skewed) | Yes (FX easiest) | High | ✅ FX `CarryAgent` |
| **Value** | Strong but decayed/contested | Yes (low turnover) | Yes | High | ◑ Price-proxy only |
| **Quality / profitability** | Strong, stabilises value | Yes (low turnover) | Needs fundamentals | High | ❌ Best unbuilt equity add |
| **Low-vol / BAB** | Strong but crowded | Yes | Diluted w/o leverage | High | ❌ Low priority |
| **Variance risk premium** | Strong, pervasive | Yes, until it isn't | Needs options infra | Moderate | ❌ High-effort/high-tail |
| **Short-horizon reversal / stat-arb** | Decayed since mid-2000s | **Rarely** (HFT-eaten) | No (latency game) | Low | ◑ Research probe only |
| **ML as a combiner** | Real but modest & fragile | Only if low-turnover | Yes, with discipline | — | ✅ FX MLP, done right |
| **Vol targeting (overlay)** | Strong (Sharpe + tail) | N/A | Yes | — | ✅ Both books |

Legend: ✅ built · ◑ partial · ❌ not built.

---

## 12. How this maps onto *this* system

**The honest headline: this system is already built around the edges that survive.**
The equity book is cross-sectional momentum (§1) + a value proxy (§4) + trend/regime
de-risking (§2/§10) + vol targeting (§10). The FX book adds trend, breakout, carry and
mean-reversion agents behind a performance-weighted ensemble, vol-targeted, with an
ML combiner validated by purged walk-forward + DSR/PBO. That is, almost exactly, the
list of things that pass §0. The gaps are specific and few.

### What the system already does well (don't re-invent)
- **Momentum + value, negatively-correlated, one weight function.** `momentum_score` +
  `value_score` → `compute_targets`. This is the AMP-2013 recipe.
- **Costs always on, no lookahead, one shared `compute_targets`.** The invariants in
  [`CLAUDE.md`](../CLAUDE.md) are precisely the things §0 says most public backtests
  omit. UK stamp duty on FTSE buys ([`fees.py`](../trading_algo/fees.py)) is exactly
  the kind of turnover-asymmetric cost that decides whether a momentum book survives.
- **Overfitting is gated, not hoped-for.** DSR/PBO (`validation.py`), purged/embargoed
  walk-forward (`walkforward.py`), the flat-surface *robustness* sweep (`sweep.py`), and
  the promotion gate (DSR ≥ 0.95, PBO ≤ 0.5, ≥6 paper rebalances) are the operational
  form of Harvey-Liu-Zhu and Bailey-López de Prado.
- **Register-then-fund discipline.** The TSX-style "backtestable but unfunded until it
  earns a slot" gate is the right way to add any edge below.
- **ML the honest way** (§9), and **carry the honest way** (§3), already in FX.

### The strongest *unbuilt* candidates, ranked
1. **Volatility-responsive momentum scaler (Daniel-Moskowitz).** *Cheapest, highest
   evidence-to-effort.* Scale the equity momentum book down when momentum's own
   conditional volatility is elevated / after market drawdowns — the forecastable part
   of the crash tail. Uses only prices you already have and the existing `vol_target`
   plumbing; it is a refinement of the static drawdown breaker + regime filter, not new
   infrastructure. Directly attacks the one known weakness of your core engine.
2. **Fundamental quality/profitability tilt for equities (Novy-Marx).** *Best
   diversifying add.* Gross-profits-to-assets is low-turnover (survives costs),
   diversifying, and specifically *stabilises* the momentum/value book you already run.
   Cost: needs a fundamentals data feed (not Yahoo prices) — the real work is data
   sourcing, not signal logic. Blends into `compute_targets` as a third ranked score
   alongside momentum and value.
3. **A managed-futures / TS-momentum diversifier sleeve.** *Best portfolio-level
   diversifier and crisis alpha* (§2). Cross-asset trend on liquid futures or ETFs
   (equity/bond/commodity/FX), run as a new registered-then-funded sleeve. Honest
   caveat: trend has decayed post-2010 — size it for the low correlation and the
   8-of-10-worst-drawdowns property, not for a headline Sharpe.
4. **Bond & commodity carry** (extending §3 beyond FX) — only if you're willing to add
   futures data; lower priority than 1-3.

### What NOT to chase
- **HFT / latency / market-making.** Different sport; see [`HFT_REALITY.md`](HFT_REALITY.md).
- **Short-horizon reversal / distance-based pairs as a *funded* book.** Decayed,
  HFT-eaten, most cost-sensitive category (§7). Keep `research.py`'s OU/stat-arb probes
  as *research judged by DSR/PBO*, never as capital.
- **Naked short volatility** without a tail hedge and margin harness (§8).
- **Factor timing / rotating into the hot factor** — invites crowding losses (§10). The
  portfolio layer already does the thing that *does* work: diversify across premia.
- **Anything with one-sided monthly turnover > ~50%** unless it *still* clears net of
  the full cost model. Run it through the gate below before believing it.

---

## How to pressure-test any candidate before funding it

Every edge above — and any new one — goes through the same gate this repo already
ships. Nothing gets capital on the strength of a backtest curve alone.

```bash
# ⚠ SYNTHETIC DATA = pipeline test only, never a performance claim (invariant #5).

# 1. Is the equity edge a plateau, not a lucky peak? (flat-surface robustness)
python -m trading_algo.sweep --region US                 # CV, %-positive cells, peak-isolation verdict
python -m trading_algo.sweep --region US --purged-cv     # + purged/embargoed walk-forward, DSR & PBO gate

# 2. Does a candidate FX edge clear the DEFLATED bar after costs?
python -m trading_algo.forex.research --synthetic        # OU / trend / breakout / xs-mom / stat-arb, each judged by DSR + PBO

# 3. Does it hold up out-of-sample, costs on, across the walk-forward?
python -m trading_algo.run_backtest --region US          # full costs (commission floor + slippage + stamp duty on FTSE buys)
python -m trading_algo.run_backtest --point-in-time      # survivorship-corrected (constituents.py)

# 4. Only then: paper-trade through the promotion gate (DSR>=0.95, PBO<=0.5, >=6 rebalances) before any live order.
```

The rule this whole document serves: **an edge is not "effective" because it backtests
well — it is effective because it survives decay, costs, and multiple testing, and this
repo is instrumented to check all three.**

---

## Key sources

*Meta / how edges die.* McLean & Pontiff, ["Does Academic Research Destroy Stock Return
Predictability?"](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2156623) (*J.
Finance* 2016). Hou, Xue & Zhang, ["Replicating
Anomalies"](https://www.nber.org/papers/w23394) (*RFS* 2020). Harvey, Liu & Zhu, "…and
the Cross-Section of Expected Returns" (*RFS* 2016). Bailey & López de Prado, ["The
Deflated Sharpe Ratio"](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551)
(2014); Bailey et al., ["The Probability of Backtest
Overfitting"](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf) (2017).

*Costs & capacity.* Novy-Marx & Velikov, ["A Taxonomy of Anomalies and Their Trading
Costs"](https://www.nber.org/papers/w20721) (*RFS* 2016). Frazzini, Israel & Moskowitz,
["Trading Costs of Asset Pricing
Anomalies"](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2294498) (2018).

*Momentum & trend.* Asness, Moskowitz & Pedersen, ["Value and Momentum
Everywhere"](https://pages.stern.nyu.edu/~lpederse/papers/ValMomEverywhere.pdf) (*J.
Finance* 2013). Daniel & Moskowitz, ["Momentum
Crashes"](https://www.nber.org/papers/w20439) (*JFE* 2016). Hurst, Ooi & Pedersen, ["A
Century of Evidence on Trend-Following
Investing"](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2993026) (*JPM* 2017);
Moskowitz, Ooi & Pedersen, "Time Series Momentum" (*JFE* 2012).

*Carry & FX.* Koijen, Moskowitz, Pedersen & Vrugt,
["Carry"](https://pages.stern.nyu.edu/~lpederse/papers/Carry.pdf) (*JFE* 2018).
Menkhoff, Sarno, Schmeling & Schrimpf, ["Carry Trades and Global FX
Volatility"](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1342968) (*J. Finance*
2012) and ["Currency Momentum
Strategies"](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1809776) (*JFE* 2012).

*Value, quality, low-vol.* Novy-Marx, ["The Other Side of Value: The Gross Profitability
Premium"](https://www.nber.org/papers/w15940) (*JFE* 2013). Frazzini & Pedersen,
["Betting Against Beta"](https://www.nber.org/papers/w16601) (*JFE* 2014). Arnott,
Harvey, Kalesnik & Linnainmaa, ["Reports of Value's Death May Be Greatly
Exaggerated"](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3488748) (2020).

*Mean-reversion / stat-arb.* Gatev, Goetzmann & Rouwenhorst, ["Pairs
Trading"](https://www.nber.org/papers/w7032) (*RFS* 2006).

*ML & overlays.* Gu, Kelly & Xiu, ["Empirical Asset Pricing via Machine
Learning"](https://www.nber.org/papers/w25398) (*RFS* 2020). Harvey et al., ["The Impact
of Volatility Targeting"](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3175538)
(*JPM* 2018).

*In-repo companions.* [`docs/research/COMBATING_BACKTEST_BIAS.md`](research/COMBATING_BACKTEST_BIAS.md)
(the methodology), [`docs/FX_DEEP_RESEARCH.md`](FX_DEEP_RESEARCH.md) (the ML design),
[`docs/HFT_REALITY.md`](HFT_REALITY.md) (why the latency game is out of scope).
