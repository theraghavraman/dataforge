"""Explicitly provisioned OpenID Connect authorization-code login (RS256 + PKCE).

Accounts are linked by exact (issuer, subject); unverified email claims never create
or link accounts. Local MFA remains mandatory when configured, including after SSO.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from urllib.parse import urlencode, urlsplit
import httpx
import jwt
from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse
from .db import transaction, audit
from .identity import digest, seal, unseal, throttle, start_session

BINDING_COOKIE='df_oidc_binding'


def configured():
    issuer=os.getenv('DATAFORGE_OIDC_ISSUER','')
    client_id=os.getenv('DATAFORGE_OIDC_CLIENT_ID','')
    if not issuer or not client_id: raise HTTPException(503,'Single sign-on is not configured.')
    p=urlsplit(issuer)
    if p.scheme!='https' or not p.hostname or p.username or p.query or p.fragment:
        raise ValueError('OIDC issuer must be an HTTPS URL without query, fragment, or credentials.')
    return issuer,client_id


def http_client():
    return httpx.Client(timeout=10,follow_redirects=False,trust_env=False)


def json_response(response):
    response.raise_for_status()
    if response.status_code!=200 or len(response.content)>256*1024: raise ValueError('Invalid identity provider response.')
    obj=response.json()
    if not isinstance(obj,dict): raise ValueError('Invalid identity provider response.')
    return obj


def endpoint(url,issuer):
    p=urlsplit(url)
    allowed={urlsplit(issuer).hostname}|{x.strip().lower() for x in os.getenv('DATAFORGE_OIDC_ALLOWED_HOSTS','').split(',') if x.strip()}
    if p.scheme!='https' or p.hostname not in allowed or p.username or p.fragment:
        raise ValueError('Identity provider endpoint is outside the configured HTTPS host allowlist.')
    return url


def discovery(client,issuer):
    doc=json_response(client.get(issuer.rstrip('/')+'/.well-known/openid-configuration'))
    if doc.get('issuer')!=issuer: raise ValueError('Identity provider issuer mismatch.')
    for name in ('authorization_endpoint','token_endpoint','jwks_uri'): endpoint(doc[name],issuer)
    if 'RS256' not in doc.get('id_token_signing_alg_values_supported',[]): raise ValueError('Identity provider must support RS256 ID tokens.')
    if 'code' not in doc.get('response_types_supported',[]): raise ValueError('Identity provider must support authorization code flow.')
    if 'S256' not in doc.get('code_challenge_methods_supported',[]): raise ValueError('Identity provider must support PKCE S256.')
    return doc


def validate_token(client,doc,token,nonce,client_id):
    if not isinstance(token,str) or len(token)>32768: raise ValueError('Invalid identity token.')
    header=jwt.get_unverified_header(token)
    if header.get('alg')!='RS256' or header.get('crit'): raise ValueError('Unsupported identity token algorithm.')
    keys=json_response(client.get(doc['jwks_uri'])).get('keys',[])
    if not isinstance(keys,list) or len(keys)>32: raise ValueError('Invalid key set.')
    candidates=[k for k in keys if k.get('kid')==header.get('kid') and k.get('kty')=='RSA' and k.get('use','sig')=='sig' and k.get('alg','RS256')=='RS256']
    if len(candidates)!=1: raise ValueError('Identity token signing key is ambiguous or unknown.')
    key=jwt.PyJWK.from_dict(candidates[0],algorithm='RS256').key
    if key.key_size<2048: raise ValueError('Identity provider signing key is too small.')
    claims=jwt.decode(token,key,algorithms=['RS256'],issuer=doc['issuer'],audience=client_id,
        leeway=30,options={'require':['iss','sub','aud','exp','iat','nonce']})
    if not isinstance(claims['sub'],str) or not 1<=len(claims['sub'])<=255: raise ValueError('Invalid identity subject.')
    if not isinstance(claims['nonce'],str) or not hmac.compare_digest(claims['nonce'],nonce): raise ValueError('Identity nonce mismatch.')
    if claims['iat']<time.time()-900 or claims['exp']-claims['iat']>86400: raise ValueError('Identity token is not recent enough.')
    audience=claims['aud']
    if isinstance(audience,list) and len(audience)>1 and claims.get('azp')!=client_id: raise ValueError('Authorized party is required.')
    if 'azp' in claims and claims['azp']!=client_id: raise ValueError('Authorized party mismatch.')
    required_acr=os.getenv('DATAFORGE_OIDC_REQUIRED_ACR','')
    if required_acr and claims.get('acr')!=required_acr: raise ValueError('Identity provider assurance requirement was not met.')
    return claims


def install_oidc_routes(app,settings):
    @app.get('/api/auth/oidc/start')
    def begin(request: Request):
        throttle(settings,request,request.client.host if request.client else 'unknown','oidc-start',30,30)
        issuer,client_id=configured()
        try:
            with http_client() as client: doc=discovery(client,issuer)
        except Exception: raise HTTPException(503,'Identity provider configuration could not be verified.')
        state=secrets.token_urlsafe(32);binding=secrets.token_urlsafe(32);nonce=secrets.token_urlsafe(32);verifier=secrets.token_urlsafe(48)
        with transaction(settings,True) as db:
            db.execute('DELETE FROM oidc_states WHERE expires<?',(time.time(),))
            db.execute('INSERT INTO oidc_states VALUES(?,?,?,?,?)',(digest(state),digest(binding),nonce,seal(settings,verifier,'oidc:'+digest(state)),time.time()+600))
        params={'response_type':'code','client_id':client_id,'redirect_uri':settings.origin+'/api/auth/oidc/callback',
                'scope':'openid','state':state,'nonce':nonce,'code_challenge_method':'S256',
                'code_challenge':base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')}
        if os.getenv('DATAFORGE_OIDC_REQUIRED_ACR'): params['acr_values']=os.environ['DATAFORGE_OIDC_REQUIRED_ACR']
        response=RedirectResponse(doc['authorization_endpoint']+('&' if '?' in doc['authorization_endpoint'] else '?')+urlencode(params),status_code=303)
        response.set_cookie(BINDING_COOKIE,binding,httponly=True,secure=settings.secure_cookies,samesite='lax',max_age=600,path='/api/auth/oidc')
        return response

    @app.get('/api/auth/oidc/callback')
    def callback(request: Request):
        state=request.query_params.get('state','');code=request.query_params.get('code','')
        if not 32<=len(state)<=128 or not 1<=len(code)<=2048: raise HTTPException(400,'Invalid identity-provider callback.')
        issuer,client_id=configured()
        # Consume the state before making external requests. Replays fail even when token exchange fails.
        with transaction(settings,True) as db:
            row=db.execute('SELECT * FROM oidc_states WHERE state_hash=?',(digest(state),)).fetchone()
            if not row or row['expires']<time.time() or not hmac.compare_digest(row['binding_hash'],digest(request.cookies.get(BINDING_COOKIE,''))):
                raise HTTPException(400,'SSO session is invalid or expired. Start sign-in again.')
            row=dict(row);db.execute('DELETE FROM oidc_states WHERE state_hash=?',(digest(state),))
        try:
            with http_client() as client:
                doc=discovery(client,issuer)
                data={'grant_type':'authorization_code','code':code,'client_id':client_id,
                      'redirect_uri':settings.origin+'/api/auth/oidc/callback','code_verifier':unseal(settings,row['verifier'],'oidc:'+digest(state))}
                secret=os.getenv('DATAFORGE_OIDC_CLIENT_SECRET','')
                auth=(client_id,secret) if secret else None
                tokens=json_response(client.post(doc['token_endpoint'],data=data,auth=auth))
                claims=validate_token(client,doc,tokens['id_token'],row['nonce'],client_id)
                if claims.get('at_hash'):
                    access=tokens.get('access_token','')
                    expected=base64.urlsafe_b64encode(hashlib.sha256(access.encode()).digest()[:16]).decode().rstrip('=')
                    if not hmac.compare_digest(expected,claims['at_hash']): raise ValueError('Access token hash mismatch.')
        except Exception: raise HTTPException(401,'SSO verification failed. Start sign-in again.')
        with transaction(settings,True) as db:
            user=db.execute('SELECT u.* FROM oidc_links l JOIN users u ON u.id=l.user_id WHERE issuer=? AND subject=? AND u.active=1',(issuer,claims['sub'])).fetchone()
            if not user: raise HTTPException(403,'An administrator must link this identity to an active DataForge account.')
            # Do not silently bypass an enrolled local factor. Require that factor in a
            # short-lived second step, bound to a one-time server-side challenge.
            enrolled=bool(db.execute('SELECT 1 FROM mfa WHERE user_id=? AND enabled=1',(user['id'],)).fetchone())
            if enrolled:
                challenge=secrets.token_urlsafe(48)
                # Reuse oidc_states as a challenge envelope; never expose the account ID in the URL.
                db.execute('INSERT INTO oidc_states VALUES(?,?,?,?,?)',(digest(challenge),digest(request.cookies.get(BINDING_COOKIE,'')),
                    'mfa:'+user['id'],seal(settings,issuer,'oidc:'+digest(challenge)),time.time()+300))
                response=RedirectResponse('/#sso-factor='+challenge,status_code=303)
                # Binding path also covers the MFA completion endpoint.
                response.set_cookie(BINDING_COOKIE,request.cookies.get(BINDING_COOKIE,''),httponly=True,secure=settings.secure_cookies,samesite='strict',max_age=300,path='/api/auth/oidc')
                return response
            response=start_session(db,settings,user,method='oidc')
        response.status_code=303;response.headers['location']='/'
        response.delete_cookie(BINDING_COOKIE,path='/api/auth/oidc')
        return response

    from .models import FactorConfirm, Model
    from pydantic import Field
    class OIDCComplete(Model):
        challenge: str=Field(min_length=32,max_length=128)
        code: str=Field(min_length=1,max_length=128)
    @app.post('/api/auth/oidc/factor')
    def factor(payload: OIDCComplete,request: Request):
        from .identity import verify_factor
        throttle(settings,request,digest(payload.challenge),'oidc-factor',5,30)
        with transaction(settings,True) as db:
            row=db.execute('SELECT * FROM oidc_states WHERE state_hash=?',(digest(payload.challenge),)).fetchone()
            if not row or row['expires']<time.time() or not row['nonce'].startswith('mfa:') or not hmac.compare_digest(row['binding_hash'],digest(request.cookies.get(BINDING_COOKIE,''))):
                raise HTTPException(400,'SSO factor challenge is invalid or expired.')
            user_id=row['nonce'][4:];user=db.execute('SELECT * FROM users WHERE id=? AND active=1',(user_id,)).fetchone()
            issuer=unseal(settings,row['verifier'],'oidc:'+digest(payload.challenge))
            if not user or not db.execute('SELECT 1 FROM oidc_links WHERE user_id=? AND issuer=?',(user_id,issuer)).fetchone(): raise HTTPException(403,'Identity access has been revoked.')
            if not verify_factor(db,settings,user_id,payload.code): raise HTTPException(401,'Incorrect or reused authenticator/recovery code.')
            db.execute('DELETE FROM oidc_states WHERE state_hash=?',(digest(payload.challenge),))
            response=start_session(db,settings,user,method='oidc+mfa')
            response.delete_cookie(BINDING_COOKIE,path='/api/auth/oidc')
            return response
