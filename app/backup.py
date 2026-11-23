"""Encrypted, consistent whole-database backups. Keys stay outside the archive.

Restores only create a NEW destination directory. Stop services before switching.
"""
import gzip
import hashlib
import io
import json
import os
import secrets
import sqlite3
import tempfile
import time
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from .config import Settings
from .db import connect, canonical, verify_audit, audit, transaction

MAGIC = b'DATAFORGE-BACKUP-v1\n'
MAX_BYTES = 512 * 1024 * 1024

def key_read(path):
    path = Path(path)
    if path.is_symlink(): raise ValueError('Backup key cannot be a symbolic link.')
    if os.name == 'posix' and path.stat().st_mode & 0o077:
        raise ValueError('Backup key permissions must be 0600 or stricter.')
    raw = path.read_bytes()
    if len(raw) != 32: raise ValueError('Expected a 32-byte backup key.')
    return raw

def key_create(path):
    with open(path, 'xb') as f:
        os.chmod(path, 0o600); f.write(secrets.token_bytes(32)); f.flush(); os.fsync(f.fileno())

def backup(settings, output, key_path):
    output = Path(output)
    if output.exists(): raise ValueError('Backup destination already exists; choose a unique filename.')
    key = key_read(key_path)
    with transaction(settings, True) as db:
        audit(db, None, None, 'backup.started')
    with tempfile.TemporaryDirectory(prefix='dataforge-backup-') as temp:
        path = Path(temp) / 'snapshot.sqlite3'
        src = connect(settings); dst = sqlite3.connect(path)
        try: src.backup(dst)
        finally: src.close(); dst.close()
        if path.stat().st_size > MAX_BYTES: raise ValueError('Backup exceeds the 512 MiB reference-tool limit.')
        raw = path.read_bytes()
        manifest = {'format': 'dataforge-db-v1', 'created': time.time(), 'database_sha256': hashlib.sha256(raw).hexdigest(), 'database_bytes': len(raw)}
        package = canonical(manifest).encode() + b'\n' + gzip.compress(raw)
        nonce = secrets.token_bytes(12)
        encrypted = MAGIC + nonce + AESGCM(key).encrypt(nonce, package, MAGIC)
        # Publish a complete file atomically; an interrupted write is not a backup.
        fd, temporary = tempfile.mkstemp(prefix='.dataforge-backup-', dir=output.parent)
        try:
            with os.fdopen(fd, 'wb') as f:
                os.fchmod(f.fileno(), 0o600)
                f.write(encrypted); f.flush(); os.fsync(f.fileno())
            os.link(temporary, output)  # Exclusive: do not overwrite a concurrent destination.
            if os.name == 'posix':
                parent_fd=os.open(output.parent,os.O_RDONLY)
                try:os.fsync(parent_fd)
                finally:os.close(parent_fd)
        finally:
            os.unlink(temporary)
    with transaction(settings, True) as db:
        audit(db, None, None, 'backup.completed', details={'bytes': len(encrypted), 'database_sha256': manifest['database_sha256']})
    return manifest

def restore(source, destination, key_path):
    source, destination = Path(source), Path(destination)
    if destination.exists(): raise ValueError('Restore destination must not exist. Never overwrite a running database.')
    if source.stat().st_size > MAX_BYTES + 1024*1024: raise ValueError('Backup is too large.')
    encrypted = source.read_bytes()
    if not encrypted.startswith(MAGIC): raise ValueError('Unknown backup format.')
    nonce = encrypted[len(MAGIC):len(MAGIC)+12]
    package = AESGCM(key_read(key_path)).decrypt(nonce, encrypted[len(MAGIC)+12:], MAGIC)
    manifest_line, compressed = package.split(b'\n', 1)
    manifest = json.loads(manifest_line)
    if manifest.get('format') != 'dataforge-db-v1': raise ValueError('Unknown database backup version.')
    with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
        data = stream.read(MAX_BYTES+1)
    if len(data) > MAX_BYTES or hashlib.sha256(data).hexdigest() != manifest['database_sha256'] or len(data) != manifest['database_bytes']:
        raise ValueError('Backup integrity verification failed.')
    # Verify in a temporary sibling first; a failed restore leaves no destination behind.
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.dataforge-restore-', dir=destination.parent) as temp:
        path = Path(temp) / 'dataforge.sqlite3'; path.write_bytes(data); os.chmod(path, 0o600)
        db = sqlite3.connect(path); db.row_factory = sqlite3.Row
        try:
            if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok': raise ValueError('SQLite integrity check failed.')
            if db.execute('PRAGMA foreign_key_check').fetchone(): raise ValueError('Foreign-key integrity check failed.')
            if db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] not in ('1', '2'): raise ValueError('Unsupported schema version.')
            check = verify_audit(db)
            if not check['valid']: raise ValueError('Audit chain verification failed.')
            db.execute('DELETE FROM sessions')
            db.execute('DELETE FROM workers')
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in ('reset_tokens', 'oidc_states', 'outbox'):
                if table in tables: db.execute('DELETE FROM '+table)
            db.execute('UPDATE schedules SET enabled=0')
            db.execute("UPDATE jobs SET status='failed',error='Interrupted by backup restore; explicitly resubmit after review.',finished=?,lease_owner=NULL,lease_until=NULL WHERE status IN ('queued','running')", (time.time(),))
            audit(db, None, None, 'backup.restored', details={'original_database_sha256': manifest['database_sha256'], 'sessions_revoked': True, 'schedules_paused': True})
            db.commit(); db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        finally: db.close()
        destination.mkdir(mode=0o700)
        os.replace(path, destination / 'dataforge.sqlite3')
    return {**manifest, 'audit_verified_events': check['verified_events'], 'sessions_revoked': True, 'schedules_paused': True}
