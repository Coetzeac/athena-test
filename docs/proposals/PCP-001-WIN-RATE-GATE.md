# PCP-001 — Reconcile the 60% hit-rate gate

## Status

`PROPOSED — OWNER/CIO DECISION REQUIRED`

This document does not change `config/decision_policy.json`.

## Conflict

The current Phase 0 Court rejects a candidate whose observed hit rate is below
60%. The recovered engineering freeze states that win rate is a secondary
validation metric and cannot establish an edge by itself. Expectancy, risk-
adjusted performance, drawdown, average trade, exposure, unseen-data performance,
and robustness are also required. [EF-010]

A profitable low-hit-rate strategy can therefore be rejected even when its
payoff distribution and robust expectancy are sound. Conversely, a high-hit-rate
strategy with catastrophic tail loss can pass the hit-rate gate. The current
threshold is structurally inconsistent with the frozen research constitution.

## Proposed remedy

After Owner/CIO approval and implementation of the complete validation engine:

1. Retain hit rate as a reported metric and confidence input.
2. Remove `target_hit_rate` as an automatic rejection gate.
3. Permit strategy-specific hit-rate constraints only when the approved
   experiment specification supplies a mechanism-based reason.
4. Require positive out-of-sample expectancy, transaction-cost survival,
   controlled drawdown, profit-factor or payoff-quality evidence, and complete
   walk-forward/Monte Carlo/cross-market/sensitivity/robustness gates.
5. Add tests for profitable low-hit-rate and dangerous high-hit-rate candidates.

## Authority required

Owner/CIO approval, dated decision, supporting experiment evidence, changed
policy version, regression tests, rollback instruction, and Decision Court audit
entry.

## Interim control

Until approved, `target_hit_rate = 0.60` remains operative because it is a
protected policy. Its existence must not be represented as part of the recovered
complete engineering freeze.

