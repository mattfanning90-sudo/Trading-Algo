"""Textual (actionlint-style) sanity checks on the GitHub workflow YAML.

Per the workstream instructions these are plain-text grep-style assertions on
the raw YAML, runnable under `pytest -q` — workflow SEMANTICS (actual cache
hits, deploy serialisation, push races) are not executable offline, so we lint
the text that encodes the agreed publishing scheme instead:

* ONE rebase-safe push block in every workflow that pushes state — abort any
  in-progress rebase before every retry, no merge-commit `git pull`, no
  swallowed failures;
* ONE `github-pages` concurrency group across exactly the two audited Pages
  publishers (day-paper + fx-paper);
* day-paper's cron never fires at hour 23 (fx-paper's nightly owns it);
* ONE shared site builder (scripts/build_site.sh) with defensive env defaults,
  no `dashboard --all` (its build_index output was being clobbered), and a
  per-account FX export loop;
* the parquet cache persisted via actions/cache in both site publishers.
"""
import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]
WF_DIR = ROOT / ".github" / "workflows"
BUILD_SITE = ROOT / "scripts" / "build_site.sh"

# The two Pages publishers (audited as the COMPLETE set — backtest.yml and
# fx-train.yml have no deploy-pages jobs) and the three state committers.
SITE_WORKFLOWS = ("day-paper.yml", "fx-paper.yml")
STATE_WORKFLOWS = ("day-paper.yml", "fx-paper.yml", "paper-trade.yml")


def _wf(name: str) -> str:
    return (WF_DIR / name).read_text(encoding="utf-8")


def _all_workflows() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8")
            for p in sorted(WF_DIR.glob("*.yml"))}


# ---------------------------------------------------------------------------
# Issues 3 + 9 — rebase-safe push block in every state-committing workflow
# ---------------------------------------------------------------------------
def test_every_pushing_workflow_aborts_rebase_before_retry():
    pushing = {n: t for n, t in _all_workflows().items() if "git push" in t}
    # the three known committers must all push (and be covered below)
    assert set(STATE_WORKFLOWS) <= set(pushing)
    for name, text in pushing.items():
        assert "rebase --abort" in text, (
            f"{name} pushes without aborting a possibly-in-progress rebase")


def test_state_workflows_carry_the_full_rebase_safe_block():
    for name in STATE_WORKFLOWS:
        text = _wf(name)
        assert "git fetch origin" in text, name
        assert "git rebase origin/" in text, name
        assert "git rebase -X theirs origin/" in text, name


def test_old_fragile_pull_pattern_is_gone():
    for name, text in _all_workflows().items():
        assert "git pull --rebase origin ${{ github.ref_name }} || true" not in text, name
        # no merge commits: the scheme never uses `git pull` at all
        assert "git pull" not in text, name


def test_push_failure_is_not_swallowed():
    for name in STATE_WORKFLOWS:
        text = _wf(name)
        assert "git push || true" not in text, name
        assert re.search(r"git push \|\| \(", text) is None, name


# ---------------------------------------------------------------------------
# Issue 37 — one github-pages concurrency group; day-paper skips hour 23
# ---------------------------------------------------------------------------
def test_both_deploy_jobs_share_the_pages_concurrency_group():
    for name in SITE_WORKFLOWS:
        assert "group: github-pages" in _wf(name), name


def test_no_other_workflow_claims_the_pages_group():
    # locks the audited two-publisher set
    for name, text in _all_workflows().items():
        if name not in SITE_WORKFLOWS:
            assert "group: github-pages" not in text, name


def test_day_paper_cron_never_fires_at_hour_23():
    text = _wf("day-paper.yml")
    assert re.search(r'cron:\s*"7 0-22 \* \* 1-5"', text), (
        "day-paper must fire hourly 00-22 only; hour 23 belongs to fx-paper")
    # rationale comment within a few lines above the cron line
    lines = text.splitlines()
    cron_i = next(i for i, ln in enumerate(lines) if "0-22" in ln)
    context = "\n".join(lines[max(0, cron_i - 6):cron_i])
    assert "fx-paper" in context, "rationale comment above the cron is missing"


# ---------------------------------------------------------------------------
# Issues 27 + 35 — one shared site builder, no clobbered build_index
# ---------------------------------------------------------------------------
def test_build_site_script_exists_and_parses():
    assert BUILD_SITE.exists()
    # bash syntax check (subprocess assertion per the test spec)
    proc = subprocess.run(["bash", "-n", str(BUILD_SITE)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_workflows_delegate_site_build_to_the_shared_script():
    for name in SITE_WORKFLOWS:
        text = _wf(name)
        assert "build_site.sh" in text, name
        assert "> public/index.html" not in text, (
            f"{name} still carries its own landing-page heredoc")


def test_build_site_step_carries_the_env_contract():
    """The step invoking build_site.sh must carry the envs the replaced steps
    had (NEWS_API_KEY + MOMENTUM_STATE_DIR) — textual proximity within the step."""
    for name in SITE_WORKFLOWS:
        lines = _wf(name).splitlines()
        run_i = next(i for i, ln in enumerate(lines)
                     if "bash scripts/build_site.sh" in ln)
        step = "\n".join(lines[max(0, run_i - 12):run_i + 1])
        assert "NEWS_API_KEY" in step, name
        assert "MOMENTUM_STATE_DIR" in step, name


def test_build_site_script_has_defensive_env_defaults():
    text = BUILD_SITE.read_text(encoding="utf-8")
    assert ":-state}" in text          # FX_STATE_DIR / MOMENTUM_STATE_DIR defaults
    assert 'SYNTH="${SYNTH:-}"' in text


def test_no_dashboard_all_in_ci_site_build():
    # `--all` runs build_index(), whose index.html the heredoc overwrote;
    # the per-account loop is the CI path, build_index stays local-only.
    assert "dashboard --all" not in BUILD_SITE.read_text(encoding="utf-8")
    for name in SITE_WORKFLOWS:
        assert "dashboard --all" not in _wf(name), name


def test_build_site_exports_fx_pages_per_account():
    text = BUILD_SITE.read_text(encoding="utf-8")
    assert "--account" in text
    assert "fx_state_" in text


# ---------------------------------------------------------------------------
# Issue 5 — parquet cache persisted across runners
# ---------------------------------------------------------------------------
def test_parquet_cache_persisted_in_both_site_workflows():
    # NOTE: actual cache-hit behaviour is not testable offline — this asserts
    # only that the actions/cache step exists with the agreed path/keys.
    for name in SITE_WORKFLOWS:
        text = _wf(name)
        assert "actions/cache" in text, name
        assert "trading_algo/forex/.cache" in text, name
        assert "restore-keys" in text, name
        assert "fx-parquet-" in text, name
