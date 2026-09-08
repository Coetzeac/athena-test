# PCP-005 — Idempotent Cycle Control 1

## Classification and authority

Protected daily-progress and continuous-cycle control approved by the Owner/CIO
on 20 August 2026 as **ATHENA Idempotent Cycle Control 1**. The immutable
approval record is `EF-016`; the evidence-first, continuous-operation, frozen
runtime, and reporting authorities remain `EF-002`, `EF-004`, `EF-005`, and
`EF-012`.

This proposal controls repeated execution of the existing governed cycle. It
does not change the seven layers, pipeline order, Court gates, research scale,
08:00 Daily Progress report, or live-execution prohibition. [EF-002, EF-004,
EF-005, EF-012, EF-016]

## Problem

The hourly workflow previously created a new time-stamped Court result and
validation record when its versioned request, policies, and implementation had
not changed. Those records contained no new research evidence and produced
runtime-only commits. This obscured genuine progress and made exact replay
harder to distinguish from duplicate processing. [EF-002, EF-004, EF-012]

## Approved control decision

1. The hourly `17 * * * *` schedule remains active and validates the controlled
   runtime state on every invocation.
2. The cycle identity is a SHA-256 manifest covering the exact research-request
   bytes, Decision Court policy bytes, idempotent-cycle policy bytes, and
   digests of the governed implementation files.
3. A matching identity may return `NO_CHANGE` only after the hash-chained
   ledger, evidence register, ledger links, and persisted status bindings all
   validate.
4. `NO_CHANGE` appends zero ledger records, appends zero register records,
   writes zero status bytes, and therefore creates no workflow commit.
5. A changed identity runs the complete existing Decision Court cycle and
   records `EXECUTED` with the exact input manifest.
6. Missing or inconsistent controlled state fails closed. It may not be
   repaired by appending another cycle over the inconsistency.
7. Reuse of an already recorded Court verdict is not a Court bypass: the prior
   verdict remains bound to the same exact functional input. Any governed change
   forces readjudication. Court bypass and live execution remain prohibited.
   [EF-005, EF-016]

## Executable evidence

- `config/idempotent_cycle_policy.json` is the closed approved control.
- `schemas/idempotent-cycle-policy.schema.json` publishes its data contract.
- `src/athena/idempotency.py` validates the policy, builds the input identity,
  reconciles state, and returns the non-persisted `NO_CHANGE` view.
- `src/athena/orchestrator.py` performs the control check before any append and
  records the identity on a changed-input execution.
- `src/athena/cli.py` exposes the outcome and includes the control in
  `athena freeze-status`.
- `tests/test_idempotency.py` proves byte-for-byte no-change behavior,
  changed-policy readjudication, tamper failure, and policy weakening rejection.

## Acceptance conditions

1. An initial controlled cycle produces a valid ledger, register, and status
   document with outcome `EXECUTED`.
2. A second cycle with the exact identity reports `NO_CHANGE`; the ledger,
   register, and status files remain byte-for-byte unchanged.
3. A changed request, Decision Court policy, cycle policy, or governed
   implementation digest cannot take the no-change path.
4. A status-to-ledger or status-to-register mismatch fails before any append.
5. `athena freeze-status` rejects any weakening of the schedule, identity,
   zero-write behavior, Decision Court boundary, or live-execution prohibition.
6. The full test suite and a governed end-to-end cycle pass.

## Accountability and remedy

| Actor | Accountability | Required remedy on failure |
|---|---|---|
| Owner/CIO | Protected control, schedule, Court boundary, and future amendments | Approve a new evidence-backed proposal; never permit a silent weakening |
| Operations Director | Observe hourly outcomes and investigate inconsistent state | Stop the runner, retain logs and bytes, reconcile from the last verified terminal hashes |
| Orchestrator | Compute the exact identity before mutation and enforce zero writes on repeats | Fail closed and emit the exact mismatch; do not append over the failure |
| Decision Court | Adjudicate every changed governed input | Preserve the prior verdict and create a new immutable result after the change |

## Status claim

This control eliminates duplicate writes for the current hourly repository
cycle and adds reproducible input binding. It does not establish the frozen VPS,
durable specialist queues, continuous learning, complete research program, or
30 consecutive days of production-scheduler evidence. `FR-001`, `FR-008`, and
`FR-012` therefore remain `partial`; `FR-017` remains `implemented`. Live
execution remains prohibited. [EF-004, EF-005, EF-012, EF-016]
