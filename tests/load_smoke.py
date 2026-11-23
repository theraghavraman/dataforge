"""Bounded local concurrency experiment, not production capacity certification."""
from concurrent.futures import ThreadPoolExecutor
import base64
import json
import os
from pathlib import Path
import secrets
import statistics
import subprocess
import sys
import tempfile
import time
import httpx
from app.config import Settings
from app.db import initialize,transaction
from app.security import create_user
from tests.http_smoke import free_port,start

ROOT=Path(__file__).resolve().parent.parent

def run():
    checks=[]
    def check(name,value):
        checks.append({'name':name,'status':'PASS' if value else 'FAIL'})
        if not value:raise AssertionError(name)
        print('PASS '+name,flush=True)
    with tempfile.TemporaryDirectory(prefix='df-load-') as tmp:
        settings=Settings(Path(tmp)/'data',origin='http://127.0.0.1:'+str(free_port()));initialize(settings);password=secrets.token_urlsafe(24)
        with transaction(settings,True) as db:create_user(db,'load@example.test','Synthetic load operator',password,True)
        with open(Path(tmp)/'api.log','w') as log:
            server=start(settings,log);workers=[]
            try:
                with httpx.Client(base_url=settings.origin,timeout=30,trust_env=False) as client:
                    login=client.post('/api/auth/login',json={'email':'load@example.test','password':password}).json();client.headers['X-CSRF-Token']=login['csrf']
                    ws=client.post('/api/workspaces',json={'name':'Bounded concurrency QA'}).json()['id'];root='/api/workspaces/'+ws
                    csv='item,amount\n'+''.join(f'{i},{i%17}\n' for i in range(10000));expected=sum(i%17 for i in range(10000))
                    source=client.post(root+'/files',json={'name':'synthetic.csv','content_base64':base64.b64encode(csv.encode()).decode()}).json()['id']
                    payload={'name':'Concurrent fixture','sql':'SELECT COUNT(*) AS rows,SUM(CAST(amount AS INTEGER)) AS total FROM inputs','sources':[{'file_id':source,'table':'inputs'}]}
                    def post(index):
                        started=time.perf_counter();response=client.post(root+'/jobs',json=payload,headers={'Idempotency-Key':'bounded-load-job-'+str(index%24)})
                        return response.status_code,response.json(),(time.perf_counter()-started)*1000
                    begun=time.perf_counter()
                    with ThreadPoolExecutor(max_workers=8) as pool:posted=list(pool.map(post,range(48)))
                    check('48 concurrent submissions return successful HTTP responses',all(status in (200,201) for status,_,_ in posted))
                    check('Duplicate idempotency keys create exactly 24 jobs',len({body['job']['id'] for _,body,_ in posted})==24 and sum(status==201 for status,_,_ in posted)==24)
                    environment=os.environ.copy();environment.update(DATAFORGE_DATA_DIR=str(settings.data_dir),DATAFORGE_ORIGIN=settings.origin)
                    for _ in range(2):workers.append(subprocess.Popen([sys.executable,'-m','app.cli','worker'],cwd=ROOT,env=environment,stdout=log,stderr=subprocess.STDOUT))
                    deadline=time.monotonic()+90
                    while time.monotonic()<deadline:
                        jobs=client.get(root+'/jobs').json()
                        if all(j['status'] not in ('queued','running') for j in jobs):break
                        time.sleep(.15)
                    check('Two independent workers finish all jobs without lost leases',len(jobs)==24 and all(j['status']=='succeeded' and j['attempts']==1 for j in jobs))
                    results=[client.get(root+'/jobs/'+j['id']).json() for j in jobs]
                    check('Every 10000-row result matches independently computed totals',all(j['result']['rows']==[[10000,expected]] for j in results))
                    ready=client.get('/health/ready')
                    check('API remains ready after concurrent submissions and execution',ready.status_code==200)
                    timings=sorted(v for _,_,v in posted)
                    report={'scope':'Synthetic bounded loopback HTTP experiment on this disposable environment; not a production SLA or capacity limit. Shared operator session, 8 concurrent request threads, 2 worker processes.','checks':checks,'requests':48,'unique_jobs':24,'csv_rows_per_job':10000,'expected_sum':expected,'submission_latency_ms':{'median':round(statistics.median(timings),2),'p95':round(timings[int(len(timings)*.95)-1],2),'max':round(max(timings),2)},'elapsed_seconds':round(time.perf_counter()-begun,2)}
            finally:
                for worker in workers:worker.terminate()
                for worker in workers:
                    try:worker.wait(timeout=15)
                    except subprocess.TimeoutExpired:worker.kill();worker.wait()
                server.terminate();server.wait(timeout=10)
    (ROOT/'evidence/load-results.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))

if __name__=='__main__':run()
