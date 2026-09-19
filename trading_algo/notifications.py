"""Notification / telemetry channel (foundation P0-F).

The engine runs unattended (cron / GitHub Actions), so a risk event that only
prints to a log can go unnoticed for weeks. This is the ONE shared channel that
risk features route through — the drawdown-breaker alert (F12), the crowding
monitor (F9), and later the promotion gate (F10) — instead of each inventing its
own alert path.

A channel is a callable `fn(payload: dict)`. The active channel is chosen by
`config.NOTIFY_CHANNEL`. Two ship: "log" (prints) and "webhook" (prints, then
POSTs to $ALERT_WEBHOOK_URL — a no-op when that is unset). Register another with
`register_channel("slack", fn)` and point the config knob at it; the call site
never changes. `notify()` never raises — a telemetry failure must not break a
trading run.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
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


# How long to wait on the webhook before giving up. Short on purpose: this runs
# inside a trading job, and a hanging telemetry POST must never be the reason a
# book is late.
WEBHOOK_TIMEOUT_SECONDS = 10


def _is_ntfy(url: str) -> bool:
    try:
        return urllib.parse.urlparse(url).hostname in ("ntfy.sh", "www.ntfy.sh")
    except Exception:
        return False


def _webhook_channel(payload: dict) -> None:
    """POST the payload as JSON to ``$ALERT_WEBHOOK_URL``.

    Slack, Discord and ntfy.sh all accept a bare JSON body and render a ``text``
    field, so one shape reaches any of them with no per-provider code and no new
    dependency (stdlib ``urllib`` only).

    Two deliberate properties:

    * **No URL configured is a silent no-op.** A book must still trade on a
      laptop with nothing wired up, so this is safe to leave selected globally.
    * **The log happens FIRST.** ``notify()`` swallows channel exceptions, so
      posting before logging would let a dead endpoint take the local trace down
      with it — losing the alert twice over.
    """
    _log_channel(payload)
    url = os.environ.get("ALERT_WEBHOOK_URL")
    if not url:
        return
    line = (f"[{payload.get('level', 'info').upper()}] "
            f"{payload.get('event')}: {payload.get('message')}")
    if _is_ntfy(url):
        # ntfy renders the raw request BODY as the notification text, so posting
        # JSON there puts a wall of escaped braces on your phone. Verified
        # against the live service before special-casing it.
        data, ctype = line.encode(), "text/plain; charset=utf-8"
    else:
        body = dict(payload)
        body.setdefault("text", line)       # Slack/Discord both render `text`
        data, ctype = json.dumps(body).encode(), "application/json"
    # Pin the scheme. $ALERT_WEBHOOK_URL is operator-supplied, and `urlopen`
    # would otherwise accept file:// — which on a POST is a local write
    # primitive driven by an env var. Alerts go over the wire or not at all.
    if urllib.parse.urlparse(url).scheme not in ("https", "http"):
        return
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": ctype}, method="POST")
    with urllib.request.urlopen(req, timeout=WEBHOOK_TIMEOUT_SECONDS):  # nosec B310 - scheme pinned above
        pass


register_channel("webhook", _webhook_channel)


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
