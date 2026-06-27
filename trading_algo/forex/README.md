# Multi-Agent FX Trading Subsystem

A low-latency, paper-ready foreign-exchange trading system built as a **parallel
ecosystem of technical strategy agents**. Five independent agents each form an
opinion on every currency pair; a performance-weighted ensemble blends them; a
volatility-targeting risk layer turns the blend into leveraged long/short
positions; and isolated multi-account paper books trade them — one for you, one
for your partner, as many as you like.

It lives alongside the equity-momentum sleeves and reuses the same engineering
principles (no lookahead, costs always on, one weight function shared by
backtest and live), redesigned for FX: pairs instead of single names, long/short
leverage instead of long-only, technical agents instead of a single momentum
score, and a fast per-bar decision cycle instead of a monthly rebalance.

## Quick start

```bash
pip install -r requirements.txt        # numpy / pandas / yfinance (offline ok)

# 1. Backtest the strategy (offline synthetic pipeline check)
python -m trading_algo.forex.run_backtest --synthetic
python -m trading_algo.forex.run_backtest --compare        # all risk profiles (real data)

# 2. Open the two ready-to-run paper books (matt = balanced, partner = conservative)
python -m trading_algo.forex.paper --init

# 3. Run a decision cycle for all accounts (use --synthetic offline)
python -m trading_algo.forex.engine --once
python -m trading_algo.forex.engine --once --account matt

# 4. Check in
python -m trading_algo.forex.paper --status --account matt
python -m trading_algo.forex.paper --compare matt partner

# 5. Run continuously (polls every 5 min, skips the weekend)
python -m trading_algo.forex.engine --loop --interval 300

# Measure the live decision-cycle latency
python -m trading_algo.forex.engine --benchmark

# Deep-learning layer: train models + honest walk-forward evaluation
python -m trading_algo.forex.train --synthetic         # offline pipeline check
python -m trading_algo.forex.train --out report.md     # real data (needs internet)
python -m trading_algo.forex.engine --once --ml        # paper-trade WITH the neural agent
```

Each account is a self-contained `fx_state_{account}.json`. Add a third book
with `python -m trading_algo.forex.paper --init --account jess --profile aggressive`.

## Live paper trading (unattended)

Run the books as a continuous paper exercise — no machine of yours needed. The
**FX Paper Trading (live)** GitHub Action (`.github/workflows/fx-paper.yml`) runs
on a weekday schedule, fetches real Yahoo FX data, advances both books one bar,
includes the deep-learning agent (retrained fresh each run, best-effort), writes a
status table to the run Summary, and **commits the updated `state/fx_state_*.json`
back to the repo** so the books compound across runs (the runner is ephemeral —
the repo is the ledger).

* `schedule:` only fires on the **default branch**, so merge to `main` for the
  daily cron to start; until then use *Run workflow* on `main` (the one-shot push
  trigger seeds the first run on this branch).
* One-time: repo *Settings → Actions → General → Workflow permissions →
  "Read and write"* so it can commit state back.
* State lives in tracked `state/fx_state_*.json`; pull the repo to watch the books.

It also publishes a **candlestick dashboard to GitHub Pages**: one page per book
with candle charts, EMA overlays, BUY/SELL markers, a per-agent vote breakdown and
a **trade journal explaining *why* each trade was made** (regime, which agents
fired, the indicator readings) — built to learn from. Build it locally with:

```bash
python -m trading_algo.forex.dashboard --all --out-dir public   # one html per book + index
python -m trading_algo.forex.dashboard --account matt -o matt.html
```

## Universe (FX majors + crypto)

The default universe is the seven FX majors **plus the three major cryptos**
(`BTCUSD`, `ETHUSD`, `SOLUSD`, via Yahoo `BTC-USD` etc.). Crypto trades 24/7, has
no spot swap, and runs far hotter than G10 FX — the volatility-targeting risk
layer sizes it down automatically, so it slots into the same agents/ensemble/book
unchanged. Existing books pick up newly-added instruments on their next run.

**Locally** (if your machine has internet) the same runs as a daemon:

```bash
python -m trading_algo.forex.paper --init                          # once
python -m trading_algo.forex.engine --loop --interval 3600 --ml    # poll hourly
```

## Data sources (`--source`)

The system is **source-agnostic**: every feed returns the same aligned OHLC panel,
so the agents/ensemble/risk/book/backtest are identical no matter the source. Pick
one with `--source`; live sources need a (free) account + that source's optional
dependency, and all ship a synthetic generator so the pipeline runs offline.

| `--source` | Asset class | Real-time? | Cost | Library |
|-----------|-------------|-----------|------|---------|
| `yahoo` *(default)* | FX + crypto | delayed | free, no key | yfinance |
| `crypto` | BTC/ETH/SOL | ✅ | free, no key | `ccxt` |
| `oanda` | FX majors | ✅ | free practice acct + token | `oandapyV20` |
| `alpaca` | US equities | ✅ (IEX) | free acct + keys | `alpaca-py` |
| `openbb` | research | mostly delayed | free | `openbb` |

```bash
python -m trading_algo.forex.run_backtest --source alpaca --bar 1h     # US equities
python -m trading_algo.forex.paper --init --account fx --source oanda --profile intraday
python -m trading_algo.forex.engine --loop --account fx --source oanda --bar 1h
```

The honest distinction (open-source *software* vs free real-time *data*), the
per-asset-class reality, credentials and caveats are all in
[`docs/DATA_FEEDS.md`](../../docs/DATA_FEEDS.md).

## The parallel agent ecosystem

| Agent | Edge | Acts when |
|-------|------|-----------|
| `TrendAgent` | EMA fast/slow spread, ATR-normalised | ADX says trending |
| `BreakoutAgent` | Donchian N-bar channel breakout (turtle) | new high/low |
| `MeanReversionAgent` | fades Bollinger/RSI extremes | ADX says ranging |
| `MomentumAgent` | rate-of-change, vol-normalised | always |
| `CarryAgent` | tilts to the positive-carry side | always |

Every agent emits a signal in **[-1, +1]** per bar and shares one interface, so
adding a sixth is a single class. They are evaluated **concurrently** by
`AgentPool` (one task per pair × agent), which is what makes this an ecosystem of
independent opinions rather than a single model.

The **ensemble** (`ensemble.py`) blends them per pair. In `adaptive` mode it
weights each agent by its own recent risk-adjusted performance *on that pair*
(rolling information ratio of signalₜ₋₁·returnₜ), with a floor so no agent is
ever fully muted — the system leans on whichever agents are currently right and
backs off the ones that aren't.

## Pipeline

```
panel (OHLC) ─▶ AgentPool ─▶ ensemble tilts ─▶ risk sizing ─▶ weights ─▶ book/backtest
              (parallel agents)  [-1,1]/pair    vol target +
                                                per-pair & gross caps
```

`fx_strategy.compute_targets()` is the **single source of truth** for target
weights — both the backtest and the live paper book call it, so they agree by
construction (pinned by `tests/test_fx_consistency.py`).

## Deep-learning layer (research-backed)

A deep-learning layer *augments* the five technical agents — it never replaces
them. The full literature review and the reasoning behind every choice is in
[`docs/FX_DEEP_RESEARCH.md`](../../docs/FX_DEEP_RESEARCH.md). The short version:

* **`nn.py`** — a pure-NumPy MLP (real Adam back-prop, dropout, L2, He init) with
  a first-class **Sharpe-ratio loss**: the net outputs a *position* and is trained
  to maximise risk-adjusted return directly (Deep Momentum Networks, 2019), which
  beats MSE-regression and direction-classification. Correctness is pinned by a
  finite-difference gradient check.
* **`NeuralAgent`** — the Sharpe-loss net as a 6th ecosystem agent (opt in with
  `engine --ml`). Frozen at inference, so live prediction has no lookahead.
* **`MetaLabeler`** — a secondary classifier (López de Prado meta-labeling) that
  sizes the ensemble's side via triple-barrier labels and bet-sizing.
* **Hedge ensemble** — agents are blended by multiplicative weights with a
  fixed-share floor (provable regret, low overfitting), the new default.
* **`walkforward.py`** — purged + embargoed expanding walk-forward, so every ML
  prediction is strictly out-of-sample; scalers fit on train folds only.
* **`validation.py`** — Probabilistic & **Deflated Sharpe** ratios and the
  **Probability of Backtest Overfitting** (CSCV), so "the model found an edge" is
  falsifiable, not hopeful.
* **`ml_backtest.py`** — one report comparing every strategy (agents, ensembles,
  neural, meta) out-of-sample with Sharpe/PSR/DSR/PBO and costs always on.

Why a small MLP and not an LSTM/Transformer, and why no RL: the evidence says
simpler regularized models win on noisy daily FX, and vol-targeting beats RL for
sizing. Daily FX is near-random-walk and factor edges decayed post-2008 — this
layer is built to *measure honestly*, not to overclaim. See the research doc.

## Quant-research agent

`research.py` is the honest version of "find alpha": it **searches** a basket of
candidate strategies — OU mean-reversion, trend/breakout parameter variants,
cross-sectional momentum, and **statistical-arbitrage pairs** — and judges every
one with the **Deflated Sharpe Ratio** and **Probability of Backtest Overfitting**
(which penalise you for how many you tried). It mostly *disproves* edges — that's
the point.

```bash
python -m trading_algo.forex.research --synthetic     # offline
python -m trading_algo.forex.research --out report.md # real data
```

## Frequency: daily, intraday — and why not HFT

The system is bar-agnostic. Default is **daily**; an **intraday / medium-frequency**
mode runs on 15m/60m bars via the `intraday` profile and `--bar`:

```bash
python -m trading_algo.forex.run_backtest --bar 60m --profile intraday
python -m trading_algo.forex.engine --once --bar 60m        # paper, medium-freq
```

The real prerequisite for *live* intraday is **data, not speed code** — Yahoo
intraday is delayed and history-limited, so live use needs a real-time broker
feed (OANDA/IBKR). **True high-frequency trading is out of scope and would be
dishonest to fake here** — the honest reasoning (latency, colocation, cost,
competition) is in [`docs/HFT_REALITY.md`](../../docs/HFT_REALITY.md).

### Crypto: the one honest home for "faster"

Crypto is the exception: exchanges hand retail **institutional-grade data for
free** (real-time 1-minute bars + funding rates via `ccxt`), so the data gate
that blocks live intraday FX simply isn't there. The `hf_crypto` profile runs the
same agent ecosystem on 1-minute crypto bars:

```bash
pip install ccxt
python -m trading_algo.forex.run_backtest --synthetic --profile hf_crypto --bar 1m
python -m trading_algo.forex.engine --once --account cryptohf --bar 1m --exchange binance
python -m trading_algo.forex.engine --loop --interval 60 --bar 1m --exchange binance  # 24/7
```

This is still **not** microsecond HFT — you can't out-latency Wintermute/Jump.
It's fast, high-turnover *systematic* crypto where the edge is signal + structure
(minute-scale trend/reversion and **funding-rate / cash-and-carry** harvesting),
not raw speed. Costs at this turnover are brutal, so every guardrail stays on.
Full scope, the real (small) edges, deployment (a VPS, not Actions) and risks:
[`docs/CRYPTO_HF.md`](../../docs/CRYPTO_HF.md).

## Design invariants

1. **No lookahead.** Every indicator/agent value at bar t uses only data ≤ t;
   the backtest applies weightₜ to the return realised over t→t+1.
   (`tests/test_fx_indicators.py`, `tests/test_fx_backtest.py`)
2. **Costs always on.** Every weight change crosses half the dealing spread;
   held positions accrue overnight carry/financing. No gross-only reporting.
3. **One weight function.** Backtest and paper trading both route through
   `fx_strategy.compute_targets`. No second copy of the sizing logic.
4. **Risk is layered and capped.** Volatility targeting sets overall size; a hard
   per-pair cap and a gross-leverage cap bound it; a drawdown breaker flattens
   the book and sits out a cooldown.
5. **Synthetic results are pipeline tests only** — never performance. The
   synthetic generator has unrealistically persistent trends; the CLI says so.

## Low latency

* The hot path is **vectorized numpy/pandas** — one decision cycle is a handful
  of array passes, not a Python loop over bars.
* `compute_targets(fast=True)` trims the panel to `min_history(p)` recent bars, so
  per-cycle time is **bounded regardless of how much history accumulates**
  (`tests/test_fx_consistency.py::test_fast_trim_is_exact` proves the trimmed
  result is identical to the full recompute).
* `AgentPool` reuses one thread pool across cycles and evaluates agents
  concurrently — the benefit grows with the universe size and intraday bar count.
* `indicators.StreamingEMA` / `StreamingATR` provide O(1)-per-tick incremental
  updates for the latency-critical path, pinned to the vectorized output.

A full daily decision cycle (7 pairs × 5 agents) runs in ~150 ms in pure Python;
`--benchmark` measures it on your machine. (Python/pandas is not an HFT engine —
this is "low latency" for a per-bar systematic FX book, not microsecond colo.)

## Going live

`compute_targets` already returns broker-agnostic signed target weights. For
**crypto** (the cheapest real-time path) live execution is built: `crypto_exec.py`
diffs target notional vs your live exchange balance and places ccxt orders —
**dry-run by default**, spot long-only, with a per-order notional cap.

```bash
python -m trading_algo.forex.crypto_exec --account cryptohf --synthetic   # offline rehearsal
python -m trading_algo.forex.crypto_exec --account cryptohf --exchange binance        # live data, dry-run
python -m trading_algo.forex.crypto_exec --account cryptohf --exchange binance --live --max-notional 50
```

For **FX/equities**, mirror `trading_algo/execution_ibkr.py` (or use the
`alpaca-py` / `oandapyV20` order APIs). **Start on paper / a tiny balance and
watch it for weeks first.** Safety model + env-var keys: `docs/CRYPTO_HF.md`.

## Files

| Module | Role |
|--------|------|
| `pairs.py` | currency-pair registry (pip, spread, carry, Yahoo ticker) |
| `fx_config.py` | `FXParams` + risk profiles + account presets |
| `indicators.py` | vectorized indicators + streaming variants |
| `fx_data.py` | OHLC panel loader + synthetic generator |
| `feeds.py` | source resolver — yahoo / crypto / oanda / alpaca / openbb |
| `crypto_data.py` / `oanda_data.py` / `alpaca_data.py` / `openbb_data.py` | per-source data adapters |
| `crypto_exec.py` | live crypto order execution via ccxt (dry-run by default) |
| `agents.py` | the five agents + concurrent `AgentPool` |
| `ensemble.py` | performance-weighted agent blending |
| `risk.py` | vol targeting + per-pair / gross caps |
| `fx_strategy.py` | **the single source of truth** for target weights |
| `fx_backtest.py` | walk-forward backtest, costs + breaker |
| `fx_book.py` | persistent multi-account paper books |
| `engine.py` | low-latency runner (`--once` / `--loop` / `--benchmark` / `--ml`) |
| `nn.py` | pure-NumPy MLP with Sharpe-loss + streaming scaler |
| `features.py` | causal feature engineering + triple-barrier labels |
| `walkforward.py` | purged + embargoed walk-forward prediction |
| `validation.py` | PSR / Deflated Sharpe / PBO / bet sizing |
| `ml_agent.py` | `NeuralAgent`, `MetaLabeler`, `ModelBundle`, pooled dataset |
| `ml_backtest.py` | honest out-of-sample strategy comparison |
| `train.py` | train + persist models, write the walk-forward report |
| `run_backtest.py` / `paper.py` | CLIs |
```
