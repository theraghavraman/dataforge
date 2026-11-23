"""Real process smoke test of shipped local launcher and example importer."""
import base64,getpass,json,os,secrets,signal,subprocess,sys,tempfile,time
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parent.parent;sys.path.insert(0,str(ROOT))
from app.config import Settings
from app.db import initialize,transaction
from app.security import create_user
from tests.http_smoke import free_port
checks=[]
def checked(name,value):
 checks.append({'name':name,'status':'PASS' if value else 'FAIL'});print(checks[-1],flush=True)
 if not value:raise AssertionError(name)
with tempfile.TemporaryDirectory(prefix='df-package-') as tmp:
 port=free_port();settings=Settings(Path(tmp)/'data',origin=f'http://127.0.0.1:{port}');initialize(settings);password=secrets.token_urlsafe(24)
 with transaction(settings,True) as db:create_user(db,'package@example.test','Package QA',password,True)
 env=os.environ.copy();env.update(DATAFORGE_ENV='development',DATAFORGE_DATA_DIR=str(settings.data_dir),DATAFORGE_ORIGIN=settings.origin)
 with open(Path(tmp)/'launcher.log','w') as log:
  p=subprocess.Popen([sys.executable,'scripts/run_local.py','--port',str(port)],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
  try:
   with httpx.Client(base_url=settings.origin,timeout=5,trust_env=False) as client:
    for _ in range(100):
     try:
      if client.get('/health/ready').json().get('worker_available'):break
     except httpx.HTTPError:pass
     time.sleep(.1)
    checked('Local launcher starts a reachable API and independent worker',client.get('/health/ready').json()['worker_available'])
    with transaction(settings) as db:
     heartbeat=db.execute("SELECT value FROM meta WHERE key='maintenance.heartbeat'").fetchone()
     checked('Local launcher also starts the maintenance service',bool(heartbeat))
    # Exercise the shipped helper while supplying temporary credentials through stdin,
    # not process arguments or any saved file. Its production CLI still uses getpass.
    code="import runpy,sys,getpass;values=iter([sys.stdin.readline().rstrip('\\n'),'']);getpass.getpass=lambda *a,**k:next(values);sys.argv=['scripts/load_example.py','--origin',"+repr(settings.origin)+",'--email','package@example.test','--create-workspace','Package example'];runpy.run_path('scripts/load_example.py',run_name='__main__')"
    sample=subprocess.run([sys.executable,'-c',code],input=password+'\n',text=True,capture_output=True,cwd=ROOT,env=env,timeout=20)
    checked('Shipped example importer succeeds without saved credentials',sample.returncode==0)
    imported=json.loads(sample.stdout);login=client.post('/api/auth/login',json={'email':'package@example.test','password':password}).json();client.headers['X-CSRF-Token']=login['csrf']
    root='/api/workspaces/'+imported['workspace_id'];body={'revision':1,'mapping_id':'mapping-example','target_id':'output','bindings':{'source':imported['source_id']}}
    job=client.post(root+'/projects/'+imported['project_id']+'/run',json=body,headers={'Idempotency-Key':'package-example-job'}).json()['job']['id']
    for _ in range(100):
     detail=client.get(root+'/jobs/'+job).json()
     if detail['status'] not in ('queued','running'):break
     time.sleep(.1)
    checked('Bundled visual example executes to North 130 and South 40',detail.get('result',{}).get('rows')==[['North',130.0],['South',40.0]])
  finally:
   p.send_signal(signal.SIGINT)
   try:p.wait(timeout=50)
   except subprocess.TimeoutExpired:p.kill();p.wait()
  checked('Launcher stops its supervised local services cleanly',p.returncode==0)
Path(ROOT/'evidence/package-smoke.json').write_text(json.dumps({'method':'Shipped launcher and example importer against real local subprocess services; temporary credentials and data removed','checks':checks},indent=2))
