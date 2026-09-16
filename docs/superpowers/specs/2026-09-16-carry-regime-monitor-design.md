# Crypto Carry Regime Monitor (Design)

**Status:** Design agreed, pending implementation plan
**Date:** 2026-09-16
**Scope:** New read-only instrument under `trading_algo/forex/`. No trading, no
execution, no position sizing. Does **not** implement crypto-subsystem
Sub-project A — it is the gate that decides whether A is worth building.

## 1. What this is

A two-sided **regime monitor** for the perpetual-swap funding rate — the cash
flow a delta-neutral long-spot/short-perp book would harvest. It answers one
question on a schedule: *is funding-rate cash-and-carry currently worth more
than cash, net of costs?*

It exists because the answer moves. A pressure-test of the carry trade against
the §0 gate in [`EFFECTIVE_PUBLIC_STRATEGIES.md`](../../EFFECTIVE_PUBLIC_STRATEGIES.md)
found the edge had decayed ~90% (BTC gross funding: 30.6% in 2021 → 2.80% YTD
2026) — but that the trailing 30 days annualised to 7.34%. A number that swings
between 1.7% and 7.3% inside one year is not a thing to judge once and file. It
is a thing to instrument.

The verdict it replaces is "don't build the carry book yet". The verdict it
produces is "now it is / still isn't worth building" — continuously, with an
alert on the transition.

## 2. Decisions (locked during brainstorming)

- **D1 — Two-sided, not a decay alarm.** Alerts on both transitions: THIN→RICH
  ("now worth building") and RICH→THIN ("edge is decaying"). A one-sided decay
  detector is useless while flat, and a one-sided entry detector is useless once
  funded. The two-sided version never needs rebuilding.
- **D2 — Trailing 60-day window with economic hysteresis.** Chosen by
  measurement against 5.7 years of real funding, not by preference (§5).
- **D3 — CUSUM rejected.** Measured ~10 days earlier, but the gain is inside the
  noise of its own parameter choice (§5). Rejected on overfitting grounds.
- **D4 — No new dependency.** Funding history comes from Binance's public
  `fapi/v1/fundingRate` endpoint over stdlib `urllib` + `certifi`. `ccxt` is not
  installed and is not required. One venue only (see §11).
- **D4a — Watch list: BTC, ETH, SOL.** The three perps already in
  `crypto_data.PERP`. SOL is retained despite being negative-carry for most of
  2026 (39% of its prints are negative): keeping it costs nothing, regimes
  change, and dropping an instrument *after* seeing its data is the selection
  bias this repo exists to prevent.
- **D5 — Reports only.** No order ever leaves this module. It cannot size, hold
  or trade a position.
- **D6 — Net, never gross.** Every number compared to cash is net of the cost
  model (invariant #2).

## 3. Architecture

### New modules

| module | responsibility |
|---|---|
| `forex/funding.py` | fetch + backfill the 8-hourly funding series; append-only persistence |
| `forex/carry_regime.py` | the detector: series → net carry → `RICH`/`THIN` + transitions |

Split because they fail differently: `funding.py` is I/O that can be offline or
rate-limited; `carry_regime.py` is pure computation over a series and must be
testable with no network. Nothing in `carry_regime.py` imports `funding.py`.

### Data flow

```
exchange public REST ──► funding.py ──► state/funding_history.json (append-only)
                                              │
                                              ▼
                                      carry_regime.py
                                   (trailing 60d net carry)
                                              │
                        ┌─────────────────────┼─────────────────────┐
                        ▼                     ▼                     ▼
             state/funding_regime.json   notifications.notify   dashboard CARRY
                (current + since)        (transitions ONLY)         panel
```

## 4. The decision rule

Net annualised carry over a trailing window `W`:

```
net(t) = mean(funding over W) * 1095  −  cost_drag
```

`1095` = 3 funding prints/day × 365. Cost drag = 0.30% round-trip amortised over
a one-year hold + 0.10%/yr delta rebalancing (measured against Binance retail
taker fees: spot 0.10%, perp 0.05%, both legs, in and out).

State transition:

```
net > RISK_FREE + PREMIUM   ->  RICH   (worth building)
net < RISK_FREE             ->  THIN   (not worth it)
otherwise                   ->  hold current state
```

**The hysteresis band is the economics, not a knob.** The dead zone between
`RISK_FREE` and `RISK_FREE + PREMIUM` is exactly the region where the trade beats
cash but not by enough to pay for its risk — so "do nothing different" is the
correct answer there, and anti-flapping falls out for free rather than being
tuned in. `RISK_FREE` is read from `config.RISK_FREE` (3.5%); `PREMIUM` defaults
to 3.0% and is a module constant.

`PREMIUM` is the single most consequential number in this design and it is a
judgement, not a measurement: it prices liquidation risk, exchange counterparty
risk and operational burden. At 1% the monitor would read RICH today; at 3% it
reads THIN despite the last 30 days annualising at 7.34%.

## 5. Why 60 days (the measurement)

Scored against a centred-90-day hindsight "truth" over the full history — 11
real regime changes on BTC, 5 on ETH:

| detector | lag (median) | caught | false alarms |
|---|---|---|---|
| trailing 30d + hysteresis | 10 d | 10/11 | 8 |
| **trailing 60d + hysteresis** | **26 d** | **11/11** | **1** |
| trailing 90d + hysteresis | 44 d | 11/11 | 0 |
| CUSUM k=0.50 h=8 | 16 d | 10/11 | 2 |

Two findings worth keeping:

1. **More sensitivity does not buy earliness.** A 14-day window has a *worse*
   median lag on ETH (38 d) than a 30-day window (10 d) — it fires on noise,
   flips back, and the real transition is then detected late. Shortening the
   window past a point makes the instrument worse at its own job.
2. **CUSUM's edge is not real.** It measured ~10 days earlier, but that winner
   was picked from six parameter pairs scored on 16 total events, and its
   behaviour is non-monotonic in the threshold (ETH: h=5 → −23 d, h=8 → +12 d).
   Non-monotonicity is the signature of fitting noise. Buying 10 days by tuning
   two parameters on 16 events is precisely what `validation.py` exists to stop.

60 days is the knee: it catches every real transition on both symbols with 1–4
false alarms across 5.7 years, at ~3–4 weeks of lag. For an instrument whose
output is "consider building / consider unwinding a book", weeks is the right
resolution.

## 6. Persistence

- **`state/funding_history.json`** — append-only `{symbol: [[ms, rate], …]}`,
  seeded by a one-off backfill (~6,250 prints/symbol from Dec 2020). Append-only
  because a funding print is a settled historical fact; nothing may rewrite one.
- **`state/funding_regime.json`** — `{symbol: {state, since, net_carry, updated}}`.

Both written through `storage.atomic_write_json` (crash-safe), consistent with
the paper books.

## 7. Alerting

Transitions only, via the one shared channel (`notifications.notify`), matching
the drawdown breaker's discipline — alert on the *transition*, never on the
ongoing state, or it becomes noise and gets ignored:

```
notify("carry_regime", "[BTC] carry regime THIN -> RICH — net 6.2% vs cash 3.5%",
       level="alert", symbol="BTC", state="RICH", net_carry=0.062)
```

## 8. Dashboard — CARRY panel

Current regime per symbol, trailing net carry against `RISK_FREE`, days in
current regime, and a sparkline of the funding history. Read-only from
`state/funding_regime.json` + `state/funding_history.json`; renders offline at
the last stored values like every other panel.

## 9. Testing strategy

A trimmed funding fixture is committed so tests run offline and deterministically
(CI has no network). The tests that matter pin **detection quality**, not just
that functions return strings:

- the detector flags the real April-2022 collapse within 60 days
- no more than 2 false alarms across the full committed history
- the hysteresis band genuinely suppresses flapping (a series oscillating inside
  the dead zone produces zero transitions)
- net carry is always below gross (cost model is actually applied)
- `funding.py` append is idempotent — re-running never duplicates a print

The first two are regression tests on the *window choice*. If someone later
retunes `W`, the suite tells them what it cost in lag or false alarms.

## 10. Invariants preserved

- **#2 costs always on** — the number compared to cash is net (§4).
- **#5 synthetic ≠ performance** — the fixture is real exchange data, trimmed;
  no synthetic funding series is ever presented as a carry result.
- **#3 one weight function** — untouched. This module computes no weights and is
  imported by nothing that trades.

## 11. Non-goals (YAGNI)

- No perp execution leg, no delta-neutral sizing, no carry paper book — that is
  Sub-project A, and it stays unbuilt until this instrument says it is worth it.
- No funding *forecast*. The instrument reports the regime that exists; it does
  not predict the next one. A predictor would need the DSR/PBO machinery and
  would reintroduce exactly the overfitting this design avoids.
- No multi-exchange aggregation. One venue's funding is the signal; comparing
  venues is a basis trade, not a regime monitor.

## 12. Risks & open questions

- **Detection lag is real.** ~3–4 weeks. A regime that reverses inside a month is
  invisible to this instrument by construction. Accepted: the action it drives
  (build/unwind a book) operates on a slower clock than that.
- **16 events is a small sample.** The 60-day choice is supported by measurement
  but that measurement rests on 16 transitions across 2 symbols. It is a
  defensible choice, not a proven optimum, and §9's tests exist so the cost of
  changing it is visible.
- **The funding stream understates the risk.** Worst funding-stream drawdown on
  BTC was −0.41%, which makes the carry look nearly riskless. The real risks —
  liquidation of the short perp leg while nominally delta-neutral, exchange
  counterparty failure — are absent from this data entirely. `PREMIUM` is the
  only place they are priced, and it is a guess. The dashboard panel must not
  imply the trade is as safe as the funding series looks.
- **Single venue is a real limitation.** Binance funding is the signal, so a
  Binance-specific dislocation reads as a regime change. Accepted for v1: the
  alternative (multi-venue aggregation) is a different instrument, and Binance
  is where the carry would actually be run.
