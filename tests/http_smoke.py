"""Real loopback TCP, separate API and worker processes, and restart persistence."""
import base64
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import httpx
from app.config import Settings
from app.db import initialize,transaction
from app.security import create_user

ROOT=Path(__file__).resolve().parent.parent

def free_port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]

def start(settings,log):
    env=os.environ.copy();env['DATAFORGE_DATA_DIR']=str(settings.data_dir);env['DATAFORGE_ORIGIN']=settings.origin
    proc=subprocess.Popen([sys.executable,'-m','app.cli','serve','--port',settings.origin.rsplit(':',1)[1]],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
    for _ in range(100):
        try:
            if httpx.get(settings.origin+'/health/live',timeout=.5).status_code==200:return proc
        except httpx.HTTPError:pass
        if proc.poll() is not None:raise RuntimeError('API process exited before readiness.')
        time.sleep(.1)
    proc.terminate();proc.wait(timeout=10);raise RuntimeError('HTTP readiness timed out.')

def run():
    results=[]
    def check(name,value):
        if not value:raise AssertionError(name)
        results.append({'name':name,'status':'PASS'});print('PASS '+name,flush=True)
    with tempfile.TemporaryDirectory(prefix='df-http-') as d:
        s=Settings(Path(d)/'data',origin='http://127.0.0.1:'+str(free_port()));initialize(s)
        password=secrets.token_urlsafe(24)
        with transaction(s,True) as db:create_user(db,'tcp-test@example.test','TCP test administrator',password,True)
        log=open(Path(d)/'server.log','w');proc=start(s,log)
        try:
            with httpx.Client(base_url=s.origin,timeout=10) as client:
                check('Unauthenticated TCP request is rejected',client.get('/api/workspaces').status_code==401)
                r=client.post('/api/auth/login',json={'email':'tcp-test@example.test','password':password})
                check('Actual HTTP session cookie is issued',r.status_code==200 and 'httponly' in r.headers['set-cookie'].lower())
                client.headers['X-CSRF-Token']=r.json()['csrf']
                ws=client.post('/api/workspaces',json={'name':'TCP validation'}).json()['id'];root='/api/workspaces/'+ws
                file=client.post(root+'/files',json={'name':'values.csv','content_base64':base64.b64encode(b'item,amount\nA,25.5\nB,14.5\n').decode()}).json()
                j=client.post(root+'/jobs',json={'sql':'SELECT SUM(CAST(amount AS REAL)) AS total FROM data','sources':[{'file_id':file['id'],'table':'data'}]},headers={'Idempotency-Key':'tcp-execution-0001'}).json()['job']['id']
                check('TCP-submitted job is durably queued',client.get(root+'/jobs/'+j).json()['status']=='queued')
                env=os.environ.copy();env['DATAFORGE_DATA_DIR']=str(s.data_dir);env['DATAFORGE_ORIGIN']=s.origin
                worker=subprocess.run([sys.executable,'-m','app.cli','worker','--once'],cwd=ROOT,env=env,capture_output=True,timeout=30)
                check('Separate worker process exits successfully',worker.returncode==0)
                result=client.get(root+'/jobs/'+j).json()
                check('CSV aggregation returns independently expected total',result['result']['rows']==[[40.0]])
                check('Output downloads over real HTTP',client.get(root+'/jobs/'+j+'/output').status_code==200)
                check('TCP cross-origin write is rejected',client.post('/api/workspaces',json={'name':'forbidden'},headers={'Origin':'https://evil.example'}).status_code==403)
                studio=client.get('/studio?workspace='+ws+'&project=none')
                check('Designer delivered with same-origin-only CSP and bridge',studio.status_code==200 and 'team-bridge.js' in studio.text and 'https://cdn.jsdelivr.net' not in studio.headers['content-security-policy'])
                proc.terminate();proc.wait(timeout=10);proc=start(s,log)
                check('Job output survives actual API process restart',client.get(root+'/jobs/'+j).json()['result']['rows']==[[40.0]])
                check('TCP sign-out invalidates the session',client.post('/api/auth/logout').status_code==200 and client.get('/api/auth/me').status_code==401)
        finally:
            if proc.poll() is None:proc.terminate();proc.wait(timeout=10)
            log.close()
    (ROOT/'evidence'/'http-results.json').write_text(json.dumps({'method':'Real loopback HTTP and separate Python worker subprocess','results':results},indent=2))
    return results

if __name__=='__main__':run()
