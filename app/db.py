"""Small explicit SQLite repository. Every write uses an immediate transaction."""
import contextlib
import hashlib
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path

SCHEMA = '''
CREATE TABLE IF NOT EXISTS mfa (
 user_id TEXT PRIMARY KEY REFERENCES users(id), secret TEXT NOT NULL,
 enabled INTEGER NOT NULL DEFAULT 0, expires REAL NOT NULL, last_counter INTEGER NOT NULL DEFAULT -1);
CREATE TABLE IF NOT EXISTS recovery_codes (
 user_id TEXT NOT NULL REFERENCES users(id), code_hash TEXT NOT NULL, PRIMARY KEY(user_id,code_hash));
CREATE TABLE IF NOT EXISTS reset_tokens (
 token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS oidc_links (
 issuer TEXT NOT NULL, subject TEXT NOT NULL, user_id TEXT NOT NULL REFERENCES users(id),
 PRIMARY KEY(issuer,subject));
CREATE TABLE IF NOT EXISTS oidc_states (
 state_hash TEXT PRIMARY KEY, binding_hash TEXT NOT NULL, nonce TEXT NOT NULL,
 verifier TEXT NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS outbox (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL, dedupe TEXT UNIQUE NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
 next_at REAL NOT NULL, lease_until REAL, lease_owner TEXT, created REAL NOT NULL,
 delivered REAL, last_error TEXT);
CREATE INDEX IF NOT EXISTS ix_outbox_queue ON outbox(status,next_at);
CREATE TABLE IF NOT EXISTS project_policies (
 project_id TEXT PRIMARY KEY REFERENCES projects(id), approval_required INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS project_approvals (
 project_id TEXT NOT NULL REFERENCES projects(id), revision INTEGER NOT NULL,
 actor TEXT NOT NULL REFERENCES users(id), approved REAL NOT NULL, PRIMARY KEY(project_id,revision));

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users (
 id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
 password_hash TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0,
 active INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
 token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
 csrf TEXT NOT NULL, expires REAL NOT NULL, last_seen REAL NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS login_attempts (bucket TEXT NOT NULL, at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS ix_attempts ON login_attempts(bucket, at);
CREATE TABLE IF NOT EXISTS workspaces (id TEXT PRIMARY KEY, name TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS memberships (
 workspace_id TEXT NOT NULL REFERENCES workspaces(id), user_id TEXT NOT NULL REFERENCES users(id),
 role TEXT NOT NULL CHECK(role IN ('owner','editor','viewer')), PRIMARY KEY(workspace_id,user_id));
CREATE TABLE IF NOT EXISTS projects (
 id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id), name TEXT NOT NULL,
 document TEXT NOT NULL, revision INTEGER NOT NULL, updated REAL NOT NULL, updated_by TEXT NOT NULL REFERENCES users(id));
CREATE TABLE IF NOT EXISTS revisions (
 project_id TEXT NOT NULL REFERENCES projects(id), revision INTEGER NOT NULL,
 document TEXT NOT NULL, actor TEXT NOT NULL REFERENCES users(id), created REAL NOT NULL,
 PRIMARY KEY(project_id, revision));
CREATE TABLE IF NOT EXISTS files (
 id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id), name TEXT NOT NULL,
 content BLOB NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL, uploaded_by TEXT NOT NULL REFERENCES users(id), created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id), actor TEXT NOT NULL REFERENCES users(id),
 payload TEXT NOT NULL, request_hash TEXT NOT NULL, idempotency_key TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed','cancelled')),
 attempts INTEGER NOT NULL DEFAULT 0, cancel_requested INTEGER NOT NULL DEFAULT 0,
 lease_owner TEXT, lease_until REAL, created REAL NOT NULL, started REAL, finished REAL,
 result TEXT, error TEXT, UNIQUE(workspace_id,idempotency_key));
CREATE INDEX IF NOT EXISTS ix_job_queue ON jobs(status, created);
CREATE TABLE IF NOT EXISTS schedules (
 id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id), actor TEXT NOT NULL REFERENCES users(id),
 name TEXT NOT NULL, payload TEXT NOT NULL, interval_seconds INTEGER NOT NULL,
 next_at REAL NOT NULL, enabled INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS audit (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, actor TEXT, workspace_id TEXT,
 action TEXT NOT NULL, target TEXT, details TEXT NOT NULL, previous_hash TEXT NOT NULL, hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS workers (id TEXT PRIMARY KEY, heartbeat REAL NOT NULL, state TEXT NOT NULL);
'''

def uid(): return str(uuid.uuid4())
def canonical(obj): return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)

def connect(settings):
    conn = sqlite3.connect(settings.db_path, timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA busy_timeout=15000')
    return conn

def initialize(settings):
    settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(settings.data_dir, 0o700)
    with contextlib.closing(connect(settings)) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA synchronous=FULL')
        # Check before DDL, then migrate both tables and version atomically.
        has_meta = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone()
        version = db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone() if has_meta else None
        if version and version[0] not in ('1', '2'):
            raise RuntimeError('Unsupported database schema. Stop and migrate with a reviewed release.')
        try:
            db.executescript("BEGIN IMMEDIATE;\n" + SCHEMA + "\nINSERT INTO meta VALUES ('schema_version','2') ON CONFLICT(key) DO UPDATE SET value='2';\nCOMMIT;")
        except BaseException:
            db.rollback()
            raise
    os.chmod(settings.db_path, 0o600)
    from .identity import ensure_secret_key
    ensure_secret_key(settings)

@contextlib.contextmanager
def transaction(settings, write=False):
    db = connect(settings)
    try:
        db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()

def audit(db, actor, workspace, action, target=None, details=None):
    last = db.execute('SELECT hash FROM audit ORDER BY seq DESC LIMIT 1').fetchone()
    previous = last[0] if last else '0' * 64
    at = time.time()
    body = canonical({'at': at, 'actor': actor, 'workspace_id': workspace, 'action': action,
                      'target': target, 'details': details or {}, 'previous_hash': previous})
    digest = hashlib.sha256(body.encode()).hexdigest()
    db.execute('INSERT INTO audit(at,actor,workspace_id,action,target,details,previous_hash,hash) VALUES(?,?,?,?,?,?,?,?)',
               (at, actor, workspace, action, target, canonical(details or {}), previous, digest))

def verify_audit(db):
    previous = '0' * 64
    count = 0
    for r in db.execute('SELECT * FROM audit ORDER BY seq'):
        body = canonical({'at': r['at'], 'actor': r['actor'], 'workspace_id': r['workspace_id'], 'action': r['action'],
                          'target': r['target'], 'details': json.loads(r['details']), 'previous_hash': r['previous_hash']})
        if r['previous_hash'] != previous or hashlib.sha256(body.encode()).hexdigest() != r['hash']:
            return {'valid': False, 'first_invalid_seq': r['seq'], 'verified_events': count}
        previous = r['hash']; count += 1
    return {'valid': True, 'verified_events': count, 'head_hash': previous}
