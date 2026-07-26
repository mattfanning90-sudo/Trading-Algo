"""One-shot repair of historical non-finite values in persisted paper books."""
import json
import sqlite3
import subprocess
import sys


def _seed_db(path, account, state):
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE books (account TEXT PRIMARY KEY, state TEXT NOT NULL, "
            "updated_at TEXT NOT NULL DEFAULT (datetime('now')))"
        )
        conn.execute(
            "INSERT INTO books (account, state) VALUES (?, ?)",
            (account, json.dumps(state)),
        )


def _db_state(path, account):
    with sqlite3.connect(path) as conn:
        payload = conn.execute(
            "SELECT state FROM books WHERE account = ?", (account,)
        ).fetchone()[0]
    return payload, json.loads(payload)


def test_cli_backs_up_and_repairs_json_and_sqlite_together(tmp_path):
    state_dir = tmp_path / "state"
    backup_dir = tmp_path / "backup"
    state_dir.mkdir()

    state = {
        "account": "full",
        "equity_history": [
            ["2026-07-16", 100.0],
            ["2026-07-17", float("nan")],
        ],
        "sleeve_history": [
            {"date": "2026-07-16", "US": 50.0},
            {"date": "2026-07-17", "US": float("nan")},
        ],
        "trades": [
            {"pair": "EURUSD", "indicators": {"bb_z": float("nan"), "rsi": 55.0}},
        ],
    }
    json_path = state_dir / "paper_state_full.json"
    json_path.write_text(json.dumps(state))
    db_path = state_dir / "paper_books.db"
    _seed_db(db_path, "full", state)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "trading_algo.state_repair",
            "--state-dir",
            str(state_dir),
            "--backup-dir",
            str(backup_dir),
        ],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert "full" in result.stdout
    assert "3 non-finite values" in result.stdout

    repaired_json = json.loads(json_path.read_text())
    db_payload, repaired_db = _db_state(db_path, "full")
    assert repaired_json == repaired_db
    assert repaired_json["equity_history"] == [["2026-07-16", 100.0]]
    assert repaired_json["sleeve_history"] == [{"date": "2026-07-16", "US": 50.0}]
    assert repaired_json["trades"][0]["indicators"]["bb_z"] is None
    assert "NaN" not in json_path.read_text()
    assert "NaN" not in db_payload
    json.dumps(repaired_json, allow_nan=False)

    backed_up = json.loads((backup_dir / "paper_state_full.json").read_text())
    _, backed_up_db = _db_state(backup_dir / "paper_books.db", "full")
    assert backed_up["equity_history"][1][1] != backed_up["equity_history"][1][1]
    assert backed_up_db["trades"][0]["indicators"]["bb_z"] != \
        backed_up_db["trades"][0]["indicators"]["bb_z"]
