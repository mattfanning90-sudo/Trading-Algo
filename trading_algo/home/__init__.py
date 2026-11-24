"""The Mac home screen: "am I up, and is anything wrong?".

Stdlib only, and nothing here imports `trading_algo.dashboard`,
`trading_algo.paper_trade` or `trading_algo.forex.fx_book` at module level:
those readers fix their state directory at import time, and the launcher must
point them at the synced snapshot first. See `__main__.py`.
"""
