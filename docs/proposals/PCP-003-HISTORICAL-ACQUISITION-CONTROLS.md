# PCP-003 — Historical acquisition controls

## Classification

Implementation control under the approved `ATHENA-MDR-001` authority. This is
not a proposal to change a protected layer, the fixed pipeline, a provider,
approved scope, budget, usage right, Decision Court authority, or the
live-execution prohibition. [EF-002, EF-006, EF-010, EF-014]

## Problem

The existing adapter could ingest one bounded request but could not prove that
thousands of historical windows were planned, quota-safe, restartable,
complete, or reconciled after interruption. Treating a collection of
individually valid Datasets as complete history would therefore be an
unsupported claim.

## Control decision

- Partition every selected symbol and interval deterministically from the
  approved start boundary through an explicit end date.
- Bind the plan to both active policy digests and immutable request IDs.
- Reserve recorded credits before transmission and pause before any limit is
  exceeded. Apply a stricter 7-per-minute and 720-per-day history ceiling below
  the observed 8/800 account limit.
- Permit no automatic retries; ambiguous failures consume the reservation and
  enter quarantine.
- Require one terminal checkpoint per request and stop a manifest after any
  quarantine.
- Publish immutable completeness reports that cannot confuse bounded-scope
  completion with full approved-history completion.
- Preserve the existing Dataset, evidence-register, quarantine, ledger,
  Decision Court, licensing, public-data, and live-execution controls.

## Evidence

- Market-data authority: `EF-014`.
- Frozen modularity and audit contracts: `EF-002` and `EF-006`.
- Fixed pipeline and Decision Court authority: `EF-010`.
- Provider quota observation: Twelve Data official pricing and credit guidance,
  observed 14 August 2026.
- Executable evidence: `tests/test_history.py` plus the existing market-data,
  record, freeze, and ledger tests.

## Acceptance conditions

1. A full manifest covers all eight approved instruments and all five approved
   intervals from their exact approved start boundaries.
2. A bounded manifest cannot claim full approved history.
3. The eighth same-minute history request is not transmitted, preserving
   account capacity outside the history controller.
4. A later UTC minute resumes only pending requests and creates no duplicate
   terminal checkpoint.
5. Short or out-of-period source coverage is quarantined and blocks resumption.
6. Checkpoint, quota, manifest, and completeness-report tampering fails
   validation.
7. The engineering freeze, traceability mapping, full unit suite, and synthetic
   end-to-end cycle pass.
8. No credential, provider price, private record, or live-trading authority is
   committed.

## Accountability and remedy

The Operations Director owns canonical paths, quota state, storage availability,
and quarantine response. The market service owns deterministic acquisition.
The evidence register and ledger own immutable identity and reconciliation.
Any failure remains recorded. Remedy is a corrected, newly hashed manifest and
new evidence; deletion, silent retry, fabricated bars, or retrospective status
editing is prohibited.

## Status claim

Passing this proposal proves the acquisition control plane, not the completed
provider corpus. `FR-005` remains `partial` until the complete approved history
and reproducible 0–100 feature plane satisfy the frozen acceptance condition.
