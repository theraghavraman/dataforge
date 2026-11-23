> Historical documentation from the supplied UI build. Not a statement of current verification or security. The root README, BUSINESS_READINESS, DEPLOYMENT, SECURITY and TEST_REPORT documents take precedence.

# DataForge — Browser ETL Studio

> **Workspace improvement build 1.6.1.** See [IMPROVEMENTS.md](IMPROVEMENTS.md) for the new interface, search, and preview controls, and [TEST_REPORT.md](TEST_REPORT.md) for verified checks and testing limits.

A visual ETL designer that runs entirely in the browser. You build mappings visually on a canvas, they compile to SQL, and DuckDB-WASM executes them locally. Nothing leaves your machine.

**Live demo:** https://theraghavraman.github.io/dataforge/

## Features

- **Mapping Designer.** Drag-and-drop canvas with CSV, JSON, Parquet and Excel sources and targets, SQL, Filter, Expression, Sorter, Joiner, Lookup, Aggregator, Router, Union, Rank, Sequence Generator, SCD Type 1, SCD Type 2, Data Quality and Target transformations.
- **In-browser execution.** Every mapping compiles to a DuckDB SQL CTE chain and runs on CSV/JSON files through DuckDB-WASM, with data preview at any node.
- **Warehouse patterns.** SCD1/SCD2 with hash-based change detection, surrogate keys, reject handling, and data quality rules (reject, flag or warn).
- **Parameters and variables.** `$$PARAM` syntax, with persistent mapping variables for incremental loads.
- **Code generation.** Export any mapping as Snowflake SQL (MERGE-based SCD), Google BigQuery, Databricks SQL or Amazon Redshift SQL, a runnable PySpark script, or a complete dbt project (models, snapshots, schema tests).
- **Column-level lineage.** Impact analysis, plus Source-to-Target Mapping (STTM) export and Markdown mapping specifications.
- **Workflow Designer.** Sessions, decisions, assignments, timers, notifications and conditional links, with concurrent branches.
- **Monitor.** Run history, Gantt timeline, session statistics, trend charts and run comparison.
- **Mapping XML import.** Best-effort conversion of repository mapping XML exports, with an import report.
- **Testing and quality.** Mapping tests with fixed input and expected rows, data profiling at any node, and data contracts that check outputs before they are written.
- **Governance.** Personal-data detection and classification, masking in previews, a data inventory, and project-wide lineage and impact analysis across chained mappings.
- **Reuse and orchestration.** Reusable components, and Airflow DAG export of workflows.
- **Projects.** A Project section at the top of the left panel shows the open project, lets you rename it, and tells you whether your project file is up to date. **Save** writes the whole project to one `.dfproj` file; **Open** brings it back in any browser, including a fresh session (see [Project files](#project-files)).
- **Project repository.** Stored in IndexedDB, with mapping versions and compare, project merge or replace, workflow export, shareable links, and SVG/PNG diagram export.

## Try it

Open the live demo. The welcome screen and Load Sample menu have three ready-to-run mappings (retail orders, sales by region, customer dimension SCD2), and the Workflow Designer has a sample "Daily retail load" workflow. Choose **Run**.

## Tech

A single self-contained `index.html` in vanilla JavaScript. It uses DuckDB-WASM, IndexedDB and inline SVG, with no build step and no backend. Projects, run history and (optionally) source files are stored in your browser only.

It works on phones and tablets too: below 900px the side panels become drawers, palette items are added with a tap, and the canvas supports pinch zoom.

## Project files

Your work is saved automatically in the browser you are using. To move it to another browser, another computer or a fresh session, choose **Save** in the Project section. It downloads one `.dfproj` file that holds everything needed to carry on where you stopped:

| Included | Details |
| --- | --- |
| Mappings | Every node, link, expression, condition, port and setting, plus parameters and the current values of persistent variables. |
| Workflows | Tasks, links, conditions, schedules and workflow variables. |
| Parameter sets | All sets and values. |
| Versions | Every saved mapping version, so compare and restore still work. |
| Source data | The CSV and JSON file loaded into each source, in every mapping, whether or not "Remember source file on this device" is ticked. Built-in sample data is not stored; it is re-attached automatically. |
| Run history | The runs shown in the Monitor. |
| Session | The mapping or workflow that was open, the designer mode and the run counter. |
| Tests, components, governance | Mapping tests, reusable components and column classifications. |

Open the file with **Open** in the Project section, the toolbar Open button, or by dropping it onto the page. In an empty session it opens straight away; otherwise you choose Merge (add it next to your work) or Replace (a backup `.dfproj` of the current project is downloaded first). Restored data files are remembered on the new device, so they also survive a reload there.

A project file can be **password-protected** (project menu ⋯ → Protect with a password). It is then encrypted with AES-256-GCM; without the password it cannot be opened or read, and a forgotten password cannot be recovered.

The project menu (⋯) also has **New project**, **Save without source data** (logic only, for large data) and **Export logic only**, which writes the readable, diff-friendly `.dfproj.json` used by earlier versions. Both formats can be opened.

A `.dfproj` file is gzip-compressed JSON (`{ format: "dataforge-project", formatVersion, project, files, runs, session }`, with source files base64-encoded), so it can be inspected with standard tools, for example `gunzip -c my_project.dfproj | jq .project`. It contains your source data: share it only with people who may see that data.

## Running it yourself

Serve the folder with any static web server (`npm run serve`, `python3 -m http.server`, GitHub Pages, ...). Opening `index.html` directly from disk also works in most browsers.

### Network access

| Host | Why | Avoidable? |
| --- | --- | --- |
| `cdn.jsdelivr.net` | DuckDB-WASM engine (pinned release) | Yes, by self-hosting (below) |
| `extensions.duckdb.org` | DuckDB's JSON and Parquet extensions, downloaded the first time a JSON or Parquet source or target runs | No |
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

`npm test` drives the real app in headless Chromium: it runs the three samples and the sample workflow on DuckDB-WASM, runs JSON sources and targets (array and newline-delimited), exports every dialect, and checks persistence across reloads, saving a `.dfproj` project file and opening it in a fresh browser context, project import/export, share links, the dialogs and the phone layout, and runs an axe-core accessibility check over the main views in both themes.

### Generated code is executed, not just generated

```sh
pip install -r tests/codegen/requirements.txt   # DuckDB, PySpark (needs Java 17+), dbt, sqlglot
npm run test:codegen
```

This exports the code for the three samples, plus a coverage mapping that uses every transformation the samples do not (joiner, router, union, sequence, rank, SQL) with awkward values, and compares every target with the file the app itself produced:

| Export | How it is checked |
| --- | --- |
| DuckDB SQL | Executed in native DuckDB. |
| PySpark | Executed on a local Spark session. |
| Snowflake SQL | Parsed as Snowflake SQL, every column resolved under Snowflake's identifier rules, and the logic executed on DuckDB through a sqlglot translation. |
| dbt | `dbt compile`, then the same three Snowflake checks on the compiled models. |
| BigQuery, Databricks SQL, Redshift | Parsed in the warehouse's own dialect, checked for leftover DuckDB-only syntax, and the logic executed on DuckDB through a sqlglot translation, with each cloud load (`LOAD DATA`, `USING CSV`, `COPY`) replaced by the sample file read as text. |

No cloud account is involved, so the Snowflake, dbt, BigQuery, Databricks and Redshift checks cover syntax and logic, not warehouse-specific runtime behaviour such as permissions, quotas or storage paths: review those exports before running them in production. The coverage mapping is not compared for Snowflake, whose export numbers sequence keys with `NEXTVAL`, which does not follow `ORDER BY` (the export warns about this). Generated Snowflake and dbt SQL uses upper-case column and table names, the Snowflake default.

Both test suites run in GitHub Actions on every push. See [CHANGELOG.md](CHANGELOG.md) for release notes.

## Keyboard shortcuts

| Shortcut | Action |
| --- | --- |
| Ctrl+Enter | Run the mapping or workflow |
| Ctrl+S | Save the mapping as JSON, or export the workflow (use **Save** in the Project section for the whole project) |
| Ctrl+Shift+S | Save a version of the mapping |
| Ctrl+Shift+V | Validate |
| Ctrl+K | Command palette |
| Ctrl+Z / Ctrl+Y | Undo / redo |
| Ctrl+D, Delete | Duplicate / delete the selection |

On a Mac, use ⌘ in place of Ctrl.

### Without a mouse

Every mapping node and workflow task can be reached with Tab.

| Key (on a focused node) | Action |
| --- | --- |
| Arrow keys | Move the selection (Shift moves further) |
| Enter | Edit the node in the Properties panel |
| Menu key or Shift+F10 | Open the node menu: preview, profile, rename, **Connect to …** another node, duplicate, delete |
| Delete | Remove the node and move to the next one |
| Escape | Clear the selection, or close a menu |

In the results drawer, the arrow keys, Home and End move between tabs.

## Author

Raghav Raman, Senior Software Engineer (data engineering)

## License

[MIT](LICENSE)
