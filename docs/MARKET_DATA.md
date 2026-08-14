# ATHENA controlled market-data intake

## Authority

`ATHENA-MDR-001` is the approved market-data authority. The Owner/CIO approved
it on 14 August 2026 and confirmed that the repository secret
`TWELVE_DATA_API_KEY` had been added. The controlling evidence record is
`EF-014`; the asset-agnostic, runtime, and fixed-pipeline authorities remain
`EF-002`, `EF-006`, and `EF-010`.

This approval resolves the provider, internal-use boundary, initial universe,
history windows, intervals, UTC storage rule, dataset fingerprint requirement,
quarantine rule, and data budget. It does not approve a model, strategy,
TradingView result, broker, commercial redistribution, or live execution.

## Approved scope

| Control | Approved value |
|---|---|
| Provider | Twelve Data, Basic plan |
| Forex | EUR/USD, GBP/USD, USD/JPY |
| ETFs | SPY, QQQ, GLD |
| Crypto | BTC/USD, ETH/USD |
| Daily history | 1 January 2010, or instrument inception if later |
| Intraday history | 1 January 2020 |
| Intervals | 5-minute, 15-minute, 1-hour, 4-hour, daily |
| Storage time | UTC, with provider metadata retained |
| Initial monthly data budget | USD 0 |
| Conditional upgrade ceiling | USD 79 per month; separate evidence and approval required |
| Usage | Personal, internal, non-commercial research only |
| Redistribution | Prohibited |
| Live execution | Prohibited |

The executable contract is `config/market_data_policy.json`. Any additional
symbol, interval, provider, licence, spend, or use requires a controlled policy
proposal and Owner/CIO approval. [EF-002, EF-006, EF-010, EF-014]

## Process

1. A request declares one approved symbol, one approved interval, and an exact
   inclusive start and end date.
2. The request is rejected if it exceeds the approved universe, history
   boundary, interval set, or controlled retrieval window.
3. The Twelve Data adapter reads the API key only from
   `TWELVE_DATA_API_KEY`. The key is excluded from source locators, records,
   exceptions, CLI output, fixtures, and repository files.
4. The adapter requests ascending JSON bars in UTC. Provider metadata is
   retained separately from normalized values.
5. Every bar is normalized into UTC timestamp, open, high, low, close, and
   nullable volume fields. Decimal values remain decimal strings; binary
   floating-point conversion is not used.
6. The adapter rejects duplicates, non-increasing timestamps, non-positive
   prices, inconsistent OHLC ranges, values outside the requested period,
   output-cap truncation, unexpected in-session gaps, incomplete crypto
   boundaries, and non-crypto responses that miss more than the greater of two
   boundary sessions or the recorded 5% session-date tolerance.
7. Accepted raw provider bytes and normalized JSONL bytes are retained in
   content-addressed runtime storage. Neither may be committed to the public
   repository.
8. A Dataset fingerprint binds source, sanitized locator, policy digest, row
   count, fields, symbol, interval, period, acquisition time, and normalized
   content digest.
9. The Dataset record is appended to the evidence register and reconciled to an
   `evidence_record_registered` ledger event. A separate
   `market_data_ingested` event records the controlled intake result.
10. A dataset remains evidence input only. It cannot submit itself to the
    Decision Court, approve a strategy, or authorize execution.

Large historical periods are partitioned into interval-specific request
windows. A response that reaches the provider output cap is quarantined rather
than treated as complete. `ATHENA-HIST-001` adds deterministic manifests,
pre-request quota reservation, resumable checkpoints, boundary coverage, and
immutable completeness reports without changing this intake contract. See
[historical acquisition](HISTORICAL_ACQUISITION.md). [EF-014]

## Evidence and reproducibility

Every accepted dataset must expose:

- resolution and policy digest;
- provider and sanitized source locator;
- symbol, asset class, interval, requested period, and observed period;
- acquisition timestamp and provider metadata;
- raw-response digest and retained raw-object path;
- normalized-content digest and retained normalized-object path;
- row count, unexpected missing-bar count, and permitted inter-session gap
  count;
- Dataset stable ID, record digest, registration ledger hash, and ingestion
  ledger hash; and
- the code and commit SHA needed to rerun validation.

`athena validate-market-data` recalculates raw and normalized digests,
re-normalizes the retained payload, checks row counts and policy scope, and
requires exactly one register event and one ingestion event for every market
dataset.

## Accountability

| Actor | Accountability |
|---|---|
| Owner/CIO | Provider, scope, licensing boundary, budget, and protected changes |
| Market service | Exact request, secret isolation, acquisition, normalization, and gap findings |
| Evidence register | Dataset identity, provenance, immutable digests, and ledger reconciliation |
| Operations Director | API failure, quota, latency, cost, retries, and unresolved quarantine |
| Experiment service | Uses only validated datasets and records exact dataset IDs |
| Decision Court | Applies policy only after the research and validation stages submit eligible evidence |

No actor may broaden the scope, rewrite failed data, fabricate bars, expose the
secret, or convert data intake into trading authority.

## Failure and remedy

A failed request is written to the append-only market-data quarantine register
with request digest, payload digest when available, symbol, interval, exact
reasons, disposition, and a matching `market_data_quarantined` ledger event. Its
disposition is `QUARANTINED_NO_RESEARCH_OR_COURT_USE`.

Remedy requires identifying the responsible request, provider response, policy,
and code version; correcting the request or partitioning without altering the
adverse record; rerunning intake; producing a new Dataset ID; and validating all
register and ledger links. Deletion, silent repair, forward filling, and
inventing price bars are prohibited.

## Current acceptance boundary

The adapter and historical control-plane increments implement the controlled
policy, synthetic end-to-end path, byte retention, Dataset registration,
deterministic planning, quota enforcement, resumable checkpoints, quarantine,
completeness reports, and validation. They do not prove that the complete
approved provider history for all eight instruments and five intervals has been
acquired or retained in durable private storage. They do not implement
normalized 0–100 market features, ATH-001, walk-forward testing, Monte Carlo,
cross-market robustness, Python/Pine parity, TradingView ingestion, or paper
trading.

`FR-005` remains `partial`; it is not implemented.
`FR-006`, `FR-007`, and `FR-014` do not advance. Live execution remains
prohibited. [EF-002, EF-003, EF-010, EF-014]
