"""Glue for the book screens: a display key → saved inputs → the Overview or
Positions answer, plus the background price fetch for equity books."""
from __future__ import annotations

from datetime import datetime, timezone

from . import api, book_inputs, book_positions, book_summary, registry
from .live_marks import LiveMarks


class BookService:
    def __init__(self, *, now=None, online=None, live_build=None):
        self._now = now or (lambda: datetime.now(timezone.utc))
        # Named `_marks`: `marks()` below is the public method.
        self._marks = LiveMarks(live_build or (lambda acct: api.build_snapshot(acct, False)),
                                online=online or (lambda: True))

    def _inputs(self, key: str):
        entry = registry.resolve(key)
        if entry is None:
            return None
        live, market = None, "n/a"
        if entry["kind"] == "equity":
            market, live = self._marks.status(entry["account"])
        return book_inputs.load_inputs(entry, self._now(), live=live, market=market)

    def summary(self, key: str) -> dict | None:
        inp = self._inputs(key)
        return api._nan_free(book_summary.summarise(inp)) if inp else None

    def positions(self, key: str) -> dict | None:
        inp = self._inputs(key)
        return api._nan_free(book_positions.positions_view(inp)) if inp else None

    def marks(self, key: str) -> dict | None:
        entry = registry.resolve(key)
        if entry is None:
            return None
        if entry["kind"] != "equity":
            return {"status": "n/a"}
        return {"status": self._marks.start(entry["account"])}
