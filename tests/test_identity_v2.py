import base64
import dataclasses
import json
import time
import pytest
from fastapi.testclient import TestClient
from app.api import create_app
from app.db import transaction
from app.identity import totp,seal,unseal,digest
from tests.conftest import PASSWORD


def enroll(env,role='editor'):
    client=env['clients'][role]
    response=client.post('/api/auth/mfa/setup',json={'password':PASSWORD})
    assert response.status_code==200,response.text
    secret=response.json()['secret']
    confirmed=client.post('/api/auth/mfa/confirm',json={'code':totp(secret,int(time.time()//30))})
    assert confirmed.status_code==200,confirmed.text
    return secret,confirmed.json()['recovery_codes']


@pytest.mark.parametrize('at,expected',[(59,'287082'),(1111111109,'081804'),(1111111111,'050471'),(1234567890,'005924'),(2000000000,'279037'),(20000000000,'353130')])
def test_rfc_totp_sha1_vectors(at,expected):
    secret=base64.b32encode(b'12345678901234567890').decode()
    assert totp(secret,at//30)==expected


def test_encryption_context_binding(env):
    value=seal(env['settings'],'example secret','one')
    assert 'example secret' not in value
    assert unseal(env['settings'],value,'one')=='example secret'
    with pytest.raises(Exception):unseal(env['settings'],value,'two')


def test_mfa_enrollment_and_secrets_not_in_database(env):
    secret,codes=enroll(env)
    assert len(codes)==10 and len(set(codes))==10
    with transaction(env['settings']) as db:
        assert secret not in db.execute('SELECT secret FROM mfa WHERE user_id=?',(env['ids']['editor'],)).fetchone()[0]
        hashes=[r[0] for r in db.execute('SELECT code_hash FROM recovery_codes')]
        assert all(code not in hashes for code in codes)
    assert env['clients']['editor'].get('/api/auth/mfa').json()['enabled']
    assert env['clients']['editor'].post('/api/auth/mfa/setup',json={'password':PASSWORD}).status_code==409


def test_mfa_login_requires_factor_and_rejects_replay(env):
    secret,codes=enroll(env)
    c=TestClient(env['app'],base_url=env['settings'].origin)
    body={'email':'editor@example.test','password':PASSWORD}
    assert c.post('/api/auth/login',json=body).status_code==401
    assert c.get('/api/workspaces').status_code==401
    body['otp']=codes[0]
    assert c.post('/api/auth/login',json=body).status_code==200
    assert c.post('/api/auth/login',json=body).status_code==401
    body['otp']=totp(secret,int(time.time()//30)+1)
    assert c.post('/api/auth/login',json=body).status_code==200
    assert c.post('/api/auth/login',json=body).status_code==401


def test_enrollment_wrong_password_and_expiry(env):
    c=env['clients']['editor']
    assert c.post('/api/auth/mfa/setup',json={'password':'wrong'}).status_code==403
    r=c.post('/api/auth/mfa/setup',json={'password':PASSWORD}).json()
    with transaction(env['settings'],True) as db:db.execute('UPDATE mfa SET expires=0')
    assert c.post('/api/auth/mfa/confirm',json={'code':totp(r['secret'],int(time.time()//30))}).status_code==400


def test_recovery_rotation_invalidates_old_codes(env):
    secret,codes=enroll(env);c=env['clients']['editor']
    r=c.post('/api/auth/mfa/recovery-codes',json={'password':PASSWORD,'code':codes[0]})
    assert r.status_code==200
    auth={'email':'editor@example.test','password':PASSWORD,'otp':codes[1]}
    assert c.post('/api/auth/login',json=auth).status_code==401
    auth['otp']=r.json()['recovery_codes'][0]
    assert c.post('/api/auth/login',json=auth).status_code==200


def test_disable_requires_factor_and_revokes_sessions(env):
    _,codes=enroll(env);c=env['clients']['editor']
    assert c.post('/api/auth/mfa/disable',json={'password':PASSWORD}).status_code==403
    assert c.post('/api/auth/mfa/disable',json={'password':PASSWORD,'code':codes[0]}).status_code==200
    assert c.get('/api/auth/me').status_code==401


def test_password_change_preserves_mfa(env):
    _,codes=enroll(env);c=env['clients']['editor']
    data={'current_password':PASSWORD,'new_password':'Another-Valid-Password-991!'}
    assert c.post('/api/auth/password',json=data).status_code==403
    data['otp']=codes[0]
    assert c.post('/api/auth/password',json=data).status_code==200
    assert c.post('/api/auth/login',json={'email':'editor@example.test','password':data['new_password']}).status_code==401


def test_recovery_generic_response_encrypted_outbox_and_single_use(env,monkeypatch):
    monkeypatch.setenv('DATAFORGE_SMTP_HOST','smtp.example.test')
    c=env['clients']['editor']
    one=c.post('/api/auth/recovery/request',json={'email':'editor@example.test'})
    two=c.post('/api/auth/recovery/request',json={'email':'missing@example.test'})
    assert one.status_code==two.status_code==202 and one.json()==two.json()
    with transaction(env['settings']) as db:
        row=db.execute("SELECT * FROM outbox WHERE dedupe LIKE 'reset:%'").fetchone()
        message=json.loads(unseal(env['settings'],row['payload'],'outbox:'+row['id']))
        token=message['body'].split('#reset=')[1].split()[0]
        assert token not in row['payload']
        assert db.execute('SELECT token_hash FROM reset_tokens').fetchone()[0]==digest(token)
    new='New-Reset-Password-9831!'
    assert c.post('/api/auth/recovery/complete',json={'token':token,'new_password':new}).status_code==200
    assert c.get('/api/auth/me').status_code==401
    assert c.post('/api/auth/recovery/complete',json={'token':token,'new_password':new}).status_code==400
    assert c.post('/api/auth/login',json={'email':'editor@example.test','password':new}).status_code==200


def test_password_reset_does_not_remove_mfa(env,monkeypatch):
    _,codes=enroll(env);monkeypatch.setenv('DATAFORGE_SMTP_HOST','smtp.example.test');c=env['clients']['editor']
    c.post('/api/auth/recovery/request',json={'email':'editor@example.test'})
    with transaction(env['settings']) as db:
        r=db.execute('SELECT * FROM outbox').fetchone();token=json.loads(unseal(env['settings'],r['payload'],'outbox:'+r['id']))['body'].split('#reset=')[1].split()[0]
    new='New-Factor-Reset-Password-123!'
    assert c.post('/api/auth/recovery/complete',json={'token':token,'new_password':new}).status_code==200
    assert c.post('/api/auth/login',json={'email':'editor@example.test','password':new}).status_code==401
    assert c.post('/api/auth/login',json={'email':'editor@example.test','password':new,'otp':codes[0]}).status_code==200


def test_recovery_not_configured_is_explicit(env,monkeypatch):
    monkeypatch.delenv('DATAFORGE_SMTP_HOST',raising=False)
    assert not env['clients']['editor'].get('/api/auth/options').json()['password_recovery']
    assert env['clients']['editor'].post('/api/auth/recovery/request',json={'email':'editor@example.test'}).status_code==503


def test_require_mfa_denies_workspace_until_enrollment(env):
    settings=dataclasses.replace(env['settings'],require_mfa=True)
    c=TestClient(create_app(settings),base_url=settings.origin)
    r=c.post('/api/auth/login',json={'email':'editor@example.test','password':PASSWORD})
    assert r.status_code==200
    c.headers['X-CSRF-Token']=r.json()['csrf']
    assert c.get('/api/workspaces').status_code==403
    assert c.get('/api/auth/mfa').status_code==200
    secret=c.post('/api/auth/mfa/setup',json={'password':PASSWORD}).json()['secret']
    assert c.post('/api/auth/mfa/confirm',json={'code':totp(secret,int(time.time()//30))}).status_code==200
    assert c.get('/api/workspaces').status_code==200
    assert c.post('/api/auth/mfa/disable',json={'password':PASSWORD,'code':'123456'}).status_code==403


def test_session_inventory_and_revoke_are_account_scoped(env):
    c=env['clients']['editor'];rows=c.get('/api/auth/sessions').json();assert rows[0]['current']
    assert env['clients']['viewer'].delete('/api/auth/sessions/'+rows[0]['id']).status_code==404
    assert c.delete('/api/auth/sessions/'+rows[0]['id']).status_code==200
    assert c.get('/api/auth/me').status_code==401


def test_request_parameter_limits(env):
    c=env['clients']['editor']
    assert c.get('/api/workspaces?'+('&'.join('x=1' for _ in range(100)))).status_code==431
    assert c.get('/api/workspaces',headers={'X-Large':'x'*20000}).status_code==431


def test_mfa_throttle_commits_failed_attempts(env):
    c=env['clients']['editor']
    for _ in range(10):assert c.post('/api/auth/mfa/setup',json={'password':'wrong'}).status_code==403
    assert c.post('/api/auth/mfa/setup',json={'password':PASSWORD}).status_code==429
