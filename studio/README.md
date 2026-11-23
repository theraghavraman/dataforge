# DataForge - Business Pilot

**Build 1.7.0-business.1 | single-user, local-browser deployment**

This is a hardening pass on the supplied DataForge application. It adds practical reliability and deployment controls, but it is **not a production multi-user platform or a security certification**. Use non-sensitive test data until the release gates are approved.

## Start the supplied build

Use a maintained, patched Node.js release that is version 22 or newer. The static host uses Node built-ins; installing npm packages is not necessary just to launch it.

```bash
cd dataforge-business-pilot
node scripts/serve.mjs
```

Open `http://127.0.0.1:8080` in your browser. The server binds only to the local machine by default. Keep the same scheme, hostname and port for the same browser workspace. Back up your old project before changing builds or addresses.

DuckDB runtime files are **not bundled**. The default mode uses the existing pinned CDN dependency, with local assets preferred when configured. A network connection or separately provisioned local runtime is required for data execution. A working user interface does not prove that the engine has loaded or that a mapping is correct.

## What changed

| Area | Implemented in this build |
| --- | --- |
| Save reliability | Revision-checked, transactional project writes; same-tab save serialization; stale-tab write protection; explicit failed-save and session-only states. |
| Recovery | Opt-in logic checkpoints, SHA-256 corruption checks, bounded retention, and confirmation before restoration. |
| Project import | Size and structural limits, duplicate and dangling-ID rejection, source-byte checks, bounded encryption parameters, and trust confirmation. |
| Scheduling | Browser schedules start paused. Explicitly arm reviewed schedules for the current tab; import, restore, conflict and new-project operations pause them again. |
| Privacy | New projects mask detected sensitive preview columns by default. Export encryption is clearly distinguished from unencrypted browser storage. |
| Operations | Business control center, allow-listed local events, sanitized diagnostics, storage information, and visible go-live gates. |
| Hosting | Restricted Node static server, response security headers, script-hash CSP, limited asset exposure, and an explicit local-only network mode. |
| Quality | New browser UI, codec, transaction-model and server tests; real-origin persistence release-gate suite; updated CI configuration and deployment documentation. |

Open **Business control center** using the settings icon near About or the command palette. Its Overview, Recovery, Activity and Go-live gates sections explain the current state and remaining limitations.

## Existing application capabilities retained

The existing visual mapping and workflow designers, previews, transformations, data-quality rules, lineage, mapping versions, parameter sets, code exports and project-file formats are retained. The core ETL execution and code-generation implementation was not rewritten. This does not imply all existing features have been requalified: the engine-backed suites remain required.

Project downloads may include uploaded sources, parameters, embedded test fixtures and run history. A "without source files" export is **not** a sanitized or secret-free export. Review files before sharing them.

## Read before business use

- [BUSINESS_READINESS.md](BUSINESS_READINESS.md): implemented controls, missing capabilities and acceptance gates.
- [DEPLOYMENT.md](DEPLOYMENT.md): startup, network modes, backup/restore and upgrade procedure.
- [SECURITY.md](SECURITY.md): trust boundaries, sensitive data and limitations.
- [TEST_REPORT.md](TEST_REPORT.md): exactly what passed and what has not been verified.

## Development and validation

The maintained hardening layer is in `src/business.js` and `src/business.css`; it is embedded into the standalone `index.html`.

```bash
node scripts/build-business.mjs
node scripts/check.mjs
node --test tests/server.test.mjs tests/unit.mjs
```

Browser tests need the dependencies in the supplied lockfile and a compatible Chromium installation:

```bash
npm ci
npx playwright install chromium
npm run test:local
npm run test:persistence
npm test
```

The dependency installation, real-origin persistence suite, full engine suite and GitHub-hosted CI were not executable/verified in the delivery environment. See TEST_REPORT for the precise restrictions and installed test-runner versions. The original generated-code suite remains available through `npm run test:codegen`; review its additional Python/Java requirements before running it.

## Files and provenance

`index.html` is the deployable app. `scripts/serve.mjs` is a static host, **not** an API, authentication service, job worker or shared database. `evidence/` contains the delivered test records. `docs/previous/` preserves the previous build's documentation as historical material, not current assurances.

Original DataForge authorship and the supplied MIT license are retained in [LICENSE](LICENSE). This derivative build is not represented as an official upstream release. Do not assume the upstream live demo contains these changes.
