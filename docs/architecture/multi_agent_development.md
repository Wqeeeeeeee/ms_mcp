# Multi-Agent Development Governance

## Purpose

This document governs parallel development of the semiconductor precision
architecture. It does not define a runtime multi-agent system. Specialist
agents contribute isolated code and evidence; one architect owns shared
contracts and integration decisions.

All assignments and completion receipts must validate against
`schemas/work_order.schema.json`. Receipt acceptance also requires the
cross-document validator:

```text
python schemas/validate_work_order.py <work-order.json> <receipt.json>
```

The JSON Schema files in the repository root are development-governance
artifacts consumed by review and CI. They are not runtime package resources;
packaged runtime schemas are introduced only with the corresponding Pydantic
contract implementation.

## Roles

### Principal Architect

The principal architect owns:

- versioned goals and architecture decisions;
- public MCP names and compatibility policy;
- shared `ModelSpec`, `SemanticPatch`, revision, plugin, and benchmark
  contracts;
- Work Order issuance and path ownership;
- reference-access policy and blind-evaluation boundaries;
- integration order, release gates, and final acceptance.

The architect must not lower a threshold after seeing a failure merely to make
a benchmark pass, reinterpret missing evidence as success, permit overlapping
ownership of core files, or equate mocked acceptance with a real backend run.

### Specialist Roles

| Role | Primary responsibility |
| --- | --- |
| `runtime_orchestration_agent` | Modeling intent, capability registry, deterministic routing, plugin lifecycle, migration modes, and runtime receipts. |
| `reference_data_agent` | Provenance, licenses, source adapters, immutable source records, and reference deduplication. |
| `canonicalization_and_comparator_agent` | Lattice normalization, atom mapping, symmetry, periodic images, and quantitative structure comparison. |
| `bulk_and_alloy_agent` | Bulk phases, conventional and primitive cells, supercells, and alloy occupancy. |
| `surface_and_2d_agent` | Miller cuts, slabs, terminations, passivation, vacuum, and two-dimensional materials. |
| `defect_and_dopant_agent` | Vacancies, substitutions, interstitials, complexes, concentrations, and local-shell audits. |
| `interface_and_device_stack_agent` | Interfaces, lattice matching, strain allocation, registry, gaps, contacts, and device stacks. |
| `calculation_agent` | CASTEP and Forcite plans, calculation settings, convergence evidence, and revision-bound results. |
| `gui_and_ms_adapter_agent` | RunMatScript, one-window GUI integration, hot-load, snapshots, view replay, and MS file round trips. |
| `benchmark_and_release_agent` | Blind splits, regression execution, hard-gate reports, benchmark deltas, and release evidence. |

A role may be narrowed in a Work Order. It gains no ownership outside the
explicit `allowed_paths`.

## Work Order Contract

A Work Order is the only normal way to assign specialist work. It records:

- stable goal and Work Order identifiers;
- specialist role, exact 40-character base commit, and expected `agent/*`
  branch;
- accepted upstream Work Order dependencies, each bound to its merged head and
  integration commit;
- scoped scenarios, materials, and operations;
- non-overlapping allowed and forbidden paths;
- reference-access policy, including hidden-holdout restrictions;
- required test and acceptance gates;
- explicit, typed acceptance criteria;
- a mandatory structured result receipt.

The JSON object in the development Goal is an abbreviated minimum-field
illustration. The canonical exchange document uses the richer, closed shape in
`schemas/work_order.schema.json`: test identifiers are expanded to typed test
requirements with commands and environments, reference policy fields are
explicit, and acceptance criteria are machine-readable. No implicit defaults
are applied when issuing a schema-valid Work Order.

Every Work Order and receipt explicitly carries `unit`, `contract`,
`protocol_preview`, `benchmark_blind`, `benchmark_regression`, and
`no_reference_leak` entries. A category that does not apply is still present,
marked optional in the Work Order, and returned as `NOT_RUN`; required entries
may not use `NOT_RUN` at merge time.

Natural-language notes may explain the task but cannot widen its scope. A
specialist that needs a shared contract change returns it in
`contract_changes_requested`; it does not silently edit architect-owned files.

## Result Receipt

Completion produces a separate `agent_result_receipt` document validated by
the same schema. It binds the Work Order to:

- branch, base SHA, and head SHA;
- the SHA-256 of the exact Work Order plus reconciliation of role, branch,
  dependencies, paths, tests, criteria, and reference policy;
- changed paths;
- new and unsupported capabilities and declared reference sources;
- exact test categories, environments, commands, and outcomes;
- benchmark before and after summaries;
- acceptance-criterion results;
- reference-isolation attestation;
- scientific boundaries, known gaps, and contract-change requests;
- whether real Materials Studio and real CASTEP were run.

`NOT_RUN` is an explicit outcome. It must not be omitted or converted to a
passing mock result. A required test or hard-failure acceptance criterion with
`NOT_RUN` makes the receipt merge-blocking and forces `overall_status=FAIL`.
Completeness across the separate Work Order and receipt documents is checked by
the executable `work_order_result_reconciliation_v1` semantic validator. It
recomputes the canonical Work Order SHA-256, rejects duplicate identifiers,
compares contract versions, test commands/required flags, acceptance severities
and real-environment requirements, checks changed and conflicting path scopes,
reconciles benchmark counts/status, and verifies role, branch, dependency,
reference-policy, and allowed-source bindings.
Caller-supplied reconciliation booleans are evidence inputs, not authority;
the PR is ineligible unless the validator independently returns `ok=true`.

## Reference Access Policies

The schema defines five policies:

| Policy | Intended use |
| --- | --- |
| `none` | Work requires no reference data. |
| `metadata_only` | Source metadata and licenses are visible, but raw structures and coordinates are not. |
| `task_only` | The agent sees a compiled semantic task, never the reference artifact or final coordinates. |
| `reference_builder` | A reference-data role may ingest and canonicalize declared source artifacts but does not build candidates. |
| `evaluation_only` | An evaluator may read reference and candidate artifacts but may not modify the candidate. |

Hidden holdout reference coordinates are accessible only to evaluation or
reference-preparation infrastructure explicitly scoped for that purpose. A
modeling agent uses `task_only`. Reference roots and candidate roots remain
separate, and test fixtures must not copy hidden coordinates into task files,
logs, snapshots, or agent prompts.

Role-to-policy compatibility is closed by schema. Modeling, calculation, GUI,
and runtime-orchestration roles may use only `none`, `metadata_only`, or
`task_only`. Only `reference_data_agent` may use `reference_builder`. Only
`canonicalization_and_comparator_agent` and `benchmark_and_release_agent` may
use `evaluation_only`; those policies forbid candidate writes. An isolation
violation remains representable in a result receipt, but it forces
`complied=false` and `overall_status=FAIL`.

## Branch And Worktree Model

The integration baseline for this goal is:

```text
integration/semiconductor-precision-v1
cfa1b27b8e88c94e3719f8f6a0407d29c6a78365
```

Each substantial assignment uses a dedicated branch and worktree created from
the current architect-approved integration SHA. Example branch names are:

```text
agent/runtime-orchestration-v1
agent/reference-ingestion-v1
agent/structure-canonicalization-v1
agent/bulk-alloy-v1
agent/surface-2d-v1
agent/defect-dopant-v1
agent/interface-device-v1
agent/calculation-validation-v1
agent/ms-gui-adapter-v1
agent/benchmark-release-v1
```

Every PR targets `integration/semiconductor-precision-v1`. Long chains in
which one specialist PR is based on another specialist PR are prohibited
unless the architect records an explicit dependency and rebases the dependent
Work Order onto an accepted integration SHA. The dependency record carries the
upstream Work Order ID, merged specialist head SHA, and resulting integration
SHA; the dependent Work Order's `base_sha` must resolve to that accepted
integration history before work starts.

Specialists must not share an uncommitted worktree, force-push another role's
branch, or resolve scope conflicts by editing a forbidden path. Large progress
increments receive separate PRs so review evidence remains attributable.

## Development Lifecycle

1. The architect freezes or selects a versioned goal.
2. The architect issues a schema-valid Work Order from an exact integration
   SHA and verifies that path scopes do not overlap active assignments.
3. The specialist creates an isolated branch and worktree.
4. The specialist implements only the declared scope and runs the required
   tests without hidden-reference leakage.
5. The specialist returns a schema-valid result receipt and opens a draft PR.
6. The architect reviews contract compatibility, scientific boundaries,
   benchmark delta, and path scope before ordinary code style.
7. Required CI and any separately authorized real-environment gates run.
8. The architect merges to the integration branch or returns a new Work Order.

## Pull Request Evidence

A specialist PR must state:

1. Goal ID.
2. Work Order ID.
3. Exact base SHA.
4. Branch name.
5. Responsible specialist role.
6. Changed file scope.
7. New capability.
8. Unsupported capability.
9. Reference-data sources.
10. Reference-access policy.
11. Scientific boundaries.
12. Unit-test evidence.
13. Contract-test evidence.
14. MCP protocol-smoke evidence.
15. Benchmark-before evidence.
16. Benchmark-after evidence.
17. Reference-leak test evidence.
18. Whether real Materials Studio 20.1 was run.
19. Whether real CASTEP was run.
20. Known issues.
21. Requested contract changes.

The PR also carries a schema-valid `agent_result_receipt`. Missing or not-yet-
implemented evidence is reported as `NOT_RUN`; it is never omitted or promoted
to a pass.

Recommended required checks are `unit-tests`, `contract-tests`,
`protocol-smoke`, `benchmark-no-reference-leak`, `benchmark-regression`,
`fake-gui-tests`, `compileall`, and `diff-check`. Real-environment checks such
as `real-ms-20.1-acceptance` and `real-castep-small-cell-acceptance` are
separate gates and are never inferred from CI mocks.

The architect-owned bootstrap PR that first introduces this schema cannot have
a pre-existing schema-valid Work Order or specialist receipt. That single PR
uses bootstrap exemption `ARCH-CONTRACT-BOOTSTRAP-V1`, records the principal
architect role and all 21 evidence fields directly in its PR body, and may use
an `arch/*` branch. The exemption ends when the schema reaches the integration
branch; every subsequent specialist PR requires an `agent/*` Work Order and
reconciled receipt.

## Merge Gates

A PR is not eligible for integration when any of these conditions holds:

- a required hard acceptance criterion fails or is silently absent;
- a required test is absent, `NOT_RUN`, or failed;
- Work Order/receipt reconciliation is incomplete or mismatched;
- benchmark performance regresses outside an approved, documented boundary;
- another domain's regression gate fails;
- a hidden reference or final coordinate leaked into candidate construction;
- public MCP compatibility or immutable revision semantics changed without an
  architect contract decision;
- a plugin claims GUI, state, runner, or public-tool ownership;
- a mock is represented as real Materials Studio or CASTEP acceptance;
- an unsupported scientific conclusion is expanded to make the result appear
  successful.

A hard failure cannot be offset by an aggregate score. An intentional contract
or threshold change requires its own reviewable architect-owned commit and a
full benchmark rerun.

## Runtime Boundary

Development roles do not persist as autonomous runtime controllers. At
runtime, one deterministic router selects one compatible domain plugin. The
shared state, validation, execution, calculation, and GUI layers remain the
only owners of their respective side effects.
