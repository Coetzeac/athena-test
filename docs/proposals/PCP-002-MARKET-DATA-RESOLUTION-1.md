# PCP-002 — ATHENA Market Data Resolution 1

## Decision status

**Approved by Owner/CIO on 14 August 2026.**

Recorded authority: `APPROVED — ATHENA MARKET DATA RESOLUTION 1.` The Owner/CIO
subsequently confirmed `SECRET ADDED — PROCEED WITH ATHENA PR #5.` The resulting
immutable engineering evidence record is `EF-014`.

## Objective

Resolve the frozen but previously unspecified market-data provider, initial
cross-market universe, history windows, intervals, internal-use boundary, data
budget, storage rules, integrity controls, and live-execution prohibition so the
Phase 1 adapter can be built without inventing owner policy. [EF-002, EF-006,
EF-010]

## Approved change

- Twelve Data Basic is the approved initial provider.
- The initial universe is EUR/USD, GBP/USD, USD/JPY, SPY, QQQ, GLD, BTC/USD,
  and ETH/USD.
- Approved intervals are 5-minute, 15-minute, 1-hour, 4-hour, and daily.
- Daily history begins on 1 January 2010 or instrument inception if later;
  intraday history begins on 1 January 2020.
- Raw responses and normalized bars are retained internally by content digest;
  UTC timestamps and provider metadata are mandatory.
- The initial monthly data budget is USD 0. A paid upgrade above the free plan,
  up to USD 79 per month, still requires evidence and a separate Owner/CIO
  decision.
- Provider data may not be redistributed or committed to this public repository.
- Quality failure is quarantined and cannot enter research or the Decision
  Court. Missing bars may not be fabricated.
- Live execution remains prohibited. [EF-014]

## Architecture impact

The decision resolves protected specifications but does not alter the seven
layers, fixed pipeline, systems of record, specialist authority, validation
sequence, or live-execution boundary. The implementation adds a replaceable
provider adapter behind stable Dataset, register, and ledger contracts. [EF-001,
EF-002, EF-005, EF-010]

## Controls and tests

The change is accepted for review only if:

1. `athena freeze-status` validates the approved resolution and all frozen
   requirements.
2. The provider key is read only from `TWELVE_DATA_API_KEY` and never appears in
   a record, locator, exception, fixture, commit, or log.
3. Requests outside the approved universe, history, interval, or retrieval
   window fail before network access.
4. Duplicate, reordered, invalid, truncated, conflicting, or unexpectedly
   gapped data is quarantined with exact reasons and a ledger link.
5. Raw and normalized content tampering fails validation.
6. Exact duplicate intake is idempotent and does not duplicate audit events.
7. Synthetic fixtures remain explicitly synthetic and cannot count as real
   market evidence.
8. The end-to-end CI path writes and validates a Dataset record, raw object,
   normalized object, evidence-register link, and ingestion event.

## Accountability and remedy

The Owner/CIO controls provider, scope, licence, and budget. The Market service
controls request and normalization integrity. The Operations Director controls
provider health and quota failures. The evidence register controls immutable
identity and reconciliation.

Failure requires quarantine, preservation of the adverse record, a corrected
request or code change, a new Dataset identity where content changes, and a full
validation rerun. No role may delete the failure, backfill invented prices,
broaden the approved scope, or promote a dataset directly.

## Traceability truth

This proposal permits `FR-005` to become `partial` only. The acceptance condition
requires reproducible 0–100 volatility, trend, momentum, liquidity, and regime
features across the approved universe; those features do not exist in this
increment. ATH-001, complete validation, TradingView parity, paper operation,
and live execution remain outside the achieved scope. [EF-002, EF-003, EF-010,
EF-014]
