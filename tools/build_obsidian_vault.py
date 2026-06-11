#!/usr/bin/env python3
"""Regenerate the in-repo Obsidian vault under ./obsidian.

Most notes are hand-authored prose, but `Reference.md` is generated **from the
code** (`trading_algo.regions` + `config.StrategyParams`) so the vault can never
silently drift from the real settings. Re-run after changing regions/params:

    python tools/build_obsidian_vault.py        # or: make obsidian

It writes only the .md notes — it never touches obsidian/.obsidian (your vault
config) — so it's safe to run repeatedly.
"""
from __future__ import annotations

import os
import sys

# Make `trading_algo` importable when run from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataclasses import fields  # noqa: E402

from trading_algo.config import ALLOCATIONS, BASE_CURRENCY, DEFAULT_PARAMS  # noqa: E402
from trading_algo.regions import REGIONS  # noqa: E402

VAULT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "obsidian")

# Human descriptions for the params we surface (values pulled live from code).
_PARAM_DOCS = {
    "lookback_days": "12-month momentum window ([[12-1 Momentum]])",
    "skip_days": "days skipped (recent-month reversal)",
    "top_n": "names held per sleeve",
    "max_weight": "single-name cap",
    "target_vol": "annualised vol target ([[Volatility Targeting]])",
    "vol_lookback": "realised-vol window (days)",
    "stock_trend_ma": "per-stock trend MA ([[Regime & Trend Filters]])",
    "index_trend_ma": "index regime MA",
}


def _fmt(v: float) -> str:
    """Render a param value, using % for the fraction-style knobs."""
    return f"{v:.0%}" if isinstance(v, float) and 0 < v < 1 else str(v)


def reference_note() -> str:
    p = DEFAULT_PARAMS
    # --- region table, straight from REGIONS ---
    rows = []
    for r in REGIONS.values():
        duty = f"**{r.stamp_duty_bps:.0f} bps (buys)**" if r.stamp_duty_bps else "–"
        rows.append(
            f"| {r.key} | {r.index_ticker} | {r.currency} | {r.ibkr_exchange} | "
            f"{r.market_open:%H:%M}–{r.market_close:%H:%M} | {r.commission_bps:.0f} bps | "
            f"{r.min_commission:g} | {r.slippage_bps:.0f} bps | {duty} |"
        )
    region_table = "\n".join(rows)

    # --- params table, straight from StrategyParams ---
    fld = {f.name for f in fields(p)}
    prows = []
    for name, doc in _PARAM_DOCS.items():
        if name in fld:
            prows.append(f"| `{name}` | {_fmt(getattr(p, name))} | {doc} |")
    alloc = " / ".join(f"{k} {v:.0%}" for k, v in ALLOCATIONS.items())
    prows.append(f"| allocation | {alloc} | capital split |")
    prows.append(f"| base currency | {BASE_CURRENCY} | reporting unit |")
    params_table = "\n".join(prows)

    return f"""---
title: Reference
type: reference
tags: [trading, reference, generated]
created: 2026-06-11
up: ["[[Multi-Region Momentum]]"]
---

# 📓 Reference — settings, costs, commands

> [!note] Generated from code
> The tables below are produced by `tools/build_obsidian_vault.py` from
> `trading_algo/regions.py` and `config.py`. Re-run `make obsidian` after
> changing settings so this note stays truthful.

## Region settings & cost schedules

| Region | Index | Ccy | IBKR | Hours (local) | Comm | Min | Slip | Stamp duty |
|--------|-------|-----|------|---------------|------|-----|------|------------|
{region_table}

> [!info] LSE pence → pounds
> LSE shares quote in pence; the FTSE sleeve scales prices by `0.01` so it's
> internally consistent in GBP.

## Strategy parameters (defaults)

| Param | Value | Meaning |
|-------|-------|---------|
{params_table}

## Commands

```bash
python -m trading_algo.run_backtest --synthetic     # full AUD portfolio, offline
python -m trading_algo.run_backtest --region US     # one sleeve
python -m trading_algo.sweep --region US            # robustness sweep
python -m trading_algo.paper_trade --account full --init --capital 100000
python -m trading_algo.dashboard --account full     # live web dashboard :8787
make obsidian                                       # regenerate this vault
pytest -q                                           # tests
```

Related: [[Multi-Region Momentum]] · [[How It Works]]

#trading/reference
"""


# --- hand-authored notes (prose) -------------------------------------------

NOTES: dict[str, str] = {
    "Multi-Region Momentum.md": """---
title: Multi-Region Momentum
type: moc
tags: [trading, momentum, quant, moc]
created: 2026-06-11
aliases: [Trading Algo, Momentum System]
---

# 🌐 Multi-Region Momentum — Map of Content

A monthly-rebalanced **12-1 cross-sectional momentum** system run as three
independent regional sleeves — **FTSE**, **US** and **ASX** — combined into one
book and reported in **AUD**.

> [!abstract] In one sentence
> Each month, in each region, buy the strongest stocks in an uptrend while the
> market itself is in an uptrend; otherwise hold cash. Size by inverse vol, scale
> to a target vol, run three books across three currencies.

## Start here
- [[How It Works]] — the full step-by-step walk-through

## Concepts
- [[12-1 Momentum]] · [[Regime & Trend Filters]] · [[Volatility Targeting]] · [[No-Lookahead]]

## Reference
- [[Reference]] — region settings, costs, commands (generated from code)

> [!tip] Syncing this vault
> This folder is an Obsidian vault committed inside the `Trading-Algo` repo. To
> keep it current: `git pull` in the repo (notes update on disk), or install the
> **Obsidian Git** community plugin for in-app pull/push. Regenerate the
> code-derived notes with `make obsidian`.

```mermaid
flowchart TD
    A["Prices (local ccy)"] --> B["12-1 momentum"]
    A --> C["Trend & regime filters"]
    A --> E["Realised vol"]
    B --> S["Select top N"]
    C --> S
    S --> W["Inverse-vol weights, cap 15%"]
    E --> W
    W --> V["Volatility targeting → 12%"]
    V --> T["Target weights"]
    T --> X["Trade t+1 · costs on · whole shares"]
```

#trading/momentum
""",

    "How It Works.md": """---
title: How It Works
type: explainer
tags: [trading, momentum, algorithm]
created: 2026-06-11
aliases: [Algorithm, Strategy Explained]
up: ["[[Multi-Region Momentum]]"]
---

# ⚙️ How the algorithm works

> [!abstract] One paragraph
> Each month, in each region, rank stocks by [[12-1 Momentum]]. Buy the strongest
> in an uptrend while the index is in an uptrend ([[Regime & Trend Filters]]);
> else hold cash. Size by inverse vol and scale to a target vol
> ([[Volatility Targeting]]). Rebalance monthly, [[No-Lookahead|signal at t /
> trade at t+1]]. Run three sleeves, report in AUD.

## 1 · The per-sleeve pipeline

```mermaid
flowchart TD
    A["Daily prices (local ccy)"] --> B["Momentum score"]
    A --> C["Trend filter: price > 200d MA"]
    A --> D["Regime filter: index > 200d MA"]
    A --> E["Realised vol (63d)"]
    B --> F{"Eligible?"}
    C --> F
    D --> F
    F -- no --> G["Hold cash"]
    F -- yes --> H["Top N by momentum"]
    H --> I["Inverse-vol weights, cap 15%"]
    E --> I
    I --> J["Volatility targeting → 12%"]
    J --> K["Target weights"]
```

**Signal** — [[12-1 Momentum]]:
$$\\text{score}(t) = \\frac{P_{t-21}}{P_{t-252}} - 1$$

**Filters** — [[Regime & Trend Filters]]: a stock must be above its 200d MA, and
the index above its own 200d MA, else the sleeve goes 100% cash.

**Weighting**: top N by momentum, inverse-vol, capped at 15%:
$$w_i \\propto \\tfrac{1}{\\text{vol}_i}, \\quad w_i \\le 15\\%, \\quad \\sum w_i \\le 1$$

**Sizing** — [[Volatility Targeting]] to 12%/yr (constant-avg-correlation, ρ=0.6):
$$\\text{scale} = \\min\\!\\left(\\tfrac{0.12}{\\sqrt{\\text{var}}},\\ 1.5\\right),\\quad \\text{gross} \\le 100\\%$$

> [!note] One function, two engines
> Selection + vol targeting live in `strategy.compute_targets`. The backtester
> and the live paper trader both call it — no second copy to drift.

## 2 · From weights to trades — [[No-Lookahead]]
Decide on the last trading day of the month from data ≤ t; execute t+1. Each
rebalance pays cost:
$$\\text{cost} = \\text{turnover}\\cdot\\tfrac{\\text{comm}+\\text{slip}}{10^4} + \\text{buys}\\cdot\\tfrac{\\text{stamp}}{10^4}$$

> [!warning] UK stamp duty
> 0.5% on **FTSE purchases only** — asymmetric, modelled explicitly. See [[Reference]].

## 3 · Three sleeves → one AUD book
$$r_\\text{AUD} = (1 + r_\\text{local})\\cdot\\frac{fx_t}{fx_{t-1}} - 1$$
Each sleeve trades local; AUD reporting includes the currency move. Capital is
split ⅓ / ⅓ / ⅓ and trued to target on a cadence.

> [!example] First live run (2026-06-11)
> 18 trades — 0 ASX (risk-off → cash), 8 US, 10 FTSE — equity A$99,831 after costs.

> [!danger] Limitations
> A backtest is a hypothesis, not a promise; default universes are survivorship
> -biased; live broker execution stays manual (the automation does paper only).

Related: [[Multi-Region Momentum]] · [[Reference]]
""",

    "Concepts/12-1 Momentum.md": """---
title: 12-1 Momentum
type: concept
tags: [trading, momentum, signal]
created: 2026-06-11
up: ["[[How It Works]]"]
---

# 📈 12-1 Momentum

Total return over the last ~12 months, **excluding the most recent ~1 month**:
$$\\text{score}(t) = \\frac{P_{t-21}}{P_{t-252}} - 1$$

- 252 days ≈ 12 months; 21 ≈ 1 month. Built from past prices only → [[No-Lookahead]].

> [!info] Why skip the last month?
> The most recent month tends to mean-revert; skipping it isolates the persistent
> trend instead of buying a short-term spike.

> [!quote] Why momentum?
> The most replicated anomaly in equities (Jegadeesh & Titman, 1993); holds across
> [[Multi-Region Momentum|FTSE, US and ASX]].

Top **N** by score that also pass the [[Regime & Trend Filters]] form the book.

#trading/momentum
""",

    "Concepts/Regime & Trend Filters.md": """---
title: Regime & Trend Filters
type: concept
tags: [trading, risk, filter]
created: 2026-06-11
up: ["[[How It Works]]"]
---

# 🛡️ Regime & Trend Filters

## Per-stock trend
Eligible only if **price > 200-day MA** — keeps you out of falling knives that
merely fall slower than peers.

## Index regime (crash protection)
The index (ASX 200 / S&P 500 / FTSE 100) must be **above its own 200d MA**, else
the sleeve goes **100% cash** (`RISK_OFF`).

> [!warning] Why it matters most
> Momentum books blow up in the violent rebound *after* a bear market. Sitting in
> cash while the index is below trend sidesteps exactly that.

> [!example] Seen live (2026-06-11)
> ASX 200 below its 200d MA → ASX sleeve 100% cash, while US & FTSE stayed invested.

Eligibility = momentum > 0 **AND** above 200d MA **AND** regime risk-on.

Related: [[How It Works]] · [[12-1 Momentum]] · [[Volatility Targeting]]

#trading/risk
""",

    "Concepts/Volatility Targeting.md": """---
title: Volatility Targeting
type: concept
tags: [trading, risk, sizing]
created: 2026-06-11
up: ["[[How It Works]]"]
---

# 🎚️ Volatility Targeting

**1. Inverse-vol weights**, capped:
$$w_i \\propto \\tfrac{1}{\\text{vol}_i}, \\qquad w_i \\le 15\\%$$
Calm names get more capital; wild ones less. Vol = trailing 63-day realised,
annualised. If a cap pushes $\\sum w_i > 1$, renormalise (never lever from a cap).

**2. Scale to target** (12%/yr), constant-avg-correlation estimate (ρ = 0.6):
$$\\text{var} \\approx (1-\\rho)\\sum (w_i\\text{vol}_i)^2 + \\rho\\big(\\sum w_i\\text{vol}_i\\big)^2$$
$$\\text{scale} = \\min\\!\\big(\\tfrac{0.12}{\\sqrt{\\text{var}}},\\ 1.5\\big), \\quad \\text{gross} \\le 100\\%$$

> [!info] Steady risk, not steady money
> Calm markets → scale up; turbulent → scale down. Risk stays roughly constant
> rather than capital deployed. Never borrows (gross ≤ 100%).

Related: [[How It Works]] · [[Regime & Trend Filters]] · [[12-1 Momentum]]

#trading/risk
""",

    "Concepts/No-Lookahead.md": """---
title: No-Lookahead
type: concept
tags: [trading, backtesting, invariant]
created: 2026-06-11
up: ["[[How It Works]]"]
---

# 🕰️ No-Lookahead

A decision at time *t* uses only data available at *t*, and is acted on at *t+1*.
Weights are decided on the month's last trading day from data ≤ t, applied the
next day.

> [!danger] Why it's sacred
> The easiest way to fake a brilliant backtest is to peek at tomorrow's price.
> Every signal here is built from `shift`ed past prices.

> [!check] Enforced in code
> `tests/test_signals.py` and `tests/test_strategy.py` assert a signal at *t* is
> identical whether computed on full history or only data up to *t*.

Related: [[How It Works]] · [[12-1 Momentum]]

#trading/backtesting
""",

    "README.md": """# Obsidian vault — Multi-Region Momentum

This folder is a self-contained **Obsidian vault** documenting the trading
system, committed inside the repo so notes are version-controlled with the code.

## Open it
**Obsidian → Open folder as vault →** select this `obsidian/` folder. Start at the
**Multi-Region Momentum** note.

## Keep it in sync
- **Read-only:** `git pull` in the repo — notes update on disk and Obsidian
  reflects them.
- **Two-way:** install the **Obsidian Git** community plugin; it detects the
  repo's `.git` and lets you pull/push from inside Obsidian.

## Regenerate
`Reference.md` is generated from `trading_algo/regions.py` and `config.py`:

```bash
make obsidian        # or: python tools/build_obsidian_vault.py
```

Notes use wikilinks, tags, callouts, MathJax and Mermaid — all native to Obsidian.
""",
}


def main() -> None:
    written = 0
    for rel, body in NOTES.items():
        path = os.path.join(VAULT, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        written += 1
    ref = os.path.join(VAULT, "Reference.md")
    with open(ref, "w", encoding="utf-8") as f:
        f.write(reference_note())
    written += 1
    print(f"Obsidian vault regenerated at {VAULT} ({written} notes).")


if __name__ == "__main__":
    main()
