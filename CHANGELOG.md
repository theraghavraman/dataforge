# Changelog

## 1.1.0 — 2026-11-15

### Fixed

- New workflow creation no longer calls an undefined function.
- Ctrl/Cmd+Enter runs workflows in workflow mode and is blocked in Monitor.
- First-run welcome screen no longer blocks the sample picker; loading a sample dismisses it.
- Bundled sample files reattach after page reload.
- Loading a sample creates a new mapping rather than overwriting the open mapping.
- Project import now offers Merge / Replace / Cancel; Replace initiates a project backup download.
- Pending repository saves flush when the page is hidden or closed; last-open mapping is remembered.
- `$$WF_ROWS` resets at the beginning of a workflow run.
- Removed stray rendered `null` text; corrected GitHub links, workflow icons, duplicate top-bar icons, and truncated mode selector.

### Improved

- Application dialogs replace native `confirm` and `prompt` calls.
- Unexpected errors show a toast.
- DuckDB initialization can be retried from engine status or when connectivity returns.
- Concurrent project editing in another tab shows a warning.
- Shared mapping import caps decompressed payload size; clipboard fallback handles browser restrictions.
- Storage-limit errors have clearer messages; browsers are asked for persistent storage when remembering files.

### Repository

- Updated README for v1.1.0.
- Added initial static checks, Playwright browser smoke tests, and GitHub Actions workflow. These tests are new release tooling, not part of the single-file application.

### Known gaps

See [README — Known limitations](README.md#known-limitations-v110). JSON-extension execution, full code-generator validation, CSP, dependency self-hosting, older modal focus trapping, mobile editing, standalone workflow export, and non-navigating scheduler behavior remain open.

## Earlier versions

Prior versions predate this repository changelog; historical details have not been reconstructed here.
