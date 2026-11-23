# Security scope and limitations

## Intended scope

A local, single-user ETL design and evaluation tool. This build contains no accounts, identity verification, server permissions, tenant isolation, durable remote jobs or central backup service. There has been no penetration test or compliance certification of this build.

## Sensitive data

IndexedDB project records, remembered files and recovery checkpoints are not encrypted by the application. Parameters and embedded mapping-test fixtures can contain sensitive values. Password protection applies only to the exported project file. Browser preview masking is a display feature, not data removal or access control. Do not store passwords, API credentials or production secrets in parameters.

A full project export can include source data and run history. Removing uploaded source files from an export does not remove parameters, test fixtures or every other sensitive value. Inspect and classify exports before sharing. Logic checkpoint deletion does not erase external exports or other stored copies.

## Untrusted content and execution

Project files and shared mappings can contain SQL and workflow logic. Import only trusted content, review it, validate it, and test it on approved data before running. Structural import checks and encryption-envelope limits reduce malformed-input risks; they do not make arbitrary SQL safe. XML conversion is an inherited best-effort importer, not a proven secure import pipeline. The full import surface and engine execution boundary still require security review.

No scheduled workflow is armed automatically by the new build. Arming is per tab and is not a replacement for a managed production scheduler. Running manually still executes the reviewed mapping/workflow using the loaded engine and data.

## Network and dependency boundary

In normal mode, runtime/extension dependencies may be fetched from external providers. Remote font loads have been removed. No claim is made that arbitrary imported SQL can never trigger network access. Local-only mode removes page-level external allowances and engine CDN fallback, but engine worker behavior and extensions must be qualified separately. Enforce approved egress outside the page when the business requires it.

The static host limits served paths and emits defensive headers. These controls do not authenticate a user or authorize project-level actions. Changing hosts can remove them. Do not expose the static host directly to the public Internet as a complete enterprise deployment.

## Local operational records

Activity contains fixed codes and timestamps, bounded to 200 entries. It is editable local state with no verified user identity, remote durability or tamper-resistant retention. It must not be presented as a compliance audit trail. Diagnostics intentionally exclude names, source rows, SQL, parameter values and file paths, but should still be reviewed before being sent to support.

Checkpoint digests detect accidental changes; an attacker who can edit both the checkpoint and digest can replace them. A SHA-256 artifact manifest likewise detects changes only against a separately trusted copy; it is not a digital signature.

## Reporting and response

No managed security support channel or response SLA is provided by this package. For an internal deployment, designate an owner and incident contact before use. Retain sanitized diagnostics and test cases; do not attach live customer data or secrets to a public issue. Rotate exposed credentials through their actual provider, preserve relevant evidence, and follow the organization's incident process.

General background: OWASP HTML5 Security Cheat Sheet and Content Security Policy Cheat Sheet; MDN storage quotas and eviction criteria. Links are listed in BUSINESS_READINESS.md.
