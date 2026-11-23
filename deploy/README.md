# Single-host deployment package

These are **unactivated templates**, not evidence of a production server. Local API/worker processes, actual loopback TLS and local TLS SMTP were tested; systemd, Caddy, Docker image building, real DNS/certificates and external provider accounts were not available here.

## Recommended host layout

A dedicated `dataforge` service identity, code and reviewed virtual environment under `/opt/dataforge-team`, private data under `/var/lib/dataforge`, local encrypted backup archives under `/var/backups/dataforge`, and secrets/configuration under `/etc/dataforge`. Mode 0700 on data/key directories and 0600 on key/config files are the starting policy. Use encrypted host storage. All three services must use the same local SQLite database; do not place SQLite WAL on an unqualified network filesystem or run these templates across multiple hosts.

Copy `production.env.example` to the restricted environment file and replace placeholders. There is no auto-loading `.env` magic: systemd's EnvironmentFile, the container env_file or explicit shell exports supply configuration. Do not commit the filled environment file. Bootstrap the schema and administrator as the service user. Provision and independently back up both application and backup keys outside the data directory. Protect any qualification file from unreviewed modification.

## Qualification before activation

Install the patched deployment requirements in a new environment. `python scripts/qualify.py` runs dependency/automated checks, including the browser-origin gate. Collect real-provider delivery, off-host recovery, security, capacity, storage-encryption and business acceptance evidence. `qualification.example.json` has only pending states; it is not a deployable approval. Replace them only with reviewed evidence references and named/date-stamped approvals.

Run `python -m app.cli preflight` with the production environment. The API, worker and maintenance all refuse production startup if gates fail. Do not set development mode to work around a production failure. The rc1 QA image's FastAPI 0.128.2 intentionally fails the patched-version gate; the rc2 QA environment resolved FastAPI 0.165.0, which passes it.

## systemd + Caddy

Review and install `dataforge-api.service`, `dataforge-worker.service` and `dataforge-maintenance.service`. Their read/write paths must exist before service startup. `ProtectSystem=strict` intentionally prevents writing keys/config during ordinary service operation; provision them beforehand. Set the private environment file, then use your normal systemd daemon-reload/enable/start/change-control process.

`Caddyfile.example` terminates HTTPS and proxies only to loopback API port 8080, preserving the original approved Host. Set the real domain in the Caddy service environment, configure DNS/firewalls and validate with your installed Caddy build. Permit external 443 and required certificate issuance traffic; do not publicly expose API port 8080. The example suppresses access logging rather than leaking OIDC codes in query paths; use reviewed query/token redaction before enabling logs. Validate the proxy/body/time limits and real browser origin after activation.

**Client addresses behind the proxy (required).** Set `DATAFORGE_TRUSTED_PROXIES` to the exact address the proxy connects from: `127.0.0.1` for the host Caddy layout, or the Docker bridge gateway (commonly `172.17.0.1`; confirm with `docker network inspect bridge`) for the compose template. The API then reads `X-Forwarded-For` only from that address; wildcards and host names are refused. Caddy 2.5+ discards client-supplied forwarding headers by default and sends the real client address, so do not strip `X-Forwarded-For` in the proxy. Without this setting every user shares the proxy's address: in rc1 about sixty sign-ins per fifteen minutes across the whole organization exhausted the shared per-IP bucket and locked everyone out. Production preflight now fails (`client_addresses`) until it is set. Edge rate limiting remains recommended in addition.

## Container alternative

`Dockerfile` and `compose.example.yaml` provide a non-root, read-only-rootfs, capability-dropped template. They share one host data directory and use tmpfs for temporary child work. The API publishes only on host loopback, so the host reverse proxy still terminates HTTPS. Host directories and secret bind mounts must be owned/readable by UID 10001 and use the expected permissions. Provision directories/keys before starting containers; absent bind files can otherwise become directories. Configure cloud access via your approved workload identity method, not source-embedded access keys.

From the project root, review:

```bash
docker compose -f deploy/compose.example.yaml config
docker compose -f deploy/compose.example.yaml build
docker compose -f deploy/compose.example.yaml run --rm api python -m app.cli preflight
```

The template's `python:3.13-slim` tag is not a supply-chain pin. Replace it with an approved digest and locked/audited dependencies before release. Compose YAML was parsed here; Docker/Caddy binaries are unavailable and no image build or service activation is claimed. Container networking/resource limits do not turn the SQLite runner into a general arbitrary-code sandbox.

## After deployment

Confirm administrator MFA, allowed/denied workspace actions, exact-origin browser login/reload/logout, actual worker and maintenance heartbeats, a known synthetic pipeline, paused schedule review, real alert receipt and an off-host restore drill with both keys. Record deployment identity, versions, artifact checksum, approvers and recovery evidence. Independently monitor the host and maintenance service itself.
