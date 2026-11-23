# Portable server pipeline contract

## Supported path

The browser designer's native project/mapping/node/link structure is preserved. The server compiles one selected target's upstream graph, not every mapping or workflow in the project. Unsupported nodes outside that selected path are not executed. Cycle detection and cardinality checks reject invalid paths. A run records project ID, exact revision, project checksum, mapping, target, SQL and explicit source file/checksum lineage.

The 13 supported node types are `csv_source`, `filter`, `expression`, `sorter`, `aggregator`, `joiner`, `union`, `rank`, `router`, `csv_target`, `json_target`, `preview_target` and `reject_target`. Joins include inner/left/right/full with source aliases `m` and `d`; duplicate detail-column names receive `d_` prefixes. Union aligns columns by name and fills absent values with NULL. Ranking supports row number/rank/dense rank, partitions and top/bottom selection. Router groups and reject paths are explicit.

Inputs must be uploaded workspace CSVs, UTF-8, comma-delimited with a header. The source node's saved column names must match the file header. CSV sources are not automatically opened from arbitrary URLs, local paths or another workspace. Missing bindings are errors.

## Types and numeric semantics

Saved text/string/VARCHAR ports map to SQLite TEXT. INTEGER/INT/BIGINT and related signed integer declarations map to signed 64-bit integers. DOUBLE/FLOAT/REAL map to finite floating-point numbers. BOOLEAN accepts true/false/1/0. DATE requires YYYY-MM-DD. TIMESTAMP must parse as an ISO timestamp and is stored as text. Blank typed numeric/date/boolean fields become NULL; blank text remains an empty string. Inputs without saved types remain text and generate an explicit warning. Invalid typed values fail execution rather than silently turning into zero.

This is **SQLite semantics, not DuckDB semantics**. There is no implicit promise of equivalent collations, timestamp comparisons, casts, rounding, NULL handling or aggregate behavior. Mixed-offset timestamp text is not normalized to UTC. Floating-point amounts are not an exact-decimal accounting type; use integer minor units with tested business rules. DECIMAL, timezone-aware/native DuckDB types and arrays are not supported. Review aggregate expressions: SQLite may accept a bare non-grouped field that another SQL engine rejects.

The compiler rejects known ambiguous dialect expressions, comments, statement separators, project `$$` variables and unsupported features. This is conservative validation, not a complete SQL parser or semantic proof. An expression not covered by the supported subset can still fail in the restricted runner. Review and test every business mapping against independently expected outputs.

## Review, approval and immutable scheduling

An editor or owner selects **Run pipeline**, binds files, compiles and reviews the SQL, and explicitly confirms the source/semantics review before queueing. The API still independently validates all submitted values; the checkbox is a UI review aid, not a server authorization boundary.

Owners can require a second workspace owner to approve each exact project revision. The approving owner must differ from the revision's last saver. New revisions need new approval. Approval is rechecked at submission, schedule firing, execution and successful completion. Revoking approval blocks pending/future runs; results of a running job are withheld if the approval is no longer valid at completion.

**Known limitation: policy changes are single-owner.** Any workspace owner can switch the approval requirement off and on without a second owner. While it is off, that owner can run an unapproved revision. Every change is recorded as a `project.approval_policy` audit event, so the hash-chained audit log makes such a bypass visible but does not prevent it. Where separation of duties is a hard control, review those events routinely and keep the number of owners small. A two-person rule for policy changes is not implemented.

This policy applies to **visual-pipeline jobs and their schedules**. It is not a global approval gate on the separate SQL-job capability that editors already possess. Use viewer access for users who must not submit SQL. There are no per-project ACLs or a separate approval-only role.

Pipeline schedules store an immutable compiled snapshot and the source checksums, not a moving pointer to the newest project. They start paused. An updated project does not silently alter an existing schedule; create a reviewed new schedule and disable the old one. Source files referenced by active work are protected against deletion. Ordinary failed jobs are not retried automatically; a deliberate Retry preserves the original payload/lineage, rechecks current authorization/approval and uses a new idempotency key. Expired worker leases have bounded recovery for read-only execution.

## Resource envelope

Defaults: 5 MiB per CSV, 100 MiB uploaded source data per workspace, 2 MiB per project document, 8 sources per job, 200 nodes per compiled mapping, 64 KiB SQL, 30-second job wall-clock timeout, 768 MiB child address-space bound, 10,000 result rows, 5 MiB result output, 100 queued/running jobs and 100 schedules per workspace. Exceeding output limits fails the job; no success-labelled partial result is stored. Some limits are settings rather than a user-facing quota control.

These are enforced guardrails, not demonstrated production capacity. The delivered load evidence is a bounded synthetic experiment: 48 submissions, 24 unique jobs, 8 concurrent HTTP threads and 2 independent workers, each processing 10,000 CSV rows. Qualify your own workloads and concurrency.

## Explicit exclusions

SCD1/SCD2, sequence generators, lookups, Excel/Parquet/JSON sources, unrestricted SQL-transform nodes, workflow scripting, remote connectors and arbitrary filesystem targets are not implemented by this compiler. The original browser designer can contain these features, but they are rejected in the server path rather than silently approximated. Browser DuckDB binaries/extensions are not bundled or validated in this release.
