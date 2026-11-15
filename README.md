# DataForge — Browser ETL Studio

A visual ETL designer that runs entirely in the browser. You build mappings visually on a canvas, they compile to SQL, and DuckDB-WASM executes them locally. Nothing leaves your machine.

**Live demo:** https://theraghavraman.github.io/dataforge/

## Features

- **Mapping Designer.** Drag-and-drop canvas with Source, Filter, Expression, Sorter, Joiner, Lookup, Aggregator, Router, Union, Rank, Sequence Generator, SCD Type 1, SCD Type 2, Data Quality and Target transformations.
- **In-browser execution.** Every mapping compiles to a DuckDB SQL CTE chain and runs on CSV/JSON files through DuckDB-WASM, with data preview at any node.
- **Warehouse patterns.** SCD1/SCD2 with hash-based change detection, surrogate keys, reject handling, and data quality rules (reject, flag or warn).
- **Parameters and variables.** `$$PARAM` syntax, with persistent mapping variables for incremental loads.
- **Code generation.** Export any mapping as Snowflake SQL (MERGE-based SCD), a runnable PySpark script, or a complete dbt project (models, snapshots, schema tests).
- **Column-level lineage.** Impact analysis, plus Source-to-Target Mapping (STTM) export and Markdown mapping specifications.
- **Workflow Designer.** Sessions, decisions, assignments, timers, notifications and conditional links, with concurrent branches.
- **Monitor.** Run history, Gantt timeline, session statistics, trend charts and run comparison.
- **Mapping XML import.** Best-effort conversion of repository mapping XML exports, with an import report.
- **Project repository.** Stored in IndexedDB, with mapping versions and compare, project import/export (merge or replace), workflow export, shareable links, and SVG/PNG diagram export.

## Try it

Open the live demo. The Load Sample menu has three ready-to-run mappings (retail orders, sales by region, customer dimension SCD2), and the Workflow Designer has a sample "Daily retail load" workflow. Choose **Run**.

## Tech

A single self-contained `index.html` in vanilla JavaScript. It uses DuckDB-WASM, IndexedDB and inline SVG, with no build step and no backend. Projects, run history and (optionally) source files are stored in your browser only.

It works on phones and tablets too: below 900px the side panels become drawers, palette items are added with a tap, and the canvas supports pinch zoom.

## Running it yourself

Serve the folder with any static web server (`npm run serve`, `python3 -m http.server`, GitHub Pages, ...). Opening `index.html` directly from disk also works in most browsers.

### Network access

| Host | Why | Avoidable? |
| --- | --- | --- |
| `cdn.jsdelivr.net` | DuckDB-WASM engine (pinned release) | Yes, by self-hosting (below) |
| `extensions.duckdb.org` | DuckDB's JSON extension, downloaded the first time a JSON source or target runs | No |
| `fonts.googleapis.com`, `fonts.gstatic.com` | UI fonts; system fonts are used if blocked | Optional |

Your data never leaves the browser. A Content-Security-Policy in `index.html` limits the page to these hosts.

### Self-hosting DuckDB-WASM

```sh
node scripts/vendor-duckdb.mjs        # downloads the pinned release into vendor/duckdb/ and updates index.html
node scripts/vendor-duckdb.mjs --cdn  # switch back to the CDN
```

Commit `vendor/duckdb/` (about 72 MB) and make sure your server sends `.wasm` as `application/wasm`. The CDN remains the fallback if the local copy cannot be loaded. To upgrade DuckDB, change `APP.duckdb` in `index.html` and run the script again.

## Development

```sh
npm install
npx playwright install chromium
npm test
```

`npm test` drives the real app in headless Chromium: it runs the three samples and the sample workflow on DuckDB-WASM, exports every dialect, and checks persistence across reloads, project import/export, share links, the dialogs and the phone layout. The same test runs in GitHub Actions on every push. See [CHANGELOG.md](CHANGELOG.md) for release notes.

## Keyboard shortcuts

| Shortcut | Action |
| --- | --- |
| Ctrl+Enter | Run the mapping or workflow |
| Ctrl+S | Save the mapping as JSON, or export the workflow |
| Ctrl+Shift+S | Save a version of the mapping |
| Ctrl+Shift+V | Validate |
| Ctrl+K | Command palette |
| Ctrl+Z / Ctrl+Y | Undo / redo |
| Ctrl+D, Delete | Duplicate / delete the selection |

## Author

Raghav Raman, Senior Software Engineer (data engineering)

## License

[MIT](LICENSE)
