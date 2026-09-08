# Delivery plan

The roadmap is governed by the complete engineering freeze and its traceability
register. Phase labels do not override a frozen requirement or acceptance gate.

## Phase 0 — executable kernel

- Evidence identity contract and tamper-evident ledger.
- Repository-backed knowledge records, provenance, dataset fingerprints, and
  register-to-ledger reconciliation.
- Controlled paper and Research Card intake with source retention, author and
  formula records, claim links, duplicate detection, and quarantine.
- Deterministic performance evaluation.
- Adversarial challenge and risk fields.
- Versioned Decision Court gates.
- End-to-end CLI, status contract, dashboard, tests, and hourly runner.
- Exact-input cycle identity and a fail-closed, zero-write `NO_CHANGE` replay
  path under `ATHENA-ICC-001`. [EF-016]

Acceptance: `make test` passes; `make demo` writes a valid ledger and a status
file whose verdict can be reproduced from the example request.

## Phase 1 — research laboratory

- Runtime-persistence policy, closed schemas, PostgreSQL migrations,
  S3-compatible immutable-object controls, bounded Redis coordination, and a
  synthetic recovery proof. Production deployment and observed RPO/RTO evidence
  remain outstanding. [EF-015]
- Historical market-data adapter and resumable acquisition control plane with
  Dataset fingerprints, immutable manifests, quota reservations, terminal
  checkpoints, and completeness reports. Real durable acquisition of the
  complete approved provider history remains outstanding. [EF-014]
- Strategy specification schema and deterministic execution engine.
- Fees, spread, slippage, session, timezone, and missing-bar controls.
- Train/validation/test separation and leakage tests.
- Walk-forward, Monte Carlo, parameter-stability, and regime analysis.

Acceptance: identical inputs and commit SHA reproduce identical result hashes;
no in-sample result can be submitted as out-of-sample evidence.

## Phase 2 — front-test organization

- Paper-trading event adapter and immutable signal journal.
- Scheduled specialist workers and durable job queue.
- Failure library, hypothesis registry, and evidence graph.
- Operational alerts, retries, idempotency, and cost budgets.

Acceptance: 30 consecutive days of recoverable paper operation; every signal is
traceable to code, data, policy, and a prior Court verdict.

## Phase 3 — controlled execution

- Broker adapter isolated behind explicit execution authority.
- Portfolio exposure limits, position sizing, kill switch, reconciliation, and
  human approval gates.
- Secret management, least privilege, monitoring, and incident response.

Acceptance: independent security and risk review; documented owner approval;
tested fail-closed behaviour. No live capital is permitted before acceptance.
