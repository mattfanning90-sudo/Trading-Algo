"""The book screens in a real headless browser, against the frozen books.
Skipped where Playwright/Chromium are missing — unless REQUIRE_BROWSER=1, which
CI's `ui` job sets, so a skip can never hide a broken screen there."""
import os
import threading

import pytest

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    if os.environ.get("REQUIRE_BROWSER"):
        raise
    pytest.skip("Playwright not installed (REQUIRE_BROWSER=1 makes this fail)",
                allow_module_level=True)


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
    above = page.evaluate(
        "[...document.querySelector('.story').parentElement.children]"
        ".filter(e => e.compareDocumentPosition(document.querySelector('.story')) & 4)"
        ".map(e => e.innerText).join(' ')")
    assert above.strip()
    text = page.locator(".attn .main-problem").inner_text().replace("Main problem", "")
    text = text.split("Source:")[0].strip().rstrip(".")
    assert len(text) > 20
    assert above.count(text) == 1, (text, above)


def _order(page, selectors):
    return page.evaluate(
        """sels => { const all = [...document.querySelectorAll(sels.join(','))];
          return all.map(e => e.closest('.attn') ? '.attn' : sels.find(s => e.matches(s))); }""", selectors)


def _first_seen(seq):
    out = []
    for x in seq:
        if x not in out:
            out.append(x)
    return out


SPEC_ORDER = [".pill", ".attn", ".result", ".main-problem", ".story", ".tiles"]


@pytest.mark.parametrize("key", ["FULL", "MATT"])
def test_overview_order_matches_the_spec(browser, app, key):
    page, _ = open_book(browser, app, key)
    seen = _first_seen(_order(page, SPEC_ORDER))
    if key == "FULL":
        assert ".attn" in seen
    # .main-problem inside .attn is the same element position as .attn
    expected = [s for s in SPEC_ORDER if s in seen]
    assert seen == expected, seen


def test_marks_polling_does_not_stack(browser, app):
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    # The fixture app is offline, so its summary says "offline" and nothing would
    # poll at all; the summary must say "idle" for this test to measure anything.
    calls = {"summary": [], "marks": [], "positions": []}
    _stub_market(page, {"market": "idle", "marks": "pending"}, calls)
    hits = calls["marks"]
    page.goto(f"{app}/home")
    page.wait_for_selector("#sidebar button.row")
    page.click('#sidebar button.row[data-key="FULL"]')
    page.wait_for_selector(".result")
    page.wait_for_timeout(300)
    for _ in range(2):
        page.click('[role="tab"][data-tab="positions"]')
        page.click('[role="tab"][data-tab="overview"]')
    page.wait_for_timeout(300)
    n = len(hits)
    assert n >= 1, "nothing polled, so this test measures nothing"
    page.wait_for_timeout(6000)
    # one live chain => at most 2 more polls in 6 s (5 s spacing); stacked chains => >= 4
    assert len(hits) - n <= 2, (n, len(hits))


def _stub_market(page, st, hits):
    """The summary reports st["market"]; /api/marks reports st["marks"]; every
    request is counted in hits[route-name]."""
    def summary(route):
        hits["summary"].append(1)
        resp = route.fetch()
        body = resp.json()
        body["market"] = st["market"]
        route.fulfill(response=resp, json=body)

    def marks(route):
        hits["marks"].append(1)
        route.fulfill(content_type="application/json", body='{"status": "%s"}' % st["marks"])

    def positions(route):
        hits["positions"].append(1)
        route.continue_()

    page.route("**/api/summary/*", summary)
    page.route("**/api/marks/*", marks)
    page.route("**/api/positions/*", positions)


def _until(page, cond, ms=3000):
    for _ in range(ms // 50):
        if cond():
            return True
        page.wait_for_timeout(50)
    return cond()


def test_a_new_price_fetch_gets_a_new_poll_budget(browser, app):
    """The poll budget is per fetch: one spent on an earlier fetch must not stop
    the 5-minute refresh from polling a new one (the screen read "market
    comparison loading" forever), and tab switches during one fetch must not
    refill it."""
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    st, hits = {"market": "idle", "marks": "pending"}, {"summary": [], "marks": [], "positions": []}
    _stub_market(page, st, hits)
    page.goto(f"{app}/home")
    page.wait_for_selector("#sidebar button.row")
    page.click('#sidebar button.row[data-key="FULL"]')
    page.wait_for_selector(".result")
    assert _until(page, lambda: len(hits["marks"]) >= 1)
    page.evaluate("marksTries.FULL = 1000")              # the first fetch spent its budget
    n = len(hits["marks"])
    page.click('[role="tab"][data-tab="positions"]')
    page.click('[role="tab"][data-tab="overview"]')
    page.wait_for_timeout(500)
    assert len(hits["marks"]) == n, "a tab switch refilled the budget mid-fetch"
    page.evaluate("refresh(false)")                      # the 5-minute refresh; the server is idle again
    assert _until(page, lambda: len(hits["marks"]) > n), "the new fetch was never polled"


def test_positions_refetch_after_prices_arrive_on_the_overview(browser, app):
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    st, hits = {"market": "idle", "marks": "pending"}, {"summary": [], "marks": [], "positions": []}
    _stub_market(page, st, hits)
    page.goto(f"{app}/home")
    page.wait_for_selector("#sidebar button.row")
    page.click('#sidebar button.row[data-key="FULL"]')
    page.wait_for_selector(".result")
    page.click('[role="tab"][data-tab="positions"]')
    page.wait_for_selector(".group")
    assert len(hits["positions"]) == 1
    st.update(market="ready", marks="ready")             # today's prices land...
    n = len(hits["summary"])
    page.click('[role="tab"][data-tab="overview"]')      # ...while the Overview is showing
    assert _until(page, lambda: len(hits["summary"]) > n)
    page.wait_for_timeout(200)
    page.click('[role="tab"][data-tab="positions"]')
    assert _until(page, lambda: len(hits["positions"]) == 2), "Positions kept the pre-price holdings"


def test_the_poll_budget_outlasts_a_hung_fetch():
    """A chain must see a hung fetch turn to "error" (HANG_AFTER), not give up first."""
    import re
    from pathlib import Path
    from trading_algo.dashboard.live_marks import HANG_AFTER
    js = (Path(__file__).parents[1] / "trading_algo/dashboard/static/home.js").read_text()
    polls = int(re.search(r"const MARKS_POLLS = (\d+);", js).group(1))
    every = int(re.search(r"const MARKS_EVERY_MS = (\d+);", js).group(1))
    assert polls * every / 1000 > HANG_AFTER


def test_polling_code_guards_generation_and_budget():
    from pathlib import Path
    js = (Path(__file__).parents[1] / "trading_algo/dashboard/static/home.js").read_text()
    assert "marksGen" in js and "marksTries" in js


def test_dshow_leaves_unparseable_dates_alone(browser):
    from pathlib import Path
    page = browser.new_page()
    page.set_content("<html></html>")
    page.add_script_tag(path=str(Path(__file__).parents[1] / "trading_algo/dashboard/static/blocks.js"))
    assert page.evaluate("MB.dshow('soon')") == "soon"
    assert page.evaluate("MB.dshow('2026-06-11T01:02:03')") == "11 Jun 2026"


def test_chart_dates_read_day_month_year(browser, app):
    import re
    page, _ = open_book(browser, app, "FULL")
    cap = page.locator(".chart .caption").first.inner_text()
    assert "11 Jun 2026" in cap
    assert not re.search(r"\d{4}-\d{2}-\d{2}", cap)


def open_positions(browser, base, key):
    page, errors = open_book(browser, base, key)
    page.click('[role="tab"][data-tab="positions"]')
    page.wait_for_selector(".group")
    return page, errors


def test_full_positions_show_each_region_and_why_with_the_network_off(browser, app):
    page, errors = open_positions(browser, app, "FULL")
    assert page.locator(".group").count() == 3
    assert page.locator(".group .why").count() == 3
    assert "since the book began" in page.inner_text(".group:has-text('Australia')")
    assert page.locator(".group tbody tr").count() > 0
    assert "offline" in page.inner_text(".prices")
    assert errors == []


def test_fx_positions_group_by_asset_class(browser, app):
    page, errors = open_positions(browser, app, "MATT")
    names = page.locator(".group h3").all_inner_texts()
    assert any(n.startswith("Currencies") for n in names) and errors == []


@pytest.mark.parametrize("tab", ["OVERVIEW", "BACKTEST", "METHOD"])
def test_classic_view_opens_the_old_terminal_on_the_same_book_and_tab(browser, app, tab):
    page = browser.new_page()
    page.goto(f"{app}/index.html?account=SMALL&tab={tab}")
    page.wait_for_function("typeof S !== 'undefined' && S.account === 'SMALL'")
    assert page.evaluate("S.tab") == tab


def _rgb(css):
    import re
    r, g, b = (int(x) for x in re.findall(r"\d+", css)[:3])
    return r, g, b


def _contrast(a, b):
    def lum(c):
        def ch(v):
            v /= 255
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        r, g, b_ = (ch(x) for x in c)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b_
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


PANEL_BG = (19, 22, 30)      # --bg #0a0d14 under the 3.5% white panel, rounded up


def test_text_contrast_in_tiles_and_month_grid(browser, app):
    page, _ = open_book(browser, app, "FULL")
    for sel in (".tile .tv", ".tile .tc", ".month .mv", ".month .ml"):
        for colour in page.eval_on_selector_all(sel, "els => els.map(e => getComputedStyle(e).color)"):
            assert _contrast(_rgb(colour), PANEL_BG) >= 4.5, (sel, colour)


def test_every_money_move_has_an_arrow(browser, app):
    page, _ = open_book(browser, app, "FULL")
    # Worth and Costs are amounts; Since start and each closed result are moves.
    moves = page.eval_on_selector_all(
        ".tile", "els => els.filter(e => e.querySelector('.tl').innerText === 'Since start')"
                 ".map(e => e.querySelector('.tv').innerText)")
    moves += page.locator(".tbl td:last-child").all_inner_texts()
    assert moves and all(t.strip()[0] in "▲▼▶" for t in moves), moves
    assert page.inner_text(".result")[0] in "▲▼▶"


def test_focus_survives_a_refresh_and_only_the_status_is_live(browser, app):
    page, _ = open_book(browser, app, "FULL")
    page.focus('[role="tab"][data-tab="overview"]')
    page.evaluate("async () => { await refresh(false); await loadBook(selected, true); }")
    assert page.evaluate("document.activeElement.dataset.focus") == "tab:overview"
    live = page.eval_on_selector_all("[aria-live], [role=status]", "els => els.map(e => e.className)")
    assert live, "no live region found at all"
    assert all("pill" in c for c in live), live


def test_the_chart_has_a_text_alternative_and_patterned_lines(browser, app):
    page, _ = open_book(browser, app, "FULL")
    assert len(page.text_content(".chart svg desc")) > 20
    # the break-even line is the one dotted line; a benchmark, when drawn, is a long-dash polyline
    assert page.locator('.chart svg line[stroke-dasharray="1 4"]').count() == 1
    for d in page.eval_on_selector_all(".chart svg polyline[stroke-dasharray]",
                                       "els => els.map(e => e.getAttribute('stroke-dasharray'))"):
        assert d == "8 4"
