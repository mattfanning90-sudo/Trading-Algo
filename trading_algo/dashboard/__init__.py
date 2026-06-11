"""Live dashboard for the multi-region momentum system.

A dependency-free web dashboard:
- `api.build_snapshot()` turns the persisted paper state into a JSON snapshot,
  marking positions to the latest prices and computing each sleeve's regime.
- `server` serves that snapshot at /api/state and the static SPA at /.

Run it:
    python -m trading_algo.dashboard --account full --synthetic
then open http://127.0.0.1:8787
"""
from .api import build_snapshot

__all__ = ["build_snapshot"]
