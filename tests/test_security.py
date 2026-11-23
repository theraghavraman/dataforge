import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
import pytest
from app.api import create_app
from app.db import transaction, verify_audit
from .conftest import PASSWORD, project, csv_file, submit


def test_no_default_account(tmp_path):
    from app.config import Settings
    from app.db import initialize
    s=Settings(tmp_path);initialize(s)
    with transaction(s) as db: assert db.execute('SELECT count(*) FROM users').fetchone()[0]==0


def test_password_hash_and_session_cookie(env):
    c=TestClient(env['app'],base_url=env['settings'].origin)
    r=c.post('/api/auth/login',json={'email':'admin@example.test','password':PASSWORD})
    assert r.status_code==200
    cookie=r.headers['set-cookie'];assert 'HttpOnly' in cookie and 'SameSite=strict' in cookie
    token=c.cookies['df_session']
    with transaction(env['settings']) as db:
        assert db.execute('SELECT password_hash FROM users LIMIT 1').fetchone()[0].startswith('$argon2id$')
        assert not db.execute('SELECT 1 FROM sessions WHERE token_hash=?',(token,)).fetchone()
        assert db.execute('SELECT 1 FROM sessions WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),)).fetchone()

@pytest.mark.parametrize('resource',['/api/workspaces','/api/admin/users','/api/admin/operations','/api/openapi.json','/studio'])
def test_authentication_required(env,resource):
    c=TestClient(env['app'],base_url=env['settings'].origin)
    assert c.get(resource).status_code==401


def test_logout_revokes_session(env):
    c=env['clients']['editor'];old=c.cookies['df_session']
    assert c.post('/api/auth/logout').status_code==200
    c.cookies.set('df_session',old)
    assert c.get('/api/auth/me').status_code==401

@pytest.mark.parametrize('mode',['absolute','idle'])
def test_session_expiration(env,mode):
    with transaction(env['settings'],True) as db:
        db.execute('UPDATE sessions SET '+('expires' if mode=='absolute' else 'last_seen')+'=0 WHERE user_id=?',(env['ids']['editor'],))
    assert env['clients']['editor'].get('/api/auth/me').status_code==401


def test_password_change_invalidates_all_sessions(env):
    c=env['clients']['editor'];other=TestClient(env['app'],base_url=env['settings'].origin)
    assert other.post('/api/auth/login',json={'email':'editor@example.test','password':PASSWORD}).status_code==200
    r=c.post('/api/auth/password',json={'current_password':PASSWORD,'new_password':'A-New-Secure-Passphrase-994!'});assert r.status_code==200
    assert other.get('/api/auth/me').status_code==401
    assert c.get('/api/auth/me').status_code==401


def test_invalid_current_password_rejected(env):
    assert env['clients']['editor'].post('/api/auth/password',json={'current_password':'wrong','new_password':'A-New-Secure-Passphrase-994!'}).status_code==403


def test_csrf_and_origin(env):
    c=env['clients']['admin'];url='/api/workspaces'
    assert c.post(url,json={'name':'Evil'},headers={'X-CSRF-Token':''}).status_code==403
    assert c.post(url,json={'name':'Evil'},headers={'Origin':'https://evil.example'}).status_code==403
    assert c.post(url,json={'name':'Evil'},headers={'Sec-Fetch-Site':'cross-site'}).status_code==403
    assert c.post(url,json={'name':'Safe'},headers={'Origin':env['settings'].origin}).status_code==201


def test_host_header_validation(env):
    assert env['clients']['admin'].get('/',headers={'Host':'evil.example'}).status_code==400


def test_login_throttle_and_generic_errors(env):
    c=TestClient(env['app'],base_url=env['settings'].origin)
    messages=[]
    for address in ['missing@example.test','editor@example.test']:
        messages.append(c.post('/api/auth/login',json={'email':address,'password':'wrong'}).json())
    assert messages[0]==messages[1]
    for _ in range(10): last=c.post('/api/auth/login',json={'email':'missing@example.test','password':'wrong'})
    assert last.status_code==429
    assert last.headers['retry-after']=='900'

@pytest.mark.parametrize('path',['/api/admin/users','/api/admin/operations'])
def test_editor_not_admin(env,path):
    assert env['clients']['editor'].get(path).status_code==403


def test_no_self_provisioning_or_role_escalation(env):
    c=env['clients']['editor'];ws=env['ws']
    assert c.post('/api/workspaces',json={'name':'Not allowed'}).status_code==403
    assert c.put(f'/api/workspaces/{ws}/members',json={'email':'editor@example.test','role':'owner'}).status_code==403
    assert env['clients']['admin'].post('/api/admin/users',json={'name':'New','email':'n@example.test','password':PASSWORD,'is_admin':True}).status_code==422


def test_disable_account_and_last_owner_guards(env):
    admin=env['clients']['admin'];ws=env['ws']
    assert admin.patch('/api/admin/users/'+env['ids']['editor'],json={'active':False}).status_code==200
    assert env['clients']['editor'].get('/api/auth/me').status_code==401
    assert admin.patch('/api/admin/users/'+env['ids']['admin'],json={'active':False}).status_code==409
    assert admin.delete(f'/api/workspaces/{ws}/members/'+env['ids']['admin']).status_code==409
    assert admin.put(f'/api/workspaces/{ws}/members',json={'email':'admin@example.test','role':'viewer'}).status_code==409

@pytest.mark.parametrize('suffix',['projects','files','jobs','schedules','members','audit'])
def test_cross_workspace_isolation(env,suffix):
    for role in ['editor','viewer','outside']:
        assert env['clients'][role].get(f"/api/workspaces/{env['other']}/{suffix}").status_code==404


def test_workspace_ids_do_not_bypass_object_scope(env):
    p=project(env);f=csv_file(env);j=submit(env).json()['job'];c=env['clients']['admin']
    root=f"/api/workspaces/{env['other']}"
    for path in [f"/projects/{p['id']}",f"/projects/{p['id']}/revisions",f"/files/{f['id']}/download",f"/jobs/{j['id']}",f"/jobs/{j['id']}/output"]:
        assert c.get(root+path).status_code==404
    assert c.post(root+'/jobs',json={'sql':'SELECT * FROM data','sources':[{'table':'data','file_id':f['id']}]},headers={'Idempotency-Key':'cross-scope-0001'}).status_code==404


def test_viewer_cannot_mutate(env):
    p=project(env);f=csv_file(env);j=submit(env).json()['job'];c=env['clients']['viewer'];root=f"/api/workspaces/{env['ws']}"
    assert c.get(root+'/projects').status_code==200
    assert c.post(root+'/projects',json={'name':'No','document':{}}).status_code==403
    assert c.put(root+'/projects/'+p['id'],json={'name':'No','document':{},'revision':1}).status_code==403
    assert c.delete(root+'/files/'+f['id']).status_code==403
    assert c.post(root+'/jobs/'+j['id']+'/cancel').status_code==403
    assert submit(env,role='viewer').status_code==403
    assert c.get(root+'/audit').status_code==403


def test_membership_revocation_is_immediate(env):
    root=f"/api/workspaces/{env['ws']}"
    assert env['clients']['admin'].delete(root+'/members/'+env['ids']['editor']).status_code==200
    assert env['clients']['editor'].get(root+'/projects').status_code==404


def test_validation_rejects_body_limits_reserved_keys(env):
    c=env['clients']['editor'];root=f"/api/workspaces/{env['ws']}"
    assert c.post(root+'/projects',json={'name':'Unsafe','document':{'__proto__':{}}}).status_code==422
    assert c.post(root+'/projects',json={'name':'Huge','document':{'payload':'x'*(2*1024*1024)}}).status_code==413
    assert c.post('/api/auth/login',content=b'x'*(8*1024*1024+1),headers={'Content-Type':'application/json'}).status_code==413


def test_private_files_are_not_http_assets(env):
    c=env['clients']['admin']
    for resource in ['/var/dataforge.sqlite3','/app/config.py','/.env','/assets/../app/config.py','/assets/no.js','/studio/SECURITY.md']:
        assert c.get(resource).status_code==404
    r=c.get('/');assert r.status_code==200
    for h in ['content-security-policy','x-frame-options','x-content-type-options','cache-control','referrer-policy','x-request-id']:
        assert h in r.headers
    assert 'no-store' in r.headers['cache-control']


def test_secure_cookie_on_https(tmp_path):
    from app.config import Settings
    from app.db import initialize
    from app.security import create_user
    s=Settings(tmp_path,origin='https://dataforge.example',secure_cookies=True);initialize(s)
    with transaction(s,True) as db:create_user(db,'test@example.test','Test',PASSWORD)
    c=TestClient(create_app(s),base_url=s.origin)
    r=c.post('/api/auth/login',json={'email':'test@example.test','password':PASSWORD})
    assert 'Secure' in r.headers['set-cookie'] and 'strict-transport-security' in r.headers


def test_audit_chain_detects_modified_event(env):
    with transaction(env['settings'],True) as db:
        assert verify_audit(db)['valid']
        db.execute("UPDATE audit SET action='modified' WHERE seq=(SELECT min(seq) FROM audit)")
        assert not verify_audit(db)['valid']
