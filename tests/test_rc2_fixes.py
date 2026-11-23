"""Regression tests for defects found while re-qualifying 2.1.0-rc1 (fixed in rc2)."""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings, trusted_proxies
from app.db import initialize, transaction
from app.security import create_user
from app.worker import Worker
from .conftest import PASSWORD, csv_file, submit

ROOT = Path(__file__).resolve().parent.parent


def login(client, address, password=PASSWORD):
    return client.post('/api/auth/login', json={'email': address, 'password': password})


def test_successful_sign_ins_do_not_exhaust_the_account_throttle(env):
    c = TestClient(env['app'], base_url=env['settings'].origin)
    # rc1 locked a user out after ten correct sign-ins in fifteen minutes.
    assert [login(c, 'viewer@example.test').status_code for _ in range(15)] == [200] * 15


def test_failures_still_count_after_an_intervening_success(env):
    c = TestClient(env['app'], base_url=env['settings'].origin)
    for _ in range(9): assert login(c, 'viewer@example.test', 'wrong-password').status_code == 401
    assert login(c, 'viewer@example.test').status_code == 200  # does not forgive earlier guesses
    assert login(c, 'viewer@example.test', 'wrong-password').status_code == 401  # tenth failure
    assert login(c, 'viewer@example.test', 'wrong-password').status_code == 429


@pytest.mark.parametrize('value', ['*', '127.0.0.1,*', 'proxy.internal'])
def test_trusted_proxies_reject_wildcards_and_names(monkeypatch, value):
    monkeypatch.setenv('DATAFORGE_TRUSTED_PROXIES', value)
    with pytest.raises(ValueError):
        trusted_proxies()


def _free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def _serve(tmp_path, trusted):
    data = tmp_path / ('data-' + ('trusted' if trusted else 'direct'))
    port = _free_port()
    origin = f'http://127.0.0.1:{port}'
    settings = Settings(data, origin)
    initialize(settings)
    with transaction(settings, True) as db:
        for i in range(70): create_user(db, f'staff{i}@example.test', 'Staff', PASSWORD)
    env = {**os.environ, 'DATAFORGE_DATA_DIR': str(data), 'DATAFORGE_ORIGIN': origin}
    env.pop('DATAFORGE_TRUSTED_PROXIES', None)
    if trusted: env['DATAFORGE_TRUSTED_PROXIES'] = '127.0.0.1'
    proc = subprocess.Popen([sys.executable, '-m', 'app.cli', 'serve', '--port', str(port)], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            if httpx.get(origin + '/health/live', timeout=1).status_code == 200: return proc, origin
        except httpx.HTTPError:
            time.sleep(.1)
    proc.kill(); raise RuntimeError('API did not start')


def _sign_in_many(origin, forwarded):
    codes = []
    with httpx.Client(base_url=origin, timeout=10) as c:
        for i in range(70):
            headers = {'X-Forwarded-For': forwarded(i)}
            codes.append(c.post('/api/auth/login', json={'email': f'staff{i}@example.test', 'password': PASSWORD}, headers=headers).status_code)
    return codes


def test_real_server_uses_proxy_client_address_only_when_trusted(tmp_path):
    # Behind the reverse proxy: 70 staff members on distinct addresses must all sign in.
    proc, origin = _serve(tmp_path, trusted=True)
    try:
        assert _sign_in_many(origin, lambda i: f'198.51.100.{i + 1}') == [200] * 70
    finally:
        proc.terminate(); proc.wait(20)
    # Without a trusted proxy a client cannot spoof X-Forwarded-For to dodge the IP bucket
    # (and successful sign-ins no longer consume it either).
    proc, origin = _serve(tmp_path, trusted=False)
    try:
        with httpx.Client(base_url=origin, timeout=10) as c:
            codes = [c.post('/api/auth/login', json={'email': f'staff{i}@example.test', 'password': 'wrong-password-value'},
                            headers={'X-Forwarded-For': f'203.0.113.{i + 1}'}).status_code for i in range(61)]
        assert codes[:60] == [401] * 60 and codes[60] == 429
    finally:
        proc.terminate(); proc.wait(20)


def _ready_environment(tmp_path, monkeypatch):
    import importlib.metadata
    original = importlib.metadata.version
    monkeypatch.setattr(importlib.metadata, 'version', lambda name: '0.163.2' if name == 'fastapi' else original(name))
    keys = tmp_path / 'keys'; keys.mkdir(mode=0o700)
    app_key = keys / 'application.key'; app_key.write_bytes(os.urandom(32)); app_key.chmod(0o600)
    backup_key = keys / 'backup.key'; backup_key.write_bytes(os.urandom(32)); backup_key.chmod(0o600)
    settings = Settings(tmp_path / 'data', 'https://dataforge.example.test', True, require_mfa=True, secret_key_file=app_key, production=True)
    initialize(settings)
    monkeypatch.setenv('DATAFORGE_BACKUP_KEY_FILE', str(backup_key))
    monkeypatch.setenv('DATAFORGE_BACKUP_DIRECTORY', str(tmp_path / 'backups'))
    monkeypatch.setenv('DATAFORGE_ALERT_WEBHOOK', 'https://alerts.example.test/hook')
    monkeypatch.setenv('DATAFORGE_TRUSTED_PROXIES', '127.0.0.1')
    gates = ('browser_origin', 'dependency_audit', 'restore_drill', 'security_review', 'business_acceptance',
             'capacity_test', 'offhost_backup', 'alert_delivery', 'storage_encryption')
    qualification = tmp_path / 'qualification.json'
    qualification.write_text(json.dumps({g: {'status': 'approved', 'evidence': 'ticket-' + g} for g in gates}))
    monkeypatch.setenv('DATAFORGE_QUALIFICATION_FILE', str(qualification))
    return settings, qualification


def test_fully_configured_production_preflight_passes(tmp_path, monkeypatch):
    from app.preflight import check
    settings, _ = _ready_environment(tmp_path, monkeypatch)
    report = check(settings)
    assert report['ready'], [r for r in report['checks'] if not r['passed']]


def test_preflight_requires_trusted_proxy_configuration(tmp_path, monkeypatch):
    from app.preflight import check
    settings, _ = _ready_environment(tmp_path, monkeypatch)
    monkeypatch.delenv('DATAFORGE_TRUSTED_PROXIES')
    flags = {r['check']: r['passed'] for r in check(settings)['checks']}
    assert flags['client_addresses'] is False


@pytest.mark.parametrize('content', ['"approved"', '[]', '{"browser_origin":"approved"}', '{"browser_origin":{"status":"approved","evidence":""}}'])
def test_malformed_qualification_file_fails_closed_without_crashing(tmp_path, monkeypatch, content):
    from app.preflight import check
    settings, qualification = _ready_environment(tmp_path, monkeypatch)
    qualification.write_text(content)
    report = check(settings)
    assert not report['ready']
    assert {r['check']: r['passed'] for r in report['checks']}['deployment_qualification'] is False


def test_runner_killed_by_resource_limit_reports_a_clear_error(env, monkeypatch):
    import app.worker as worker_module
    real_popen = subprocess.Popen

    def killed_runner(command, **kwargs):
        # Same pipes and environment, but the child dies without writing a report.
        return real_popen([sys.executable, '-c', 'import os,sys; sys.stdin.buffer.read(); os.kill(os.getpid(), 9)'], **kwargs)

    monkeypatch.setattr(worker_module.subprocess, 'Popen', killed_runner)
    r = submit(env, sql='SELECT 1 AS value', key='killed-runner-0001'); assert r.status_code == 201
    Worker(env['settings']).once()
    job = env['clients']['editor'].get(f"/api/workspaces/{env['ws']}/jobs/{r.json()['job']['id']}").json()
    assert job['status'] == 'failed'
    assert 'CPU or memory limit' in job['error'] and 'Expecting value' not in job['error']
