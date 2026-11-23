"""Independent maintenance process: encrypted backups, alerts and durable email delivery."""
import base64
import hashlib
import json
import os
import signal
import time
from pathlib import Path
from .backup import backup
from .db import transaction, audit, canonical
from .outbox import enqueue_message, dispatch_once


def integer_env(name,default,low,high):
    value=int(os.getenv(name,str(default)))
    if not low<=value<=high:raise ValueError(name+' is outside the allowed range.')
    return value


def s3_upload(path,client=None):
    import boto3
    bucket=os.environ['DATAFORGE_BACKUP_S3_BUCKET']
    prefix=os.getenv('DATAFORGE_BACKUP_S3_PREFIX','dataforge').strip('/')
    key=(prefix+'/' if prefix else '')+path.name
    raw=path.read_bytes();checksum=base64.b64encode(hashlib.sha256(raw).digest()).decode()
    client=client or boto3.client('s3')
    params={'Bucket':bucket,'Key':key,'Body':raw,'ChecksumSHA256':checksum,'ContentType':'application/octet-stream',
            'ServerSideEncryption':'aws:kms' if os.getenv('DATAFORGE_BACKUP_KMS_KEY') else 'AES256'}
    if os.getenv('DATAFORGE_BACKUP_KMS_KEY'):params['SSEKMSKeyId']=os.environ['DATAFORGE_BACKUP_KMS_KEY']
    client.put_object(**params)
    head=client.head_object(Bucket=bucket,Key=key,ChecksumMode='ENABLED')
    if head.get('ContentLength')!=len(raw) or head.get('ChecksumSHA256')!=checksum:
        raise ValueError('Remote backup checksum verification failed.')
    return {'bucket':bucket,'key':key,'checksum_sha256':checksum,'bytes':len(raw)}


def announce(db,settings,title,details,dedupe):
    message={'service':'dataforge','title':title,'details':details,'time':time.time()}
    targets=[x.strip() for x in os.getenv('DATAFORGE_ALERT_EMAILS','').split(',') if x.strip()]
    for target in targets:
        enqueue_message(db,settings,'email',{'to':target,'subject':'DataForge: '+title,'body':json.dumps(message,indent=2)},dedupe+':email:'+hashlib.sha256(target.encode()).hexdigest())
    if os.getenv('DATAFORGE_ALERT_WEBHOOK'):enqueue_message(db,settings,'webhook',message,dedupe+':webhook')


def monitor(settings):
    now=time.time()
    with transaction(settings,True) as db:
        count=db.execute("SELECT count(*) FROM workers WHERE heartbeat>? AND state!='stopped'",(now-120,)).fetchone()[0]
        old=db.execute("SELECT value FROM meta WHERE key='monitor.worker_down'").fetchone()
        down=not bool(count)
        if old is None or old[0]!=str(int(down)):
            db.execute("INSERT INTO meta VALUES('monitor.worker_down',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(int(down)),))
            if old is not None or down:announce(db,settings,'Worker unavailable' if down else 'Worker recovered',{'active_workers':count},'worker-state:'+str(int(now)))
        cursor=db.execute("SELECT value FROM meta WHERE key='monitor.audit_cursor'").fetchone()
        rows=db.execute("SELECT seq,action,target FROM audit WHERE seq>? AND action IN ('job.failed','job.permission_revoked','schedule.paused_on_error','schedule.permission_revoked') ORDER BY seq LIMIT 100",(int(cursor[0]) if cursor else 0,)).fetchall()
        for row in rows:announce(db,settings,'Execution needs attention',{'event':row['action'],'object_id':row['target']},'event:'+str(row['seq']))
        if rows:db.execute("INSERT INTO meta VALUES('monitor.audit_cursor',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(rows[-1]['seq']),))
        db.execute("INSERT INTO meta VALUES('maintenance.heartbeat',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(now),))
        db.execute('DELETE FROM reset_tokens WHERE expires<?',(now,));db.execute('DELETE FROM oidc_states WHERE expires<?',(now,))
        db.execute('DELETE FROM sessions WHERE expires<? OR last_seen<?',(now,now-settings.idle_seconds))
        db.execute("DELETE FROM outbox WHERE status IN ('delivered','expired','failed') AND created<?",(now-30*86400,))


def scheduled_backup(settings,force=False,upload=s3_upload):
    directory=os.getenv('DATAFORGE_BACKUP_DIRECTORY','');key=os.getenv('DATAFORGE_BACKUP_KEY_FILE','')
    if not directory or not key:return {'status':'not_configured'}
    interval=integer_env('DATAFORGE_BACKUP_INTERVAL_SECONDS',86400,300,604800)
    with transaction(settings) as db:
        row=db.execute("SELECT value FROM meta WHERE key='maintenance.last_backup'").fetchone()
        retry=db.execute("SELECT value FROM meta WHERE key='maintenance.backup_retry_at'").fetchone()
    if not force and retry and float(retry[0])>time.time():return {'status':'retry_backoff'}
    previous=json.loads(row[0]) if row else {}
    if not force and previous.get('at',0)>time.time()-interval:return {'status':'not_due'}
    target=Path(directory).resolve();target.mkdir(parents=True,exist_ok=True,mode=0o700)
    # A unique suffix prevents filename reuse, including clock rollback and retries.
    import secrets
    filename='dataforge-'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+secrets.token_hex(4)+'.dfbackup'
    output=target/filename
    try:
        manifest=backup(settings,output,Path(key))
        remote=upload(output) if os.getenv('DATAFORGE_BACKUP_S3_BUCKET') else None
        result={'at':time.time(),'file':filename,'database_bytes':manifest['database_bytes'],'remote_verified':bool(remote)}
        with transaction(settings,True) as db:
            db.execute("INSERT INTO meta VALUES('maintenance.last_backup',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(canonical(result),))
            db.execute("DELETE FROM meta WHERE key IN ('maintenance.backup_error','maintenance.backup_retry_at')")
            audit(db,None,None,'backup.maintenance_completed',details={'remote_verified':bool(remote)})
        days=integer_env('DATAFORGE_BACKUP_RETENTION_DAYS',30,7,3650)
        for old in target.glob('dataforge-*.dfbackup'):
            if old.is_file() and not old.is_symlink() and old!=output and old.stat().st_mtime<time.time()-days*86400:old.unlink()
        return {'status':'completed',**result}
    except Exception as exc:
        # Keep the encrypted local artifact. Back off an hour on failure to bound disk churn.
        with transaction(settings,True) as db:
            db.execute("INSERT INTO meta VALUES('maintenance.backup_error',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(type(exc).__name__,))
            db.execute("INSERT INTO meta VALUES('maintenance.backup_retry_at',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(time.time()+3600),))
            announce(db,settings,'Backup failed',{'error_type':type(exc).__name__},'backup-failure:'+str(int(time.time()//3600)))
        return {'status':'failed','error_type':type(exc).__name__}


def retention(settings):
    days=integer_env('DATAFORGE_RESULT_RETENTION_DAYS',90,7,3650);now=time.time()
    with transaction(settings,True) as db:
        row=db.execute("SELECT value FROM meta WHERE key='maintenance.last_retention'").fetchone()
        if row and float(row[0])>now-86400:return
        changed=db.execute("UPDATE jobs SET result=NULL,error=coalesce(error,'Output removed by retention policy.') WHERE result IS NOT NULL AND status IN ('succeeded','failed','cancelled') AND finished<?",(now-days*86400,)).rowcount
        db.execute("INSERT INTO meta VALUES('maintenance.last_retention',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(now),))
        if changed:audit(db,None,None,'retention.applied',details={'outputs_removed':changed,'days':days})


def once(settings):
    # One maintenance process per host. API and worker do not wait for SMTP or backup IO.
    import fcntl
    with open(settings.data_dir/'maintenance.lock','a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return {'status':'already_running'}
        monitor(settings);result=scheduled_backup(settings);retention(settings)
        delivered=0
        for _ in range(20):
            if not dispatch_once(settings):break
            delivered+=1
        return {'status':'ok','backup':result,'notifications_processed':delivered}


def run(settings):
    stopped=False
    def stop(*_):
        nonlocal stopped
        stopped=True
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    while not stopped:
        try:once(settings)
        except Exception as exc:
            import logging
            logging.getLogger('dataforge.maintenance').error('Maintenance cycle failed: %s',type(exc).__name__)
        for _ in range(30):
            if stopped:break
            time.sleep(1)
