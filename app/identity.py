"""Local identity factors and secret envelopes. No secrets are written to audit events."""
import base64
import hashlib
import hmac
import os
import secrets
import struct
import time
from urllib.parse import quote, urlencode
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from .db import transaction, audit
from .security import COOKIE, PH, verify_password


def ensure_secret_key(settings):
    path = settings.encryption_key_path
    with transaction(settings) as db:
        protected = db.execute('SELECT user_id,secret FROM mfa LIMIT 1').fetchone()
        pending = db.execute("SELECT id,payload FROM outbox WHERE payload<>'' LIMIT 1").fetchone()
    if not path.exists() and (protected or pending):
        raise ValueError('Restore the original application encryption key before starting this database. Encrypted identity or notification data exists.')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        read_key(settings)
    else:
        with os.fdopen(fd, 'wb') as f:
            f.write(secrets.token_bytes(32)); f.flush(); os.fsync(f.fileno())
    # Validate a protected value at startup; never silently replace a lost key.
    try:
        if protected: unseal(settings, protected['secret'], 'mfa:'+protected['user_id'])
        if pending: unseal(settings, pending['payload'], 'outbox:'+pending['id'])
    except Exception as exc:
        raise ValueError('Application encryption key does not match the stored identity/notification data.') from exc


def read_key(settings):
    path = settings.encryption_key_path
    if path.is_symlink(): raise ValueError('Application key must not be a symbolic link.')
    if os.name == 'posix' and path.stat().st_mode & 0o077:
        raise ValueError('Application key permissions must be 0600 or stricter.')
    key = path.read_bytes()
    if len(key) != 32: raise ValueError('Application key must contain 32 random bytes.')
    return key


def seal(settings, plaintext, purpose):
    nonce = secrets.token_bytes(12)
    return base64.urlsafe_b64encode(nonce + AESGCM(read_key(settings)).encrypt(nonce, plaintext.encode(), purpose.encode())).decode()


def unseal(settings, envelope, purpose):
    raw = base64.urlsafe_b64decode(envelope)
    return AESGCM(read_key(settings)).decrypt(raw[:12], raw[12:], purpose.encode()).decode()


def digest(text): return hashlib.sha256(text.encode()).hexdigest()


def throttle(settings, request, subject, action, account_limit=10, ip_limit=60):
    """Persist attempts outside the operation transaction, including failed attempts."""
    ip = request.client.host if request.client else 'unknown'
    buckets = [(f'{action}:account:'+digest(subject), account_limit), (f'{action}:ip:'+digest(ip), ip_limit)]
    with transaction(settings, True) as db:
        now = time.time(); db.execute('DELETE FROM login_attempts WHERE at<?', (now-900,))
        for bucket, limit in buckets:
            if db.execute('SELECT count(*) FROM login_attempts WHERE bucket=?', (bucket,)).fetchone()[0] >= limit:
                raise HTTPException(429, 'Too many attempts. Try again later.', headers={'Retry-After':'900'})
        for bucket, _ in buckets: db.execute('INSERT INTO login_attempts VALUES(?,?)', (bucket, now))


def totp(secret, counter):
    key = base64.b32decode(secret + '=' * ((8-len(secret)%8)%8))
    mac = hmac.new(key, struct.pack('>Q', counter), hashlib.sha1).digest()
    start = mac[-1] & 15
    number = struct.unpack('>I', mac[start:start+4])[0] & 0x7fffffff
    return f'{number % 1000000:06d}'


def valid_counter(secret, code, last=-1, now=None):
    if not isinstance(code, str) or len(code) != 6 or not code.isascii() or not code.isdigit(): return None
    current = int((time.time() if now is None else now)//30)
    for counter in (current, current-1, current+1):
        if counter >= 0 and counter > last and hmac.compare_digest(totp(secret, counter), code): return counter
    return None


def verify_factor(db, settings, user_id, code):
    row = db.execute('SELECT * FROM mfa WHERE user_id=? AND enabled=1', (user_id,)).fetchone()
    if not row: return True
    secret = unseal(settings, row['secret'], 'mfa:'+user_id)
    counter = valid_counter(secret, code.strip(), row['last_counter'])
    if counter is not None:
        db.execute('UPDATE mfa SET last_counter=? WHERE user_id=?', (counter, user_id)); return True
    value = digest(code.strip().replace('-', '').lower())
    if db.execute('DELETE FROM recovery_codes WHERE user_id=? AND code_hash=?', (user_id, value)).rowcount:
        audit(db, user_id, None, 'auth.recovery_code_used'); return True
    return False


def recovery_codes(db, user_id):
    db.execute('DELETE FROM recovery_codes WHERE user_id=?', (user_id,))
    codes = [secrets.token_hex(16) for _ in range(10)]
    db.executemany('INSERT INTO recovery_codes VALUES(?,?)', [(user_id,digest(c)) for c in codes])
    return ['-'.join(c[i:i+8] for i in range(0,len(c),8)) for c in codes]


def start_session(db, settings, user, method='password'):
    now = time.time(); token = secrets.token_urlsafe(48); csrf = secrets.token_urlsafe(32)
    db.execute('DELETE FROM sessions WHERE expires<? OR last_seen<?', (now, now-settings.idle_seconds))
    old = db.execute('SELECT token_hash FROM sessions WHERE user_id=? ORDER BY created DESC LIMIT -1 OFFSET 4', (user['id'],)).fetchall()
    for row in old: db.execute('DELETE FROM sessions WHERE token_hash=?', (row[0],))
    db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?)', (digest(token), user['id'], csrf, now+settings.session_seconds, now, now))
    audit(db, user['id'], None, 'auth.login', details={'method':method})
    response = JSONResponse({'user': {'id':user['id'], 'email':user['email'], 'name':user['name'], 'is_admin':bool(user['is_admin'])}, 'csrf':csrf})
    response.set_cookie(COOKIE, token, httponly=True, secure=settings.secure_cookies, samesite='strict', max_age=settings.session_seconds, path='/')
    return response


def enrollment_uri(address, secret):
    return 'otpauth://totp/' + quote('DataForge:'+address, safe='') + '?' + urlencode({'secret':secret,'issuer':'DataForge','algorithm':'SHA1','digits':6,'period':30})
