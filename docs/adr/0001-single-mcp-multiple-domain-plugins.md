# ADR 0001: Single MCP With Multiple Internal Domain Plugins

- Status: Accepted
- Date: 2026-07-19
- Decision owners: Semiconductor Precision Architecture

## Context

The Materials Studio MCP already exposes a broad compatibility surface,
structured specs and patches, immutable revision state, preview and execution
gates, calculation workflows, and one-window GUI controls. Semiconductor
modeling adds many domains, including bulk, alloy, surface, two-dimensional,
defect, dopant, interface, contact, and device-stack scenarios.

Creating a public MCP service or public tool for every domain would duplicate
state and safety logic, enlarge protocol discovery, and allow incompatible
components to compete for the same Materials Studio window. Running multiple
autonomous agents at runtime would also make geometry generation and revision
ownership difficult to reproduce.

## Decision

Keep one public Materials Studio MCP and retain the existing public tools.
Use `material_studio_live_modeling_request` as the primary high-level entry.
Add future semiconductor capabilities as versioned internal domain plugins
behind a deterministic intent normalizer, capability registry, and router.

Plugins implement `match`, `plan`, `build`, and `validate`. They emit a
`ModelSpec` or `SemanticPatch` and do not register public tools, own revision
state, execute calculations, or control the GUI. Shared state, translators,
validators, runners, and GUI adapters retain those responsibilities.

Parallel agents are a development mechanism only. Their changes are isolated
by Work Orders, branches, worktrees, and PRs; they are not concurrent runtime
controllers.

## Consequences

Positive consequences:

- public protocol growth is bounded;
- existing safety and compatibility behavior is reused;
- routing and geometry can be deterministic and receipt-bound;
- domain implementations can evolve independently behind one contract;
- one component continues to own the current revision and GUI window.

Costs and constraints:

- the plugin contract and router become architect-owned compatibility surfaces;
- ambiguous capability matches must fail closed rather than choose silently;
- shared-contract changes require coordinated migrations and full regression;
- plugins cannot bypass common layers for convenience.

## Alternatives Rejected

### One MCP per domain

Rejected because it duplicates state, execution, and GUI ownership and creates
cross-service consistency problems.

### One public tool per material or scenario

Rejected because tool discovery and compatibility scale with the catalog rather
than with stable user workflows.

### Multiple runtime agents vote on or edit a model

Rejected because the output is harder to reproduce, hidden references are
harder to isolate, and concurrent side effects cannot safely share one current
revision or Materials Studio window.

### Continue adding all logic to `server.py`

Rejected as the target architecture because domain boundaries, ownership, and
independent testing would remain implicit. Existing behavior remains in place
until separately reviewed migrations are implemented.
