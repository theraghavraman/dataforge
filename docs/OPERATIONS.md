# Operations and recovery runbook

## Three services

Run the API, worker and maintenance processes under the same dedicated service identity and local data-directory configuration. `scripts/run_local.py` is for disposable loopback evaluation only. For deployment use the reviewed `deploy/dataforge-*.service` units or your equivalent supervisor. All service entrypoints enforce production gates when `DATAFORGE_ENV=production`.

The worker is independent of the browser. Leases fence stale workers; permissions and source integrity are rechecked. Cancelled or expired work cannot publish stale successful results. Crash recovery of read-only jobs is bounded to three attempts. Ordinary failures require deliberate resubmission/retry.

The separate maintenance process polls every 30 seconds and has a host file lock to prevent concurrent maintenance. It records a heartbeat, creates scheduled backups, applies output retention, cleans expired authentication state and dispatches up to 20 notifications per cycle. Notification delivery is leased and at-least-once, with up to six attempts and exponential backoff. A receiver can see a duplicate after a crash; webhook messages carry a stable idempotency key and SMTP messages a stable Message-ID. Terminal payloads are scrubbed; metadata is retained for 30 days.

## Monitoring

`/health/live` is a basic liveness probe; `/health/ready` checks the database and reports worker availability. Authenticated Operations shows job/worker/audit state. Delivery & readiness shows maintenance, backup state, notification outcomes and preflight failures. Health endpoints are not acceptance tests for a business pipeline.

Worker loss/recovery, failed execution, schedule errors and backup failures create deduplicated notifications to configured email recipients and/or an HTTPS webhook. Messages avoid SQL/source contents. Configure:

```text
DATAFORGE_ALERT_EMAILS=operations@your-company.example
DATAFORGE_ALERT_WEBHOOK=https://your-reviewed-receiver.example/dataforge
DATAFORGE_ALERT_WEBHOOK_TOKEN=private-receiver-token
```

Use only trusted operator-provisioned endpoints. SMTP certificate checks are mandatory; webhook requests do not follow redirects. The SMTP adapter was exercised over actual local verified TLS, not an external mailbox. The HTTPS webhook adapter requires real endpoint delivery qualification. If the maintenance service itself is down, it cannot notify you: independent host/service monitoring remains required.

The API trusts `X-Forwarded-For` only from the exact addresses in `DATAFORGE_TRUSTED_PROXIES`; it never trusts arbitrary forwarding headers. Set this to the reverse proxy's address (see `deploy/README.md`), or every user shares the proxy's per-IP sign-in bucket. Sign-in throttles count failed attempts only: 10 per account and 60 per client address per 15 minutes. Successful sign-ins no longer consume either budget. Keep reviewed edge rate limiting as an additional layer.

## Keys and backup setup

The application key encrypts MFA secrets, SSO verifier state and notification contents. It does **not** encrypt the live SQLite database, CSV BLOBs, SQL or job results. Use an encrypted host volume and the deployment's access controls for live data.

Provision two distinct 32-byte keys with mode 0600, outside the data directory. The application key can be created by `init` at `DATAFORGE_SECRET_KEY_FILE`; `keygen` creates a random 32-byte backup key without overwriting existing files:

```bash
python -m app.cli keygen --output /etc/dataforge/backup.key
```

Keep protected, separately recoverable copies of **both** keys. A backup archive does not include either key. Losing the application key can make enrolled factors and encrypted pending state unrecoverable even when a database backup decrypts successfully. The application refuses startup if encrypted stored data has no matching application key. There is no built-in automated key rotation; design and test any rotation/migration before changing a live key.

Configure scheduled encrypted backup creation:

```text
DATAFORGE_BACKUP_KEY_FILE=/etc/dataforge/backup.key
DATAFORGE_BACKUP_DIRECTORY=/var/backups/dataforge
DATAFORGE_BACKUP_INTERVAL_SECONDS=86400
DATAFORGE_BACKUP_RETENTION_DAYS=30
```

A consistent SQLite snapshot is compressed, AES-GCM encrypted, fsynced and atomically published under a unique filename. Archives and key files are not silently overwritten. The current helper has a **512 MiB database snapshot limit**. Uploaded source data, history and metadata all contribute to database size. Monitor growth and migrate the storage/backup architecture before exceeding this envelope. Retention alone does not guarantee that SQLite releases disk space.

A backup is marked completed only after local creation and any configured remote verification succeed. An upload failure retains the encrypted local archive, records failure, sends an alert and backs off an hour; it is not reported as an independent successful backup. Old matching local archives are pruned only after a successful cycle. Check disk usage, especially during prolonged remote outages.

## Optional S3 replication

Install `requirements-s3.txt` and configure bucket/prefix/region through an approved host role. The uploaded content is already application-encrypted; the adapter additionally requests S3 server-side AES256 or configured KMS encryption. It sends SHA-256 and verifies the returned object size/checksum using HEAD with checksum mode. IAM must permit those operations and any required KMS access. Configure bucket versioning, lifecycle retention, access policy and independent restore permissions in your account.

```text
DATAFORGE_BACKUP_S3_BUCKET=your-private-bucket
DATAFORGE_BACKUP_S3_PREFIX=dataforge
DATAFORGE_BACKUP_KMS_KEY=your-optional-kms-key
AWS_DEFAULT_REGION=your-region
```

S3 adapter behavior was tested with the real SDK's Stubber, not a live bucket. No off-host destination, account credential or external retention policy is configured in the archive. Do not equate local encryption with off-host disaster recovery.

## Restore drill

1. Obtain an archive and its separate backup key from the approved backup location. Also recover the original application key. Preserve the current live database and keys; do not overwrite them.
2. Restore to a destination that does not exist:

```bash
python -m app.cli restore --input /secure/backup.dfbackup --destination /var/lib/dataforge-restored --key-file /etc/dataforge/backup.key
```

3. Integrity, authentication tag, database checksum, SQLite quick/foreign-key checks and audit chain must pass. Restoration revokes sessions/reset/SSO state, clears pending delivery messages/workers, pauses schedules and marks interrupted queued/running jobs failed. Completed outputs and project history remain available.
4. Point a stopped/test service at the restored directory and **original** application key. Run `init` for schema migration, then `verify-audit`. Start an isolated evaluation origin. Sign in with a known account and its authenticator/recovery code, inspect project revisions and sources, execute a known pipeline and compare independent expected totals. Check roles and rejected access as well as successful access.
5. Record archive ID, key custody, recovery duration, recovered data point, source/expected result checks, approver and evidence. Only after acceptance stop the old services and switch configuration. Re-arm reviewed schedules deliberately. Never point two independently restored environments at the same external delivery destination without reviewing possible duplicate notifications.

Both successful re-execution after restore and MFA-preserving restoration were tested locally. Your own off-host restore, workload RPO/RTO and operator procedure must still be qualified.

## Retention and incident actions

`DATAFORGE_RESULT_RETENTION_DAYS` defaults to 90 (minimum 7). Maintenance removes old completed result payloads, retaining job metadata. It does not delete source files, project history, audit events or user accounts. Projects retain the latest 20 revisions; the live database needs capacity monitoring. There is no automatic database vacuum or compliance retention engine.

For a failed worker, preserve diagnostics, restart the supervised process and check lease recovery/idempotency. For an incorrect mapping, pause its schedules, revoke the affected approval, inspect the exact source/revision and create a corrected revision. For suspected account compromise, disable the account or revoke sessions, rotate credentials, review the audit and notifications, and perform an independently verified factor reset only when justified. For a lost application key, stop services and recover the key; do not generate an unrelated replacement and call the restore complete.
