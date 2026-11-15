# DataForge v1.1.0 — Browser ETL Studio

[Live demo](https://theraghavraman.github.io/dataforge/) · [Source code](https://github.com/theraghavraman/dataforge) · [Changelog](CHANGELOG.md)

DataForge is a visual ETL designer that runs in a browser. Design mappings on a canvas, compile mappings to SQL, and execute them locally using DuckDB-WASM. The application itself is a **single `index.html` file with no build step and no backend**.

## Features

- **Mapping designer:** Source, Filter, Expression, Sorter, Joiner, Lookup, Aggregator, Router, Union, Rank, Sequence Generator, SCD Type 1, SCD Type 2, Data Quality, and Target transformations.
- **In-browser execution:** SQL CTE compilation and DuckDB-WASM execution, including CSV and JSON source handling and node-level data previews.
- **Warehouse patterns:** SCD1/SCD2, hash-based change detection, surrogate keys, rejects, and configurable data quality rules.
- **Parameters and variables:** `$$PARAM` syntax and persistent mapping variables for incremental processing.
- **Code export:** DuckDB SQL, Snowflake SQL, PySpark, and dbt project output.
- **Lineage and documentation:** Column-level lineage, impact analysis, Source-to-Target Mapping (STTM), and Markdown mapping specifications.
- **Workflow designer:** Sessions, decisions, assignments, timers, notifications, conditional links, and concurrent branches.
- **Run monitor:** History, execution timeline, statistics, trends, and run comparison.
- **Repository:** IndexedDB project storage, mapping versions, project import/export, share links, and SVG/PNG diagram export.
- **Mapping XML import:** Best-effort conversion with an import report.

## Getting started

Open the [live demo](https://theraghavraman.github.io/dataforge/). Use **Load Sample** to choose retail orders, sales by region, or customer dimension SCD2. The workflow designer includes a sample **Daily retail load** workflow. Choose **Run** after DuckDB has initialized.

To serve the single-file application yourself, place `index.html` on a static HTTP host, or run from the repository directory:

```sh
python3 -m http.server 8000
```

Then visit <http://localhost:8000>. An internet connection is currently required for first-time loading of DuckDB-WASM from jsDelivr, and may be needed to load DuckDB extensions. Source file bytes are processed in the browser rather than sent to a DataForge backend; loading third-party dependencies still causes network requests to their hosts.

## Saving and sharing

- **Mappings:** Ctrl/Cmd+S downloads the current mapping as JSON. Load Sample creates/opens a new repository mapping rather than replacing your current work.
- **Workflows:** Changes are saved to the browser's local project repository. Ctrl/Cmd+S in workflow mode currently saves to that repository **without downloading a standalone workflow file**.
- **Projects:** Use **Repository → Export Project** for a `.dfproj.json` backup. Import offers **Merge**, **Replace**, or **Cancel**. Replace initiates a backup download before replacement.
- **Source files:** Source bytes are not included in mapping, project, or share-link JSON. Bundled samples reattach their own sample bytes after reload; user-provided files must be remembered in IndexedDB or selected again.
- **Sharing:** Share links contain compressed mapping definitions, **not** uploaded source file bytes. Treat links as untrusted input and share only with people you intend to receive the mapping.

Browser storage may be cleared by the browser/user. Export a project backup before clearing site data or replacing the project.

## Tests and CI

The application has no build step. Tests and CI are optional development tooling and do not change the single-file deployment.

```sh
python3 -m pip install pytest playwright
python3 -m playwright install chromium
python3 -m pytest tests/ -q
```

The test suite includes a source/syntax check and Playwright browser smoke checks for sample loading/reload, workflow creation, and project export. The browser tests do **not** establish full ETL engine correctness or validate generated Snowflake/PySpark/dbt code; they also do not currently exercise JSON-extension-backed execution. GitHub Actions runs these checks on pushes and pull requests.

## Known limitations (v1.1.0)

- DuckDB-WASM `1.32.0` is loaded from jsDelivr without self-hosted fallback or subresource integrity; a verified CSP has not yet been configured.
- JSON execution via DuckDB's JSON extension has not been independently validated for this release.
- Some older modal dialogs do not fully trap keyboard focus.
- Under 900px, the mobile layout is view-only.
- Ctrl/Cmd+S in workflow mode does not export a workflow file.
- A scheduled workflow temporarily navigates to the running workflow; scheduling only works while the tab remains open.
- Code generation has been exercised against sample mappings, but generated SQL/PySpark/dbt requires review and execution in its target environment.

## Technology

Vanilla JavaScript, inline CSS and SVG, DuckDB-WASM, IndexedDB. No application build process or server backend.

## License

[MIT](LICENSE)
