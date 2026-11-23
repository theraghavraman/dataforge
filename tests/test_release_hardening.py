import json
import sqlite3
import time
import pytest
from app.config import Settings
from app.db import initialize,transaction
from app.identity import digest
from app.maintenance import scheduled_backup
from app.backup import key_create
from app.worker import Worker
from tests.conftest import PASSWORD,submit
from tests.test_pipeline_v2 import setup_pipeline


def test_unknown_schema_is_not_mutated(tmp_path):
    settings=Settings(tmp_path/'unknown');settings.data_dir.mkdir()
    db=sqlite3.connect(settings.db_path);db.execute('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)');db.execute("INSERT INTO meta VALUES('schema_version','900')");db.commit();db.close()
    with pytest.raises(RuntimeError):initialize(settings)
    db=sqlite3.connect(settings.db_path)
    assert [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]==['meta']
    assert db.execute("SELECT value FROM meta").fetchone()[0]=='900';db.close()


def test_additive_v1_migration_preserves_accounts(env):
    with transaction(env['settings'],True) as db:
        original=db.execute('SELECT count(*) FROM users').fetchone()[0]
        db.execute("UPDATE meta SET value='1' WHERE key='schema_version'")
        for name in ('mfa','recovery_codes','reset_tokens','oidc_links','oidc_states','outbox','project_policies','project_approvals'):db.execute('DROP TABLE '+name)
    initialize(env['settings'])
    with transaction(env['settings']) as db:
        assert db.execute('SELECT count(*) FROM users').fetchone()[0]==original
        assert db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]=='2'
        assert db.execute('SELECT count(*) FROM mfa').fetchone()[0]==0


def test_password_change_clears_existing_reset_links_and_challenges(env):
    uid=env['ids']['editor']
    with transaction(env['settings'],True) as db:
        db.execute('INSERT INTO reset_tokens VALUES(?,?,?)',(digest('stale-token'),uid,time.time()+60))
        db.execute('INSERT INTO oidc_states VALUES(?,?,?,?,?)',('state','binding','mfa:'+uid,'challenge',time.time()+60))
    r=env['clients']['editor'].post('/api/auth/password',json={'current_password':PASSWORD,'new_password':'Updated-Long-Password-3863!'})
    assert r.status_code==200
    with transaction(env['settings']) as db:
        assert db.execute('SELECT count(*) FROM reset_tokens WHERE user_id=?',(uid,)).fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM oidc_states').fetchone()[0]==0


def test_failed_backup_backs_off_without_accumulating_files(env,tmp_path,monkeypatch):
    key=tmp_path/'backup.key';key_create(key)
    monkeypatch.setenv('DATAFORGE_BACKUP_DIRECTORY',str(tmp_path/'backups'));monkeypatch.setenv('DATAFORGE_BACKUP_KEY_FILE',str(key));monkeypatch.setenv('DATAFORGE_BACKUP_S3_BUCKET','qa-bucket')
    def failed(path):raise RuntimeError('offline')
    assert scheduled_backup(env['settings'],upload=failed)['status']=='failed'
    assert scheduled_backup(env['settings'],upload=failed)['status']=='retry_backoff'
    assert len(list((tmp_path/'backups').glob('*.dfbackup')))==1


def test_retry_preserves_pipeline_and_requires_current_approval(env):
    p,root,body,f=setup_pipeline(env);editor=env['clients']['editor'];admin=env['clients']['admin']
    old=editor.post(root+'/run',json=body,headers={'Idempotency-Key':'original-release-job'}).json()['job']
    endpoint=f"/api/workspaces/{env['ws']}/jobs/{old['id']}/retry"
    assert editor.post(endpoint,headers={'Idempotency-Key':'retry-release-job'}).status_code==409
    with transaction(env['settings'],True) as db:db.execute("UPDATE jobs SET status='failed',finished=? WHERE id=?",(time.time(),old['id']))
    admin.put(root+'/approval',json={'approval_required':True})
    assert editor.post(endpoint,headers={'Idempotency-Key':'retry-release-job'}).status_code==409
    admin.post(root+'/approve/1')
    one=editor.post(endpoint,headers={'Idempotency-Key':'retry-release-job'});two=editor.post(endpoint,headers={'Idempotency-Key':'retry-release-job'})
    assert one.status_code==201 and two.status_code==200
    assert one.json()['job']['id']==two.json()['job']['id']
    with transaction(env['settings']) as db:
        payload=json.loads(db.execute('SELECT payload FROM jobs WHERE id=?',(one.json()['job']['id'],)).fetchone()[0])
        assert payload['pipeline']['revision']==1 and payload['sources'][0]['sha256']==f['sha256']
    assert env['clients']['viewer'].post(endpoint,headers={'Idempotency-Key':'viewer-retry-key'}).status_code==403
    assert env['clients']['outside'].post(endpoint,headers={'Idempotency-Key':'outside-retry-key'}).status_code in (403,404)


def test_service_status_is_administrator_only(env):
    assert env['clients']['editor'].get('/api/admin/service-status').status_code==403
    r=env['clients']['admin'].get('/api/admin/service-status');assert r.status_code==200
    assert not r.json()['preflight']['ready'] and r.json()['maintenance_online'] is False


def test_restore_of_mfa_requires_original_application_key(env,tmp_path):
    import dataclasses
    import shutil
    from fastapi.testclient import TestClient
    from app.api import create_app
    from app.backup import backup,restore
    from app.identity import unseal
    from tests.test_identity_v2 import enroll
    secret,codes=enroll(env)
    key=tmp_path/'backup-key';key_create(key);output=tmp_path/'saved.dfbackup';backup(env['settings'],output,key)
    destination=tmp_path/'restored';restore(output,destination,key)
    restored=Settings(destination)
    with pytest.raises(ValueError,match='original application encryption key'):initialize(restored)
    assert not restored.encryption_key_path.exists()
    key_create(restored.encryption_key_path)
    with pytest.raises(ValueError,match='does not match'):initialize(restored)
    shutil.copyfile(env['settings'].encryption_key_path,restored.encryption_key_path);restored.encryption_key_path.chmod(0o600);initialize(restored)
    client=TestClient(create_app(restored),base_url=restored.origin)
    response=client.post('/api/auth/login',json={'email':'editor@example.test','password':PASSWORD,'otp':codes[0]})
    assert response.status_code==200
    with transaction(restored) as db:
        assert db.execute('SELECT count(*) FROM outbox').fetchone()[0]==0
        row=db.execute('SELECT * FROM mfa WHERE user_id=?',(env['ids']['editor'],)).fetchone()
        assert unseal(restored,row['secret'],'mfa:'+row['user_id'])==secret


def test_large_stdin_is_fully_transferred_after_poll_timeout(env,tmp_path,monkeypatch):
    import dataclasses
    import subprocess
    from pathlib import Path
    from tests.conftest import csv_file
    import app.worker as worker_module
    count=20000
    source=csv_file(env,'item,amount\n'+''.join(f'{i},{i%17}\n' for i in range(count)))
    wrapper=tmp_path/'delayed_runner.py'
    original_runner=str(Path(worker_module.__file__).with_name('runner.py'))
    wrapper.write_text('import time,runpy\ntime.sleep(0.4)\nrunpy.run_path('+repr(original_runner)+',run_name="__main__")\n')
    real_popen=subprocess.Popen
    def delayed(command,*args,**kwargs):
        if command[-1]==original_runner:command=[*command[:-1],str(wrapper)]
        return real_popen(command,*args,**kwargs)
    monkeypatch.setattr(worker_module.subprocess,'Popen',delayed)
    result=submit(env,'SELECT COUNT(*),SUM(CAST(amount AS INTEGER)) FROM data',[{'table':'data','file_id':source['id']}],key='large-pipe-regression')
    jid=result.json()['job']['id'];Worker(dataclasses.replace(env['settings'],job_timeout=5)).once()
    with transaction(env['settings']) as db:job=db.execute('SELECT status,result,error FROM jobs WHERE id=?',(jid,)).fetchone()
    assert job['status']=='succeeded',job['error']
    assert json.loads(job['result'])['rows']==[[count,sum(i%17 for i in range(count))]]
