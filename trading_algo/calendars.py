"""Per-region market calendars (timezone-aware).

Lightweight: weekday + local cash-session window. Public holidays are NOT
modelled — for a monthly-rebalanced strategy that keys off the data's last
available session, holiday precision doesn't change decisions; it only affects
*when* the background scheduler wakes. The scheduler is defensive (it re-checks
the data date), so a holiday at worst means a no-op run.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from .regions import Region


def now_local(region: Region) -> datetime:
    return datetime.now(ZoneInfo(region.timezone))


def is_weekday(dt: datetime) -> bool:
    return dt.weekday() < 5  # Mon-Fri


def is_market_open(region: Region, dt: datetime | None = None) -> bool:
    """True if `dt` (default now) is within the local cash session on a weekday."""
    dt = dt or now_local(region)
    dt = dt.astimezone(ZoneInfo(region.timezone))
    if not is_weekday(dt):
        return False
    return region.market_open <= dt.timetz().replace(tzinfo=None) <= region.market_close


def is_after_close(region: Region, dt: datetime | None = None) -> bool:
    """True on a weekday once the local market close has passed."""
    dt = dt or now_local(region)
    dt = dt.astimezone(ZoneInfo(region.timezone))
    if not is_weekday(dt):
        return False
    return dt.timetz().replace(tzinfo=None) >= region.market_close


def next_close(region: Region, dt: datetime | None = None) -> datetime:
    """The next local market close at or after `dt` (skips weekends)."""
    tz = ZoneInfo(region.timezone)
    dt = (dt or now_local(region)).astimezone(tz)
    candidate = dt.replace(hour=region.market_close.hour,
                           minute=region.market_close.minute,
                           second=0, microsecond=0)
    if candidate <= dt:
        candidate += timedelta(days=1)
    while not is_weekday(candidate):
        candidate += timedelta(days=1)
    return candidate


def session_date(region: Region, dt: datetime | None = None) -> str:
    """The local calendar date (YYYY-MM-DD) of the current/last session."""
    dt = (dt or now_local(region)).astimezone(ZoneInfo(region.timezone))
    return dt.strftime("%Y-%m-%d")
