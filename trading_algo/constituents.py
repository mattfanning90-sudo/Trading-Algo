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
    def from_wide_frame(cls, df: pd.DataFrame, normalize_us: bool = True) -> "MembershipTable":
        """Parse the 'date, comma-separated tickers' wide format used by the free
        fja05680/hanshof S&P 500 history CSVs (one row per snapshot, the second
        column a quoted comma-list). With `normalize_us`, class-share dots are
        mapped to Yahoo's hyphen (BRK.B -> BRK-B) so the provider chain can route
        them."""
        df = df.copy()
        df.columns = [c.lower() for c in df.columns]
        tcol = "tickers" if "tickers" in df.columns else df.columns[-1]
        snaps: dict[pd.Timestamp, set[str]] = {}
        for _, row in df.iterrows():
            toks = [t.strip().strip('"') for t in str(row[tcol]).split(",")]
            members = {t.replace(".", "-") if normalize_us else t
                       for t in toks if t and t.lower() != "nan"}
            if members:
                snaps[pd.Timestamp(row["date"])] = members
        return cls(snaps)

    @classmethod
    def from_file(cls, path: str) -> "MembershipTable":
        df = pd.read_parquet(path) if path.endswith(".parquet") else pd.read_csv(path)
        cols = {c.lower() for c in df.columns}
        # 'tickers' (one comma-list column) = wide fja05680 format; else long date,ticker
        if "tickers" in cols or (len(df.columns) == 2 and "ticker" not in cols):
            return cls.from_wide_frame(df)
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


_CACHE_DIR = os.path.join(os.path.dirname(__file__), ".cache")

# Free, MIT-licensed point-in-time S&P 500 history (dated snapshots incl. the
# delisted graveyard, 1996->now). Override with SP500_CONSTITUENTS_URL.
FJA05680_SP500_URL = (
    "https://raw.githubusercontent.com/fja05680/sp500/master/"
    "S%26P%20500%20Historical%20Components%20%26%20Changes(MM-DD-YYYY).csv"
)


def default_constituents_path(region_key: str) -> str:
    return os.path.join(_CACHE_DIR, f"constituents_{region_key}.csv")


def get_membership(region: Region) -> MembershipTable | None:
    """Load a region's PIT membership: the configured `constituents_file` if set,
    else an auto-discovered cache file at `default_constituents_path(region.key)`
    (written by `download_constituents`). None if neither exists."""
    path = getattr(region, "constituents_file", None)
    if not (path and os.path.exists(path)):
        cached = default_constituents_path(region.key)
        path = cached if os.path.exists(cached) else None
    return MembershipTable.from_file(path) if path else None


def download_constituents(region_key: str = "US", url: str | None = None) -> str:
    """Fetch a free point-in-time constituents CSV into the cache so PIT backtests
    pick it up automatically. US uses the fja05680 S&P 500 history by default.
    Needs network (run on your machine or in CI). Returns the written path."""
    import urllib.request

    url = url or os.environ.get("SP500_CONSTITUENTS_URL") or FJA05680_SP500_URL
    if region_key != "US" and url == FJA05680_SP500_URL:
        raise ValueError(f"No default free constituents source for {region_key}; "
                         "pass url= (e.g. an iShares-holdings export or LSEG list).")
    os.makedirs(_CACHE_DIR, exist_ok=True)
    dest = default_constituents_path(region_key)
    req = urllib.request.Request(url, headers={"User-Agent": "trading-algo"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    with open(dest, "wb") as f:
        f.write(data)
    return dest


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
