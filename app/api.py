"""Authenticated same-origin API; SQLite is deliberately a single-host deployment."""
import base64
import csv
import hashlib
import io
import json
import re
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from . import __version__
from .config import Settings
from .db import initialize, transaction, uid, canonical, audit, verify_audit
from .security import (PH, COOKIE, email, validate_password, verify_password, create_user,
                       session_user, require_member, actor_can_run)
from .models import (Login, PasswordChange, UserCreate, UserState, Named, Member, ProjectCreate,
                     ProjectSave, RestoreRevision, FileCreate, Job, Schedule, ScheduleState)
from .validation import validate_document, decode_csv, validate_job
from .identity import verify_factor, start_session, throttle

ROOT = Path(__file__).resolve().parent.parent

class BodyLimit:
    def __init__(self, app, limit): self.app, self.limit = app, limit
    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http':
            query = scope.get('query_string', b'')
            headers = scope.get('headers', [])
            if len(query) > 4096 or query.count(b'&') > 63 or len(headers) > 80 or sum(len(k)+len(v) for k,v in headers) > 16384:
                return await JSONResponse({'detail':'Request metadata exceeds limits.'}, 431)(scope, receive, send)
        if scope['type'] != 'http' or scope['method'] in ('GET', 'HEAD'):
            return await self.app(scope, receive, send)
        body = bytearray()
        while True:
            event = await receive()
            if event['type'] == 'http.disconnect': return
            body.extend(event.get('body', b''))
            if len(body) > self.limit:
                return await JSONResponse({'detail': 'Request body is too large.'}, 413)(scope, receive, send)
            if not event.get('more_body', False): break
        sent = False
        async def again():
            nonlocal sent
            if sent: return await receive()
            sent = True
            return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
        await self.app(scope, again, send)

def public_job(row):
    r = dict(row)
    for key in ('request_hash', 'idempotency_key', 'lease_owner'): r.pop(key, None)
    r['payload'] = json.loads(r['payload'])
    r['result'] = json.loads(r['result']) if r['result'] else None
    return r

def public_project(row):
    r = dict(row); r['document'] = json.loads(r['document']); return r

def enqueue(db, ws, actor, payload, key):
    encoded = canonical(payload); digest = hashlib.sha256(encoded.encode()).hexdigest()
    old = db.execute('SELECT * FROM jobs WHERE workspace_id=? AND idempotency_key=?', (ws, key)).fetchone()
    if old:
        if old['request_hash'] != digest: raise HTTPException(409, 'Idempotency key was already used for a different job.')
        return dict(old), False
    if db.execute("SELECT count(*) FROM jobs WHERE workspace_id=? AND status IN ('queued','running')", (ws,)).fetchone()[0] >= 100:
        raise HTTPException(429, 'Workspace queue is full (100 outstanding jobs).')
    job_id = uid()
    db.execute("INSERT INTO jobs(id,workspace_id,actor,payload,request_hash,idempotency_key,status,created) VALUES(?,?,?,?,?,?,'queued',?)",
               (job_id, ws, actor, encoded, digest, key, time.time()))
    audit(db, actor, ws, 'job.queued', job_id)
    return dict(db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()), True

def create_app(settings=None):
    settings = settings or Settings.from_env()
    @asynccontextmanager
    async def lifespan(app):
        initialize(settings)
        if settings.production:
            from .preflight import enforce
            enforce(settings)
        yield
    app = FastAPI(title='DataForge Team API', version=__version__, docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)
    app.state.settings = settings
    app.add_middleware(BodyLimit, limit=settings.request_limit)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[urlsplit(settings.origin).hostname])

    @app.middleware('http')
    async def security_headers(request, call_next):
        request_id = secrets.token_hex(8)
        origin = request.headers.get('origin')
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and origin and origin != settings.origin:
            response = JSONResponse({'detail': 'Cross-origin writes are not allowed.'}, 403)
        elif request.method not in ('GET', 'HEAD', 'OPTIONS') and request.headers.get('sec-fetch-site') == 'cross-site':
            response = JSONResponse({'detail': 'Cross-site writes are not allowed.'}, 403)
        else:
            response = await call_next(request)
        defaults = {
            'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY',
            'Referrer-Policy': 'no-referrer', 'Cache-Control': 'no-store',
            'Permissions-Policy': 'camera=(), microphone=(), geolocation=(), payment=(), usb=()',
            'Cross-Origin-Opener-Policy': 'same-origin', 'X-Request-ID': request_id,
            'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; font-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'; object-src 'none'"
        }
        for k, v in defaults.items():
            if k not in response.headers: response.headers[k] = v
        if settings.secure_cookies: response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        return response

    @app.exception_handler(sqlite3.Error)
    async def database_error(request, exc):
        return JSONResponse({'detail': 'Storage is temporarily unavailable. Retry safely with the same idempotency key.'}, 503)

    @app.get('/health/live')
    def live(): return {'status': 'ok', 'service': 'dataforge-team', 'version': __version__}

    @app.get('/health/ready')
    def ready():
        try:
            with transaction(settings) as db:
                db.execute('SELECT 1').fetchone()
                alive = db.execute('SELECT count(*) FROM workers WHERE heartbeat>?', (time.time()-120,)).fetchone()[0]
        except sqlite3.Error: return JSONResponse({'status': 'not_ready'}, 503)
        return {'status': 'ready', 'worker_available': bool(alive)}

    @app.post('/api/auth/login')
    def login(payload: Login, request: Request):
        address = payload.email.strip().lower()
        ip = request.client.host if request.client else 'unknown'
        buckets = [('account:' + hashlib.sha256(address.encode()).hexdigest(), 10), ('ip:' + hashlib.sha256(ip.encode()).hexdigest(), 60)]
        # Attempts are committed before password validation. Failures cannot roll back the throttle.
        with transaction(settings, True) as db:
            now = time.time(); db.execute('DELETE FROM login_attempts WHERE at<?', (now-900,))
            for bucket, limit in buckets:
                count = db.execute('SELECT count(*) FROM login_attempts WHERE bucket=?', (bucket,)).fetchone()[0]
                if count >= limit: raise HTTPException(429, 'Too many sign-in attempts. Try again later.', headers={'Retry-After': '900'})
            for bucket, _ in buckets: db.execute('INSERT INTO login_attempts VALUES(?,?)', (bucket, now))
        with transaction(settings, True) as db:
            user = db.execute('SELECT * FROM users WHERE email=?', (address,)).fetchone()
            valid = verify_password(user['password_hash'] if user else None, payload.password)
            if not user or not valid or not user['active']:
                audit(db, None, None, 'auth.failed')
                response = JSONResponse({'detail': 'Incorrect email or password.'}, 401)
            else:
                if not verify_factor(db, settings, user['id'], payload.otp):
                    audit(db, None, None, 'auth.factor_failed')
                    response = JSONResponse({'detail':'An authenticator or unused recovery code is required.'}, 401)
                else:
                    if PH.check_needs_rehash(user['password_hash']): db.execute('UPDATE users SET password_hash=? WHERE id=?', (PH.hash(payload.password), user['id']))
                    # A verified sign-in is not a guessing attempt. Remove only the rows this
                    # request recorded; earlier failures stay counted against both buckets.
                    for bucket, _ in buckets:
                        db.execute('DELETE FROM login_attempts WHERE rowid=(SELECT rowid FROM login_attempts WHERE bucket=? AND at=? LIMIT 1)', (bucket, now))
                    response = start_session(db, settings, user)
        return response

    @app.get('/api/auth/me')
    def me(request: Request):
        with transaction(settings, True) as db:
            u = session_user(db, request, settings)
            return {'user': {'id': u['user_id'], 'email': u['email'], 'name': u['name'], 'is_admin': bool(u['is_admin'])}, 'csrf': u['csrf']}

    @app.post('/api/auth/logout')
    def logout(request: Request):
        with transaction(settings, True) as db:
            u = session_user(db, request, settings)
            db.execute('DELETE FROM sessions WHERE token_hash=?', (u['token_hash'],)); audit(db, u['user_id'], None, 'auth.logout')
        response = JSONResponse({'ok': True}); response.delete_cookie(COOKIE, path='/', secure=settings.secure_cookies, httponly=True, samesite='strict')
        response.headers['Clear-Site-Data'] = '"cache", "cookies"'
        return response

    @app.post('/api/auth/password')
    def change_password(payload: PasswordChange, request: Request):
        validate_password(payload.new_password)
        throttle(settings, request, request.cookies.get(COOKIE, ''), 'password-change')
        with transaction(settings, True) as db:
            u = session_user(db, request, settings)
            row = db.execute('SELECT password_hash FROM users WHERE id=?', (u['user_id'],)).fetchone()
            if not verify_password(row[0], payload.current_password) or not verify_factor(db, settings, u['user_id'], payload.otp): raise HTTPException(403, 'Current password or authenticator/recovery code is incorrect.')
            db.execute('UPDATE users SET password_hash=? WHERE id=?', (PH.hash(payload.new_password), u['user_id']))
            db.execute('DELETE FROM sessions WHERE user_id=?', (u['user_id'],))
            db.execute('DELETE FROM reset_tokens WHERE user_id=?', (u['user_id'],))
            db.execute('DELETE FROM oidc_states WHERE nonce=?', ('mfa:'+u['user_id'],))
            audit(db, u['user_id'], None, 'auth.password_changed')
        response = JSONResponse({'ok': True, 'sign_in_required': True}); response.delete_cookie(COOKIE, path='/', secure=settings.secure_cookies, httponly=True, samesite='strict'); return response

    @app.get('/api/admin/users')
    def users(request: Request):
        with transaction(settings, True) as db:
            u = session_user(db, request, settings)
            if not u['is_admin']: raise HTTPException(403, 'Administrator role required.')
            return [dict(r) for r in db.execute('SELECT id,email,name,is_admin,active,created FROM users ORDER BY created DESC LIMIT 500')]

    @app.post('/api/admin/users', status_code=201)
    def add_user(payload: UserCreate, request: Request):
        with transaction(settings, True) as db:
            u = session_user(db, request, settings)
            if not u['is_admin']: raise HTTPException(403, 'Administrator role required.')
            user_id = create_user(db, payload.email, payload.name, payload.password, actor=u['user_id'])
            return {'id': user_id}

    @app.patch('/api/admin/users/{user_id}')
    def state_user(user_id: str, payload: UserState, request: Request):
        with transaction(settings, True) as db:
            u = session_user(db, request, settings)
            if not u['is_admin']: raise HTTPException(403, 'Administrator role required.')
            target = db.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
            if not target: raise HTTPException(404, 'User not found.')
            if target['is_admin'] and not payload.active:
                if db.execute('SELECT count(*) FROM users WHERE is_admin=1 AND active=1').fetchone()[0] <= 1:
                    raise HTTPException(409, 'The last active administrator cannot be disabled.')
            if not payload.active:
                owned = db.execute("SELECT workspace_id FROM memberships WHERE user_id=? AND role='owner'", (user_id,)).fetchall()
                for row in owned:
                    count = db.execute("SELECT count(*) FROM memberships m JOIN users x ON x.id=m.user_id WHERE workspace_id=? AND role='owner' AND x.active=1", (row[0],)).fetchone()[0]
                    if count <= 1: raise HTTPException(409, 'Transfer ownership before disabling the last active owner of a workspace.')
            db.execute('UPDATE users SET active=? WHERE id=?', (int(payload.active), user_id))
            db.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))
            db.execute('DELETE FROM reset_tokens WHERE user_id=?', (user_id,))
            db.execute('DELETE FROM oidc_states WHERE nonce=?', ('mfa:'+user_id,))
            audit(db, u['user_id'], None, 'user.state_changed', user_id, {'active': payload.active})
            return {'ok': True}

    @app.get('/api/workspaces')
    def workspaces(request: Request):
        with transaction(settings, True) as db:
            u = session_user(db, request, settings)
            return [dict(r) for r in db.execute('SELECT w.*,m.role FROM workspaces w JOIN memberships m ON m.workspace_id=w.id WHERE m.user_id=? ORDER BY w.created', (u['user_id'],))]

    @app.post('/api/workspaces', status_code=201)
    def add_workspace(payload: Named, request: Request):
        with transaction(settings, True) as db:
            u = session_user(db, request, settings)
            if not u['is_admin']: raise HTTPException(403, 'Administrator role required to provision a workspace.')
            wid = uid(); db.execute('INSERT INTO workspaces VALUES(?,?,?)', (wid, payload.name.strip(), time.time()))
            db.execute('INSERT INTO memberships VALUES(?,?,?)', (wid, u['user_id'], 'owner'))
            audit(db, u['user_id'], wid, 'workspace.created', wid)
            return {'id': wid, 'name': payload.name.strip(), 'role': 'owner'}

    @app.get('/api/workspaces/{ws}/members')
    def members(ws: str, request: Request):
        with transaction(settings, True) as db:
            u = session_user(db, request, settings); require_member(db, u, ws, 'owner')
            return [dict(r) for r in db.execute('SELECT u.id,u.name,u.email,m.role,u.active FROM memberships m JOIN users u ON u.id=m.user_id WHERE workspace_id=?', (ws,))]

    @app.put('/api/workspaces/{ws}/members')
    def set_member(ws: str, payload: Member, request: Request):
        with transaction(settings, True) as db:
            u = session_user(db, request, settings); require_member(db, u, ws, 'owner')
            target = db.execute('SELECT id, active FROM users WHERE email=?', (email(payload.email),)).fetchone()
            if not target or not target['active']: raise HTTPException(404, 'Ask an administrator to provision an active account first.')
            old = db.execute('SELECT role FROM memberships WHERE workspace_id=? AND user_id=?', (ws, target['id'])).fetchone()
            if old and old['role'] == 'owner' and payload.role != 'owner':
                count = db.execute("SELECT count(*) FROM memberships m JOIN users x ON x.id=m.user_id WHERE workspace_id=? AND role='owner' AND x.active=1", (ws,)).fetchone()[0]
                if count <= 1: raise HTTPException(409, 'A workspace must retain an active owner.')
            db.execute('INSERT INTO memberships VALUES(?,?,?) ON CONFLICT(workspace_id,user_id) DO UPDATE SET role=excluded.role', (ws, target['id'], payload.role))
            audit(db, u['user_id'], ws, 'membership.changed', target['id'], {'role': payload.role})
            return {'ok': True}

    @app.delete('/api/workspaces/{ws}/members/{user_id}')
    def remove_member(ws: str, user_id: str, request: Request):
        with transaction(settings, True) as db:
            u = session_user(db, request, settings); require_member(db, u, ws, 'owner')
            old = db.execute('SELECT role FROM memberships WHERE workspace_id=? AND user_id=?', (ws, user_id)).fetchone()
            if not old: raise HTTPException(404, 'Member not found.')
            if old['role'] == 'owner' and db.execute("SELECT count(*) FROM memberships m JOIN users x ON x.id=m.user_id WHERE workspace_id=? AND role='owner' AND x.active=1", (ws,)).fetchone()[0] <= 1:
                raise HTTPException(409, 'A workspace must retain an active owner.')
            db.execute('DELETE FROM memberships WHERE workspace_id=? AND user_id=?', (ws, user_id))
            db.execute('UPDATE schedules SET enabled=0 WHERE workspace_id=? AND actor=?', (ws, user_id))
            audit(db, u['user_id'], ws, 'membership.removed', user_id); return {'ok': True}

    @app.get('/api/workspaces/{ws}/projects')
    def projects(ws: str, request: Request):
        with transaction(settings, True) as db:
            u = session_user(db, request, settings); require_member(db, u, ws)
            return [dict(r) for r in db.execute('SELECT id,name,revision,updated,updated_by FROM projects WHERE workspace_id=? ORDER BY updated DESC LIMIT 500', (ws,))]

    @app.post('/api/workspaces/{ws}/projects', status_code=201)
    def add_project(ws: str, payload: ProjectCreate, request: Request):
        document = validate_document(payload.document, settings.project_limit)
        with transaction(settings, True) as db:
            u = session_user(db, request, settings); require_member(db, u, ws, 'editor')
            if db.execute('SELECT count(*) FROM projects WHERE workspace_id=?', (ws,)).fetchone()[0] >= 100: raise HTTPException(409, 'Workspace project limit reached (100).')
            pid = uid(); now = time.time()
            db.execute('INSERT INTO projects VALUES(?,?,?,?,?,?,?)', (pid, ws, payload.name, document, 1, now, u['user_id']))
            db.execute('INSERT INTO revisions VALUES(?,?,?,?,?)', (pid, 1, document, u['user_id'], now))
            audit(db, u['user_id'], ws, 'project.created', pid)
            return public_project(db.execute('SELECT * FROM projects WHERE id=?', (pid,)).fetchone())

    def get_project(db, ws, pid):
        row = db.execute('SELECT * FROM projects WHERE id=? AND workspace_id=?', (pid, ws)).fetchone()
        if not row: raise HTTPException(404, 'Project not found.')
        return row

    def save_project(db, u, ws, pid, name, document, expected, action='project.saved'):
        row = get_project(db, ws, pid)
        if row['revision'] != expected: raise HTTPException(409, {'message': 'Newer changes exist. Download your edits before reloading.', 'current_revision': row['revision']})
        new = expected + 1; now = time.time()
        db.execute('UPDATE projects SET name=?,document=?,revision=?,updated=?,updated_by=? WHERE id=? AND revision=?', (name, document, new, now, u['user_id'], pid, expected))
        db.execute('INSERT INTO revisions VALUES(?,?,?,?,?)', (pid, new, document, u['user_id'], now))
        db.execute('DELETE FROM revisions WHERE project_id=? AND revision NOT IN (SELECT revision FROM revisions WHERE project_id=? ORDER BY revision DESC LIMIT 20)', (pid, pid))
        audit(db, u['user_id'], ws, action, pid, {'revision': new})
        return public_project(get_project(db, ws, pid))

    @app.get('/api/workspaces/{ws}/projects/{pid}')
    def project(ws: str, pid: str, request: Request):
        with transaction(settings, True) as db:
            u = session_user(db, request, settings); require_member(db, u, ws)
            return public_project(get_project(db, ws, pid))

    @app.put('/api/workspaces/{ws}/projects/{pid}')
    def update_project(ws: str, pid: str, payload: ProjectSave, request: Request):
        document = validate_document(payload.document, settings.project_limit)
        with transaction(settings, True) as db:
            u = session_user(db, request, settings); require_member(db, u, ws, 'editor')
            return save_project(db, u, ws, pid, payload.name, document, payload.revision)

    @app.get('/api/workspaces/{ws}/projects/{pid}/revisions')
    def revisions(ws: str, pid: str, request: Request):
        with transaction(settings, True) as db:
            u = session_user(db, request, settings); require_member(db, u, ws); get_project(db, ws, pid)
            return [dict(r) for r in db.execute('SELECT revision,actor,created FROM revisions WHERE project_id=? ORDER BY revision DESC', (pid,))]

    @app.post('/api/workspaces/{ws}/projects/{pid}/restore')
    def restore_revision(ws: str, pid: str, payload: RestoreRevision, request: Request):
        with transaction(settings, True) as db:
            u = session_user(db, request, settings); require_member(db, u, ws, 'editor'); current = get_project(db, ws, pid)
            old = db.execute('SELECT document FROM revisions WHERE project_id=? AND revision=?', (pid, payload.restore_revision)).fetchone()
            if not old: raise HTTPException(404, 'Retained revision not found.')
            return save_project(db, u, ws, pid, current['name'], old['document'], payload.revision, 'project.restored')

    @app.get('/api/workspaces/{ws}/files')
    def files(ws: str, request: Request):
        with transaction(settings, True) as db:
            u = session_user(db, request, settings); require_member(db, u, ws)
            return [dict(r) for r in db.execute('SELECT id,name,size,sha256,created FROM files WHERE workspace_id=? ORDER BY created DESC LIMIT 500', (ws,))]

    @app.post('/api/workspaces/{ws}/files', status_code=201)
    def add_file(ws: str, payload: FileCreate, request: Request):
        with transaction(settings, True) as db:
            u = session_user(db, request, settings); require_member(db, u, ws, 'editor')
            data, digest, columns, count = decode_csv(payload, settings)
            used = db.execute('SELECT coalesce(sum(size),0) FROM files WHERE workspace_id=?', (ws,)).fetchone()[0]
            if used + len(data) > settings.workspace_limit: raise HTTPException(413, 'Workspace source-storage quota is exhausted.')
            fid = uid(); db.execute('INSERT INTO files VALUES(?,?,?,?,?,?,?,?)', (fid, ws, payload.name, data, len(data), digest, u['user_id'], time.time()))
            audit(db, u['user_id'], ws, 'file.uploaded', fid, {'bytes': len(data), 'sha256': digest})
            return {'id': fid, 'name': payload.name, 'sha256': digest, 'columns': columns, 'row_count': count}

    @app.get('/api/workspaces/{ws}/files/{fid}/download')
    def download_file(ws: str, fid: str, request: Request):
        with transaction(settings, True) as db:
            u = session_user(db, request, settings); require_member(db, u, ws)
            f = db.execute('SELECT * FROM files WHERE id=? AND workspace_id=?', (fid, ws)).fetchone()
            if not f: raise HTTPException(404, 'File not found.')
            audit(db, u['user_id'], ws, 'file.downloaded', fid)
            # Raw source bytes are explicitly a source download, not spreadsheet-safe transformed output.
            return Response(bytes(f['content']), media_type='application/octet-stream', headers={'Content-Disposition': f'attachment; filename="source-{fid}.csv"'})

    @app.delete('/api/workspaces/{ws}/files/{fid}')
    def delete_file(ws: str, fid: str, request: Request):
        with transaction(settings, True) as db:
            u = session_user(db, request, settings); require_member(db, u, ws, 'owner')
            if not db.execute('SELECT 1 FROM files WHERE id=? AND workspace_id=?', (fid, ws)).fetchone(): raise HTTPException(404, 'File not found.')
            refs = db.execute("SELECT payload FROM jobs WHERE workspace_id=? AND status IN ('queued','running') UNION ALL SELECT payload FROM schedules WHERE workspace_id=?", (ws, ws))
            if any(any(s['file_id'] == fid for s in json.loads(r[0])['sources']) for r in refs): raise HTTPException(409, 'File is referenced by an outstanding job or retained schedule.')
            db.execute('DELETE FROM files WHERE id=?', (fid,)); audit(db, u['user_id'], ws, 'file.deleted', fid)
            return {'ok': True}

    @app.get('/api/workspaces/{ws}/jobs')
    def jobs(ws: str, request: Request):
        with transaction(settings, True) as db:
            u = session_user(db, request, settings); require_member(db, u, ws)
            return [dict(r) for r in db.execute('SELECT id,status,attempts,created,started,finished,error,json_extract(payload,\'$.name\') AS name FROM jobs WHERE workspace_id=? ORDER BY created DESC LIMIT 100', (ws,))]

    @app.post('/api/workspaces/{ws}/jobs')
    def submit_job(ws: str, payload: Job, request: Request):
        key = request.headers.get('idempotency-key', '')
        if not re.fullmatch(r'[A-Za-z0-9_.:-]{8,120}', key): raise HTTPException(422, 'Supply an Idempotency-Key of 8 to 120 safe ASCII characters.')
        with transaction(settings, True) as db:
            u = session_user(db, request, settings); require_member(db, u, ws, 'editor')
            body = payload.model_dump(); validate_job(db, ws, body)
            row, created = enqueue(db, ws, u['user_id'], body, key)
            return JSONResponse({'job': public_job(row), 'created': created}, status_code=201 if created else 200)

    def get_job(db, ws, jid):
        row = db.execute('SELECT * FROM jobs WHERE id=? AND workspace_id=?', (jid, ws)).fetchone()
        if not row: raise HTTPException(404, 'Job not found.')
        return row

    @app.get('/api/workspaces/{ws}/jobs/{jid}')
    def job(ws: str, jid: str, request: Request):
        with transaction(settings, True) as db:
            u = session_user(db, request, settings); require_member(db, u, ws); return public_job(get_job(db, ws, jid))

    @app.post('/api/workspaces/{ws}/jobs/{jid}/cancel')
    def cancel_job(ws: str, jid: str, request: Request):
        with transaction(settings, True) as db:
            u = session_user(db, request, settings); require_member(db, u, ws, 'editor'); row = get_job(db, ws, jid)
            if row['status'] in ('queued', 'running'):
                if row['status'] == 'queued': db.execute("UPDATE jobs SET status='cancelled',cancel_requested=1,finished=? WHERE id=?", (time.time(), jid))
                else: db.execute('UPDATE jobs SET cancel_requested=1 WHERE id=?', (jid,))
                audit(db, u['user_id'], ws, 'job.cancel_requested', jid)
            return {'ok': True}

    @app.get('/api/workspaces/{ws}/jobs/{jid}/output')
    def job_output(ws: str, jid: str, request: Request):
        with transaction(settings, True) as db:
            u = session_user(db, request, settings); require_member(db, u, ws); row = get_job(db, ws, jid)
            if row['status'] != 'succeeded' or not row['result']: raise HTTPException(409, 'Job has no retained successful output.')
            result = json.loads(row['result'])
            def safe(value):
                if isinstance(value, str) and (value[:1] in ('=', '+', '-', '@', '\t', '\r', '\n') or value.lstrip(' \t\r\n')[:1] in ('=', '+', '-', '@')):
                    return "'" + value
                return value
            out = io.StringIO(newline=''); writer = csv.writer(out)
            writer.writerow([safe(c) for c in result['columns']])
            writer.writerows([[safe(v) for v in r] for r in result['rows']])
            audit(db, u['user_id'], ws, 'job.output_downloaded', jid, {'spreadsheet_safe': True})
            return Response(out.getvalue().encode('utf-8-sig'), media_type='text/csv', headers={'Content-Disposition': f'attachment; filename="result-{jid}.csv"'})

    @app.get('/api/workspaces/{ws}/schedules')
    def schedules(ws: str, request: Request):
        with transaction(settings, True) as db:
            u = session_user(db, request, settings); require_member(db, u, ws)
            return [dict(r) for r in db.execute('SELECT id,name,interval_seconds,next_at,enabled,actor FROM schedules WHERE workspace_id=? ORDER BY created DESC', (ws,))]

    @app.post('/api/workspaces/{ws}/schedules', status_code=201)
    def add_schedule(ws: str, payload: Schedule, request: Request):
        with transaction(settings, True) as db:
            u = session_user(db, request, settings); require_member(db, u, ws, 'editor')
            body = payload.job.model_dump(); validate_job(db, ws, body)
            if db.execute('SELECT count(*) FROM schedules WHERE workspace_id=?', (ws,)).fetchone()[0] >= 100: raise HTTPException(409, 'Schedule limit reached (100).')
            sid = uid(); now = time.time()
            db.execute('INSERT INTO schedules VALUES(?,?,?,?,?,?,?,?,?)', (sid, ws, u['user_id'], payload.name, canonical(body), payload.interval_seconds, now+payload.interval_seconds, int(payload.enabled), now))
            audit(db, u['user_id'], ws, 'schedule.created', sid, {'enabled': payload.enabled})
            return {'id': sid, 'enabled': payload.enabled}

    @app.patch('/api/workspaces/{ws}/schedules/{sid}')
    def toggle_schedule(ws: str, sid: str, payload: ScheduleState, request: Request):
        with transaction(settings, True) as db:
            u = session_user(db, request, settings); require_member(db, u, ws, 'editor')
            row = db.execute('SELECT * FROM schedules WHERE id=? AND workspace_id=?', (sid, ws)).fetchone()
            if not row: raise HTTPException(404, 'Schedule not found.')
            if payload.enabled:
                validate_job(db, ws, json.loads(row['payload']))
                if not actor_can_run(db, row['actor'], ws): raise HTTPException(409, 'The schedule creator no longer has execution permission. Create a new schedule.')
            db.execute('UPDATE schedules SET enabled=?,next_at=? WHERE id=?', (int(payload.enabled), time.time()+row['interval_seconds'], sid))
            audit(db, u['user_id'], ws, 'schedule.state_changed', sid, {'enabled': payload.enabled}); return {'ok': True}

    @app.delete('/api/workspaces/{ws}/schedules/{sid}')
    def delete_schedule(ws: str, sid: str, request: Request):
        with transaction(settings, True) as db:
            u = session_user(db, request, settings); require_member(db, u, ws, 'editor')
            if not db.execute('DELETE FROM schedules WHERE id=? AND workspace_id=?', (sid, ws)).rowcount: raise HTTPException(404, 'Schedule not found.')
            audit(db, u['user_id'], ws, 'schedule.deleted', sid); return {'ok': True}

    @app.get('/api/workspaces/{ws}/audit')
    def audit_events(ws: str, request: Request, before: int | None = None):
        with transaction(settings, True) as db:
            u = session_user(db, request, settings); require_member(db, u, ws, 'owner')
            query = 'SELECT seq,at,actor,action,target,details,hash FROM audit WHERE workspace_id=? AND seq<? ORDER BY seq DESC LIMIT 100'
            return [dict(r) for r in db.execute(query, (ws, before or 9223372036854775807))]

    @app.get('/api/admin/operations')
    def operations(request: Request):
        with transaction(settings, True) as db:
            u = session_user(db, request, settings)
            if not u['is_admin']: raise HTTPException(403, 'Administrator role required.')
            return {'version': __version__, 'storage': 'SQLite / single host', 'engine': 'SQLite CSV runner',
                    'counts': {r[0]: r[1] for r in db.execute('SELECT status,count(*) FROM jobs GROUP BY status')},
                    'workers': [dict(r) for r in db.execute('SELECT * FROM workers ORDER BY heartbeat DESC LIMIT 20')],
                    'database_bytes': settings.db_path.stat().st_size, 'audit': verify_audit(db),
                    'limits': {'source_file_mib': 5, 'workspace_source_mib': 100, 'result_rows': settings.max_result_rows, 'job_timeout_seconds': settings.job_timeout},
                    'deployment_notice': 'Single-host build. Configure identity, key custody, delivery endpoints and qualification evidence before production.'}

    @app.get('/api/openapi.json')
    def openapi(request: Request):
        with transaction(settings, True) as db: session_user(db, request, settings)
        return app.openapi()

    @app.get('/')
    def index(): return FileResponse(ROOT / 'web' / 'index.html', media_type='text/html')

    @app.get('/assets/{name}')
    def asset(name: str):
        allowed = {'app.js': 'text/javascript', 'app.css': 'text/css', 'team-bridge.js': 'text/javascript'}
        if name not in allowed: raise HTTPException(404, 'Asset not found.')
        return FileResponse(ROOT / 'web' / name, media_type=allowed[name])

    @app.get('/studio')
    def studio(request: Request):
        # Exact project permissions are checked again by the bridge before loading content.
        with transaction(settings, True) as db: session_user(db, request, settings)
        html = (ROOT / 'studio' / 'index.html').read_text()
        html = html.replace('<meta name="dataforge-network" content="cdn">', '<meta name="dataforge-network" content="local">')
        html = re.sub(r'<link[^>]+href="https://cdn\.jsdelivr\.net"[^>]*>', '', html)
        # Session-only designer: suppress automatic app start until authenticated bridge has loaded.
        original = "if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => App.start()); else App.start();"
        if original not in html: raise HTTPException(503, 'Designer integration integrity check failed.')
        html = html.replace(original, 'window.DataForge.start = () => App.start();')
        html = html.replace('</body>', '<script src="/assets/team-bridge.js"></script></body>')
        scripts = re.findall(r'<script(?:\s[^>]*)?>([\s\S]*?)</script>', html)
        hashes = ' '.join("'sha256-"+base64.b64encode(hashlib.sha256(s.encode()).digest()).decode()+"'" for s in scripts if s)
        csp = f"default-src 'none'; script-src 'self' {hashes} 'wasm-unsafe-eval' blob:; worker-src 'self' blob:; style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self' data: blob:; connect-src 'self' blob:; base-uri 'none'; form-action 'none'; object-src 'none'; frame-ancestors 'none'"
        html = re.sub(r'<meta http-equiv="Content-Security-Policy" content="[^"]*">', '', html)
        return HTMLResponse(html, headers={'Content-Security-Policy': csp})

    @app.get('/vendor/duckdb/{name}')
    def engine_asset(name: str):
        allowed = {'duckdb.mjs', 'duckdb-eh.wasm', 'duckdb-mvp.wasm', 'duckdb-browser-mvp.worker.js', 'duckdb-browser-eh.worker.js'}
        if name not in allowed: raise HTTPException(404, 'Asset not found.')
        path = ROOT / 'studio' / 'vendor' / 'duckdb' / name
        if not path.is_file(): raise HTTPException(404, 'DuckDB assets have not been provisioned.')
        return FileResponse(path, media_type='application/wasm' if name.endswith('.wasm') else 'text/javascript')
    from .identity_routes import install_identity_routes
    install_identity_routes(app, settings)
    from .oidc import install_oidc_routes
    install_oidc_routes(app, settings)
    from .pipeline import install_pipeline_routes
    install_pipeline_routes(app, settings)
    from .operations_routes import install_operations_routes
    install_operations_routes(app, settings)
    return app
