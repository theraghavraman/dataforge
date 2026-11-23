# DataForge completion summary

**Delivered: DataForge Team 2.1.0-rc2 - locally implemented and tested business release candidate.**

rc2 is a corrective re-qualification of rc1. On a clean install with the patched FastAPI, a dependency audit and a working browser-origin gate, it fixes six defects:

- a dependency bound that forced a vulnerable `cryptography`
- a reverse-proxy configuration that let about 60 sign-ins lock out the whole organization
- successful sign-ins consuming the brute-force budget
- a preflight crash on malformed qualification files
- an unclear sandbox resource-limit error
- a designer test bound to one host's browser path

See `CHANGELOG.md`. The sections below describe the full release; the original rc1 notes are retained where still accurate.

The new ZIP replaces the earlier reference package. It includes the complete application source, preserved designer, shared backend, execution worker, maintenance service, example data/mapping, tests/evidence, deployment templates and operating guides. No configured accounts, runtime database or private keys are included.

## Completed in this environment

Added MFA and single-use recovery codes; factor-preserving email reset; explicit OIDC SSO support; real portable visual-pipeline execution on the server; typed CSV bindings and exact-revision/source lineage; second-owner approval controls; immutable paused pipeline schedules; lineage-preserving retry; encrypted notification queue with TLS SMTP/HTTPS adapters; scheduled encrypted backups and optional S3 adapter; maintenance/retention/alert controls; production startup gates; UI workflows; deployment and qualification helpers.

Found and fixed a larger-input worker pipe-transfer defect, then reran the same concurrent workload. Also tightened key-preserving restore, password/reset/SSO challenge invalidation, unknown-schema migration behavior, backup publication/backoff and removal of secret-bearing UI dialog content on close/sign-out.

## Verification

**rc2: 287 checks passed, none failed, blocked or skipped**, in one `scripts/qualify.py` run on a fresh virtual environment:

- 178 backend tests, including 13 new regression tests
- 10 actual HTTP checks
- 36 rendered team-interface checks
- 9 designer-bridge checks
- 40 inherited Node tests
- 5 bounded-concurrency checks
- 5 shipped launcher/example checks
- 4 genuine browser-origin checks

The same run's `pip check` and `pip-audit` passed. Local TLS API and SMTP tests are within the backend total. The concurrency experiment used 48 submissions, 24 unique jobs, two worker processes and 10,000 rows per job; all results matched expected totals. rc1's 270-check result is preserved under `evidence/rc1/`.

## What is not represented as complete

rc2 resolved rc1's two environment blocks: the patched FastAPI is installed and audited, and the browser-origin gate passes on a loopback origin. Neither replaces running the same qualification on your build host and at your real HTTPS origin. SSO and S3 were tested with a mocked provider and SDK stub, not an organization's live accounts. Public DNS/TLS/hosting, encrypted production storage, independent backup and alert delivery, and business/security sign-off require the actual deployment environment. Approval-policy changes are single-owner; this is documented in `docs/PIPELINES.md`.

The server supports **13 portable visual node types using SQLite**, not every native DuckDB feature. Unsupported paths are rejected explicitly. Browser DuckDB runtime binaries and full native execution remain unprovisioned/unverified. The architecture is single-host with documented limits, not a high-availability SaaS platform.

**Production startup is intentionally blocked until these qualification requirements are met.** This is not a claim that every desired enterprise feature, dependency and external system has been completed or certified.

Start with README.md for the local launcher and synthetic example. TEST_REPORT.md records exact methods and limits; BUSINESS_READINESS.md and deploy/README.md identify the remaining deployment gates.
