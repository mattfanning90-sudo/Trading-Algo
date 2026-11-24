"""Which audit problems Matt has already seen.

A WARN/ERROR finding is NEW during the app session in which it is first
observed (or in which it became more severe) and KNOWN in every later session.
Identity is (book, code, subject) — never the message, whose wording changes
daily ("77 days" -> "78 days"). On the first run, or when the file is missing
or unreadable, everything current is recorded as known: losing this file must
never flood the screen with amber. INFO findings are never tracked.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

VERSION = 1
_TRACKED = ("WARN", "ERROR")
_RANK = {"INFO": 0, "WARN": 1, "ERROR": 2}


def key(f: dict) -> str:
    return f"{f['book']}|{f['code']}|{f.get('subject') or ''}"


def _set_aside(path: Path) -> None:
    try:
        os.replace(path, path.with_name(path.name + ".unreadable"))
    except OSError:
        pass


def _load(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        _set_aside(path)
        return None
    if not isinstance(data, dict) or data.get("v") != VERSION:
        _set_aside(path)
        return None
    data.setdefault("first_seen", {})
    return data


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
    os.replace(tmp, path)


def begin_session(path: Path, now: datetime) -> str | None:
    data = _load(path) or {"v": VERSION, "first_seen": {}}
    previous = data.get("last_opened")
    data["last_opened"] = now.isoformat()
    _save(path, data)
    return previous


def classify(findings: list[dict], *, path: Path, session_start: str,
             first_run: bool, now: datetime) -> list[dict]:
    data = _load(path)
    baseline = first_run or data is None
    if data is None:
        data = {"v": VERSION, "first_seen": {}, "last_opened": session_start}
    first_seen = data["first_seen"]
    stamp = now.isoformat()
    out: list[dict] = []
    for finding in findings:
        g = dict(finding)
        g["new"] = False
        if finding["level"] in _TRACKED:
            k = key(finding)
            prev = first_seen.get(k)
            if prev is None:
                first_seen[k] = {"at": stamp, "level": finding["level"],
                                 "baseline": baseline}
                g["new"] = not baseline
            elif _RANK[finding["level"]] > _RANK.get(prev["level"], 0):
                first_seen[k] = {"at": stamp, "level": finding["level"],
                                 "baseline": False}
                g["new"] = True
            else:
                g["new"] = (not prev.get("baseline")) and prev["at"] >= session_start
        out.append(g)
    _save(path, data)
    return out
