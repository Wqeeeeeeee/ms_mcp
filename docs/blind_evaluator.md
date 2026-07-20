# Blind Benchmark Evaluator

The `material_studio_mcp_server.benchmark_evaluation` package is an offline,
internal evaluator for the frozen `benchmark_case.schema.json` contract. It is
not imported by the MCP server entry path, registers no tools, creates no model
revision, and does not call Materials Studio, CASTEP, a GUI, a process, or the
network.

## Contract Boundary

The semantic validator contract is
`benchmark_evaluation_semantic_validator_v1`. The validator copies and
strictly revalidates every input. Caller-provided semantic-validation
attestations are retained only as untrusted input and are never used to decide
acceptance.

The validator independently checks:

- pairwise lexical and physical separation of the reference, candidate, and
  evaluator roots;
- canonical root-relative artifact bindings and content digests;
- unique source, artifact, criterion, rule, and result identifiers;
- complete source, gate, criterion, hard-failure, and evidence references;
- disabled-gate emptiness and `NOT_RUN` state;
- exact result counts and the required-gate aggregation truth table;
- real-backend evidence boundaries for this offline phase;
- chronological result metadata; and
- development-only split authorization.

A strict candidate submission is required before any physical root or artifact
is accessed. Stored report JSON rejects duplicate object keys and nonstandard
numeric constants before strict contract validation, so parser overwrite
behavior cannot hide coordinate-disclosure fields.

All failures cross the package boundary as fixed `EvaluationReason` values.
Untrusted exception text and artifact content are not included in exceptions,
reports, or task projections.

## Path Isolation

Callers supply three explicit absolute roots through `EvaluationRoots`. The
package never discovers a workspace from the environment, current directory,
home directory, registry, or running process.

Windows aliases are rejected, including alternate streams, reserved device
names, extended device prefixes, trailing-dot or trailing-space names, and
reparse-point traversal. Unicode compatibility spellings are rejected through
NFKC normalization, including Win32 superscript-digit device aliases. Each
artifact must have one canonical relative POSIX
spelling and bind to exactly one declared root. Equal roots, ancestor overlap,
string-prefix collisions, and equal physical identities fail closed.

Candidate evaluation uses a bounded full-tree identity snapshot. The snapshot
includes path-set identity, file and directory metadata, file identity, content
digests, empty directories, and aggregate counts. Files with aliasing links,
unsupported node types, excessive depth, excessive count, or excessive size
are rejected. Directory streams are capped before sorting or child metadata
access, so a hostile directory cannot bypass the global count budget through
unbounded enumeration. The evaluator rechecks the complete tree before and after every
major evaluation stage and never repairs a candidate.

## Evaluation Flow

`evaluate_benchmark_case` performs the following deterministic sequence:

1. Strictly reload the benchmark case and candidate submission.
2. Recompute semantic validation against the explicit roots.
3. Snapshot the complete candidate tree.
4. Bind and read the declared reference and submitted candidate artifacts.
5. Call only the public canonicalization package exports.
6. Retain full comparison state inside the evaluator process.
7. Produce only reviewed task and result projections.
8. Apply the frozen first-phase limits.
9. Recheck the candidate tree and return an immutable outcome.

The compiled task contract is `benchmark_coordinate_free_task_v1`; the report
contract is `benchmark_coordinate_free_report_v1`. Both carry explicit
negative disclosure flags and are checked again at the serialization boundary.

## Validity States

Every report preserves exactly these independent states:

- `structure_valid`
- `semiconductor_domain_valid`
- `ms_roundtrip_valid`
- `calculation_evidence_valid`
- `scientifically_verified`

Required-gate aggregation is fixed as
`FAIL > NOT_RUN > PASS_WITH_WARNINGS > PASS`. A hard failure cannot be offset
by another result and weighted scoring is unavailable. Disabled gates are
non-required, contain no criteria, and remain `NOT_RUN`.

This PR can evaluate structural comparison evidence and evaluator-trusted
domain observations. It does not implement the surface-construction rules that
produce those domain observations. Materials Studio round-trip, calculation
evidence, and scientific verification remain `NOT_RUN` until their later,
separately reviewed real-environment PRs.

## Frozen Limits

The first-phase limits are fixed before hidden-holdout use:

| Metric | Inclusive limit |
| --- | ---: |
| Vacuum absolute error | 0.10 angstrom |
| RMS structural error | 0.05 angstrom |
| Maximum structural error | 0.15 angstrom |
| Maximum relative lattice error | 0.001 |

Equality passes. Non-finite values and boolean-to-number coercion fail closed.
Changing these values requires a separate architect-owned Work Order and PR.

## Public API Compatibility

Importing the evaluator before or after
`material_studio_mcp_server.server` leaves the public MCP inventory at exactly
40 tools and leaves the `material_studio_live_modeling_request` input schema
and Python signature unchanged. The evaluator is intentionally absent from the
server import graph.

## Scientific Boundary

Passing this offline evaluator establishes only the validity states supported
by the supplied evidence. It does not prove a real Materials Studio
round-trip, a real CASTEP run, convergence, or scientific correctness. Mocked
or caller-attested backend results cannot upgrade those states.
