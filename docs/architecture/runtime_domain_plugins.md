# Runtime Domain Plugin Architecture

## Purpose

Domain plugins add semiconductor modeling capabilities behind the existing
Materials Studio MCP without creating one public tool per material or running
multiple autonomous modeling agents. This document is an architecture
contract; the first contract PR does not implement the described modules.

Plugin manifests must validate against `schemas/domain_plugin.schema.json`.
This repository-level manifest schema is reviewed in CI and is not exposed as
a public MCP input or bundled runtime resource by this architecture-only PR.

## Public Boundary

The service remains `material_studio_mcp_server.server:main`. Existing tools
remain compatible, and `material_studio_live_modeling_request` is the primary
high-level natural-language entry.

A domain plugin is internal. It cannot:

- register or rename a public MCP tool;
- read or publish project state directly;
- execute MaterialsScript, Forcite, or CASTEP directly;
- launch, select, or control a Materials Studio GUI window;
- mutate a base `ModelSpec` or overwrite an immutable revision;
- read references outside the access policy supplied by orchestration.

## Proposed Package Boundaries

Later implementation increments may add:

```text
src/material_studio_mcp_server/
  orchestration/
    intent.py
    router.py
    planner.py
    work_order.py
    capability_registry.py
  domains/
    base.py
    bulk.py
    alloy.py
    surface.py
    two_dimensional.py
    defect.py
    dopant.py
    interface.py
    gate_stack.py
    contact.py
  references/
    source_record.py
    optimade.py
    cod.py
    jarvis.py
    oqmd.py
  benchmark/
    canonicalize.py
    task_compiler.py
    blind_runner.py
    compare_structure.py
    compare_semiconductor.py
    compare_calculation.py
    report.py
  evaluation/
    bulk.py
    slab.py
    defect.py
    interface.py
    calculation.py
```

The existing `specs`, `state`, `validators`, `translators`, and `parsers`
packages remain shared infrastructure. Domain code composes them rather than
forking their behavior.

## Runtime Pipeline

```text
natural-language request or structured request
  -> normalize to ModelingIntent
  -> resolve current immutable ModelState when required
  -> query CapabilityRegistry
  -> deterministic match and ambiguity gate
  -> plugin.plan
  -> plugin.build
  -> common schema validation
  -> plugin.validate
  -> common script/materialization preview
  -> explicit execution gate when requested
  -> shared state publication
  -> independent diagnostics or benchmark evaluation
```

No plugin method is allowed to publish a revision or produce a GUI side effect.
The common layer performs those actions only after validation and the existing
confirmation gates.

## Plugin Contract

A plugin declares a stable `plugin_id`, semantic `contract_version`, supported
materials, scenarios, operations, limits, dependencies, and four callable
stages:

```python
class SemiconductorDomainPlugin(Protocol):
    plugin_id: str
    contract_version: str
    implementation_version: str

    def match(self, intent: ModelingIntent) -> MatchResult: ...

    def plan(
        self,
        intent: ModelingIntent,
        current_state: ModelState | None,
    ) -> ModelingPlan: ...

    def build(self, plan: ModelingPlan) -> ModelSpec | SemanticPatch: ...

    def validate(self, model: ModelSpec) -> DomainValidationReport: ...
```

The manifest binds each stage to exact input and output contract names:
`ModelingIntent -> MatchResult`, `ModelingIntent + ModelState -> ModelingPlan`,
`ModelingPlan -> ModelSpec | SemanticPatch`, and
`ModelSpec -> DomainValidationReport`. The Runtime Contract Models PR freezes
the corresponding Pydantic shapes before registry or router behavior is added.
`match`, `plan`, and `validate` are pure. `build` may allocate in-memory objects
but is filesystem- and process-side-effect free. Every stage is deterministic
for its declared inputs.

### Match

`match` returns `none`, `compatible`, or `exact`, plus structured reason codes
and a bounded specificity value. It does not inspect the GUI, filesystem,
network, wall clock, or hidden reference data.

### Plan

`plan` resolves all assumptions that affect geometry into an auditable plan.
Missing scientific inputs produce questions, unsupported-capability reasons,
or a preview warning; they are not filled from undeclared global defaults.

### Build

`build` deterministically emits a new `ModelSpec` or a `SemanticPatch`. It does
not mutate `current_state`, write a revision, invoke a runner, or copy final
coordinates from a hidden reference.

### Validate

`validate` reports domain-specific facts and failures without replacing shared
schema, chemistry, script-safety, execution, or artifact validation. It cannot
promote GUI visibility or calculation completion to scientific verification.

## Deterministic Routing

The capability registry loads only manifests compatible with the server's
supported contract major version. For each request, the router:

1. filters by declared material, scenario, operation, periodicity, and limits;
2. calls the pure `match` contract on the remaining plugins;
3. ranks by match kind, specificity, and architect-declared priority;
4. selects only a unique best match;
5. fails closed with structured ambiguity evidence when the best rank is tied.

`plugin_id` provides stable output ordering but is not used to hide a semantic
tie. The router does not ask multiple LLMs to vote. A forced plugin selection,
when later supported, must be explicit, capability-compatible, and recorded in
the plan receipt.

## Limits And Unsupported Behavior

A manifest declares periodicity, atom-count bounds, model kinds, whether a
current structure is required, creation and patch support, and unsupported
capabilities. A request outside those limits is rejected before `build`.
At least one of create or patch support is mandatory. The registry's semantic
manifest validator must also reject cross-field contradictions that JSON
Schema cannot compare directly, including `min_atoms > max_atoms`; that
validator is delivered with the registry rather than trusted as a plugin-authored
attestation.

Plugins must distinguish:

- `unsupported`: the plugin contract cannot represent or verify the request;
- `invalid_input`: the request contradicts a declared invariant;
- `needs_user_input`: a scientifically material choice is missing;
- `preview_warning`: a model can be inspected but must not be executed yet;
- `internal_error`: the plugin violated its deterministic contract.

None of these states may be converted to success by a GUI screenshot.

## Migration Modes

Plugin rollout is controlled per scenario by one architect-owned mode. A user
request or plugin cannot promote its own mode.

### `off`

The existing compatibility path remains fully authoritative. The new router
and plugin are not loaded or invoked for the scenario.

### `shadow`

The existing path remains authoritative and produces the only publishable
result. The new path may normalize intent, match one plugin, and produce a
plan for comparison. It must stop before `build`, revision allocation, runner
execution, calculation execution, filesystem publication, or GUI input. The
shadow receipt binds both decisions, their hashes, and structured differences.
Shadow failures are diagnostic only: they cannot change the legacy return
value, exception, persisted report, capability response, public status, or GUI
behavior.

### `active`

A scenario may become active only after deterministic routing and plan hashes,
blind benchmark acceptance, reference-isolation checks, compatibility
regression, an explicit plugin version, architect approval, and a tested
fallback path are all recorded. Different scenarios migrate independently.
Failure of an active plugin fails closed or uses only the explicitly reviewed
fallback; it never silently executes arbitrary script text.

Migration mode is internal architect-owned configuration. It is not a public
MCP argument, response-field requirement, or user-controlled plugin selector.
The existing `NaturalLanguagePlan` representation remains the authoritative
public compatibility boundary until a separately reviewed lossless adapter and
golden protocol tests are merged.

## State And Execution Ownership

The orchestrator binds a plugin result to the current revision observed before
planning. The existing state transaction allocates and publishes any new
revision. The existing execution layer validates deterministic scripts,
records attempts, and publishes result metadata. A current-revision mismatch
causes refresh and retry rather than silent rebasing.

Execution remains preview-first. The plugin manifest cannot override
`execution_mode`, confirmation requirements, script safety, or runner policy.

## GUI Ownership

Only the common GUI adapter may activate, snapshot, hot-load, or replay a view.
It reuses the verified existing Materials Studio window and follows the current
single-window and revision-binding rules. A plugin may request a view or
artifact in its plan but cannot drive coordinates, menus, accessibility
controls, or process launch itself.

## Reference And Evaluation Boundary

Runtime candidate construction receives only references allowed by the
normalized request and its access policy. Blind benchmark modelers receive a
semantic task without final reference coordinates. The evaluator runs after
candidate publication and may read both roots, but cannot modify the candidate
or feed hidden coordinates back into `plan` or `build`.

Benchmark acceptance remains external to plugin matching. A plugin cannot
change thresholds, suppress hard failures, or mark itself scientifically
verified.

## Versioning

Plugin manifests use semantic contract versions. A major change is incompatible
and requires an architect migration decision. A minor change may add optional
capabilities without changing existing stage meaning. A patch change may fix
behavior within the same declared contract.

A runtime receipt records the migration mode, selected `plugin_id`, plugin
implementation version, contract version, normalized intent hash, current
revision identity, plan hash, and emitted spec or patch hash. A shadow receipt
also records the authoritative legacy decision and structured comparison.
Reproducing a model requires those bindings, not merely the plugin name.

## Failure And Fallback

A plugin exception, undeclared side effect, output-schema failure, ambiguous
route, or contract-version mismatch fails closed before revision publication.
Fallback to a generic plugin is allowed only when that plugin independently
matches the normalized request and the router records the fallback reason. The
system never falls back by executing arbitrary generated script text.
