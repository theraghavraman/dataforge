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
- **Project repository.** Stored in IndexedDB, with mapping versions and compare, project import/export, shareable links, and SVG/PNG diagram export.

## Try it

Open the live demo. The Load Sample menu has three ready-to-run mappings (retail orders, sales by region, customer dimension SCD2), and the Workflow Designer has a sample "Daily retail load" workflow. Choose **Run**.

## Tech

A single self-contained `index.html` in vanilla JavaScript. It uses DuckDB-WASM (loaded from jsDelivr), IndexedDB and inline SVG, with no build step and no backend.

## Author

Raghav Raman, Senior Software Engineer (data engineering)

## License

[MIT](LICENSE)
