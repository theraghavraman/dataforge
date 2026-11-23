import json
import re
import time
from fastapi import Request,HTTPException
from fastapi.responses import JSONResponse
from .db import transaction
from .security import session_user,require_member
from .validation import validate_job
from .preflight import check


def install_operations_routes(app,settings):
    @app.get('/api/admin/service-status')
    def service_status(request: Request):
        with transaction(settings,True) as db:
            u=session_user(db,request,settings)
            if not u['is_admin']:raise HTTPException(403,'Administrator role required.')
            states={r['key']:r['value'] for r in db.execute("SELECT key,value FROM meta WHERE key LIKE 'maintenance.%'")}
            outbox={r[0]:r[1] for r in db.execute('SELECT status,count(*) FROM outbox GROUP BY status')}
            heartbeat=float(states.get('maintenance.heartbeat',0))
            return {'maintenance_online':heartbeat>time.time()-180,'maintenance_heartbeat':heartbeat,
                    'last_backup':json.loads(states.get('maintenance.last_backup','null')),
                    'backup_error':states.get('maintenance.backup_error'),'notifications':outbox,
                    'preflight':check(settings)}

    @app.post('/api/workspaces/{ws}/jobs/{jid}/retry')
    def retry(ws: str,jid: str,request: Request):
        from .api import enqueue,public_job
        key=request.headers.get('idempotency-key','')
        if not re.fullmatch(r'[A-Za-z0-9_.:-]{8,120}',key):raise HTTPException(422,'Supply a valid Idempotency-Key.')
        with transaction(settings,True) as db:
            u=session_user(db,request,settings);require_member(db,u,ws,'editor')
            old=db.execute('SELECT * FROM jobs WHERE id=? AND workspace_id=?',(jid,ws)).fetchone()
            if not old:raise HTTPException(404,'Job not found.')
            if old['status'] not in ('failed','cancelled'):raise HTTPException(409,'Only failed or cancelled jobs can be retried.')
            payload=json.loads(old['payload']);validate_job(db,ws,payload,enforce_approval=True)
            row,created=enqueue(db,ws,u['user_id'],payload,key)
            return JSONResponse({'job':public_job(row),'created':created},201 if created else 200)
