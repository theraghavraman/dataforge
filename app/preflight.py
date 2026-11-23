"""Fail-closed deployment gate, not a replacement for a vulnerability scan or review."""
import importlib.metadata
import json
import os
import platform
import sqlite3
from pathlib import Path
from .identity import read_key


def check(settings):
    results=[]
    def add(name,ok,detail):results.append({'check':name,'passed':bool(ok),'detail':detail})
    def version(name):
        try:return importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:return '0'
    # Installed version is compared explicitly, never hidden behind an environment override.
    from packaging.version import Version
    fv=version('fastapi')
    add('patched_fastapi',Version(fv)>=Version('0.163.2'),'Installed '+fv+'; require >=0.163.2 (GHSA-f56c-v883-vx8r).')
    add('https',settings.secure_cookies and settings.origin.startswith('https://'),'Production requires an HTTPS origin and Secure session cookies.')
    add('mfa_policy',settings.require_mfa,'Set DATAFORGE_REQUIRE_MFA=true.')
    try:
        from .config import trusted_proxies
        proxies=trusted_proxies()
    except ValueError:proxies=[]
    add('client_addresses',bool(proxies),'TLS terminates at the reverse proxy: set DATAFORGE_TRUSTED_PROXIES to its exact address so per-client sign-in throttles do not collapse into one shared bucket.')
    add('single_host_platform',platform.system()=='Linux','Deployment templates and process limits target Linux.')
    add('sqlite_version',sqlite3.sqlite_version_info>=(3,39,0),'SQLite >=3.39 is required for RIGHT/FULL joins.')
    try:
        key_path=settings.encryption_key_path.resolve();read_key(settings)
        external=not key_path.is_relative_to(settings.data_dir.resolve())
    except (OSError,ValueError):external=False
    add('application_key',external,'Place the 32-byte application key outside the data directory with mode 0600.')
    try:
        from .backup import key_read
        bk=Path(os.getenv('DATAFORGE_BACKUP_KEY_FILE','/nonexistent'));key_read(bk)
        separate=bk.resolve()!=settings.encryption_key_path.resolve() and not bk.resolve().is_relative_to(settings.data_dir.resolve())
    except (OSError,ValueError):separate=False
    add('backup_key',separate,'Provision a separate valid backup key outside the data directory.')
    add('backup_destination',bool(os.getenv('DATAFORGE_BACKUP_DIRECTORY')),'Configure an encrypted backup destination; independently verify its off-host replication.')
    add('alerts',bool(os.getenv('DATAFORGE_ALERT_WEBHOOK') or (os.getenv('DATAFORGE_ALERT_EMAILS') and os.getenv('DATAFORGE_SMTP_HOST'))),'Configure an HTTPS webhook or SMTP alert recipients.')
    add('smtp_tls',not os.getenv('DATAFORGE_SMTP_HOST') or os.getenv('DATAFORGE_SMTP_MODE','starttls') in ('starttls','tls'),'SMTP must use certificate-verified TLS.')
    # This is an explicit human/CI evidence artifact, not an auto-generated certification.
    path=os.getenv('DATAFORGE_QUALIFICATION_FILE','')
    try:
        report=json.loads(Path(path).read_text())
        required=('browser_origin','dependency_audit','restore_drill','security_review','business_acceptance','capacity_test','offhost_backup','alert_delivery','storage_encryption')
        approved=isinstance(report,dict) and all(isinstance(report.get(k),dict) and report[k].get('status')=='approved' and bool(report[k].get('evidence')) for k in required)
    except (OSError,ValueError,KeyError,TypeError,AttributeError):approved=False
    add('deployment_qualification',approved,'Provide signed-off evidence references for browser, dependencies, recovery, security, acceptance, capacity, off-host backup, alerts and storage encryption.')
    return {'ready':all(r['passed'] for r in results),'checks':results}


def enforce(settings):
    report=check(settings)
    if not report['ready']:
        failures=', '.join(r['check'] for r in report['checks'] if not r['passed'])
        raise RuntimeError('Production startup is blocked by preflight: '+failures+'. Run python -m app.cli preflight for details.')
