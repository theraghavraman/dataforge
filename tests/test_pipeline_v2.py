import copy
import json
import time
import pytest
from app.db import transaction
from app.worker import Worker
from tests.conftest import csv_file,project


def node(nid,kind,config=None,ports=None):
    return {'id':nid,'type':kind,'name':nid,'position':{'x':0,'y':0},'config':config or {},'ports':{'in':[],'out':ports or []}}


def setup_pipeline(env,nodes=None,links=None,content=None):
    f=csv_file(env,content or 'region,amount,status\nNorth,100,accepted\nSouth,40,accepted\nNorth,30,accepted\nNorth,9,rejected\n')
    if nodes is None:
        nodes=[node('src','csv_source',{'header':True,'delimiter':','},[{'name':'region','type':'VARCHAR'},{'name':'amount','type':'DOUBLE'},{'name':'status','type':'VARCHAR'}]),
               node('filter','filter',{'condition':"status = 'accepted'"}),
               node('agg','aggregator',{'groupBy':['region'],'fields':[{'name':'total','expression':'SUM(amount)'}]}),
               node('sort','sorter',{'keys':[{'column':'total','direction':'DESC'}]}),node('out','csv_target')]
        links=[{'from':a['id'],'to':b['id']} for a,b in zip(nodes,nodes[1:])]
    doc={'projectVersion':'1.0','project':{'id':'local-id','name':'Portable sample'},'mappings':[{'mapping':{'id':'m1','name':'Sample'},'nodes':nodes,'links':links or []}],'workflows':[]}
    p=project(env,doc);root=f"/api/workspaces/{env['ws']}/projects/{p['id']}"
    body={'revision':p['revision'],'mapping_id':'m1','target_id':'out','bindings':{'src':f['id']}}
    return p,root,body,f


def execute(env,root,body,key='pipeline-test-0001'):
    c=env['clients']['editor'];r=c.post(root+'/run',json=body,headers={'Idempotency-Key':key})
    assert r.status_code==201,r.text
    jid=r.json()['job']['id'];Worker(env['settings']).once()
    return c.get(f"/api/workspaces/{env['ws']}/jobs/{jid}").json()


def test_visual_pipeline_reconciles_expected_totals(env):
    p,root,body,f=setup_pipeline(env)
    c=env['clients']['editor'];compiled=c.post(root+'/compile',json=body)
    assert compiled.status_code==200,compiled.text
    assert compiled.json()['output_columns']==['region','total']
    job=execute(env,root,body)
    assert job['status']=='succeeded',job
    assert job['result']['rows']==[['North',130.0],['South',40.0]]
    assert job['payload']['pipeline']['revision']==p['revision']
    assert job['payload']['sources'][0]['sha256']==f['sha256']


def test_pipeline_run_idempotency_and_revision_conflict(env):
    p,root,body,f=setup_pipeline(env);c=env['clients']['editor']
    first=c.post(root+'/run',json=body,headers={'Idempotency-Key':'same-pipeline-key'})
    second=c.post(root+'/run',json=body,headers={'Idempotency-Key':'same-pipeline-key'})
    assert first.status_code==201 and second.status_code==200
    assert first.json()['job']['id']==second.json()['job']['id']
    body['revision']+=1
    assert c.post(root+'/run',json=body,headers={'Idempotency-Key':'new-pipeline-key'}).status_code==409


@pytest.mark.parametrize('kind',['scd2','sequence_generator','lookup','excel_source','parquet_source','sql_transform','missing-node-type'])
def test_unsupported_nodes_fail_closed(env,kind):
    nodes=[node('src','csv_source'),node('middle',kind),node('out','csv_target')]
    p,root,body,f=setup_pipeline(env,nodes,[{'from':'src','to':'middle'},{'from':'middle','to':'out'}])
    r=env['clients']['editor'].post(root+'/compile',json=body)
    assert r.status_code==422 and 'Unsupported server node' in r.text


@pytest.mark.parametrize('expression',["strftime(order_date, '%Y-%m')",'TRY_CAST(amount AS REAL)','SUM(amount); DROP TABLE x','$$RATE * amount','amount /* comment */ + 1'])
def test_ambiguous_dialect_statements_and_variables_rejected(env,expression):
    nodes=[node('src','csv_source'),node('exp','expression',{'fields':[{'name':'value','expression':expression}]}),node('out','csv_target')]
    p,root,body,f=setup_pipeline(env,nodes,[{'from':'src','to':'exp'},{'from':'exp','to':'out'}])
    assert env['clients']['editor'].post(root+'/compile',json=body).status_code==422


def test_typed_csv_rejects_bad_number_instead_of_coercing_to_zero(env):
    p,root,body,f=setup_pipeline(env,content='region,amount,status\nNorth,not-a-number,accepted\n')
    job=execute(env,root,body)
    assert job['status']=='failed' and job['result'] is None


def test_source_schema_mismatch_is_rejected(env):
    p,root,body,f=setup_pipeline(env,content='amount,region,status\n100,North,accepted\n')
    assert env['clients']['editor'].post(root+'/compile',json=body).status_code==422


def test_pipeline_roles_and_workspace_binding(env):
    p,root,body,f=setup_pipeline(env)
    assert env['clients']['viewer'].post(root+'/compile',json=body).status_code==403
    assert env['clients']['outside'].post(root+'/compile',json=body).status_code==404
    other=env['clients']['admin'].post(f"/api/workspaces/{env['other']}/files",json={'name':'other.csv','content_base64':'eCx5CjEsMgo='}).json()
    body['bindings']['src']=other['id']
    assert env['clients']['editor'].post(root+'/compile',json=body).status_code==404


def test_required_approval_and_revocation_before_execution(env):
    p,root,body,f=setup_pipeline(env);a=env['clients']['admin'];e=env['clients']['editor']
    assert a.put(root+'/approval',json={'approval_required':True}).status_code==200
    assert e.post(root+'/compile',json=body).status_code==200
    assert e.post(root+'/run',json=body,headers={'Idempotency-Key':'approval-test-0001'}).status_code==409
    assert a.post(root+'/approve/1').status_code==200
    r=e.post(root+'/run',json=body,headers={'Idempotency-Key':'approval-test-0001'});assert r.status_code==201
    a.delete(root+'/approve/1');Worker(env['settings']).once()
    job=e.get(f"/api/workspaces/{env['ws']}/jobs/{r.json()['job']['id']}").json()
    assert job['status']=='failed'


def test_saver_cannot_approve_own_revision(env):
    p,root,body,f=setup_pipeline(env)
    a=env['clients']['admin'];a.put(f"/api/workspaces/{env['ws']}/members",json={'email':'editor@example.test','role':'owner'})
    assert env['clients']['editor'].post(root+'/approve/1').status_code==409
    assert a.post(root+'/approve/1').status_code==200


def test_pipeline_schedule_paused_idempotent_and_uses_snapshot(env):
    p,root,body,f=setup_pipeline(env);body['interval_seconds']=60;c=env['clients']['editor']
    first=c.post(root+'/run',json=body,headers={'Idempotency-Key':'schedule-pipeline-0001'});assert first.status_code==201
    assert first.json()['enabled'] is False
    again=c.post(root+'/run',json=body,headers={'Idempotency-Key':'schedule-pipeline-0001'});assert again.json()['schedule_id']==first.json()['schedule_id']
    with transaction(env['settings'],True) as db:db.execute('UPDATE schedules SET enabled=1,next_at=0')
    Worker(env['settings']).once()
    with transaction(env['settings']) as db:
        job=db.execute('SELECT * FROM jobs ORDER BY created DESC LIMIT 1').fetchone()
        assert job['status']=='succeeded'
        assert json.loads(job['payload'])['pipeline']['revision']==1


def test_join_and_column_collision(env):
    left=node('src','csv_source');right=node('other','csv_source');join=node('join','joiner',{'conditions':[{'master':'region','detail':'region'}],'joinType':'LEFT'});out=node('out','csv_target')
    p,root,body,f=setup_pipeline(env,[left,right,join,out],[{'from':'src','to':'join','toPort':'master'},{'from':'other','to':'join','toPort':'detail'},{'from':'join','to':'out'}])
    lookup=csv_file(env,'region,manager\nNorth,Alice\nSouth,Bob\n',name='lookup.csv');body['bindings']['other']=lookup['id']
    job=execute(env,root,body);assert job['status']=='succeeded',job
    assert job['result']['columns']==['region','amount','status','d_region','manager']
    assert len(job['result']['rows'])==4


def test_union_aligns_missing_columns_by_name(env):
    nodes=[node('src','csv_source'),node('other','csv_source'),node('union','union',{'distinct':False}),node('out','csv_target')]
    p,root,body,f=setup_pipeline(env,nodes,[{'from':'src','to':'union'},{'from':'other','to':'union'},{'from':'union','to':'out'}],content='a,b\n1,2\n')
    other=csv_file(env,'b,c\n3,4\n',name='other.csv');body['bindings']['other']=other['id']
    job=execute(env,root,body);assert job['status']=='succeeded',job
    assert job['result']['columns']==['a','b','c'] and job['result']['rows']==[['1','2',None],[None,'3','4']]


def test_router_default_emits_rejection_reason(env):
    nodes=[node('src','csv_source'),node('router','router',{'groups':[{'key':'accepted','condition':"status = 'accepted'"}],'multiple':True}),node('out','reject_target')]
    p,root,body,f=setup_pipeline(env,nodes,[{'from':'src','to':'router'},{'from':'router','to':'out','fromPort':'default'}])
    job=execute(env,root,body);assert job['status']=='succeeded',job
    assert job['result']['row_count']==1 and job['result']['rows'][0][-2]=='No router group matched'


def test_expression_exclusion_and_replacement(env):
    nodes=[node('src','csv_source'),node('exp','expression',{'exclude':['status'],'fields':[{'name':'amount','expression':'CAST(amount AS REAL)*2'}]}),node('out','csv_target')]
    p,root,body,f=setup_pipeline(env,nodes,[{'from':'src','to':'exp'},{'from':'exp','to':'out'}])
    job=execute(env,root,body);assert job['status']=='succeeded',job
    assert job['result']['columns']==['region','amount'] and job['result']['rows'][0]==['North',200.0]


def test_rank_and_preview_limit(env):
    nodes=[node('src','csv_source'),node('rank','rank',{'column':'amount','n':2,'function':'DENSE_RANK','direction':'DESC','outputName':'rank_index'}),node('out','preview_target',{'limit':1})]
    p,root,body,f=setup_pipeline(env,nodes,[{'from':'src','to':'rank'},{'from':'rank','to':'out'}])
    job=execute(env,root,body);assert job['status']=='succeeded',job
    assert job['result']['row_count']==1 and job['result']['rows'][0][-1]==1


def test_invalid_graph_cycle_and_unknown_extra_binding(env):
    nodes=[node('src','csv_source'),node('one','filter',{'condition':'1'}),node('two','filter',{'condition':'1'}),node('out','csv_target')]
    links=[{'from':'one','to':'two'},{'from':'two','to':'one'},{'from':'two','to':'out'}]
    p,root,body,f=setup_pipeline(env,nodes,links)
    assert env['clients']['editor'].post(root+'/compile',json=body).status_code==422


def test_source_integrity_checked_before_compile(env):
    p,root,body,f=setup_pipeline(env)
    with transaction(env['settings'],True) as db:db.execute('UPDATE files SET content=? WHERE id=?',(b'region,amount,status\nX,1,accepted\n',f['id']))
    assert env['clients']['editor'].post(root+'/compile',json=body).status_code==422


def test_capabilities_report_honest_engine_boundary(env):
    data=env['clients']['editor'].get('/api/capabilities').json()
    assert data['engine']=='sqlite' and data['duckdb_compatible'] is False
    assert 'csv_source' in data['supported_nodes'] and 'scd2' not in data['supported_nodes']
