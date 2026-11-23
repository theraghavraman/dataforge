# Architecture - 2.1.0-rc2

```text
Browser team UI / retained visual designer
        | HTTPS, session cookie, CSRF
Reviewed reverse proxy (no public backend port)
        | private local HTTP
FastAPI API ------------ local SQLite WAL database ------------ Worker
        |                        |                              |
        |                  Maintenance                   Disposable runner
        |                 /     |     \                  in-memory SQLite
        |        encrypted backups  notifications              CSV SELECT
        |               optional S3 / SMTP / HTTPS
Optional OIDC provider (exact configured issuer/subject, PKCE, local MFA)
```

The API, worker and maintenance service run under a dedicated trusted service identity on one host. SQLite WAL and immediate write transactions serialize mutations. The database stores accounts, memberships, sessions, projects, revisions, CSV BLOBs, jobs, schedules, audit events, worker heartbeats, factor secrets, recovery/reset state, explicit OIDC links, notification outbox entries and approval policy/records. Schema v2 is an additive upgrade from v1. Unknown schemas are rejected before DDL; known migration DDL and version updates are atomic.

## Responsibilities

`api.py`, `security.py` and `identity_routes.py` enforce authentication, active membership, CSRF, project conflicts and identity lifecycle. `oidc.py` validates external sign-in without treating email as account proof. `pipeline.py` compiles the declared portable subset and binds actual workspace CSVs. `worker.py` owns leases and final authorization checks. `runner.py` has no application database handle, receives explicit copied inputs through a pipe, executes restricted SQLite and returns a bounded result. Pipe transfer is a single background I/O operation while the parent checks leases/deadlines; SQL work remains in the child process, not that thread.

`maintenance.py` handles backups, monitor events, expiry and retention; `outbox.py` encrypts/leases/retries delivery. `backup.py` uses SQLite's consistent backup API plus AES-GCM and restores only into a fresh destination. `preflight.py` enforces explicit deployment gates. `web/team-bridge.js` retains revision-checked visual editing without silently copying every local browser file into shared server storage.

## Roles

An administrator can provision accounts and view instance operations. A workspace owner can manage membership, save/restore projects, upload data, submit/cancel jobs, manage schedules, set approval policy and approve another saver's revision. Editors can save/upload/run/cancel/manage schedules but not alter membership or approval policy. Viewers read the workspace's projects, sources and results. Membership remains required even for an administrator. These are workspace roles, not per-project or per-row data permissions.

## Consistency and failure model

Job and schedule submissions use scoped idempotency. Jobs carry immutable payloads; source bytes/checksums and compiled revision lineage are checked at execution. Expired read-only worker leases recover at most three attempts. Cancellation and permission/approval revocation are rechecked before publishing output. Paused-by-default schedules coalesce missed intervals rather than flooding the queue after downtime.

Notifications are at-least-once with stable message identifiers. Backups and messages are not on the critical API request path. The database, worker service and maintenance host are still single points of failure. A local backup and a local event chain are not independent disaster recovery or immutable audit facilities. Scale/HA requires a separate architecture and migration, not an environment-variable switch.
