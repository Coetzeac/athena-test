# Governance and change control

## Authority

The Decision Court derives its authority from the repository's versioned policy
and this engineering contract. No model output, role assertion, or favourable
metric overrides the policy.

## Process

1. Register evidence and preserve its digest.
2. Define a falsifiable claim, mechanism, assumptions, and invalidation basis.
3. Execute a reproducible test on declared inputs.
4. Record adverse evidence and Red Team challenges.
5. Declare risk controls.
6. Submit the complete packet to the Decision Court.
7. Append the packet and verdict to the audit ledger.
8. Apply the stated remediation before resubmission.

## Accountability

Every event identifies the actor that produced it. Policy changes must identify
the approving human, date, reason, affected threshold, supporting evidence, and
rollback method. The current public repository must never contain secrets or
private source records.

## Protected changes

The following require an explicit pull request, passing tests, and human review:

- promotion or rejection thresholds;
- confidence or evidence-weight formulas;
- ledger hashing and validation;
- required recommendation fields;
- execution permissions, capital limits, or kill-switch behaviour;
- any mechanism that can bypass a Court gate.

ATHENA may open or generate an improvement proposal. It may not merge a protected
change, weaken a failing gate, or rewrite an adverse result to improve apparent
performance.

## Remedy and escalation

| Failure | Required remedy | Deadline |
|---|---|---|
| Missing evidence or risk fields | Return `HOLD` with exact omissions | Same cycle |
| Non-positive expectancy or unsafe drawdown | Return `REJECT` with failed gates | Same cycle |
| Ledger integrity failure | Stop the cycle and mark status `ERROR` | Immediately |
| Policy/version mismatch | Refuse adjudication | Immediately |
| Suspected leakage or fabrication | Quarantine the packet and preserve evidence | Immediately |
| Workflow failure | Retain logs and block status promotion | Before next cycle |

