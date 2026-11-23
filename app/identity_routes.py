import base64
import os
import secrets
import time
from fastapi import Request, HTTPException
from .db import transaction, audit
from .identity import (seal, unseal, digest, throttle, valid_counter, verify_factor,
                       recovery_codes, enrollment_uri)
from .security import session_user, verify_password, validate_password, PH
from .models import VerifyFactor, FactorConfirm, RecoveryRequest, RecoveryComplete


def install_identity_routes(app, settings):
    @app.get('/api/auth/options')
    def options():
        return {'password_recovery': bool(os.getenv('DATAFORGE_SMTP_HOST')),
                'sso': bool(os.getenv('DATAFORGE_OIDC_ISSUER')),
                'mfa_required': settings.require_mfa}

    @app.get('/api/auth/mfa')
    def status(request: Request):
        with transaction(settings, True) as db:
            u = session_user(db, request, settings)
            m = db.execute('SELECT enabled FROM mfa WHERE user_id=?', (u['user_id'],)).fetchone()
            count = db.execute('SELECT count(*) FROM recovery_codes WHERE user_id=?', (u['user_id'],)).fetchone()[0]
            return {'enabled': bool(m and m['enabled']), 'required': settings.require_mfa, 'recovery_codes_remaining':count}

    @app.post('/api/auth/mfa/setup')
    def setup(payload: VerifyFactor, request: Request):
        throttle(settings, request, request.cookies.get('df_session',''), 'mfa')
        with transaction(settings, True) as db:
            u = session_user(db, request, settings)
            row = db.execute('SELECT password_hash FROM users WHERE id=?', (u['user_id'],)).fetchone()
            if not verify_password(row[0], payload.password): raise HTTPException(403,'Current password is incorrect.')
            old = db.execute('SELECT enabled FROM mfa WHERE user_id=?', (u['user_id'],)).fetchone()
            if old and old['enabled']: raise HTTPException(409,'MFA is already enabled. Disable it with your current factor before replacing it.')
            secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip('=')
            db.execute('INSERT INTO mfa VALUES(?,?,0,?,-1) ON CONFLICT(user_id) DO UPDATE SET secret=excluded.secret,enabled=0,expires=excluded.expires,last_counter=-1',
                       (u['user_id'],seal(settings,secret,'mfa:'+u['user_id']),time.time()+600))
            audit(db,u['user_id'],None,'auth.mfa_setup_started')
            return {'secret':secret,'otpauth_uri':enrollment_uri(u['email'],secret),'expires_in':600}

    @app.post('/api/auth/mfa/confirm')
    def confirm(payload: FactorConfirm, request: Request):
        throttle(settings,request,request.cookies.get('df_session',''),'mfa')
        with transaction(settings, True) as db:
            u=session_user(db,request,settings)
            row=db.execute('SELECT * FROM mfa WHERE user_id=?',(u['user_id'],)).fetchone()
            if not row or row['enabled'] or row['expires'] <= time.time(): raise HTTPException(400,'Start a fresh MFA enrollment.')
            counter=valid_counter(unseal(settings,row['secret'],'mfa:'+u['user_id']),payload.code)
            if counter is None: raise HTTPException(400,'Incorrect or expired authenticator code.')
            db.execute('UPDATE mfa SET enabled=1,expires=0,last_counter=? WHERE user_id=?',(counter,u['user_id']))
            codes=recovery_codes(db,u['user_id'])
            db.execute('DELETE FROM sessions WHERE user_id=? AND token_hash<>?',(u['user_id'],u['token_hash']))
            audit(db,u['user_id'],None,'auth.mfa_enabled')
            return {'enabled':True,'recovery_codes':codes,'notice':'Save these codes securely now. Each can be used once; they will not be shown again.'}

    @app.post('/api/auth/mfa/recovery-codes')
    def rotate_codes(payload: VerifyFactor, request: Request):
        throttle(settings,request,request.cookies.get('df_session',''),'mfa')
        with transaction(settings, True) as db:
            u=session_user(db,request,settings)
            row=db.execute('SELECT password_hash FROM users WHERE id=?',(u['user_id'],)).fetchone()
            if not db.execute('SELECT 1 FROM mfa WHERE user_id=? AND enabled=1',(u['user_id'],)).fetchone(): raise HTTPException(409,'Enable MFA first.')
            if not verify_password(row[0],payload.password) or not verify_factor(db,settings,u['user_id'],payload.code):
                raise HTTPException(403,'Password or authenticator/recovery code is incorrect.')
            codes=recovery_codes(db,u['user_id']);audit(db,u['user_id'],None,'auth.recovery_codes_rotated')
            return {'recovery_codes':codes}

    @app.post('/api/auth/mfa/disable')
    def disable(payload: VerifyFactor, request: Request):
        throttle(settings,request,request.cookies.get('df_session',''),'mfa')
        with transaction(settings, True) as db:
            u=session_user(db,request,settings)
            if settings.require_mfa: raise HTTPException(403,'MFA is required by deployment policy. Contact an administrator for a supervised factor reset.')
            row=db.execute('SELECT password_hash FROM users WHERE id=?',(u['user_id'],)).fetchone()
            if not verify_password(row[0],payload.password) or not verify_factor(db,settings,u['user_id'],payload.code):
                raise HTTPException(403,'Password or authenticator/recovery code is incorrect.')
            db.execute('DELETE FROM mfa WHERE user_id=?',(u['user_id'],));db.execute('DELETE FROM recovery_codes WHERE user_id=?',(u['user_id'],))
            db.execute('DELETE FROM sessions WHERE user_id=?',(u['user_id'],))
            db.execute('DELETE FROM oidc_states WHERE nonce=?',('mfa:'+u['user_id'],))
            audit(db,u['user_id'],None,'auth.mfa_disabled')
            return {'ok':True,'sign_in_required':True}

    @app.post('/api/auth/recovery/request', status_code=202)
    def recovery_request(payload: RecoveryRequest, request: Request):
        address=payload.email.strip().lower()
        throttle(settings,request,address,'recovery',5,30)
        if not os.getenv('DATAFORGE_SMTP_HOST'): raise HTTPException(503,'Email recovery is not configured. Contact your administrator.')
        from .outbox import enqueue_message
        with transaction(settings,True) as db:
            user=db.execute('SELECT id,email FROM users WHERE email=? AND active=1',(address,)).fetchone()
            if user:
                token=secrets.token_urlsafe(48)
                db.execute('DELETE FROM reset_tokens WHERE user_id=? OR expires<?',(user['id'],time.time()))
                db.execute('INSERT INTO reset_tokens VALUES(?,?,?)',(digest(token),user['id'],time.time()+900))
                # Fragment tokens are not sent in HTTP paths/referrers or server access logs.
                url=settings.origin+'/#reset='+token
                enqueue_message(db,settings,'email',{'to':user['email'],'subject':'DataForge password reset',
                    'body':'A password reset was requested for your DataForge account. This link expires in 15 minutes and can be used once:\n\n'+url+'\n\nYour authenticator remains required. Ignore this message if you did not request it.',
                    'expires':time.time()+900},'reset:'+digest(token))
                audit(db,None,None,'auth.password_reset_requested',user['id'])
        return {'message':'When an active account matches, a reset message will be sent. Check your inbox and spam folder.'}

    @app.post('/api/auth/recovery/complete')
    def recovery_complete(payload: RecoveryComplete, request: Request):
        throttle(settings,request,digest(payload.token),'reset',10,30)
        validate_password(payload.new_password)
        hashed=PH.hash(payload.new_password)
        with transaction(settings,True) as db:
            row=db.execute('SELECT r.*,u.active FROM reset_tokens r JOIN users u ON u.id=r.user_id WHERE token_hash=?',(digest(payload.token),)).fetchone()
            if not row or not row['active'] or row['expires']<=time.time(): raise HTTPException(400,'Reset link is invalid or expired. Request a new link.')
            db.execute('UPDATE users SET password_hash=? WHERE id=?',(hashed,row['user_id']))
            db.execute('DELETE FROM reset_tokens WHERE user_id=?',(row['user_id'],))
            db.execute('DELETE FROM sessions WHERE user_id=?',(row['user_id'],))
            db.execute('DELETE FROM oidc_states WHERE nonce=?',('mfa:'+row['user_id'],))
            audit(db,row['user_id'],None,'auth.password_reset_completed')
        return {'ok':True,'sign_in_required':True,'mfa_preserved':True}

    @app.get('/api/auth/sessions')
    def list_sessions(request: Request):
        with transaction(settings,True) as db:
            u=session_user(db,request,settings)
            rows=db.execute('SELECT token_hash,created,last_seen,expires FROM sessions WHERE user_id=? AND expires>? ORDER BY created DESC',(u['user_id'],time.time())).fetchall()
            return [{'id':digest(r['token_hash']), 'created':r['created'],'last_seen':r['last_seen'],'expires':r['expires'],'current':r['token_hash']==u['token_hash']} for r in rows]

    @app.delete('/api/auth/sessions/{session_id}')
    def revoke_session(session_id: str, request: Request):
        with transaction(settings,True) as db:
            u=session_user(db,request,settings)
            rows=db.execute('SELECT token_hash FROM sessions WHERE user_id=?',(u['user_id'],)).fetchall()
            row=next((r for r in rows if digest(r[0])==session_id),None)
            if not row: raise HTTPException(404,'Session not found.')
            db.execute('DELETE FROM sessions WHERE token_hash=?',(row[0],));audit(db,u['user_id'],None,'auth.session_revoked')
            return {'ok':True,'current':row[0]==u['token_hash']}
