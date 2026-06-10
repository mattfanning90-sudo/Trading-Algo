"""Entry point baked into the macOS .app bundle.

Reads two optional environment variables so the same bundle can run live or on
synthetic data without rebuilding:
    MOMENTUM_ACCOUNT     paper account name        (default: "full")
    MOMENTUM_SYNTHETIC   "1"/"true" -> synthetic   (default: live)
"""
import os

from trading_algo.dashboard.desktop import launch

_TRUE = {"1", "true", "yes", "on"}

if __name__ == "__main__":
    account = os.environ.get("MOMENTUM_ACCOUNT", "full")
    synthetic = os.environ.get("MOMENTUM_SYNTHETIC", "0").strip().lower() in _TRUE
    launch(account=account, synthetic=synthetic)
