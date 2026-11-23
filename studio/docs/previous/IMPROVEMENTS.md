> Historical documentation from the supplied UI build. Not a statement of current verification or security. The root README, BUSINESS_READINESS, DEPLOYMENT, SECURITY and TEST_REPORT documents take precedence.

# DataForge 1.6.1 - Workspace improvements

This is an improved copy of the supplied DataForge 1.6.0 project, not an upstream release.
The MIT license and original author attribution are retained.

## What changed

### Workspace and onboarding
- A two-row toolbar separates project navigation from actions; Run remains prominent.
- Refined dark and light themes, typography, spacing, node styling, and canvas grid.
- A responsive welcome screen with three real sample mappings and a visual pipeline overview.
- About no longer interrupts the first visit; it remains accessible from the toolbar.
- Welcome's Import mapping XML now opens the file picker instead of silently loading demo XML.
- Updated header offsets for the monitor, mobile drawers, version banner, and upload banner.

### Node library
- Search mapping nodes and workflow tasks, with result counts, clear controls, and no-match feedback.
- Mapping search supports aliases such as spreadsheet, group, merge, and formula.
- Search queries are retained independently for mapping and workflow modes during the session.
- Build tools appear ahead of the repository. The Repository button jumps directly to its search field.
- Enter and Space can add nodes; workflow palette items are keyboard-focusable.

### Data preview
- Pagination renders 100 rows by default, with 25, 50, 100, or 250 rows per page.
- Search works across the loaded preview rows, not the entire underlying source file.
- Pagination and search change only the preview; they do not change pipeline output or source data.
- When preview masking is enabled, sensitive columns are excluded from search to avoid revealing hidden values through matches.
- Sort buttons work with the keyboard and expose their current sort direction.
- Loaded versus total row counts, continuous row numbering, and explicit empty states.

### Accessibility and usability
- Skip-to-canvas link, clearer control labels, and reduced-motion support.
- Command search now explains when no commands match.
- Mobile library/properties drawers align with the taller header; engine retry remains accessible.
- Existing mapping and project file formats are unchanged. No new production dependencies or build step.

## Start the application

Extract the ZIP and open a terminal in the dataforge-improved folder:

```sh
python3 -m http.server 8080
```

Visit `http://localhost:8080` in a modern browser. For Windows installations without `python3`, use `python -m http.server 8080`.

The standalone HTML contains the same application. For reliable browser storage and worker behavior, serving it over HTTP is preferable to opening it directly from disk.

DuckDB-WASM still loads from the original pinned CDN by default. This build does not bundle it. The existing `scripts/vendor-duckdb.mjs` can prepare a self-hosted copy in an environment with network access. JSON and Parquet extensions may still need network access, as described in the original README.

## Tests

```sh
npm ci
npx playwright install chromium
npm run test:ui
npm test
```

`test:ui` is the added offline UI suite. It deliberately blocks external requests and tests real browser rendering and interactions without a DuckDB engine. Preview tests use clearly identified synthetic rows. This suite does not simulate successful database execution.

`npm test` remains the existing engine-backed smoke suite. Its obsolete automatic-About expectations have been updated. The existing generated-code execution tests are also retained.

See TEST_REPORT.md for the checks actually executed and their limits.
