"""Back up and repair historical non-finite values in persisted paper books.

SQLite is the source of truth when an account exists in both stores. Invalid
valuation-history rows are removed because their numeric value is unknowable;
non-finite diagnostic fields elsewhere are retained as JSON ``null``.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import math
from pathlib import Path
import shutil
import sqlite3

from . import storage

_BOOK_TYPES = (
    ("paper", "paper_books.db", "paper_state_", ".json"),
    ("fx", "fx_books.db", "fx_state_", ".json"),
)
_DROP_ROW_KEYS = ("equity_history", "sleeve_history")


def _is_nonfinite(value) -> bool:
    return isinstance(value, float) and not math.isfinite(value)


def _count_nonfinite(value) -> int:
    if _is_nonfinite(value):
        return 1
    if isinstance(value, dict):
        return sum(_count_nonfinite(item) for item in value.values())
    if isinstance(value, list):
        return sum(_count_nonfinite(item) for item in value)
    return 0


def _null_nonfinite(value):
    if _is_nonfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _null_nonfinite(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_null_nonfinite(item) for item in value]
    return value


def repair_state(state: dict) -> tuple[dict, int]:
    """Return a strict-JSON-safe copy and the number of repaired values."""
    repaired = dict(state)
    repaired_count = 0
    for key in _DROP_ROW_KEYS:
        rows = repaired.get(key)
        if not isinstance(rows, list):
            continue
        kept = []
        for row in rows:
            count = _count_nonfinite(row)
            repaired_count += count
            if count == 0:
                kept.append(row)
        repaired[key] = kept

    remaining = _count_nonfinite(repaired)
    repaired_count += remaining
    repaired = _null_nonfinite(repaired)
    json.dumps(repaired, allow_nan=False)
    return repaired, repaired_count


def _json_accounts(state_dir: Path, prefix: str, suffix: str) -> set[str]:
    return {
        path.name[len(prefix):-len(suffix)]
        for path in state_dir.glob(f"{prefix}*{suffix}")
    }


def _backup_database(source: Path, target: Path) -> None:
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as src:
        with sqlite3.connect(target) as dst:
            src.backup(dst)


def _backup_state_files(state_dir: Path, backup_dir: Path) -> None:
    backup_dir.mkdir(parents=True, exist_ok=True)
    if any(backup_dir.iterdir()):
        raise ValueError(f"backup directory must be empty: {backup_dir}")

    for _kind, db_name, prefix, suffix in _BOOK_TYPES:
        db_path = state_dir / db_name
        if db_path.exists():
            _backup_database(db_path, backup_dir / db_name)
        for path in state_dir.glob(f"{prefix}*{suffix}"):
            shutil.copy2(path, backup_dir / path.name)


def repair_directory(state_dir: Path, backup_dir: Path) -> tuple[int, int]:
    """Back up, repair and dual-write all persisted accounts under state_dir."""
    state_dir = state_dir.resolve()
    backup_dir = backup_dir.resolve()
    books: list[tuple[str, Path, str, Path]] = []
    accounts_to_lock: set[str] = set()

    for kind, db_name, prefix, suffix in _BOOK_TYPES:
        db_path = state_dir / db_name
        accounts = _json_accounts(state_dir, prefix, suffix)
        accounts.update(storage.db_accounts(str(db_path)))
        for account in sorted(accounts):
            books.append((kind, db_path, account, state_dir / f"{prefix}{account}{suffix}"))
            accounts_to_lock.add(account)

    with contextlib.ExitStack() as stack:
        for account in sorted(accounts_to_lock):
            stack.enter_context(storage.account_lock(account, lock_dir=str(state_dir)))
        _backup_state_files(state_dir, backup_dir)

        total_values = 0
        changed_books = 0
        for kind, db_path, account, json_path in books:
            state = storage.db_load(str(db_path), account)
            if state is None:
                if not json_path.exists():
                    continue
                state = json.loads(json_path.read_text())
            repaired, count = repair_state(state)
            if count == 0:
                continue
            storage.db_save(str(db_path), account, repaired)
            storage.atomic_write_json(str(json_path), repaired)
            total_values += count
            changed_books += 1
            print(f"{kind}/{account}: repaired {count} non-finite values")

    return changed_books, total_values


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--backup-dir", required=True)
    args = parser.parse_args(argv)

    try:
        books, values = repair_directory(Path(args.state_dir), Path(args.backup_dir))
    except (OSError, ValueError, sqlite3.Error, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(f"complete: repaired {values} non-finite values across {books} books")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
