# FX subsystem — working rules

Loaded when you work in `trading_algo/forex/`. The project-wide invariants in the
root `CLAUDE.md` apply here too. Full tour: `README.md` in this folder.

## What it is
A self-contained foreign-exchange (and crypto) paper trader: parallel technical
agents → performance-weighted ensemble → volatility-targeted long/short book →
isolated paper books (`matt`, `partner`, `daytrader`, `multiasset`). One weight
function here too (`fx_strategy.target_weights_history`); the paper book and the
backtest both route through it.

## Rules specific to this folder
- **Data is sourced per symbol, not per book** (`--source auto`): crypto legs go
  to a real exchange via `ccxt`, FX legs to a bar source. The ECB's daily fixings
  (`frankfurter`) are close-only, which silently changes ADX/ATR/Donchian — so
  `bar_quality.py` makes range indicators refuse range-less bars and each book
  declares `FXParams.close_only_signals` (`refuse` default / `exclude` / `allow`).
  Source table and measured cost: `docs/DATA_FEEDS.md`.
- **Closed venues freeze, they don't flatten** (`sessions.py`): crypto 24/7; FX
  Sun 22:00 → Fri 22:00 UTC; equities/bonds a weekday cash session. Shut
  instruments are trimmed from the candidates and held, because you cannot
  liquidate on a closed venue.
- **What a learning loop may change** (decision D1,
  `docs/superpowers/specs/2026-09-16-champion-challenger-learning-design.md`):
  models and the agent roster only — never indicator windows, thresholds,
  holding policy, vol target, gross caps, the drawdown stop, capital, or which
  books exist.
- **Reporting:** every FX book is in the FX reporting group — a separate total,
  never the headline (`dashboard/overview.py`).
- **Intraday ≠ HFT.** `--bar 60m` / the `intraday` profile is medium frequency.
  Live intraday needs a real-time broker feed (`docs/HFT_REALITY.md`).

## Known operational facts (measured 2026-10-10)
- GitHub skips most of DAYTRADER's scheduled hourly runs: 23 are scheduled per
  weekday; it has run 2–6 a day since early September (gaps up to 796 min). Runs
  take ~3 min, so it is GitHub's scheduler, not a queue. Not fixed yet.

## Commands
```bash
python -m trading_algo.forex.run_backtest --synthetic          # offline FX pipeline test
python -m trading_algo.forex.paper --init                      # open the paper books
python -m trading_algo.forex.paper --account matt --source auto
python -m trading_algo.forex.engine --once [--ml]              # one decision cycle (all books)
python -m trading_algo.forex.train --synthetic                 # deep-learning models + walk-forward
python -m trading_algo.forex.research --synthetic              # edge search judged by DSR/PBO
python -m trading_algo.forex.evolve --all --synthetic          # breed the swarm
python -m trading_algo.forex.champions --all --synthetic       # DSR/PBO gate + promote
python -m trading_algo.forex.permtest --account matt --permutations 200   # ~20 min
python -m trading_algo.forex.dashboard --all --out-dir public  # static candlestick pages
```
More, with explanations: `README.md` in this folder.
