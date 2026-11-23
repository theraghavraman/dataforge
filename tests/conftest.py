from pathlib import Path
import base64
import pytest
from fastapi.testclient import TestClient
from app.api import create_app
from app.config import Settings
from app.db import initialize, transaction
from app.security import create_user

PASSWORD = 'Fixture-Only-Password-4829!'

@pytest.fixture
def env(tmp_path):
    settings = Settings(tmp_path / 'data')
    initialize(settings)
    ids = {}
    with transaction(settings, True) as db:
        for role in ['admin','editor','viewer','outside']:
            ids[role] = create_user(db, role+'@example.test', role.title(), PASSWORD, role=='admin')
    app = create_app(settings)
    clients = {}
    for role in ids:
        client = TestClient(app, base_url=settings.origin)
        result = client.post('/api/auth/login', json={'email':role+'@example.test','password':PASSWORD})
        assert result.status_code == 200
        client.headers['X-CSRF-Token'] = result.json()['csrf']
        clients[role] = client
    admin = clients['admin']
    ws = admin.post('/api/workspaces', json={'name':'Quality workspace'}).json()['id']
    other = admin.post('/api/workspaces', json={'name':'Isolated workspace'}).json()['id']
    for role in ['editor','viewer']:
        assert admin.put(f'/api/workspaces/{ws}/members', json={'email':role+'@example.test','role':role}).status_code == 200
    data = {'settings':settings,'app':app,'clients':clients,'ids':ids,'ws':ws,'other':other,'root':tmp_path}
    yield data
    for client in clients.values(): client.close()


def csv_file(env, content='region,amount,status\nNorth,100.50,accepted\nSouth,20,accepted\nNorth,9.50,rejected\nNorth,40,accepted\n', name='orders.csv'):
    c=env['clients']['editor'];r=c.post(f"/api/workspaces/{env['ws']}/files",json={'name':name,'content_base64':base64.b64encode(content.encode()).decode()})
    assert r.status_code==201,r.text
    return r.json()


def submit(env, sql='SELECT 1 AS value', sources=None, key='test-job-key-0001', role='editor'):
    return env['clients'][role].post(f"/api/workspaces/{env['ws']}/jobs",json={'name':'Test query','sql':sql,'sources':sources or []},headers={'Idempotency-Key':key})


def project(env, document=None):
    doc=document or {'projectVersion':'1.0','project':{'id':'p-test','name':'Sample'},'mappings':[],'workflows':[]}
    r=env['clients']['editor'].post(f"/api/workspaces/{env['ws']}/projects",json={'name':'Sample','document':doc})
    assert r.status_code==201,r.text
    return r.json()
