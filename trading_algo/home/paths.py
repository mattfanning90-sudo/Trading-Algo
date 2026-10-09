"""Where the Mac home screen keeps its own files (never the repo's state/)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_URL = "https://github.com/mattfanning90-sudo/Trading-Algo.git"
API_RUNS = "https://api.github.com/repos/mattfanning90-sudo/Trading-Algo/actions/runs"


@dataclass(frozen=True)
class AppPaths:
    root: Path

    @property
    def repo(self) -> Path:          # private bare copy of the GitHub repo
        return self.root / "repo"

    @property
    def snapshots(self) -> Path:     # one folder of state/*.json per commit
        return self.root / "snapshots"

    @property
    def current(self) -> Path:       # symlink → the snapshot being shown
        return self.root / "current"

    @property
    def seen(self) -> Path:          # which problems Matt has already seen
        return self.root / "seen.json"

    @classmethod
    def default(cls) -> "AppPaths":
        override = os.environ.get("MOMENTUM_HOME_DIR")
        if override:
            return cls(Path(override))
        return cls(Path.home() / "Library" / "Application Support" / "Momentum")
