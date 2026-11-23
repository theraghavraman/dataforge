"""Added UI flows using real TestClient API + rendered Chromium, not browser-origin proof."""
import base64
import json
import os
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
from app.identity import totp
from app.security import create_user
from app.worker import Worker
from tests.browser_support import launch_browser
from tests.test_pipeline_v2 import node

ROOT=Path(__file__).resolve().parent.parent
OUT=ROOT/'evidence'/'ui-v2';OUT.mkdir(parents=True,exist_ok=True)
results=[]
def check(name,value):
    results.append({'name':name,'status':'PASS' if value else 'FAIL'})
    print(('PASS ' if value else 'FAIL ')+name,flush=True)
    if not value:raise AssertionError(name)

def run():
  with tempfile.TemporaryDirectory(prefix='df-ui-v2-') as tmp:
    settings=Settings(Path(tmp)/'data');initialize(settings);password=secrets.token_urlsafe(24)
    with transaction(settings,True) as db:
        aid=create_user(db,'ui-admin@example.test','Operations administrator',password,True)
        eid=create_user(db,'ui-editor@example.test','Pipeline editor',password)
    app=create_app(settings);api=TestClient(app,base_url=settings.origin)
    login=api.post('/api/auth/login',json={'email':'ui-admin@example.test','password':password}).json();api.headers['X-CSRF-Token']=login['csrf']
    ws=api.post('/api/workspaces',json={'name':'Business operations'}).json()['id'];root='/api/workspaces/'+ws
    api.put(root+'/members',json={'email':'ui-editor@example.test','role':'editor'})
    content=b'region,amount,status\nNorth,100,accepted\nSouth,40,accepted\nNorth,30,accepted\nNorth,9,rejected\n'
    f=api.post(root+'/files',json={'name':'orders.csv','content_base64':base64.b64encode(content).decode()}).json()
    editor=TestClient(app,base_url=settings.origin);login=editor.post('/api/auth/login',json={'email':'ui-editor@example.test','password':password}).json();editor.headers['X-CSRF-Token']=login['csrf']
    nodes=[node('source','csv_source',{'fileName':'orders.csv'},[{'name':'region','type':'VARCHAR'},{'name':'amount','type':'DOUBLE'},{'name':'status','type':'VARCHAR'}]),node('accepted','filter',{'condition':"status='accepted'"}),node('totals','aggregator',{'groupBy':['region'],'fields':[{'name':'total','expression':'SUM(amount)'}]}),node('ordered','sorter',{'keys':[{'column':'region','direction':'ASC'}]}),node('out','csv_target')]
    doc={'projectVersion':'1.0','project':{'id':'demo','name':'Accepted revenue'},'mappings':[{'mapping':{'id':'m1','name':'Accepted revenue by region'},'nodes':nodes,'links':[{'from':a['id'],'to':b['id']} for a,b in zip(nodes,nodes[1:])]}],'workflows':[]}
    project=editor.post(root+'/projects',json={'name':'Accepted revenue','document':doc}).json()
    api.post('/api/auth/logout');api.cookies.clear();api.headers.pop('X-CSRF-Token',None)
    with sync_playwright() as pw:
        browser=launch_browser(pw);page=browser.new_page(viewport={'width':1440,'height':1040},reduced_motion='reduce');errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)));page.on('dialog',lambda d:d.accept())
        def bridge(source,url,options):
            response=api.request(options.get('method','GET'),url,headers=options.get('headers',{}),content=options.get('body'))
            return {'status':response.status_code,'body':response.text,'headers':dict(response.headers)}
        page.expose_binding('testAPI',bridge)
        html=(ROOT/'web/index.html').read_text();html=re.sub(r'<link rel="stylesheet"[^>]*>','',html);html=re.sub(r'<script src="/assets/app.js" defer></script>','',html)
        page.set_content(html);page.add_style_tag(path=str(ROOT/'web/app.css'))
        page.evaluate('''() => {window.fetch=async(url,options={})=>{const r=await window.testAPI(String(url),options);return new Response(r.body,{status:r.status,headers:r.headers});};if(!crypto.randomUUID)crypto.randomUUID=()=>Array.from(crypto.getRandomValues(new Uint8Array(16)),x=>x.toString(16).padStart(2,'0')).join('');}''')
        page.add_script_tag(path=str(ROOT/'web/app.js'));page.locator('#loginForm').wait_for()
        check('Optional second-factor input appears at login',page.locator('#otp').is_visible())
        check('SSO is hidden when no provider is configured',page.locator('#ssoLogin').is_hidden())
        page.locator('#email').fill('ui-admin@example.test');page.locator('#password').fill(password);page.get_by_role('button',name='Sign in',exact=False).click();page.get_by_role('heading',name='Workspace overview',exact=True).wait_for()
        page.get_by_role('button',name='Account security',exact=True).click();page.get_by_role('heading',name='Account security',exact=True).wait_for()
        check('Account security lists the authenticated session',page.get_by_text('This session',exact=True).is_visible())
        page.get_by_role('button',name='Set up two-step verification',exact=True).click();page.get_by_label('Current password',exact=True).fill(password);page.get_by_role('button',name='Create setup key',exact=True).click();page.locator('.secret-value').wait_for()
        secret=page.locator('.secret-value').inner_text();page.get_by_label('Six-digit authenticator code',exact=True).fill(totp(secret,int(time.time()//30)));page.get_by_role('button',name='Verify and enable',exact=True).click()
        page.get_by_role('heading',name='Save your recovery codes',exact=True).wait_for()
        codes=page.locator('#modalBody pre').inner_text().splitlines()
        check('MFA enrollment displays ten single-use recovery codes',len(codes)==10)
        with transaction(settings) as db:check('Rendered enrollment does not store the setup key as plaintext',secret not in db.execute('SELECT secret FROM mfa WHERE user_id=?',(aid,)).fetchone()[0])
        page.get_by_role('button',name='I have saved my codes',exact=True).click();page.get_by_role('heading',name='Account security',exact=True).wait_for();page.get_by_text('Enabled',exact=True).wait_for()
        page.screenshot(path=str(OUT/'security-desktop.png'),full_page=True)
        page.get_by_role('button',name='Sign out',exact=True).click();page.locator('#loginForm').wait_for();page.locator('#email').fill('ui-admin@example.test');page.locator('#password').fill(password);page.get_by_role('button',name='Sign in',exact=False).click();page.wait_for_function("document.getElementById('loginError').textContent.length>0")
        check('Password alone is rejected after enabling MFA',page.locator('#shell').is_hidden())
        page.locator('#otp').fill(codes[0]);page.get_by_role('button',name='Sign in',exact=False).click();page.get_by_role('heading',name='Account security',exact=True).wait_for()
        check('Recovery-code login consumes exactly one code',page.get_by_text('9 single-use recovery codes remaining.',exact=False).is_visible())
        page.get_by_role('button',name='Projects',exact=True).click();page.get_by_role('heading',name='Projects',exact=True).wait_for();page.get_by_role('button',name='Approvals',exact=True).click();page.get_by_role('heading',name='Project approval controls',exact=True).wait_for()
        page.get_by_label('Require another workspace owner to approve each revision').check();page.get_by_role('button',name='Save approval policy',exact=True).click();page.get_by_role('button',name='Approvals',exact=True).click();page.get_by_role('button',name='Approve this revision',exact=True).click();page.locator('#modal').wait_for(state='hidden')
        with transaction(settings) as db:check('A different owner approves the exact project revision',db.execute('SELECT actor FROM project_approvals WHERE project_id=?',(project['id'],)).fetchone()[0]==aid)
        page.get_by_role('button',name='Run pipeline',exact=True).click();page.get_by_role('heading',name='Run visual pipeline on server',exact=True).wait_for()
        check('Pipeline queue action stays disabled until review',page.get_by_role('button',name='Queue reviewed pipeline',exact=True).is_disabled())
        page.get_by_role('button',name='Compile and review',exact=True).click();page.get_by_role('heading',name='Compiled SQLite query',exact=True).wait_for()
        check('Actual compiler output is displayed for review','SUM(amount)' in page.locator('#modalBody pre').inner_text())
        page.screenshot(path=str(OUT/'pipeline-review-desktop.png'),full_page=True)
        page.get_by_label('I reviewed the SQL, source bindings, and SQLite semantics.').check();page.get_by_role('button',name='Queue reviewed pipeline',exact=True).click();page.get_by_role('heading',name='Jobs & results',exact=True).wait_for();Worker(settings).once()
        with transaction(settings) as db:
            job=dict(db.execute('SELECT * FROM jobs ORDER BY created DESC LIMIT 1').fetchone())
            check('Reviewed UI pipeline produces independently expected results',json.loads(job['result'])['rows']==[['North',130.0],['South',40.0]])
            check('UI submission preserves project revision lineage',json.loads(job['payload'])['pipeline']['revision']==1)
        page.get_by_role('button',name='Projects',exact=True).click();page.get_by_role('button',name='Run pipeline',exact=True).click();page.get_by_label('Schedule interval (minutes; leave blank for one run)').fill('60');page.get_by_role('button',name='Compile and review',exact=True).click();page.get_by_role('heading',name='Compiled SQLite query',exact=True).wait_for();page.get_by_label('I reviewed the SQL, source bindings, and SQLite semantics.').check();page.get_by_role('button',name='Queue reviewed pipeline',exact=True).click();page.get_by_role('heading',name='Schedules',exact=True).wait_for()
        check('Pipeline schedules are created paused through the UI',page.get_by_text('paused',exact=True).is_visible())
        with transaction(settings) as db:check('Scheduled payload retains an immutable compiled snapshot',json.loads(db.execute('SELECT payload FROM schedules').fetchone()[0])['pipeline']['revision']==1)
        page.get_by_role('button',name='Delivery & readiness',exact=True).click();page.get_by_role('heading',name='Delivery and readiness',exact=True).wait_for()
        check('Unqualified deployment is clearly marked as blocked',page.get_by_text('Production startup is blocked until every required gate is satisfied.',exact=True).is_visible())
        page.screenshot(path=str(OUT/'readiness-desktop.png'),full_page=True)
        page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(100)
        check('Readiness screen fits a phone viewport',page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
        page.screenshot(path=str(OUT/'readiness-phone.png'),full_page=True)
        page.get_by_role('button',name='Account security',exact=True).click();page.get_by_role('heading',name='Account security',exact=True).wait_for()
        check('Security screen fits a phone viewport',page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
        page.screenshot(path=str(OUT/'security-phone.png'),full_page=True)
        check('No uncaught JavaScript errors in added flows',not errors)
        browser.close()
    api.close();editor.close()
  (ROOT/'evidence/ui-v2-results.json').write_text(json.dumps({'method':'Chromium DOM rendering + real API TestClient bridge; not browser-origin validation','results':results},indent=2))

if __name__=='__main__':
    try:run()
    finally:
        (ROOT/'evidence/ui-v2-results.json').write_text(json.dumps({'method':'Chromium DOM rendering + real API TestClient bridge; not browser-origin validation','results':results},indent=2))
