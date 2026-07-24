"""Notification / telemetry channel (foundation P0-F).

The engine runs unattended (cron / GitHub Actions), so a risk event that only
prints to a log can go unnoticed for weeks. This is the ONE shared channel that
risk features route through — the drawdown-breaker alert (F12), the crowding
monitor (F9), and later the promotion gate (F10) — instead of each inventing its
own alert path.

A channel is a callable `fn(payload: dict)`. The active channel is chosen by
`config.NOTIFY_CHANNEL` (default "log", which prints). Register a webhook / email
channel with `register_channel("slack", fn)` and point the config knob at it; the
call site never changes. `notify()` never raises — a telemetry failure must not
break a trading run.
"""
from __future__ import annotations

from typing import Callable

from . import config as cfg

_CHANNELS: dict[str, Callable[[dict], None]] = {}


def register_channel(name: str, fn) -> None:
    """Register a delivery channel `fn(payload: dict)` under `name`."""
    _CHANNELS[name] = fn


def _log_channel(payload: dict) -> None:
    print(f"🔔 [{payload.get('level', 'info').upper()}] "
          f"{payload.get('event')}: {payload.get('message')}")


register_channel("log", _log_channel)   # always-available default


def notify(event: str, message: str, level: str = "info",
           channel: str | None = None, **fields) -> dict:
    """Emit a notification and return the payload. Dispatches to the configured
    channel (falling back to "log"); never raises."""
    payload = {"event": event, "message": message, "level": level, **fields}
    name = channel or getattr(cfg, "NOTIFY_CHANNEL", "log") or "log"
    fn = _CHANNELS.get(name) or _CHANNELS.get("log")
    try:
        if fn is not None:
            fn(payload)
    except Exception:   # pragma: no cover - telemetry must never break a run
        pass
    return payload


def breaker_transition(prev_halted: bool, now_halted: bool) -> str | None:
    """Classify a halt-state change: 'halt' (off->on), 'resume' (on->off), or
    None (no change). Used to alert exactly once per transition — never every day
    while halted."""
    if now_halted and not prev_halted:
        return "halt"
    if prev_halted and not now_halted:
        return "resume"
    return None
