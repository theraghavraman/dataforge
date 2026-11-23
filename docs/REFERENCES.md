# References and source provenance

The supplied DataForge source and prior team package are the basis of this derivative. Existing MIT licensing and authorship are preserved. The retained `studio/` documentation describes earlier builds. Root source/docs/evidence describe the 2.1.0-rc2 release candidate; `evidence/rc1/` preserves the superseded rc1 evidence.

Primary design/security references consulted:

- FastAPI security advisory, published 17 November 2026, patched version 0.163.2: https://github.com/fastapi/fastapi/security/advisories/GHSA-f56c-v883-vx8r
- TOTP RFC 6238: https://www.rfc-editor.org/rfc/rfc6238
- OpenID Connect Core 1.0: https://openid.net/specs/openid-connect-core-1_0.html
- OWASP Password Storage: https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html
- OWASP Forgot Password: https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html
- OWASP Session Management: https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html
- DuckDB security guidance: https://duckdb.org/docs/stable/operations_manual/securing_duckdb/overview.html
- Caddy reverse proxy configuration: https://caddyserver.com/docs/caddyfile/directives/reverse_proxy

These references inform implementation choices, not proof of compliance or a claim that all features of the referenced standards are implemented. Local tests and their limitations are recorded in TEST_REPORT.md and evidence/.
