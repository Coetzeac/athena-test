# Idempotent hourly cycle

## Control outcome

`ATHENA-ICC-001` keeps the hourly research-cycle invocation while preventing an
unchanged versioned input from generating duplicate Court and validation
records. The control is approved by the Owner/CIO and frozen under `EF-016`.

An input identity covers four components:

| Component | Binding |
|---|---|
| Research request | SHA-256 of exact request bytes |
| Decision policy | SHA-256 of exact policy bytes |
| Cycle policy | SHA-256 of exact `ATHENA-ICC-001` policy bytes |
| Governed implementation | Sorted manifest of path and SHA-256 for each controlled runtime file |

## Invocation process

1. Parse and validate the request, Decision Court policy, and cycle policy.
2. Validate the current ledger and reconcile every register record to exactly
   one matching ledger link.
3. If a controlled status exists, verify its ledger and register bindings.
4. Compare its prior input identity with the current identity.
5. On an exact match, return `NO_CHANGE` in memory without writing the ledger,
   register, or status file.
6. Otherwise, run the complete Decision Court cycle and persist `EXECUTED` with
   the new identity.

The persisted status always describes the last executed Court cycle. A
`NO_CHANGE` result is deliberately not persisted, because persisting the
observation would itself defeat the zero-write control. The CLI still reports
the result to the workflow log. [EF-002, EF-016]

## Failure and remedy

Malformed status, a broken ledger hash chain, register-link drift, or a status
binding mismatch raises a control error before any cycle append. The Operations
Director must retain the failed bytes and logs, identify the last verified
terminal hashes, and reconcile or restore state through the approved governance
process. Appending a fresh cycle over inconsistent state is prohibited.

An exact replay reuses an immutable prior Court verdict; it does not bypass the
Court. Any change to request, policy, or governed implementation causes a new
adjudication. Live execution remains prohibited. [EF-005, EF-016]
