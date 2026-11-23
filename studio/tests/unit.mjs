// Tests exact application functions in Node's VM and WebCrypto.
// Persistence cases use a deterministic in-memory transaction MODEL, NOT real IndexedDB.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { webcrypto, randomUUID } from 'node:crypto';
const html=readFileSync(new URL('../index.html',import.meta.url),'utf8');
const source=readFileSync(new URL('../src/business.js',import.meta.url),'utf8');
const cut=(s,start,end)=>s.slice(s.indexOf(start),s.indexOf(end,s.indexOf(start)));
const codec=cut(html,'const ProjectFile = {','/* Project section at the top');
const inflate=cut(html,'async function inflateText(','function blurActive(');
const hardening=source.slice(0,source.indexOf('const originalRepoSave'));
const codecWrappers=cut(source,'const originalProjectParse','const originalSources');
const fresh=()=>({projectVersion:'1.0',project:{id:'project-test',name:'Test',createdAt:'2026-01-01',updatedAt:'2026-01-01'},mappings:[],workflows:[],parameterSets:[],components:[],versions:{}});
const deep=x=>x===undefined?undefined:structuredClone(x);

class TransactionModel {
  constructor(){this.stores={project:new Map(),files:new Map(),runs:new Map()};this.jobs=[];this.active=false;this.failNextWrite=null;}
  transaction(names,mode){
    const db=this,tx={error:null,requests:[],aborted:false,finished:false,mode};
    tx.abort=()=>{if(tx.finished)return;tx.aborted=true;};
    tx.objectStore=name=>({
      get:id=>enqueue(()=>deep(tx.data[name].get(id))),
      getAll:()=>enqueue(()=>[...tx.data[name].values()].map(deep)),
      put:value=>enqueue(()=>{if(db.failNextWrite){const e=db.failNextWrite;db.failNextWrite=null;throw e;}tx.data[name].set(value.id,deep(value));return value.id;}),
      delete:id=>enqueue(()=>{tx.data[name].delete(id);})
    });
    function enqueue(fn){const req={};tx.requests.push({fn,req});return req;}
    tx.begin=()=>{tx.data=Object.fromEntries(Object.entries(db.stores).map(([k,v])=>[k,new Map([...v].map(([a,b])=>[a,deep(b)]))]));queueMicrotask(step);};
    function finish(){if(tx.finished)return;tx.finished=true;if(!tx.aborted&&mode==='readwrite')db.stores=tx.data;try{if(tx.aborted)tx.onabort?.();else tx.oncomplete?.();}finally{db.active=false;db.next();}}
    function step(){
      if(tx.aborted){finish();return;}const item=tx.requests.shift();if(!item){finish();return;}
      try{item.req.result=item.fn();item.req.onsuccess?.({target:item.req});}catch(e){tx.error=e;tx.aborted=true;}
      queueMicrotask(step);
    }
    this.jobs.push(tx);queueMicrotask(()=>this.next());return tx;
  }
  next(){if(this.active||!this.jobs.length)return;this.active=true;this.jobs.shift().begin();}
}
function runtime(db=new TransactionModel()){
  const Repo={db,project:fresh(),mode:'indexeddb',stale:false,written:null,tabId:randomUUID(),signature(){return JSON.stringify({...this.project,project:{...this.project.project,updatedAt:''}});},track(){},rememberActive(){}};
  const sandbox={Repo,APP:{formatVersion:'1.0',version:'test',duckdb:'1.32.0'},crypto:webcrypto,TextEncoder,TextDecoder,Response,Blob,CompressionStream,DecompressionStream,Uint8Array,ArrayBuffer,atob,btoa,DOMException,
    setTimeout,clearTimeout,setInterval,clearInterval,console,dfUuid:randomUUID,clone:x=>JSON.parse(JSON.stringify(x)),fmtBytes:n=>String(n)+' bytes',toast(){},state:{running:false},Workflow:{runState:null},Studio:{mode:'mapping'},Registry:{get:t=>['csv_source','csv_target','filter','preview_target'].includes(t)?{category:t.endsWith('source')?'source':'target'}:null},TaskRegistry:{get:t=>t==='start'?{}:null},$(){return null;},navigator:{},isSecureContext:true};
  vm.createContext(sandbox);vm.runInContext(inflate+codec+hardening+codecWrappers+'\nglobalThis.parts={ProjectFile,ProjectSafety,Business};',sandbox);
  sandbox.parts.Business.renderStatus=()=>{};
  Repo.flush=()=>Repo.writeProject();
  return {...sandbox.parts,Repo,db};
}
async function load(r){r.Repo._initializingBusiness=true;const stored=await r.Repo.get('project','current');r.Repo._initializingBusiness=false;if(stored){r.Repo.project=deep(stored.document);r.Repo.written=r.Repo.signature();}}
const bundle=()=>({format:'dataforge-project',formatVersion:1,project:fresh(),files:[],runs:[],session:{}});

await test('Compressed project codec preserves the full bundle',async()=>{const r=runtime(),b=bundle(),p=await r.ProjectFile.encode(b),raw=await r.ProjectFile.parse(new Uint8Array(await p.arrayBuffer()),'test.dfproj');assert.equal(JSON.stringify(raw),JSON.stringify(b));});
await test('AES-GCM encrypted project round trip uses the actual codec',async()=>{const r=runtime(),b=bundle(),packed=await r.ProjectFile.encode(b),locked=await r.ProjectFile.lock(new Uint8Array(await packed.arrayBuffer()),'correct-horse-battery-42'),env=JSON.parse(await locked.text());r.ProjectSafety.inspect(env);const opened=await r.ProjectFile.unlock(env,'correct-horse-battery-42'),raw=await r.ProjectFile.parse(opened,'test.dfproj');assert.equal(JSON.stringify(raw),JSON.stringify(b));});
await test('Wrong project password is rejected',async()=>{const r=runtime(),locked=await r.ProjectFile.lock(new TextEncoder().encode('private'),'correct-password'),env=JSON.parse(await locked.text());await assert.rejects(r.ProjectFile.unlock(env,'wrong-password'),e=>e.wrongPassword===true);});
await test('Tampered encrypted project is rejected',async()=>{const r=runtime(),locked=await r.ProjectFile.lock(new TextEncoder().encode('private'),'correct-password'),env=JSON.parse(await locked.text()),b=Buffer.from(env.data,'base64');b[0]^=1;env.data=b.toString('base64');await assert.rejects(r.ProjectFile.unlock(env,'correct-password'),e=>e.wrongPassword===true);});
await test('Encrypted envelope rejects a different hash algorithm',()=>{const r=runtime();assert.throws(()=>r.ProjectSafety.encrypted({formatVersion:1,kdf:{name:'PBKDF2',hash:'SHA-1',iterations:600000},cipher:{name:'AES-GCM'}}),/Unsupported/);});
await test('Encrypted envelope rejects a non-integer work factor',()=>{const r=runtime();assert.throws(()=>r.ProjectSafety.encrypted({formatVersion:1,kdf:{name:'PBKDF2',hash:'SHA-256',iterations:600000.5},cipher:{name:'AES-GCM'}}),/work factor/);});
await test('Malformed compressed input fails without returning a partial project',async()=>{const r=runtime();await assert.rejects(r.ProjectFile.parse(new Uint8Array([0x1f,0x8b,1,2,3]),'broken.dfproj'),/damaged|incomplete/);});
await test('Base64 padding and alphabets are validated',()=>{const r=runtime();for(const s of ['=AAA','A===','a!aa','a'])assert.throws(()=>r.ProjectSafety.base64Size(s,'fixture'));assert.equal(r.ProjectSafety.base64Size('YWJj','fixture'),3);});
await test('Transaction model: committed saves advance the revision',async()=>{const r=runtime();assert.equal(await r.Repo.writeProject(),true);assert.equal(r.Business.saveState,'saved');assert.equal(r.db.stores.project.get('current').revision,r.Repo._expectedRecord);});
await test('Transaction model: unchanged content does not rewrite the record',async()=>{const r=runtime();await r.Repo.writeProject();const revision=r.Repo._expectedRecord;assert.equal(await r.Repo.writeProject(),true);assert.equal(r.Repo._expectedRecord,revision);});
await test('Transaction model: concurrent first saves allow only one writer',async()=>{const db=new TransactionModel(),a=runtime(db),b=runtime(db);a.Repo.project.project.name='A';b.Repo.project.project.name='B';const result=await Promise.all([a.Repo.writeProject(),b.Repo.writeProject()]);assert.equal(result.filter(Boolean).length,1);assert.equal(b.Repo.stale,true);assert.equal(db.stores.project.get('current').document.project.name,'A');});
await test('Transaction model: same-tab saves are serialized in order',async()=>{const r=runtime();r.Repo.project.project.name='first';const a=r.Repo.writeProject();r.Repo.project.project.name='second';const b=r.Repo.writeProject();await Promise.all([a,b]);assert.equal(r.db.stores.project.get('current').document.project.name,'second');assert.equal(r.Business.saveState,'saved');});
await test('Transaction model: lost-update protection works without BroadcastChannel',async()=>{const db=new TransactionModel(),a=runtime(db);await a.Repo.writeProject();const b=runtime(db);await load(b);a.Repo.project.project.name='newer';await a.Repo.writeProject();b.Repo.project.project.name='stale';assert.equal(await b.Repo.writeProject(),false);assert.equal(b.Repo.stale,true);assert.equal(db.stores.project.get('current').document.project.name,'newer');});
await test('Transaction model: stale tabs cannot overwrite stored source files',async()=>{const db=new TransactionModel(),a=runtime(db);await a.Repo.writeProject();const b=runtime(db);await load(b);a.Repo.project.project.name='newer';await a.Repo.writeProject();await assert.rejects(b.Repo.put('files',{id:'file1',bytes:new Uint8Array([1])}),/newer/);assert.equal(db.stores.files.size,0);});
await test('Transaction model: stale tabs cannot delete stored source files',async()=>{const db=new TransactionModel(),a=runtime(db);await a.Repo.writeProject();await a.Repo.put('files',{id:'file1'});const b=runtime(db);await load(b);a.Repo.project.project.name='newer';await a.Repo.writeProject();await assert.rejects(b.Repo.del('files','file1'));assert.equal(db.stores.files.size,1);});
await test('Transaction model: quota failure retains the previous committed project',async()=>{const r=runtime();await r.Repo.writeProject();r.Repo.project.project.name='unsaved';r.db.failNextWrite=new DOMException('Full','QuotaExceededError');assert.equal(await r.Repo.writeProject(),false);assert.equal(r.Business.saveState,'error');assert.equal(r.db.stores.project.get('current').document.project.name,'Test');});
await test('Transaction model: aborted writes reject instead of hanging',async()=>{const r=runtime();await r.Repo.writeProject();r.db.failNextWrite=new DOMException('Aborted','AbortError');await assert.rejects(r.Repo.put('files',{id:'broken'}),e=>e.name==='AbortError');});
await test('Transaction model: a later retry can commit after a write failure',async()=>{const r=runtime();await r.Repo.writeProject();r.Repo.project.project.name='retried';r.db.failNextWrite=new DOMException('Full','QuotaExceededError');await r.Repo.writeProject();assert.equal(await r.Repo.writeProject(),true);assert.equal(r.Business.saveState,'saved');});
await test('Transaction model: legacy records can be loaded and upgraded safely',async()=>{const db=new TransactionModel();db.stores.project.set('current',{id:'current',document:fresh()});const r=runtime(db);await load(r);assert.match(r.Repo._expectedRecord,/^legacy:/);r.Repo.project.project.name='upgraded';assert.equal(await r.Repo.writeProject(),true);assert.ok(!r.Repo._expectedRecord.startsWith('legacy:'));});
await test('Transaction model: checkpoints exclude source stores and run history',async()=>{const r=runtime();await r.Repo.writeProject();await r.Repo.put('files',{id:'s',bytes:new TextEncoder().encode('PRIVATE_SOURCE')});await r.Repo.put('runs',{id:'r',value:'PRIVATE_RUN'});const point=await r.Business.checkpoint({force:true});assert.ok(!point.json.includes('PRIVATE_SOURCE'));assert.ok(!point.json.includes('PRIVATE_RUN'));assert.equal(point.digest,await r.Business.digest(point.json));});
await test('Transaction model: checkpoints retain parameter values and disclose that scope',async()=>{const r=runtime();r.Repo.project.parameterSets=[{id:'ps',name:'params',values:{$$SECRET:'TEST_SECRET'}}];const point=await r.Business.checkpoint({force:true});assert.ok(point.json.includes('TEST_SECRET'));});
await test('Transaction model: automatic checkpoints skip unchanged logic',async()=>{const r=runtime();await r.Business.checkpoint({force:true});assert.equal(await r.Business.checkpoint(),null);assert.equal(r.Business.points.length,1);});
await test('Transaction model: checkpoint retention is bounded to 10 records',async()=>{const r=runtime();for(let i=0;i<13;i++){r.Repo.project.project.name='Version '+i;await r.Business.checkpoint({force:true});}assert.equal(r.Business.points.length,10);});
await test('Transaction model: oversized logic checkpoint is refused',async()=>{const r=runtime();r.Repo.project.project.description='x'.repeat(4*2**20);await assert.rejects(r.Business.checkpoint({force:true}),/4 MiB/);assert.equal(r.Business.points.length,0);});
await test('SHA-256 checkpoint digest detects accidental content changes',async()=>{const r=runtime();assert.notEqual(await r.Business.digest('first'),await r.Business.digest('second'));});
