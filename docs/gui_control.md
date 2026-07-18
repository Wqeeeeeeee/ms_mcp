# Materials Studio GUI Control

The GUI layer is optional and does not replace the structured modeling path.
Use `ModelSpec`, `SemanticPatch`, validation, and script preview as the source
of truth, then use GUI tools to inspect the open Materials Studio session.
For crystal `ModelSpec` revisions, explicit execute mode materializes a CIF
artifact and hot-loads that file into the GUI; direct MaterialsScript lattice
construction remains disabled until Copy Script output confirms the local API.

## Capabilities

- `material_studio_gui_status`: detects `MatStudio.exe` and the active window.
  When called without `project_id`/`revision`, it resolves the latest current
  structured project when available and returns `project_resolution`,
  `target_window`, and `target_window_resolution` so clients can distinguish the
  latest MCP revision window from an arbitrary older selected Materials Studio
  window. The response also includes `window_management`, a compact receipt with
  process/window counts, selected-window versus target-window identity, wrapper
  metadata counts, tri-state visibility/minimized/foreground evidence, warnings
  for multi-window ambiguity, and the next GUI tool to
  call (`material_studio_gui_activate`, `material_studio_gui_open_structure`,
  `material_studio_gui_snapshot`, or `material_studio_gui_launch`).
- `material_studio_gui_launch`: launches `MatStudio.exe` if no window is found, or activates the existing window; it can optionally capture a snapshot. If project context is omitted, it resolves the latest current structured project when available and activates that wrapper window.
- `material_studio_gui_activate`: brings the existing Materials Studio window forward. If project context is omitted, it resolves the latest current structured project when available and activates that wrapper window. With `take_snapshot=true`, it captures the activated target window and, when project/revision context is available, refreshes the structured `report.json`, `view_audit.json`, view bundle, and `gui_current_revision` receipt.
- `material_studio_gui_snapshot`: writes a BMP snapshot under `workspace/screenshots/`, returns lightweight visibility metrics, and when project/revision context is available refreshes the current revision's view-audit bundle and `modeling_report`. If `project_id` and `revision` are omitted, it resolves the latest current project when available and writes the screenshot plus GUI log under that resolved project/revision. It refuses capture when Win32 evidence says the exact target is minimized, hidden, or not foreground; call `material_studio_gui_activate(take_snapshot=true)` instead.
- `material_studio_gui_open_structure`: opens an existing structure file only when an existing Materials Studio window is available. On Windows, the fallback uses the already-running window's File/Open dialog to load the generated workspace `.stp` wrapper; it does not implicitly launch `MatStudio.exe`, and it refuses file-open methods that may spawn another Materials Studio window. When `project_id` and `revision` are provided, it also persists the GUI-open artifact into the structured revision's `view_audit.json`, view bundle, `modeling_health`, `modeling_report`, and `report.json` by default. If `project_id` is provided and `revision` is omitted, it resolves that project's current revision; if `project_id` is omitted, it only syncs diagnostics when `structure_path` matches the latest current revision's planned structure.
- `material_studio_gui_apply_current_revision`: validates the saved revision script, exports the current revision's `view_audit.json`/`modeling_health` by default, previews by default, may omit `project_id` for the latest current project, and only executes when `execution_mode="execute"`.
- `material_studio_gui_copy_script_assist`: returns a checklist plus a non-callable reviewed-evidence payload template for extracting exact Materials Studio Copy Script output, with status scoped to the latest current project when no project context is supplied. The template explicitly requires exact window binding and a workspace screenshot and never authorizes script execution.
- `material_studio_gui_prepare_view_replay`: resolves the requested/current revision, computes deterministic Cartesian, crystal-direction, reciprocal-plane-normal, or surface/interface-frame camera parameters, and writes `gui_view_replay_manifest.json` under that revision. Optional `runtime_accessibility_evidence` records named Reset/Movement observations in `gui_view_replay_accessibility_preflight.json`; optional `runtime_ui_evidence` records the separate Miller-plane probe in `gui_view_replay_runtime_preflight.json`. Either artifact is written only after exact revision, wrapper handle/title, and single-window binding succeeds. The tool never activates the window or changes the GUI.
- `material_studio_gui_execute_view_replay`: performs a read-only local UIA probe by default. With explicit `execution_mode="execute"`, it executes exactly one pending front/back/right/left/top/bottom, reviewed isometric, or automation-ready Miller-plane recipe in the existing verified window. Reset and Movement Options use UIA `InvokePattern`; arrows are sent only after the unique enabled/visible `CViewer3DCtrl` accepts semantic keyboard focus. Isometric additionally binds the exact owned Movement window, writes only `numNudgeAngle` through ValuePattern, verifies `numNudgeFactor=2.0` and disabled `cmdNudge*` buttons, closes Movement before each keyboard stage, and restores 45 degrees on success or partial failure. Miller execution is a separate transaction: it captures the pre-action viewport without Reset, verifies the modeless Miller dialog and Properties selection, derives one click from fresh pixel differences, invokes the live-mapped native View Onto command, captures the aligned view, then undoes exactly View Onto and Create Plane and verifies byte-exact viewport restoration. The tool never uses blind/stale coordinates or viewport modifiers and never records visual acceptance. It persists the refreshed preflight, aligned or post-action BMP, structure SHA-256 comparison, and a deliberately incomplete record template.
- `material_studio_gui_record_view_replay`: records Computer Use, reviewed Copy Script, or human evidence for one prepared view in append-only `gui_view_replay_events.jsonl`. Evidence is accepted only when the wrapper identifies the exact project/revision, the current revision is loaded, and the single-window policy passes. For `source="reviewed_copy_script"`, `reviewed_copy_script_evidence`, exact handle/title binding, and a workspace screenshot are mandatory. The script is archived only as inert evidence after static safety checks and is never executed.
- `material_studio_gui_record_visual_confirmation`: persists Computer Use or manual viewport evidence after verifying the current project/revision, exact wrapper title and handle, wrapper metadata, and single-window state. The same path is available through `material_studio_live_modeling_request.visual_confirmation` for restricted MCP allowlists. In an ongoing session, either entry may omit `project_id`; the supplied observed revision must match the latest current project's revision before window binding is evaluated.

Accepted visual and view-replay evidence recomputes the current revision's
diagnostic report, but that automatic re-audit is not itself a user-requested
normality check. The `gui_evidence_reaudit` receipt records the trigger, prior
and current-request diagnostic intent, effective intent, and proves that no
revision, structure, or simulation state was changed. Existing explicit intent
is preserved; a plain evidence-recording request cannot silently set
`normality_check_requested=true`.

GUI open, standalone snapshot, and visual-confirmation re-audits also preserve
the current revision's nondefault view list when the persisted audit matches the
project ID, revision, and immutable spec fingerprint. Explicit `views` still
take precedence. A missing or stale binding recomputes defaults from the current
model: generic models use the standard views, while semiconductor crystals use
front/top/isometric plus interface-, surface-, or lattice-family diagnostic
views. `view_selection`, `view_selection_resolution`, and the visual-confirmation
`gui_evidence_reaudit` receipt expose the selected source, names, reuse flag,
and mismatch reasons.

- `material_studio_live_session_preflight`: read-only session check that combines runner status, GUI status, latest current project, readiness flags, and next recommended tool.
  It also detects when the visible MCP wrapper came from a different trusted workspace. In that case it returns `state="preview_ready_gui_workspace_context_mismatch"`, the exact visible wrapper project/revision, and `recommended_working_dir`; it does not switch workspaces or write external state.
- `material_studio_live_capabilities`: lists the high-level live-modeling entry point, deterministic natural-language templates, supported patch commands, schema paths, GUI tools, and diagnostic fields. `view_replay_automation_policy.local_uia_implementation_contract` is the static source of truth for implemented recipe classes: Cartesian faces, staged isometric, transactional Miller planes, and exact-collinear crystal directions are implemented; non-collinear directions are not. With `include_status=true`, both full and compact responses retain bounded `runner_status`, `gui_status`, and `view_replay_runtime_availability` receipts. Static implementation never authorizes GUI input: runtime transactional-Miller support, the single-window gate, and the current prepared recipe's `automation_ready=true` are still required.
- `material_studio_live_update_with_patch`: applies a semantic patch, creates a new revision, and can execute/open it in the live GUI when explicitly requested.
- `material_studio_live_modeling_request`: high-level entry point for a natural-language request, with optional local template inference or explicit `ModelSpec`/`SemanticPatch` payloads.
- `material_studio_live_project_status`: summarizes the current revision, saved script, planned outputs, latest change, persisted `view_audit.json`/`report.json` receipt, computed audit, `modeling_health`, optional GUI status, and next action. If the audit JSON is missing a GUI-open artifact but `report.json` still has `gui_open`, status uses that fallback to preserve current-revision GUI checks.
  It also returns `gui_view_replay` with the current revision's replay manifest/event paths, replay status, preflight, confirmed-view counts, last event, and next action so resumed sessions and watchdog checks can continue without scanning the workspace.
- `material_studio_model_export_view_audit`: exports `modeling_health`, model-health checks, semiconductor health checks, stable spec fingerprints, rounded atom coordinates, and per-view projection parameters for front/back/right/left/top/bottom/isometric-style inspection.
- `material_studio_model_export_view_bundle`: writes `view_audit.json` plus CSV tables for atoms, bonds, bond angles, dihedrals, connectivity, close contacts, crystal nearest neighbors, crystal coordination, semiconductor lattice volume/density, semiconductor neighbor-pair distances, semiconductor local environments, semiconductor interface profiles, semiconductor interface quality, MOS/gate-stack diagnostics, metal/semiconductor contact diagnostics, semiconductor composition, nominal charge-balance/valence-electron summaries, semiconductor calculation-preflight summaries, reciprocal-lattice/k-point summaries, band-path preflight summaries, band-alignment metadata preflight summaries, semiconductor sublattice balance, semiconductor layer profiles, semiconductor dopants, p-n junctions, dopant fractions, alloy fractions, finite-size/dilution preflight, vacancy/defect and defect-complex summaries, heterostructure strain, surface termination, surface polarity/asymmetry, view summaries, per-view atom projections, projection overlaps, a health summary, and a compact modeling-report summary.

## Operating Model

GUI control is not COM automation and does not hand-write `.xsd` XML.  The
fallback layer uses local Windows process/window APIs.  For generated structure
files such as `.xsd`, it creates a minimal Materials Studio `.stp` project
wrapper under `workspace/gui_projects/` and opens that project, so Materials
Studio starts with an active project and a visible viewer.
When opening an MCP-generated wrapper, the fallback waits for a Materials Studio
window whose title matches the wrapper project name before treating the open as
settled; if the title never appears before the timeout, it falls back to the
latest visible Materials Studio window and reports weaker window-identity
evidence.
`material_studio_gui_status` also reports the resolved `MatStudio.exe` path,
the selected open strategy, whether that strategy may launch another Materials
Studio instance, the same-window dialog capability,
`same_window_open_supported`,
`can_open_structure_in_existing_window`, and `target_window_found` for the
requested or latest-current revision.  `window`/`selected_window_handle` remain
the backend's default window, while `target_window` is the best resolved
structured-project window for live-modeling decisions. If the same-window
dialog path fails or `same_window_open_supported=false`, clients should keep the
generated structure/report artifacts, use Computer Use or manual File/Open
inside the already-open Materials Studio window, and then call
`material_studio_gui_snapshot` or `material_studio_live_project_status` to
refresh diagnostics. The fallback must not create a second Materials Studio
window just to hot-load a revision.
For crystal specs the generated structure file is a CIF artifact; molecule and
imported-structure script executions can still produce `.xsd` outputs.
Precise structure changes should remain spec/patch driven because they are
reproducible, logged, and rollback-safe.

Computer Use can still be used for menu navigation, viewport checks, dialogs,
and Copy Script extraction when its helper is available. If Computer Use is not
available, the MCP GUI fallback still supports process detection, activation,
same-window File/Open dialog loading, BMP snapshots, and operation logs. It does
not silently fall back to `MatStudio.exe <file>` because that path can create
many Materials Studio windows.

After a real Computer Use or manual observation, record visual evidence with
the observed revision, exact top-level window handle, and exact wrapper title.
The server rejects stale or metadata-free windows and writes no revision on
success. This evidence can establish that the expected model is visible in the
current GUI, but it cannot clear geometry, semiconductor metadata, acceptance,
or calculation-readiness failures.

### View replay boundary

The installed Materials Studio 20.1 help and `#SVViewer3d.xml` command registry
confirm Reset View, Recenter, View Onto, View Across, and Fit-to-View. They do
not document a public MaterialsScript API that accepts an arbitrary camera
direction/up vector. Therefore view replay is deliberately split into three
auditable phases:

1. `material_studio_gui_prepare_view_replay` persists exact camera, framing,
   crystallographic metadata, expected projection bounds, target-window
   identity, and single-window preflight state without touching the GUI.
2. When the continuation is `automatic_recipe_ready`, use
   `material_studio_gui_execute_view_replay` for one of the six standard face
   views, the exact reviewed isometric recipe, or a transactional Miller-plane
   recipe, first in preview and then with explicit execute. The local backend
   uses pywinauto UIA, exact Movement ValuePattern readback where required,
   semantic viewport focus, and bounded native commands. The Miller path may use
   only a screenshot-derived transient-plane hit target and must restore the
   exact pre-action viewport after capturing the aligned evidence. This phase
   may issue GUI input and persist mechanical receipts, but cannot write
   accepted replay evidence, mutate the structure, or create a revision.
3. After the action, Computer Use or a reviewer captures a fresh screenshot and
   current accessibility/camera observations, fills the null fields in
   `post_action_record_payload_template`, and calls
   `material_studio_gui_record_view_replay` for that view.

For an externally executed recipe, the pre-action `payload_hint` is deliberately
marked `payload_hint_is_directly_callable=false`: command targets and expected
window identity are instructions, not success receipts. A locally supported
transactional Miller recipe instead resolves the continuation to a directly
callable `material_studio_gui_execute_view_replay` payload with
`execution_mode=preview` and `gui_input_required=false`. That call only performs
the read-only preflight; explicit execute intent is still required for the
confirmation action it returns. In particular,
`accessibility_tree_refreshed`, `invocation_succeeded`, camera-match fields,
Miller-plane cleanup fields, and reviewed Copy Script attestations remain null
until they are observed after the GUI action. `record_call_ready=false` remains
in the continuation and preflight safety receipt until that observation exists.

The local executor serializes one action per project/revision and rechecks the
single-process, single-window, foreground, and wrapper binding immediately
before input and again afterward. A standard-view failure after Reset may leave
a partial camera orientation, but no acceptance event is written; retrying the
same recipe starts from Reset again. Miller execution instead requires its
bounded cleanup to restore the exact pre-action viewport or reports failure.
The post-action/aligned screenshot and mechanical receipt are evidence to
review, not proof that the requested camera or native crystal roll is visually
correct.

For a reviewed Copy Script path, the record call also supplies the exact script
text and review attestations in `reviewed_copy_script_evidence`. The server
computes a SHA-256, scans for external effects, calculations, and structure
mutation, and stores safe text plus JSON metadata under
`outputs/rNNN/gui_copy_script_evidence/`. Unsafe text is not written as a `.pl`
artifact; only its hash and rejection analysis are retained. This evidence path
does not execute the script and cannot bypass camera, screenshot, revision,
window, or single-window gates.

Each accepted reviewed Copy Script event also records SHA-256 and byte-count
evidence for the screenshot, inert script, Copy Script metadata, and current
structure artifact. `material_studio_live_project_status` and a later manifest
refresh recompute those digests. Artifact drift does not delete or rewrite the
append-only event; it changes the trusted replay summary to
`evidence_integrity_reverification_required`, removes the affected view from the
accepted set, and prevents its derived visual confirmation from satisfying GUI
validation until a new bound event is recorded.

Replay events are durably appended to `gui_view_replay_events.jsonl` before the
manifest publishes the new summary. A stable event SHA-256 binds immutable
payload fields while excluding current revalidation fields. On resume, the
manifest event and JSONL event must have one matching ID and digest. Missing,
duplicate, or divergent copies set `event_journal_reverification_required` and
invalidate replay-derived visual confirmation. Read-only status reports but does
not reconcile files automatically; a new real observation is the recovery path.

`material_studio_live_project_status` converts a fully verified replay into the
separate `trusted_clean_view_replay` receipt. This receipt is positive only when
the current project/revision binding is verified, replay and diagnostic view
sets match exactly, all supported views are confirmed under current recipes,
the recommended clean view and every manual-review view are confirmed, artifact
integrity is `verified`, and event-journal consistency is `consistent`. A
positive receipt may resolve the known nonblocking projection, view-warning,
viewport-visibility, and capture-limitation review reasons. The original flags
remain visible as notes through `resolved_visual_review_reasons`; unknown reasons
remain in `unresolved_visual_review_reasons` and continue to block a live-GUI
normality claim. Replay evidence never repairs structural, semiconductor,
acceptance, single-window, or calculation-readiness failures.

`prepare_view_replay` and `record_view_replay` share a project/revision-scoped
kernel file lock. The lock covers each complete manifest mutation, including
evidence persistence and journal publication. Concurrent callers are serialized;
if the bounded wait expires, the operation fails before writing a partial event.
The lock is released by the operating system when a process exits, and callers
must not delete the persistent lock file to force progress.

Structured model revisions use a separate project-scoped
`project_state.lock`. Creation, patch, rollback, redo, restore, and metadata
repair serialize revision allocation, immutable spec/script writes,
`history.jsonl` publication, and `current.json` publication through that lock.
Patch and rollback commits compare the current revision under the lock with the
revision used to prepare the change. They also compare the allocated revision
with the exact revision embedded in the prepared script and output paths. A
concurrent advance returns `project_revision_conflict`; an orphan file that
forces a different safe allocation returns
`project_revision_allocation_conflict`. Both leave the prepared write deferred
and require a fresh status read and regenerated revision-scoped artifacts.

Each individual file is published with `fsync` and atomic replacement, but the
spec, script, history, and current pointer are not one cross-file database
transaction. A process interruption can leave an immutable orphan spec or
script. Recovery skips occupied revision numbers and never overwrites or
deletes those files.

Persisted structured execution has its own
`outputs/rNNN/revision_execution.lock`. The transaction binds the request to
the immutable revision file, re-resolves `current.json` after acquiring the
lock, runs MaterialsScript or materializes the crystal CIF, and atomically
publishes one canonical `result_metadata.json` containing the same
`execution_transaction` receipt returned to the caller. Two requests cannot
run the same revision concurrently. A bounded wait that expires returns
`revision_execution_busy`, `execution_started=false`, and an exact status retry
payload. If the project advanced while the request waited, it returns
`current_revision_execution_block` before invoking the runner. If the project
advances during an execution that already started, the immutable result remains
valid for its old revision, but the receipt records
`current_revision_still_current=false` and the GUI phase refuses to open it.

Each execution also owns a unique durable attempt identity. The output directory
contains `execution_attempts.jsonl`, a hash-linked lifecycle journal, and
`execution_attempt_state.json`, an atomically replaced cache of its journal
head. The `started` event is durable before the backend is invoked. Terminal
events record completion or failure, while canonical `result_metadata.json`
contains the same terminal `execution_attempt`. Attempt records bind the
project/revision, process ID, backend, immutable spec digest, exact saved script
digest, lock path, planned structure, current revision observations, result
success, and bounded error metadata. Sequential re-executions receive new
attempt IDs and monotonically increasing sequences without replacing history.

`material_studio_live_project_status.execution_runtime` probes the execution
lock before and after reading those files. Stable active probes report a
`running` state, including explicit unrecorded or identity-mismatch variants; a
changed probe reports `transitioning`; a durable running attempt with an
inactive lock reports `interrupted`. Terminal records are reconciled against
canonical result metadata and current spec/script identities. Invalid journal
chains, hash or identity drift, and missing canonical results are reported
explicitly and never converted into a successful or automatically retryable
state. See `docs/execution_observability.md` for the status and continuation
contract.

The lock order is strict: finish and release the project state transaction,
acquire and release the revision execution transaction, then acquire
`gui_artifact_report.lock` for current-revision revalidation, hot-load,
snapshot, and report publication. No runner or GUI action occurs while the
state lock is held, and no GUI report lock is held during execution.

Persisting a GUI open, snapshot, or accepted manual or replay-derived visual
confirmation uses a shared `gui_artifact_report.lock` for the same
project/revision. That transaction reads prior GUI artifacts, applies the
operation's reset or append semantics, rebuilds diagnostics, and publishes
`report.json` with `fsync` plus atomic replacement. Concurrent report updates
therefore follow lock acquisition order without losing evidence; a timeout or
publish interruption preserves the previously committed report. This report
lock remains separate from the replay manifest lock.

When a structured project/revision is resolved, the direct
`material_studio_gui_activate` snapshot path,
`material_studio_gui_snapshot`, `material_studio_gui_open_structure`, and
`material_studio_gui_record_visual_confirmation` acquire this lock before
revalidating the target window and before capturing, opening, or binding
evidence. They hold it until report publication and return
`gui_action_transaction`; successful persistence returns the same lock receipt
as `report_write_transaction`. The internal report writer reuses an active
same-revision transaction instead of attempting a second OS lock. If the lock
wait times out, these direct tools do not begin their GUI action.

High-level create, live-patch, and apply-current execute workflows use the GUI
artifact revision lock for their GUI phase, after releasing the distinct
revision execution lock. After the structure exists, they acquire the GUI lock,
verify that the target revision is still current, rerun the single-window
preflight, open the structure, optionally capture a snapshot, and publish the
final report before releasing it. A revision superseded during execution is
never hot-loaded. Successful replies
return one matching `gui_action_transaction` and `report_write_transaction`
with `high_level_hotload` plus the workflow name in `coverage`. If the lock
times out after execution, the structure remains available while GUI open and
report publication are deferred. The response sets
`report_persistence_deferred=true`,
`execution_completed_before_gui_transaction=true`, and returns
`gui_open_retry_tool` with an exact `gui_open_retry_payload`; retry that open
after the active transaction completes.

The same transaction owns the final high-level orchestration metadata. Live
`show_current`, natural-language patch, rollback, redo, and restore calls pass
their workflow, request, revision, execution-source, and diagnostic intent into
the nested apply or update operation before diagnostics are rebuilt. Their
persisted `modeling_report` and `report.json` are therefore final when the GUI
artifact lock is released. Callers and maintainers must not perform a second
post-lock report write, because it could replace evidence appended by a
concurrent snapshot or visual-confirmation call.

Diagnostic audit and bundle exports participate in this transaction domain as
well. For a persisted revision,
`material_studio_model_export_view_audit` and
`material_studio_model_export_view_bundle` acquire the lock before rereading
prior GUI artifacts, rechecking the current revision, probing the target
window, optionally capturing it, and writing the bundle plus `report.json`.
They return `report_write_transaction`; when a GUI snapshot was attempted they
also return the same receipt as `gui_action_transaction`. The lock is required
even with `include_gui_snapshot=false`, because the diagnostic bundle and
report are still writes. Natural-language `inspect_current` holds one outer
transaction while its nested bundle export and final inspection report run, so
all report writes remain serialized.

If the lock wait expires, the export returns
`diagnostic_export_deferred=true`, leaves the committed report and GUI
untouched, and provides `diagnostic_export_retry_tool` plus the exact
`diagnostic_export_retry_payload`. If the project advances while an export is
waiting, the old revision is not captured or rewritten; the response includes
`diagnostic_export_current_revision_block` and retries against current state.
An inline `ModelSpec` uses the same output lock. If its project/revision already
exists with different immutable content, the export is rejected rather than
replacing that revision's diagnostics.

When the direct replay tool is not enabled in the active MCP allowlist, submit
the same evidence through
`material_studio_live_modeling_request.view_replay_confirmation`. This payload
uses `extra="forbid"` and requires `view_name`, `model_visible`,
`camera_matches_manifest`, `expected_revision`, `expected_window_handle`, and
the exact `expected_window_title`. The server resolves the current project
revision first, rejects stale or mismatched bindings before appending any
event, and records `native_command_id` only when it matches a reviewed local
Materials Studio 20.1 3D-view command. Documented keyboard recipes may also
record `key_sequence`, `reset_before_key_sequence`,
`rotation_increment_degrees`, and `modifier_keys`; staged recipes additionally
record `keyboard_stages`, the restored angle, Movement command/control IDs,
Screen factor, and dialog-close evidence. Supplied values must match the
prepared recipe before an event can be appended.

Every manifest view also has an `execution_recipe`. The companion
`replay_continuation` receipt reports pending, automatic-ready, and
review-required view names plus the next view and its camera/projection checks.
On Materials Studio 20.1, all six face-aligned orthographic recipes are
statically eligible when the local command registry and installed keyboard
help are verified. They become automatic-ready only when a refreshed
current-window accessibility observation proves either the exact named Reset
View control is invocable or the server derives Reset from an exact anonymous
toolbar sequence verified against the installed registry SHA-256. The target
document must be visible, and keyboard views require a verified empty viewport
focus target. `front` targets Reset View; `back` uses
Reset + `Left x4`; `right` uses Reset + `Up x2, Left x2`; `left` uses Reset +
`Up x2, Right x2`; `top` uses Reset + `Up x2`; and `bottom` uses Reset +
`Left x4, Down x2`. The installed help defines each arrow rotation as 45
degrees and
states that Shift+arrow rotates selected objects, so Shift is prohibited and
the camera axis layout plus projection/overlap counts require a fresh visual
postcheck. Isometric additionally requires the named Movement control or its
server-verified anonymous toolbar target at runtime before its staged recipe
can become automatic-ready: Reset, `45
degrees: Up x2, Left x3`, then `35.26438968 degrees: Down`.
It must show A left-down, B right-down, C up, restore Angle to 45 degrees,
preserve Screen factor 2.0, and close Movement.

For a crystal model, every standard face or isometric recipe also carries the
`crystal_standard_view_with_native_in_plane_roll` camera contract. Acceptance
requires a fresh workspace screenshot plus strict `crystal_camera_evidence`:
`view_direction_matches_manifest=true`,
`native_in_plane_roll_observed=true`, and the required nullable
`analytic_in_plane_basis_matches_manifest`. The last field may be `false` or
`null`, because Reset View and unmodified arrow rotation establish Materials
Studio's native in-plane roll rather than the audit's exact analytic
`camera_up`/`camera_right` basis. `camera_matches_manifest=true` therefore means
the requested direction and native-roll contract were observed. Molecule
standard views retain the prior camera contract and do not require this nested
crystal evidence.

The observation is submitted through `runtime_accessibility_evidence` and is
persisted as `gui_view_replay_accessibility_preflight.json`. Static registry or
help files never substitute for that live binding. For unnamed toolbar
children, `anonymous_toolbars` must include the full ordered direct-child tree;
the server checks exact count, checkbox/separator roles, separator positions,
installed registry identity and SHA-256, and target enabled state. Only the
returned `verified_anonymous_toolbar_child` target is eligible, and its element
index is ephemeral: refresh and re-check the same tree immediately before use.
Recording requires the matching `accessibility_command_uses` receipt. A
partial, stale, reordered, disabled, or client-guessed index remains blocked;
blind toolbar coordinates are never acceptable.

If that exact verified invocation later fails the visual model/camera
postcheck, the manifest preserves the failed event and suppresses automatic
retry of the same semantic mapping. A failed `front` Reset baseline also
pauses `back`, `right`, `left`, `top`, `bottom`, and `isometric` recipes that
depend on that same Reset mapping. `replay_continuation.status` becomes
`automatic_recipe_postcheck_failed` and routes to reviewed Copy Script or
manual GUI review. Re-preparing the manifest does not clear this gate; only
new success evidence with verified artifact integrity can supersede it. View
list changes preserve all replay events for the immutable revision, so
preparing only a dependent view cannot bypass the failed baseline.

`crystal_plane_*` views have a separate documented MS 20.1 recipe. Installed
Miller Plane, Properties Explorer, and View Onto registry/help evidence is
necessary but not sufficient. The local transactional path can become
automation-ready from a current bound semantic viewport preflight plus the
installed registry/help contract; it then verifies and persists the Miller UI
evidence inside the transaction before Create. An externally driven path still
requires a current `gui_view_replay_runtime_preflight.json` whose revision,
wrapper handle/title, single-window binding, and semantic selection profile all
match. Missing, incomplete, or stale external evidence returns
`runtime_ui_preflight_required` and keeps that external path blocked. The
continuation hint identifies the evidence schema and window binding but never
supplies example observed values.

Miller replay does not invoke Reset. It captures a fresh pre-action viewport,
and `cmdViewer3DViewOnto` establishes the temporary aligned plane-normal view.
Consequently a verified generic front Reset orientation failure suppresses only
Reset-dependent standard views; it does not suppress an otherwise-ready Miller
transaction whose `camera_result_depends_on_reset_baseline=false`.

Open Tools > Miller Planes only through the verified keyboard menu path
`Alt+T`, then `M`. Do not invoke that menu item with a pointer or accessibility
click: in MS 20.1 the release can click through into the modeless dialog and
activate Create. After the modeless dialog appears, refresh its child-window
state and target `TxtHKL`, `CmdCreate`, and the close control only through an
accessibility element that resolves inside those child bounds or a coordinate
derived from that fresh child screenshot. Never reuse a parent-window
screenshot coordinate for a modeless dialog control, and reject duplicated
accessibility elements that resolve outside the dialog. Do not assume that
`Ctrl+A` replaced the existing `TxtHKL` contents. Prefer exact accessibility
`set_value`; when that is unsupported, follow the prepared
`dialog_index_entry_contract` using only unmodified keys. If the fresh observed
value has a verified affix relation with the target, repair it first; otherwise,
if it shares a prefix with the target, focus `End`, backspace only the differing
suffix, and type only the target suffix. For a cross-offset overlap, preserve the
longest common contiguous substring, apply exactly one nonempty edge repair in
observed-prefix, observed-suffix, expected-prefix, expected-suffix order, and
replan from the next fresh readback. Perform at most one full replacement
using the fresh observed character count. MS 20.1 can retain an arbitrary prefix
or suffix before or after that operation. Repair only a verified affix
relation: if the fresh value ends with the target, focus `Home` and delete the
retained prefix character count; if the target ends with the nonempty fresh
value, focus `Home` and type only the missing target prefix. After one full
replacement, an unrelated value must abort. Never use Shift or a selection range.
MS 20.1's ActiveX field can drop a destructive key when `Home`/`End` and
`Backspace`/`Delete` are injected too quickly. Obey the recipe timing contract:
wait `200 ms` after `Home` or `End`, wait `200 ms` between every repeated
`Backspace` or `Delete`, and never batch those key events. After typing or
deleting, wait `500 ms` before obtaining the next fresh child readback. Refresh
and replan after every mutation and compare the trimmed accessibility value exactly with
`dialog_miller_indices_text` before Create. A mismatch blocks Create and must be
corrected and reverified; abort without Create after the final strategy fails.

Persisted replay recipes are versioned safety contracts. Read
`gui_view_replay.recipe_contract` before any pending replay. When it reports
`pending_recipe_upgrade_required`, the continuation status is
`recipe_upgrade_required` and new replay evidence is rejected. Call the
high-level `continue_view_replay` workflow to regenerate the manifest with the
current recipe schemas. The migration preserves replay events and does not
create a model revision or change the structure. An older crystal standard-view
event may remain historically `accepted=true`, but it is excluded from current
`accepted_view_names` until a fresh screenshot and current
`crystal_camera_evidence` are recorded. Compact status exposes this gate through
`current_camera_evidence_reverification_view_names` in the replay summary and
recipe contract. Full and compact status also expose a reconciled `next_action`
and `next_action_resolution`. A continuation safety override supersedes an older
GUI activation or execution hint; when `stale_recipe_execution_blocked=true`,
call the directly callable high-level recipe-upgrade payload before any replay
input. Reading status performs this reconciliation in memory only and does not
rewrite the manifest, create a revision, or change the structure. Compact MCP
responses also include the Miller dialog correction timing contract required for
execution.
Persist the observed text, the
`fresh_modeless_child_accessibility_value` source, and the verification result in
`miller_plane_evidence`. If an unexpected
default plane is created, invoke only the
exact named `Undo Create Miller Plane`, verify a clean document, no temporary
node, and an unchanged structure hash, then abort the replay attempt and run
the preflight again.

After that gate passes, the recipe captures the current viewport and creates
exactly one temporary plane with the requested three-index dialog values. On an installation
that exposes Object Tree, it may isolate the exact new
`<Miller Family>/<Miller Parallel Planes>/<Miller Plane>` leaf by before/after
diff and select its semantic item rectangle. The local MS 20.1 installation
instead verifies that Object Tree is hidden and that Project Explorer contains
project documents, so Project Explorer must not be used as a substitute. Its
supported `viewport_unique_plane_properties_verified` profile captures fresh
screenshots before and after creation, derives one unique newly rendered plane
region, selects only that fresh region with no modifiers, and verifies
`Filter=Miller Plane` plus the expected Miller label in Properties Explorer.
Only then may it invoke View Onto after live toolbar inspection verifies the
installed `Selection=33288`, `Recenter=33296`, `View Onto=33297`, and
`Fit=33299` numeric mapping. It captures the aligned view before cleanup, then
must observe and invoke exactly `Undo View Onto Miller Plane` followed by
`Undo Create Miller Plane`. Reset, Recenter, and any additional cleanup command
are forbidden. Success requires a clean document, no temporary plane, exact
pixel restoration of the pre-action viewport, and unchanged wrapper source
structure SHA-256. These observations are submitted in
`miller_plane_evidence`; local execution still requires later visual acceptance.

The verified MS 20.1 runtime has several accessibility details that are part of
the safety contract. The dirty marker appears on the internal viewer document
title (`model_*.cif *`), while the outer wrapper title may remain unchanged.
The owner-drawn View/Explorers/Properties Explorer menu can expose blank submenu
labels; the fallback accepts that shape only for the exact live command ID
`33439`. Properties uses a virtualized `vGridControl`, so the unique
`MillerIndex Record 0` `DataItem` can report `is_visible=false`; this is accepted
only when the Properties pane and grid are visible and its exact value is the
prepared label such as `(001)`. A viewer on a negative-coordinate secondary
monitor may extend below its wrapper window. Capture therefore intersects the
visible `CViewer3DCtrl`, viewer pane, internal document, `MDIClient`, and exact
target-window rectangles, requiring that ancestry to terminate at the expected
window handle. This excludes status-bar/help text outside the MDI client while
retaining strict zero-pixel comparison of the actual visible viewport; a hidden
ancestor, broken binding, or undersized intersection fails closed. None of
these runtime details relax exact window, revision, label, undo, or hash
verification.

Native View Onto guarantees the requested reciprocal-plane normal, but local
help documents that its in-plane roll uses the smallest acute angle from the
initial orientation. The replay contract is therefore
`crystal_plane_normal_with_native_in_plane_roll`: it does not claim exact
agreement with the audit's analytic `camera_up` or `camera_right`. A lattice
direction (`crystal_*`) recipe may reuse View Onto only when the audit finds an
exact low-index integer Miller-plane normal collinear with that direct-space
direction. Such a recipe records the mapped plane, uses the separate
`crystal_lattice_direction_via_collinear_plane_normal_with_native_in_plane_roll`
scope, and additionally requires
`direct_lattice_direction_matches_manifest=true`. No same-index `[uvw]=(hkl)`
assumption is made. Directions without an exact mapping remain review-gated.
Spin/Roll/Rock are continuous and nondeterministic;
`cmdNudge*` and object-align commands modify selected structure objects and are
prohibited for camera replay.

A screenshot taken before target activation may contain an occluding Codex or
other application window even when the requested window handle belongs to
Materials Studio. Replay automation must activate the target, re-check the
title/wrapper identity, and only then inspect or send input. Unverified unnamed
toolbar controls and blind coordinates are not an accepted replay backend. The
narrow exception is a current-window, full-sequence, installed-registry-backed
`verified_anonymous_toolbar_child` target returned by the preparation tool.
Snapshot and open results include BMP analysis fields such as dimensions,
sampled color count, dominant color ratio, non-dominant ratio, and
`likely_nonblank`; these help flag empty or failed captures. They also include
central model-viewport fields such as `viewport_likely_visible_model`,
`viewport_capture_diagnostic`, and `viewport_capture_limitation_possible`. A
uniform dark viewport means the generated structure may still be loaded, but the
Windows GDI/BitBlt fallback did not capture visible 3D model pixels. Treat that
as a visual-validation warning and use a fresh GUI snapshot, manual/Computer Use
viewport inspection, or the Materials Studio File | Export GUI path before
claiming that the visible model is normal.
Materials Studio 20.1 scripting supports `document->Export` for structure files,
but the local scripting documentation states that `.bmp` files cannot be
exported through scripting; BMP image export is a GUI dialog workflow.
Live and standalone audit responses include `modeling_health.verdict` as the
quick status field.  Orthographic projection warnings remain in
`modeling_health.warnings` and `view_audit.json`, but they do not by themselves
make a normal planar structure fail.
For semiconductor models, `modeling_health.checks` also promotes domain-specific
signals such as formula/reduced formula, element count, nominal valence-electron count, electron-count parity, carrier-type hint, CASTEP calculation-preflight status/cutoff/k-point checks, finite-size/dilution warnings for isolated dopants or defects, p-n junction count/axis/p-side and n-side dopants, lattice constants, cell volume, volume per non-passivant atom, non-passivant atom density, nearest-neighbor pair count/type count and min/mean/max distance, local-environment coordination outlier count, local angle min/mean/max, tetrahedral angle deviation mean/max, interface segment count, interface transition count, mixed-layer count, abrupt-interface flag, slab vacuum fraction, III-V, II-VI, and TMD metal/chalcogen counts, sublattice balance, vacancy/interstitial/antisite count and fraction, layer count and minimum interlayer spacing, dopant-fraction rounding, defect
missing-bond estimate, interstitial coordination outlier count, antisite
same-sublattice neighbor count, dopant coordination outliers, slab dangling-bond
estimate, passivation coverage, surface polarity/asymmetry warnings, and
heterostructure strain warnings.
Concrete dopant-site records are also compared with the current atom table.
`dopant_site_summary.metadata_consistent=false` or a nonzero
`stale_site_count` is a structural consistency error, not a GUI-only warning;
it blocks model normality and calculation readiness even when the correct
revision is visibly hot-loaded. `semiconductor_dopant_sites.csv` records the
actual element, record status, and consistency error for each stale entry.
Use `material_studio_project_reconcile_dopant_metadata` to repair this state.
Preview mode creates a metadata-only revision and re-audits it without opening
another Materials Studio window. Execute mode may materialize and hot-load that
revision only after explicit confirmation and remains subject to the single-
window gate. The repair receipt must show that structure and simulation data
were unchanged.
Path and wrapper identity alone do not prove that a crystal file still contains
the current revision. Crystal hot-load responses therefore include
`structure_artifact_validation`, generated by parsing the materialized CIF and
comparing its atom IDs, elements, fractional coordinates, and lattice with the
current `CrystalSpec`. The exported view bundle contains
`structure_artifact_validation.json` and `.csv`. This parser is intentionally
scoped to the deterministic generated-style CIF used by the MCP workflow; it
does not claim general XSD or arbitrary CIF semantic coverage.
`ready_with_warnings` and
`passed_with_warnings` mean the model was generated or hot-loaded, but the
semiconductor state should be reviewed before calculation.
Responses that persist `view_audit.json` also include `view_bundle_manifest_path`,
`view_bundle_files`, and `view_bundle_row_counts`, so a client can immediately
inspect CSV tables after a live update or GUI apply.
The bundle includes `modeling_health_summary.csv`, a one-row machine-readable
receipt with the verdict, error/warning counts, GUI open/window identity,
GUI snapshot checks, and key semiconductor checks for fast external validation.
It also includes `modeling_report_summary.csv`, a one-row client receipt for
`normality`, readiness booleans, blocking/review reasons, GUI trust state,
acceptance pass/fail state, view status, basic geometry/view counts, key
semiconductor flags, CASTEP calculation-preflight status/task/k-point fields,
structure path, and report/audit paths.
They also write `report.json` in the revision output directory and return
`report_json_path` as the stable compact report entry point.
Live, audit, and GUI-apply responses also include `modeling_report`, a compact
display-ready receipt with `normality`, revision, health verdict, structure
path, GUI hot-load state, `modeling_report.gui.snapshot_path` when a BMP
snapshot was captured, GUI snapshot readability/nonblank metrics, sampled color
counts, open method, window title, `modeling_report.gui.visual_validation`,
diagnostics file paths, `modeling_report.revision_delta`, and the recommended
next action. When a fresh GUI status probe reports `window_found=false`,
`material_studio_live_project_status` treats any older persisted GUI-open
artifact as stale and recommends reloading the current revision instead of
claiming the visible model is current. GUI reports also expose
`window_identity_verification` with values such as `verified`, `mismatched`,
`unverified`, or `no_window`; `unverified` means a window exists but the GUI
fallback could not prove its project/revision from MCP project-wrapper metadata,
so hot-loaded responses are reported as `review_warnings` rather than
`hot_loaded_and_passed` until stronger GUI identity evidence is available.
`open_identity_verification` is reported separately: it describes whether the
recorded open action matched the current revision, while
`window_identity_verification` describes the currently visible window evidence.
For @mcp clients, read top-level `live_summary` or
`modeling_report.live_summary` first; it is a stable compact receipt for the
current project/revision, hot-load state, normality, next-edit/calculation
readiness, acceptance pass/fail state, view status, semiconductor rule/risk
flags, key CSV/report paths, and next action. It also includes a compact
next-action projection:
`next_action_id`, `next_action_tool`, `next_action_payload_hint`, confirmation
flags, and `next_action_ready`, so simple @mcp clients can call the next tool
without parsing prose. `modeling_report.next_action_plan` is the full structured
call recipe for clients; it includes `action_id`, `recommended_tool`,
`payload_hint`, confirmation requirement, readiness booleans, key artifact
paths, and the blocking/review reasons behind the recommendation.
`modeling_report.live_readiness`
is the underlying orchestration decision; it combines script validity,
`ModelSpec.acceptance` review, change validation, GUI status, semiconductor
review, and multi-view review into a state, readiness booleans,
user-confirmation requirement, recommended tool, blocking reasons, and review
reasons. `revision_delta` summarizes the current revision against the
previous revision: atom-count changes, element-count deltas, added/deleted or
moved atoms, bond changes, lattice changes, simulation setting changes, and
metadata/output key changes when available. `modeling_report.change_validation`
cross-checks that delta against the current `view_audit`: final atom and
element counts, added/deleted/substituted/moved atoms, molecule bond counts and
bond changes, and crystal lattice values when available. Treat
`change_validation.ok=false` as a review warning even if script generation
succeeded.
`modeling_report.change_receipt` is the compact receipt for the latest
create/patch/live update. It combines the user request, base/new revision,
delta, GUI current-revision state, formula, dopants, strain, readiness, review
reasons, and key structure/snapshot paths for client display. Its
`gui_current_revision` subobject mirrors the top-level GUI trust receipt, so
client code can decide whether to reload, snapshot, or continue from the same
change receipt it already renders.
`modeling_report.acceptance_review` evaluates the revision against
`ModelSpec.acceptance` constraints such as `max_warnings` and required
convergence evidence. If it reports `ok=false`, `live_readiness` includes
`acceptance_criteria_failed` as a review reason and a calculation-blocking
reason; live editing or hot-loading can continue when no structural or GUI
blocking reasons remain.
For live GUI trust, also inspect `modeling_report.gui.loaded_current_revision`,
`revision_matches_current`, `structure_path_matches_current`, and
`stale_reasons`. A GUI-open artifact from an older revision or different
structure path is reported as stale and downgrades `normality` to
`review_warnings` rather than `hot_loaded_and_passed`. A current GUI-open
artifact with `window_identity_verification=unverified` is also a review warning:
the structure may be editable and visually available, but the window identity is
not strong enough to claim full GUI verification. If live status is requested
without a fresh GUI probe, the persisted window-identity evidence is preserved
instead of being upgraded simply because the old `gui_open` artifact still
exists.
`material_studio_live_project_status` and every live `modeling_report` also
surface `gui_current_revision`: a compact current-window trust receipt with
`status`, `loaded_current_revision`, `needs_reload`, `needs_activation`, `needs_snapshot`,
`stale_reasons`, and a `payload_hint` for the next GUI tool. Prefer this field
when deciding whether to reload the current revision, activate an already-loaded
target revision window, capture a fresh snapshot, or continue with the next
model edit. In multi-window sessions, `needs_activation=true` means the target
revision has window evidence and should be activated with
`material_studio_gui_activate(take_snapshot=true)` instead of re-hot-loading the structure.
The same rule applies to a single loaded window that is minimized or explicitly
observed outside the foreground. `window_management.activation_reasons` explains
the gate, and no screenshot or File/Open input is issued until activation is
re-enumerated against the same handle.
For semiconductor workflows, `modeling_report.semiconductor_review` is the
compact client-facing material receipt. It pulls formula, lattice, CASTEP task,
k-point estimates, band-path preflight, charge balance, dopant/alloy/defect
state, interface or quantum-well state, surface passivation/polarity, risk
flags, and the next action out of the full `semiconductor_health` object.
Single oxide-interface or gate-stack templates such as Si/SiO2 MOS gate oxide,
Al/SiO2/Si MOS capacitor, Al/SiO2/6H-SiC(0001) Si-face MOS capacitor,
TiN/HfO2/Si high-k MOS capacitor, and Cu/SiO2 use the
same interface review fields, but are treated as one-shot interface starts
rather than periodic quantum-well stacks or passivated semiconductor slabs. MOS
capacitor templates also emit a dedicated `gate_stack_summary` and
`semiconductor_gate_stack.csv` table that check the declared material sequence,
including `6H-SiC -> SiO2 -> Al`, `Si -> SiO2 -> Al`, or `Si -> HfO2 -> TiN`,
plus gate/oxide/channel presence, declared
oxide/gate/channel thicknesses, and per-segment layer spans. Si/SiO2,
Al/SiO2/Si, and TiN/HfO2/Si mark mixed oxide or compound gate layers as
expected, so those layers remain visible in `interface_profile_summary` without
becoming a mixed-interface risk flag.
The 6H-SiC MOS start reuses the reviewed centered `2x2` six-bilayer Si-face
channel and its hydrogen-passivated C back face. Its two mixed Si/O planes are
only deterministic thickness and visualization markers; they do not establish
an amorphous oxide network, relaxed interface, band offsets, trap states, or
device readiness.
Follow-up MOS/gate-stack thickness edits such as `set HfO2 thickness to 6
angstrom` or `make TiN gate thickness 2 angstrom` use a structured
`set_gate_stack_thickness` patch. The patch adjusts the target segment along
the interface axis, shifts upper stack segments, records
`gate_stack_thickness_edits`, and leaves GUI tools to reload or snapshot the
resulting revision.
Al/Si Schottky contact templates use `metal_semiconductor_contact_summary` and
`semiconductor_contact.csv` so reports show metal/semiconductor roles, contact
type, gap, thicknesses, sequence checks, and metadata-only Schottky-Mott barrier
preflight fields without misclassifying the contact as a quantum-well stack or
surface-passivation problem. The barrier preflight is a quick reference check,
not a DFT band-alignment calculation.
Heterostructure and MQW templates with electronic metadata use
`band_alignment_summary` and `semiconductor_band_alignment.csv` to expose
electron-affinity band-offset preflight values, type-I confinement hints, and
well/barrier review warnings. These values are for model sanity screening only;
they do not replace band-alignment calculations.
Wurtzite III-nitride HEMT-style templates such as AlGaN/GaN and AlN/GaN also
surface `polarization_2deg_summary` and
`semiconductor_polarization_2deg.csv` with metadata-only polarization,
sheet-density, and electron-barrier checks. Treat these as GUI/review receipts,
not as quantitative electrostatic or device results.
For variable formula alloys, the same preflight can interpolate common
endpoint reference values so changed compositions keep a usable quick-check
instead of degrading to missing metadata.
For multi-view visual review, `modeling_report.view_review` is the compact
entry point. It reports supported view count, per-view projection counts,
overlap/warning views, best view candidates, GUI visual-validation state, risk
flags, critical flags, and the next action. Projection overlaps are review
flags rather than hard failures; unsupported views, projection atom-count
mismatches, stale GUI loads, or blank GUI snapshots are critical visual
problems.
`modeling_report.inspection` summarizes key geometry checks without opening
CSV files: atom and bond counts, element counts, bond-angle and dihedral counts,
close-contact count, view overlap count, bond/angle/dihedral statistics,
crystal nearest-neighbor/coordination statistics, and `semiconductor_health`.
For semiconductor templates, `inspection.semiconductor_health` reports the
lattice summary, neighbor-distance summary, local-environment/tetrahedral-angle or TMD 6/3 coordination summary, interface-profile summary, interface-quality/material-sequence summary, MOS/gate-stack sequence and thickness summary, metal/semiconductor contact sequence and thickness summary, III-nitride polarization/2DEG preflight, composition summary, nominal charge-balance/valence-electron summary, calculation-preflight summary, sublattice balance summary, detected tetrahedral or TMD rule, expected coordination, per-element coordination
statistics, neighbor pair counts, unexpected III-V, II-VI, or TMD near-neighbor pair types,
layer profiles along the interface/surface axis, superlattice period summaries, and dopant/dopant-fraction/alloy summaries with host elements, dopant concentration, requested versus actual dopant fraction, alloy fraction, donor/acceptor
role hints, and dopant coordination statistics. Vacancy, interstitial, and
antisite patches add defect summaries with the removed, added, or substituted
site, estimated concentration, nearest neighbor IDs, under-coordinated neighbor
counts, interstitial coordination outliers, and antisite same-sublattice
neighbors.
Nearest-neighbor divacancy patches additionally bind two vacancy records to one
`defect_complexes` entry. The audit independently recomputes the periodic
minimum-image pair distance and exports
`semiconductor_defect_complexes.csv` with member IDs/elements, recorded and
recomputed distances, neighbor threshold, image offset, selection rule, and
integrity status. A failed member or distance binding is a health error. This
receipt verifies the deterministic structural edit only; it does not establish
a relaxed geometry, charge state, or formation energy. Preview remains the
default, while explicit execute/hot-load continues through the existing
single-window GUI path.
Explicitly distributed alloy or dopant-fraction requests use a deterministic
periodic maximin site-selection receipt. The receipt binds the full candidate
site geometry, 3x3 minimum-image distance mode, selected IDs, each farthest-point
step, atom-ID-order baseline, and SHA-256. The alloy and dopant-fraction CSVs
include current selected-pair distance statistics, baseline improvement,
candidate-nearest pair count, integrity, and current-geometry replay status.
Receipt inconsistency is a structural health error; a later legitimate geometry
change preserves historical integrity but marks current-geometry replay
unavailable. This heuristic is not an SQS or a claim of alloy optimality. It
does not change preview-first execution or the one-window GUI policy. Exact
selection is capped at 512 candidate sites so oversized requests fail closed
instead of starting unbounded pair-distance work.
The same receipt drives `semiconductor_site_pair_distribution.csv`, which bins
all candidate pairs into numerical periodic-distance shells and compares the
selected pair count with the atom-ID baseline and the exact fixed-composition
expectation. A nearest-shell excess is a review warning, while receipt,
selection-replay, geometry-digest, or pair-conservation failure is an integrity
error. A nearest-shell reduction is descriptive evidence of the requested
separation heuristic, not proof of SQS quality, random-alloy statistics,
relaxation, or calculation readiness. After geometry drift, the historical CSV
may still verify its recorded input but reports
`current_geometry_applicable=false`; rerun the selection workflow or inspect the
current geometry before using pair distances for live-model review.
`semiconductor_site_short_range_order.csv` reuses those SHA-bound shells and
partitions each one into selected-selected, unselected-unselected, and mixed
pairs. It reports both the global-composition Warren-Cowley-like pair-count alpha
and an exact fixed-composition correction. Negative corrected values are labeled
ordering-like unlike-pair enrichment; positive values are labeled clustering-like
unlike-pair depletion. A clustering-like nearest shell is a review warning, not
proof of phase separation. Receipt, source pair-distribution, occupancy
partition, or analysis integrity failure is blocking. Because the audit counts
unique finite-cell pairs without reconstructing periodic-image multiplicity, it
must keep `classical_bulk_shell_interpretation_ready=false` and cannot be used as
an SQS, equilibrium SRO, thermodynamic, relaxation, or calculation-readiness
claim. The GUI may display the same current revision for visual review, but no
new automatic camera or structure mutation is authorized by this diagnostic.
For heterostructure templates,
it also reports interface metadata, in-plane lattice, cell volume, density, per-material reference
lattice values, epitaxial strain percentages, and lattice mismatch relative to
the substrate. For slab templates, it reports surface termination diagnostics:
surface atom IDs, under-coordinated atom counts, dangling-bond estimates,
passivant bond counts, passivation coverage, and a surface-polarity/asymmetry
heuristic comparing top and bottom non-passivant formulas and passivant-bond
counts. Plain hydrogen passivation is
conservative; explicit requests such as `fully hydrogen passivate both
surfaces` or `saturate all dangling bonds` add enough deterministic H positions
to satisfy the missing-coordination estimate when possible.
The charge-balance summary is a nominal valence-electron heuristic, not a DFT
charge analysis; use it to flag odd-electron cells and quick donor/acceptor
carrier hints before spin-sensitive or charged calculations.
The calculation-preflight summary is a static setup check, not a convergence
test. It reports CASTEP task family/intent, cutoff energy, k-point mode, coarse
k-point warnings, slab surface-normal k-point risks, whether the task can change
structure, whether property tasks need a prior relaxed structure, execution
risk, and the next action before expensive calculations.
The band-path preflight summary exports `semiconductor_band_path.csv` for
diamond/zinc-blende fcc and wurtzite or 2D TMD hexagonal starts. Use it as a quick
BandStructure path review aid, then still inspect Materials Studio/CASTEP
settings before execution.
The surface-polarity summary is also heuristic. It flags asymmetric top/bottom
surface compositions such as Ga-terminated versus As-terminated slabs, plus
one-sided or uneven passivation, so those cases are reviewed before slab DFT.
The finite-size summary is a dilute-defect preflight. It flags isolated dopant
or defect models when the non-passivant cell has fewer than 64 atoms or the
largest isolated dopant/defect fraction exceeds 3%, so users know to consider a
larger supercell before quantitative calculations.
For semiconductor slab templates it also includes `inspection.surface` with
surface orientation, surface axis, slab thickness, vacuum thickness, and
termination metadata, plus `inspection.slab_vacuum` with declared vacuum,
atom-center extent, inferred atom-center vacuum, and a `vacuum_ok` flag.
It also includes `inspection.views`, a compact per-view list with camera
vectors, camera position/distance, orthographic framing, projection bounding
boxes/spans, projected atom counts, and overlap/warning counts for quick UI
display.
When a persisted `view_audit.json` contains an executed `modeling_health` result
and a GUI-open artifact, `material_studio_live_project_status` reports that
same hot-loaded state instead of downgrading the project to a preview summary.

## Safe Workflow

1. If the live session state is unclear, call `material_studio_live_session_preflight` first. It reports whether preview, execute, GUI hot-load, crystal CIF hot-load, and latest-project follow-ups are currently ready. If it recommends `material_studio_gui_launch`, call that tool before attempting a hot-load. If it reports `ready_for_live_edit_gui_review`, use the returned `material_studio_gui_open_structure` payload to reload the latest current revision into the GUI and capture a snapshot before continuing; keep the returned `project_id` and `revision` when present. If it reports `ready_for_live_edit_gui_activation`, call `material_studio_gui_activate` with the returned project/revision and `take_snapshot=true` to bring the already-loaded target revision window forward without re-hot-loading. `material_studio_gui_open_structure` can also write the GUI-open artifact back to the structured report when `project_id` is omitted, but only if the opened `structure_path` matches the latest current revision's planned structure.
2. If the request shape is unclear, call `material_studio_live_capabilities` and use the returned templates, patch commands, and schemas to choose the supported path.
3. Create or modify a structure with structured tools in preview mode.
4. Review the generated MaterialsScript and validation output.
5. For real-time GUI updates, call `material_studio_live_modeling_request` with the original user text. If the request matches a conservative local template, the tool can infer the payload; otherwise provide a `ModelSpec` for new projects or a `SemanticPatch` for modifications. For precise follow-up patches, including MOS/gate-stack layer-thickness edits, the high-level entry can use the latest workspace `current.json` when `project_id` is omitted and returns `project_resolution` to make that choice visible. Before writing, an omitted-project follow-up compares that project with the visible wrapper provenance. A different trusted workspace returns `workspace_context_mismatch`, creates no revision, and supplies the exact `working_dir`/`project_id`/`base_revision` retry context. If `execution_mode` is omitted, the entry point stays in preview unless the text explicitly asks for hot-loading/real-time GUI execution, in which case it returns `execution_mode_source="explicit_live_intent"`.
6. Open or apply the current revision in the GUI. The tool wraps generated structures
   in an `.stp` project so the model is visible rather than failing with
   "There is no active project." `material_studio_gui_apply_current_revision`
   can omit `project_id` for ongoing sessions, but still requires explicit
   `execution_mode="execute"` to run and hot-load.
7. Export `material_studio_model_export_view_audit` or `material_studio_model_export_view_bundle` diagnostics and capture a snapshot when possible. `project_id` may be omitted for the latest current project; the response includes `project_resolution`. The audit includes per-view camera vectors, camera position/distance, orthographic framing, projection bounding boxes, per-atom 2D/depth projections, likely overlap candidates, bond-length rows, bond-angle rows, dihedral rows, atom connectivity, crystal nearest-neighbor rows, crystal coordination rows, semiconductor health summaries, semiconductor calculation-preflight summaries, semiconductor layer profiles, slab vacuum diagnostics, common over-coordination errors, and non-bonded close-contact warnings to help detect malformed or visually ambiguous models. Live and audit responses automatically include a view-bundle manifest and CSV file map for external checks.
8. Call `material_studio_live_project_status` to read the current revision, persisted report state, computed diagnostics, GUI status, and recommended next action. `project_id` may be omitted for ongoing sessions; the response includes `project_resolution` when it resolves the latest workspace project.
9. For client display, read `modeling_report.normality` first; normality-check requests automatically set `diagnostic_export_requested=true` and write view-bundle diagnostics. Use `modeling_report.normality_gate` as the machine-readable decision gate: only report the model as normal when `can_claim_model_normal=true`, and only report the live GUI as normal/current when `can_claim_live_gui_normal=true`. Otherwise surface `normality_gate.status`, `must_not_claim_normal_reasons`, and `next_action`. Use `modeling_report.acceptance_review`, `modeling_health.verdict`, `modeling_health.errors`, `modeling_report.gui.visual_validation`, GUI snapshot readability/nonblank metrics, view-bundle row counts, and warnings before trusting a visual artifact as proof that the model loaded normally. For semiconductor models, use `modeling_report.change_receipt.semiconductor` plus `live_readiness.calculation_blocking_reasons` to explain why a model can remain editable/hot-loadable while still not ready for trusted CASTEP/DFT calculation. For crystal lattice or vacuum edits, also read `modeling_report.change_receipt.delta.crystal.cartesian_moved_atom_count` and `fractional_rescale_preserved_cartesian` so fractional coordinate rescaling is not mistaken for physical atom motion.
10. Record any Copy Script snippets needed for API alignment.

Any calculation, file-changing action, or GUI synchronization that executes a
script requires explicit user confirmation.

## Local Natural-Language Templates

`material_studio_live_modeling_request` can infer payloads only for safe local
patterns. Current new-structure templates include benzene, water, methane,
ammonia, carbon dioxide, a small graphene-vacancy example, and semiconductor
crystal starts for Si diamond cubic, Ge diamond cubic, 3C-SiC zinc blende, c-BN zinc blende,
ZnO wurtzite, AlN wurtzite, CdTe zinc blende, ZnS zinc blende, ZnSe zinc blende,
ZnTe zinc blende, CdS zinc blende, CdSe zinc blende, 2D MoS2 monolayer, Si/Ge(001)
heterostructure, GaAs zinc blende, AlAs zinc blende, AlP zinc blende,
AlSb zinc blende, GaP zinc blende, GaSb zinc blende, InP zinc blende,
InAs zinc blende, InSb zinc blende, GaAs/AlAs(001) heterostructure,
Al0.25Ga0.75N/GaN(0001) heterostructure, AlN/GaN(0001) heterostructure, In0.25Ga0.75N/GaN(0001) heterostructure, GaN wurtzite, and AlN wurtzite, plus slab starts for Si(100), GaAs(001), GaN(0001), AlN(0001), and ZnO(0001).
Chinese aliases are available for the common semiconductor starts, including
GaAs/GaP/GaSb, AlAs/AlP/AlSb, InP/InAs/InSb, ZnS/ZnSe/ZnTe, CdS/CdSe/CdTe,
Ge, Si/Ge heterostructures, and 2D MoS2/WS2/MoSe2/WSe2 monolayers. Examples
such as `构建砷化镓晶体并热加载到 Materials Studio`,
`构建砷化镓(001)表面 slab 并热加载到 Materials Studio`,
`构建二硫化钼单层并热加载到 Materials Studio`, and
`构建硒化锌晶体并热加载到 Materials Studio` route directly through the local
template planner.
`material_studio_live_capabilities` exposes the same mapping in
`domain_focus.cjk_semiconductor_aliases` and
`natural_language.cjk_semiconductor_hotload_examples`, so @mcp clients can show
supported Chinese semiconductor prompts before falling back to a custom
`ModelSpec`.
For semiconductor crystal templates, one-shot requests can include deterministic
inline modifiers such as supercell expansion, superlattice period count, lattice strain, dopant fraction, alloy fraction, explicit-site or auto-site vacancy/dopant,
fractional interstitial placement, antisite placement, vacuum, fractional atom placement,
conservative hydrogen passivation, and
explicit full dangling-bond hydrogen passivation; post-supercell
site edits require IDs like `Si1_000` when a specific site is named. Site-free
requests such as `dope with P` choose a deterministic matching site and record
`metadata.nl_auto_selected_sites`.
For TMD starts such as MoS2, follow-up prompts such as `create S vacancy`,
`dope S sublattice with Cl`, or `dope with W` preserve `tmd_chalcogen` or
`tmd_metal` site families, inherit the substituted site's expected
coordination, and keep doped TMD neighbor pairs in the expected preflight role
when the local geometry remains consistent.
Semiconductor template IDs are also grouped under
`domain_focus.semiconductor_template_ids` in `material_studio_live_capabilities`.
Current
modification templates support precise atom-level commands such as deleting an
atom, substituting an atom with an element, moving an atom to explicit Cartesian
coordinates, adding an atom at explicit Cartesian coordinates, adding/deleting
a bond between explicit atom IDs, changing an existing bond type, or replacing a
bonded site with nitro, hydroxyl, amino, or methyl groups. For crystal current
projects, semiconductor-style patch templates also support explicit supercells,
superlattice period repetition with `superlattice_period_summary`, vacancies, interstitials, and antisites with defect-summary metadata, dopant substitutions, lattice summaries with `semiconductor_lattice.csv`, reciprocal-lattice/k-point sampling with `semiconductor_reciprocal_lattice.csv`, band-path preflight with `semiconductor_band_path.csv`, neighbor-pair distances with `semiconductor_neighbor_pairs.csv`, local environments with `semiconductor_local_environment.csv`, interface profiles with `semiconductor_interface_profile.csv`, interface quality sequence checks with `semiconductor_interface_quality.csv`, composition summaries with `semiconductor_composition.csv`, calculation preflight with `semiconductor_calculation_preflight.csv`, sublattice balance with `semiconductor_sublattice_balance.csv`, finite-size/dilution preflight with `semiconductor_finite_size.csv`, layer profiles with `semiconductor_layer_profile.csv`, dopant fractions with `semiconductor_dopant_fraction.csv`, alloy fractions with `semiconductor_alloy.csv`, applied lattice strain with `semiconductor_strain.csv`, surface polarity/asymmetry with `semiconductor_surface_polarity.csv`, auto-site vacancy/dopant selection, vacuum layers, deterministic surface hydrogen
passivation, adding crystal atoms at explicit fractional coordinates, and moving
crystal atoms to explicit fractional coordinates. Unsupported
requests return an explicit `nl_plan.kind="unsupported"` response and should be
translated into a reviewed `ModelSpec` or `SemanticPatch`. The response includes
`capabilities_hint` so a client can find the supported template IDs, patch
command IDs, and schema paths without making a separate discovery call.
