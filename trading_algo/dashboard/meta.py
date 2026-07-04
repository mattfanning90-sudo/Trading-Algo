"""Static-ish configuration payload for the terminal dashboard.

Everything the METHOD pages and the chrome (status bar, account switcher) need
that lives in code rather than in state files: strategy knobs, per-region cost
schedules, risk controls, FX profiles, engine schedule, test count.
"""
from __future__ import annotations

import functools
import glob
import os
import re
from datetime import datetime, timezone

from .. import calendars
from .. import config as cfg
from ..forex import fx_config as fxcfg
from ..regions import REGIONS
from . import registry


@functools.lru_cache(maxsize=1)
def _tests_total() -> int | None:
    """Count test functions in the repo's tests/ dir (absent when installed
    as a package — then the status-bar segment is simply hidden). Cached: the
    count can't change while the server is running."""
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    files = glob.glob(os.path.join(root, "tests", "test_*.py"))
    if not files:
        return None
    n = 0
    for path in files:
        try:
            with open(path, encoding="utf-8") as f:
                n += len(re.findall(r"^def test_", f.read(), re.M))
        except OSError:
            pass
    return n or None


def _schedule() -> list[dict]:
    """Each region's next market close in UTC — the engine's wake points."""
    out = []
    for key, region in REGIONS.items():
        try:
            close = calendars.next_close(region).astimezone(timezone.utc)
            out.append({"region": key, "close_utc": close.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "close_hhmm": close.strftime("%H:%M")})
        except Exception:
            continue
    out.sort(key=lambda s: s["close_utc"])
    return out


def build_meta() -> dict:
    p = cfg.DEFAULT_PARAMS
    fx_profiles = {}
    for account, spec in fxcfg.ACCOUNTS.items():
        try:
            fp = fxcfg.profile(spec.get("profile", "balanced"))
        except KeyError:
            fp = fxcfg.FXParams()
        fx_profiles[account] = {
            "profile": spec.get("profile", "balanced"),
            "bar": spec.get("bar", fp.bar),
            "target_vol": fp.target_vol,
            "max_gross": fp.max_gross,
            "per_pair_cap": fp.per_pair_cap,
            "max_drawdown_stop": fp.max_drawdown_stop,
            "drawdown_cooldown_days": fp.drawdown_cooldown_days,
            "ema_fast": fp.ema_fast,
            "ema_slow": fp.ema_slow,
            "roc_window": fp.roc_window,
        }

    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "base_currency": cfg.BASE_CURRENCY,
        "accounts": [{k: e[k] for k in ("account", "key", "kind", "micro", "label", "sub")}
                     for e in registry.discover_accounts()],
        "params": {
            "lookback_days": p.lookback_days,
            "skip_days": p.skip_days,
            "top_n": p.top_n,
            "max_weight": p.max_weight,
            "target_vol": p.target_vol,
            "vol_lookback": p.vol_lookback,
            "max_gross": p.max_gross,
            "stock_trend_ma": p.stock_trend_ma,
            "index_trend_ma": p.index_trend_ma,
            "rebalance": p.rebalance,
        },
        "risk": {
            "max_drawdown_stop": cfg.MAX_DRAWDOWN_STOP,
            "drawdown_cooldown_days": cfg.DRAWDOWN_COOLDOWN_DAYS,
            "min_viable_equity_base": cfg.MIN_VIABLE_EQUITY_BASE,
        },
        "regions": [
            {
                "key": r.key, "name": r.name, "currency": r.currency,
                "index_ticker": r.index_ticker,
                "commission_bps": r.commission_bps,
                "min_commission": r.min_commission,
                "slippage_bps": r.slippage_bps,
                "stamp_duty_bps": r.stamp_duty_bps,
                "price_scale": r.price_scale,
            }
            for r in REGIONS.values()
        ],
        "fx_profiles": fx_profiles,
        "schedule": _schedule(),
        "tests_total": _tests_total(),
    }
