import hashlib
import re
import secrets
import time
from argon2 import PasswordHasher, Type
from argon2.exceptions import VerificationError, InvalidHashError
from fastapi import HTTPException
from .db import uid, audit

PH = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1, hash_len=32, salt_len=16, type=Type.ID)
DUMMY_HASH = PH.hash(secrets.token_urlsafe(32))
COOKIE = 'df_session'
ROLES = {'viewer': 1, 'editor': 2, 'owner': 3}

def email(value):
    value = value.strip().lower()
    if len(value) > 254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
        raise HTTPException(422, 'Enter a valid email address.')
    return value

def validate_password(value):
    if not 14 <= len(value) <= 128 or len(value.encode()) > 512:
        raise HTTPException(422, 'Use a password of 14 to 128 characters.')
    if len(set(value)) < 6:
        raise HTTPException(422, 'Choose a less repetitive password.')
    return value

def verify_password(encoded, value):
    try: return PH.verify(encoded or DUMMY_HASH, value)
    except (VerificationError, InvalidHashError): return False

def create_user(db, address, name, password, admin=False, actor=None):
    address = email(address); validate_password(password)
    if db.execute('SELECT 1 FROM users WHERE email=?', (address,)).fetchone():
        raise HTTPException(409, 'An account with that email already exists.')
    user_id = uid()
    db.execute('INSERT INTO users VALUES(?,?,?,?,?,?,?)', (user_id, address, name.strip()[:120], PH.hash(password), int(admin), 1, time.time()))
    audit(db, actor, None, 'user.created', user_id, {'administrator': bool(admin)})
    return user_id

def session_user(db, request, settings, csrf=True):
    token = request.cookies.get(COOKIE, '')
    if not token or len(token) > 128: raise HTTPException(401, 'Sign in to continue.')
    digest = hashlib.sha256(token.encode()).hexdigest()
    row = db.execute('SELECT s.*, u.email, u.name, u.is_admin, u.active FROM sessions s JOIN users u ON u.id=s.user_id WHERE token_hash=?', (digest,)).fetchone()
    now = time.time()
    if not row or not row['active'] or row['expires'] <= now or row['last_seen'] <= now - settings.idle_seconds:
        raise HTTPException(401, 'Your session has expired. Sign in again.')
    allowed_enrollment = request.url.path in ('/api/auth/me','/api/auth/logout','/api/auth/options','/api/auth/password','/api/auth/sessions') or request.url.path.startswith('/api/auth/mfa')
    if settings.require_mfa and not allowed_enrollment and not db.execute('SELECT 1 FROM mfa WHERE user_id=? AND enabled=1', (row['user_id'],)).fetchone():
        raise HTTPException(403, 'Enroll in two-step verification before accessing workspace data.')
    if csrf and request.method not in ('GET', 'HEAD', 'OPTIONS'):
        supplied = request.headers.get('x-csrf-token', '')
        if not secrets.compare_digest(row['csrf'], supplied): raise HTTPException(403, 'Invalid CSRF token.')
    if now - row['last_seen'] > 30:
        db.execute('UPDATE sessions SET last_seen=? WHERE token_hash=?', (now, digest))
    return dict(row)

def require_member(db, user, workspace, minimum='viewer'):
    row = db.execute('SELECT role FROM memberships WHERE workspace_id=? AND user_id=?', (workspace, user['user_id'])).fetchone()
    if not row: raise HTTPException(404, 'Workspace not found.')
    if ROLES[row['role']] < ROLES[minimum]: raise HTTPException(403, 'Your workspace role does not allow this action.')
    return row['role']

def actor_can_run(db, actor, workspace):
    row = db.execute('SELECT m.role, u.active FROM memberships m JOIN users u ON u.id=m.user_id WHERE workspace_id=? AND user_id=?', (workspace, actor)).fetchone()
    return bool(row and row['active'] and ROLES[row['role']] >= ROLES['editor'])
