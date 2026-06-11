"""Point-in-time index constituents — the fix for survivorship bias.

The backtest's universes are *today's* members, so a name that dropped out of an
index years ago is invisible and the results are flattered. Supplying a
point-in-time (PIT) membership file fixes this: the backtest can then (a) only
select names that were actually in the index at each rebalance, and (b) include
names that have since been delisted — provided the data layer can fetch their
prices.

Membership file format (CSV or parquet), one row per (snapshot, member):

    date,ticker
    2012-01-31,BHP.AX
    2012-01-31,CBA.AX
    ...

Each `date` is a snapshot (month-end is plenty); a ticker present on that date
was a member then. `members_asof(d)` returns the membership of the most recent
snapshot on or before `d`. Point a region at a file via
`Region(..., constituents_file="path.csv")`. Norgate sells this for the ASX;
for US/FTSE drop in any CSV in the format above.

Without a file the system falls back to the current universe (survivorship
-biased) and labels backtest output accordingly.
"""
from __future__ import annotations

import bisect
import os

import pandas as pd

from .regions import Region


class MembershipTable:
    """Dated index membership with an as-of lookup."""

    def __init__(self, snapshots: dict[pd.Timestamp, set[str]]):
        self._dates: list[pd.Timestamp] = sorted(snapshots)
        self._snap = snapshots
        self._all: set[str] = set().union(*snapshots.values()) if snapshots else set()

    @classmethod
    def from_frame(cls, df: pd.DataFrame) -> "MembershipTable":
        if not {"date", "ticker"}.issubset(df.columns):
            raise ValueError("membership frame needs 'date' and 'ticker' columns")
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"])
        snaps: dict[pd.Timestamp, set[str]] = {}
        for d, grp in df.groupby("date"):
            snaps[pd.Timestamp(d)] = set(grp["ticker"].astype(str))
        return cls(snaps)

    @classmethod
    def from_file(cls, path: str) -> "MembershipTable":
        df = pd.read_parquet(path) if path.endswith(".parquet") else pd.read_csv(path)
        return cls.from_frame(df)

    def members_asof(self, date) -> set[str]:
        """Membership of the latest snapshot on or before `date`."""
        ts = pd.Timestamp(date)
        i = bisect.bisect_right(self._dates, ts) - 1
        return set(self._snap[self._dates[i]]) if i >= 0 else set()

    @property
    def all_tickers(self) -> list[str]:
        """Every ticker that was ever a member (incl. since-delisted names)."""
        return sorted(self._all)

    def __len__(self) -> int:
        return len(self._dates)


def get_membership(region: Region) -> MembershipTable | None:
    """Load a region's configured PIT file, or None if not set / missing."""
    path = getattr(region, "constituents_file", None)
    if path and os.path.exists(path):
        return MembershipTable.from_file(path)
    return None


def synthetic_membership(region: Region, start: str = "2012-01-01",
                         end: str = "2026-01-01", seed: int | None = None
                         ) -> MembershipTable:
    """A plausible PIT membership built by rotating names in/out of the current
    universe over time. OFFLINE TESTING ONLY — it does not represent real index
    history, only exercises the PIT machinery."""
    import numpy as np

    if seed is None:
        seed = 2000 + sum(ord(c) for c in region.key)
    rng = np.random.default_rng(seed)
    months = pd.date_range(start, end, freq="ME")
    uni = list(region.universe)

    keep = max(region.params.top_n + 5, int(len(uni) * 0.9))
    current = set(rng.choice(uni, size=min(keep, len(uni)), replace=False).tolist())
    pool = set(uni) - current

    snaps: dict[pd.Timestamp, set[str]] = {}
    for i, m in enumerate(months):
        if i % 3 == 0 and pool and current:          # churn ~1 name a quarter
            out = str(rng.choice(sorted(current)))
            inn = str(rng.choice(sorted(pool)))
            current.discard(out); pool.add(out)
            current.add(inn); pool.discard(inn)
        snaps[pd.Timestamp(m)] = set(current)
    return MembershipTable(snaps)
