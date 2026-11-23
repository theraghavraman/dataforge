# Changelog

## 2.1.0-rc2 - 23 November 2026

Re-qualification of rc1 on a clean install from PyPI with the patched FastAPI, a full
dependency audit and a working Chromium origin gate. Each defect below was reproduced
against the unmodified rc1 code before it was fixed. The new regression tests in
`tests/test_rc2_fixes.py` were run against rc1: 11 of the 13 fail there and all pass in rc2.
The other two are coverage that rc1 already satisfied: the first passing preflight, and one
malformed-file variant rc1 already rejected.

### Security and availability

- **Dependency bound forced a vulnerable cryptography release.** `requirements.txt` capped
  `cryptography<48`, so every install resolved to 47.x, which has GHSA-537c-gmf6-5ccf,
  PYSEC-2026-3552, PYSEC-2026-3553 and PYSEC-2026-3554. It now requires `>=50.0.0,<51`.
  `pip-audit` over the resolved rc2 environment reports no known vulnerabilities.
- **Successful sign-ins consumed the brute-force budget.** Ten correct sign-ins to one account
  within 15 minutes locked that account out. Only failed attempts are now counted; failures
  made before a success still count.
- **Organization-wide sign-in lockout behind the reverse proxy.** The shipped Caddy template
  stripped `X-Forwarded-For` and the API ran with proxy headers off. Every user therefore
  shared the proxy's address, and about 60 sign-ins per 15 minutes across the organization
  returned HTTP 429 to everyone. The same shared bucket applied to password-recovery and
  SSO-start throttles.
  - New `DATAFORGE_TRUSTED_PROXIES` setting: exact IP addresses or networks only. Wildcards
    and host names are rejected, so clients cannot choose their own address.
  - `X-Forwarded-For` is read only from those addresses.
  - The Caddy template now forwards the real client address.
  - Production preflight has a new `client_addresses` gate requiring the setting.

### Fail-closed behavior

- **Preflight crashed on a malformed qualification file.** An entry like
  `{"browser_origin": "approved"}`, a bare string or a JSON array raised `AttributeError`
  instead of reporting "not ready". These now fail the `deployment_qualification` gate.
- **Unclear error when the query sandbox hit a resource limit.** A runner killed by its CPU or
  memory limit leaves no report. Users saw `Execution failed: Expecting value: line 1 column 1`;
  the job now fails with an explicit resource-limit message.

### Test and release tooling

- `tests/designer_bridge.py` hard-coded `/usr/bin/chromium` and `--no-sandbox` instead of the
  shared `launch_browser` helper. It failed on any host without that exact path. Its results
  file also had no overall status, so a crashed run looked like a clean partial run; it now
  records `PASS`/`FAIL`.
- First test of a fully configured production preflight that passes, so the go-live path
  itself is exercised.
- Cookie clearing after a password change now uses the same attributes the cookie was set with.
- Repository-ready: `studio/.gitignore` no longer drops the two checksummed studio evidence logs,
  and a root `.gitattributes` keeps LF line endings so `SHA256SUMS.release` verifies in any clone.
- `pip` is upgraded before dependency installation in the README and Dockerfile. pip 24.0 has
  its own advisories.

### Documentation

- Deployment, operations, security, dependency and readiness documents updated for the
  above.
- The single-owner approval-policy limitation is now documented in `docs/PIPELINES.md`.
- rc1 evidence is preserved unchanged under `evidence/rc1/`.

### Unchanged

- The API schema is unchanged apart from the version string; `docs/openapi.json` was
  regenerated and compared.
- No database migration is required; the schema version is still 2.
- Upgrading from rc1: install the new requirements and set `DATAFORGE_TRUSTED_PROXIES`.
  Remove `header_up -X-Forwarded-For` from an existing Caddyfile.
