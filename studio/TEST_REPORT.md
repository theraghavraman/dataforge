# DataForge business pilot - verification report

Build: **1.7.0-business.1**. This report covers tests actually executed against the delivered source. It is not production certification.

## Executed results

| Suite | Passed | Failed | What actually ran |
| --- | ---: | ---: | --- |
| Existing UI regression | 37 | 0 | Chromium rendering and interaction using `page.setContent`, with external runtime requests blocked. |
| Business UI and validation | 33 | 0 | New controls, consent, import checks, diagnostic allow-listing, scheduler defaults, compressed codec round trip and responsive layouts. Same offline browser scope. |
| Restricted HTTP server | 15 | 0 | Real Node HTTP requests against a loopback server: response headers, script hash, methods, paths, asset MIME, traversal and symlink boundaries. |
| Codec and persistence-model units | 25 | 0 | Actual app codec/WebCrypto code and transaction-model tests, described below. |
| **Total named checks** | **110** | **0** | **70 offline browser checks + 40 Node tests; not 110 end-to-end production checks.** |

Inline JavaScript syntax and CSP hash consistency also passed `node scripts/check.mjs`. The new real-origin suite and modified smoke suite passed JavaScript syntax checks only.

## What the 25 unit tests mean

Eight checks exercise the actual project codec/validation code using Node's compression and WebCrypto, including compressed round trip, AES-GCM round trip, wrong password, modified ciphertext, bad algorithm/work factor, damaged compression and malformed base64. One additional check exercises the actual SHA-256 checkpoint digest.

Sixteen checks execute the actual revision-checking and checkpoint implementation against a deliberately limited in-memory transaction model: commit state, unchanged saves, first-write conflicts, serialized saves, absent notifications, stale file writes/deletes, quota failure, abort behavior, retry, legacy revision upgrade, checkpoint scope, sensitive parameters, unchanged checkpoint skipping, retention and size limits.

**The transaction model is not a browser IndexedDB implementation.** It tests application control flow; it does not establish browser-specific scheduling, durability, quota semantics, reload behavior or genuine multi-tab correctness. Those require the real-origin suite.

## Browser evidence and visual review

Offline tests ran in Chromium 144.0.7559.96 on Linux with the environment's available Playwright driver, `playwright-core 1.57.0-beta-1764944708000`, not a newly installed lockfile environment. Node was v22.16.0. These are observed test versions, not recommendations to deploy unpatched versions.

Business-control screenshots were visually inspected in dark/light desktop and phone layouts. Tests also check the existing workspace at narrow widths and keyboard interactions. Screenshot animations were disabled to avoid capturing a partly completed theme-color transition. This is not a full accessibility conformance or cross-browser certification.

The generic preview-search fixture explicitly disables masking so it tests ordinary search. Separate masking assertions remain, and the new suite verifies masking is on by default for new projects. No product security default was disabled to make tests pass.

## Not executed or not established

| Area | Status and reason |
| --- | --- |
| Real-origin IndexedDB and reload | NOT VERIFIED. Browser navigation to local HTTP/file test origins was blocked by environment policy (`ERR_BLOCKED_BY_ADMINISTRATOR`). Offline `setContent` has no usable persistent origin and runs memory fallback. |
| Native-browser two-tab conflict and checkpoint restore | NOT VERIFIED. Transaction-model coverage is separate. The new `tests/persistence.mjs` is a release-gate suite for a normal development/CI host and was not executed here. |
| DuckDB data execution | NOT VERIFIED. External runtime dependency downloads were unavailable; there are no vendored engine binaries in the deliverable. Engine-unavailable UI was tested, not successful query execution. |
| Original engine-backed smoke / generated-code tests | NOT RUN. Kept in the package; smoke import interactions were updated for the trust prompt. Python/Java/cloud-runtime qualification is not claimed. |
| Dependency installation and security scan | NOT VERIFIED. Registry access was unavailable. `npm ci`, supplied package versions, transitive dependency inventory and vulnerability review must be performed on the approved build system. |
| GitHub-hosted CI | NOT RUN. Configuration adds business/local and persistence gates and uses Node 22 as the local-validation baseline. An actual repository run is required. |
| Local-only runtime/extension deployment | NOT VERIFIED end to end. HTTP policy and fallback configuration were checked; extension provisioning, browser/worker egress and all data formats still require qualification. |
| Authenticated HTTPS / security review | NOT PROVIDED or tested. Static hosting is not authentication or a managed backend. No penetration test or compliance assessment. |
| Workload capacity, other browsers and full accessibility | NOT VERIFIED. No representative production-volume benchmark, Safari/Firefox/Edge matrix or complete axe/screen-reader review. |

## Reproduction commands

Dependency-free checks:

```bash
node scripts/check.mjs
node --test tests/server.test.mjs tests/unit.mjs
```

After validating and installing browser test dependencies on your build machine:

```bash
npm ci
npx playwright install chromium
npm run test:local
npm run test:persistence
npm test
```

The first command group and both offline UI scripts ran here. The persistence and engine commands above are **required future release gates**, not evidence of a passing run in this delivery.

For generated code, install the dependencies declared in `tests/codegen/requirements.txt` and satisfy its Java requirements, then run `npm run test:codegen`. Treat cloud dialect translation checks separately from execution on actual target warehouses.

## Evidence included

- `evidence/ui-results.json` and `evidence/ui-tests.log`: all 37 UI check names and results.
- `evidence/business-results.json` and `evidence/business-ui-tests.log`: all 33 business check names and results.
- `evidence/node-tests.tap`: all 40 Node test names and results.
- `evidence/environment.json`: observed test environment, app checksum and explicit scope flags.

These are test records from this delivery, not independent attestations. The release owner should attach new evidence from the intended host before approving business data.
