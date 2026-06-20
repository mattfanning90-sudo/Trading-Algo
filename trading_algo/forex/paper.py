"""CLI entry point for the FX paper book — see `fx_book` for the engine.

    python -m trading_algo.forex.paper --init
    python -m trading_algo.forex.paper --account matt --synthetic
"""
from __future__ import annotations

from .fx_book import main

if __name__ == "__main__":
    main()
