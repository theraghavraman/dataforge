// REAL-ORIGIN RELEASE GATE. Not executed during this delivery: browser navigation was policy-blocked.
// Runs in a disposable browser context against a temporary loopback server, not your saved workspace.
// DuckDB network requests are blocked; this suite tests persistence and recovery, not data execution.
import { chromium } from 'playwright';
import { createDataForgeServer } from '../scripts/serve.mjs';
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
const root=join(dirname(fileURLToPath(import.meta.url)),'..');
const artifacts=process.env.PERSISTENCE_ARTIFACTS || join(root,'persistence-test-artifacts');
mkdirSync(artifacts,{recursive:true});
const results=[];const expect=(value,message)=>{if(!value)throw new Error(message);};
const server=createDataForgeServer({root});
await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve);});
const origin=`http://127.0.0.1:${server.address().port}/`;
let browser,context;
async function check(name,fn){try{await fn();results.push({name,status:'PASS'});console.log('PASS '+name);}catch(e){results.push({name,status:'FAIL',error:e.message});throw e;}}
async function ready(page){await page.waitForFunction(()=>window.DataForge?.Business.ready && DataForge.Repo.db);await page.evaluate(()=>DataForge.Repo.flush());}
async function open(){const p=await context.newPage();p.setDefaultTimeout(10000);await p.goto(origin);await ready(p);return p;}
try{
 browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{})});
 context=await browser.newContext({viewport:{width:1440,height:1000}});
 await context.route('https://**/*',route=>route.abort());
 const p=await open();
 await check('Real IndexedDB commits a revision',async()=>expect(await p.evaluate(async()=>{const d=DataForge,r=await d.Repo.get('project','current');return d.Repo.mode==='indexeddb'&&r.revision===d.Repo._expectedRecord&&d.Business.saveState==='saved';}),'No committed revision'));
 await check('Project logic survives an actual reload',async()=>{
  await p.evaluate(async()=>{const d=DataForge;d.Repo.acceptMapping(d.Sample.mapping());d.Repo.project.project.name='Persistence fixture';await d.Repo.flush();});
  await p.reload();await ready(p);expect(await p.evaluate(()=>DataForge.Repo.project.project.name==='Persistence fixture'&&DataForge.Repo.project.mappings.length===1),'Reload lost project');
 });
 let point;
 await check('Logic checkpoint and remembered source remain separate',async()=>{
  point=await p.evaluate(async()=>{const d=DataForge;await d.Repo.put('files',{id:'test-source',bytes:new TextEncoder().encode('SOURCE_FIXTURE'),size:14});const c=await d.Business.checkpoint({force:true});return {id:c.id,json:c.json};});
  expect(!point.json.includes('SOURCE_FIXTURE'),'Source data in logic checkpoint');
  expect(await p.evaluate(async()=>!!(await DataForge.Repo.get('files','test-source'))),'Source not stored');
 });
 await check('Confirmed restore recovers the earlier project logic',async()=>{
  await p.evaluate(async()=>{DataForge.Repo.project.project.name='Changed';await DataForge.Repo.flush();});
  await p.evaluate(id=>{window.restoreResult=DataForge.Business.restore(id);},point.id);
  await p.locator('#dialogOverlay').getByRole('button',{name:'Restore logic',exact:true}).click();
  expect(await p.evaluate(()=>window.restoreResult),'Restore failed');
  expect(await p.evaluate(()=>DataForge.Repo.project.project.name==='Persistence fixture'&&!DataForge.Business.schedulerArmed),'Wrong restored logic or armed scheduler');
  await p.locator('#generalModalClose').click();
 });
 await check('Checkpoint corruption is rejected before changing the project',async()=>{
  expect(await p.evaluate(async id=>{const d=DataForge,c=await d.Repo.get('project',id);c.json+=' ';await d.Repo.put('project',c);const name=d.Repo.project.project.name;return !(await d.Business.restore(id))&&d.Repo.project.project.name===name;},point.id),'Corrupt checkpoint restored');
 });
 const other=await open();
 await check('Second tab detects another tab committing newer work',async()=>{
  await p.evaluate(async()=>{DataForge.Repo.project.project.name='Newer tab';await DataForge.Repo.flush();});
  await other.waitForFunction(()=>DataForge.Repo.stale);
  expect(await other.locator('#businessConflict').isVisible(),'Conflict not visible');
 });
 await check('A stale tab cannot overwrite the project',async()=>{
  expect(await other.evaluate(async()=>{DataForge.Repo.project.project.name='Stale tab';return !(await DataForge.Repo.flush());}),'Stale overwrite accepted');
  expect(await p.evaluate(async()=>(await DataForge.Repo.get('project','current')).document.project.name==='Newer tab'),'Newer project lost');
 });
 await other.reload();await ready(other);
 await check('Revision conflict remains blocked without BroadcastChannel',async()=>{
  await other.evaluate(()=>{DataForge.Repo.channel?.close();DataForge.Repo.channel=null;});
  await p.evaluate(async()=>{DataForge.Repo.project.project.name='Latest without channel';await DataForge.Repo.flush();});
  expect(await other.evaluate(async()=>{DataForge.Repo.project.project.name='Stale without channel';return !(await DataForge.Repo.flush())&&DataForge.Repo.stale;}),'No-channel conflict was not blocked');
 });
 await check('A stale tab cannot modify source-file records',async()=>expect(await other.evaluate(async()=>{try{await DataForge.Repo.put('files',{id:'forbidden'});return false;}catch(_){return true;}}),'Stale source write accepted'));
 await check('Reloaded project retains the committed winning revision',async()=>{
  await other.reload();await ready(other);expect(await other.evaluate(()=>!DataForge.Repo.stale&&DataForge.Repo.project.project.name==='Latest without channel'),'Winning project not reloaded');
 });
}catch(error){console.error(error.stack);process.exitCode=1;if(!results.some(x=>x.status==='FAIL'))results.push({name:'Browser setup or navigation',status:'FAIL',error:error.message});}
finally{await context?.close();await browser?.close();await new Promise(resolve=>server.close(resolve));writeFileSync(join(artifacts,'results.json'),JSON.stringify({environment:'real-origin IndexedDB; external engine blocked',results},null,2));}
