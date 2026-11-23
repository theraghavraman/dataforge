"""Offline DOM integration of the legacy designer with the authenticated API.

Uses the real served designer script and bridge. Only location.search is injected
because navigation is blocked; DuckDB startup is marked unavailable, not simulated.
This test does not validate real-origin persistence or actual DuckDB execution.
"""
import json
import re
import secrets
import tempfile
from pathlib import Path
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright
from tests.browser_support import launch_browser
from app.api import create_app
from app.config import Settings
from app.db import initialize,transaction
from app.security import create_user

ROOT=Path(__file__).resolve().parent.parent
OUT=ROOT/'evidence'/'designer';OUT.mkdir(parents=True,exist_ok=True)
results=[]
def check(name,value):
    if not value:raise AssertionError(name)
    results.append({'name':name,'status':'PASS'});print('PASS '+name,flush=True)


def run():
 with tempfile.TemporaryDirectory() as d:
    settings=Settings(Path(d));initialize(settings);pw=secrets.token_urlsafe(24)
    with transaction(settings,True) as db:create_user(db,'designer@example.test','Designer QA',pw,True)
    api=TestClient(create_app(settings),base_url=settings.origin)
    session=api.post('/api/auth/login',json={'email':'designer@example.test','password':pw}).json();api.headers['X-CSRF-Token']=session['csrf']
    ws=api.post('/api/workspaces',json={'name':'Designer test'}).json()['id'];root='/api/workspaces/'+ws
    project=api.post(root+'/projects',json={'name':'Visual project QA','document':{}}).json();pid=project['id']
    with sync_playwright() as p:
        browser=launch_browser(p);page=browser.new_page(viewport={'width':1440,'height':1000});errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        def bridge(source,url,options):
            r=api.request(options.get('method','GET'),url,headers=options.get('headers',{}),content=options.get('body'))
            return {'status':r.status_code,'body':r.text,'headers':dict(r.headers)}
        page.expose_binding('testAPI',bridge)
        html=api.get('/studio?workspace='+ws+'&project='+pid).text
        html=html.replace('<script src="/assets/team-bridge.js"></script>','')
        page.set_content(html)
        page.evaluate('''()=>{window.fetch=async(url,options={})=>{const r=await testAPI(String(url),options);return new Response(r.body,{status:r.status,headers:r.headers});};DataForge.Engine.init=async function(){this.set('error','DuckDB runtime not provisioned');};}''')
        script=(ROOT/'web'/'team-bridge.js').read_text().replace('new URLSearchParams(location.search)', 'new URLSearchParams('+json.dumps('?workspace='+ws+'&project='+pid)+')')
        page.add_script_tag(content=script)
        page.wait_for_function('window.DataForgeTeamBridge && DataForge.Business.ready')
        check('Designer bootstraps the server project in session-only mode',page.evaluate("DataForge.Repo.project.project.name==='Visual project QA' && DataForge.Repo.db===null && DataForge.Repo.channel===null"))
        page.evaluate('DataForge.Repo.acceptMapping(DataForge.Sample.mapping())')
        page.wait_for_timeout(300)
        check('Existing six-node sample mapping is usable without replacing its format',page.evaluate('DataForge.state.doc.nodes.length===6'))
        page.get_by_role('button',name='Publish logic',exact=True).click();page.wait_for_function('DataForgeTeamBridge.revision===2')
        saved=api.get(root+'/projects/'+pid).json()
        check('Publishing stores actual original-format mapping on the server',len(saved['document']['mappings'][0]['nodes'])==6 and saved['revision']==2)
        check('Uploaded source store is not included in project revision payload',not any(key in saved['document'] for key in ['files','runs','sessionFiles']))
        # A separate API edit races this browser, which must keep the browser copy intact.
        r=api.put(root+'/projects/'+pid,json={'name':'Other editor won','document':saved['document'],'revision':2});check('Independent API editor can create the competing revision',r.status_code==200)
        page.evaluate("DataForge.Repo.project.project.name='Unpublished local changes'")
        page.get_by_role('button',name='Publish logic',exact=True).click();page.wait_for_function('DataForgeTeamBridge.stopped')
        check('Stale designer publish is blocked with recovery actions',page.get_by_role('button',name='Publish logic',exact=True).is_disabled() and page.get_by_role('button',name='Download recovery',exact=True).is_visible())
        check('Conflicting browser edits remain available for recovery',page.evaluate("DataForge.Repo.project.project.name==='Unpublished local changes'"))
        check('Server keeps the winning revision',api.get(root+'/projects/'+pid).json()['name']=='Other editor won')
        page.screenshot(path=str(OUT/'designer-publish-conflict.png'),full_page=True)
        check('No uncaught JavaScript errors in tested bridge flow',not errors)
        browser.close()

if __name__=='__main__':
    status='FAIL'
    try:
        run();status='PASS'
    finally:
        # Record an explicit overall status so a crash cannot read as a clean partial run.
        (OUT/'results.json').write_text(json.dumps({'method':'set_content + real API; injected location.search; unavailable DuckDB startup','status':status,'results':results},indent=2))
