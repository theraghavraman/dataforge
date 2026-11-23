from pathlib import Path
import pytest
from cryptography.exceptions import InvalidTag
from fastapi.testclient import TestClient
from app.api import create_app
from app.config import Settings
from app.backup import key_create,backup,restore
from app.db import transaction,verify_audit
from app.worker import Worker
from .conftest import PASSWORD,project,csv_file,submit


def test_encrypted_full_backup_restores_data_and_safe_state(env):
    p=project(env);f=csv_file(env);j=submit(env,'SELECT SUM(CAST(amount AS REAL)) AS total FROM data',[{'table':'data','file_id':f['id']}]).json()['job']['id'];Worker(env['settings']).once()
    c=env['clients']['editor'];sid=c.post(f"/api/workspaces/{env['ws']}/schedules",json={'name':'Enabled','job':{'sql':'SELECT 1'},'interval_seconds':60,'enabled':True}).json()['id']
    pending=submit(env,key='pending-restore-01').json()['job']['id']
    key=env['root']/'backup.key';archive=env['root']/'backup.dfbak';key_create(key);manifest=backup(env['settings'],archive,key)
    assert not archive.read_bytes().startswith(b'SQLite format')
    assert b'region,amount' not in archive.read_bytes()
    dest=env['root']/'restored';result=restore(archive,dest,key);assert result['database_sha256']==manifest['database_sha256']
    restored=Settings(dest)
    with transaction(restored) as db:
        assert db.execute('SELECT count(*) FROM sessions').fetchone()[0]==0
        assert db.execute('SELECT enabled FROM schedules WHERE id=?',(sid,)).fetchone()[0]==0
        assert db.execute('SELECT status FROM jobs WHERE id=?',(pending,)).fetchone()[0]=='failed'
        assert verify_audit(db)['valid']
    client=TestClient(create_app(restored),base_url=restored.origin)
    r=client.post('/api/auth/login',json={'email':'editor@example.test','password':PASSWORD});assert r.status_code==200
    client.headers['X-CSRF-Token']=r.json()['csrf']
    root=f"/api/workspaces/{env['ws']}"
    assert client.get(root+'/projects/'+p['id']).json()['document']==p['document']
    assert client.get(root+'/files/'+f['id']+'/download').content.startswith(b'region,amount')
    assert client.get(root+'/jobs/'+j).json()['result']['rows']==[[170.0]]
    # Re-execute with restored inputs and independently confirm the same total.
    r=client.post(root+'/jobs',json={'sql':'SELECT SUM(CAST(amount AS REAL)) AS total FROM data','sources':[{'table':'data','file_id':f['id']}]},headers={'Idempotency-Key':'restore-execute-01'})
    new=r.json()['job']['id'];Worker(restored).once()
    assert client.get(root+'/jobs/'+new).json()['result']['rows']==[[170.0]]


def test_wrong_key_and_corrupt_backup_rejected(env):
    key=env['root']/'key';wrong=env['root']/'wrong';archive=env['root']/'snapshot';key_create(key);key_create(wrong);backup(env['settings'],archive,key)
    dest=env['root']/'bad-restore'
    with pytest.raises(InvalidTag):restore(archive,dest,wrong)
    assert not dest.exists()
    data=bytearray(archive.read_bytes());data[-1]^=1;archive.write_bytes(data)
    with pytest.raises(InvalidTag):restore(archive,dest,key)
    assert not dest.exists()


def test_restore_never_overwrites_existing_directory(env):
    with pytest.raises(ValueError,match='must not exist'):restore(env['root']/'missing',env['settings'].data_dir,env['root']/'missingkey')


def test_key_and_archive_are_not_silently_overwritten(env):
    key=env['root']/'key';out=env['root']/'backup';key_create(key)
    with pytest.raises(FileExistsError):key_create(key)
    backup(env['settings'],out,key)
    with pytest.raises(ValueError,match='already exists'):backup(env['settings'],out,key)


def test_tampered_audit_prevents_restore(env):
    with transaction(env['settings'],True) as db:db.execute("UPDATE audit SET action='tampered' WHERE seq=1")
    key=env['root']/'key';out=env['root']/'backup';key_create(key);backup(env['settings'],out,key)
    with pytest.raises(ValueError,match='Audit chain'):restore(out,env['root']/'restored',key)
