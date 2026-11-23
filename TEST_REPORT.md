# Verification report - DataForge Team 2.1.0-rc2

**Prepared 23 November 2026. 287 automated checks passed; none failed, blocked or skipped. Browser-origin gate PASSED. Dependency audit CLEAN. Production qualification NOT approved; that remains your operators' decision.**

All results below come from one `python scripts/qualify.py` run on a newly created virtual environment. That run executed `pip check`, `pip-audit` and `scripts/check_all.py --origin` against the delivered code. Intermediate development runs are not added to any total. rc1's results are preserved unchanged in `evidence/rc1/`.

| Final test group | rc1 | rc2 |
| --- | ---: | ---: |
| Backend, crypto, permissions, pipelines, backup, local TLS/SMTP | 165 | 178 |
| Actual HTTP API and separate worker/restart | 10 | 10 |
| Existing team interface (DOM + API bridge) | 18 | 18 |
| New security, approvals, pipeline and readiness interface | 18 | 18 |
| Designer publishing and conflict bridge | 9 | 9 |
| Inherited Node server/codec/encryption regression | 40 | 40 |
| Bounded HTTP concurrency with two worker processes | 5 | 5 |
| Shipped launcher and example-import integration | 5 | 5 |
| Genuine browser origin (real Chromium navigation) | BLOCKED | 4 |
| **Total** | **270** | **287** |

Python/JavaScript syntax checks, `pip check`, `pip-audit` and `npm audit` also passed; they are not counted in the 287. One Starlette deprecation warning (about `httpx` in its test client) was reported; it does not affect results.

## Environment

- Linux x86-64; CPython 3.12.3; SQLite 3.45.1; Node 22.23.5.
- Clean install from PyPI: FastAPI 0.165.0, Starlette 1.10.0, uvicorn 0.54.0, cryptography 50.0.2, pydantic 2.14.4, boto3 1.44.9 and pip 26.3.
- Chromium via Playwright 1.63.0, the newest release whose browser build was available offline; `requirements-dev.txt` permits it.
- The full record is in `evidence/qa-environment.json` and `evidence/qualification/resolved-environment.txt`.
- rc1 was qualified on CPython 3.13.5. Both 3.12 and 3.13 are now documented; qualify on the version you deploy.

## Defects found by re-qualification

rc1 was first re-run unchanged in this environment. With the patched FastAPI, 12 of 13 suites passed. The designer-bridge suite failed because it hard-coded `/usr/bin/chromium`. The browser-origin gate, blocked in rc1, passed immediately.

The newly possible dependency audit then failed on rc1's own requirements. Code review and targeted reproductions found further defects. Each was reproduced against unmodified rc1 before fixing:

| Defect | Reproduction against rc1 | rc2 |
| --- | --- | --- |
| `cryptography<48` forced a release with 5 advisories | `pip-audit` failure on a clean install | `>=50.0.0,<51`; audit clean |
| Successful sign-ins counted as attempts | 11th correct sign-in in 15 minutes returned 429 | 15 consecutive correct sign-ins succeed; prior failures still count |
| Behind the shipped proxy, all users shared one IP bucket | 51st sign-in across 70 distinct users returned 429 | `DATAFORGE_TRUSTED_PROXIES` + preflight gate; 70 users via a real server process all succeed |
| Malformed qualification file crashed preflight | `AttributeError` on `{"browser_origin":"approved"}` | Fails closed: `deployment_qualification` false |
| Sandbox killed by resource limit gave a parser error | Job error `Expecting value: line 1 column 1` | Explicit CPU/memory-limit message |
| Designer test bound to one host's browser path | Suite failed: executable not found | Uses the shared `launch_browser` helper; results file records overall status |

The 13 regression tests in `tests/test_rc2_fixes.py` were also run against unmodified rc1: 11 failed there and all 13 pass in rc2. The two that pass on rc1 are coverage additions: the first fully configured preflight that passes, and one malformed-file variant rc1 already handled. The trusted-proxy test starts the real `app.cli serve` process twice:

- with a trusted proxy, 70 distinct forwarded clients all sign in;
- without one, spoofed `X-Forwarded-For` values cannot evade the per-IP limit, which returns 429 after 60 failures.

The regenerated `docs/openapi.json` is identical to rc1's apart from the version string. No API or database schema change was made.

## Actual behavior exercised

The tests cover:

- real SQLite transactions, permission isolation and optimistic save conflicts;
- source checksums, independent worker subprocesses, cancellation and lease recovery;
- precise pipeline expected outputs, unsupported nodes and dialects, and strict source types;
- revisions and approval revocation;
- MFA and one-use recovery codes, and purpose-bound secret encryption;
- RSA-signed mocked OIDC claims and encrypted outbox retries;
- archive integrity and restored execution.

HTTPS tests start a real TLS API listener with a temporary trusted certificate. They reject an untrusted certificate and exercise actual Secure/HttpOnly/SameSite cookies, CSRF, origin rejection and logout. SMTP tests use a real local TLS receiver, verify delivery and reject an untrusted certificate. These are actual sockets, not proof of a production proxy, public CA certificate or external mailbox.

The browser-origin gate loads the running application in Chromium over loopback HTTP, with no request interception or HTML replacement. It then signs in with real cookies and CSRF, survives a page reload and signs out. This validates browser-enforced cookie, CSP and same-origin behavior on a local origin. It does not validate your public hostname, certificate chain or reverse proxy; repeat the gate after activation.

The bounded concurrency experiment made 48 submissions with 8 concurrent HTTP threads. They were deduplicated into 24 jobs and executed by 2 independent workers, each over 10,000 synthetic CSV rows. Every result matched the independently computed sum 79,974. Observed submission latency was median 8.75 ms, p95 94.83 ms and maximum 120.01 ms, with an elapsed time of 1.53 s. These are observations on a disposable environment, not a production SLA.

## Methods that must not be conflated

**UI and designer tests.** Chromium renders the actual application, with API requests routed to a real FastAPI TestClient through an explicit bridge. They verify UI actions, backend results and desktop/390-pixel layouts. Browser-origin behavior is covered separately by the origin gate above.

**OIDC.** Real RSA signature, claim, state and PKCE validation against a deterministic mocked provider transport. A real organization's provider registration, redirects, deprovisioning and clock policy are not qualified.

**S3.** Real boto3 client shapes and checksum logic with the SDK Stubber. No real bucket, IAM policy, KMS key or cloud restore was used.

**DuckDB designer engine.** The runtime binaries remain unprovisioned. Neither native DuckDB execution nor semantic equivalence of unsupported visual paths was validated; the portable server compiler's declared subset was executed.

## Still not performed

- Caddy, Docker and systemd activation; Compose was not built.
- Real DNS and public TLS, firewall, encrypted production volumes.
- External identity-provider, mail, webhook and backup accounts.
- Off-host restore drill.
- Independent security review and business acceptance.
- A hash-locked dependency artifact for the deployment platform.

Production preflight in this development environment correctly reports not ready (`evidence/preflight-qa.json`). The patched-FastAPI gate now passes.

## Evidence

- `evidence/qualification/`: the qualification run itself. Contains `automated-results.json`, `pip-audit.json` (62 packages, no findings), the resolved environment and logs.
- `evidence/verification-run.json` and per-suite logs: the orchestrated suite including the origin gate.
- `evidence/backend-junit.xml`: 178 pytest cases, none failed, errored or skipped.
- `evidence/origin-smoke.json`: genuine browser-origin results.
- `evidence/http-results.json`, `ui/`, `ui-v2/`, `designer/`, `legacy-node-tests.tap`, `load-results.json`, `package-smoke.json`: per-suite results and synthetic-data screenshots. Screenshots show no MFA secrets or recovery codes.
- `evidence/npm-audit.log`, `evidence/preflight-qa.json`, `evidence/qa-environment.json`.
- `evidence/rc1/` and `evidence/prior-release/`: provenance only, not counted.

On your network-enabled qualification host, install the approved requirements and a supported Chromium. Then run `python scripts/qualify.py` on the platform you will deploy. Provider, security, business, recovery and capacity evidence still needs responsible operator approval.
