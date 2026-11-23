# Migration and rollback

## From 2.1.0-rc1 to rc2

No database migration is required; the schema version is still 2. Stop the three services and take an encrypted backup. Then:

1. Upgrade `pip`, then install the new `requirements.txt` into a fresh virtual environment. `cryptography` moves to 50.x.
2. Add `DATAFORGE_TRUSTED_PROXIES` to the private environment file. Use `127.0.0.1` for host Caddy, or the Docker bridge gateway for the compose template.
3. Remove `header_up -X-Forwarded-For` from your Caddyfile and reload Caddy.
4. Run `python -m app.cli preflight`. It now also checks `client_addresses`.
5. Start the services and confirm that two users signing in from different machines are throttled independently.


## From DataForge Team 2.0.0-rc1

Stop writes and take a verified whole-database backup before replacing code. Keep the previous code/runtime and all keys separately. Use a new reviewed virtual environment with the patched deployment dependencies; the old installed-version snapshots are not production requirements.

Schema v2 adds identity, outbox and approval tables without changing the existing project/job file formats. `python -m app.cli init` checks the version before DDL and performs the known additive migration in one transaction. Existing accounts, memberships, sources and jobs remain. No account is automatically enrolled in MFA or linked to an SSO provider. Enable mandatory MFA deliberately; users will be restricted to enrollment until they configure a factor.

Configure an external application key before adding factors or notifications. When upgrading an existing v2 database or restoring its backup, retain the original application key. Do not replace it just because the data directory changed. Backup and application keys have different roles and both must be recoverable.

Start on a private test origin with schedules paused. Verify access rejection, login/MFA, project conflicts, source checksums, a known worker query, maintenance/backup, alert delivery and recovery. Configure production qualification only after recording the real environment's evidence.

Do not run the old API/worker against the v2 database as a rollback. Stop services and restore the pre-upgrade database into a new directory with the matching prior code/runtime, validate, then switch paths. Keep a record of post-upgrade work that needs controlled replay; restoration is not automatic reconciliation.

## From a browser-only pilot

Export a project from the existing browser and retain an independent copy before changing origins. Browser storage is scoped by scheme/host/port and profile; switching from localhost to 127.0.0.1 or to HTTPS does not move it. Keep original export passwords where applicable.

Use the authenticated designer bridge to import/review and publish project logic. The API accepts a native `projectVersion: 1.0` document, not an encrypted export envelope or arbitrary bundle. Decode/import those in the original designer as appropriate, then publish reviewed logic. Uploaded source files and local run histories are not automatically shared. Upload approved CSV inputs separately and bind them explicitly in the server pipeline form.

The server execution subset is intentionally narrower than the retained DuckDB designer. Use `docs/PIPELINES.md` and the capabilities API. Do not call a pipeline migrated successfully merely because its graph saves: compile it, execute it, compare independent expected data and inspect rejects/nulls/types. Unsupported paths fail explicitly. Retain the original application and exports until business acceptance.
