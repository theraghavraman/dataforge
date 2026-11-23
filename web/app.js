'use strict';
(() => {
 const $ = id => document.getElementById(id);
 const state = {user:null,csrf:null,workspaces:[],ws:null,tab:'overview',loading:false};
 const icons = {security:'\u26bf',delivery:'\u2709',overview:'\u25a6',projects:'\u25c8',files:'\u25a4',jobs:'\u25b7',schedules:'\u25f7',members:'\u2637',audit:'\u2261',operations:'\u25ce',accounts:'\u2699'};
 function el(tag, attrs={}, ...children) {
  const n=document.createElement(tag);
  for (const [k,v] of Object.entries(attrs)) {if(k==='class')n.className=v;else if(k==='text')n.textContent=v;else if(k.startsWith('on'))n.addEventListener(k.slice(2),v);else if(v!==null&&v!==false)n.setAttribute(k,v===true?'':v);}
  children.flat().forEach(c=>{if(c!==null&&c!==undefined)n.append(c instanceof Node?c:document.createTextNode(String(c)));});return n;
 }
 function button(text, action, primary=false) {return el('button',{type:'button',class:primary?'primary':'',text,onclick:()=>run(action)});}
 function notify(text, error=false){if(error&&!$('login').hidden)$('loginError').textContent=text;$('notice').textContent=text;$('notice').className=error?'error':'';$('notice').hidden=false;}
 async function run(fn){try{return await fn();}catch(e){notify(e.message,true);const err=$('modalError');if(err)err.textContent=e.message;}}
 async function api(path, options={}) {
  const headers={...options.headers};if(options.body!==undefined)headers['Content-Type']='application/json';
  if(state.csrf)headers['X-CSRF-Token']=state.csrf;
  const r=await fetch(path,{...options,headers,credentials:'same-origin',body:options.body===undefined?undefined:JSON.stringify(options.body)});
  const data=await r.json().catch(()=>({detail:'Unexpected server response.'}));
  if(!r.ok){if(r.status===401&&path!='/api/auth/login'&&path!='/api/auth/oidc/factor'){showLogin();}const detail=data.detail;throw new Error(typeof detail==='string'?detail:detail?.message||`Request failed (${r.status}). Check the supplied values.`);}
  return data;
 }
 const path = suffix=>`/api/workspaces/${encodeURIComponent(state.ws.id)}${suffix}`;
 const edit = ()=>['owner','editor'].includes(state.ws?.role);
 const fmtDate = value=>value?new Date(value*1000).toLocaleString(undefined,{dateStyle:'medium',timeStyle:'short'}):'\u2014';
 const badge = value=>el('span',{class:'badge '+value,text:value});
 function title(name,desc,actions=[]){return el('div',{class:'page-head'},el('div',{},el('p',{class:'eyebrow',text:'DATAFORGE / '+name.toUpperCase()}),el('h1',{text:name}),el('p',{text:desc})),el('div',{class:'actions'},actions));}
 function card(name,body,actions=[]){return el('section',{class:'card'},el('div',{class:'card-head'},el('h2',{text:name}),el('div',{class:'actions'},actions)),body);}
 function empty(heading,text){return el('div',{class:'empty'},el('strong',{text:heading}),text);}
 function table(headers,rows){return el('div',{class:'table-wrap'},el('table',{},el('thead',{},el('tr',{},headers.map(x=>el('th',{scope:'col',text:x})))),el('tbody',{},rows.map(row=>el('tr',{},row.map(x=>el('td',{},x)))))));}
 function field(text,type='text',value='',attrs={}){const input=el(type==='textarea'?'textarea':'input',{...(type==='textarea'?{}:{type}),...attrs});input.value=value;return {input,node:el('label',{},text,input)};}
 function modal(name,body){$('modalTitle').textContent=name;$('modalBody').replaceChildren(body,el('p',{id:'modalError',class:'danger',role:'alert'}));$('modal').showModal();}
 function closeModal(){$('modal').close();$('modalBody').replaceChildren();}
 function download(data,name){const u=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=el('a',{href:u,download:name});document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(u),1000);}
 function showLogin(){state.renderToken=Symbol();state.user=null;state.csrf=null;state.ws=null;state.workspaces=[];closeModal();$('view').replaceChildren();$('shell').hidden=true;$('login').hidden=false;$('password').value='';$('otp').value='';}
 function navigation(){
  const items=[['security','Account security'],['overview','Overview'],['projects','Projects'],['files','Data sources'],['jobs','Jobs & results'],['schedules','Schedules']];
  if(state.ws?.role==='owner')items.push(['members','Team access'],['audit','Activity log']);
  if(state.user?.is_admin)items.push(['operations','Operations'],['delivery','Delivery & readiness'],['accounts','Accounts']);
  if(!items.some(([id])=>id===state.tab))state.tab='overview';
  $('nav').replaceChildren(...items.map(([id,label])=>el('button',{'aria-current':state.tab===id?'page':null,onclick:()=>run(()=>navigate(id))},el('span',{class:'nav-icon','aria-hidden':'true',text:icons[id]}),label)));
 }
 async function enter(){
  const factor=await api('/api/auth/mfa');state.needsMFA=factor.required&&!factor.enabled;if(state.needsMFA)state.tab='security';
  state.workspaces=state.needsMFA?[]:await api('/api/workspaces');state.ws=state.workspaces.find(w=>w.id===state.ws?.id)||state.workspaces[0]||null;
  $('workspace').replaceChildren(...state.workspaces.map(w=>el('option',{value:w.id,text:w.name})));if(state.ws)$('workspace').value=state.ws.id;
  $('accountName').textContent=state.user.name;$('roleBadge').textContent=state.ws?.role||'No workspace';$('roleBadge').className='badge '+(state.ws?.role||'');
  $('login').hidden=true;$('shell').hidden=false;await navigate(state.tab);
 }
 async function navigate(tab){
  state.tab=tab;navigation();$('view').replaceChildren(el('p',{class:'muted',text:'Loading workspace\u2026'}));
  if(!state.ws&&!['accounts','operations','security','delivery'].includes(tab)){
   $('view').replaceChildren(title('Your workspace','An administrator must provision a workspace before you can start.',state.user.is_admin?[button('Create workspace',createWorkspace,true)]:[]));return;
  }
  const token=Symbol();state.renderToken=token;
  const content=await views[tab]();if(state.renderToken===token)$('view').replaceChildren(content);
 }
 async function createWorkspace(){const name=field('Workspace name','text','',{required:true,maxlength:120});const form=el('form',{},name.node,el('button',{class:'primary',type:'submit',text:'Create workspace'}));form.onsubmit=e=>{e.preventDefault();run(async()=>{const w=await api('/api/workspaces',{method:'POST',body:{name:name.input.value.trim()}});state.ws=w;closeModal();await enter();});};modal('Create workspace',form);}
 async function overview(){
  const [projects,jobs,files,schedules]=await Promise.all([api(path('/projects')),api(path('/jobs')),api(path('/files')),api(path('/schedules'))]);
  const active=jobs.filter(j=>['queued','running'].includes(j.status)).length;
  const metric=(label,value,note)=>el('div',{class:'metric'},el('div',{class:'label',text:label}),el('div',{class:'value',text:value}),el('small',{text:note}));
  const metrics=el('div',{class:'metrics'},metric('Projects',projects.length,'Central, revision-checked logic'),metric('Source files',files.length,'Workspace-scoped CSV data'),metric('Active jobs',active,'In the latest 100 jobs'),metric('Schedules',schedules.filter(s=>s.enabled).length,'Enabled server schedules'));
  const recent=jobs.length?jobTable(jobs.slice(0,6)):empty('A clean slate','Submit a SQL job to start building your execution history.');
  const step=(n,name,description)=>el('div',{class:'flow-step'},el('span',{class:'step-num',text:n}),el('div',{},el('h3',{text:name}),el('p',{text:description})));
  return el('div',{},title('Workspace overview','A shared view of your projects, source data, and execution history.',state.user.is_admin?[button('New workspace',createWorkspace)]:[]),metrics,el('div',{class:'two-col'},card('Recent execution',recent,[button('All jobs',()=>navigate('jobs'))]),card('Your workflow',el('div',{},step('01','Upload controlled inputs','Add CSV files. Each upload is validated and checksummed.'),step('02','Design or query','Publish and run supported visual mappings, or write a CSV/SQLite query.'),step('03','Run with a record','Submit jobs, inspect results, and review the activity log.')))),el('div',{class:'notice warning'},'Portable visual pipelines now run on the server: bind uploaded CSV sources, compile, review, and queue or schedule supported nodes. Execution uses SQLite semantics. Unsupported DuckDB features are rejected, not silently converted.'));
 }
 async function projectHistory(p){const rows=await api(path(`/projects/${p.id}/revisions`));modal('Project revision history',el('div',{},el('p',{class:'muted',text:'The latest 20 logic revisions are retained. Restoring creates a new revision and never overwrites history silently.'}),table(['Revision','Saved','Action'],rows.map(r=>[String(r.revision),fmtDate(r.created),edit()?button('Restore',async()=>{if(!confirm(`Restore revision ${r.revision} as a new current revision?`))return;await api(path(`/projects/${p.id}/restore`),{method:'POST',body:{revision:p.revision,restore_revision:r.revision}});closeModal();notify('Earlier project logic restored as a new revision.');await navigate('projects');}):'View only']))));}
 async function createProject(){const name=field('Project name','text','',{required:true,maxlength:120});const file=field('Optional DataForge project logic JSON','file','',{accept:'.json'});const form=el('form',{},name.node,file.node,el('p',{class:'fine',text:'Import a projectVersion 1.0 logic document. Open .dfproj bundles in the visual designer instead. Normal designer publishing excludes source files; review inline parameters and test fixtures before sharing.'}),el('button',{class:'primary',type:'submit',text:'Create project'}));form.onsubmit=e=>{e.preventDefault();run(async()=>{let document={};if(file.input.files[0]){if(file.input.files[0].size>2*1024*1024)throw new Error('Logic file exceeds 2 MiB.');document=JSON.parse(await file.input.files[0].text());}await api(path('/projects'),{method:'POST',body:{name:name.input.value.trim(),document}});closeModal();notify('Project created.');await navigate('projects');});};modal('Create project',form);}
 async function projects() {
  const data=await api(path('/projects'));
  const cards=data.map(p=>{
   const actions=el('div',{class:'actions'},
    edit()?el('a',{href:`/studio?workspace=${encodeURIComponent(state.ws.id)}&project=${encodeURIComponent(p.id)}`,text:'Open designer'}):badge('viewer'),
    button('Export logic',async()=>{const full=await api(path('/projects/'+p.id));download(full.document,'project-logic.json');}),
    button('History',()=>projectHistory(p)),edit()?button('Run pipeline',()=>pipelineForm(p),true):null,state.ws.role==='owner'?button('Approvals',()=>approvalForm(p)):null);
   return card('',el('div',{class:'project-card'},el('div',{class:'project-icon','aria-hidden':'true',text:'< >'}),el('h3',{text:p.name}),el('p',{class:'fine',text:`Revision ${p.revision} | Saved ${fmtDate(p.updated)}`}),actions));
  });
  return el('div',{},title('Projects','Shared project logic, explicit publishing, and revision history.',edit()?[button('New project',createProject,true)]:[]),data.length?el('div',{class:'grid-cards'},cards):empty('No projects yet','Create a project to begin sharing versioned design logic.'));
 }

 async function upload(){const file=field('CSV file (UTF-8, up to 5 MiB)','file','',{accept:'.csv',required:true});const form=el('form',{},file.node,el('button',{class:'primary',type:'submit',text:'Upload CSV'}));form.onsubmit=e=>{e.preventDefault();run(async()=>{const f=file.input.files[0];if(!f||f.size>5*1024*1024)throw new Error('Choose a CSV of at most 5 MiB.');const bytes=new Uint8Array(await f.arrayBuffer());let text='';for(let i=0;i<bytes.length;i+=8192)text+=String.fromCharCode(...bytes.subarray(i,i+8192));const result=await api(path('/files'),{method:'POST',body:{name:f.name,content_base64:btoa(text)}});closeModal();notify(`Uploaded ${result.row_count} rows with ${result.columns.length} columns.`);await navigate('files');});};modal('Upload source data',form);}
 async function files(){const data=await api(path('/files'));return el('div',{},title('Data sources','Immutable CSV inputs with checksums and workspace access controls.',edit()?[button('Upload CSV',upload,true)]:[]),el('div',{class:'notice warning'},'Downloads return original source bytes. Review untrusted CSV files before opening them in spreadsheet software.'),card('Workspace files',data.length?table(['Filename','Size','Uploaded','Checksum','Actions'],data.map(f=>[f.name,`${(f.size/1024).toFixed(1)} KB`,fmtDate(f.created),el('span',{class:'mono',title:f.sha256,text:f.sha256.slice(0,12)+'\u2026'}),el('div',{class:'actions'},el('a',{href:path(`/files/${f.id}/download`),text:'Download'}),state.ws.role==='owner'?button('Delete',async()=>{if(!confirm('Delete this source file? Retained past outputs are kept.'))return;await api(path('/files/'+f.id),{method:'DELETE'});await navigate('files');}):null)])):empty('No source files','Upload a CSV to use it in a server-side SQL job.')));}
 async function jobDetail(id){const j=await api(path('/jobs/'+id));const r=j.result;const body=el('div',{},el('p',{},badge(j.status),' ',j.payload.name),el('pre',{text:j.payload.sql}),j.error?el('p',{class:'danger',text:j.error}):null,r?el('div',{},el('p',{class:'muted',text:`${r.row_count} rows \u00b7 SQLite ${r.engine_version} \u00b7 ${r.elapsed_seconds}s. Preview shows the first 100 rows.`}),table(r.columns,r.rows.slice(0,100)),el('p',{},el('a',{href:path(`/jobs/${id}/output`),text:'Download spreadsheet-safe CSV'}))):null,el('div',{class:'actions'},edit()&&['queued','running'].includes(j.status)?button('Cancel job',async()=>{await api(path(`/jobs/${id}/cancel`),{method:'POST'});closeModal();notify('Cancellation requested.');await navigate('jobs');}):null,edit()&&['failed','cancelled'].includes(j.status)?button('Retry as a new job',async()=>{await api(path(`/jobs/${j.id}/retry`),{method:'POST',headers:{'Idempotency-Key':crypto.randomUUID()}});closeModal();notify('A new job was queued.');await navigate('jobs');}):null));modal('Execution details',body);}
 function jobTable(data){return table(['Job','Status','Started / queued','Attempts','Details'],data.map(j=>[el('span',{text:j.name||j.id.slice(0,8)}),badge(j.status),fmtDate(j.started||j.created),String(j.attempts),button('Inspect',()=>jobDetail(j.id))]));}
 async function jobForm(asSchedule=false){
  const files=await api(path('/files'));const name=field('Name','text',asSchedule?'Scheduled query':'New query',{required:true,maxlength:120});const sql=field('SQLite SELECT query','textarea',files.length?'SELECT * FROM data LIMIT 100;':'SELECT 1 AS ready;',{required:true,maxlength:65536});
  const rows=files.map((f,i)=>{const checked=el('input',{type:'checkbox','aria-label':'Use '+f.name});const alias=el('input',{type:'text',value:i?'data'+(i+1):'data',pattern:'[A-Za-z][A-Za-z0-9_]{0,47}','aria-label':'Table alias for '+f.name});if(i===0)checked.checked=true;return {f,checked,alias,node:el('div',{class:'source-row'},el('label',{class:'check-label'},checked,f.name),alias)};});
  const interval=field('Interval in minutes','number','60',{min:1,max:43200,required:true});
  const form=el('form',{},name.node,el('h3',{text:'Source tables'}),rows.length?el('div',{},rows.map(r=>r.node)):el('p',{class:'fine',text:'No files selected. Constant queries are allowed.'}),sql.node,el('p',{class:'fine',text:'CSV columns are text. Use CAST(amount AS REAL) for numeric calculations. Read-only SELECT only; no file access, extensions, or database writes. Maximum output: 10,000 rows / 5 MiB.'}),asSchedule?interval.node:null,el('button',{class:'primary',type:'submit',text:asSchedule?'Create paused schedule':'Queue job'}));
  let key=crypto.randomUUID();form.onsubmit=e=>{e.preventDefault();run(async()=>{const body={name:name.input.value.trim(),sql:sql.input.value,sources:rows.filter(r=>r.checked.checked).map(r=>({file_id:r.f.id,table:r.alias.value.trim()}))};if(asSchedule)await api(path('/schedules'),{method:'POST',body:{name:body.name,job:body,interval_seconds:Number(interval.input.value)*60,enabled:false}});else await api(path('/jobs'),{method:'POST',headers:{'Idempotency-Key':key},body});closeModal();notify(asSchedule?'Schedule created paused. Enable it after review.':'Job queued. The separate worker runs it even after this page closes.');await navigate(asSchedule?'schedules':'jobs');});};modal(asSchedule?'Create server schedule':'Run a SQL query',form);
 }
 async function jobs(){const data=await api(path('/jobs'));return el('div',{},title('Jobs & results','Read-only CSV/SQLite execution in a separate, time-limited worker.',edit()?[button('New SQL job',()=>jobForm(),true)]:[]),card('Latest 100 executions',data.length?jobTable(data):empty('No jobs yet','Upload source data, then queue a SELECT query. Refresh to retrieve the latest status.')));}
 async function schedules(){const data=await api(path('/schedules'));return el('div',{},title('Schedules','Persisted interval schedules. Missed intervals are coalesced after downtime.',edit()?[button('New schedule',()=>jobForm(true),true)]:[]),el('div',{class:'notice warning'},'Schedules require the worker service. They start paused; enabling them is an explicit action. Restoring a backup pauses all schedules again.'),card('Server schedules',data.length?table(['Name','Interval','Next due','State','Actions'],data.map(s=>[s.name,`${s.interval_seconds/60} minutes`,fmtDate(s.next_at),badge(s.enabled?'enabled':'paused'),edit()?el('div',{class:'actions'},button(s.enabled?'Pause':'Enable',async()=>{if(!s.enabled&&!confirm('Enable this reviewed server schedule? It will run without an open browser.'))return;await api(path('/schedules/'+s.id),{method:'PATCH',body:{enabled:!s.enabled}});await navigate('schedules');}),button('Delete',async()=>{if(!confirm('Delete this schedule?'))return;await api(path('/schedules/'+s.id),{method:'DELETE'});await navigate('schedules');})):null])):empty('No schedules yet','Create a paused schedule from a reviewed SQL query.')));}
 async function memberForm(){const address=field('Existing account email','email','',{required:true});const select=el('select',{},['viewer','editor','owner'].map(r=>el('option',{value:r,text:r})));const form=el('form',{},address.node,el('label',{},'Workspace role',select),el('p',{class:'fine',text:'Viewer: read/download. Editor: projects, files, jobs and schedules. Owner: team access, activity and source deletion. Roles apply to every project in this workspace.'}),el('button',{class:'primary',type:'submit',text:'Save access'}));form.onsubmit=e=>{e.preventDefault();run(async()=>{await api(path('/members'),{method:'PUT',body:{email:address.input.value,role:select.value}});closeModal();await enter();});};modal('Grant or change access',form);}
 async function members(){const data=await api(path('/members'));return el('div',{},title('Team access','Server-enforced roles for every project, file, job and schedule in this workspace.',[button('Grant or change access',memberForm,true)]),card('Workspace members',table(['Name','Email','Role','Account','Actions'],data.map(m=>[m.name,m.email,badge(m.role),m.active?'Active':'Disabled',button('Remove',async()=>{if(!confirm('Remove workspace access for '+m.email+'?'))return;await api(path('/members/'+m.id),{method:'DELETE'});await enter();})]))));}
 async function auditView(){const data=await api(path('/audit'));return el('div',{},title('Activity log','Authenticated workspace events. Append-only through the application API.'),el('div',{class:'notice warning'},'A hash chain detects accidental edits, but is not independent tamper-proof evidence. A database administrator can rewrite the entire chain. No compliance certification is claimed.'),card('Latest 100 events',data.length?table(['Time','Event','Actor ID','Target'],data.map(e=>[fmtDate(e.at),el('span',{class:'mono',text:e.action}),e.actor?e.actor.slice(0,12):'System',e.target?e.target.slice(0,12):'\u2014'])):empty('No events yet','Workspace actions will appear here.')));}
 async function operations(){
  const op=await api('/api/admin/operations');
  const active=op.workers.filter(w=>w.heartbeat>Date.now()/1000-120);
  const metric=(name,value,note)=>el('div',{class:'metric'},el('div',{class:'label',text:name}),el('div',{class:'value',text:value}),el('small',{text:note}));
  const metrics=el('div',{class:'metrics'},
    metric('Worker heartbeat',active.length?'Online':'Offline','Heartbeat within 120 seconds'),
    metric('Database',(op.database_bytes/1048576).toFixed(1)+' MB','Main database file, excludes WAL'),
    metric('Audit chain',op.audit.valid?'Verified':'Invalid',op.audit.verified_events+' checked events'),
    metric('Build','2.1 RC1',op.storage));
  const limits=el('div',{},Object.entries(op.limits).map(([k,v])=>el('div',{class:'label-value'},el('span',{text:k.replaceAll('_',' ')}),el('strong',{text:v}))));
  const workers=op.workers.length?table(['Worker','State','Heartbeat'],op.workers.map(w=>[w.id.slice(0,12),w.state,fmtDate(w.heartbeat)])):empty('No worker registered','Start python -m app.cli worker in a separate service.');
  return el('div',{},title('Operations','Single-host health, execution limits and audit verification.'),metrics,card('Execution policy',limits),card('Deployment responsibilities',el('p',{class:'muted',text:op.deployment_notice})),card('Recent workers',workers));
 }

 async function accountForm(){const name=field('Full name','text','',{required:true,maxlength:120}),email=field('Email','email','',{required:true}),password=field('Initial password','password','',{required:true,minlength:14,maxlength:128,autocomplete:'new-password'});const form=el('form',{},name.node,email.node,password.node,el('p',{class:'fine',text:'Provisioned accounts are standard users. Share credentials through an approved secure channel. Administrator promotion is a local administrative action.'}),el('button',{class:'primary',type:'submit',text:'Create account'}));form.onsubmit=e=>{e.preventDefault();run(async()=>{await api('/api/admin/users',{method:'POST',body:{name:name.input.value,email:email.input.value,password:password.input.value}});password.input.value='';closeModal();notify('Account created. Grant workspace access separately.');await navigate('accounts');});};modal('Provision an account',form);}
 async function passwordForm(){
  const old=field('Current password','password','',{required:true,autocomplete:'current-password'}),next=field('New password (14 to 128 characters)','password','',{required:true,minlength:14,maxlength:128,autocomplete:'new-password'}),otp=field('Authenticator or recovery code (when enabled)','text','',{maxlength:128,autocomplete:'one-time-code'});
  const form=el('form',{},old.node,next.node,otp.node,el('button',{class:'primary',type:'submit',text:'Change password and sign out'}));form.onsubmit=e=>{e.preventDefault();run(async()=>{await api('/api/auth/password',{method:'POST',body:{current_password:old.input.value,new_password:next.input.value,otp:otp.input.value.trim()}});showLogin();});};modal('Change password',form);
 }
 async function accounts(){const data=await api('/api/admin/users');return el('div',{},title('Accounts','Provision identities separately from workspace membership.',[button('Change my password',passwordForm),button('Create account',accountForm,true)]),card('User directory',table(['Name','Email','Type','State','Action'],data.map(u=>[u.name,u.email,u.is_admin?'Administrator':'Standard',u.active?'Active':'Disabled',button(u.active?'Disable':'Enable',async()=>{if(!confirm((u.active?'Disable':'Enable')+' '+u.email+'? Existing sessions will be revoked.'))return;await api('/api/admin/users/'+u.id,{method:'PATCH',body:{active:!u.active}});await navigate('accounts');})]))));}
  async function revealCodes(codes){
  modal('Save your recovery codes',el('div',{},el('p',{class:'notice warning',text:'Each code can be used once. Store them in your password manager. They will not be shown again.'}),el('pre',{class:'recovery-codes',text:codes.join('\n')}),el('div',{class:'actions'},button('Download recovery codes',()=>download({recovery_codes:codes},'dataforge-recovery-codes.json')),button('I have saved my codes',async()=>{closeModal();state.tab='security';await enter();},true))));
 }
 async function setupMFA(){
  const password=field('Current password','password','',{required:true,autocomplete:'current-password'});
  const form=el('form',{},el('p',{text:'Enter your password, then add the setup key to an authenticator app.'}),password.node,el('button',{type:'submit',class:'primary',text:'Create setup key'}));
  form.onsubmit=e=>{e.preventDefault();run(async()=>{const setup=await api('/api/auth/mfa/setup',{method:'POST',body:{password:password.input.value}});password.input.value='';
   const code=field('Six-digit authenticator code','text','',{required:true,inputmode:'numeric',pattern:'[0-9]{6}',autocomplete:'one-time-code'});
   const confirm=el('form',{},el('p',{text:'Add this key manually in your authenticator. Account: '+state.user.email}),el('pre',{class:'secret-value',text:setup.secret}),el('p',{},el('a',{href:setup.otpauth_uri,text:'Open authenticator app'})),el('p',{class:'fine',text:'Setup expires in ten minutes. Verify the generated code to enable protection.'}),code.node,el('button',{type:'submit',class:'primary',text:'Verify and enable'}));
   confirm.onsubmit=event=>{event.preventDefault();run(async()=>{const result=await api('/api/auth/mfa/confirm',{method:'POST',body:{code:code.input.value.trim()}});await revealCodes(result.recovery_codes);});};modal('Verify two-step setup',confirm);
  });};modal('Set up two-step verification',form);
 }
 async function factorAction(action){
  const password=field('Current password','password','',{required:true,autocomplete:'current-password'}),code=field('Authenticator or unused recovery code','text','',{required:true,autocomplete:'one-time-code',maxlength:128});
  const form=el('form',{},password.node,code.node,el('button',{type:'submit',class:'primary',text:action==='disable'?'Disable and sign out':'Replace recovery codes'}));
  form.onsubmit=e=>{e.preventDefault();run(async()=>{const result=await api('/api/auth/mfa/'+action,{method:'POST',body:{password:password.input.value,code:code.input.value.trim()}});password.input.value='';if(action==='disable'){showLogin();}else await revealCodes(result.recovery_codes);});};modal(action==='disable'?'Disable two-step verification':'Replace recovery codes',form);
 }
 async function securityView(){
  const [factor,sessions]=await Promise.all([api('/api/auth/mfa'),api('/api/auth/sessions')]);
  const buttons=factor.enabled?[button('Replace recovery codes',()=>factorAction('recovery-codes')),factor.required?null:button('Disable two-step verification',()=>factorAction('disable'))]:[button('Set up two-step verification',setupMFA,true)];
  return el('div',{},title('Account security','Manage sign-in protection and your active sessions.',[button('Change password',passwordForm)]),factor.required&&!factor.enabled?el('p',{class:'notice warning',text:'Your administrator requires two-step verification. Complete setup before accessing workspace data.'}):null,
   card('Two-step verification',el('div',{},badge(factor.enabled?'Enabled':'Not enabled'),el('p',{text:factor.enabled?`${factor.recovery_codes_remaining} single-use recovery codes remaining. Authenticator codes cannot be reused.`:'Use a time-based authenticator app to protect your account.'}),el('div',{class:'actions'},buttons))),
   card('Active sessions',table(['Session','Created','Last seen','Action'],sessions.map(s=>[s.current?'This session':'Other session',fmtDate(s.created),fmtDate(s.last_seen),button(s.current?'Sign out here':'Revoke session',async()=>{await api('/api/auth/sessions/'+s.id,{method:'DELETE'});if(s.current)showLogin();else await navigate('security');})]))));
 }
 async function requestRecovery(){
  const options=await api('/api/auth/options');if(!options.password_recovery)throw new Error('Email recovery is not configured. Contact your administrator.');
  const email=field('Account email address','email',$('email').value,{required:true,autocomplete:'username'});
  const form=el('form',{},email.node,el('button',{type:'submit',class:'primary',text:'Send reset link'}));
  form.onsubmit=e=>{e.preventDefault();run(async()=>{const result=await api('/api/auth/recovery/request',{method:'POST',body:{email:email.input.value}});modal('Check your email',el('p',{role:'status',text:result.message}));});};modal('Reset your password',form);
 }
 async function finishRecovery(token){
  const password=field('New password (14 to 128 characters)','password','',{required:true,minlength:14,maxlength:128,autocomplete:'new-password'}),confirmation=field('Confirm new password','password','',{required:true,minlength:14,maxlength:128,autocomplete:'new-password'});
  const form=el('form',{},el('p',{text:'The link is single-use and expires after fifteen minutes. Your authenticator remains required.'}),password.node,confirmation.node,el('button',{type:'submit',class:'primary',text:'Set new password'}));
  form.onsubmit=e=>{e.preventDefault();run(async()=>{if(password.input.value!==confirmation.input.value)throw new Error('Passwords do not match.');await api('/api/auth/recovery/complete',{method:'POST',body:{token,new_password:password.input.value}});showLogin();$('loginError').textContent='Password updated. Sign in with your new password and authenticator.';});};modal('Choose a new password',form);
 }
 async function finishSSO(challenge){
  const code=field('Authenticator or unused recovery code','text','',{required:true,autocomplete:'one-time-code',maxlength:128});
  const form=el('form',{},el('p',{text:'Your identity provider verified the account. Complete your DataForge second factor.'}),code.node,el('button',{type:'submit',class:'primary',text:'Complete sign-in'}));
  form.onsubmit=e=>{e.preventDefault();run(async()=>{const result=await api('/api/auth/oidc/factor',{method:'POST',body:{challenge,code:code.input.value.trim()}});state.user=result.user;state.csrf=result.csrf;closeModal();await enter();});};modal('Complete two-step sign-in',form);
 }
 async function pipelineForm(p){
  const [full,files,caps]=await Promise.all([api(path('/projects/'+p.id)),api(path('/files')),api('/api/capabilities')]);
  const mappings=full.document.mappings||[];if(!mappings.length)throw new Error('Publish a mapping from the designer before running it.');
  const mapping=el('select',{'aria-label':'Mapping'}),target=el('select',{'aria-label':'Target'}),sources=el('div'),review=el('div');
  const minute=field('Schedule interval (minutes; leave blank for one run)','number','',{min:1,max:43200});
  const reviewed=el('input',{type:'checkbox'});let bindings=[],compiled=null,key=crypto.randomUUID();
  mapping.append(...mappings.map(m=>el('option',{value:m.mapping.id,text:m.mapping.name||m.mapping.id})));
  const runButton=button('Queue reviewed pipeline',async()=>{if(!compiled||!reviewed.checked)throw new Error('Compile and review the pipeline first.');runButton.disabled=true;try{const body=request();if(minute.input.value)body.interval_seconds=Number(minute.input.value)*60;const result=await api(path(`/projects/${p.id}/run`),{method:'POST',headers:{'Idempotency-Key':key},body});closeModal();notify(result.schedule_id?'Pipeline schedule created paused. Enable it after reviewing the stored revision.':'Pipeline queued. Execution does not depend on this browser tab.');await navigate(result.schedule_id?'schedules':'jobs');}finally{runButton.disabled=false;}},true);runButton.disabled=true;
  function clear(){compiled=null;reviewed.checked=false;runButton.disabled=true;review.replaceChildren();}
  function selected(){return mappings.find(m=>m.mapping.id===mapping.value);}
  function request(){return {revision:full.revision,mapping_id:mapping.value,target_id:target.value,bindings:Object.fromEntries(bindings.map(b=>[b.node.id,b.select.value]))};}
  function sourceFields(){clear();const m=selected(),needed=new Set();const visit=id=>{if(needed.has(id))return;needed.add(id);for(const l of m.links||[]){const to=typeof l.to==='object'?l.to.node:l.to,from=typeof l.from==='object'?l.from.node:l.from;if(to===id)visit(from);}};visit(target.value);
   bindings=(m.nodes||[]).filter(n=>needed.has(n.id)&&n.type==='csv_source').map(n=>{const select=el('select',{'aria-label':'Source file for '+n.name,required:true},el('option',{value:'',text:'Choose a workspace CSV'}),files.map(f=>el('option',{value:f.id,text:f.name})));const match=files.find(f=>f.name===n.config?.fileName);if(match)select.value=match.id;select.onchange=clear;return {node:n,select,element:el('label',{},n.name||n.id,select)};});sources.replaceChildren(...bindings.map(b=>b.element));
  }
  function targetFields(){const m=selected();target.replaceChildren(...m.nodes.filter(n=>['csv_target','json_target','preview_target','reject_target'].includes(n.type)).map(n=>el('option',{value:n.id,text:n.name||n.id})));sourceFields();}
  mapping.onchange=targetFields;target.onchange=sourceFields;reviewed.onchange=()=>{runButton.disabled=!compiled||!reviewed.checked;};minute.input.onchange=()=>{key=crypto.randomUUID();};
  const compileButton=button('Compile and review',async()=>{const result=await api(path(`/projects/${p.id}/compile`),{method:'POST',body:request()});compiled=result;reviewed.checked=false;runButton.disabled=true;review.replaceChildren(el('h3',{text:'Compiled SQLite query'}),el('pre',{text:result.job.sql}),el('p',{class:'fine',text:'Columns: '+result.output_columns.join(', ')}),...result.warnings.map(w=>el('p',{class:'notice warning',text:w})));});
  const body=el('div',{},el('p',{class:'notice warning',text:'Portable SQLite execution, not full DuckDB compatibility. Unsupported nodes and dialect-specific expressions are rejected. Source bindings and the exact project revision are recorded.'}),el('p',{class:'fine',text:'Supported nodes: '+caps.supported_nodes.join(', ')}),el('label',{},'Mapping',mapping),el('label',{},'Target',target),el('h3',{text:'Bind source files'}),sources,minute.node,compileButton,review,el('label',{class:'check-label'},reviewed,'I reviewed the SQL, source bindings, and SQLite semantics.'),runButton);
  targetFields();modal('Run visual pipeline on server',body);
 }
 async function approvalForm(p){
  const data=await api(path(`/projects/${p.id}/approval`)),required=el('input',{type:'checkbox'});required.checked=data.required;
  const body=el('div',{},el('p',{text:`Current revision: ${data.revision}. ${data.approval?'Approved by another owner.':'Not approved.'}`}),el('p',{class:'fine',text:'This policy controls visual pipeline runs and their schedules. It does not remove an editor\'s separate permission to submit SQL jobs.'}),el('label',{class:'check-label'},required,'Require another workspace owner to approve each revision'),button('Save approval policy',async()=>{await api(path(`/projects/${p.id}/approval`),{method:'PUT',body:{approval_required:required.checked}});closeModal();notify('Approval policy updated.');}),button('Approve this revision',async()=>{await api(path(`/projects/${p.id}/approve/${data.revision}`),{method:'POST'});closeModal();notify('Revision approved.');},true),data.approval?button('Revoke approval',async()=>{await api(path(`/projects/${p.id}/approve/${data.revision}`),{method:'DELETE'});closeModal();notify('Approval revoked. Pending and future pipeline execution will recheck approval.');}):null);modal('Project approval controls',body);
 }
 async function deliveryView(){
  const data=await api('/api/admin/service-status');
  return el('div',{},title('Delivery and readiness','Maintenance, encrypted backups, notification retries, and deployment gates.'),
   card('Maintenance process',el('p',{text:data.maintenance_online?'Online - heartbeat '+fmtDate(data.maintenance_heartbeat):'Offline. Start python -m app.cli maintenance as a separate service.'})),
   card('Last completed backup',el('p',{text:data.last_backup?`${fmtDate(data.last_backup.at)} | ${data.last_backup.file} | Remote verified: ${data.last_backup.remote_verified?'yes':'no'}`:'No completed scheduled backup has been recorded.'}),data.backup_error?[badge(data.backup_error)]:[]),
   card('Notification queue',table(['State','Messages'],Object.entries(data.notifications).map(([status,count])=>[status,String(count)]))),
   card('Production gates',el('div',{},el('p',{class:data.preflight.ready?'':'notice warning',text:data.preflight.ready?'Configured gates passed. Operator approval and continuing monitoring remain necessary.':'Production startup is blocked until every required gate is satisfied.'}),table(['Check','Status','Details'],data.preflight.checks.map(c=>[c.check.replaceAll('_',' '),badge(c.passed?'Passed':'Blocked'),c.detail])))));
 }

 const views={security:securityView,delivery:deliveryView,overview,projects,files,jobs,schedules,members,audit:auditView,operations,accounts};
 $('loginForm').addEventListener('submit',e=>{e.preventDefault();const submit=e.currentTarget.querySelector('button');submit.disabled=true;$('loginError').textContent='';(async()=>{try{const data=await api('/api/auth/login',{method:'POST',body:{email:$('email').value,password:$('password').value,otp:$('otp').value.trim()}});state.user=data.user;state.csrf=data.csrf;$('password').value='';$('otp').value='';await enter();}catch(error){$('loginError').textContent=error.message;}finally{submit.disabled=false;}})();});
 $('logout').onclick=()=>run(async()=>{await api('/api/auth/logout',{method:'POST'});showLogin();});
 $('refresh').onclick=()=>run(()=>enter());
 $('workspace').onchange=()=>run(async()=>{state.ws=state.workspaces.find(w=>w.id===$('workspace').value);state.tab='overview';await enter();});
 $('modalClose').onclick=closeModal;
 $('accountName').onclick=()=>run(passwordForm);
 // A keyboard-accessible account action is available to every role, not only administrators.
 const pwButton=button('Password',passwordForm);pwButton.className='subtle';$('logout').before(pwButton);
 $('forgotPassword').onclick=()=>run(requestRecovery);
 run(async()=>{const options=await api('/api/auth/options');$('ssoLogin').hidden=!options.sso;});
 run(async()=>{
  const fragment=new URLSearchParams(location.hash.slice(1));const reset=fragment.get('reset'),sso=fragment.get('sso-factor');
  if(reset||sso){history.replaceState(null,'',location.pathname+location.search);showLogin();if(reset)await finishRecovery(reset);else await finishSSO(sso);return;}
  try{const session=await api('/api/auth/me');state.user=session.user;state.csrf=session.csrf;await enter();}catch(_){showLogin();}
 });
 // Test hook exposes only non-sensitive UI state; never tokens or password fields.
 window.DataForgeTeamUI={get tab(){return state.tab;},get workspace(){return state.ws?.id;}};
})();
