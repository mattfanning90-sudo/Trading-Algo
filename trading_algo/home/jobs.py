"""Are the three scheduled GitHub jobs healthy?

Reads GitHub's public run list (no login: the repo is public) and judges each
job against grace periods MEASURED from its own history (2026-08-06 →
2026-10-09, 500+ scheduled runs), not guessed:

  * the daily jobs start a median ~80 min after their slot and the worst seen
    was ~8 h, so a daily job is "late" until 8 h 30 m after its slot and only
    then "missed";
  * GitHub skips most of DAYTRADER's hourly runs (2-6 of 23 a day since early
    September) with same-day gaps up to 796 min, so it is "missed" only after
    14 h without a run on a weekday.

A job GitHub can't tell us about is "unknown", never "ok".
"""
from __future__ import annotations

import http.client
import json
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from .paths import API_RUNS

DAILY_GRACE = timedelta(hours=8, minutes=30)
HOURLY_GAP = timedelta(hours=14)
CACHE_SECONDS = 300

# workflow name -> (label, cadence, hour, minute), UTC, weekdays only
JOBS: dict[str, tuple[str, str, int | None, int]] = {
    "Paper Trade & Publish Dashboard": ("Shares (daily)", "daily", 21, 30),
    "FX Paper Trading (live)": ("Currencies (daily)", "daily", 23, 0),
    "Day Trading Paper (hourly)": ("DAYTRADER (hourly)", "hourly", None, 7),
}
_FAILED = {"failure", "timed_out", "startup_failure"}


@dataclass(frozen=True)
class JobStatus:
    name: str
    label: str
    state: str          # ok | running | late | missed | failed | unknown
    summary: str
    last_run_at: str | None = None
    url: str | None = None


def _hm(t: datetime) -> str:
    return t.strftime("%a %H:%M UTC")


def _start(run: dict) -> datetime:
    return datetime.fromisoformat(run["run_started_at"].replace("Z", "+00:00"))


def _last_slot(now: datetime, hour: int, minute: int) -> datetime:
    """The most recent weekday HH:MM UTC at or before `now`."""
    slot = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if slot > now:
        slot -= timedelta(days=1)
    while slot.weekday() >= 5:
        slot -= timedelta(days=1)
    return slot


def classify(runs: list[dict], now: datetime) -> list[JobStatus]:
    out: list[JobStatus] = []
    for name, (label, cadence, hour, minute) in JOBS.items():
        # Only runs that did (or are doing) the work count. A cancelled/skipped
        # run never did it (GitHub cancels a queued run when a newer one arrives
        # in the same concurrency group), and a queued/waiting run that never
        # starts must not read as "running" forever and hide a missed slot.
        mine = sorted((r for r in runs
                       if r.get("name") == name and r.get("run_started_at")
                       and r.get("status") in ("completed", "in_progress")
                       and r.get("conclusion") not in ("cancelled", "skipped")),
                      key=_start, reverse=True)
        latest = mine[0] if mine else None
        last_at = _start(latest) if latest else None
        stamp = last_at.isoformat() if last_at else None
        url = latest.get("html_url") if latest else None
        if latest and latest.get("status") == "in_progress":
            out.append(JobStatus(name, label, "running", f"{label} is running now",
                                 stamp, url))
            continue
        if latest and latest.get("conclusion") in _FAILED:
            # A run can fail AFTER the books were saved (the Pages deploy or the
            # final audit gate), so say "check", not "the books didn't update".
            out.append(JobStatus(name, label, "failed",
                                 f"{label} run failed at {_hm(last_at)} — open it to "
                                 "check whether the books still updated", stamp, url))
            continue
        if cadence == "daily":
            slot = _last_slot(now, hour, minute)
            if last_at and last_at >= slot - timedelta(minutes=5):
                state, text = "ok", f"{label} ran {_hm(last_at)}"
            elif now - slot > DAILY_GRACE:
                state, text = "missed", f"{label} didn't run for its {_hm(slot)} slot"
            else:
                state, text = "late", (f"{label} due {_hm(slot)} — GitHub often "
                                       "starts it late")
        elif now.weekday() >= 5:
            state, text = "ok", f"{label} doesn't run at weekends"
        else:
            midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
            anchor = max(last_at, midnight) if last_at else midnight
            if now - anchor > HOURLY_GAP:
                state, text = "missed", f"{label} hasn't run for over 14 hours"
            elif last_at:
                state, text = "ok", f"{label} last ran {_hm(last_at)}"
            else:
                state, text = "ok", f"{label} hasn't run yet today"
        out.append(JobStatus(name, label, state, text, stamp, url))
    return out


def _ssl_context() -> ssl.SSLContext:
    """Verify GitHub against a real certificate list. python.org's Python on
    macOS ships none (`ssl.get_default_verify_paths().cafile` is None until its
    "Install Certificates" step is run), so a bare urlopen fails with
    CERTIFICATE_VERIFY_FAILED (found live 2026-10-10). certifi — already
    installed alongside `requests` — carries one."""
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def fetch_runs(timeout: float = 10.0) -> list[dict]:
    req = urllib.request.Request(
        f"{API_RUNS}?branch=main&event=schedule&per_page=100",
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": "momentum-home"})
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
        return json.load(resp)["workflow_runs"]


_cache: dict = {"at": 0.0, "runs": None, "ok_at": None}


def check(now: datetime | None = None, fetch=None,
          clock=time.monotonic) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    fetch = fetch or fetch_runs           # looked up at call time, so tests can swap it
    fresh = _cache["runs"] is not None and clock() - _cache["at"] <= CACHE_SECONDS
    if not fresh:
        try:
            runs = fetch()
        # http.client.HTTPException (IncompleteRead, BadStatusLine) is not an
        # OSError: a truncated reply must read "unknown", not blank the screen.
        except (urllib.error.URLError, OSError, ValueError, KeyError,
                http.client.HTTPException):
            since = _hm(_cache["ok_at"] or now)
            return [asdict(JobStatus(name, label, "unknown",
                                     f"{label}: GitHub status unknown since {since}"))
                    for name, (label, _cad, _h, _m) in JOBS.items()]
        _cache.update(runs=runs, at=clock(), ok_at=now)
    return [asdict(s) for s in classify(_cache["runs"], now)]
