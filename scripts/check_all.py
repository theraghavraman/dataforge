"""Run release checks without downloading dependencies or hiding blocked gates."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--origin', action='store_true', help='Require actual browser-origin smoke gate; blocked is nonzero.')
    args = parser.parse_args()
    output = ROOT/'evidence'
    output.mkdir(exist_ok=True)
    py = sys.executable
    jobs = [
        ('python-compile', [py, '-m', 'compileall', '-q', 'app', 'tests', 'scripts'], ROOT, 'python-compile.log'),
        ('team-js-syntax', ['node', '--check', 'web/app.js'], ROOT, 'team-js-syntax.log'),
        ('bridge-js-syntax', ['node', '--check', 'web/team-bridge.js'], ROOT, 'bridge-js-syntax.log'),
        ('backend', [py, '-m', 'pytest', '-q', 'tests', '--durations=10', '--junitxml=evidence/backend-junit.xml'], ROOT, 'backend-tests.log'),
        ('http', [py, '-m', 'tests.http_smoke'], ROOT, 'http-tests.log'),
        ('team-ui', [py, '-m', 'tests.browser_ui'], ROOT, 'ui-tests.log'),
        ('business-ui', [py, '-m', 'tests.browser_ui_v2'], ROOT, 'business-ui-tests.log'),
        ('bounded-concurrency', [py, '-m', 'tests.load_smoke'], ROOT, 'load-tests.log'),
        ('package-launcher', [py, '-m', 'tests.package_smoke'], ROOT, 'package-tests.log'),
        ('designer-bridge', [py, '-m', 'tests.designer_bridge'], ROOT, 'designer-tests.log'),
        ('inherited-syntax', ['node', 'scripts/check.mjs'], ROOT/'studio', 'legacy-syntax.log'),
        ('inherited-tests', ['node', '--test', 'tests/server.test.mjs', 'tests/unit.mjs'], ROOT/'studio', 'legacy-node-tests.tap'),
    ]
    if args.origin:
        jobs.append(('actual-browser-origin', [py, '-m', 'tests.origin_smoke'], ROOT, 'origin-smoke.log'))
    report = []
    for name, command, cwd, logfile in jobs:
        print('Running '+name+'...', flush=True)
        started = time.monotonic()
        try:
            with (output/logfile).open('w') as log:
                environment = os.environ.copy()
                if name == 'backend':
                    # This project needs only built-in pytest plugins. Do not let
                    # unrelated globally installed tracing/plugins alter the run.
                    environment['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
                proc = subprocess.run(command, cwd=cwd, env=environment, stdout=log, stderr=subprocess.STDOUT, timeout=240)
            status = 'PASS' if proc.returncode == 0 else 'BLOCKED' if name == 'actual-browser-origin' and proc.returncode == 2 else 'FAIL'
            row = {'suite': name, 'status': status, 'exit_code': proc.returncode, 'seconds': round(time.monotonic()-started, 2), 'log': logfile}
        except (OSError, subprocess.TimeoutExpired) as exc:
            row = {'suite': name, 'status': 'FAIL', 'error': str(exc), 'log': logfile}
        report.append(row)
        print(name+': '+row['status'], flush=True)
    (output/'verification-run.json').write_text(json.dumps({'origin_gate_requested': args.origin, 'suites': report}, indent=2))
    return 0 if all(row['status'] == 'PASS' for row in report) else 1

if __name__ == '__main__':
    raise SystemExit(main())
