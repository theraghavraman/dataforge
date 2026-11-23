import base64
import hashlib
import json
import os
import time
from pathlib import Path
import pytest
from app.db import transaction
from app.outbox import enqueue_message,dispatch_once,email_send
from app.identity import unseal
from app.maintenance import scheduled_backup,monitor,s3_upload,retention
from app.backup import key_create,restore
from app.preflight import check


def queued(env,kind='email'):
    with transaction(env['settings'],True) as db:
        return enqueue_message(db,env['settings'],kind,{'to':'qa@example.test','subject':'QA','body':'sensitive reset token'},'unique-qa')


def test_outbox_encrypts_delivers_and_removes_payload(env):
    mid=queued(env);seen=[]
    with transaction(env['settings']) as db:assert 'sensitive' not in db.execute('SELECT payload FROM outbox').fetchone()[0]
    assert dispatch_once(env['settings'],send_email=lambda payload,id:seen.append((payload,id)))
    assert seen[0][0]['body']=='sensitive reset token' and seen[0][1]==mid
    with transaction(env['settings']) as db:
        r=db.execute('SELECT * FROM outbox').fetchone();assert r['status']=='delivered' and r['payload']==''
    assert not dispatch_once(env['settings'],send_email=lambda *_:pytest.fail('Duplicate delivery'))


def test_outbox_failed_attempt_retry_and_terminal_failure(env):
    mid=queued(env)
    def broken(*_):raise RuntimeError('Must not leak recipient or reset token')
    for i in range(6):
        with transaction(env['settings'],True) as db:db.execute('UPDATE outbox SET next_at=0')
        assert dispatch_once(env['settings'],send_email=broken)
    with transaction(env['settings']) as db:
        r=db.execute('SELECT * FROM outbox').fetchone();assert r['status']=='failed' and r['attempts']==6 and r['payload']=='' and r['last_error']=='RuntimeError'


def test_outbox_deduplication_and_expired_messages(env):
    queued(env);queued(env)
    with transaction(env['settings'],True) as db:
        assert db.execute('SELECT count(*) FROM outbox').fetchone()[0]==1
        enqueue_message(db,env['settings'],'email',{'to':'qa@example.test','subject':'Expired','body':'do not send','expires':0},'expired')
        db.execute("UPDATE outbox SET status='failed' WHERE dedupe='unique-qa'")
    assert dispatch_once(env['settings'],send_email=lambda *_:pytest.fail('Expired message delivered'))
    with transaction(env['settings']) as db:assert db.execute("SELECT status FROM outbox WHERE dedupe='expired'").fetchone()[0]=='expired'


def test_outbox_expired_worker_lease_recovers(env):
    queued(env)
    with transaction(env['settings'],True) as db:db.execute("UPDATE outbox SET status='sending',lease_until=0,lease_owner='crashed'")
    assert dispatch_once(env['settings'],send_email=lambda *_:None)
    with transaction(env['settings']) as db:assert db.execute('SELECT status FROM outbox').fetchone()[0]=='delivered'


def test_smtp_cleartext_mode_rejected(monkeypatch):
    monkeypatch.setenv('DATAFORGE_SMTP_HOST','localhost');monkeypatch.setenv('DATAFORGE_SMTP_MODE','plain')
    with pytest.raises(ValueError):email_send({'to':'x@example.test','subject':'x','body':'x'},'qa')


def test_scheduled_backup_and_restore(env,monkeypatch,tmp_path):
    key=tmp_path/'backup.key';key_create(key)
    monkeypatch.setenv('DATAFORGE_BACKUP_DIRECTORY',str(tmp_path/'backups'));monkeypatch.setenv('DATAFORGE_BACKUP_KEY_FILE',str(key))
    one=scheduled_backup(env['settings']);assert one['status']=='completed'
    assert scheduled_backup(env['settings'])['status']=='not_due'
    restored=tmp_path/'restored';result=restore(tmp_path/'backups'/one['file'],restored,key)
    assert result['sessions_revoked'] and (restored/'dataforge.sqlite3').is_file()


def test_failed_remote_upload_does_not_report_success(env,monkeypatch,tmp_path):
    key=tmp_path/'backup.key';key_create(key)
    monkeypatch.setenv('DATAFORGE_BACKUP_DIRECTORY',str(tmp_path/'backups'));monkeypatch.setenv('DATAFORGE_BACKUP_KEY_FILE',str(key));monkeypatch.setenv('DATAFORGE_BACKUP_S3_BUCKET','qa-bucket')
    def broken(_):raise RuntimeError('remote failed')
    result=scheduled_backup(env['settings'],upload=broken)
    assert result['status']=='failed' and len(list((tmp_path/'backups').glob('*.dfbackup')))==1
    with transaction(env['settings']) as db:assert db.execute("SELECT 1 FROM meta WHERE key='maintenance.last_backup'").fetchone() is None


def test_s3_checksum_verification_with_sdk_stub(tmp_path,monkeypatch):
    import boto3
    from botocore.stub import Stubber
    monkeypatch.setenv('DATAFORGE_BACKUP_S3_BUCKET','dataforge-qa-bucket')
    p=tmp_path/'encrypted.dfbackup';p.write_bytes(b'encrypted fixture');raw=p.read_bytes();digest=base64.b64encode(hashlib.sha256(raw).digest()).decode()
    client=boto3.client('s3',region_name='us-east-1',aws_access_key_id='fixture',aws_secret_access_key='fixture')
    expected={'Bucket':'dataforge-qa-bucket','Key':'dataforge/'+p.name,'Body':raw,'ChecksumSHA256':digest,'ContentType':'application/octet-stream','ServerSideEncryption':'AES256'}
    with Stubber(client) as stub:
        stub.add_response('put_object',{'ChecksumSHA256':digest},expected)
        stub.add_response('head_object',{'ContentLength':len(raw),'ChecksumSHA256':digest},{'Bucket':'dataforge-qa-bucket','Key':'dataforge/'+p.name,'ChecksumMode':'ENABLED'})
        assert s3_upload(p,client)['checksum_sha256']==digest
        stub.assert_no_pending_responses()


def test_monitor_alerts_are_deduplicated_and_no_query_data(env,monkeypatch):
    monkeypatch.setenv('DATAFORGE_ALERT_EMAILS','ops@example.test');monitor(env['settings']);monitor(env['settings'])
    with transaction(env['settings']) as db:
        rows=db.execute('SELECT * FROM outbox').fetchall();assert len(rows)==1
        msg=json.loads(unseal(env['settings'],rows[0]['payload'],'outbox:'+rows[0]['id']))
        assert 'Worker unavailable' in msg['subject'] and 'password' not in msg['body']


def test_production_preflight_refuses_unqualified_environment(env,monkeypatch):
    import importlib.metadata
    original=importlib.metadata.version
    monkeypatch.setattr(importlib.metadata,'version',lambda name:'0.128.2' if name=='fastapi' else original(name))
    report=check(env['settings']);assert not report['ready']
    flags={r['check']:r['passed'] for r in report['checks']}
    assert flags['https'] is False and flags['deployment_qualification'] is False
    assert flags['patched_fastapi'] is False  # Simulated vulnerable version; independent of the CI runtime.
