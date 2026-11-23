# Dependencies and qualification

`requirements.txt` sets minimum deployment versions and major-version bounds. It is not a hash-locked lockfile. `requirements-s3.txt` adds the optional AWS SDK; `requirements-dev.txt` adds test/audit tools. The environment actually used for rc2 qualification is recorded in `evidence/qa-environment.json` and, as a full `pip freeze`, in `evidence/qualification/resolved-environment.txt`. Superseded rc1 snapshots are kept under `evidence/rc1/` for provenance only.

## Security prerequisites

**FastAPI.** GHSA-f56c-v883-vx8r affects >=0.123.2,<0.163.2 and is fixed in 0.163.2. The rc1 QA image ran 0.128.2 and could not download the fix. The rc2 qualification performed a clean install of the deployment and development requirements from PyPI, which resolved FastAPI 0.165.0 (Starlette 1.10.0). The full suite passed on it, and production preflight accepts it.

**cryptography (changed in rc2).** rc1 required `cryptography>=46.0.4,<48`. That upper bound made every install resolve to a 47.x release with published advisories: GHSA-537c-gmf6-5ccf (bundled OpenSSL, fixed 48.0.1), PYSEC-2026-3553 and PYSEC-2026-3554 (certificate-chain verification denial of service and name-constraint bypass, fixed 49.0.0), and PYSEC-2026-3552 (PKCS#7 decryption oracle, fixed 50.0.0). rc2 requires `cryptography>=50.0.0,<51`; 50.0.2 was resolved and tested. The application itself uses AES-GCM, and PyJWT uses the library for RS256 verification; neither calls the PKCS#7 or X.509 path-validation APIs. The bundled-OpenSSL advisory, however, applies to every process that loads the library. In any case the bound forced a vulnerable package into every deployment and failed any dependency audit.

**Audit result.** `pip-audit` 2.10.1 over the resolved rc2 environment reported no known vulnerabilities (`evidence/qualification/pip-audit.json`). The virtual environment's own `pip` had to be upgraded first; the bundled pip 24.0 carried advisories of its own. Upgrade pip in every build environment before installing. `npm audit` of `studio/package-lock.json` reported no vulnerabilities. A clean scan reflects the advisory databases on 23 November 2026 only. Review native OS packages, container images and Node tooling too; a package audit is not an application security review.

## Build-host procedure

Test processes clear inherited `DATAFORGE_*` configuration before constructing fixtures, so a production SMTP/backup/alert setting is not accidentally used by synthetic tests. Still run qualification in a dedicated non-production build/test environment.

Use an isolated approved Python 3.12 or 3.13/Linux build environment with SQLite >=3.39 and a trusted package index. Install deployment and development requirements. Run `pip check`, then `python scripts/qualify.py`. This performs the vulnerability scan and full automated suite including genuine browser-origin tests, and writes the resolved environment and result logs. A nonzero result must be investigated; do not ignore advisory IDs merely to get a green build.

After approving the resolved environment, create a hash-locked dependency artifact and reviewed wheelhouse through your organization's standard reproducible-build toolchain. Pin an approved container base digest, archive licenses/SBOM and artifact checksums, rebuild/install in a fresh environment, and rerun the qualification suite. This release still does not ship a hash lockfile: the rc2 resolution was made for CPython 3.12 on x86-64 Linux, and pinned binary wheels (for example `pydantic-core`) differ for the Python 3.13 container base. Lock on the actual build platform.

## Retained DuckDB designer

The authenticated designer is configured to use same-origin runtime assets; it does not automatically fetch its DuckDB runtime from a CDN. The original vendoring helper remains under `studio/scripts/`. Its downloads, Node toolchain, extension dependencies and browser execution were not qualified here. Provision it only through a reviewed build process, preserve integrity/version/license evidence and execute each required browser feature at the actual origin. The portable CSV/SQLite server path does not need DuckDB binaries.

## Source of the advisory

- https://github.com/fastapi/fastapi/security/advisories/GHSA-f56c-v883-vx8r

Environment and test evidence are produced from this release's actual local run. They are not proof that later package versions are compatible or vulnerability-free.
