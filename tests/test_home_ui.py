"""The page's assets exist, load in order, parse, never collide, and never do
money arithmetic of their own."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "trading_algo" / "dashboard" / "static"
SCRIPTS = ["blocks.js", "screens/book_overview.js", "screens/book_positions.js", "home.js"]


def test_assets_exist_and_load_in_order():
    html = (STATIC / "home.html").read_text()
    srcs = re.findall(r'<script src="([^"]+)"></script>', html)
    assert srcs == SCRIPTS
    assert 'href="home.css"' in html and (STATIC / "home.css").stat().st_size > 0
    assert "aria-live" not in html.split("<main", 1)[1].split(">", 1)[0]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("name", SCRIPTS)
def test_every_script_parses(name):
    done = subprocess.run(["node", "--check", str(STATIC / name)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_no_two_scripts_declare_the_same_top_level_name():
    seen = {}
    for name in SCRIPTS:
        for m in re.finditer(r"^(?:const|let|var|function|async function|class)\s+([A-Za-z_$][\w$]*)",
                             (STATIC / name).read_text(), re.M):
            assert m.group(1) not in seen, f"{m.group(1)} in {name} and {seen.get(m.group(1))}"
            seen[m.group(1)] = name


def test_js_takes_numbers_from_the_api_not_its_own_maths():
    """No script totals or divides money (the server does, G-4 / AC-14)."""
    for name in SCRIPTS:
        js = (STATIC / name).read_text()
        assert not re.search(r"\.reduce\(", js), name
        assert "/ start" not in js and "- start" not in js, name
        assert "- b.initial" not in js and "- g.initial" not in js, name


def test_screen_files_only_use_server_text_for_money():
    for name in SCRIPTS[1:3]:
        js = (STATIC / name).read_text()
        assert not re.search(r"\.(equity|initial|net_pnl|value_base|unrealized\w*|costs)\b", js), name


def test_links_are_relative_never_root():
    for name in SCRIPTS:
        assert 'href="/' not in (STATIC / name).read_text(), name
