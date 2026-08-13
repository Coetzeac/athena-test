# ATHENA controlled research intake

## Objective

The intake service converts retained source bytes and a structured Research
Card into immutable ATHENA records without permitting an incomplete,
unsupported, duplicated, digest-conflicting, or rights-uncertain source to enter
the research pipeline. This is the executable first step of the frozen paper-to-
Decision-Court lifecycle. [EF-002, EF-009, EF-010]

It does not decide whether a research claim is true. It preserves exactly what
was supplied, the stated relationship between evidence and claims, and the
deficiencies that must be resolved before testing.

## Authority

`config/research_intake_policy.json` controls eligible source formats, maximum
size, accepted rights declarations, mandatory paper metadata, Research Card
fields, claim relationships, hypothesis and formula requirements, duplicate
rules, and the live-execution prohibition. The policy cites EF-002, EF-009, and
EF-010 and is validated by `athena freeze-status`.

The addition of Author and Formula records implements the frozen Authors and
Mathematical Formulae collections. It does not change the seven layers or their
order. [EF-001, EF-009]

## Intake process

1. Read a closed-schema JSON manifest and a UTF-8 source file located inside the
   manifest directory.
2. Reject path traversal, unsupported file types, oversized files, unknown
   fields, invalid digests, and missing mandatory metadata.
3. Verify the declared SHA-256 digest against the exact retained source bytes.
4. Reject unknown, uncertain, unverified, or otherwise unapproved usage rights.
5. Detect canonical-locator conflicts and identical bytes submitted under a
   second locator.
6. Create stable Author, Paper, Research Card, Hypothesis, and Formula records.
7. Bind every extracted claim to the registered Paper record and its exact
   source locator.
8. Store accepted source bytes at a content-addressed SHA-256 object path.
9. Append each record to the evidence register and anchor it in the hash-chained
   ledger.
10. Record the accepted intake event and expose the record IDs, source digest,
    object path, and ledger hash.

Exact resubmission is idempotent: the original Paper record is returned and a
duplicate-detection event is added. It does not increment the corpus count.

## Quarantine

Intake returns `QUARANTINED` and does not submit the source to the Decision Court
when any mandatory control fails. The append-only quarantine record contains:

- manifest digest;
- canonical locator or supplied source locator;
- computed source digest when the source was safely readable;
- exact failure reasons;
- `QUARANTINED_NO_COURT_SUBMISSION`; and
- its own digest linked to one ledger event.

Rights-uncertain source bytes are not copied into the controlled object store.
The source remains at its supplied local location pending an accountable rights
decision.

## Accountability

| Role | Accountable output | Prohibited action |
|---|---|---|
| Evidence Scout | Source identity, digest, locator, rights declaration and retained bytes | Claim that metadata proves the source |
| Research analyst | Complete Research Card, hypotheses, formulae, limitations and counter-evidence | Omit adverse evidence or invent missing source support |
| Memory Custodian | Stable records, relationships, quarantine and ledger reconciliation | Rewrite or delete history |
| Owner/CIO | Approve protected policy changes and external systems of record | Delegate live authority through intake |

## Proof and validation

`athena validate-intake` fails unless:

- the evidence register and ledger reconcile;
- every quarantine entry has exactly one ledger link;
- every Paper has retained bytes matching both the object and provenance digest;
- every Paper identifies registered Author records; and
- every Research Card claim identifies a registered Paper as evidence.

The public fixture under `examples/research_intake/` is synthetic. It proves the
control path only. It is not a paper, licensed dataset, accepted hypothesis,
validated formula, or evidence of market edge.

## Remedy

The accountable operator must correct the manifest or source at origin, retain
the quarantine record, create a new version or identity when controlled identity
fields changed, rerun intake, and preserve the corrective commit and new ledger
event. Overwriting an accepted record or deleting an adverse quarantine outcome
is prohibited.

## Remaining deficiencies

The required corpus of at least 100 papers does not exist. Google Drive remains
the frozen authoritative research-document repository, but its structure,
connector authority, and reconciliation are not yet verified. PostgreSQL and
object-storage production controls are also absent. FR-002 and FR-003 remain
`partial`, while FR-013 remains `blocked_external`. [EF-002, EF-006, EF-009]
