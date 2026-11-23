# DataForge Team - business release candidate

**2.1.0-rc2 | 23 November 2026 | Single-host deployment**

A backend-enabled DataForge application with shared project storage, authenticated team workspaces, independently running jobs and schedules, a portable visual-pipeline compiler, MFA and optional OIDC sign-in, encrypted recovery tooling, and operational delivery services.

This is the completed locally implementable release from the supplied DataForge project. It is **not a production certification**. rc2 fixes defects found when rc1 was re-qualified on a clean install with the patched FastAPI, a dependency audit and a working browser-origin gate; see `CHANGELOG.md`. Production startup is deliberately blocked until documented deployment gates are satisfied. `TEST_REPORT.md` distinguishes actual execution, mocked integrations, and checks that only your deployment can perform.

## Start a local evaluation

Use Linux and Python 3.12 or 3.13 with SQLite >=3.39. The actual QA environment and versions are recorded in `evidence/qa-environment.json`. Use synthetic data until your deployment is qualified.

```bash
cd dataforge-team
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
export DATAFORGE_DATA_DIR="$PWD/var"
export DATAFORGE_ORIGIN="http://127.0.0.1:8080"
python -m app.cli init
python -m app.cli create-admin --email admin@your-company.example --name "Workspace Administrator"
python scripts/run_local.py
```

Open `http://127.0.0.1:8080`. The local launcher starts the API, worker and maintenance processes together and stops all three on Ctrl+C. The administrator command prompts for a password; there are **no default credentials**. The launcher does not create accounts or bypass permissions. Use port 8081 consistently when 8080 is already occupied: change `DATAFORGE_ORIGIN` and supply `--port 8081`.

`requirements.txt` is a deployment dependency policy, not a verified lockfile. Dependency installation requires an approved package index. It requires FastAPI >=0.163.2 (GHSA-f56c-v883-vx8r) and cryptography >=50.0.0 (several 2026 advisories). Upgrade `pip` itself first. Install, audit, freeze and retest on the intended deployment build host; do not use the old installed-version snapshots as deployment requirements.

Provision accounts in **Accounts**, create workspaces, and assign **owner/editor/viewer** roles in **Team access**. Enable MFA in **Account security**. Owners can grant membership to provisioned accounts; administrator status alone does not confer access to every workspace.

## Run the included visual example

With the local services running, load only synthetic example data through the real API:

```bash
python scripts/load_example.py --email admin@your-company.example --create-workspace "Synthetic evaluation"
```

The helper prompts for the password and optional factor and signs out after import. It does not store credentials. Open **Projects > Run pipeline**, verify the `orders.csv` source binding, select **Compile and review**, review the SQLite query, check the confirmation, then queue it. The expected output is **North = 130.0, South = 40.0**. The results are downloadable as CSV or JSON. A schedule interval creates a paused schedule; enable it only after reviewing its stored revision.

## What is implemented

| Area | Implementation |
| --- | --- |
| Identity | Argon2id passwords, hashed cookie sessions, CSRF checks, expiry/revocation, TOTP MFA, ten hashed single-use recovery codes, reset links delivered through an encrypted durable outbox |
| Optional SSO | OIDC authorization code + PKCE S256, RS256 signature validation, issuer/audience/nonce/state checks, explicit issuer/subject account links, local MFA preserved |
| Shared work | Workspace-scoped projects and CSV files, optimistic revision checks, 20 retained revisions, restoration as a new revision, source checksums |
| Execution | Restricted read-only SQLite subprocesses, queue idempotency, bounded crash recovery, cancellation, permission and approval rechecks, interval schedules independent of the browser |
| Visual server pipeline | Explicit CSV bindings, strict typed imports, 13 supported node types, SQL review, exact-revision lineage and optional second-owner approval |
| Recovery | AES-GCM database backups, independent application/backup keys, safe restoration to a new directory, restored sessions/tokens revoked, schedules paused |
| Operations | Separate maintenance service, bounded notification retries, SMTP with verified TLS, HTTPS webhooks, optional checksummed S3 backup upload, retention and readiness views |
| Deployment | Production preflight gates, systemd/Caddy/Docker templates, qualification helper, API schema, migration and incident runbooks |

## Execution boundary

**Server:** read-only SQLite over uploaded CSV files. The portable compiler supports CSV source, filter, expression, sorter, aggregator, joiner, union, rank, router, CSV/JSON/preview/reject targets. It rejects unsupported nodes on the selected path. Targets are stored job results, not arbitrary server filesystem writes. See `docs/PIPELINES.md` for data types, semantics and limits.

**Retained designer:** the original canvas and DuckDB-WASM engine remain under `studio/`. The canvas can edit and publish project logic. Browser DuckDB execution still needs reviewed same-origin runtime binaries; those binaries are not bundled, and that engine was not executed here. A portable mapping can run on the new server without DuckDB. **This compiler is not full DuckDB compatibility**: SCD, sequence generation, lookup, Excel/Parquet, general SQL-transform nodes, arbitrary workflow tasks and DuckDB-specific expressions are not converted.

## Verify and qualify

```bash
python -m pip install -r requirements-dev.txt
python -m playwright install chromium
python scripts/check_all.py
python scripts/check_all.py --origin
python scripts/qualify.py
```

`--origin` requires real Chromium navigation and cookie behavior; a blocked gate exits nonzero. The UI bridge tests are explicitly labeled and cannot substitute for that gate. The qualification helper runs `pip check`, a vulnerability audit and the full suite with the origin gate; it never approves human acceptance/security gates automatically.

## Operating and deployment documents

| File | Purpose |
| --- | --- |
| `TEST_REPORT.md` | Final check counts, methods, evidence and blocked gates |
| `BUSINESS_READINESS.md` | Implemented scope versus deployment responsibilities |
| `SECURITY.md` | Threat boundaries, credential handling and non-guarantees |
| `docs/IDENTITY.md` | MFA, password recovery, explicit OIDC linking and break-glass recovery |
| `docs/PIPELINES.md` | Compiler contract, source schemas, approvals and semantics |
| `docs/OPERATIONS.md` | Backups, restore drills, alerts, maintenance, retention and incidents |
| `docs/MIGRATION.md` | Upgrade from the prior team build or browser-only designer |
| `docs/DEPENDENCIES.md` | Patched dependency requirement and reproducible qualification |
| `deploy/README.md` | Single-host systemd, reverse proxy and container templates, including the required `DATAFORGE_TRUSTED_PROXIES` setting |
| `CHANGELOG.md` | Defects fixed in rc2 and how each was verified |
| `docs/openapi.json` | API schema generated from this release |

No runtime database, configured account, live integration credential, private TLS key, application encryption key or backup key is included. Prior documentation/evidence inside `studio/` describes historical browser-only builds; this root documentation governs the team release.
