# Execution Attempt Observability

Persisted structured executions have two separate responsibilities:

1. `revision_execution.lock` prevents concurrent execution of one immutable
   project/revision.
2. Durable attempt artifacts explain what happened before, during, and after
   the lock was held.

## Artifacts

Each revision output directory may contain:

- `execution_attempts.jsonl`: hash-linked lifecycle events retained across
  sequential re-executions.
- `execution_attempt_state.json`: atomically replaced cache bound to the latest
  journal event ID and SHA-256.
- `result_metadata.json`: canonical backend result containing the same terminal
  `execution_attempt` record.
- `revision_execution.lock`: persistent coordination file whose existence alone
  does not mean the lock is active.

An attempt records a unique ID and sequence, project/revision, process ID,
backend, start/finish timestamps, immutable ModelSpec SHA-256, exact saved
script SHA-256, lock and artifact paths, current revision before and after the
run, result success, and bounded failure details. The `started` event is durable
before MaterialsScript or CIF materialization begins. A terminal `completed` or
`failed` event is appended without replacing prior attempts.

Before creating the `started` event, execution regenerates the deterministic
translator output and compares it byte-for-byte with the immutable saved
revision script. Missing, unreadable, or changed saved scripts return
`revision_execution_script_identity_block`; the runner is not called and no
attempt is created. Runtime inspection also hashes the saved artifact so a
change after execution becomes `identity_mismatch`.

## Read-Only Status

`material_studio_live_project_status` returns `execution_runtime`. It probes the
kernel lock, reads and validates state/journal/result artifacts, then probes the
lock again. `lock_observation_stable=false` means the observation crossed an
execution transition and should be polled again.

| Status | Meaning | Monitor action |
| --- | --- | --- |
| `not_started` | No managed attempt or legacy result exists. | Wait for explicit execute intent. |
| `running` | Both lock probes are active and the latest attempt is running. | Continue read-only polling. |
| `running_unrecorded` | The lock is active before a matching started record is visible. | Poll again; do not execute. |
| `running_identity_mismatch` | The lock is active but the attempt identity, hashes, or paths do not match the revision. | Preserve evidence and reconcile identity after the active run ends. |
| `transitioning` | Lock observations changed or could not be established. | Poll again; do not execute. |
| `completed` | The authoritative journal and canonical result agree on a successful terminal attempt. | Review result, diagnostics, or GUI sync. |
| `failed` | The backend raised, result publication failed, or a completed invocation returned `success=false`. | Review logs; retry only with explicit execute intent. |
| `interrupted` | A running attempt remains but the lock is inactive. | Preserve artifacts and inspect runner/MatServer before explicit retry. |
| `history_invalid` | The managed journal is missing or its syntax, digest chain, sequence, or lifecycle validation failed. | Preserve the available evidence; do not retry or rewrite it. |
| `identity_mismatch` | Attempt hashes, paths, required artifacts, or result success do not match the immutable revision. | Reconcile provenance; do not trust or hot-load the result. |
| `result_missing` | A terminal completion exists without its matching canonical result. | Preserve outputs and reconcile publication before retry. |
| `legacy_completed` | Pre-attempt result metadata remains readable. | Treat as legacy evidence; a future execution creates attempt history. |

The continuation receipt always sets `automatic_retry_allowed=false`. For a
20-minute monitor, call status at each interval. Continue polling only
`running`, `running_unrecorded`, or `transitioning`. Every other nonterminal or
inconsistent state requires explicit review rather than an automatic second
MaterialsScript job.

## Crash Boundaries

The journal, state cache, and result metadata are separate atomic files rather
than one database transaction. Status reconciles expected crash windows:

- started journal durable, lock inactive: `interrupted`;
- terminal event that expects canonical result metadata durable, result absent:
  `result_missing`;
- canonical result terminal attempt newer than a stale running cache: result is
  visible with a history consistency issue instead of being mistaken for an
  active run;
- malformed or non-newline-terminated journal: `history_invalid`, and a new
  execution is refused before the backend starts.

When a later explicitly confirmed execution acquires the revision lock after a
prior `started` event was left without a terminal event, the prior attempt is
first closed with an `interrupted` event. The new attempt then receives the next
sequence number. This is interruption accounting for a new explicit request,
not an automatic retry.

Never delete lock, state, journal, result, or runner artifacts to clear a status.
The lock order remains project state, release, revision execution, release, then
GUI artifact/report. Attempt persistence occurs only while the revision
execution lock is held and never performs GUI input.
