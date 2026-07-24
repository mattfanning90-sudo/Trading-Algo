# Backlog — Crypto as its own subsystem

Status: **Phase 0 shipped** (2026-07-24). Sub-projects A/B/C are **not yet
specced** — each gets its own `docs/specs/` spec → plan → implementation cycle
when picked up. This file is the captured intent, not a spec.

## Why (the evidence)

A loss-attribution pass on the mature paper books (matt, partner) found the
crypto pairs are the whole story, while the FX majors are ~flat:

- **matt** (balanced, $5k): −$227. ETHUSD −$122, SOLUSD −$59, BTCUSD −$21 →
  crypto ≈ 94% of the loss.
- **partner** (conservative, $5k): −$136. Crypto ≈ 98% of the loss.

Three diagnostics on the real matt trade log:

1. **Per-agent directional hit-rate on crypto is below a coin flip**
   (neural 32%, trend 38%, breakout 41%, momentum 41%) vs ~50% on FX majors.
   The FX technical agents have *negative* directional edge on crypto.
2. **Persistent wrong-way bias**: BTC short 10/10 trades while +2.1%; SOL short
   10/10 while +5.9%; ETH whipsawed (7 sign-flips in 5 weeks) while +8.5%.
3. **Churn is secondary**: a hysteresis flip-band recovers only ~$22 of the
   ~$202 crypto loss (~11%). ~90% of the loss is wrong-way exposure, not churn.

Root cause in code: the technical agents take a single `FXParams` — the **same**
windows/thresholds for every pair. Crypto carries an `asset_class="crypto"` tag
([pairs.py](../../trading_algo/forex/pairs.py)) used **only** for the risk
gross-cap, **never** for signal calibration. A 36%-vol, 24/7 asset is read with
knobs tuned for 8%-vol FX. The agents don't know they're trading crypto.

`docs/CRYPTO_HF.md` already names the honest edges — minute-scale signals and
**funding-rate / cash-and-carry** — and flags that the delta-neutral carry leg
is "the next build (funding is reported, not yet traded)."

## Phase 0 — stop the directional bleed (DONE, 2026-07-24)

Tightened the crypto gross cap on the two directionally-crypto books so a
negative-edge bet can't dominate P&L while the real subsystem is built:

- `balanced` (matt): `crypto_gross_cap` 0.25 → **0.10**
- `conservative` (partner): 0.15 → **0.05**

Reduces, does **not** eliminate, the expected-negative directional exposure.
Reversible (config only). Left untouched: the `FXParams` default (0.25),
`aggressive`, `intraday`/`daytrader`, and `hf_crypto` (None). Test:
`tests/test_fx_ensemble_risk.py::test_phase0_directional_crypto_bleed_stop`.

**Open option:** go to a full pull (remove crypto from matt/partner's directional
universe → crypto gross 0) if we'd rather run zero directional crypto until
Sub-project A lands. One-line change; not taken yet.

## Sub-project A — funding-rate cash-and-carry book (FIRST to spec)

The honest, evidence-backed core: **long spot / short perp, delta-neutral**,
harvesting the 8-hourly funding rate. No directional bet → sidesteps the
negative directional edge entirely. This is the repo's named "next build."

Build items:
- **Historical funding data.** `crypto_data.fetch_funding()` returns only the
  *latest* rate (one float). A carry backtest needs `fetchFundingRateHistory`
  (ccxt) → a stored funding time series aligned to the price panel.
- **Delta-neutral sizing** routed through the one `compute_targets`
  (invariant #3): pair a long-spot leg with a short-perp leg so net delta ≈ 0;
  size by funding level / carry-to-vol, not price direction.
- **Perp execution leg.** `crypto_exec.py` is spot-only today (shorts clamped).
  Carry needs a short-perp leg on a margin/perp account — a bigger risk surface
  (funding flips to negative carry; liquidation risk). Dry-run first.
- **Its own paper book + reporting** (separate state file, dashboard surface).
- **Validation**: run the carry through walk-forward + Deflated-Sharpe/PBO
  (`research.py`) — costs and funding-decay on. Fund only if it survives OOS.

Honesty constraints:
- Not validatable in the CI sandbox (no `ccxt`/network) — backtests/validation
  run on a machine with exchange access; here it's pipeline + synthetic only.
- Carry is **not riskless**: funding compresses when crowded and flips negative
  on sentiment turns; counterparty/exchange risk is real (see docs/CRYPTO_HF.md).

## Sub-project B — crypto-native directional signals, validated

Give crypto its own brain instead of FX knobs:
- Extend the `hf_crypto` short-window calibration to the daily/60m crypto books;
  make agent params **asset-class-aware** (crypto vs fx) rather than one global.
- Add a **trend-vs-range regime gate** (e.g. ADX) so the trend/breakout agents
  sit out choppy tapes where they measured <50%.
- Run through `research.py` (Deflated-Sharpe/PBO). **Ship only what passes OOS**;
  size small. A null result (no directional edge) is an acceptable outcome —
  then crypto stays carry-only.

## Sub-project C — integration & reporting

- Dedicated crypto book/sleeve (24/7 engine loop, not the FX-week gate).
- Dashboard surface for the carry book (funding earned, basis, net delta).
- Execution hardening (perp leg, max-notional, dry-run discipline).

## Unrelated follow-up (parked here so it isn't lost)

- **Scrub historical NaN rows in `state/paper_state_full.json`.** The US-sleeve
  NaN bug (fixed 2026-07-24 — FX-rate carry-forward in `paper_trade.py`) left
  NaN entries in `equity_history`/`sleeve_history` and a NaN `fx_snapshot["USD"]`
  from before the fix. The code now self-heals on the next successful `AUDUSD=X`
  fetch, but the historical rows remain. Offer: a backed-up, one-shot repair
  (drop the NaN rows; reset the stored USD rate) — **do not** auto-run on live
  state without a backup + explicit go-ahead.
