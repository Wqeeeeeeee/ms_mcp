# Semiconductor Modeling Precision Architecture v1

## Status

Goal ID: `SEM-PRECISION-MULTI-AGENT-V1`

Frozen architecture goal. The implementation baseline is commit
`cfa1b27b8e88c94e3719f8f6a0407d29c6a78365`, the head of Draft PR #90 when
this goal was created.

## Goal

Build a precision-oriented semiconductor modeling architecture without
replacing the existing Materials Studio MCP. The system keeps one public MCP
service and one primary natural-language entry point,
`material_studio_live_modeling_request`, while adding deterministic internal
routing and versioned domain-plugin contracts in later increments.

Development may be divided among a principal architect and isolated specialist
agents. Runtime modeling must not become a group of autonomous agents that
concurrently edit a structure or control Materials Studio. Runtime behavior is
implemented by reviewed Python, Pydantic, geometry algorithms,
MaterialsScript, validators, and the existing shared state and execution
layers.

## Invariants

1. There is one public `material_studio_mcp` service. Existing public tools are
   retained for compatibility; domain plugins do not register their own public
   tools.
2. `material_studio_live_modeling_request` remains the preferred high-level
   entry. Intent normalization and routing are internal implementation details.
3. Revisions are immutable. A change, rollback, or promoted calculation result
   creates a new revision and never rewrites or deletes prior evidence.
4. New modeling and calculation paths are preview-first. Execution requires
   explicit user intent and passes through the common execution gates.
5. Live GUI work follows the existing one-window policy. A domain plugin does
   not own, launch, or directly drive Materials Studio.
6. Model construction is deterministic for a fixed normalized intent, current
   revision, plugin version, and declared reference inputs.
7. Reference structures have provenance, license information, retrieval
   context, and SHA-256 bindings. Hidden references are isolated from candidate
   construction.
8. Opening a file, producing a screenshot, finishing a process, or observing a
   small scalar difference is evidence, not automatic scientific validation.
9. Mocked runner, GUI, or CASTEP tests are labeled as mocks and never stand in
   for real Materials Studio 20.1 or CASTEP acceptance.
10. Public schemas and plugin contracts are changed only through an explicit
    architect-owned contract PR.

## Independent Validity States

Every benchmark result preserves these five states independently:

| State | Meaning |
| --- | --- |
| `structure_valid` | Elements, atom identity, lattice, coordinates, and periodicity satisfy the structural contract. |
| `semiconductor_domain_valid` | Facet, termination, layers, defect, dopant, interface, or other domain semantics satisfy the case contract. |
| `ms_roundtrip_valid` | Import and export through the declared Materials Studio path preserve the bounded structure invariants. |
| `calculation_evidence_valid` | Calculation inputs, settings, outputs, hashes, and revision bindings are complete and internally consistent. |
| `scientifically_verified` | The declared scientific method, convergence, and comparison requirements have been met. |

Each state and the overall result uses `PASS`, `PASS_WITH_WARNINGS`, `FAIL`, or
`NOT_RUN`. A weighted score cannot compensate for a hard failure. In
particular, visual similarity cannot compensate for incorrect termination,
small lattice error cannot compensate for a misplaced defect, and process
completion cannot compensate for unconverged calculation evidence.

Overall aggregation follows a fixed truth table over gates marked
`required_for_overall_pass`: any hard failure or required `FAIL` yields `FAIL`;
otherwise any required `NOT_RUN` yields `NOT_RUN`; otherwise any required
`PASS_WITH_WARNINGS` or failed warning criterion yields
`PASS_WITH_WARNINGS`; only all-required-`PASS` yields `PASS`. Disabled gates
must be non-required and retain `NOT_RUN` without being silently dropped.
JSON Schema enforces local hard gates; referential completeness between gate,
criterion, and hard-failure IDs is attested only after the named
`benchmark_evaluation_semantic_validator_v1` performs the cross-record check.
That validator, delivered with the blind evaluator, also proves reference,
candidate, and evaluator roots are disjoint; binds artifacts to those roots;
rejects duplicate IDs; reconciles count totals; requires disabled gates to
remain `NOT_RUN`; and verifies the exact aggregation truth table. A producer
cannot satisfy this gate by copying `true` values into the receipt.

## Target Architecture

The intended runtime flow is:

```text
user request
  -> one MCP high-level entry
  -> normalized ModelingIntent
  -> deterministic capability router
  -> one versioned domain plugin
  -> ModelingPlan
  -> ModelSpec or SemanticPatch
  -> shared structure and domain validation
  -> shared preview or explicitly confirmed execution
  -> independent evaluation
```

The intended development flow is:

```text
versioned goal
  -> architect-issued Work Order
  -> isolated branch and worktree
  -> specialist implementation and evidence receipt
  -> contract, regression, blind-benchmark, and release review
  -> integration branch
```

## Precision Loop

Precision improvements are driven by an evidence loop rather than by the
number of agents:

1. Register an openly usable reference with provenance, license, query context,
   and content hashes.
2. Canonicalize the reference while preserving the original artifact.
3. Compile a blind semantic task that omits final reference coordinates.
4. Construct the candidate through the public MCP workflow.
5. Optionally round-trip the candidate through the explicitly selected
   Materials Studio environment.
6. Let an isolated evaluator compare candidate and reference evidence.
7. Classify failures by structural, domain, round-trip, calculation-evidence,
   and scientific gates.
8. Assign a scoped fix and rerun the complete regression set.

## Delivery Sequence

After this architecture-contract bootstrap, the planned major PRs are:

1. Runtime contract models.
2. Capability registry and shadow router.
3. Reference ingestion.
4. Structure canonicalization.
5. Blind evaluator.
6. 3C-SiC surface plugin.
7. Real Materials Studio round-trip acceptance.
8. Minimal real CASTEP acceptance with revision-bound evidence.
9. Integration release review.

Each substantial increment is delivered as a separate PR targeting
`integration/semiconductor-precision-v1` until the integration goal is ready
for release review.

## Scope Of The First Contract PR

The first PR adds only this goal, development and runtime architecture
documents, two ADRs, three JSON Schemas, and contract regression tests. It does
add the repository-level Work Order/receipt reconciliation CLI required to use
those governance contracts. It does not add runtime plugin code, material
templates, reference adapters, benchmark execution, public MCP tools, or GUI
behavior. Materials Studio and CASTEP are not run for this PR.

## Exit Criteria

This goal is complete only when all of the following are true:

- Domain capabilities route through a versioned internal contract without
  expanding the public tool surface for each material or scenario.
- Each scenario migrates through `off`, `shadow`, and explicitly approved
  `active` modes without shadow-side effects.
- Development changes can be traced from a Work Order through a branch, tests,
  benchmark delta, scientific boundaries, and a reviewed result receipt.
- Reference and candidate access controls prevent hidden-coordinate leakage.
- Benchmark reports retain all five independent validity states and fail closed
  on hard errors.
- Real Materials Studio and CASTEP evidence, when required, is explicitly
  distinguished from mocked or not-run evidence.
- Existing public tools, revision semantics, preview defaults, explicit
  execution confirmation, and the one-window GUI policy continue to pass their
  regression gates.

## Non-Goals

This goal does not authorize multiple public MCP servers, concurrent runtime
agents controlling Materials Studio, direct coordinate copying from hidden
references, one public tool per material, screenshot-based structural
acceptance, or automatic promotion of a completed calculation to a scientific
claim.
