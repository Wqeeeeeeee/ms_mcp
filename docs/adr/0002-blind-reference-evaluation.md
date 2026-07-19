# ADR 0002: Blind Reference Evaluation With Hard Gates

- Status: Accepted
- Date: 2026-07-19
- Decision owners: Semiconductor Precision Architecture

## Context

Reference structures from crystallographic and computational databases can
improve modeling precision, but they can also invalidate a benchmark if final
coordinates are exposed to the candidate builder. Visual similarity, file-open
success, and calculation completion do not independently prove structural or
scientific correctness. A single weighted score can also hide a critical
domain error behind several easy metrics.

## Decision

Use independent blind evaluation with three benchmark splits:
`development`, `validation`, and `hidden_holdout`.

Reference ingestion records source provenance, license information, retrieval
context, and SHA-256 hashes. A task compiler produces a semantic modeling task
without final reference coordinates. Candidate construction can read only that
task. An evaluator may read both immutable reference and candidate artifacts
after construction, but it cannot modify the candidate or return hidden
coordinates to the builder.

Every result retains five independent states:
`structure_valid`, `semiconductor_domain_valid`, `ms_roundtrip_valid`,
`calculation_evidence_valid`, and `scientifically_verified`. Each state and the
overall result uses `PASS`, `PASS_WITH_WARNINGS`, `FAIL`, or `NOT_RUN`.

Acceptance uses explicit hard-failure rules. Weighted-score compensation is
forbidden. A hard structural, domain, round-trip, evidence-binding, or
scientific-method failure forces the overall result to `FAIL` even when other
metrics pass.

## Consequences

Positive consequences:

- benchmark success measures reconstruction rather than coordinate copying;
- source and license provenance remains auditable;
- failures are attributable to a specific validation layer;
- screenshots and process completion remain useful evidence without becoming
  scientific claims;
- hidden family-level cases can test generalization beyond memorized templates.

Costs and constraints:

- reference, candidate, task, and evaluator roots require strict access control;
- benchmark infrastructure must preserve immutable artifacts and hashes;
- some gates legitimately remain `NOT_RUN` without a real MS or CASTEP run;
- experimental and DFT-optimized structures require method-aware comparisons;
- calculation values are directly comparable only when the declared method and
  convergence settings are compatible.

## Alternatives Rejected

### Give the modeling component the complete reference structure

Rejected because it tests copying rather than deterministic semantic modeling.

### Accept by screenshot or successful Materials Studio load

Rejected because visibility does not prove atom mapping, termination, defect
identity, periodicity, or scientific correctness.

### Use one weighted quality score

Rejected because a low-cost metric could compensate for a scientifically fatal
error such as the wrong surface termination or an unbound calculation result.

### Treat completed calculations as scientifically verified

Rejected because execution success does not prove parameter convergence,
method comparability, or validity of the scientific interpretation.
