# DataForge business readiness

Build: **1.7.0-business.1**. Assessment basis: the supplied DataForge source, the changes in this package and the executed tests listed in TEST_REPORT.md.

## Decision

**Suitable for a controlled evaluation with non-sensitive test data. Not approved by this delivery for business-critical production, regulated data, multi-user workspaces or unattended production jobs.**

The browser remains the execution environment and the storage boundary. There is no backend account system, centrally stored project, durable job queue or managed backup service. A login-looking screen or client-side role selector would not change that boundary, so neither has been added.

## Implemented controls and their limits

| Control | Scope | Important limit |
| --- | --- | --- |
| Project revision checking | Comparison and write share an IndexedDB readwrite transaction. Same-tab writes are serialized; stale tabs cannot mutate repository records. | Actual cross-tab browser integration still needs the real-origin release gate. No collaborative merge, account separation or shared team database. |
| Visible save state | Saved, pending, saving, memory-only, failed and conflict states; export/reload conflict actions. | Browser commit does not mean independent backup. Before-unload warnings are not guaranteed recovery. |
| Logic checkpoints | Manual creation and opt-in automatic creation every five minutes when changed; SHA-256 digest; confirmation on restore. | Local, unencrypted, and includes parameters/test fixtures. Uploaded source files and run history are excluded. |
| Bounded retention | At most 10 points, 20 MiB total, 4 MiB per point, across local projects. | Browser storage errors can interrupt maintenance; external exports are still required. |
| Project-file validation | Limits, graph checks, reserved-key rejection, source binding/byte checks and constrained encryption envelopes. | Not a sandbox, a complete schema proof, a malware scan or a SQL security review. |
| Schedule consent | Paused on initial load. Explicit per-tab arming after the engine is available; disarmed on import/restore/new project/conflict. | Browser timers are not a durable service and stop with the tab. |
| Privacy defaults | Default preview masking for newly created projects; removal of remote font requests. | Existing governance preferences are preserved. Masking is not authorization and does not encrypt stored data or every export. |
| Diagnostics | Fixed event codes, timestamps, technical counts and engine/storage state. | Not centralized monitoring, authenticated audit evidence or guaranteed telemetry delivery. Review any file before sharing. |
| Restricted static hosting | Loopback default, explicit served paths, HTTP CSP and other defensive headers. | No TLS termination, identity provider, API permissions, server-side SQL sandbox or enterprise egress enforcement. |

## Acceptance gates before routine single-user business use

All gates below are **open** unless an owner records evidence from the intended deployment. Delivered offline UI checks and modeled transactions do not close these gates.

| Priority | Gate | Required acceptance evidence | Suggested owner |
| --- | --- | --- | --- |
| P0 | Engine and output correctness | Engine-backed smoke suite succeeds on the actual host; independently reconcile row counts, totals, rejects, nulls, date/time zones and precision against representative expected outputs. Test every required source/target format. | Data engineering and business owner |
| P0 | Real persistence | Run `npm run test:persistence`; confirm browser reload, two-tab conflict with and without channel notifications, source-store protection and checkpoint restore. | Engineering / QA |
| P0 | Independent backup restore | Export the intended project scope, retain the file outside browser storage, reopen in a fresh profile, restore the sources, run and reconcile output. Test encrypted export and wrong-password behavior. | Operations / data owner |
| P0 | Device and data policy | Approve the browser profile/device, retention and allowed data classification. Do not place credentials in mapping parameters. Establish who can access exported files. | Security / data owner |
| P0 | Deployment security | Review HTTPS/authenticated access if network-facing, CSP behavior with the real engine, dependency inventory, imported SQL and required outbound destinations. | Security / platform |
| P0 | Release reproducibility | Validate `npm ci`, lockfile packages, supported runtime/browser versions and CI on the team's infrastructure; retain logs and artifact checksums. | Engineering |
| P1 | Capacity and failure handling | Test the actual maximum supported workload on representative devices, memory pressure, failed downloads, malformed inputs, quota exhaustion, cancellation and interrupted runs. Record limits; no measured throughput limit is claimed here. | QA / operations |
| P1 | Accessibility and browser matrix | Run accessibility automation and manual keyboard/screen-reader review; test each browser and mobile device the business intends to support. | QA / accessibility |
| P1 | Operating ownership | Name the support owner, incident path, approved release/rollback process and backup frequency. Agree acceptable data-loss and recovery-time targets. | Product / operations |

## Additional work for a shared business product

The following is a **proposed architecture**, not code implemented by this package:

```text
Browser designer
    |
Authenticated API / identity integration
    |-- Projects, revisions, role checks and workspace isolation
    |-- Metadata database
    |-- Controlled object storage for source and output files
    |-- Durable queue -> isolated ETL workers -> run results
    |-- Central operational logs, verified audit records and alerts
    `-- Managed backups, restore procedures and retention policy
```

Prioritize server-enforced identity and project permissions, versioned central storage, and tested backup restore before adding collaborative features. Then add isolated jobs with resource/time limits, retry/idempotency policy, durable scheduling, secret management and monitoring. Adopt a database, object store and queue aligned with the existing organization stack; this package does not mandate a vendor.

For collaboration, retain revision checks at the API boundary and define merge/conflict UX. For multiple organizations, validate workspace isolation in every API, object-storage access path, export and job. A reverse-proxy login may restrict entry to the page, but it does not provide these repository and job-level controls.

## Operational definitions

**Saved in this browser** means the current local project write committed; it does not prove an independent copy exists. **Checkpoint** means a limited local logic snapshot, not a full source-data backup. **Password-protected project** means the exported file is encrypted, not that browser storage is encrypted. **Activity** means editable local events, not a compliance audit trail. **Engine available** means loaded, not that business outputs have been accepted.

## References supporting the boundaries

These references explain general platform behavior; the implementation and tests above are specific to this package.

- MDN, storage quotas, origins and eviction: https://developer.mozilla.org/en-US/docs/Web/API/Storage_API/Storage_quotas_and_eviction_criteria
- OWASP, browser storage and IndexedDB security: https://cheatsheetseries.owasp.org/cheatsheets/HTML5_Security_Cheat_Sheet.html
- DuckDB, WASM deployment and dependency options: https://duckdb.org/docs/stable/clients/wasm/deploying_duckdb_wasm
- OWASP, Content Security Policy: https://cheatsheetseries.owasp.org/cheatsheets/Content_Security_Policy_Cheat_Sheet.html
