# Business-readiness assessment - 2.1.0-rc2

## Release decision

**Implemented and locally tested single-host business release candidate. Not yet approved for public/production deployment.** The application has operational identity, storage, worker, recovery and maintenance features; production readiness also depends on its actual runtime, host, browser origin, identity/mail/backup providers and accountable acceptance evidence. The software deliberately blocks production startup when configured gates are unmet.

## Completed application scope

| Capability | Delivered implementation | Evidence level |
| --- | --- | --- |
| Team access | Active accounts, workspace roles, session/CSRF enforcement, account/session revocation | Real API and independent HTTP tests |
| MFA/recovery | TOTP, replay rejection, one-use hashed recovery codes, encrypted secret/outbox state, expiring reset tokens that preserve MFA | Automated API/crypto tests, rendered UI; actual local SMTP TLS transport |
| SSO | OIDC code+PKCE, RS256 verification, state/nonce/issuer/audience checks, explicit issuer/subject account links, local MFA after SSO | Deterministic provider transport with real RSA-signed tokens; no live IdP |
| Shared projects | Server storage, save conflicts, latest 20 revisions, source checksums and workspace isolation | Real API/storage/concurrency tests |
| Visual server execution | 13-node portable SQLite compiler, explicit typed CSV bindings, review UI, revision/source lineage and optional second-owner approvals | Actual runner subprocess and expected-total assertions; 18 new UI checks |
| Background jobs/schedules | Durable queue, duplicate suppression, cancellation, lease recovery, permission/approval rechecks, paused immutable pipeline schedules | Real worker processes and bounded concurrency experiment |
| Recovery | Consistent AES-GCM backups, atomic archive publish, safe-new-directory restore, revoked state, required original application key for encrypted data | Actual backup, corruption rejection, restoration and re-execution tests |
| Operations | Independent maintenance, heartbeat, alert events, retrying encrypted delivery outbox, output retention and readiness UI | Local process/service tests; transport/adapter evidence separated below |
| Deployment materials | Local three-process launcher, qualification helper, systemd/Caddy/Docker templates, migration/security/operations runbooks and API schema | Scripts syntax-checked; Compose parsed; live deployment templates not activated |

## Still requires your deployment environment

**Patched runtime and dependency audit (done in rc2 QA; repeat on your build host).** rc1's QA image ran FastAPI 0.128.2 (affected by GHSA-f56c-v883-vx8r) and could not run an audit. rc2 was qualified on a clean install from PyPI: FastAPI 0.165.0, the full suite passing, and `pip-audit` reporting no known vulnerabilities. That audit surfaced a defect in rc1 itself: its `cryptography<48` bound forced a release with published advisories. rc2 requires `cryptography>=50.0.0,<51`. Results apply to that environment and date. Resolve, audit, lock and retest on your own build platform.

**Actual browser origin (passed in rc2 QA; repeat at your real origin).** rc1's environment blocked Chromium navigation, so the gate was BLOCKED. In rc2 qualification, ordinary Chromium loaded the running application over loopback HTTP without request interception, and login, reload persistence and sign-out passed. This exercises real browser cookie, CSP and origin behavior on a local origin. It is not a test of your public HTTPS hostname, certificate or proxy, which you must still run after activation.

**Reverse-proxy client addresses (new requirement).** Set `DATAFORGE_TRUSTED_PROXIES` to the proxy's exact address. Without it, rc1's default deployment let about sixty sign-ins per fifteen minutes lock out the entire organization. Production preflight now enforces the setting.

**Real providers and independent recovery.** Configure and test your IdP/client registration, outbound SMTP or webhook receiver, and off-host backup account. Local SMTP TLS was exercised; SSO used a mock provider transport and S3 used the real SDK's Stubber. Neither configures a real tenant/bucket. The remaining tests need your provider credentials/permissions and cannot be truthfully completed against nonexistent accounts.

**Host operations and acceptance.** DNS, actual public certificates, firewall, host patching, encrypted live storage, service accounts, separate key custody, independent uptime monitoring, off-host restore RPO/RTO, real business-data correctness, capacity and independent security review remain operator tasks. The archive provisions none of these external resources. The qualification file must reference reviewed evidence and named approvals, not blanket automatic approvals.

## Deliberate product boundaries

The server execution contract is read-only SQLite over modest CSV inputs, with a declared visual subset. It is not full DuckDB compatibility. Browser DuckDB runtime/extension files remain unprovisioned and unverified. Unsupported native visual features are rejected rather than represented as completed. Refer to `docs/PIPELINES.md` before choosing a business workload.

Single-host SQLite, workspace-level permissions, a local non-immutable audit chain, 512 MiB backup-tool limit and bounded source/result sizes remain architectural constraints. This is not multi-node HA, a data warehouse, arbitrary-code hosting, per-project/row-level security, or a general ETL connector platform. A production deployment can only be approved within this declared scope.

## Go-live gate

Run `python -m app.cli preflight` with actual production configuration. It requires patched FastAPI, HTTPS, mandatory MFA, a trusted reverse-proxy address, Linux/SQLite compatibility, separate protected external keys, backup/alert configuration and a protected qualification record for browser origin, dependency audit, restore drill, security review, business acceptance, capacity, off-host backup, alert delivery and live-storage encryption. `DATAFORGE_ENV=production` refuses API/worker/maintenance startup if any check fails.

No statement in this document converts local passing tests into independent security or compliance certification.
