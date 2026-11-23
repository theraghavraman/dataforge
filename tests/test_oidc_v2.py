import base64
import hashlib
import json
import time
from urllib.parse import parse_qs,urlsplit
import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from app import oidc
from app.db import transaction
from tests.test_identity_v2 import enroll

@pytest.fixture
def provider(env,monkeypatch):
    issuer='https://id.example.test';client_id='dataforge-test'
    monkeypatch.setenv('DATAFORGE_OIDC_ISSUER',issuer);monkeypatch.setenv('DATAFORGE_OIDC_CLIENT_ID',client_id)
    private=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    public=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()));public.update(kid='test-key',use='sig',alg='RS256')
    state={'nonce':'','subject':'subject-editor','changes':{},'tokens':0}
    def handle(request):
        path=request.url.path
        if path.endswith('/.well-known/openid-configuration'):
            return httpx.Response(200,json={'issuer':issuer,'authorization_endpoint':issuer+'/authorize','token_endpoint':issuer+'/token','jwks_uri':issuer+'/keys','id_token_signing_alg_values_supported':['RS256'],'response_types_supported':['code'],'code_challenge_methods_supported':['S256']})
        if path=='/keys':return httpx.Response(200,json={'keys':[public]})
        if path=='/token':
            data=parse_qs(request.content.decode());assert data['grant_type']==['authorization_code'];assert data['redirect_uri']==[env['settings'].origin+'/api/auth/oidc/callback']
            verifier=data['code_verifier'][0]
            assert base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')==state['challenge']
            now=int(time.time());claims={'iss':issuer,'sub':state['subject'],'aud':client_id,'exp':now+300,'iat':now,'nonce':state['nonce'],'email':'admin@example.test'};claims.update(state['changes'])
            state['tokens']+=1
            return httpx.Response(200,json={'id_token':jwt.encode(claims,private,algorithm='RS256',headers={'kid':'test-key'})})
        raise AssertionError(str(request.url))
    monkeypatch.setattr(oidc,'http_client',lambda:httpx.Client(transport=httpx.MockTransport(handle),follow_redirects=False))
    with transaction(env['settings'],True) as db:db.execute('INSERT INTO oidc_links VALUES(?,?,?)',(issuer,'subject-editor',env['ids']['editor']))
    c=TestClient(env['app'],base_url=env['settings'].origin,follow_redirects=False)
    def start():
        r=c.get('/api/auth/oidc/start');assert r.status_code==303,r.text
        query=parse_qs(urlsplit(r.headers['location']).query)
        state['nonce']=query['nonce'][0];state['challenge']=query['code_challenge'][0]
        return '/api/auth/oidc/callback?'+ 'state='+query['state'][0]+'&code=sample-code'
    return c,state,start


def test_oidc_code_flow_pkce_and_replay(provider):
    c,state,start=provider;url=start();r=c.get(url);assert r.status_code==303,r.text
    assert c.get('/api/auth/me').json()['user']['email']=='editor@example.test'
    assert c.get(url).status_code==400 and state['tokens']==1


@pytest.mark.parametrize('claims',[{'nonce':'wrong'},{'iss':'https://wrong.example.test'},{'aud':'wrong'},{'exp':1},{'iat':1},{'azp':'another-client'},{'sub':''},{'aud':['dataforge-test','other'] }])
def test_oidc_rejects_invalid_claims(provider,claims):
    c,state,start=provider;url=start();state['changes']=claims
    assert c.get(url).status_code==401
    assert c.get('/api/auth/me').status_code==401


def test_oidc_never_links_by_email(provider):
    c,state,start=provider;url=start();state['subject']='unlinked-subject'
    assert c.get(url).status_code==403
    assert c.get('/api/auth/me').status_code==401


def test_oidc_requires_browser_binding(provider,env):
    c,state,start=provider;url=start();other=TestClient(env['app'],base_url=env['settings'].origin,follow_redirects=False)
    assert other.get(url).status_code==400
    assert c.get(url).status_code==303


def test_oidc_mfa_cannot_be_bypassed(provider,env):
    _,codes=enroll(env);c,state,start=provider;url=start();r=c.get(url)
    assert r.status_code==303 and '#sso-factor=' in r.headers['location']
    assert c.get('/api/auth/me').status_code==401
    challenge=r.headers['location'].split('#sso-factor=')[1]
    assert c.post('/api/auth/oidc/factor',json={'challenge':challenge,'code':'wrong'}).status_code==401
    assert c.post('/api/auth/oidc/factor',json={'challenge':challenge,'code':codes[0]}).status_code==200
    assert c.get('/api/auth/me').json()['user']['email']=='editor@example.test'
    assert c.post('/api/auth/oidc/factor',json={'challenge':challenge,'code':codes[1]}).status_code==400


def test_oidc_endpoint_allowlist():
    with pytest.raises(ValueError):oidc.endpoint('https://unapproved.example.test/token','https://id.example.test')
    with pytest.raises(ValueError):oidc.endpoint('http://id.example.test/token','https://id.example.test')


def test_oidc_disabled_without_configuration(env,monkeypatch):
    monkeypatch.delenv('DATAFORGE_OIDC_ISSUER',raising=False)
    assert env['clients']['admin'].get('/api/auth/oidc/start').status_code==503
