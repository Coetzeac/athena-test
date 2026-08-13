# ATHENA engineering contract

This repository implements the frozen ATHENA architecture. Do not redesign the
kernel while implementing a feature.

## Non-negotiable rules

1. Evidence precedes opinion. Every material claim cites immutable evidence IDs.
2. Every result is reproducible from versioned inputs and configuration.
3. No researcher, agent, model, or operator bypasses the Decision Court.
4. A recommendation records confidence, evidence weight, sample size,
   expectancy, risk, reasoning, assumptions, and counter-evidence.
5. Modules are replaceable; audit records and domain contracts remain stable.
6. Protected policy changes require tests, an explicit proposal, and human
   approval. ATHENA may propose improvements; it may not silently rewrite its
   own controls.
7. Never commit credentials, private correspondence, personal information, or
   production trading keys. The public test repository uses synthetic data only.
8. `config/engineering_freeze.json` is the canonical architecture contract.
   Every material architecture claim must cite its `EF-*` evidence record.
9. Run `athena freeze-status` before changing a frozen requirement. Missing,
   reduced, reordered, or unmapped controls are build failures.
10. Do not label a documented or scaffolded requirement as implemented. Use the
    traceability statuses and acceptance criteria exactly.

## Definition of done

- Code and tests are committed together.
- `make test` passes.
- An end-to-end cycle produces a valid hash-chained ledger and status document.
- New decisions expose gate results and exact reasons.
- Documentation describes authority, process, evidence, accountability, and
  remedy for any governance change.
- The engineering freeze and traceability mapping validate.

## Protected architecture changes

The seven layers, minimum research targets, fixed pipeline, required validation
stages, specialist services, runtime services, systems of record, daily progress
control, human authority, and live-execution prohibition are protected. A change
requires an evidence-backed proposal, tests, and explicit Owner/CIO approval.
