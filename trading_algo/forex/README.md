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
```

Each account is a self-contained `fx_state_{account}.json`. Add a third book
with `python -m trading_algo.forex.paper --init --account jess --profile aggressive`.

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

`compute_targets` already returns broker-agnostic signed target weights. To wire
real execution, mirror `trading_algo/execution_ibkr.py`: map each pair to an
IBKR `Forex` contract, diff target notional vs live positions, and place orders.
**Start on the paper port and watch it for weeks first.**

## Files

| Module | Role |
|--------|------|
| `pairs.py` | currency-pair registry (pip, spread, carry, Yahoo ticker) |
| `fx_config.py` | `FXParams` + risk profiles + account presets |
| `indicators.py` | vectorized indicators + streaming variants |
| `fx_data.py` | OHLC panel loader + synthetic generator |
| `agents.py` | the five agents + concurrent `AgentPool` |
| `ensemble.py` | performance-weighted agent blending |
| `risk.py` | vol targeting + per-pair / gross caps |
| `fx_strategy.py` | **the single source of truth** for target weights |
| `fx_backtest.py` | walk-forward backtest, costs + breaker |
| `fx_book.py` | persistent multi-account paper books |
| `engine.py` | low-latency runner (`--once` / `--loop` / `--benchmark`) |
| `run_backtest.py` / `paper.py` | CLIs |
```
