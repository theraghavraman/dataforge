/* Authenticated visual-designer integration. Logic is explicitly published;
   uploaded source bytes and preview results remain in this tab only. */
'use strict';
(async()=>{
 const D=window.DataForge;
 const params=new URLSearchParams(location.search);
 const ws=params.get('workspace'),pid=params.get('project');
 let revision=null,csrf=null,lastPublished=null,serverName='',publishing=false,stopped=false;
 const memory=new Map();
 const text=(tag,value)=>{const n=document.createElement(tag);n.textContent=value;return n;};
 const fail=message=>{const box=text('main',message);box.style.cssText='padding:40px;font:16px system-ui;color:#eee;background:#121922;min-height:100vh';const a=text('a',' Return to Team workspace');a.href='/';box.append(a);document.body.replaceChildren(box);};
 if(!D||!ws||!pid){fail('Open a project from the authenticated Team workspace.');return;}
 const path=`/api/workspaces/${encodeURIComponent(ws)}/projects/${encodeURIComponent(pid)}`;
 async function api(url,opts={}){
  const response=await fetch(url,{...opts,credentials:'same-origin',headers:{'Content-Type':'application/json',...(csrf?{'X-CSRF-Token':csrf}:{}),...opts.headers},body:opts.body===undefined?undefined:JSON.stringify(opts.body)});
  const data=await response.json();
  if(!response.ok){const e=new Error(typeof data.detail==='string'?data.detail:data.detail?.message||'Server request failed.');e.status=response.status;throw e;}return data;
 }
 let session,project,role;
 try{
  session=await api('/api/auth/me');csrf=session.csrf;
  const workspaces=await api('/api/workspaces');role=workspaces.find(w=>w.id===ws)?.role;
  if(!['owner','editor'].includes(role))throw new Error('An editor or owner role is required to open the designer. Viewers can export project logic from the workspace.');
  project=await api(path);revision=project.revision;serverName=project.name;
 }catch(e){fail(e.message);return;}
 // Avoid reading the legacy browser repository or remembering project data locally.
 // Only appearance preferences may persist. The original standalone designer is retained separately.
 const ephemeral='dataforge.team.tab.'+Math.random().toString(36).slice(2);
 D.APP.storageKey=ephemeral+'.mapping';D.APP.activeKey=ephemeral+'.active';D.ProjectFile.stateKey=ephemeral+'.file';
 D.Repo.rememberActive=()=>{};D.ProjectUI.load=()=>{D.ProjectUI.saved=null;};
 const savedMark=D.ProjectUI.markSaved.bind(D.ProjectUI);
 D.ProjectUI.markSaved=function(name){savedMark(name);try{localStorage.removeItem(D.ProjectFile.stateKey);}catch(_){};};
 D.Repo.get=async(store,id)=>memory.get(store+':'+id)||null;
 D.Repo.all=async store=>[...memory.entries()].filter(([k])=>k.startsWith(store+':')).map(([,v])=>v);
 D.Repo.put=async(store,value)=>{memory.set(store+':'+value.id,structuredClone(value));};
 D.Repo.del=async(store,id)=>{memory.delete(store+':'+id);};
 D.Repo.guardFresh=async()=>!stopped;
 D.Repo.writeProject=async()=>{D.Business.setSave('memory');return false;};
 D.Repo.init=async function(){
  this.db=null;this.mode='memory';this.channel=null;
  this.project=project.document.projectVersion?this.normalize(project.document):this.fresh();
  this.project.project.name=serverName;
  this.runs=[];this.written=null;
  if(this.project.mappings.length){const first=this.project.mappings[0];this.activeId=first.mapping.id;D.Actions.loadDoc(structuredClone(first));await this.hydrateFiles(first);}
  else D.UI.renderAll();
  this.render();await this.storageStats();await D.Business.init();
  D.Business.disarmScheduler();
  lastPublished=this.signature();
 };
 D.start();
 // The banner is separate from the legacy local save indicator.
 const bar=document.createElement('div');bar.id='teamPublishBar';bar.style.cssText='position:fixed;z-index:800;bottom:0;left:0;right:0;display:flex;flex-wrap:wrap;align-items:center;gap:12px;padding:10px 16px;background:#20372f;color:#e8fff4;border-top:1px solid #6ab697;font:12px system-ui;box-shadow:0 -4px 14px #0005';
 const back=text('a','Team workspace');back.href='/';back.style.color='#a8efce';
 const status=text('span',`Server revision ${revision}. Edits are session-only until published. Logic only; source files are not uploaded.`);status.setAttribute('role','status');status.style.flex='1';
 const publish=text('button','Publish logic');publish.type='button';publish.className='btn primary';
 const recover=text('button','Download recovery');recover.type='button';recover.className='btn';recover.onclick=()=>D.Repo.saveFile({suffix:'-team-recovery'});
 const reload=text('button','Reload server version');reload.type='button';reload.className='btn';reload.onclick=()=>{if(confirm('Discard unpublished edits and reload the server version? Download a recovery file first.'))location.reload();};
 bar.append(back,status,recover,reload,publish);document.body.append(bar);
 const appElement=document.getElementById('app');
 if(appElement&&typeof ResizeObserver!=='undefined')new ResizeObserver(()=>{appElement.style.height=`calc(100dvh - ${bar.getBoundingClientRect().height}px)`;}).observe(bar);
 async function doPublish(){
  if(publishing||stopped)return;
  publishing=true;publish.disabled=true;status.textContent='Publishing logic to the server...';
  try{
   D.Repo.track();
   const snapshot=structuredClone(D.Repo.project);
   D.ProjectSafety.inspect(snapshot);
   const sig=JSON.stringify({...snapshot,project:{...snapshot.project,updatedAt:''}});
   const saved=await api(path,{method:'PUT',body:{name:snapshot.project.name||serverName,document:snapshot,revision}});
   revision=saved.revision;lastPublished=sig;
   status.textContent=`Published server revision ${revision}. Source files remain in this tab; project parameters/test fixtures are part of published logic.`;
  }catch(e){
   status.textContent=(e.status===409?'Save conflict: newer server changes exist. Download recovery before reloading. ':e.status===401?'Session expired. Download recovery and sign in again. ':'Publish failed. ')+e.message;
   if(e.status===409||e.status===401||e.status===403||e.status===404)stopped=true;
  }finally{publishing=false;publish.disabled=stopped;}
 }
 publish.onclick=doPublish;
 // Keep closure state private; never persist auth/session/CSRF tokens in browser storage.
 window.addEventListener('beforeunload',e=>{if(D.Repo.project&&D.Repo.signature()!==lastPublished){e.preventDefault();e.returnValue='';}});
 document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='s'&&!e.shiftKey){e.preventDefault();e.stopImmediatePropagation();doPublish();}},true);
 window.DataForgeTeamBridge={get revision(){return revision;},get stopped(){return stopped;}};
})().catch(error=>{document.body.textContent='Team designer failed to initialize: '+error.message;});
