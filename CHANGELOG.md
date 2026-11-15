# Changelog

## 1.1.0

### Fixed
- **New workflow crashed.** "+ Workflow" and New in the Workflow Designer called a function that did not exist.
- **Ctrl+Enter in the Workflow Designer** now runs the workflow (it used to try to run the mapping behind it).
- **First run:** the welcome screen no longer covers the sample menu, and loading a sample dismisses it.
- **Samples after a reload:** bundled sample data is re-attached, so a sample mapping still runs after the page is reloaded.
- **Load Sample no longer overwrites the open mapping.** It opens as a new repository mapping.
- **Project import can be cancelled.** A Merge / Replace / Cancel dialog replaces the old OK = replace, Cancel = merge prompt, and Replace downloads a backup of the current project first.
- **The last edit is no longer lost** when the tab is closed within a quarter of a second of making it.
- **Reload reopens the mapping you were working on** instead of the first one in the repository.
- **`$$WF_ROWS` is reset at the start of every workflow run** instead of accumulating across runs.
- **DuckDB SQL export** inlines mapping parameters; it used to leave `$$PARAM` tokens that DuckDB cannot parse.
- Stray "null" text under the data preview and in the export warnings.
- Dead GitHub links, missing Timer and Notification icons, duplicate top-bar icons, truncated mode selector.
- Creating a mapping while viewing a historical version left the new mapping read-only.

### Added
- **In-app dialogs** replace every browser `confirm` / `prompt`; destructive actions are named on the button and Escape always cancels.
- **Phones and tablets:** below 900px the canvas takes the full width, the repository/palette and properties become slide-in drawers, palette items are added with a tap, and two-finger pinch zooms the canvas. Editing is no longer disabled.
- **Workflow export:** Save / Ctrl+S in the Workflow Designer downloads the workflow with the mappings and parameter sets it uses as a `.dfproj.json`; Open accepts mapping, workflow and project files.
- **Engine retry:** if DuckDB cannot be loaded, click the engine status to retry; it also retries when the device comes back online.
- **Self-hosting:** `node scripts/vendor-duckdb.mjs` copies DuckDB-WASM into `vendor/duckdb/`; the CDN stays as the fallback.
- **Content-Security-Policy** limiting scripts and connections to the page itself, the DuckDB CDN, the DuckDB extension host and Google Fonts.
- **Keyboard and screen-reader support for dialogs:** focus moves into a dialog, Tab stays inside it, focus returns to the opener, and canvas shortcuts cannot act on the mapping behind it. The command palette supports the arrow keys.
- **Multi-tab warning** when the same project is changed in another tab.
- **Error reporting:** unexpected errors show a message instead of failing silently; storage-full errors explain what to do.
- **Scheduled workflows** wait for an idle moment, announce themselves and return you to what you had open. Navigation that would load another mapping is blocked while a workflow runs.
- Share links are size-capped on import and fall back to a copyable field when the clipboard is blocked.
- End-to-end smoke test (`npm test`) and a GitHub Actions workflow.
