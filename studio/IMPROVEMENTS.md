# Business-pilot change notes

## 1.7.0-business.1

This revision builds on the supplied UI-improved DataForge, preserving the existing engine and project format while adding an explicitly bounded business-pilot layer.

- Transactional revision checks now block stale-tab repository writes, including file and run-record changes. Same-tab project saves are serialized. Commit, failure, quota and abort paths have explicit outcomes.
- A Business control center exposes save state, local recovery, storage status, sanitized activity/diagnostics and go-live gates. It is accessible from the toolbar and command palette, with keyboard-navigable tabs and responsive layouts.
- Optional logic recovery includes manual/automatic snapshots, integrity digests and bounded retention. It explicitly excludes uploaded source files and run history and discloses unencrypted parameters/fixtures.
- Project-file imports are bounded and structurally checked before normalization. Duplicate identifiers, broken links, reserved keys, mismatched source byte lengths and excessive key-derivation parameters are rejected. Imports require a trust prompt.
- Browser scheduling is explicitly armed per tab and pauses on import, restore, new project or conflict. It is not presented as unattended orchestration.
- New projects enable sensitive preview masking by default. Existing preferences remain intact. Remote font links and overbroad privacy/recovery promises were removed.
- A dependency-free Node static host serves only intended assets and adds defensive response headers, script hashes, correct WASM MIME and a limited local-only mode.
- New tests and release-gate documentation distinguish offline UI coverage, real crypto, modeled transactions, real HTTP server checks and unexecuted integration gates.

## Compatibility notes

Mapping/project versions and normal exports remain unchanged. Newly enforced project-file limits are intentional: 256 MiB raw input/source aggregate; 500 mappings and 500 workflows; 5,000 nodes and 20,000 links per graph; 10,000 bundled source entries. Additional nesting/object/version-history bounds apply. Reserved object keys such as `__proto__`, `constructor` and `prototype` are rejected even when embedded in imported fixtures. Review and rename such fixture keys where appropriate; do not silently remove business data.

No existing project is automatically assigned a user role, uploaded to a server, encrypted in browser storage or granted a production readiness status.

The inherited engine-backed smoke suite was updated for the new trust prompts, but could not be executed here. The new real-origin persistence suite and CI configuration also remain to be executed on the intended infrastructure. See TEST_REPORT.md.
