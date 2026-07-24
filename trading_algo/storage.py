"""SQLite-backed store for paper-trading books — plus a crash-safe JSON writer.

Every paper book (equity sleeve set or FX account) is a single nested ``dict``
that used to live only in a per-account ``*.json`` file, rewritten in place on
each run. An in-place ``open(path, "w")`` is not crash-safe: a process that dies
mid-write leaves a truncated, unparseable file with no backup.

This module gives the books a real datastore without adding a dependency
(``sqlite3`` is in the standard library) or forcing a schema onto the evolving
state dict. Each book is stored as one row — ``account -> JSON blob`` — in a
single SQLite file per state directory. SQLite gives us:

* **Atomic, durable writes** — a commit either lands whole or not at all, so a
  crash can no longer truncate a book.
* **Concurrent-write protection** — WAL plus optimistic revisions reject a
  stale scheduler/manual run instead of letting it clobber newer book state.
* **One queryable file** per state dir instead of a directory of loose blobs.

The legacy JSON files are still written alongside the DB (see
``atomic_write_json`` and the ``paper_trade`` / ``fx_book`` callers) so the
dashboards, CI ``*.json`` globs and hand-editing workflows keep working, and a
book created before the DB existed is still read as a fallback. Making SQLite
the *sole* source of truth is tracked in ``BACKLOG.md``.
"""
from __future__ import annotations

import json
import os
import sqlite3

_BUSY_TIMEOUT_MS = 5_000


class ConcurrentStateError(RuntimeError):
    """Raised when a stale process tries to overwrite a newer account state."""


def _connect(db_path: str) -> sqlite3.Connection:
    """Open ``db_path`` (creating its directory and the ``books`` table if
    needed) with WAL journalling and a busy-timeout so concurrent runs wait
    rather than raising ``database is locked``."""
    parent = os.path.dirname(os.path.abspath(db_path))
    os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=_BUSY_TIMEOUT_MS / 1000)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS books ("
        "  account    TEXT PRIMARY KEY,"
        "  state      TEXT NOT NULL,"
        "  revision   INTEGER NOT NULL DEFAULT 1,"
        "  updated_at TEXT NOT NULL DEFAULT (datetime('now'))"
        ")"
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(books)")}
    if "revision" not in columns:
        conn.execute("ALTER TABLE books ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")
    return conn


def db_load(db_path: str, account: str) -> dict | None:
    """Return the stored book for ``account``, or ``None`` if the DB or row is
    absent (so the caller can fall back to a legacy JSON file)."""
    state, _revision = db_load_with_revision(db_path, account)
    return state


def db_load_with_revision(db_path: str, account: str) -> tuple[dict | None, int | None]:
    """Return a book and its optimistic-concurrency revision, if present."""
    if not os.path.exists(db_path):
        return None, None
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT state, revision FROM books WHERE account = ?", (account,)
        ).fetchone()
    return (json.loads(row[0]), int(row[1])) if row else (None, None)


def db_save(db_path: str, account: str, state: dict,
            expected_revision: int | None = None) -> int:
    """Save a book and return its revision.

    When ``expected_revision`` is supplied, reject a stale writer instead of
    overwriting a state saved since it was loaded.
    """
    payload = json.dumps(state, indent=2)
    with _connect(db_path) as conn:
        if expected_revision is None:
            conn.execute(
                "INSERT INTO books (account, state, revision, updated_at) "
                "VALUES (?, ?, 1, datetime('now')) "
                "ON CONFLICT(account) DO UPDATE SET "
                "  state = excluded.state, revision = books.revision + 1, "
                "  updated_at = excluded.updated_at",
                (account, payload),
            )
            revision = conn.execute(
                "SELECT revision FROM books WHERE account = ?", (account,)
            ).fetchone()[0]
        else:
            cur = conn.execute(
                "UPDATE books SET state = ?, revision = revision + 1, "
                "updated_at = datetime('now') WHERE account = ? AND revision = ?",
                (payload, account, expected_revision),
            )
            if cur.rowcount != 1:
                raise ConcurrentStateError(
                    f"stale state for account '{account}'; reload before saving")
            revision = expected_revision + 1
        conn.commit()
    return int(revision)


def db_accounts(db_path: str) -> list[str]:
    """Account names present in the DB (empty if the DB does not exist yet)."""
    if not os.path.exists(db_path):
        return []
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT account FROM books ORDER BY account").fetchall()
    return [r[0] for r in rows]


def db_has(db_path: str, account: str) -> bool:
    """True if ``account`` has a row in the DB."""
    if not os.path.exists(db_path):
        return False
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM books WHERE account = ?", (account,)
        ).fetchone()
    return row is not None


def atomic_write_json(path: str, state: dict) -> None:
    """Write ``state`` to ``path`` crash-safely: serialise to a temp file in the
    same directory, ``fsync`` it, then ``os.replace`` (atomic on POSIX) over the
    target. A crash leaves either the old file or the new one — never a
    half-written one."""
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
