# Changelog

## 1.4.0

### Added
- **Project lineage and impact** (Project menu ⋯ → Project lineage and impact). Mappings are joined through the files they share: a target that writes `clean.csv` feeds every mapping whose source reads `clean.csv`. Pick a file and column to see everything it feeds downstream, or everything it comes from, across all mappings, with the workflows affected and a flow diagram. Exports as CSV.
- **Governance and privacy.** Columns holding personal data (email, phone, card number, national ID, date of birth, IP address, name, address) are detected automatically and can be marked sensitive or not by hand. Columns derived from them are listed as inheriting the risk. Optionally masks sensitive values in Data Preview and Profile. Exports a data inventory CSV.
- **Data contracts on file targets.** List the columns an output promises, with type, Required and Unique. Checked on every run before writing: *Fail* writes nothing and fails the step, *Warn* writes and reports.
- **Airflow export** (Workflow settings → Export as Airflow DAG). A zip with the DAG, the PySpark job of every mapping it runs, and a README. Sessions, timers, notifications and control tasks are translated; link conditions become trigger rules; the schedule carries over. Anything that cannot be translated is listed for review.

## 1.3.0

### Added
- **Parquet and Excel sources and targets.** Excel keeps numbers, dates and booleans as real values, lets you pick the sheet, and freezes the header row on output. Parquet, like JSON, is a DuckDB extension downloaded from extensions.duckdb.org on first use.
- **Data profiling** (right-click a node → Profile data, or the Profile tab): fill rate, distinct values, range, mean and most common values for every column, with hints for likely keys, constant or empty columns and personal data.
- **Mapping tests** (Tests tab). A test feeds fixed input rows through the mapping and compares the result with expected rows. "New test from current data" records today's behaviour so later changes that alter the output are caught. Tests are saved with the mapping and the project file.
- **SQL transformation**: a hand-written SELECT over one or two inputs (`input`, `input2`), with column types detected and exports to every dialect.
- **Reusable components**: save selected nodes as a component and insert them into any mapping from the Repository.
- **Password-protected project files** (Project menu ⋯ → Protect with a password): AES-256-GCM with a PBKDF2-SHA-256 key (600,000 iterations). The password is kept in memory for the tab only.

### Fixed
- Exported PySpark jobs with an Excel target failed when the output folder did not exist yet.

## 1.2.0

### Added
- **Project section** at the top of the left panel: the project name (click to rename), what it contains, and whether your project file is up to date (not saved yet, changed since saving, or up to date). Save and Open sit right there; the ⋯ menu has New project, Save without source data and Export logic only.
- **`.dfproj` project files.** Save writes the whole project to one compressed file: mappings, workflows, parameter sets, versions, the source data loaded into every mapping, run history and the mapping or workflow that was open. Opening it in a fresh browser session restores all of it, so mappings and workflows run straight away without uploading any file again. Restored data is remembered on the new device.
- **New project**, which offers to save the current project to a file first.
- **Drop a project file** onto the page to open it.
- Smoke test: save a project file, open it in a brand-new browser context, run the restored mappings and workflow, reload, and run again.

### Changed
- Replacing a project now downloads the backup as a `.dfproj` file that includes source data and run history.
- The Repository panel's Export Project / Import buttons moved to the Project section. The `.dfproj.json` format is still written by Export logic only and the Workflow Designer, and is still read everywhere.
- The browser tab title shows the project name.

### Fixed
- **An uploaded source file was lost when you opened another mapping** unless "Remember source file on this device" was ticked. Uploads are now kept for the whole session, and workflows can use them too.

## 1.1.3

### Changed
- **Opens in the dark theme again.** Light is used only if you switched to it before on this device; the theme button still toggles.

## 1.1.2

### Changed
- **Opens in the light theme.** Dark is used only if you switched to it before on this device; the theme button still toggles.

## 1.1.1

Found by running the generated code instead of only generating it.

### Fixed
- **PySpark export crashed on its first line of logic.** Parameters were written as JSON (`null`, `true`, `false`), which is not Python. Every generated script failed with `NameError: name 'null' is not defined`.
- **PySpark SCD Type 2** failed with an unresolved `__change_type` column.
- **PySpark targets** are written to a folder named after the chosen format (`orders.parquet`), not to a Parquet folder called `orders.csv`.
- **Snowflake and dbt: column names did not resolve.** Generated SQL quoted names in lower case (`"qty"`) while expressions used them unquoted (`qty`, which Snowflake reads as `QTY`). Quoted names are now upper-case throughout, the Snowflake default.
- **Snowflake and dbt: regular expressions lost their backslashes** (`'\s'` is `'s'` in a Snowflake string), so a "valid email" rule rejected every row. Backslashes in string literals are now doubled.
- **Snowflake and dbt: `regexp_matches` became `REGEXP_LIKE`**, which must match the whole value. It is now `REGEXP_INSTR(...) > 0`, which finds the pattern anywhere, as DuckDB does.
- **dbt: `strftime` became Spark's `date_format`**, which Snowflake does not have. It is now `TO_VARCHAR` with a Snowflake format.

### Added
- **Generated-code test** (`npm run test:codegen`, also in GitHub Actions): executes the DuckDB SQL and PySpark exports of the three samples and checks the Snowflake and dbt exports by parser, name resolution and a DuckDB translation, comparing every target with the app's own output.
- **JSON in the smoke test:** JSON array and newline-delimited sources and targets now run in CI, including the download of DuckDB's JSON extension.

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
