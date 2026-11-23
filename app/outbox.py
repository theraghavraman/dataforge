"""Encrypted durable delivery outbox with leases, bounded retry and at-least-once delivery."""
import json
import os
import secrets
import smtplib
import ssl
import time
from email.message import EmailMessage
from urllib.parse import urlsplit
import httpx
from .db import transaction, uid, canonical, audit
from .identity import seal, unseal


def enqueue_message(db,settings,kind,payload,dedupe):
    now=time.time(); mid=uid()
    if kind not in ('email','webhook'): raise ValueError('Unknown notification kind.')
    if db.execute("SELECT count(*) FROM outbox WHERE status IN ('pending','sending')").fetchone()[0]>=10000:
        raise ValueError('Notification queue is full.')
    body=seal(settings,canonical(payload),'outbox:'+mid)
    db.execute("INSERT OR IGNORE INTO outbox(id,kind,payload,dedupe,next_at,created) VALUES(?,?,?,?,?,?)",(mid,kind,body,dedupe,now,now))
    return mid


def email_send(payload,message_id):
    host=os.environ.get('DATAFORGE_SMTP_HOST','')
    if not host: raise ValueError('SMTP is not configured.')
    mode=os.getenv('DATAFORGE_SMTP_MODE','starttls')
    if mode not in ('starttls','tls'): raise ValueError('SMTP requires STARTTLS or implicit TLS.')
    port=int(os.getenv('DATAFORGE_SMTP_PORT','465' if mode=='tls' else '587'))
    sender=os.environ['DATAFORGE_SMTP_FROM']
    for value in (sender,payload['to'],payload['subject']):
        if '\r' in value or '\n' in value: raise ValueError('Invalid mail header.')
    context=ssl.create_default_context(cafile=os.getenv('DATAFORGE_SMTP_CA_FILE') or None)
    message=EmailMessage();message['From']=sender;message['To']=payload['to'];message['Subject']=payload['subject']
    message['Message-ID']='<'+message_id+'@dataforge.local>';message.set_content(payload['body'])
    factory=smtplib.SMTP_SSL if mode=='tls' else smtplib.SMTP
    kwargs={'context':context} if mode=='tls' else {}
    with factory(host,port,timeout=10,**kwargs) as smtp:
        smtp.ehlo()
        if mode=='starttls': smtp.starttls(context=context);smtp.ehlo()
        if os.getenv('DATAFORGE_SMTP_USERNAME'):
            smtp.login(os.environ['DATAFORGE_SMTP_USERNAME'],os.environ['DATAFORGE_SMTP_PASSWORD'])
        refused=smtp.send_message(message)
        if refused: raise ValueError('Mail recipient was rejected.')


def webhook_send(payload,message_id):
    # URL is administrator-provisioned, never taken from an API/user payload.
    url=os.environ.get('DATAFORGE_ALERT_WEBHOOK','');p=urlsplit(url)
    if p.scheme!='https' or not p.hostname or p.username or p.fragment: raise ValueError('Alert webhook must be an HTTPS URL without credentials.')
    headers={'Idempotency-Key':message_id}
    if os.getenv('DATAFORGE_ALERT_WEBHOOK_TOKEN'): headers['Authorization']='Bearer '+os.environ['DATAFORGE_ALERT_WEBHOOK_TOKEN']
    with httpx.Client(timeout=10,follow_redirects=False,trust_env=False) as client:
        r=client.post(url,json=payload,headers=headers)
        r.raise_for_status()
        if not 200<=r.status_code<300: raise ValueError('Webhook was not accepted.')


def dispatch_once(settings,send_email=email_send,send_webhook=webhook_send):
    now=time.time();owner=secrets.token_hex(16)
    with transaction(settings,True) as db:
        db.execute("UPDATE outbox SET status='pending',lease_owner=NULL WHERE status='sending' AND lease_until<?",(now,))
        row=db.execute("SELECT * FROM outbox WHERE status='pending' AND next_at<=? ORDER BY created LIMIT 1",(now,)).fetchone()
        if not row:return False
        row=dict(row)
        db.execute("UPDATE outbox SET status='sending',attempts=attempts+1,lease_until=?,lease_owner=? WHERE id=?",(now+120,owner,row['id']))
    status='delivered';error=None
    try:
        payload=json.loads(unseal(settings,row['payload'],'outbox:'+row['id']))
        if payload.get('expires',float('inf'))<=time.time(): status='expired'
        else: (send_email if row['kind']=='email' else send_webhook)(payload,row['id'])
    except Exception as exc:
        status='failed' if row['attempts']+1>=6 else 'pending'
        error=type(exc).__name__  # Never store server error text containing tokens or destinations.
    with transaction(settings,True) as db:
        changed=db.execute('UPDATE outbox SET status=?,delivered=?,last_error=?,next_at=?,lease_owner=NULL,lease_until=NULL WHERE id=? AND lease_owner=?',
            (status,time.time() if status=='delivered' else None,error,time.time()+min(3600,30*2**row['attempts']),row['id'],owner)).rowcount
        if changed and status in ('delivered','failed','expired'):
            # Delete encrypted message content after terminal state, preserving metadata only.
            db.execute("UPDATE outbox SET payload='' WHERE id=?",(row['id'],))
            audit(db,None,None,'notification.'+status,row['id'],{'kind':row['kind']})
    return True
