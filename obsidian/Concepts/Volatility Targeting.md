---
title: Volatility Targeting
type: concept
tags: [trading, risk, sizing]
created: 2026-06-11
up: ["[[How It Works]]"]
---

# 🎚️ Volatility Targeting

**1. Inverse-vol weights**, capped:
$$w_i \propto \tfrac{1}{\text{vol}_i}, \qquad w_i \le 15\%$$
Calm names get more capital; wild ones less. Vol = trailing 63-day realised,
annualised. If a cap pushes $\sum w_i > 1$, renormalise (never lever from a cap).

**2. Scale to target** (12%/yr), constant-avg-correlation estimate (ρ = 0.6):
$$\text{var} \approx (1-\rho)\sum (w_i\text{vol}_i)^2 + \rho\big(\sum w_i\text{vol}_i\big)^2$$
$$\text{scale} = \min\!\big(\tfrac{0.12}{\sqrt{\text{var}}},\ 1.5\big), \quad \text{gross} \le 100\%$$

> [!info] Steady risk, not steady money
> Calm markets → scale up; turbulent → scale down. Risk stays roughly constant
> rather than capital deployed. Never borrows (gross ≤ 100%).

Related: [[How It Works]] · [[Regime & Trend Filters]] · [[12-1 Momentum]]

#trading/risk
