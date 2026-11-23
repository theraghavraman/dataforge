import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import time
import pytest
from fastapi.testclient import TestClient
from app.api import create_app
from app.db import transaction, verify_audit
from app.worker import Worker
from .conftest import project,csv_file,submit


def test_project_conflict_revision_restore(env):
    p=project(env);c=env['clients']['editor'];root=f"/api/workspaces/{env['ws']}/projects/{p['id']}"
    changed={**p['document'],'project':{'id':'p-test','name':'Changed'}}
    r=c.put(root,json={'name':'Changed','document':changed,'revision':1});assert r.status_code==200 and r.json()['revision']==2
    r=c.put(root,json={'name':'Stale','document':{},'revision':1});assert r.status_code==409
    assert c.get(root).json()['name']=='Changed'
    r=c.post(root+'/restore',json={'revision':2,'restore_revision':1});assert r.status_code==200 and r.json()['revision']==3
    assert r.json()['document']==p['document']


def test_concurrent_project_saves_only_one_wins(env):
    p=project(env);url=f"/api/workspaces/{env['ws']}/projects/{p['id']}"
    def save(index):
        c=TestClient(env['app'],base_url=env['settings'].origin)
        c.cookies.update(env['clients']['editor'].cookies);c.headers['X-CSRF-Token']=env['clients']['editor'].headers['X-CSRF-Token']
        return c.put(url,json={'name':'Concurrent '+str(index),'document':p['document'],'revision':1}).status_code
    with ThreadPoolExecutor(max_workers=8) as pool:statuses=list(pool.map(save,range(8)))
    assert statuses.count(200)==1 and statuses.count(409)==7,statuses


def test_last_twenty_project_revisions_retained(env):
    p=project(env);c=env['clients']['editor'];url=f"/api/workspaces/{env['ws']}/projects/{p['id']}"
    for revision in range(1,24):assert c.put(url,json={'name':'Rev '+str(revision),'document':p['document'],'revision':revision}).status_code==200
    rows=c.get(url+'/revisions').json();assert len(rows)==20 and rows[0]['revision']==24 and rows[-1]['revision']==5

@pytest.mark.parametrize('doc',[
 {'projectVersion':'2.0','project':{},'mappings':[]},
 {'projectVersion':'1.0','project':{},'mappings':[{'mapping':{'id':'m'},'nodes':[{'id':'x'},{'id':'x'}]}]},
 {'projectVersion':'1.0','project':{},'mappings':[{'mapping':{'id':'m'},'nodes':[{'id':'x'}],'links':[{'from':'x','to':'missing'}]}]},
 {'projectVersion':'1.0','project':{},'mappings':[{'mapping':{'id':'m'}},{'mapping':{'id':'m'}}]}
])
def test_invalid_mapping_graph_rejected(env,doc):
    assert env['clients']['editor'].post(f"/api/workspaces/{env['ws']}/projects",json={'name':'Invalid','document':doc}).status_code==422


def test_api_restart_keeps_projects_and_files(env):
    p=project(env);f=csv_file(env)
    c=TestClient(create_app(env['settings']),base_url=env['settings'].origin);c.cookies.update(env['clients']['editor'].cookies)
    assert c.get(f"/api/workspaces/{env['ws']}/projects/{p['id']}").json()['document']==p['document']
    assert c.get(f"/api/workspaces/{env['ws']}/files/{f['id']}/download").content.startswith(b'region,amount')

@pytest.mark.parametrize('content,name',[
 ('a,a\n1,2','duplicate.csv'),('a,b\n1','ragged.csv'),('a\n\x00','nul.csv'),('','empty.csv'),('a\n1','../escape.csv'),('a\n1','source.txt')
])
def test_invalid_csv_rejected(env,content,name):
    r=env['clients']['editor'].post(f"/api/workspaces/{env['ws']}/files",json={'name':name,'content_base64':base64.b64encode(content.encode()).decode()})
    assert r.status_code==422


def test_source_upload_size_and_base64(env):
    c=env['clients']['editor'];path=f"/api/workspaces/{env['ws']}/files"
    assert c.post(path,json={'name':'bad.csv','content_base64':'not%base64'}).status_code==422
    assert c.post(path,json={'name':'large.csv','content_base64':base64.b64encode(b'a\n'+b'x'*5*1024*1024).decode()}).status_code==413


def test_real_sql_aggregation_and_join(env):
    f=csv_file(env);s=[{'table':'orders','file_id':f['id']}]
    r=submit(env,"SELECT region,ROUND(SUM(CAST(amount AS REAL)),2) AS total FROM orders WHERE status='accepted' GROUP BY region ORDER BY region",s)
    jid=r.json()['job']['id'];assert Worker(env['settings']).once()
    result=env['clients']['editor'].get(f"/api/workspaces/{env['ws']}/jobs/{jid}").json()
    assert result['status']=='succeeded',result
    assert result['result']['rows']==[['North',140.5],['South',20.0]]
    labels=csv_file(env,'region,label\nNorth,N\nSouth,S\n','labels.csv')
    s.append({'table':'labels','file_id':labels['id']})
    j=submit(env,'SELECT labels.label,COUNT(*) AS n FROM orders JOIN labels USING(region) GROUP BY labels.label ORDER BY labels.label',s,key='join-example-0001').json()['job']['id']
    Worker(env['settings']).once()
    r=env['clients']['editor'].get(f"/api/workspaces/{env['ws']}/jobs/{j}").json();assert r['result']['rows']==[['N',3],['S',1]]


def test_idempotency_same_and_different_payload(env):
    r1=submit(env);r2=submit(env);assert r1.status_code==201 and r2.status_code==200
    assert r1.json()['job']['id']==r2.json()['job']['id']
    assert submit(env,'SELECT 2').status_code==409


def test_missing_idempotency_key(env):
    assert env['clients']['editor'].post(f"/api/workspaces/{env['ws']}/jobs",json={'sql':'SELECT 1'}).status_code==422

@pytest.mark.parametrize('sql',[
 "ATTACH DATABASE '/etc/passwd' AS stolen",'CREATE TABLE stolen(x)','PRAGMA database_list',
 'SELECT load_extension(\'file\')','SELECT readfile(\'/etc/passwd\')','SELECT writefile(\'/tmp/x\',\'x\')',
 'SELECT * FROM sqlite_master','SELECT 1; SELECT 2;',"SELECT * FROM pragma_database_list()",
 'VACUUM','BEGIN','SELECT randomblob(2000000)'
])
def test_unsafe_sql_is_rejected_in_real_subprocess(env,sql):
    jid=submit(env,sql).json()['job']['id'];Worker(env['settings']).once()
    r=env['clients']['editor'].get(f"/api/workspaces/{env['ws']}/jobs/{jid}").json()
    assert r['status']=='failed' and r['result'] is None,r


def test_dml_on_input_is_rejected(env):
    f=csv_file(env);s=[{'table':'data','file_id':f['id']}]
    for i,sql in enumerate(['DELETE FROM data','UPDATE data SET amount=0',"INSERT INTO data VALUES ('x','1','accepted')",'DROP TABLE data']):
        jid=submit(env,sql,s,key='readonly-000'+str(i)).json()['job']['id'];Worker(env['settings']).once()
        assert env['clients']['editor'].get(f"/api/workspaces/{env['ws']}/jobs/{jid}").json()['status']=='failed'


def test_recursive_work_is_bounded_and_next_job_succeeds(env):
    jid=submit(env,'WITH RECURSIVE t(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM t) SELECT SUM(x) FROM t').json()['job']['id']
    before=time.monotonic();Worker(replace(env['settings'],job_timeout=1)).once();elapsed=time.monotonic()-before
    r=env['clients']['editor'].get(f"/api/workspaces/{env['ws']}/jobs/{jid}").json();assert r['status']=='failed' and elapsed<6,r
    second=submit(env,'SELECT 8 AS value',key='after-timeout-01').json()['job']['id'];Worker(env['settings']).once()
    assert env['clients']['editor'].get(f"/api/workspaces/{env['ws']}/jobs/{second}").json()['result']['rows']==[[8]]


def test_output_limit_is_error_not_silent_truncation(env):
    sql='WITH RECURSIVE t(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM t WHERE x<10001) SELECT x FROM t'
    jid=submit(env,sql).json()['job']['id'];Worker(env['settings']).once()
    r=env['clients']['editor'].get(f"/api/workspaces/{env['ws']}/jobs/{jid}").json();assert r['status']=='failed' and '10,000' in r['error']


def test_cancel_queued_and_claimed_jobs(env):
    root=f"/api/workspaces/{env['ws']}/jobs";c=env['clients']['editor']
    jid=submit(env).json()['job']['id'];assert c.post(root+'/'+jid+'/cancel').status_code==200
    assert c.get(root+'/'+jid).json()['status']=='cancelled';assert not Worker(env['settings']).once()
    jid=submit(env,key='running-cancel-0001').json()['job']['id'];w=Worker(env['settings']);w.tick();job=w.claim()
    assert c.post(root+'/'+jid+'/cancel').status_code==200;w.execute(job)
    assert c.get(root+'/'+jid).json()['status']=='cancelled'


def test_two_workers_cannot_claim_same_job(env):
    submit(env);w1=Worker(env['settings']);w2=Worker(env['settings']);w1.tick();w2.tick()
    with ThreadPoolExecutor(max_workers=2) as pool:jobs=list(pool.map(lambda w:w.claim(),[w1,w2]))
    assert sum(j is not None for j in jobs)==1


def test_stale_lease_recovery_and_fencing(env):
    jid=submit(env).json()['job']['id'];old=Worker(env['settings']);old.tick();job=old.claim()
    with transaction(env['settings'],True) as db:db.execute('UPDATE jobs SET lease_until=0 WHERE id=?',(jid,))
    new=Worker(env['settings']);new.tick();newjob=new.claim();assert newjob['attempts']==2
    assert not old.finish(job,'failed',error='Stale completion')
    new.execute(newjob)
    assert env['clients']['editor'].get(f"/api/workspaces/{env['ws']}/jobs/{jid}").json()['status']=='succeeded'


def test_lease_recovery_stops_after_three_attempts(env):
    jid=submit(env).json()['job']['id'];w=Worker(env['settings']);w.tick();w.claim()
    with transaction(env['settings'],True) as db:db.execute('UPDATE jobs SET attempts=3,lease_until=0 WHERE id=?',(jid,))
    Worker(env['settings']).tick()
    assert env['clients']['editor'].get(f"/api/workspaces/{env['ws']}/jobs/{jid}").json()['status']=='failed'


def test_queued_job_rechecks_membership(env):
    jid=submit(env).json()['job']['id']
    env['clients']['admin'].put(f"/api/workspaces/{env['ws']}/members",json={'email':'editor@example.test','role':'viewer'})
    Worker(env['settings']).once()
    r=env['clients']['editor'].get(f"/api/workspaces/{env['ws']}/jobs/{jid}").json();assert r['status']=='failed' and 'permission' in r['error'].lower()


def test_source_references_protect_pending_jobs(env):
    f=csv_file(env);jid=submit(env,'SELECT * FROM data',[{'file_id':f['id'],'table':'data'}]).json()['job']['id']
    root=f"/api/workspaces/{env['ws']}";c=env['clients']['admin']
    assert c.delete(root+'/files/'+f['id']).status_code==409
    c.post(root+'/jobs/'+jid+'/cancel')
    assert c.delete(root+'/files/'+f['id']).status_code==200


def test_output_csv_formula_defense_and_retention(env):
    jid=submit(env,"SELECT '=1+1' AS dangerous, -2.5 AS numeric, '@SUM(A1)' AS other").json()['job']['id'];Worker(env['settings']).once()
    url=f"/api/workspaces/{env['ws']}/jobs/{jid}/output"
    r=env['clients']['viewer'].get(url);assert r.status_code==200 and "'=1+1" in r.text and '-2.5' in r.text and "'@SUM" in r.text
    with transaction(env['settings'],True) as db:db.execute('UPDATE jobs SET result=NULL WHERE id=?',(jid,))
    assert env['clients']['viewer'].get(url).status_code==409


def test_schedule_paused_defaults_and_deduplicated_dispatch(env):
    c=env['clients']['editor'];url=f"/api/workspaces/{env['ws']}/schedules"
    sid=c.post(url,json={'name':'Daily','job':{'sql':'SELECT 1'},'interval_seconds':60}).json()['id']
    with transaction(env['settings'],True) as db:db.execute('UPDATE schedules SET next_at=0 WHERE id=?',(sid,))
    w=Worker(env['settings']);w.tick()
    with transaction(env['settings']) as db:assert db.execute('SELECT count(*) FROM jobs').fetchone()[0]==0
    assert c.patch(url+'/'+sid,json={'enabled':True}).status_code==200
    with transaction(env['settings'],True) as db:db.execute('UPDATE schedules SET next_at=1 WHERE id=?',(sid,))
    w.tick();Worker(env['settings']).tick()
    with transaction(env['settings']) as db:
        assert db.execute('SELECT count(*) FROM jobs').fetchone()[0]==1
        assert db.execute('SELECT next_at FROM schedules').fetchone()[0]>time.time()


def test_schedules_cannot_run_with_revoked_permissions(env):
    c=env['clients']['editor'];url=f"/api/workspaces/{env['ws']}/schedules"
    sid=c.post(url,json={'name':'Scheduled','job':{'sql':'SELECT 1'},'interval_seconds':60,'enabled':True}).json()['id']
    env['clients']['admin'].put(f"/api/workspaces/{env['ws']}/members",json={'email':'editor@example.test','role':'viewer'})
    with transaction(env['settings'],True) as db:db.execute('UPDATE schedules SET next_at=0 WHERE id=?',(sid,))
    Worker(env['settings']).tick()
    with transaction(env['settings']) as db:
        assert db.execute('SELECT enabled FROM schedules').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM jobs').fetchone()[0]==0


def test_duplicate_or_reserved_source_alias_rejected(env):
    f=csv_file(env)
    assert submit(env,'SELECT 1',[{'table':'same','file_id':f['id']},{'table':'SAME','file_id':f['id']}]).status_code==422
    assert submit(env,'SELECT 1',[{'table':'sqlite_secret','file_id':f['id']}]).status_code==422


def test_actual_sqlite_engine_and_health_report(env):
    c=env['clients']['admin'];assert c.get('/health/ready').json()['worker_available'] is False
    Worker(env['settings']).tick();assert c.get('/health/ready').json()['worker_available'] is True
    r=c.get('/api/admin/operations').json();assert r['engine']=='SQLite CSV runner' and r['audit']['valid']
