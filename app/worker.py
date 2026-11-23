"""Durable single-host queue. Leases fence stale workers; SQL jobs are read-only.

Run as a separate supervised process: python -m app.cli worker
"""
import base64
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import json
import logging
import os
import secrets
import signal
import subprocess
import sys
import time
from pathlib import Path
from fastapi import HTTPException
from .db import transaction, audit, canonical
from .security import actor_can_run
from .validation import validate_job

LOG = logging.getLogger('dataforge.worker')

class Worker:
    def __init__(self, settings):
        self.settings = settings
        self.id = secrets.token_hex(16)
        self.stopping = False

    def tick(self):
        from .api import enqueue
        now = time.time()
        with transaction(self.settings, True) as db:
            db.execute('INSERT INTO workers VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET heartbeat=excluded.heartbeat,state=excluded.state', (self.id, now, 'idle'))
            db.execute('DELETE FROM workers WHERE heartbeat<?', (now - 86400,))
            # Expired leases are only retried for these read-only jobs, with a bounded budget.
            for job in db.execute("SELECT * FROM jobs WHERE status='running' AND lease_until<?", (now,)).fetchall():
                status = 'cancelled' if job['cancel_requested'] else 'queued' if job['attempts'] < 3 else 'failed'
                db.execute('UPDATE jobs SET status=?,lease_owner=NULL,lease_until=NULL,error=?,finished=? WHERE id=?',
                           (status, 'Worker lease expired; recovery applied.', now if status != 'queued' else None, job['id']))
                audit(db, None, job['workspace_id'], 'job.lease_recovered', job['id'], {'next_status': status})
            for schedule in db.execute('SELECT * FROM schedules WHERE enabled=1 AND next_at<=? ORDER BY next_at LIMIT 100', (now,)).fetchall():
                if not actor_can_run(db, schedule['actor'], schedule['workspace_id']):
                    db.execute('UPDATE schedules SET enabled=0 WHERE id=?', (schedule['id'],))
                    audit(db, None, schedule['workspace_id'], 'schedule.permission_revoked', schedule['id'])
                    continue
                try:
                    payload = json.loads(schedule['payload']); validate_job(db, schedule['workspace_id'], payload, enforce_approval=True)
                    key = f"schedule:{schedule['id']}:{schedule['next_at']:.6f}"
                    enqueue(db, schedule['workspace_id'], schedule['actor'], payload, key)
                    # Coalesce missed intervals rather than flooding the queue after downtime.
                    db.execute('UPDATE schedules SET next_at=? WHERE id=?', (now+schedule['interval_seconds'], schedule['id']))
                except HTTPException as exc:
                    db.execute('UPDATE schedules SET enabled=0 WHERE id=?', (schedule['id'],))
                    audit(db, None, schedule['workspace_id'], 'schedule.paused_on_error', schedule['id'], {'status_code': exc.status_code})

    def claim(self):
        with transaction(self.settings, True) as db:
            job = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
            if not job: return None
            if not actor_can_run(db, job['actor'], job['workspace_id']):
                db.execute("UPDATE jobs SET status='failed',finished=?,error=? WHERE id=?", (time.time(), 'Execution permission was revoked before start.', job['id']))
                audit(db, None, job['workspace_id'], 'job.permission_revoked', job['id']); return None
            now = time.time()
            db.execute("UPDATE jobs SET status='running',attempts=attempts+1,started=?,lease_owner=?,lease_until=?,error=NULL WHERE id=? AND status='queued'", (now, self.id, now+self.settings.lease_seconds, job['id']))
            db.execute("UPDATE workers SET heartbeat=?,state='running' WHERE id=?", (now, self.id))
            audit(db, None, job['workspace_id'], 'job.started', job['id'], {'attempt': job['attempts']+1})
            return dict(db.execute('SELECT * FROM jobs WHERE id=?', (job['id'],)).fetchone())

    def finish(self, job, status, result=None, error=None):
        with transaction(self.settings, True) as db:
            row = db.execute('SELECT status,lease_owner,cancel_requested FROM jobs WHERE id=?', (job['id'],)).fetchone()
            if not row or row['status'] != 'running' or row['lease_owner'] != self.id: return False
            if row['cancel_requested']: status, result, error = 'cancelled', None, 'Cancelled by a workspace editor.'
            elif not actor_can_run(db, job['actor'], job['workspace_id']): status, result, error = 'failed', None, 'Execution permission was revoked before completion.'
            if status == 'succeeded':
                try: validate_job(db, job['workspace_id'], json.loads(job['payload']), enforce_approval=True)
                except HTTPException:
                    status, result, error = 'failed', None, 'Source integrity or pipeline approval changed before completion.'
            changed = db.execute('UPDATE jobs SET status=?,result=?,error=?,finished=?,lease_owner=NULL,lease_until=NULL WHERE id=? AND status=\'running\' AND lease_owner=?',
                                 (status, canonical(result) if result is not None else None, error, time.time(), job['id'], self.id)).rowcount
            if changed: audit(db, None, job['workspace_id'], 'job.'+status, job['id'], {'rows': result['row_count']} if result else {})
            db.execute("UPDATE workers SET state='idle',heartbeat=? WHERE id=?", (time.time(), self.id))
            return bool(changed)

    def execute(self, job):
        payload = json.loads(job['payload'])
        try:
            with transaction(self.settings) as db:
                validate_job(db, job['workspace_id'], payload, enforce_approval=True)
                sources = []
                for item in payload['sources']:
                    data = db.execute('SELECT content FROM files WHERE id=? AND workspace_id=?', (item['file_id'], job['workspace_id'])).fetchone()[0]
                    sources.append({'table': item['table'], 'content_base64': base64.b64encode(data).decode(), 'types': item.get('types', {})})
            body = json.dumps({'sql': payload['sql'], 'sources': sources, 'timeout': self.settings.job_timeout,
                               'max_rows': self.settings.max_result_rows}).encode()
            process = subprocess.Popen([sys.executable, '-I', '-B', str(Path(__file__).with_name('runner.py'))],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       env={'PATH': os.defpath, 'LANG': 'C.UTF-8'}, start_new_session=True)
            deadline = time.monotonic()+self.settings.job_timeout
            # One complete communicate call owns all pipes. Repeated timed calls
            # can leave a partially sent stdin body stranded after a timeout.
            io_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dataforge-runner-io")
            communication = io_pool.submit(process.communicate, body)
            last_heartbeat = 0
            try:
                while True:
                    try:
                        stdout, stderr = communication.result(timeout=0.2)
                        break
                    except FutureTimeout:
                        if self.stopping: process.kill(); communication.result(timeout=5); self.finish(job, 'failed', error='Worker stopped during execution; resubmit the read-only job.'); return
                        now = time.time()
                        with transaction(self.settings, True) as db:
                            row = db.execute('SELECT cancel_requested,lease_owner,status FROM jobs WHERE id=?', (job['id'],)).fetchone()
                            permission = actor_can_run(db, job['actor'], job['workspace_id'])
                            if not row or row['lease_owner'] != self.id or row['status'] != 'running': process.kill(); communication.result(timeout=5); return
                            if now-last_heartbeat > 5:
                                db.execute('UPDATE jobs SET lease_until=? WHERE id=? AND lease_owner=?', (now+self.settings.lease_seconds, job['id'], self.id))
                                db.execute('UPDATE workers SET heartbeat=? WHERE id=?', (now, self.id)); last_heartbeat = now
                        if row['cancel_requested'] or not permission:
                            process.kill(); communication.result(timeout=5); self.finish(job, 'cancelled' if row['cancel_requested'] else 'failed', error='Cancelled or execution permission revoked.'); return
                        if time.monotonic() > deadline:
                            process.kill(); communication.result(timeout=5); self.finish(job, 'failed', error='Execution exceeded the configured wall-clock timeout.'); return
                if len(stdout) > 6*1024*1024: raise ValueError('Worker response exceeds allowed size.')
                try: result = json.loads(stdout)
                except ValueError: result = None
                if not isinstance(result, dict):
                    # A runner killed by its CPU/memory limits leaves no JSON report.
                    self.finish(job, 'failed', error=f'Restricted runner stopped without a result (exit code {process.returncode}); the query likely exceeded a CPU or memory limit.')
                    return
                if process.returncode == 0 and result.get('ok'):
                    self.finish(job, 'succeeded', result=result['result'])
                else:
                    self.finish(job, 'failed', error=result.get('error', 'Restricted query execution failed.'))
            finally:
                if process.poll() is None: process.kill()
                try: communication.result(timeout=5)
                finally: io_pool.shutdown(wait=True)
        except Exception as exc:
            # No source content, SQL, environment variables or stack traces are exposed in shared logs.
            self.finish(job, 'failed', error='Execution failed: '+(str(exc)[:200] if isinstance(exc, (ValueError, HTTPException)) else type(exc).__name__))

    def once(self):
        self.tick(); job = self.claim()
        if job: self.execute(job)
        return bool(job)

    def run(self):
        def stop(*_): self.stopping = True
        signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
        while not self.stopping:
            try:
                if not self.once(): time.sleep(1)
            except Exception:
                LOG.exception('Worker loop failed; no query payload is logged.')
                time.sleep(2)
        with transaction(self.settings, True) as db:
            db.execute("UPDATE workers SET state='stopped',heartbeat=0 WHERE id=?", (self.id,))
