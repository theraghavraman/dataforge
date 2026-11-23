"""Fail-closed compiler for the explicitly supported portable DataForge subset.

This compiles the stored mapping graph, not SQL submitted by the browser. It uses
SQLite semantics, never claims DuckDB equivalence, and rejects unsupported nodes,
variables, source types and ambiguous output names before anything is queued.
"""
import csv
import hashlib
import io
import json
import re
import time
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from .db import transaction, canonical, uid, audit
from .security import session_user, require_member
from .models import PipelineRun, ApprovalPolicy, RestoreRevision
from .validation import validate_job

SUPPORTED = ('csv_source','filter','expression','sorter','aggregator','joiner','union','rank','router','csv_target','json_target','preview_target','reject_target')
TARGETS={'csv_target','json_target','preview_target','reject_target'}
TYPE_MAP={'VARCHAR':'TEXT','TEXT':'TEXT','STRING':'TEXT','UNKNOWN':'TEXT',
          'INTEGER':'INTEGER','INT':'INTEGER','BIGINT':'INTEGER','SMALLINT':'INTEGER','TINYINT':'INTEGER',
          'DOUBLE':'REAL','FLOAT':'REAL','REAL':'REAL','BOOLEAN':'BOOLEAN','BOOL':'BOOLEAN','DATE':'DATE','TIMESTAMP':'TIMESTAMP'}


def fail(message): raise HTTPException(422,message)
def q(value):
    if not isinstance(value,str) or not value or len(value)>128 or '\x00' in value: fail('Invalid or missing column name.')
    return '"'+value.replace('"','""')+'"'
def literal(value): return "'"+str(value).replace("'","''")+"'"
def unique(names):
    if not names or len(names)>128 or len({n.casefold() for n in names})!=len(names): fail('Empty, excessive, or duplicate output columns.')
    for name in names: q(name)
    return names

def expr(value):
    if not isinstance(value,str) or not value.strip() or len(value)>8192: fail('Expression must be nonempty and at most 8192 characters.')
    # Conservative portability checks; the SQLite authorizer is the execution boundary.
    if '\x00' in value or ';' in value or '--' in value or '/*' in value or '$$' in value:
        fail('Portable expressions cannot contain statements, comments, NUL, or unresolved variables.')
    if re.search(r'\b(try_cast|strftime|date_trunc|date_diff|read_csv|read_parquet|read_json|unnest|regexp_matches|list|array_agg|median|quantile)\s*\(|::|\bILIKE\b',value,re.I):
        fail('DuckDB-specific or dialect-ambiguous expression. Rewrite explicitly for SQLite or use the original DuckDB designer.')
    return value.strip()


def compile_mapping(db,workspace,project,payload):
    try: return _compile_mapping(db,workspace,project,payload)
    except (KeyError,TypeError,ValueError,IndexError,RecursionError) as exc:
        fail('Invalid or unsupported mapping configuration ('+type(exc).__name__+').')


def _compile_mapping(db,workspace,project,payload):
    if project['revision']!=payload.revision: raise HTTPException(409,'Project changed. Reload before compiling or running.')
    document=json.loads(project['document'])
    mapping=next((m for m in document.get('mappings',[]) if m['mapping']['id']==payload.mapping_id),None)
    if not mapping: raise HTTPException(404,'Mapping not found.')
    nodes={n['id']:n for n in mapping.get('nodes',[])}
    if len(nodes)>200: fail('Server mappings are limited to 200 nodes.')
    if payload.target_id not in nodes or nodes[payload.target_id].get('type') not in TARGETS: fail('Choose a supported target node.')
    links=[]
    for item in mapping.get('links',[]):
        a=item['from'];b=item['to']
        links.append({**item,'from':a.get('node') if isinstance(a,dict) else a,'to':b.get('node') if isinstance(b,dict) else b})
    memo={};visiting=set();ctes=[];sources=[];lineage=[];warnings=[];bindings_used=set();orders={}
    def compile_node(nid,group='out'):
        key=(nid,group)
        if key in memo:return memo[key]
        if nid in visiting:fail('Mapping contains a cycle.')
        if nid not in nodes:fail('Mapping references an unknown node.')
        visiting.add(nid);node=nodes[nid];kind=node.get('type');c=node.get('config',{})
        if kind not in SUPPORTED:fail(f'Unsupported server node: {kind}. It has not been silently skipped or converted.')
        if not isinstance(c,dict):fail('Node configuration must be an object.')
        incoming=[x for x in links if x['to']==nid]
        def inputs(slot=None):
            rows=[]
            default='master' if kind=='joiner' else 'in'
            for link in incoming:
                target_slot=link.get('toPort',default)
                if slot is None or target_slot==slot:
                    rows.append(compile_node(link['from'],link.get('fromPort','out')))
            return rows
        def one(slot=None):
            rows=inputs(slot)
            if len(rows)!=1:fail(f'{node.get("name",nid)} requires exactly one input'+(' on '+slot if slot else '')+'.')
            return rows[0]
        alias='dfn_'+str(len(ctes))
        sql='';names=[];order=[]
        if kind=='csv_source':
            if incoming:fail('Source nodes cannot have input links.')
            if group!='out':fail('Invalid CSV source output group.')
            if c.get('header',True) is not True or c.get('delimiter',',') not in (',','auto',''):
                fail('Server inputs require comma-separated CSV with a header row.')
            fid=payload.bindings.get(nid)
            if not fid:fail('Bind the source '+node.get('name',nid)+' to an uploaded workspace CSV.')
            file=db.execute('SELECT * FROM files WHERE id=? AND workspace_id=?',(fid,workspace)).fetchone()
            if not file:raise HTTPException(404,'Bound source not found in this workspace.')
            if hashlib.sha256(file['content']).hexdigest()!=file['sha256']:fail('Stored source checksum does not match its content.')
            names=next(csv.reader(io.StringIO(bytes(file['content']).decode('utf-8-sig'))))
            ports=node.get('ports',{}).get('out',[])
            if ports and [p.get('name') for p in ports]!=names:fail('Saved source schema differs from the uploaded CSV header. Refresh the mapping schema first.')
            schema={}
            for port in ports:
                declared=str(port.get('type','TEXT')).upper()
                if declared not in TYPE_MAP:fail('Unsupported portable source type: '+declared+'. DECIMAL requires an explicitly reviewed precision strategy.')
                schema[port['name']]=TYPE_MAP[declared]
            if not ports:warnings.append('Source '+node.get('name',nid)+' has no saved schema; columns remain TEXT. There is no implicit type inference.')
            table='src_'+str(len(sources));sources.append({'file_id':fid,'table':table,'types':schema,'sha256':file['sha256']});bindings_used.add(nid)
            sql='SELECT * FROM '+q(table)
        elif kind=='joiner':
            if len(incoming)!=2:fail('Joiner requires master and detail only.')
            m,mcols=one('master');d,dcols=one('detail')
            clauses=[]
            for pair in c.get('conditions',[]):
                if pair.get('master') not in mcols or pair.get('detail') not in dcols:fail('Join condition refers to an unknown column.')
                clauses.append('m.'+q(pair['master'])+' = d.'+q(pair['detail']))
            if c.get('advancedCondition'):clauses.append('('+expr(c['advancedCondition'])+')')
            if not clauses:fail('Joiner needs at least one condition.')
            on=' AND '.join(clauses)
            if group=='reject':
                if not c.get('reject'):fail('Join reject output is not enabled.')
                names=mcols+['reject_reason','reject_node']
                sql='SELECT m.*, '+literal('No matching detail row')+' AS reject_reason, '+literal(node.get('name',nid))+' AS reject_node FROM '+q(m)+' m WHERE NOT EXISTS (SELECT 1 FROM '+q(d)+' d WHERE '+on+')'
            else:
                if group!='out':fail('Invalid joiner output group.')
                join_type=c.get('joinType','INNER')
                if join_type not in ('INNER','LEFT','RIGHT','FULL'):fail('Unsupported join type.')
                names=list(mcols);fields=['m.'+q(x) for x in mcols];used={x.casefold() for x in names}
                for name in dcols:
                    out=(c.get('detailAliases',{}).get(name) or name).strip()
                    if out.casefold() in used:out='d_'+out
                    base=out;i=2
                    while out.casefold() in used:out=base+'_'+str(i);i+=1
                    used.add(out.casefold());names.append(out);fields.append('d.'+q(name)+' AS '+q(out))
                sql='SELECT '+', '.join(fields)+' FROM '+q(m)+' m '+join_type+' JOIN '+q(d)+' d ON '+on
        elif kind=='union':
            if group!='out':fail('Invalid union output group.')
            refs=inputs()
            if len(refs)<2:fail('Union requires at least two inputs.')
            names=[];seen=set()
            for ref,cols in refs:
                for col in cols:
                    if col.casefold() not in seen:names.append(col);seen.add(col.casefold())
            parts=[]
            for ref,cols in refs:
                actual={x.casefold():x for x in cols}
                parts.append('SELECT '+', '.join((q(actual[x.casefold()]) if x.casefold() in actual else 'NULL')+' AS '+q(x) for x in names)+' FROM '+q(ref))
            sql=(' UNION ' if c.get('distinct') else ' UNION ALL ').join(parts)
        else:
            ref,cols=one();names=list(cols)
            if kind!='router' and group!='out':fail('Invalid output group for '+kind+'.')
            if kind=='filter':sql='SELECT * FROM '+q(ref)+' WHERE '+expr(c.get('condition'))
            elif kind=='expression':
                fields=c.get('fields',[]);excluded=c.get('exclude',[])
                if any(f.get('setVariable') for f in fields):fail('Stateful variable assignments are not supported by the read-only server runner.')
                if any(x not in cols for x in excluded):fail('Excluded expression column is not in the input.')
                derived=unique([f.get('name','').strip() for f in fields]) if fields else []
                kept=[x for x in cols if x not in excluded and x.casefold() not in {y.casefold() for y in derived}]
                names=kept+derived
                sql='SELECT '+', '.join([q(x) for x in kept]+[expr(f.get('expression'))+' AS '+q(f['name'].strip()) for f in fields])+' FROM '+q(ref)
            elif kind=='sorter':
                for k in c.get('keys',[]):
                    if k.get('column') not in cols or k.get('direction','ASC') not in ('ASC','DESC'):fail('Invalid sorter key or direction.')
                    order.append(q(k['column'])+' '+k.get('direction','ASC')+' NULLS LAST')
                sql='SELECT '+('DISTINCT ' if c.get('distinct') else '')+'* FROM '+q(ref)+(' ORDER BY '+', '.join(order) if order else '')
            elif kind=='aggregator':
                group_by=c.get('groupBy',[]);fields=c.get('fields',[])
                if any(x not in cols for x in group_by):fail('Group-by column is not in the input.')
                names=group_by+[f.get('name','').strip() for f in fields]
                sql='SELECT '+', '.join([q(x) for x in group_by]+[expr(f.get('expression'))+' AS '+q(f['name'].strip()) for f in fields])+' FROM '+q(ref)
                if group_by:sql+=' GROUP BY '+', '.join(q(x) for x in group_by)
                warnings.append('Aggregate expressions use SQLite semantics. Review numeric precision and GROUP BY expressions before approving business outputs.')
            elif kind=='rank':
                column=c.get('column');groups=c.get('groupBy',[]);fn=c.get('function','ROW_NUMBER');rank=c.get('outputName','rank_index');n=c.get('n',3)
                if column not in cols or any(x not in cols for x in groups) or fn not in ('ROW_NUMBER','RANK','DENSE_RANK') or type(n) is not int or not 1<=n<=10000:fail('Invalid rank configuration.')
                direction=c.get('direction','DESC')
                if direction not in ('ASC','DESC') or c.get('position','top') not in ('top','bottom'):fail('Invalid rank direction or position.')
                if c.get('position')=='bottom':direction='ASC' if direction=='DESC' else 'DESC'
                over=('PARTITION BY '+', '.join(q(x) for x in groups)+' ' if groups else '')+'ORDER BY '+q(column)+' '+direction+' NULLS LAST'
                names=cols+[rank];order=[q(x) for x in groups]+[q(rank)+' ASC']
                sql='SELECT * FROM (SELECT *, '+fn+'() OVER ('+over+') AS '+q(rank)+' FROM '+q(ref)+') WHERE '+q(rank)+' <= '+str(n)
                warnings.append('Tied ROW_NUMBER values have no stable tie-breaker unless the mapping defines one.')
            elif kind=='router':
                groups=c.get('groups',[])
                keys=[g.get('key') for g in groups]
                if len(set(keys))!=len(keys) or 'default' in keys:fail('Router group keys must be unique and cannot be default.')
                predicates=['COALESCE(('+expr(g.get('condition'))+'),0)' for g in groups]
                if group=='default':
                    pred='NOT ('+' OR '.join(predicates)+')' if predicates else '1'
                    names=cols+['reject_reason','reject_node']
                    extra=', '+literal('No router group matched')+' AS reject_reason, '+literal(node.get('name',nid))+' AS reject_node'
                else:
                    if group not in keys:fail('Unknown router output group.')
                    i=keys.index(group);pred=predicates[i];extra=''
                    if not c.get('multiple',True) and i:pred+=' AND NOT ('+' OR '.join(predicates[:i])+')'
                sql='SELECT *'+extra+' FROM '+q(ref)+' WHERE '+pred
            elif kind in TARGETS:
                if c.get('mode') not in (None,'','overwrite'):fail('Server targets produce a result artifact; append/merge modes are not supported.')
                sql='SELECT * FROM '+q(ref);order=orders.get(ref,[])
                if kind=='preview_target':
                    limit=c.get('limit',1000)
                    if type(limit) is not int or not 1<=limit<=10000:fail('Preview limit must be between 1 and 10,000.')
                    # Final ordering is applied outside this CTE; apply it before LIMIT too.
                    if order:sql+=' ORDER BY '+', '.join(order)
                    sql+=' LIMIT '+str(limit)
                warnings.append('Target '+node.get('name',nid)+' produces a retained server result; it does not write to a configured filesystem path.')
            else:fail('Unsupported portable node.')
        unique(names)
        alias='dfn_'+str(len(ctes));ctes.append(q(alias)+' AS ('+sql+')');orders[alias]=order
        memo[key]=(alias,names);visiting.remove(nid);lineage.append({'node_id':nid,'type':kind,'output_group':group})
        return memo[key]
    final,columns=compile_node(payload.target_id)
    if set(payload.bindings)!=bindings_used:fail('Bindings must match exactly the source nodes feeding the selected target.')
    if len(sources)>8:fail('Server jobs support at most eight bound sources.')
    sql='WITH '+',\n'.join(ctes)+'\nSELECT * FROM '+q(final)
    if orders.get(final):sql+=' ORDER BY '+', '.join(orders[final])
    if len(sql)>65536:fail('Compiled SQL exceeds the 64 KiB execution limit.')
    meta={'project_id':project['id'],'revision':project['revision'],'mapping_id':payload.mapping_id,'target_id':payload.target_id,
          'document_sha256':hashlib.sha256(project['document'].encode()).hexdigest(),'compiler':'portable-sqlite-v1','lineage':lineage}
    result={'name':project['name']+' / '+nodes[payload.target_id].get('name',payload.target_id),'sql':sql,'sources':sources,'pipeline':meta}
    validate_job(db,workspace,result)
    return {'job':result,'engine':'sqlite','output_columns':columns,'warnings':list(dict.fromkeys(warnings)),
            'sql_sha256':hashlib.sha256(sql.encode()).hexdigest()}


def check_approval(db,workspace,metadata):
    project=db.execute('SELECT id FROM projects WHERE id=? AND workspace_id=?',(metadata['project_id'],workspace)).fetchone()
    if not project:raise HTTPException(404,'Pipeline project no longer exists in this workspace.')
    policy=db.execute('SELECT approval_required FROM project_policies WHERE project_id=?',(project[0],)).fetchone()
    if policy and policy[0] and not db.execute('SELECT 1 FROM project_approvals WHERE project_id=? AND revision=?',(project[0],metadata['revision'])).fetchone():
        raise HTTPException(409,'This exact project revision needs approval by another workspace owner before execution.')


def install_pipeline_routes(app,settings):
    @app.get('/api/capabilities')
    def capabilities(request: Request):
        with transaction(settings,True) as db:session_user(db,request,settings)
        return {'engine':'sqlite','pipeline_compiler':'portable-sqlite-v1','supported_nodes':SUPPORTED,
                'source_types':list(TYPE_MAP),'duckdb_compatible':False,'targets_are_result_artifacts':True}

    def get_project(db,ws,pid):
        row=db.execute('SELECT * FROM projects WHERE id=? AND workspace_id=?',(pid,ws)).fetchone()
        if not row:raise HTTPException(404,'Project not found.')
        return row

    @app.post('/api/workspaces/{ws}/projects/{pid}/compile')
    def compile_project(ws: str,pid: str,payload: PipelineRun,request: Request):
        with transaction(settings,True) as db:
            u=session_user(db,request,settings);require_member(db,u,ws,'editor')
            return compile_mapping(db,ws,get_project(db,ws,pid),payload)

    @app.post('/api/workspaces/{ws}/projects/{pid}/run')
    def run_project(ws: str,pid: str,payload: PipelineRun,request: Request):
        from .api import enqueue,public_job
        key=request.headers.get('idempotency-key','')
        if not re.fullmatch(r'[A-Za-z0-9_.:-]{8,120}',key):fail('Supply a valid Idempotency-Key.')
        with transaction(settings,True) as db:
            u=session_user(db,request,settings);require_member(db,u,ws,'editor')
            compiled=compile_mapping(db,ws,get_project(db,ws,pid),payload)
            check_approval(db,ws,compiled['job']['pipeline'])
            if payload.interval_seconds is not None:
                # Global primary key needs a workspace-specific hash to avoid collisions.
                sid=hashlib.sha256((ws+':'+key).encode()).hexdigest()
                existing=db.execute('SELECT * FROM schedules WHERE id=?',(sid,)).fetchone()
                if existing:
                    if existing['payload']!=canonical(compiled['job']) or existing['interval_seconds']!=payload.interval_seconds:raise HTTPException(409,'Schedule idempotency key was reused with different input.')
                    return {'schedule_id':sid,'enabled':bool(existing['enabled']),'created':False}
                if db.execute('SELECT count(*) FROM schedules WHERE workspace_id=?',(ws,)).fetchone()[0]>=100:raise HTTPException(409,'Schedule limit reached.')
                now=time.time();db.execute('INSERT INTO schedules VALUES(?,?,?,?,?,?,?,?,?)',(sid,ws,u['user_id'],compiled['job']['name'],canonical(compiled['job']),payload.interval_seconds,now+payload.interval_seconds,0,now))
                audit(db,u['user_id'],ws,'pipeline.schedule_created',sid,compiled['job']['pipeline'])
                return JSONResponse({'schedule_id':sid,'enabled':False,'created':True,'warnings':compiled['warnings']},201)
            row,created=enqueue(db,ws,u['user_id'],compiled['job'],key)
            return JSONResponse({'job':public_job(row),'created':created,'warnings':compiled['warnings']},201 if created else 200)

    @app.get('/api/workspaces/{ws}/projects/{pid}/approval')
    def approval(ws: str,pid: str,request: Request):
        with transaction(settings,True) as db:
            u=session_user(db,request,settings);require_member(db,u,ws);p=get_project(db,ws,pid)
            policy=db.execute('SELECT approval_required FROM project_policies WHERE project_id=?',(pid,)).fetchone()
            approved=db.execute('SELECT actor,approved FROM project_approvals WHERE project_id=? AND revision=?',(pid,p['revision'])).fetchone()
            return {'required':bool(policy and policy[0]),'revision':p['revision'],'approval':dict(approved) if approved else None}

    @app.put('/api/workspaces/{ws}/projects/{pid}/approval')
    def policy(ws: str,pid: str,payload: ApprovalPolicy,request: Request):
        with transaction(settings,True) as db:
            u=session_user(db,request,settings);require_member(db,u,ws,'owner');get_project(db,ws,pid)
            db.execute('INSERT INTO project_policies VALUES(?,?) ON CONFLICT(project_id) DO UPDATE SET approval_required=excluded.approval_required',(pid,int(payload.approval_required)))
            audit(db,u['user_id'],ws,'project.approval_policy',pid,{'required':payload.approval_required})
            return {'ok':True}

    @app.post('/api/workspaces/{ws}/projects/{pid}/approve/{revision}')
    def approve(ws: str,pid: str,revision: int,request: Request):
        with transaction(settings,True) as db:
            u=session_user(db,request,settings);require_member(db,u,ws,'owner');p=get_project(db,ws,pid)
            if p['revision']!=revision:raise HTTPException(409,'Only the current revision can be approved.')
            if p['updated_by']==u['user_id']:raise HTTPException(409,'A different workspace owner must approve this revision.')
            db.execute('INSERT OR REPLACE INTO project_approvals VALUES(?,?,?,?)',(pid,revision,u['user_id'],time.time()))
            audit(db,u['user_id'],ws,'project.approved',pid,{'revision':revision});return {'ok':True}

    @app.delete('/api/workspaces/{ws}/projects/{pid}/approve/{revision}')
    def revoke(ws: str,pid: str,revision: int,request: Request):
        with transaction(settings,True) as db:
            u=session_user(db,request,settings);require_member(db,u,ws,'owner');get_project(db,ws,pid)
            db.execute('DELETE FROM project_approvals WHERE project_id=? AND revision=?',(pid,revision))
            audit(db,u['user_id'],ws,'project.approval_revoked',pid,{'revision':revision});return {'ok':True}
