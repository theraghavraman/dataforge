"""Chromium UI + real in-process API bridge, not a network-origin E2E test.

The execution environment blocks browser URL navigation. The DOM is loaded with
set_content and fetch is adapted to FastAPI TestClient. Screenshots are from this
actual rendered application. Browser cookies/CSP/origin rules are NOT tested here.
"""
import base64
import json
from pathlib import Path
import re
import secrets
import tempfile
import time
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright
from app.api import create_app
from app.config import Settings
from app.db import initialize, transaction
from app.security import create_user
from app.worker import Worker
from tests.browser_support import launch_browser

ROOT=Path(__file__).resolve().parent.parent
OUT=ROOT/'evidence'/'ui';OUT.mkdir(parents=True,exist_ok=True)
results=[]
def check(name,fn):
    try:
        fn();results.append({'name':name,'status':'PASS'});print('PASS '+name,flush=True)
    except Exception as e:
        results.append({'name':name,'status':'FAIL','error':str(e)});print('FAIL '+name+': '+str(e),flush=True);raise

def expect(value,message):
    if not value:raise AssertionError(message)

def run():
 with tempfile.TemporaryDirectory(prefix='df-ui-') as d:
    settings=Settings(Path(d)/'data');initialize(settings);password=secrets.token_urlsafe(24)
    with transaction(settings,True) as db:
        admin_id=create_user(db,'qa-admin@example.test','Workspace Admin',password,True)
        viewer_id=create_user(db,'qa-viewer@example.test','Read-only Reviewer',password)
    app=create_app(settings);api=TestClient(app,base_url=settings.origin)
    signed=api.post('/api/auth/login',json={'email':'qa-admin@example.test','password':password}).json();api.headers['X-CSRF-Token']=signed['csrf']
    ws=api.post('/api/workspaces',json={'name':'Sample | Data Operations'}).json()['id'];root='/api/workspaces/'+ws
    api.put(root+'/members',json={'email':'qa-viewer@example.test','role':'viewer'})
    for name in ['Contributor quality review','Accepted hours reconciliation','Monthly reporting']:
        api.post(root+'/projects',json={'name':name,'document':{}})
    uploaded=api.post(root+'/files',json={'name':'quality_sample.csv','content_base64':base64.b64encode(b'region,hours,status\nNorth,10.5,accepted\nSouth,5,accepted\nNorth,2,rejected\nNorth,6,accepted\n').decode()}).json()
    sql="SELECT region,ROUND(SUM(CAST(hours AS REAL)),2) AS accepted_hours FROM data WHERE status='accepted' GROUP BY region ORDER BY region"
    for index,name in enumerate(['Accepted hours by region','Source record count','Quality acceptance check']):
        query=sql if index==0 else 'SELECT COUNT(*) AS records FROM data'
        api.post(root+'/jobs',json={'name':name,'sql':query,'sources':[{'file_id':uploaded['id'],'table':'data'}]},headers={'Idempotency-Key':'ui-seed-job-'+str(index)})
        Worker(settings).once()
    api.post(root+'/schedules',json={'name':'Daily quality summary','job':{'name':'Scheduled review','sql':sql,'sources':[{'file_id':uploaded['id'],'table':'data'}]},'interval_seconds':86400})
    api.post('/api/auth/logout');api.cookies.clear();api.headers.pop('X-CSRF-Token',None)
    with sync_playwright() as pw:
        browser=launch_browser(pw)
        context=browser.new_context(viewport={'width':1440,'height':1000},reduced_motion='reduce')
        page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.on('dialog',lambda dialog:dialog.accept())
        def bridge(source,url,options):
            method=options.get('method','GET');headers=options.get('headers',{})
            body=options.get('body')
            response=api.request(method,url,headers=headers,content=body)
            return {'status':response.status_code,'body':response.text,'headers':dict(response.headers)}
        page.expose_binding('testAPI',bridge)
        html=(ROOT/'web'/'index.html').read_text();html=re.sub(r'<link rel="stylesheet"[^>]*>','',html);html=re.sub(r'<script src="/assets/app.js" defer></script>','',html)
        page.set_content(html)
        page.add_style_tag(path=str(ROOT/'web'/'app.css'))
        page.evaluate('''() => {window.fetch=async(url,options={})=>{const r=await window.testAPI(String(url),options);return new Response(r.body,{status:r.status,headers:r.headers});};if(!crypto.randomUUID)crypto.randomUUID=()=>{const a=new Uint8Array(16);crypto.getRandomValues(a);return Array.from(a,x=>x.toString(16).padStart(2,'0')).join('');};}''')
        page.add_script_tag(path=str(ROOT/'web'/'app.js'))
        page.wait_for_selector('#loginForm')
        check('Login view renders without horizontal overflow',lambda:expect(page.evaluate('document.documentElement.scrollWidth <= innerWidth'),'Overflow'))
        page.screenshot(path=str(OUT/'login-desktop.png'),full_page=True)
        page.locator('#email').fill('qa-admin@example.test');page.locator('#password').fill('incorrect-password');page.get_by_role('button',name='Sign in',exact=False).click()
        page.wait_for_function("document.getElementById('loginError').textContent.includes('Incorrect')")
        check('Invalid credentials produce an inline generic error',lambda:expect(page.locator('#loginError').is_visible(),'Error not visible'))
        page.locator('#password').fill(password);page.get_by_role('button',name='Sign in',exact=False).click()
        page.get_by_role('heading',name='Workspace overview',exact=True).wait_for()
        check('Valid login loads real server workspace data',lambda:expect(page.locator('#workspace').input_value()==ws,'Wrong workspace'))
        check('Dashboard displays executed sample jobs',lambda:expect(page.get_by_text('Accepted hours by region',exact=True).is_visible(),'Seeded job missing'))
        page.screenshot(path=str(OUT/'overview-desktop.png'),full_page=True)
        page.get_by_role('button',name='Projects',exact=True).click();page.get_by_role('heading',name='Projects',exact=True).wait_for()
        page.get_by_role('button',name='New project',exact=True).click();page.get_by_label('Project name',exact=True).fill('UI-created project');page.get_by_role('button',name='Create project',exact=True).click()
        page.get_by_role('heading',name='UI-created project',exact=True).wait_for()
        check('Project creation form commits to the authenticated API',lambda:expect(any(p['name']=='UI-created project' for p in api.get(root+'/projects').json()),'Project not stored'))
        page.get_by_role('button',name='History',exact=True).first.click();page.get_by_role('heading',name='Project revision history').wait_for()
        check('Revision history dialog opens',lambda:expect(page.locator('#modalBody').get_by_text('1',exact=True).is_visible(),'Revision missing'))
        page.get_by_role('button',name='Close dialog').click()
        page.get_by_role('button',name='Data sources',exact=True).click();page.get_by_role('heading',name='Data sources',exact=True).wait_for()
        page.get_by_role('button',name='Upload CSV',exact=True).click();page.locator('input[type=file]').set_input_files({'name':'ui_uploaded.csv','mimeType':'text/csv','buffer':b'name,amount\nA,3\nB,7\n'})
        page.locator('#modal').get_by_role('button',name='Upload CSV',exact=True).click();page.get_by_text('ui_uploaded.csv',exact=True).wait_for()
        check('CSV upload is validated and persisted through the UI',lambda:expect(any(f['name']=='ui_uploaded.csv' for f in api.get(root+'/files').json()),'Uploaded file missing'))
        page.get_by_role('button',name='Jobs & results',exact=True).click();page.get_by_role('heading',name='Jobs & results',exact=True).wait_for()
        page.get_by_role('button',name='New SQL job',exact=True).click();page.get_by_label('Name',exact=True).fill('UI constant query');page.get_by_label('SQLite SELECT query').fill('SELECT 17 AS business_check;')
        page.get_by_role('button',name='Queue job',exact=True).click();page.get_by_text('UI constant query',exact=True).wait_for()
        check('Job form creates a durable queue entry',lambda:expect(any(j['name']=='UI constant query' and j['status']=='queued' for j in api.get(root+'/jobs').json()),'Job not queued'))
        Worker(settings).once();page.get_by_role('button',name='Refresh',exact=True).click();page.get_by_text('UI constant query',exact=True).wait_for()
        row=page.locator('tr').filter(has_text='UI constant query');row.get_by_role('button',name='Inspect',exact=True).click();page.get_by_role('heading',name='Execution details').wait_for()
        check('Job details display actual worker output',lambda:expect(page.locator('#modalBody').get_by_text('17',exact=True).is_visible(),'Actual result not displayed'))
        page.screenshot(path=str(OUT/'job-output-desktop.png'),full_page=True);page.get_by_role('button',name='Close dialog').click()
        page.get_by_role('button',name='Schedules',exact=True).click();page.get_by_role('heading',name='Schedules',exact=True).wait_for()
        check('Schedule status is explicitly paused',lambda:expect(page.get_by_text('paused',exact=True).is_visible(),'Paused state absent'))
        page.get_by_role('button',name='Team access',exact=True).click();page.get_by_role('heading',name='Team access',exact=True).wait_for()
        check('Role management lists provisioned users',lambda:expect(page.get_by_text('qa-viewer@example.test',exact=True).is_visible(),'Viewer missing'))
        page.get_by_role('button',name='Activity log',exact=True).click();page.get_by_role('heading',name='Activity log',exact=True).wait_for()
        check('Audit view displays real server events',lambda:expect(page.get_by_text('job.succeeded',exact=True).count()>0,'No job events'))
        page.get_by_role('button',name='Operations',exact=True).click();page.get_by_role('heading',name='Operations',exact=True).wait_for()
        check('Operations verifies the real audit chain',lambda:expect(page.get_by_text('Verified',exact=True).is_visible(),'Chain not verified'))
        page.screenshot(path=str(OUT/'operations-desktop.png'),full_page=True)
        page.get_by_role('button',name='Overview',exact=True).click();page.get_by_role('heading',name='Workspace overview',exact=True).wait_for()
        page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(150)
        check('Mobile overview fits a 390-pixel viewport',lambda:expect(page.evaluate('document.documentElement.scrollWidth <= innerWidth'),'Mobile overflow'))
        page.screenshot(path=str(OUT/'overview-phone.png'),full_page=True)
        page.get_by_role('button',name='Sign out',exact=True).click();page.wait_for_selector('#loginForm')
        check('Sign out clears rendered workspace data',lambda:expect(page.locator('#view').inner_text()=='','Data remained in DOM'))
        page.locator('#email').fill('qa-viewer@example.test');page.locator('#password').fill(password);page.get_by_role('button',name='Sign in',exact=False).click();page.get_by_role('heading',name='Workspace overview',exact=True).wait_for()
        check('Viewer navigation hides administrative actions',lambda:expect(page.get_by_role('button',name='Accounts',exact=True).count()==0 and page.get_by_role('button',name='Team access',exact=True).count()==0,'Forbidden navigation visible'))
        page.get_by_role('button',name='Jobs & results',exact=True).click();page.get_by_role('heading',name='Jobs & results',exact=True).wait_for()
        check('Viewer cannot open a job-submission form',lambda:expect(page.get_by_role('button',name='New SQL job',exact=True).count()==0,'Viewer has job control'))
        check('No uncaught JavaScript errors in tested team flows',lambda:expect(not errors,repr(errors)))
        browser.close()

if __name__=='__main__':
    try:run()
    finally:(OUT/'results.json').write_text(json.dumps({'method':'Chromium set_content + real FastAPI TestClient bridge; not network-origin E2E','results':results},indent=2))
