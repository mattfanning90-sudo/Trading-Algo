"""Today's prices for an equity book, fetched in the background and kept for
a while. The live re-mark (`api.build_snapshot`) takes ~50 s and can hang on a
failing download, so no request ever waits on it: the page asks, gets
"pending", and polls. Offline (the last sync failed), nothing is fetched."""
from __future__ import annotations

import sys
import threading
import time
import traceback

ERROR_RETRY = 60.0


class LiveMarks:
    def __init__(self, build, *, online, ttl: float = 900.0, clock=time.monotonic):
        self._build, self._online, self._ttl, self._clock = build, online, ttl, clock
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, dict]] = {}
        self._running: dict[str, threading.Thread] = {}
        self._errors: dict[str, float] = {}

    def status(self, account: str) -> tuple[str, dict | None]:
        with self._lock:
            hit = self._cache.get(account)
            if hit and self._clock() - hit[0] < self._ttl:
                return "ready", hit[1]
            if account in self._running:
                return "pending", None
            err = self._errors.get(account)
            if err is not None and self._clock() - err < ERROR_RETRY:
                return "error", None
        return ("idle", None) if self._online() else ("offline", None)

    def start(self, account: str) -> str:
        state, _ = self.status(account)
        if state != "idle":
            return state
        with self._lock:
            if account in self._running:
                return "pending"
            t = threading.Thread(target=self._run, args=(account,), daemon=True)
            self._running[account] = t
            t.start()
        return "pending"

    def wait(self, account: str, timeout: float) -> None:
        """Tests only: block until a running fetch finishes."""
        with self._lock:
            t = self._running.get(account)
        if t is not None:
            t.join(timeout)

    def _run(self, account: str) -> None:
        try:
            payload = self._build(account)
        except Exception as exc:
            print(f"[dashboard] live prices for {account!r} failed: {exc!r}", file=sys.stderr)
            traceback.print_exc()
            with self._lock:
                self._errors[account] = self._clock()
        else:
            with self._lock:
                self._cache[account] = (self._clock(), payload)
                self._errors.pop(account, None)
        finally:
            with self._lock:
                self._running.pop(account, None)
