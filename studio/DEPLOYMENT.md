# Deployment and recovery runbook

This runbook describes the supplied **single-user business pilot**, not a managed service. Use non-sensitive test data until BUSINESS_READINESS.md gates are satisfied.

## 1. Protect the existing workspace

Before replacing any build, export the complete project from the current app and retain a separate copy. Know its export password where applicable; there is no password recovery service. Verify that the file exists on disk. A download notification is not a successful restore test.

Use the same browser profile and origin to access the same local data. Scheme, host and port matter: `http://localhost:8080`, `http://127.0.0.1:8080` and `https://your-host` are different storage origins. Changing them can make a project appear absent without deleting the old origin's data. Restore via an exported project rather than editing browser databases directly.

## 2. Start locally

Use a maintained, patched Node.js version 22 or later:

```bash
cd dataforge-business-pilot
node scripts/check.mjs
node scripts/serve.mjs
```

Open `http://127.0.0.1:8080`. No npm install is needed for these server/check commands. The default host is loopback only. This static server does not receive or persist your projects.

Optional settings:

| Variable | Default | Meaning |
| --- | --- | --- |
| `PORT` | `8080` | TCP listening port. |
| `DATAFORGE_HOST` | `127.0.0.1` | Bind address. Do not expose to a network without reviewed access controls and HTTPS. |
| `DATAFORGE_NETWORK` | `cdn` | `cdn` permits the existing runtime/extension hosts; `local` removes them from the response policy and disables engine CDN fallback. |

For example, on a POSIX shell:

```bash
PORT=8081 node scripts/serve.mjs
```

In PowerShell:

```powershell
$env:PORT = "8081"
node scripts/serve.mjs
```

## 3. Engine provisioning and network modes

No DuckDB binaries are included in this ZIP. Default CDN mode requires the pinned runtime dependency to be reachable. JSON/Parquet and other extension-dependent features must also be tested against the configured extension source.

The inherited vendoring helper downloads and bundles runtime dependencies; it was not run here because external dependency downloads were unavailable:

```bash
node scripts/vendor-duckdb.mjs
node scripts/check.mjs
node scripts/serve.mjs
```

Do this only on an approved build machine with dependency/network access. Review the downloaded package, helper toolchain, licenses and integrity before deploying. Preserve the completed runtime artifact so deployment does not depend on rebuilding it ad hoc.

After provisioning local assets, strict local mode is available:

```bash
DATAFORGE_NETWORK=local node scripts/serve.mjs
```

PowerShell equivalent:

```powershell
$env:DATAFORGE_NETWORK = "local"
node scripts/serve.mjs
```

**This is not an air-gap certification.** The helper does not mirror all DuckDB extensions or configure an extension repository. The restricted host does not currently expose a full version/platform-specific extension directory tree. Features requiring an unavailable extension may fail in local mode. Complete the extension-hosting and worker egress design, then test every required format before claiming offline operation. HTTP CSP is defense in depth, not a substitute for a reviewed execution environment.

## 4. What the server does and does not expose

The host serves the app, its license, approved files directly under `/vendor/duckdb/`, and `/healthz`. It does not serve arbitrary project files, package files, source directories, dotfiles or directory listings. It rejects methods other than GET/HEAD. Keep customer data outside the application directory regardless.

It adds script-hash CSP, frame restrictions, content-type protection, a no-referrer policy and restricted browser feature permissions. HTTP headers are part of this host: using `python -m http.server`, opening the HTML directly, or moving it to another host does not automatically reproduce these controls. Never disable CSP merely to hide a runtime-loading error; investigate and requalify the required policy.

`/healthz` reports **static server availability** and runtime-file presence. Its `engineExecutionValidated` value is intentionally false. It is not an ETL execution or business correctness health check.

For a reviewed network deployment, keep this process behind the organization's authenticated HTTPS reverse proxy, preserve the required headers, bind the upstream privately and restrict access. The proxy, TLS setup and authentication integration are not bundled or tested here. Do not turn this into a public service merely by setting `DATAFORGE_HOST=0.0.0.0`.

## 5. Daily backup and recovery procedure

At the frequency approved by the data owner and before significant changes, download the complete project to an approved location outside browser storage. Review source-data scope and use export password protection when policy requires it. Keep the password through an approved separate channel.

For logic-only work, optionally enable checkpoints in Business control center -> Recovery after reviewing the consent notice. A checkpoint includes parameters and embedded test fixtures and is not encrypted by the export password. Automatic recovery is off by default. It keeps at most 10 points / 20 MiB across all local projects, with a 4 MiB per-point limit.

To test an external restore, use a fresh browser profile, open the exported project, accept the trust prompt only after reviewing its provenance, verify mappings/parameters/versions/source availability, and reconcile a representative run. Record the date, artifact and outcome. Do not clear the original workspace until the restore is confirmed.

To restore a local checkpoint, use Recovery -> Restore and confirm. The app first attempts to checkpoint current logic, then restores the selected logic. Uploaded source files and run history are not in the checkpoint; reattach missing sources. Schedules remain paused.

## 6. Conflicts and storage errors

When a tab reports a conflict, stop editing that tab. Download its recovery copy if its changes matter, then use Reload latest. A stale tab cannot merge automatically. Review separate copies before manually combining logic.

When a save fails or the app reports session-only storage, keep the tab open long enough to export your work. Check the downloaded file. Investigate browser policy, private browsing, available space or database errors before proceeding. Do not treat a local checkpoint or a persistence-permission grant as an independent backup.

## 7. Release and rollback

Retain the previous app artifact and an external project export before deployment. Build the hardening sources, run the syntax/hash check, run all applicable test suites, and record the supported browser/Node/dependency versions and acceptance results. Test from the actual deployment address so headers and runtime fetches are included.

Do not edit inline JavaScript after hashing it without rerunning `node scripts/build-business.mjs`. The host is immutable for the process lifetime: restart it after changing artifacts. Preserve `SHA256SUMS` as a change-detection manifest, not a signature or certification.

For rollback, stop the new host, restore the previous application artifact, start it at the approved origin, and restore from the retained export if needed. Validate backward compatibility and outputs in a test profile before doing this with the only copy of a business project. No guarantee is made that future project formats will open in older builds.
