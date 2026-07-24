"""Account discovery for the terminal dashboard.

Finds every paper book on disk — equity sleeves (paper_state_*.json) and FX
agent books (fx_state_*.json) — and gives each a stable display key, label and
subtitle. Pure filesystem read; no network, no state mutation. Parsed state
files are cached by mtime so the 5-second poll doesn't re-parse every book on
every request.
"""
from __future__ import annotations

import glob
import json
import os

from .. import paper_trade
from .. import profiles
from ..forex import fx_book
from ..forex import fx_config as fxcfg
from ..forex import pairs

# Display keys/labels the terminal uses where the derived form isn't right.
# Everything else (bar cadence, profile, asset mix) is derived from the state
# file itself so the switcher can't go stale when a book's config changes.
_DISPLAY_KEY = {"daytrader": "DAY", "multiasset": "MULTI"}
_FX_LABEL = {"daytrader": "DAYTRADER · 60M", "multiasset": "MULTI-ASSET"}
_FX_SUB = {"multiasset": "EQUITIES + BONDS + FX · 10 SYMBOLS"}


def _compact_money(v: float) -> str:
    if v >= 1_000_000:
        return f"A${v / 1_000_000:.0f}M"
    if v >= 1_000:
        return f"A${v / 1_000:.0f}K"
    return f"A${v:.0f}"


_peek_cache: dict[str, tuple[float, dict | None]] = {}


def _peek(path: str) -> dict | None:
    """json.load with an mtime-keyed cache (state files change ~daily but the
    dashboard polls every 5s)."""
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        _peek_cache.pop(path, None)
        return None
    hit = _peek_cache.get(path)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    try:
        with open(path) as f:
            state = json.load(f)
    except (OSError, ValueError):
        # Torn read (engine mid-save) or corrupt file: keep serving the last
        # good parse and retry on the next poll rather than dropping the book.
        if hit is not None and hit[1] is not None:
            return hit[1]
        _peek_cache[path] = (mtime, None)
        return None
    _peek_cache[path] = (mtime, state)
    return state


def _equity_entry(account: str, state: dict) -> dict:
    initial = float(state.get("initial_capital_base") or 0.0)
    regions = list(state.get("allocations") or {})
    group = str(state.get("group") or profiles.CORE).upper()
    prof = profiles.PROFILES.get(state.get("profile") or "")
    # "micro" here selects the single-sleeve SMALL screens; multi-region books
    # keep the full terminal even if their sleeves trade in concentrated mode.
    # A profiled book (ultra / experimental) is intentionally sized/geared, so it
    # keeps the full terminal even when funded small.
    micro = (bool(initial) and initial < paper_trade.MICRO_THRESHOLD
             and len(regions) == 1 and prof is None)
    if prof is not None:
        label = f"{account.upper()} · {prof.label.split(' · ', 1)[-1]}"
        sub = prof.sub
    elif micro:
        label = f"{account.upper()} · {_compact_money(initial)}"
        only = " ONLY" if len(regions) == 1 else ""
        sub = f"EQUITIES · {' / '.join(regions)}{only} · MICRO MODE"
    else:
        label = f"{account.upper()} · EQUITIES"
        sub = f"EQUITIES · {len(regions)} REGIONS · MONTHLY"
    return {
        "account": account,
        "key": account.upper(),
        "kind": "equity",
        "micro": bool(micro),
        "label": label,
        "sub": sub,
        "group": group,
        "initial": initial,
        "regions": regions,
    }


def _pair_prefixes() -> set[str]:
    """The 3-letter currency / crypto codes that begin a 6-char pair symbol —
    derived from the pair registry (base + quote of every FX and crypto pair)
    instead of a hardcoded literal, so a new pair never has to be mirrored here.
    Equity/bond pseudo-pairs (base = a ticker, not a currency) are excluded."""
    codes: set[str] = set()
    for p in pairs.ALL_PAIRS.values():
        if p.asset_class in ("fx", "crypto"):
            codes.add(p.base)
            codes.add(p.quote)
    return codes


def _is_pairlike(symbol: str) -> bool:
    return len(symbol) == 6 and symbol[:3] in _pair_prefixes()


def _fx_entry(account: str, state: dict) -> dict:
    profile = state.get("profile", "balanced")
    bar = state.get("bar", "1d")
    sub = _FX_SUB.get(account)
    if sub is None:
        symbols = state.get("symbols") or []
        bar_txt = "60-MINUTE BARS" if bar == "60m" else "DAILY BARS"
        mixed = any(not _is_pairlike(s) for s in symbols)
        asset_txt = "MIXED ASSETS" if mixed else "FX + CRYPTO"
        if profile == "conservative":
            sub = f"{asset_txt} · CONSERVATIVE"
        else:
            sub = f"{asset_txt} · {bar_txt}"
    return {
        "account": account,
        "key": _DISPLAY_KEY.get(account, account.upper()),
        "kind": "fx",
        "micro": False,
        "label": _FX_LABEL.get(account, f"FX · {account.upper()}"),
        "sub": sub,
        "initial": float(state.get("initial_capital") or 0.0),
        "profile": profile,
        "bar": bar,
    }


def discover_accounts() -> list[dict]:
    """All paper books on disk, ordered: equity (largest first), then FX books
    in fx_config.ACCOUNTS order, then any stray FX books alphabetically."""
    out: list[dict] = []

    eq = []
    for path in sorted(glob.glob(os.path.join(paper_trade.STATE_DIR, "paper_state_*.json"))):
        account = os.path.basename(path)[len("paper_state_"):-len(".json")]
        state = _peek(path)
        if state is not None:
            eq.append(_equity_entry(account, state))
    eq.sort(key=lambda e: -e["initial"])
    out.extend(eq)

    found = {}
    for path in sorted(glob.glob(os.path.join(fx_book.STATE_DIR, "fx_state_*.json"))):
        account = os.path.basename(path)[len("fx_state_"):-len(".json")]
        state = _peek(path)
        if state is not None:
            found[account] = state
    ordered = [a for a in fxcfg.ACCOUNTS if a in found]
    ordered += sorted(a for a in found if a not in fxcfg.ACCOUNTS)
    out.extend(_fx_entry(a, found[a]) for a in ordered)

    # Display keys must be unique — an equity book named like an FX book would
    # otherwise shadow it in resolve()/the SPA's state maps.
    seen: set[str] = set()
    for entry in out:
        while entry["key"] in seen:
            entry["key"] += "·FX" if entry["kind"] == "fx" else "·EQ"
        seen.add(entry["key"])
    return out


def resolve(key: str) -> dict | None:
    """Map a display key ('FULL', 'DAY', …) or raw account name back to its
    registry entry."""
    k = (key or "").upper()
    for entry in discover_accounts():
        if entry["key"] == k or entry["account"].upper() == k:
            return entry
    return None
