# Delivery plan

The roadmap is governed by the complete engineering freeze and its traceability
register. Phase labels do not override a frozen requirement or acceptance gate.

## Phase 0 — executable kernel

- Evidence identity contract and tamper-evident ledger.
- Deterministic performance evaluation.
- Adversarial challenge and risk fields.
- Versioned Decision Court gates.
- End-to-end CLI, status contract, dashboard, tests, and hourly runner.

Acceptance: `make test` passes; `make demo` writes a valid ledger and a status
file whose verdict can be reproduced from the example request.

## Phase 1 — research laboratory

- Historical market-data adapter with dataset fingerprints.
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
