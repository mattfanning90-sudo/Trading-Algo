"""The page's assets exist, parse, and never do money arithmetic of their own."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "trading_algo" / "dashboard" / "static"


def test_assets_exist_and_are_linked():
    html = (STATIC / "home.html").read_text()
    assert 'href="home.css"' in html and 'src="home.js"' in html
    assert (STATIC / "home.css").stat().st_size > 0


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_home_js_parses():
    done = subprocess.run(["node", "--check", str(STATIC / "home.js")],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_js_takes_numbers_from_the_api_not_its_own_maths():
    """G-4: one source per number. The page may format and slice, but must not
    total or divide money (that lives in dashboard/home.py)."""
    js = (STATIC / "home.js").read_text()
    assert not re.search(r"\.reduce\(", js)
    assert "/ start" not in js and "- start" not in js
    assert "- b.initial" not in js and "- g.initial" not in js
