import pathlib

# NOTE: the task brief hardcoded an absolute path into the OTHER local checkout
# (/Users/matthewfanning/Trading-Algo, the main-branch worktree this task is
# forbidden from touching) — that path also wouldn't resolve in CI, where the
# repo is checked out under $GITHUB_WORKSPACE, not a local dev machine path.
# Use the repo-relative path instead, matching tests/test_workflow_sanity.py.
WF = pathlib.Path(__file__).resolve().parents[1] / ".github" / "workflows" / "fx-swarm.yml"


def test_swarm_workflow_exists_and_has_required_shape():
    text = WF.read_text()
    assert "schedule:" in text and "cron:" in text
    assert "contents: write" in text                     # commit state back
    assert "trading_algo.forex.evolve" in text           # breeder step
    assert "trading_algo.forex.champions" in text         # promotion step
    assert "swarm_log_" in text or "state/" in text       # commits state
    assert "[skip ci]" in text                            # no CI storm on the state commit
    assert "group: github-pages" in text                  # shares the Pages serialiser
