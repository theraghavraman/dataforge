"""Actual browser-origin gate; exit 2 means environment blocked, not passed.

No fetch bridging, route interception, custom HTML, or origin substitutions.
"""
import json
from pathlib import Path
import secrets
import tempfile
from playwright.sync_api import sync_playwright
from app.config import Settings
from app.db import initialize, transaction
from app.security import create_user
from tests.http_smoke import free_port, start
from tests.browser_support import launch_browser

ROOT = Path(__file__).resolve().parent.parent

def run():
    results = []
    report = {'method': 'Real Chromium navigation to a running HTTP application; no request bridging', 'results': results}
    with tempfile.TemporaryDirectory(prefix='df-origin-') as directory:
        settings = Settings(Path(directory)/'data', origin='http://127.0.0.1:'+str(free_port()))
        initialize(settings)
        password = secrets.token_urlsafe(24)
        with transaction(settings, True) as db:
            create_user(db, 'origin-test@example.test', 'Origin QA', password, True)
        with open(Path(directory)/'api.log', 'w') as log:
            process = start(settings, log)
            try:
                with sync_playwright() as pw:
                    browser = launch_browser(pw)
                    page = browser.new_page()
                    try:
                        page.goto(settings.origin, wait_until='networkidle', timeout=20000)
                        page.locator('#loginForm').wait_for()
                        results.append({'name': 'Application loads through the real browser origin', 'status': 'PASS'})
                        page.locator('#email').fill('origin-test@example.test')
                        page.locator('#password').fill(password)
                        page.get_by_role('button', name='Sign in', exact=False).click()
                        page.locator('#shell').wait_for(state='visible')
                        results.append({'name': 'Real cookies and CSRF permit authenticated login', 'status': 'PASS'})
                        page.reload(wait_until='networkidle')
                        page.locator('#shell').wait_for(state='visible')
                        results.append({'name': 'Authentication survives browser page reload', 'status': 'PASS'})
                        page.get_by_role('button', name='Sign out', exact=True).click()
                        page.locator('#loginForm').wait_for()
                        results.append({'name': 'Browser sign-out returns to the unauthenticated view', 'status': 'PASS'})
                        report['status'] = 'PASS'
                    except Exception as exc:
                        blocked = 'ERR_BLOCKED_BY_ADMINISTRATOR' in str(exc)
                        report['status'] = 'BLOCKED' if blocked else 'FAIL'
                        report['error'] = str(exc)
                    finally:
                        browser.close()
            finally:
                process.terminate()
                process.wait(timeout=10)
    (ROOT/'evidence'/'origin-smoke.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0 if report.get('status') == 'PASS' else 2 if report.get('status') == 'BLOCKED' else 1

if __name__ == '__main__':
    raise SystemExit(run())
