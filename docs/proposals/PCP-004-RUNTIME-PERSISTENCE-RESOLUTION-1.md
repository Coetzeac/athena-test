# PCP-004 — Runtime Persistence Resolution 1

## Classification and authority

Protected runtime implementation approved by the Owner/CIO on 14 August 2026
as **ATHENA Runtime Persistence Resolution 1**. The immutable authority record is
`EF-015`; the frozen runtime foundation remains `EF-002` and `EF-006`.

This resolution implements `FR-011`. It changes no layer, pipeline stage,
Decision Court power, research standard, market-data scope, system-of-record
authority, or live-execution prohibition. [EF-002, EF-006, EF-015]

## Problem

Repository JSONL files and ephemeral GitHub Actions artifacts prove control
logic but not durable runtime state. They cannot establish single-writer
concurrency, immutable private byte retention, versioned backups, off-site
failure separation, or recoverability within a declared RPO and RTO. Continuing
full historical acquisition on that basis would create evidence that cannot be
proven durable. [EF-006, EF-015]

## Approved control decision

1. PostgreSQL is the canonical operational store for audit ledger entries,
   evidence records, historical manifests, checkpoints, quota reservations,
   quarantine indexes, object metadata, runtime status, and recovery records.
2. A private S3-compatible store holds content-addressed immutable source,
   dataset, manifest, chart, report, and backup bytes. Public access is
   prohibited; versioning, TLS at the external boundary, and server-side
   encryption are mandatory.
3. Redis may hold bounded coordination messages only. It may not hold a
   canonical record, retained source, ledger, credential, or unbounded payload.
4. The recovery-point objective is 60 minutes and the recovery-time objective
   is 240 minutes.
5. Backups must be encrypted and retained in a separate failure domain for at
   least 35 days. A production-ready claim requires a successful observed
   restore no older than 30 days.
6. Secrets may enter only through a runtime environment or secret store. No
   secret value may be committed to the public repository.
7. Live execution remains prohibited. [EF-015]

## Executable evidence

- `config/runtime_persistence_policy.json` records the approved mappings and
  recovery limits.
- Three closed JSON Schemas control policy, object references, and recovery
  manifests.
- Two ordered PostgreSQL migrations create the canonical tables and mutation
  rejection triggers.
- `src/athena/persistence.py` validates the policy, deterministic object keys,
  versioned encrypted S3-compatible writes, Redis limits, migration digests,
  and synthetic recovery round trips.
- `deploy/docker-compose.persistence.yml` supplies isolated development
  services with required external secrets and no host ports.
- `tests/test_persistence.py` attacks policy weakening, object tampering,
  absent versioning, Redis overreach, migration drift, default credentials,
  and false production-readiness claims.

## Acceptance conditions

1. `athena freeze-status` rejects any weakening or remapping of the approved
   persistence authorities.
2. PostgreSQL migrations are contiguous, digest-controlled, atomic through the
   migration runner, and install update/delete rejection on immutable tables.
3. S3-compatible writes fail unless retained bytes, SHA-256 metadata, AES-256
   server-side encryption, and a non-empty object version are verified.
4. Redis rejects canonical or secret fields, payloads above 65,536 bytes, TTLs
   above 86,400 seconds, invalid queues, and duplicate immutable work IDs.
5. A synthetic backup and restore reproduces the exact original bytes and
   records that it is not production recovery evidence.
6. Docker development services expose no host ports and contain no default or
   embedded credentials.
7. Full unit, freeze, configuration, and governed end-to-end tests pass.

## Accountability and remedy

| Actor | Accountability | Required remedy on failure |
|---|---|---|
| Owner/CIO | Protected mapping, RPO, RTO, budget and production acceptance | Approve any change through a new evidence-backed resolution |
| Operations Director | Secrets, canonical endpoints, backups, restore schedule and incident record | Quarantine affected writes, restore from a verified version, document loss window |
| Persistence service | Migration order, immutable writes, byte verification and store reconciliation | Fail closed; do not overwrite or silently downgrade controls |
| Evidence register and audit ledger | Identity and hash-chain continuity | Reconcile from retained immutable evidence; preserve adverse records |

## Status claim

Passing this proposal proves executable persistence contracts and a synthetic
recovery control. It does not prove that a production VPS, private bucket,
off-site backup, TLS boundary, PostgreSQL instance, or timed restore exists.
`FR-011` therefore advances from `pending` to `partial`, not `implemented`.
Production readiness and live execution remain prohibited. [EF-015]
