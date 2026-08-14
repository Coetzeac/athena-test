# ATHENA historical market-data acquisition

## Authority

`ATHENA-HIST-001` implements the historical-acquisition controls already
authorized by `ATHENA-MDR-001`. It does not broaden the provider, universe,
intervals, history boundaries, usage rights, budget, or trading authority.
The controlling evidence remains `EF-002`, `EF-006`, `EF-010`, and `EF-014`.

Twelve Data's Basic-plan limits were observed on 14 August 2026 from its
[official pricing page](https://twelvedata.com/pricing): eight API credits per
minute and 800 per UTC day. The `/time_series` request weight is one credit per
symbol. These vendor facts are recorded as observed operational inputs, not as
permanent ATHENA architecture. A provider change must update the versioned
policy and tests before acquisition resumes.

## Objective and scope

The controller converts an approved historical scope into deterministic,
inclusive request windows and resumes them without losing provenance or
repeating terminal work. The full approved plan covers eight instruments, five
intervals, daily history from 2010 or inception, and intraday history from
2020. A bounded manifest may be created for controlled testing, but it cannot
claim complete approved history. [EF-014]

## Enforced process

1. `athena plan-market-history` validates the active market-data and historical
   policies, partitions the declared scope, assigns a SHA-256 request identity
   to every window, and writes a content-addressed immutable manifest.
2. `athena acquire-market-history` obtains an exclusive single-writer lock for
   the canonical quota ledger. Parallel workers using that ledger cannot both
   reserve the same provider capacity.
3. A quota reservation is appended and hash-chained before each external
   request. The observed provider limits are eight credits per minute and 800
   per UTC day. Historical acquisition uses stricter ceilings of seven per run,
   seven per minute, and 720 per day, retaining capacity for controlled smoke
   and diagnostic requests.
4. Quota exhaustion pauses before transmission. It does not sleep, retry,
   purchase capacity, or issue an unrecorded request. Sanitized
   `api-credits-used` and `api-credits-left` response-header observations are
   added to the audit ledger; a zero balance pauses below the local ceiling.
5. Accepted provider bytes and normalized bars pass through the existing
   `Dataset`, evidence-register, quarantine, and audit-ledger contracts.
6. Coverage must reach both requested boundaries within the permitted session
   tolerance. A short, capped, malformed, gapped, or out-of-period response is
   quarantined and cannot count as a completed window.
7. Each terminal window receives exactly one append-only hash-chained
   checkpoint and one matching ledger event. Accepted and exact duplicate
   Datasets are `COMPLETED`; every other result is `QUARANTINED`.
8. A quarantine stops the manifest. Remedy requires a new manifest; adverse
   checkpoints and provider bytes are not deleted or rewritten.
9. Every invocation writes a content-addressed completeness report linked to
   the ledger. Reports distinguish manifest-scope completion from complete
   approved-history completion.
10. `athena validate-market-history` verifies policy digests, manifest identity,
    checkpoint and quota chains, report bytes, and ledger reconciliation.

## Commands

Create the full approved plan through the last complete UTC day:

```bash
athena plan-market-history \
  --end 2026-08-13 \
  --manifest-root runtime/market-data/history-control
```

Resume at most seven one-credit requests:

```bash
athena acquire-market-history PATH_TO_MANIFEST \
  --objects runtime/market-data/objects \
  --reports-root runtime/market-data/history-control \
  --checkpoints runtime/market-data/history-checkpoints.jsonl \
  --quota-ledger runtime/market-data/quota-ledger.jsonl \
  --register runtime/evidence-register.jsonl \
  --quarantine runtime/market-data-quarantine.jsonl \
  --ledger runtime/ledger.jsonl
```

Validate the retained control state:

```bash
athena validate-market-history PATH_TO_MANIFEST \
  --objects runtime/market-data/objects \
  --reports-root runtime/market-data/history-control \
  --register runtime/evidence-register.jsonl \
  --quarantine runtime/market-data-quarantine.jsonl \
  --checkpoints runtime/market-data/history-checkpoints.jsonl \
  --quota-ledger runtime/market-data/quota-ledger.jsonl \
  --ledger runtime/ledger.jsonl
```

## Evidence and status semantics

| Status | Meaning | Permitted next action |
|---|---|---|
| `IN_PROGRESS` | Valid terminal work exists; capacity remains for a later run | Resume the same manifest |
| `PAUSED_QUOTA` | The run or provider quota stopped transmission before the next request | Resume after the relevant UTC reset |
| `BLOCKED_QUARANTINE` | A request failed a source, coverage, or quality control | Investigate evidence and issue a new manifest |
| `COMPLETE` | Every window in this manifest has a terminal accepted Dataset | Full scope may be validated for research; bounded scope remains control-path evidence |

`scope_complete` proves only the declared manifest. Only a `COMPLETE` report
whose manifest scope is `FULL_APPROVED_HISTORY` may set
`full_approved_history_complete` to true. `ready_for_research` additionally
requires revalidation of every retained market Dataset, fingerprint, register
link, quarantine entry, and ledger event. None of these fields authorizes a
research claim, Decision Court submission, strategy approval, broker action, or
live execution.

## Accountability and remedy

| Actor | Enforceable responsibility |
|---|---|
| Owner/CIO | Provider, licence, universe, history, budget, and any policy expansion |
| Operations Director | Canonical single-writer paths, secret availability, quota resets, storage, and quarantine response |
| Market service | Deterministic partitioning, exact requests, coverage controls, Dataset creation, and fail-closed results |
| Evidence register | Stable Dataset identities and one registration ledger link per record |
| Audit ledger | Quota, quarantine, checkpoint, ingestion, and completeness-event integrity |
| Experiment service | Use only validated Dataset IDs from a complete eligible manifest |

The controller deliberately does not commit provider bytes to the public
repository. Real acquisition therefore remains operationally blocked until a
durable private object store and canonical persistent control paths are
configured. Ephemeral CI storage is not acceptable evidence retention.

## Acceptance boundary

This increment implements and tests planning, resumability, quotas, manifests,
coverage gates, checkpoints, completeness reports, tamper detection, and ledger
reconciliation. It does not itself acquire the full provider history, configure
private production storage, create 0–100 features, implement ATH-001, or
authorize live execution. `FR-005` remains `partial`. [EF-014]
