# Security model and release boundaries

This derivative build is designed for a trusted organization's modest-data, **single-host** deployment. It is not a hardened public multi-tenant SaaS boundary, arbitrary-code sandbox, compliance certification or independently penetration-tested release.

## Enforced application controls

- API-checked active accounts and workspace owner/editor/viewer membership; no browser-only authorization. Editor permissions include separate SQL submission. Optional revision approval governs visual pipeline jobs, not that independent capability.
- Password sign-in is throttled per account and per client address on failed attempts only; successful sign-ins do not consume the budget. Recovery, MFA changes, password change and SSO start count every request. Behind a reverse proxy the client address comes from `X-Forwarded-For` only when the proxy's exact address is listed in `DATAFORGE_TRUSTED_PROXIES` (production preflight requires it).
- Argon2id passwords; session tokens hashed in SQLite; CSRF and origin checks; HttpOnly/SameSite cookies; HTTPS Secure cookies; bounded sessions; TOTP with replay prevention; hashed one-use recovery codes; factor-preserving password recovery.
- Explicit OIDC issuer/subject linkage, TLS/PKCE/state/nonce/claim checks and no email-claim account linking. Existing local factors are not bypassed by SSO.
- Size/shape/checksum validation, optimistic project revisions, duplicate-submission protection, source/workspace access checks and restricted SQL execution.
- Query work occurs in a fresh in-memory SQLite subprocess. Writes, attachments, extension loading, protected SQLite metadata and file-access functions are denied. Child CPU/address-space/file-size/descriptor limits apply on the tested Linux platform; parent deadlines/cancellation/leases are independently enforced. The runner now uses a single asynchronous pipe communication operation to avoid losing partially transmitted large input after a poll timeout.
- CSP with approved scripts, no arbitrary static file exposure, defensive headers, bounded request body/query/header counts. Authenticated browser designer execution requires reviewed same-origin runtime files.
- AES-GCM protection for secret envelopes and whole-database backup archives; separate keys; safe-new-destination restoration with revoked state and paused schedules.

## Known dependency gate

The local QA image contains FastAPI 0.128.2. The upstream advisory **GHSA-f56c-v883-vx8r**, published 17 November 2026, covers versions >=0.123.2 and <0.163.2. The deployment requirements therefore require >=0.163.2, and production preflight explicitly refuses older versions. Request metadata limits provide defense in depth but are not treated as a substitute for upgrading. This release was not executed on the patched FastAPI build because the package could not be downloaded here. A clean install, full dependency audit and complete regression run on the resolved production environment are mandatory.

No full vulnerability scan was completed: `pip_audit` is absent in this environment. The old dependency snapshots under evidence describe what was tested, not safe production requirements.

## Non-guarantees

The active database and ordinary source/output payloads are not encrypted by the application. Require encrypted host storage, private service accounts, proper permissions and separate key custody. All worker/API/maintenance services share a trusted operator boundary; a host administrator or compromised service identity can access database contents and keys. SQLite process restrictions are not a general OS sandbox or proof against an SQLite/native-library vulnerability. Workspace membership is not per-project ACLs. There is no SSO-only enforcement, invitation/email verification flow, MFA push, passkey support, distributed queue, database cluster or HA failover.

The activity chain detects accidental or unsophisticated modification when verified, but it is not externally anchored or immutable: a privileged database operator can rewrite and recompute it. Use an external independently controlled audit sink when required. Notifications are operational records, not an immutable compliance log.

SMTP, SSO and webhook URLs are privileged operator configuration, not arbitrary user input. Live identity-provider, external mail, webhook and cloud backup integration were not available for qualification. Tests of a mocked provider or SDK are not proof of actual provider delivery. The SMTP transport itself was tested with a real local TLS endpoint and rejection of an untrusted certificate.

Genuine browser-origin navigation remains blocked in the QA browser by `ERR_BLOCKED_BY_ADMINISTRATOR`. DOM/API-bridge tests do not prove cookie, redirect, CSP or browser storage behavior at the real origin. No bypass of that browser policy was attempted. Local TLS HTTP tests are complementary, not a substitute for a real browser gate.

## Reporting and change control

Report defects through your organization's private security channel. Preserve minimal sanitized diagnostic evidence; do not include CSV contents, tokens, password/reset codes, application/backup keys or live credentials in a public issue. This package creates no external issue tracker, subscription or managed monitoring service.

`DATAFORGE_ENV=production` fails closed until patched dependencies, HTTPS, mandatory MFA, external keys, backups/alerts and a documented qualification file pass the configured checks. The qualification file is an operator-controlled approval record, not a cryptographic certification; protect it and require real evidence rather than changing statuses to bypass an incomplete review.
