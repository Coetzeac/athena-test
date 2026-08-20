# ATHENA runtime persistence

## Authority

`ATHENA-RPR-001` is the executable form of the Owner/CIO-approved Runtime
Persistence Resolution 1. PostgreSQL, S3-compatible object storage, and Redis
have separate authority and may not substitute for each other. [EF-006, EF-015]

| Store | Permitted authority | Prohibited use |
|---|---|---|
| PostgreSQL | Canonical operational state and record indexes | Unversioned source-byte replacement |
| Private S3-compatible store | Immutable content-addressed runtime bytes and backups | Public objects, mutable canonical keys, missing version IDs |
| Redis | Short-lived queues and cache entries | Evidence, ledgers, source bytes, credentials, permanent state |

The exact policy is `config/runtime_persistence_policy.json`. Every material
runtime-persistence claim must cite `EF-015` and remain subordinate to the
engineering freeze.

## Canonical mapping

PostgreSQL owns the audit ledger, evidence register, historical manifests,
historical checkpoints, quota reservations, quarantine index, object catalog,
runtime status, and recovery catalog. Its migrations are ordered under
`migrations/postgres/` and record their filename and SHA-256 digest. Applied
migration bytes may not be changed retrospectively.

The object store owns immutable bytes in keys of the form:

```text
objects/<approved-kind>/sha256/<first-two-digest-characters>/<full-sha256>
```

An accepted reference records the bucket, key, version ID, content length,
content type, digest, and server-side encryption. A write is not successful
until the retained version is read back and its bytes reproduce the declared
digest. [EF-002, EF-015]

Redis keys are derived from canonical transient payload bytes. Every item has a
TTL of no more than 86,400 seconds and a payload no larger than 65,536 bytes.
Redis failure may delay work; it may never erase the authoritative evidence.

## Development services

`deploy/docker-compose.persistence.yml` provides isolated PostgreSQL 16, Redis
7, MinIO, and a one-shot pinned MinIO client bootstrap. The bootstrap creates
the declared private bucket, enables object versioning, and removes anonymous
access. The stack has no host port mappings and accepts
credentials only from required environment variables. The versions were pinned
from the official PostgreSQL and Redis image catalogs and the published MinIO
container release observed on 14 August 2026.

PostgreSQL development initialization runs both approved migration files in one
`ON_ERROR_STOP` transaction and records their policy-pinned SHA-256 digests. A
partial initialization or changed migration therefore fails instead of leaving
an apparently current schema.

The MinIO containers exist solely for local contract development. Their presence
does not select a production provider or establish off-site durability. The
latest MinIO security release requires a separately built container; production
image selection therefore remains an Operations Director deployment control,
not an assumption embedded in this public repository.

## Commands

Install the pinned production adapters only in the controlled runtime image:

```bash
python -m pip install -e ".[persistence]"
```

The pinned adapter set is Psycopg 3.3.4, Boto3 1.43.72, and redis-py 8.1.0,
observed from their official PyPI releases on 14 August 2026. Base kernel and CI
tests remain dependency-free and use injected fakes; this prevents external
services from being mistaken for unit-test evidence.

Validate the policy, schemas, migrations and development-compose boundary:

```bash
athena validate-runtime-persistence
```

Run the synthetic byte-for-byte backup/restore proof:

```bash
athena prove-runtime-recovery \
  examples/recovery/synthetic_runtime_backup.json \
  --store-root /tmp/athena-recovery-proof
```

The proof output must state:

- `synthetic_control_proof: true`;
- `production_restore_observed: false`;
- `production_ready: false`; and
- `live_execution: prohibited`.

## Production gate

Production acceptance requires all of the following evidence:

1. An approved VPS/provider and private endpoints in a separate deployment
   record; no credentials in GitHub.
2. PostgreSQL migrations executed and reconciled by digest.
3. Object-store versioning, public-access block, TLS, encryption, and retained
   byte verification observed against the selected endpoint.
4. Encrypted backups in a separate failure domain with at least 35-day
   retention.
5. A restore completed within 240 minutes from a recovery point no older than
   60 minutes, observed within the preceding 30 days.
6. Post-restore ledger, evidence-register, object-catalog, manifest, checkpoint,
   quota, quarantine, and status reconciliation.

Failure of any gate leaves `FR-011` partial and the runtime `BLOCKED_EXTERNAL`.
No synthetic proof or development container may be presented as production
evidence. [EF-015]

## Incident remedy

Stop new acquisition, preserve the failed version and logs, identify the last
verified terminal hashes and object versions, restore only into an isolated
environment, rerun all reconciliation, and record the observed loss window and
restore duration. Overwriting adverse evidence or resetting an immutable table
is prohibited. The Owner/CIO approves any control change; the Operations
Director owns execution and the incident record.
