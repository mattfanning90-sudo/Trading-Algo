"""Multi-region cross-sectional momentum trading system.

Three independent regional sleeves — FTSE (London), US (stocks + ETFs) and
ASX (Australia) — each running a 12-1 momentum book with its own index regime
filter, currency, fee schedule and trading calendar. A portfolio layer
allocates capital across sleeves and reports combined equity in a single base
currency (AUD by default), converting via FX.

Key modules
-----------
- config        global strategy parameters + portfolio (allocations, base ccy)
- regions       Region registry: universe, index, currency, fees, calendar, IBKR routing
- universes     the per-region ticker lists
- signals       12-1 momentum score + trend/regime filters + inverse-vol selection
- strategy      compute_targets() — the SINGLE source of truth for target weights
- backtest      per-sleeve no-lookahead walk-forward backtester
- portfolio_backtest   multi-sleeve backtest combined in the base currency
- paper_trade   persistent multi-region paper-trading simulator
- execution_ibkr  ib_insync execution layer (per-region routing)
- engine        background scheduler that runs each sleeve after its market close
- fx            FX conversion to the base currency
- fees          per-region commission + UK stamp duty
- calendars     per-region market hours / timezones
- metrics       performance statistics
"""

__version__ = "0.2.0"
