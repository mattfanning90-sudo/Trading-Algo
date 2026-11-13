"""The book screens in a real headless browser, against the frozen books.
Skipped where Playwright/Chromium are missing — unless REQUIRE_BROWSER=1, which
CI's `ui` job sets, so a skip can never hide a broken screen there."""
import os
import shutil
import threading

import pytest

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    if os.environ.get("REQUIRE_BROWSER"):
        raise
    pytest.skip("Playwright not installed (REQUIRE_BROWSER=1 makes this fail)",
                allow_module_level=True)

from book_fixtures import FIXTURE

BOOKS = ["FULL", "MATT", "EXPERIMENTAL", "SMALL"]


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # browser binary missing
            if os.environ.get("REQUIRE_BROWSER"):
                raise
            pytest.skip(f"Chromium not available: {exc}")
        yield b
        b.close()


@pytest.fixture()
def app(tmp_path, monkeypatch):
    from trading_algo.home import __main__ as launcher, jobs, sync
    from trading_algo.home.paths import AppPaths

    import book_fixtures
    state = book_fixtures.use_snapshot(tmp_path, monkeypatch)
    book_fixtures.no_network(monkeypatch)
    monkeypatch.setattr(jobs, "fetch_runs", lambda *a, **k: [])
    paths = AppPaths(tmp_path / "app")
    paths.snapshots.mkdir(parents=True)
    paths.current.symlink_to(state)
    first = sync.SyncResult("fixture", "2026-10-13T06:45:11Z", False, True, "test")
    httpd = launcher.make_app(paths, first, port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def open_book(browser, base, key, width=1280):
    page = browser.new_page(viewport={"width": width, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base}/home")
    page.wait_for_selector("#sidebar button.row")
    page.click(f'#sidebar button.row[data-key="{key}"]')
    page.wait_for_selector(".result")
    return page, errors


@pytest.mark.parametrize("key", BOOKS)
def test_overview_has_every_part(browser, app, key):
    page, errors = open_book(browser, app, key)
    assert page.locator(".pill").count() == 1
    assert page.locator(".main-problem").count() == 1
    assert page.locator(".story [data-item]").count() >= 2
    assert page.locator(".tile").count() == 6
    assert page.locator(".vcol").count() == 3
    assert errors == []


def test_links_open_the_old_terminal_on_this_book_and_tab(browser, app):
    page, _ = open_book(browser, app, "FULL")
    assert page.get_attribute('a.ext[data-link="classic"]', "href") == "index.html?account=FULL&tab=OVERVIEW"
    assert page.get_attribute('a.ext[data-link="backtest"]', "href") == "index.html?account=FULL&tab=BACKTEST"
    assert page.locator('a.ext[data-link="swarm"]').count() == 0          # equity book


def test_tabs_follow_the_keyboard_pattern(browser, app):
    page, _ = open_book(browser, app, "MATT")
    page.focus('[role="tab"][data-tab="overview"]')
    page.keyboard.press("ArrowRight")
    page.wait_for_selector('[role="tab"][data-tab="positions"][aria-selected="true"]')
    assert page.evaluate("document.activeElement.dataset.focus") == "tab:positions"


def test_main_problem_is_said_once(browser, app):
    page, _ = open_book(browser, app, "FULL")
    assert page.locator(".attn .main-problem").count() == 1
    assert page.locator(".main-problem").count() == 1
    text = page.locator(".attn .main-problem").inner_text()
    body = text.replace("Main problem", "").strip().split(".")[0]
    above = page.evaluate(
        "[...document.querySelector('.result').parentElement.children]"
        ".filter(e => e.compareDocumentPosition(document.querySelector('.story')) & 4)"
        ".map(e => e.innerText).join(' ')")
    assert above.count(body) == 1


def test_chart_dates_read_day_month_year(browser, app):
    import re
    page, _ = open_book(browser, app, "FULL")
    cap = page.locator(".chart .caption").first.inner_text()
    assert "11 Jun 2026" in cap
    assert not re.search(r"\d{4}-\d{2}-\d{2}", cap)
