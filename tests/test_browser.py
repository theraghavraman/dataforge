"""Browser smoke tests for UI/repository behaviors that do not require a working DuckDB CDN.

These intentionally do not assert successful ETL execution; a separate mirrored-DuckDB
integration suite is needed to test that reliably.
"""
from contextlib import contextmanager
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from threading import Thread

import pytest

playwright = pytest.importorskip('playwright.sync_api')
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def site_url():
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(ROOT), **kwargs)
        def log_message(self, *args):
            pass

    srv = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = Thread(target=srv.serve_forever, daemon=True)
    worker.start()
    try:
        yield f'http://127.0.0.1:{srv.server_port}/index.html'
    finally:
        srv.shutdown()
        srv.server_close()
        worker.join(timeout=3)


@pytest.fixture
def page(site_url):
    with sync_playwright() as browser_engine:
        browser = browser_engine.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 900}, accept_downloads=True)
        tab = context.new_page()
        errors = []
        tab.on('pageerror', lambda error: errors.append(str(error)))
        tab.goto(site_url, wait_until='domcontentloaded')
        expect(tab.locator('#repoNav')).to_be_attached(timeout=15000)
        yield tab
        assert not errors, 'Uncaught browser errors: ' + '; '.join(errors)
        context.close()
        browser.close()


def test_sample_menu_and_reload(page):
    page.locator('#btnSampleMenu').click()
    expect(page.locator('#sampleMenu')).to_be_visible()
    page.locator('[data-sample="original"]').click()
    expect(page.locator('#welcomeStage')).to_be_hidden()
    expect(page.locator('#world .node').first).to_be_visible()
    assert 'file required' not in page.locator('#world').inner_text().lower()
    page.wait_for_timeout(800)  # allow IndexedDB's debounced write to complete
    page.reload(wait_until='domcontentloaded')
    expect(page.locator('#repoNav')).to_be_attached(timeout=15000)
    expect(page.locator('#world .node').first).to_be_visible()
    assert 'file required' not in page.locator('#world').inner_text().lower()


def test_new_workflow_and_monitor_shortcut_guard(page):
    page.locator('#studioMode').select_option('workflow')
    page.locator('#btnNew').click()
    expect(page.locator('#studioMode')).to_have_value('workflow')
    expect(page.locator('#wfCanvas .wf-node')).to_have_count(1)
    page.locator('#studioMode').select_option('monitor')
    page.keyboard.press('Control+Enter')
    expect(page.locator('#studioMode')).to_have_value('monitor')


def test_export_project_download(page):
    with page.expect_download(timeout=15000) as download:
        page.get_by_role('button', name='Export Project', exact=True).click()
    assert download.value.suggested_filename.endswith('.dfproj.json')
