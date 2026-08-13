# ATHENA evidence foundation

## Control objective

ATHENA must preserve the identity, provenance, content digest, relationships,
and audit history of every material research artifact before that artifact can
influence a Decision Court verdict. This implements the evidence-first rule and
the frozen paper-to-Court pipeline; it does not establish that a source is true
or that a strategy has an edge. [EF-002, EF-010]

## Authority

The controlled record families are papers, Research Cards, hypotheses,
datasets, experiments, factors, indicators, strategies, and validation results.
They correspond to the frozen research collections and systems-of-record model.
[EF-002, EF-009]

`config/evidence_registers.json` is the executable Phase 0 contract. Its record
types, stable-ID prefixes, required identity fields, schemas, ledger event, and
mutation rule are validated by `athena freeze-status`. A change to the record
families or their authority is a protected architecture change and therefore
requires an evidence-backed proposal, tests, and Owner/CIO approval. [EF-005]

## Identity and provenance process

1. Canonical JSON is produced with sorted keys, compact separators, UTF-8, and
   non-finite numbers prohibited.
2. A stable ID is derived from the ATHENA namespace, schema version, record
   type, and declared immutable identity using SHA-256.
3. The record stores its source class, locator, source digest, observed time,
   acquisition method, usage-rights declaration, and immutable evidence IDs.
4. Dataset versions additionally store a fingerprint covering source,
   extraction configuration, row count, fields, universe, timeframe, period,
   acquisition time, and content digest.
5. The record receives separate content and whole-record SHA-256 digests.
6. The canonical record is appended to the JSONL register.
7. An `evidence_record_registered` event anchors the record, content, and
   provenance digests in the existing hash-chained ledger.
8. Register validation fails unless every record has exactly one matching
   ledger event.

The same identity cannot be reused for different content. Correcting an
identity or versioned specification requires a new record; history is not
rewritten.

## Current executable evidence

Each governed example cycle now registers four artifacts before publishing its
status:

| Record | Purpose | Court boundary |
|---|---|---|
| Dataset | Fingerprint the ordered synthetic outcome set | Does not convert synthetic data into eligible promotion evidence |
| Strategy | Preserve the declared claim, mechanism, instrument, timeframe, and controls | Does not approve the strategy |
| Experiment | Bind methodology, assumptions, dataset, strategy, and policy digest | Does not satisfy walk-forward or robustness validation |
| Validation result | Preserve metrics and the exact Court result | `PROMOTE` means only the next controlled research stage |

The demonstration remains synthetic and live execution remains prohibited.
[EF-005, EF-010]

## Accountability and evidence

The Evidence Scout or ingestion service is accountable for declared source
identity, acquisition method, usage rights, and retained bytes. The Experiment
service is accountable for the dataset and specification links. The Decision
Court is accountable only for applying the versioned policy to the submitted
record. The Memory Custodian is accountable for register-ledger reconciliation.
No role may silently rewrite a record or bypass the Court. [EF-005, EF-010]

Required proof for a registered artifact is:

- its stable record ID;
- its canonical record digest and content digest;
- its provenance digest and immutable evidence IDs;
- its dataset fingerprint where applicable;
- its matching ledger event and valid terminal ledger hash; and
- the code, configuration, and commit SHA capable of reproducing the record.

## Failure and remedy

Blank provenance, malformed timestamps, invalid digests, identity mismatches,
content tampering, duplicate IDs, conflicting immutable records, missing ledger
links, and ledger digest mismatches are hard failures. The affected artifact
must be quarantined and cannot enter or remain in the Decision Court pipeline.

Remedy requires preserving the rejected bytes, creating a corrected record with
a new stable identity when the identity changed, restoring the ledger/register
link, rerunning validation, and recording the responsible actor and corrective
commit. Deleting or overwriting the adverse record is prohibited.

## Boundary and remaining deficiency

This is a repository-backed Phase 0 control. It does not yet provide the frozen
paper corpus, Research Cards, production PostgreSQL persistence, Google Drive
reconciliation, licensed market-data ingestion, backups, or recovery evidence.
FR-002 and FR-003 therefore advance only to `partial`; neither is implemented.
[EF-006, EF-009]
