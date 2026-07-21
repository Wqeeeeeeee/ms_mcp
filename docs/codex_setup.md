# Codex Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .[dev]
```

Configure Codex with `.codex/config.toml.example` adapted to the local repository path. Start by calling `material_studio_get_status`, then use structured preview tools before any execution.

Keep `material_studio_run_script` disabled in normal Codex configuration unless custom Perl execution is required.

Audit the active Codex registration before claiming that `@mcp` is available:

```powershell
.\.venv\Scripts\python.exe -m material_studio_mcp_server.codex_config `
  --cwd . `
  --output-snippet workspace\codex_config\materials_studio.toml
```

The command is read-only with respect to the active config. It reports whether
`mcp_servers.materials_studio` uses this checkout's `.venv` and
`run_server.py`, whether the safe tool allowlist is complete, and whether
`material_studio_run_script` remains disabled. The optional output is a
separate review artifact; the command rejects an output path equal to the
active `%USERPROFILE%\.codex\config.toml`. Merge the reviewed section manually
without replacing unrelated servers or trusted-project entries, restart Codex,
and call `material_studio_live_session_preflight`.

## Protocol Acceptance

Direct tool-function tests do not verify the MCP transport. Run the stdio
acceptance client after changing tool registration, schemas, annotations, or
the example Codex configuration:

```powershell
.\.venv\Scripts\python.exe -m material_studio_mcp_server.protocol_smoke `
  --cwd . `
  --workspace workspace\mcp_protocol_acceptance `
  --config .codex\config.toml.example `
  --output workspace\mcp_protocol_acceptance\summary.json
```

This initializes `run_server.py`, pages through `tools/list`, validates the
required live and GUI contracts, and performs preview-safe calls in an isolated
workspace. It does not run Materials Studio or touch the current GUI session.
The optional config audit reports drift without modifying the active config.
Use `--strict-config` when drift should fail CI or local acceptance.

The example allowlist includes `material_studio_gui_execute_view_replay` with
`approval_mode="prompt"`. Its default `execution_mode="preview"` only probes
the exact UIA tree. Explicit execute is limited to one standard face view in
the existing wrapper and still requires a separate visual review before the
record tool can accept the view.

The example allowlist also includes `material_studio_gui_fit_to_view` with
`approval_mode="prompt"`. Its default `execution_mode="preview"` verifies the
exact current wrapper, the installed Fit-to-View toolbar mapping, and a fresh
UIA tree without sending input. Explicit execute invokes only
`cmdViewer3DFitToView` in the existing window, captures before/after screenshots
by default, and requires the bound structure SHA-256 to remain unchanged. It
never launches another Materials Studio process or uses blind coordinates.

Use `response_mode="compact"` for normal interactive calls to the live
capabilities, modeling, update, status, view-bundle, and GUI-apply tools. The
compact receipt includes multi-view camera parameters, normality and
semiconductor decisions, current GUI revision identity, next-action payloads,
and stable artifact paths. Use `response_mode="full"` only when the complete
in-band report is needed; both modes persist the same full report files.

If `views` is omitted for a semiconductor crystal, read
`view_parameter_summary.view_selection`: the server selects front/top/isometric
plus interface-, surface-, or lattice-family diagnostic views. An explicit
`views` list is preserved exactly and records `source="explicit_request"`.

Compact schema v2 targets 45 KB and is protocol-tested below the hard 48 KB
limit for capabilities, create, status, and view-bundle replies. The response
deduplicates callable payloads into `next_action_plan.payload_hint` and reduces
successful diagnostic focuses to issue-only summaries. Full diagnostic-focus
profiles and repeated evidence trees are retrieved with `response_mode="full"`
or from `report_json_path`.
For unusually large all-view/focus requests, inspect
`response_compaction.hard_budget_applied`, `semantic_core_preserved`,
`response_bytes`, `headroom_bytes`, and `omitted_fields`; use the returned detail
paths instead of assuming an omitted duplicate field was unavailable.
For diagnostic files, read `view_bundle_files_complete` as persisted-path
availability, not as the size of the in-band index. A normal compact response
may set `view_bundle_file_index_compacted=true` and return only five stable
paths while all files remain present. Use `view_bundle_artifact_availability`
for existing/missing counts and `view_bundle_manifest_path` for the complete
artifact index.

When Computer Use or a human reviewer has actually observed the model, record
that evidence with the already-enabled live entry point:

```json
{
  "user_request": "Record verified visual evidence for the current MS viewport.",
  "project_id": "current project id",
  "response_mode": "compact",
  "visual_confirmation": {
    "source": "computer_use",
    "model_visible": true,
    "expected_revision": 1,
    "expected_window_handle": 12345,
    "expected_window_title": "msmcp_r001_xxxxxxxxxx - Materials Studio",
    "note": "The expected model and unit cell are visible in the single viewport."
  }
}
```

This path creates no revision. It requires the current wrapper metadata, exact
window identity, current revision, and single-window policy to match. A visual
pass does not override geometry, metadata, acceptance, or semiconductor
normality failures.

The server still re-audits the current revision after accepting the evidence,
but reports that internal work separately in `gui_evidence_reaudit`. A plain
"record visual evidence" request preserves the existing
`diagnostic_export_requested`, `normality_check_requested`, and requested-focus
state. Only an evidence request that explicitly asks for diagnostics or a
normality check can add that intent; the receipt also records that no revision,
structure, or simulation state changed.

For one prepared standard or crystallographic view, the same high-level tool
can persist strict replay evidence without requiring the direct replay tool:

```json
{
  "user_request": "Record the verified front-view replay in the current MS viewport.",
  "project_id": "current project id",
  "response_mode": "compact",
  "view_replay_confirmation": {
    "view_name": "front",
    "source": "computer_use",
    "model_visible": true,
    "camera_matches_manifest": true,
    "screenshot_path": "C:\\path\\inside\\workspace\\front.bmp",
    "crystal_camera_evidence": {
      "camera_match_scope": "crystal_view_direction_with_observed_native_in_plane_roll",
      "view_direction_matches_manifest": true,
      "analytic_in_plane_basis_matches_manifest": false,
      "native_in_plane_roll_observed": true
    },
    "expected_revision": 1,
    "expected_window_handle": 12345,
    "expected_window_title": "msmcp_r001_xxxxxxxxxx - Materials Studio",
    "native_command_id": "cmdViewer3DResetView",
    "note": "Reset View matches the prepared front projection."
  }
}
```

The view must already exist in `gui_view_replay_manifest.json`. Binding failure
does not append `gui_view_replay_events.jsonl` and does not create a revision.
For the documented MS 20.1 face-aligned recipes, use the exact `key_sequence`
returned by `replay_continuation.payload_hint`, plus
`reset_before_key_sequence: true`, `rotation_increment_degrees: 45`, and
`modifier_keys: []`. Supplied keyboard
evidence must exactly match the manifest recipe; Shift is rejected before an
event is written.
For crystal standard views, the screenshot and `crystal_camera_evidence` are
required. Observe the direction and Materials Studio native in-plane roll;
exact analytic `camera_up`/`camera_right` equality is not required, so
`analytic_in_plane_basis_matches_manifest` may be `false` or `null`. Molecule
standard views do not require this crystal-only nested object.
For isometric, use the returned `keyboard_stages`: Reset, then `45 degrees: Up
x2, Left x3`, followed by `35.26438968 degrees: Down x1`. Also submit
`rotation_increment_restored_degrees: 45`, the returned Movement command and
control IDs, `movement_screen_factor: 2.0`, and
`movement_dialog_closed: true`.

The local `material_studio_gui_execute_view_replay` path now performs this
exact staged isometric recipe after an explicit execute request. Its preview is
input-free. Execute requires the single current wrapper, verified Reset and
Movement targets, one owned Movement window, unique `numNudgeAngle` and
`numNudgeFactor` controls, a disabled `cmdNudge*` inventory, exact ValuePattern
readback, and a unique `CViewer3DCtrl`. It closes Movement before each arrow
stage, restores 45 degrees even after a partial failure when the exact window
remains recoverable, and still leaves visual acceptance for a fresh screenshot
review and `material_studio_gui_record_view_replay`.

Before a standard recipe can be automatic-ready, submit a fresh exact-window
accessibility observation to the prepare tool:

```json
{
  "project_id": "current project id",
  "revision": 1,
  "views": ["front", "top", "isometric"],
  "runtime_accessibility_evidence": {
    "source": "computer_use",
    "expected_revision": 1,
    "expected_window_handle": 12345,
    "expected_window_title": "msmcp_r001_xxxxxxxxxx - Materials Studio",
    "accessibility_tree_refreshed": true,
    "viewer_document_observed": true,
    "empty_viewport_focus_target_observed": true,
    "unnamed_toolbar_children_observed": false,
    "controls": [
      {
        "command_id": "cmdViewer3DResetView",
        "observed_control_name": "3D Viewer Reset View",
        "invoke_supported": true
      },
      {
        "command_id": "cmdViewer3DMovementOptions",
        "observed_control_name": "3D Movement Options",
        "invoke_supported": true
      }
    ]
  }
}
```

MS 20.1 may expose the toolbar children without names. In that case, do not
claim a command for an element index. Submit the complete direct-child
observation under `anonymous_toolbars`. The observed `3D Viewer` toolbar must
contain nine children with roles `checkbox, checkbox, checkbox, checkbox,
separator, checkbox, checkbox, checkbox, checkbox`. For isometric replay, also
submit the complete eight-child `3D Movement` toolbar with the separator in
position six. Include the numeric toolbar automation ID, every fresh child
`element_index`, canonical role, enabled state, and null observed name;
`controls` may be empty.

The server independently parses and hashes the installed `#SVViewer3d.xml`,
checks the exact toolbar identity and full command/separator sequence, and
derives only the allowlisted Reset and Movement targets. A successful recipe
returns `target_kind: verified_anonymous_toolbar_child`, the zero-based child
index, ephemeral UIA element index, registry SHA-256, and semantic mapping
SHA-256. Refresh the accessibility tree immediately before invoking that
returned target. If any count, role, separator, name, enabled state, index
ordering, window binding, or hash differs, submit a new preflight. Arbitrary
unnamed indexes and all toolbar coordinates remain prohibited.

After invocation, pass the recipe-derived values back through
`accessibility_command_uses` with `accessibility_tree_refreshed: true` and
`invocation_succeeded: true`. The record tool rejects a mismatched mapping and
does not accept Computer Use replay evidence when this receipt is missing.

After a complete receipt, inspect the visual postcheck result before retrying.
When `replay_continuation.status` is
`automatic_recipe_postcheck_failed`, do not invoke the returned anonymous
mapping again. A verified failed `front` Reset also blocks every pending recipe
bound to the same Reset semantic hash. Follow
`recommended_mcp_tool=material_studio_gui_copy_script_assist` and collect a
reviewed camera path; simply preparing the same views again does not erase the
failure receipt. Clearing the gate requires a later successful postcheck with
artifact integrity `verified`; an unbacked boolean success is not sufficient.

When the reviewed Copy Script fallback is used, submit it only as inert evidence:

```json
{
  "view_name": "front",
  "source": "reviewed_copy_script",
  "model_visible": true,
  "camera_matches_manifest": true,
  "screenshot_path": "C:\\path\\inside\\workspace\\front.bmp",
  "crystal_camera_evidence": {
    "camera_match_scope": "crystal_view_direction_with_observed_native_in_plane_roll",
    "view_direction_matches_manifest": true,
    "analytic_in_plane_basis_matches_manifest": null,
    "native_in_plane_roll_observed": true
  },
  "expected_window_handle": 12345,
  "expected_window_title": "msmcp_r001_xxxxxxxxxx - Materials Studio",
  "reviewed_copy_script_evidence": {
    "script_text": "use MaterialsScript qw(:all);\n...exact Copy Script text...\n",
    "capture_method": "materials_studio_copy_script",
    "reviewer": "computer_use",
    "copy_script_command_observed": true,
    "review_completed": true,
    "view_action_matches_manifest": true,
    "structure_unchanged_observed": true,
    "note": "Reviewed against the prepared front-view manifest."
  }
}
```

The nested object is strict (`extra="forbid"`). The server never executes this
text. Safe evidence is archived with its SHA-256; shell, network, file
import/export/delete, calculation, or structure-mutation signals block view
acceptance and prevent raw script-text persistence.

Do not infer that an old `accepted=true` event is still trusted. Read the current
`gui_view_replay.replay_summary.evidence_integrity_status` and
`integrity_blocked_view_names`. Status refresh rechecks SHA-256 for the bound
screenshot, inert script, metadata, and structure artifact. A mismatch keeps the
historical event but requires a fresh screenshot and reviewed Copy Script record.
Also inspect `gui_view_replay.event_journal.consistency_status` and
`replay_summary.journal_blocked_view_names`. New events are written to the
append-only JSONL journal before manifest publication and are trusted only when
the immutable event digests match. Do not copy a manifest event into the journal
or vice versa to clear a mismatch; collect and record a fresh observed view.
Also inspect `recipe_contract.current_evidence_reverification_view_names` and
`replay_summary.current_camera_evidence_reverification_view_names`. A crystal
event from an older recipe schema remains in append-only history but cannot
authorize the current view until fresh screenshot/native-roll evidence is
recorded.
Prepare and record calls for one project/revision are serialized by a bounded
kernel lock. A `view replay write transaction is busy` error means another write
is active; retry the same observed payload after it completes, without deleting
the lock file or editing the manifest/journal.

For `automatic_recipe_ready`, do not pass `replay_continuation.payload_hint` to
the record tool. It is a non-callable GUI execution description, and
`next_action.recommended_tool=computer_use` is emitted only after the current
recipe, revision, single-window, and foreground gates pass. Execute the recipe,
refresh the exact window and accessibility state, capture a new screenshot, and
then fill `post_action_record_payload_template`. Its null observation fields are
mandatory placeholders, not defaults; only the completed post-action payload
may be sent to `material_studio_gui_record_view_replay`.

Structured revision writes have an independent project-scoped
`project_state.lock`. Successful create, patch, rollback, redo, restore, and
metadata-repair responses expose `state_write_transaction`; its `coverage`
lists revision, history, and current-pointer publication. Patch and rollback
commits validate both `expected_revision` and the prepared
`expected_new_revision` while holding this lock. A stale current pointer returns
`project_revision_conflict`. If an interrupted earlier write left an occupied
revision filename and the next safe allocation differs from the prepared
revision, the response returns `project_revision_allocation_conflict` with
`expected_new_revision`, `allocated_revision`, and `current_revision`. Refresh
with `state_retry_tool`/`state_retry_payload` and regenerate the patch, script,
and output paths; do not rename or delete the orphan.

The state lock serializes publication and each destination file is atomically
replaced, but the complete spec/script/history/current set is not a database
transaction.

Persisted structured execution uses a distinct
`outputs/rNNN/revision_execution.lock`. Its `execution_transaction` receipt
binds the immutable stored revision, the current revision observed immediately
before execution, the backend, canonical result publication, and the current
revision observed afterward. The transaction publishes
`result_metadata.json` once, atomically, with that receipt included. A second
same-revision request that exhausts the bounded wait returns
`status=revision_execution_busy`, `execution_started=false`, and
`execution_retry_tool`/`execution_retry_payload`; inspect status before retrying
instead of starting another job. If current advanced before the lock holder can
start, `status=current_revision_execution_block` proves that the runner was not
called. If it advances during execution, inspect
`execution_transaction.current_revision_still_current`; a false value blocks
the subsequent GUI hot-load but does not erase the immutable old-revision
result.

Each execution writes `execution_attempts.jsonl` and
`execution_attempt_state.json` beside `result_metadata.json`. Inspect
`material_studio_live_project_status.execution_runtime` for the current attempt
ID, sequence, PID, backend, spec/script SHA-256 bindings, recent event summaries,
incomplete attempt IDs, two lock observations, consistency issues, and the
continuation receipt. A persistent lock file is not evidence that work is
active; only the read-only kernel lock probes establish that. Conversely, an
inactive lock with a durable `running` attempt is `interrupted`, not completed.
Do not delete attempt files or manufacture a terminal event. Follow
`docs/execution_observability.md` when wiring a recurring monitor.

Use this lock order: project state lock, release it, revision execution lock,
release it, then GUI artifact report lock. Execution and GUI input must never
occur while `project_state.lock` is held, and the GUI report lock must never be
held while starting an execution. Persistent lock files are coordination
artifacts and must not be deleted to force progress.

GUI open, snapshot, and visual-confirmation report persistence share a separate
revision-scoped lock and return `report_write_transaction`. If that lock is
busy, retry the same GUI evidence operation after the current report update
completes. Lock acquisition order defines whether a later GUI open invalidates
older viewport evidence or a later snapshot appends to that open. `report.json`
is atomically replaced, so do not reconstruct it from a temporary file after an
interrupted write.

For direct GUI calls with a resolved structured project/revision, inspect
`gui_action_transaction.coverage`. Snapshot, open-structure,
activation-with-snapshot, and visual-confirmation tools acquire the same lock
before target-window revalidation and hold it through the GUI action or evidence
binding plus report publication. A busy error therefore means the direct GUI
action did not start and can be retried. A successful structured sync returns
the same path in `gui_action_transaction` and
`report_write_transaction`; `nested_call_count` confirms that internal report
persistence reused the outer transaction instead of reacquiring the OS lock.

High-level create, patch, and apply-current execute calls acquire this lock only
after MaterialsScript execution or crystal materialization succeeds. Inside the
lock they verify the target revision is still current, rerun GUI status and the
single-window gate, hot-load the structure, optionally snapshot it, and publish
the final report. A revision superseded during execution returns
`current_revision_hotload_block` and is not opened. Inspect
`gui_action_transaction.coverage` for `high_level_hotload` and the matching
`workflow:*` label. If the lock becomes busy after execution, no GUI action or
report overwrite occurs; the compact response keeps
`report_persistence_deferred`, `execution_completed_before_gui_transaction`,
`structure_ready_for_gui_retry`, `gui_open_retry_tool`, and
`gui_open_retry_payload`. Retry the returned `material_studio_gui_open_structure`
payload after the active transaction completes.

For `show_current`, natural-language patch, rollback, redo, and restore, the
workflow metadata visible in the compact response is also the metadata written
inside that same GUI transaction. Verify that `report.json` and
`modeling_report.workflow` match `gui_action_transaction.coverage`. Do not add
or invoke a follow-up report persistence step after the nested apply/update call
returns; the lock has already been released and a later write could discard a
concurrent snapshot or visual-confirmation update.

Direct view-audit and view-bundle exports use this same revision transaction,
including exports with `include_gui_snapshot=false`. Inspect
`report_write_transaction.coverage` for `diagnostic_export`, the matching
`workflow:model_export_view_*` label, `view_audit_bundle_write`, and
`report_read_modify_write`. When a snapshot is attempted, the response also
returns the same receipt as `gui_action_transaction` and adds
`target_window_revalidation` plus `gui_snapshot`. High-level
`inspect_current` owns one outer transaction; its nested bundle export reuses
that lock and the final inspection report is published before release.

On lock timeout, do not reconstruct or manually overwrite diagnostics. Retry
the returned `diagnostic_export_retry_tool` with
`diagnostic_export_retry_payload`; no snapshot or report write occurred. A
`diagnostic_export_current_revision_block` means the current pointer advanced
while the request waited, so use the retry payload to resolve and export the
new current revision. Inline-spec retry payloads carry the original `spec`, and
an inline spec that conflicts with a stored immutable revision is rejected.

Before choosing a replay backend, call
`material_studio_live_capabilities(include_status=true, response_mode="compact")`.
Use `local_uia_implementation_contract` only to discover implemented recipe
classes. Runtime permission additionally requires
`view_replay_runtime_availability.transactional_miller_supported=true`, a clean
single-window gate, and the current prepared recipe's `automation_ready=true`.
Exact-collinear crystal directions share the transactional Miller backend;
non-collinear directions remain reviewed-camera-backend gated.

For an external/manual `crystal_plane_*` or exact-collinear `crystal_*` replay,
observe the live controls on the exact current wrapper and submit the
Miller-specific `runtime_ui_evidence` to the prepare tool. The local
transactional executor can instead generate and persist this evidence during
explicit execution after its read-only accessibility preflight succeeds. It
does not require or invoke Reset. A complete externally observed Miller UI
payload has this shape:

```json
{
  "project_id": "current project id",
  "revision": 1,
  "views": ["crystal_plane_100"],
  "runtime_ui_evidence": {
    "source": "computer_use",
    "expected_revision": 1,
    "expected_window_handle": 12345,
    "expected_window_title": "msmcp_r001_xxxxxxxxxx - Materials Studio",
    "reset_view_control_observed": true,
    "tools_miller_planes_menu_observed": true,
    "miller_planes_keyboard_menu_path_verified": true,
    "miller_planes_dialog_observed": true,
    "miller_indices_control_observed": true,
    "create_button_observed": true,
    "tree_explorer_menu_observed": false,
    "properties_explorer_menu_observed": true,
    "view_onto_control_observed": true,
    "view_onto_native_command_mapping_verified": true,
    "pointer_menu_click_through_risk_observed": true,
    "unexpected_plane_created_during_probe": false,
    "unexpected_plane_cleanup_verified": false,
    "document_clean_before_probe": true,
    "document_clean_after_probe": true,
    "miller_planes_menu_key_sequence": ["Alt+T", "M"],
    "miller_planes_dialog_title": "Miller Planes",
    "miller_planes_dialog_control_id": "MillerPlanesCtl",
    "miller_indices_control_id": "TxtHKL",
    "create_button_control_id": "CmdCreate",
    "selection_modifier_keys": [],
    "viewport_selection_probe": {
      "selection_method": "viewport_unique_transient_plane_properties_verified",
      "probe_miller_indices": [1, 0, 0],
      "dialog_miller_indices": [1, 0, 0],
      "unique_transient_plane_visual_target_observed": true,
      "viewport_plane_selection_observed": true,
      "properties_selection_verified": true,
      "view_onto_popup_menu_observed": false,
      "view_onto_native_command_mapping_verified": true,
      "hit_test_basis": "fresh_before_after_screenshot_unique_transient_plane_region",
      "properties_filter": "Miller Plane",
      "properties_miller_label": "(100)",
      "view_onto_command_id": "cmdViewer3DViewOnto",
      "undo_labels_observed": [
        "Undo View Onto Miller Plane",
        "Undo Create Miller Plane"
      ],
      "structure_artifact_path": "C:\\Users\\user\\ms-mcp-workspace\\projects\\current\\outputs\\r001\\structure_r001.cif",
      "structure_artifact_sha256_before": "0000000000000000000000000000000000000000000000000000000000000000",
      "structure_artifact_sha256_after": "0000000000000000000000000000000000000000000000000000000000000000"
    }
  }
}
```

Report observed values only, and replace the example path and zero hashes with
the exact current workspace artifact and its observed SHA-256 values. Static
XML/help evidence cannot replace this probe. Never click Tools > Miller Planes
with the pointer or an accessibility menu action; use `Alt+T`, then `M`. If that
invocation creates an unexpected plane, use only the exact named
`Undo Create Miller Plane`, verify cleanup, set the cleanup evidence truthfully,
and abort the replay attempt. The installed MS 20.1 Object Tree component is
hidden, and Project Explorer lists project documents rather than structure
objects. Do not set `tree_explorer_menu_observed=true` for Project Explorer.
The nested viewport probe is accepted only after fresh before/after screenshots
isolate one unique new plane, a no-modifier selection is semantically verified
in Properties Explorer, the native View Onto popup is observed, and the source
structure hash is unchanged.
The returned Miller recipe must expose
`pre_action_view_baseline_required=true`, `reset_view_allowed=false`, no Reset
`accessibility_target`, `camera_result_depends_on_reset_baseline=false`, and
`final_camera_established_by_native_command_id=cmdViewer3DViewOnto`. The
transaction verifies the installed numeric mapping (`View Onto=33297`) before
invocation. A generic front Reset camera mismatch does not invalidate this
independent View Onto transaction.
For every prepared Miller-plane replay, inspect
`execution_recipe.dialog_index_entry_contract`. Read `TxtHKL` back from a fresh
modeless child accessibility state after entry; never treat `Ctrl+A` as proof
that replacement succeeded. Prefer exact `set_value`; otherwise apply the
contract's unmodified-key correction order: minimal suffix replacement from
`End`, `Home`-based affix repair, or longest-common-substring preservation before
falling back to one observed-count full replacement. For a cross-offset overlap,
apply one nonempty edge repair in observed-prefix, observed-suffix,
expected-prefix, expected-suffix order and replan from a fresh readback. Delete a retained prefix only when the fresh value ends with
the target, or type a missing prefix only when the target ends with the nonempty
fresh value. After the single full replacement, allow only those relation-based
repairs and abort on an unrelated value. Follow the recipe's ActiveX timing
fields: wait `200 ms` after `Home`/`End`, wait `200 ms` between repeated
`Backspace`/`Delete` events without batching them, and wait `500 ms` after the
mutation before the next fresh child readback. Refresh the child state after each mutation and abort without
Create if the final readback still differs. Invoke Create only after the trimmed value exactly
matches `dialog_miller_indices_text`, then record
`dialog_miller_indices_text_before_create`,
`dialog_miller_indices_value_source="fresh_modeless_child_accessibility_value"`,
and `dialog_miller_indices_verified_before_create=true` in
`miller_plane_evidence`.
While this gate is blocked, the continuation receipt sets
`payload_hint_is_directly_callable=false` and does not supply record-evidence
examples. Fill the strict runtime evidence schema from the current observation.
For `crystal_plane_*`, submit the returned `miller_plane_evidence` only after
observing every value. It binds the requested/dialog Miller indices, the exact
prepared selection method, Properties label, plane-normal/native-roll camera
scope, screenshot timing, temporary-plane counts, whitelisted undo labels,
clean document/temporary-plane/view restoration, and equal before/after SHA-256
for the wrapper source structure. For the viewport method, also record the exact
hit-test basis, both fresh screenshots, unique transient-plane region,
Properties selection, native popup, and disabled parallel/symmetry options.
Do not reuse the payload hint's example counts or old viewport coordinates.
Exact analytic in-plane
roll is optional and must be reported separately from the required plane
normal match. Lattice-direction views use this workflow only when their
execution recipe reports `exact_integer_plane_collinear`; then submit the
mapped Miller indices and
`direct_lattice_direction_matches_manifest=true`. Non-collinear directions
remain review-gated, and `[uvw]` must never be treated as same-index `(hkl)`.
Before executing another view, inspect `gui_view_replay.replay_continuation`
from compact live status. Proceed automatically only when
`automatic_replay_ready=true` and the selected view's
`execution_recipe.automation_ready=true`; otherwise follow the returned review
requirement without issuing trackball, spin, nudge, or align input.
For a locally executable recipe, call
`material_studio_gui_execute_view_replay` in preview first and use
`execution_mode="execute"` only after explicit confirmation. A successful
Miller execution returns the aligned pre-cleanup screenshot, exact two-step
undo evidence, runtime UI preflight path, and unchanged structure hash, but it
still does not create an accepted replay event.
When the next Miller recipe matches the local transactional selection profile,
the continuation exposes that preview call directly with
`recommended_mcp_tool=material_studio_gui_execute_view_replay`,
`payload_hint_is_directly_callable=true`, and `gui_input_required=false`.
Unsupported Object Tree or other selection profiles continue to return the
reviewed external path instead of being promoted to local automation.
Also require `gui_view_replay.recipe_contract.pending_recipe_upgrade_required`
to be false. When it is true, do not execute the persisted pending recipe and
do not submit new replay evidence. Send a high-level `continue_view_replay`
request; it regenerates current recipes, retains accepted replay events, and
does not create a model revision or modify the structure. Compact responses
carry the Miller dialog timing contract needed by the external GUI executor.

Call `material_studio_live_session_preflight` when starting a live @mcp
session or when runner/GUI/latest-project readiness is uncertain. It returns
runner status, GUI status, latest current project metadata, readiness flags,
safe smoke-test prompts, and the next recommended tool.

### Detect a stale MCP server process

`material_studio_get_status`, `material_studio_live_capabilities` with
`include_status=true`, and `material_studio_live_session_preflight` expose a
`runtime_provenance` receipt. Compare:

- `runtime_instance_id` and `process_id` to distinguish concurrent Codex MCP sessions.
- `source_snapshot_at_start.sha256` with `source_snapshot_current.sha256`.
- `source_current` and `restart_required` before following any modeling or GUI action.

When source files changed after the server started, preflight returns
`state="mcp_server_restart_required"`, marks preview/execute/hot-load readiness
false, and returns an external restart plan with `tool_call_ready=false`. Stop
there. Restart the MCP server/Codex MCP session, leave the existing
`MatStudio.exe` window open, and rerun the exact `retry_payload`. Do not kill
other `run_server.py` processes automatically because they may belong to other
Codex tasks.

Side-effect-capable direct tools enforce the same gate before their tool body.
This includes revision-changing previews, runner-backed helpers, diagnostic
exports, GUI logging/input, and calculation tools. A bypassed preflight therefore
returns `status="mcp_server_restart_required"` with
`tool_body_started=false`, `execution_started=false`,
`gui_input_started=false`, `revision_created=false`, and
`artifact_write_started=false`. The guard blocks the whole affected tool while
source is stale, including a requested preview or dry run, because the loaded
implementation cannot prove that its path is side-effect free. Status,
capability discovery, and live preflight remain callable for recovery.

The runner receipt may show different `default_workspace_root` and
`request_workspace_root` values. This is not an implicit workspace migration:
workspace-aware tool payloads use the explicit preflight `working_dir`, and the
receipt records `execution_working_dir_policy=explicit_tool_working_dir_overrides_runner_default`.

For resumed projects, `next_action_plan` is retained as the immediate
session-control compatibility plan. Use
`coordinated_next_action_plan.recommended_sequence` for the complete order
across `session_control`, `visual_diagnostics`, and `modeling`, and resolve each
step through its `plan_ref`. Require
`latest_project_visual_diagnostics.binding_verified=true` for replay/visual
continuation and `latest_project_modeling.binding_verified=true` for structural
or calculation work. Rerun preflight after each completed step; session work
does not clear project actions, and visual preparation does not confirm a later
modeling action.
The preflight derives those decisions from full local probes and then
deduplicates its response. Verify `response_compaction.target_exceeded=false`
and use `latest_project_gui.window_management_ref`,
`target_window_resolution_ref`, or `workspace_context_ref` to resolve the
authoritative compact context. Call the receipt's `full_detail_tools` only when
the unabridged probe internals are required.
If `latest_project.current_pointer_recovery_used=true`, continue read-only
inspection from the returned immutable revision and report the recovery. Do
not rewrite `current.json` merely to hide the warning; only an explicit
successful revision-changing request may atomically repair it.
For a local command-line acceptance pass, run
`ms-mcp-live-smoke --scenario sic_mos --working-dir workspace/live_smoke`.

For preview-only 3C-SiC polar-surface and Schottky-contact acceptance without
touching the GUI, use `sic_3c_slab`, `sic_3c_c_face_slab`, `sic_3c_contact`, or
`sic_3c_c_face_contact`. For example:
`ms-mcp-live-smoke --scenario sic_3c_slab --execution-mode preview --no-include-gui-status --no-take-snapshot --working-dir workspace/live_smoke_3c_slab`
or
`ms-mcp-live-smoke --scenario sic_3c_c_face_contact --execution-mode preview --no-include-gui-status --no-take-snapshot --working-dir workspace/live_smoke_3c_c_face_contact`.
The slab checks require four-bilayer polarity and termination diagnostics; the
contact checks additionally require material sequence, declared-versus-measured
gap, metal thickness, and metadata-only barrier preflight. The ideal exposed
surface and metal interface remain unrelaxed review scaffolds, so a successful
smoke run does not establish calculation readiness.

For preview-only 4H-SiC polar-surface acceptance without touching the GUI, use
`sic_4h_slab`, `sic_4h_c_face_slab`, `sic_4h_contact`,
`sic_4h_c_face_contact`, `sic_4h_oxide_interface`,
`sic_4h_c_face_oxide_interface`, `sic_mos`, or `sic_4h_c_face_mos` with
`--execution-mode preview --no-include-gui-status --no-take-snapshot` and a
scenario-specific `--working-dir`. For example:
`ms-mcp-live-smoke --scenario sic_4h_c_face_mos --execution-mode preview --no-include-gui-status --no-take-snapshot --working-dir workspace/live_smoke_4h_c_face_mos`.
Use `--follow-up-preset interface_gaps_2p0_2p5` for either 4H MOS scenario or
`--follow-up-preset o_vacancy` for either 4H oxide-interface scenario. These
continuations remain preview-only under the same flags.

For preview-only 6H-SiC surface, contact, oxide-interface, and MOS gate-stack acceptance, run
`ms-mcp-live-smoke --scenario sic_6h_slab --execution-mode preview --no-include-gui-status --no-take-snapshot --working-dir workspace/live_smoke_6h_slab`
or
`ms-mcp-live-smoke --scenario sic_6h_contact --execution-mode preview --no-include-gui-status --no-take-snapshot --working-dir workspace/live_smoke_6h_contact`,
or
`ms-mcp-live-smoke --scenario sic_6h_oxide_interface --execution-mode preview --no-include-gui-status --no-take-snapshot --working-dir workspace/live_smoke_6h_oxide_interface`,
or
`ms-mcp-live-smoke --scenario sic_6h_mos --execution-mode preview --no-include-gui-status --no-take-snapshot --working-dir workspace/live_smoke_6h_mos`.
The equivalent explicit C-face checks are
`ms-mcp-live-smoke --scenario sic_6h_c_face_slab --execution-mode preview --no-include-gui-status --no-take-snapshot --working-dir workspace/live_smoke_6h_c_face_slab`,
`ms-mcp-live-smoke --scenario sic_6h_c_face_contact --execution-mode preview --no-include-gui-status --no-take-snapshot --working-dir workspace/live_smoke_6h_c_face_contact`,
`ms-mcp-live-smoke --scenario sic_6h_c_face_oxide_interface --execution-mode preview --no-include-gui-status --no-take-snapshot --working-dir workspace/live_smoke_6h_c_face_oxide_interface`, and
`ms-mcp-live-smoke --scenario sic_6h_c_face_mos --execution-mode preview --no-include-gui-status --no-take-snapshot --working-dir workspace/live_smoke_6h_c_face_mos`.
To verify the deterministic O-vacancy continuation without touching a real GUI,
add `--follow-up-preset o_vacancy` while keeping `--execution-mode preview` and
the two `--no-*` GUI flags.
To verify both MOS boundary-spacing edits in one preview-only continuation, run
the `sic_6h_mos` or `sic_6h_c_face_mos` scenario with
`--follow-up-preset interface_gaps_2p0_2p5`,
`--execution-mode preview`, and the same two `--no-*` GUI flags.
The Si-face and C-face 4H-SiC and 6H-SiC oxide-interface scenarios each require
`semiconductor_oxide_interface_geometry.csv` with at least 38 rows (one
summary, one interface-spacing row, 24 boundary candidates, and 12 oxide-atom
coverage rows). Each MOS scenario requires at least 39 rows because it reports
both semiconductor/oxide and oxide/gate spacing rows. Both require
`semiconductor_oxide_interface_health.csv` with at least three rows (one
summary plus the deterministic oxide layers). The O-vacancy follow-up requires
at least 33 geometry rows, at least four health rows, and a defect row bound to
its oxide layer and interface distance. Inspect
`oxide_interface_geometry_summary.status`,
`boundary_neighbor_pair_count`, `short_contact_count`,
`isolated_oxide_atom_count`,
`oxide_interface_health_summary.stoichiometry_status`,
`recorded_oxygen_vacancy_binding`, and
`modeling_report.normality_gate.primary_reason_code`; none of these fields is a
claim that the oxide is amorphous, relaxed, or calculation-ready.

For a preview-safe Chinese halide-perovskite acceptance run with seven exported
views, alloy diagnostics, and the normality gate, use
`ms-mcp-live-smoke --scenario mapbi3_alloy_cjk --execution-mode preview --no-include-gui-status --no-take-snapshot --working-dir workspace/live_smoke_mapbi3_alloy`.
The default scenario request is preview-safe. Add `--hotload` or pass
`--execution-mode execute` only when you intend to materialize the structure and
open it in Materials Studio. The command runs preflight, the high-level live
modeling request, live project status, and view-bundle export, then prints a
compact JSON receipt with `project_id`, `execution_mode`, normality gate fields,
GUI current-revision fields, recommended semiconductor diagnostic focuses,
`report_json_path`, and `view_bundle_manifest_path`.

Add `--verify-ms-roundtrip` to request and validate the revision-bound CIF
import/export audit. In preview it accepts only a side-effect-free plan and
does not call Materials Studio. For a real existing-window smoke test, use
`--require-real-ms-roundtrip` together with explicit
`--execution-mode execute`; the CLI rejects `auto` and preview, and
`real_materials_studio_status=NOT_RUN` does not pass. See
`docs/revision_roundtrip_audit.md` for the receipt and single-window gates.

For the narrow pre-execution
`gui_activation_required_before_execution` state, use
`--execution-mode execute --resume-deferred-execution`. Both arguments are
required: `auto` is not an explicit authorization to resume execution. The
smoke runner validates the exact server-issued activation and apply-current
payloads, verifies the workspace/project/revision binding, reads the current
revision before and after activation, and then calls
`material_studio_gui_apply_current_revision` exactly once. It does not recreate
the revision and does not automatically retry a busy, stale, failed, or
identity-mismatched execution. Inspect
`preexecution_execution_continuation_status`,
`preexecution_execution_continuation_apply_call_count`, and
`preexecution_execution_continuation_failures` in the compact receipt.

For an explicit execute/hot-load smoke run, add
`--resume-deferred-hotload` when the command should recover from either of two
strict execution-complete states. For
`execution_completed_gui_activation_required`, the smoke runner validates the
server-issued workspace, project, revision, structure, tool names,
single-window gates, and exact payloads before calling
`material_studio_gui_activate`, followed by
`material_studio_gui_open_structure`. For the GUI artifact report-lock timeout,
it requires `report_persistence_deferred=true`,
`execution_completed_before_gui_transaction=true`, a successful result, the
same current revision, the existing planned structure, and the exact
workspace-bound open payload before calling
`material_studio_gui_open_structure` once. It never calls the modeling request
or MaterialsScript runner again for either state. Activation failure, payload
mismatch, current-revision drift, a newly spawned process, a repeated lock
failure, or an unverified open stops the continuation and prevents a follow-up
edit. Fit-to-View and view-replay preparation flags are forwarded unchanged in
the artifact-only open payload. The flag is disabled by default and has no GUI
effect on preview responses. Inspect
`postexecution_hotload_continuation_status` and
`postexecution_hotload_continuation_failures` in the compact receipt.
When a resumed pre-execution apply itself loses GUI focus after successful
execution, pass both continuation flags. The apply still runs only once; the
existing post-execution continuation consumes only the returned activation and
artifact-open payload and never reruns MaterialsScript.
The same two-flag sequence handles an apply that completes execution but times
out acquiring the GUI artifact report transaction; it revalidates current and
opens the artifact once without reporting the apply as an execution failure.

For a view-bundle export that alone returns
`status=diagnostic_export_deferred`, pass
`--resume-deferred-bundle-export` with an explicit `--working-dir`. This flag
does not authorize execution or GUI opening. The smoke runner requires the
server-issued retry payload to match the exact workspace, project, revision,
views, `include_gui_snapshot`, and `response_mode`; reads the current revision;
then calls `material_studio_model_export_view_bundle` once with that payload.
It accepts completion only after the returned manifest is bound to the same
project/revision, `view_projections.csv` exists with the declared row count,
and the report transaction covers `diagnostic_export`. Compact responses may
resolve the CSV path from their bound manifest. Contract drift, revision drift,
another lock timeout, or invalid artifacts stop the continuation without a
loop and without rerunning the modeling request, MaterialsScript, runner, or
GUI open. Inspect `bundle_export_continuation_status`,
`bundle_export_continuation_completed`, and
`bundle_export_continuation_failures` in the compact receipt. The flag is off
by default; when `--no-export-bundle` is present it returns the stable
`bundle_export_disabled` receipt and performs no continuation call.

The receipt keeps `gui_window_identity_verification` as the raw observed-window
value for audit. Use `current_revision_gui_evidence_applicable`,
`current_revision_gui_evidence_status`, and
`current_revision_gui_window_identity_verification` for decisions about the
new revision. A value of `not_applicable_to_current_revision` means the visible
window was observed for single-window safety but is not evidence about the
previewed revision; it must not fail deterministic view or semiconductor
diagnostics. Execute/hot-load acceptance still requires bound, verified GUI
identity evidence.
For GUI evidence, treat `gui_hot_loaded=true` and
`gui_loaded_current_revision=true` as window/revision evidence, then separately
check `snapshot_viewport_likely_visible_model`. If that field is false, the
window opened but the central Materials Studio model viewport appears blank or
not fit-to-view, so report a visual-validation warning and re-snapshot or adjust
the viewport before claiming the model is visibly normal. Also check
`snapshot_viewport_capture_limitation_possible`: when true, the fallback BMP may
be a uniform dark OpenGL viewport capture from Windows GDI/BitBlt. In that case
the model should remain editable through spec/patch workflows, but visual proof
requires GUI inspection or a manual/Computer Use File | Export image workflow.
If the preflight state is `preview_ready_gui_not_open` and the recommended tool
is `material_studio_gui_launch`, call it only when starting a new GUI session is
intentional; otherwise activate an already-open Materials Studio window before
executing a hot-load request. Explicit hot-load/open calls do not attempt a
fallback launch when no window is open. On Windows, the GUI fallback tries the
existing window's File/Open dialog first. If the local fallback would have to
start `MatStudio.exe`, might create another Materials Studio window, or cannot
control the same-window dialog, the workflow keeps the generated
structure/report artifacts and returns `gui_open_warning`.
On a fresh single-process session, the fallback also recognizes the file
association prompt, the empty `New Project` save dialog, and `Welcome to
Materials Studio`. It cancels only the empty new-project dialog and selects the
generated `.stp` through the welcome page's `Browse...` picker. This avoids a
stale Recent Projects selection silently opening the wrong model. Any unknown
modal dialog remains a hard stop.
If the preflight state is `ready_for_live_edit_gui_review`, call the returned
`material_studio_gui_open_structure` next-action payload to reload the latest
current revision in the GUI and capture a snapshot before continuing edits.
If the preflight state is `ready_for_live_edit_gui_activation`, call the
returned `material_studio_gui_activate` payload with `take_snapshot=true` to
bring the already-loaded target revision window forward and refresh the
structured visual receipt instead of re-hot-loading the structure.
This state also covers a minimized, hidden, or explicitly non-foreground target.
Do not call `material_studio_gui_snapshot` directly while
`activation_required_before_capture_or_input=true`.
Keep the payload's `project_id` and `revision` when present. With those fields,
or when an omitted `project_id` safely matches the opened `structure_path` to
the latest current revision's planned structure, `material_studio_gui_open_structure`
writes the GUI-open artifact, `view_audit`, `modeling_health`,
`modeling_report`, and `report.json` back to that revision.
Call `material_studio_live_capabilities` to discover the current live-modeling
entry point, supported deterministic natural-language templates, supported
patch commands, schema file paths, GUI helper tools, and diagnostic fields.
The capabilities payload also lists `change_receipt` subfield contracts,
including artifact fields, diagnostic row-count fields, `view_check` fields,
and `health_check` fields for MCP clients that render compact receipts.

The JSON schemas under `src/material_studio_mcp_server/schemas/` are generated
from the Pydantic models and can be used by external MCP clients to construct
valid `ModelSpec`, molecule, crystal, simulation, and `SemanticPatch` payloads.
`patch_spec.schema.json` enumerates the supported patch operations, including
`set_bond_type`, `translate_crystal_atoms`, `rotate_crystal_atoms`,
`make_commensurate_twisted_bilayer`, `make_commensurate_tmd_heterobilayer`, and
`set_metadata`.

For open-GUI workflows, start with `material_studio_gui_status`.  When no
project is supplied, it resolves the latest current structured project when
available and returns `target_window`/`target_window_resolution` separately from
the backend's default selected window.  The GUI tools use a local Windows
fallback for process/window detection, activation, same-window File/Open dialog
loading of generated Materials Studio project wrappers, and BMP snapshots.
Generated
structures are wrapped into `.stp` projects under `workspace/gui_projects/`
before opening, which avoids the Materials Studio "no active project" failure.
`material_studio_gui_launch`, `material_studio_gui_activate`, and
`material_studio_gui_copy_script_assist` follow the same latest-current
resolution when project context is omitted, so multi-window sessions should read
`project_resolution` and `target_window_resolution` before trusting the selected
window.
Snapshot and open results include lightweight BMP analysis metrics, including
`likely_nonblank`, sampled color counts, and dominant color ratio, so callers
can flag empty or failed visual captures.
Computer Use is still preferred for menu navigation, viewport manipulation,
dialogs, and Copy Script extraction when its helper is available; if the helper
reports a missing native pipe, continue with the MCP GUI fallback and keep
structural edits spec/patch driven.

For live modeling requests, call `material_studio_live_modeling_request` with
the original user text and either a new-project `ModelSpec` or an existing
project `SemanticPatch`.  The tool creates or updates a revision, validates the
generated script, optionally executes and opens the result in the existing GUI,
and writes `view_audit.json` with model health and standard view parameters.
If `execution_mode` is omitted, this live entry remains preview-first unless the
text explicitly asks for hot-loading or real-time GUI execution; check
`execution_mode_source` in the response.
Use `material_studio_live_project_status` after create, patch, export, rollback,
or GUI apply operations to summarize the current revision, generated script,
planned structure, persisted report, computed diagnostics, GUI status, and
recommended next action.
For ongoing sessions, `project_id` may be omitted; the response includes
`project_resolution` when it selects the latest current project.
Status responses also preserve `latest_change`,
`persisted_change_receipt`, workflow, and user-request context from
history/report files, so a resumed @mcp session can explain the current
semiconductor model without replaying the original tool call. Live responses and
status responses expose top-level `live_summary` as the first machine-readable
client receipt: project/revision, hot-load/current-GUI state, normality,
ready-for-next-edit/calculation booleans, view status, semiconductor rule/risk
flags, key report/CSV paths, and the next action. The same summary includes
`next_action_id`, `next_action_tool`, `next_action_payload_hint`, confirmation
flags, and `next_action_ready` as a compact projection of the full
`modeling_report.next_action_plan`.
The same latest-project fallback applies to
`material_studio_model_export_view_audit` and
`material_studio_model_export_view_bundle` for current-model diagnostics.
The high-level `material_studio_live_modeling_request` entry point can route
natural-language checks such as "Is the current model normal?" to live project
status, and export requests such as "export current view parameters" or
`导出当前模型视角参数` to the view-bundle exporter without creating a new
revision.
If a request combines GUI display with diagnostic export, such as "show the
current model in Materials Studio and export current view parameters", keep the
GUI hot-load path active and return `diagnostic_export_requested=true` plus
`view_bundle_manifest_path`; do not treat it as diagnostics-only.
For create or patch requests that also ask for diagnostic/view export, the live
response and persisted `modeling_report` set `diagnostic_export_requested=true`
so clients can distinguish user-requested artifacts from default preview
diagnostics.
Later `material_studio_live_project_status` calls preserve the same field from
the persisted report, which lets resumed clients keep the diagnostic-export
receipt without replaying the original request.
Requests that explicitly ask whether the model is normal set
`normality_check_requested=true` in the live response, persisted
`modeling_report`, `change_receipt`, and later status responses. They also set
`diagnostic_export_requested=true` and write the view-bundle diagnostics, because
normality claims should be backed by exported view/model inspection artifacts.
Treat this as the user's inspection intent; the actual answer still comes from
`modeling_report.normality`, `modeling_health.verdict`, semiconductor review,
view review fields, and the generated diagnostic tables.
Use `material_studio_model_export_view_bundle` when another tool or script needs
file-based inspection tables. It writes `view_audit.json`, `manifest.json`,
`health_summary.json`, `modeling_health_summary.csv`, and CSV files for atoms, bonds, bond angles, dihedrals, connectivity, close
contacts, crystal nearest neighbors, crystal coordination, semiconductor
lattice volume/density, neighbor-pair distances, local environments, interface profiles, interface quality, composition, nominal charge-balance/valence-electron summaries, calculation-preflight summaries, sublattice balance, layer profiles, dopants, dopant fractions, alloy fractions, finite-size/dilution preflight, vacancy/defect summaries, heterostructure strain, surface termination, surface polarity/asymmetry,
view summaries, per-view atom projections, and projection overlaps.
For quick side-by-side review, the same bundle also writes
`view_reference_atlas.svg`, `view_reference_manifest.json`, and
`view_reference_index.csv`. These are deterministic projections of the stored
specification, not Materials Studio screenshots and not visual-confirmation
evidence. Full responses expose all three paths through `artifacts`,
`modeling_report.diagnostics`, `change_receipt.artifacts`, and
`live_summary.mcp_view_reference_*`. Compact responses promote the atlas and
manifest in `artifacts`; use the manifest for the complete file index.
Read `modeling_health_summary.csv` first when another script needs a one-row
normality receipt with verdict, counts, GUI snapshot checks, and promoted
semiconductor checks.
Live and audit responses that persist `view_audit.json` include
`view_bundle_manifest_path`, `view_bundle_files`, and `view_bundle_row_counts`
for immediate follow-up checks.
They also write `report.json` in the revision output directory and return
`report_json_path`, which should be treated as the stable compact entry point
for clients.
They also include `modeling_report`, a compact receipt for clients.  Use
`modeling_report.next_action_plan` as the structured @mcp call recipe. It
contains the action id, recommended tool, payload hint, confirmation
requirement, readiness booleans, key artifact paths, and blocking or review
reasons, so clients do not need to parse the prose `next_action`.
`live_summary.next_action_id`, `next_action_tool`, `next_action`, payload, and
confirmation booleans are reconciled from that same plan. Check
`live_summary.next_action_source` and `next_action_resolution` when resuming an
older report. A differing prior free-text hint is preserved only as
`legacy_next_action` or superseded-action evidence and is not callable. Use
`modeling_report.live_readiness` for the underlying orchestration decision. It
tells the client whether the current revision is ready for hot-loading, ready
for the next edit, or ready for calculation, whether explicit user
confirmation is needed, which tool to call next, and which blocking or review
reasons remain.
Use `modeling_report.normality` for quick display status and
`modeling_report.revision_delta` to confirm what changed in the current
revision before trusting a live GUI refresh. The delta includes atom-count and
element-count changes plus molecule/crystal-specific added, deleted, moved,
substituted, bond, lattice, simulation, and metadata changes when applicable.
Use `modeling_report.change_validation` as the audited receipt that the delta
matches the current `view_audit`; a false `ok` means the generated or loaded
model needs review before reporting the edit as reflected in the current
structure.
Use `modeling_report.change_receipt` for compact UI/chat display of the latest
create or follow-up edit. It includes the user request, base/new revision,
delta, GUI current-revision state, formula, dopants, strain, readiness, and
review or calculation-blocking reasons.
Use `change_receipt.artifacts` as the compact file map for clients: generated
structure, GUI snapshot, `report_json_path`, `view_audit_report_path`,
`view_bundle_manifest_path`, view projection CSVs, and semiconductor diagnostic
CSVs when available.
Use `change_receipt.diagnostic_row_counts` as the compact table-size receipt for
the same bundle, including atom, view, projection, and semiconductor diagnostic
row counts.
Use `change_receipt.view_check` as the compact visual sanity receipt. It exposes
view count, projection row count, overlap or warning views, best view
candidates, GUI visual validation, and whether the GUI shows the current
revision.
Use `change_receipt.health_check` as the compact normality/trust receipt before
answering that a model is normal. It combines `normality`, the health verdict,
script validity, acceptance state, view status, semiconductor risk flags,
readiness, blocking or review reasons, and the next action.
For semiconductor projects, `change_receipt.semiconductor` mirrors the compact
material review: formula, structure family, calculation preflight, k-point
status, dopants, p-n junctions, alloy, defects, interface, surface, risk flags,
and the next action.
For crystal lattice or vacuum edits, inspect the receipt delta's
`cartesian_moved_atom_count`, `cartesian_preserved_atom_count`,
`max_cartesian_displacement_angstrom`, and
`fractional_rescale_preserved_cartesian` fields before describing whether atoms
physically moved or only had fractional coordinates rescaled.
Use `modeling_report.acceptance_review` to check `ModelSpec.acceptance`
criteria such as maximum allowed health warnings and required convergence
evidence. A false `ok` is surfaced as `acceptance_criteria_failed` in
`live_readiness.review_reasons` and
`live_readiness.calculation_blocking_reasons`, so clients can keep live editing
while withholding calculation/trust claims.
For semiconductor work, use `modeling_report.semiconductor_review` as the
short material-facing summary. It includes formula, lattice, CASTEP/k-point and
band-path preflight, dopant/junction/alloy/defect/interface/surface summaries,
risk flags, and a next action without requiring clients to parse the full
`inspection.semiconductor_health` payload first.
Semiconductor risk flags are also copied into
`live_readiness.calculation_blocking_reasons`; this blocks calculation/trust
claims, not continued live editing or GUI hot-loading.
For visual checks, use `modeling_report.view_review` as the short multi-view
summary. It lists best view candidates, projection overlap or warning views,
projection atom-count mismatches, GUI snapshot validation state, and critical
visual flags before clients open `view_summary.csv`, `view_projections.csv`, or
`view_overlaps.csv`.
Use
`modeling_report.inspection` for compact geometry counts, statistics, and
per-view projection summaries, including crystal nearest-neighbor,
coordination, semiconductor health, lattice summaries, neighbor-distance summaries, local-environment summaries, interface-profile summaries, interface-quality summaries, composition summaries, nominal charge-balance/valence-electron summaries, calculation-preflight summaries, sublattice balance summaries, layer profiles, superlattice period summaries, dopant/dopant-fraction/alloy summaries, heterostructure strain
summaries, finite-size/dilution summaries, vacancy/defect summaries, surface termination and polarity summaries, and
slab-vacuum summaries when present, then inspect
`modeling_health`, `view_audit.json`, and the CSV bundle for detailed diagnosis.
When GUI snapshots are captured, `modeling_report.gui` includes the window
title, open method, snapshot path, readability/nonblank metrics, sampled color
counts, `visual_validation`, and current-revision consistency fields such as
`loaded_current_revision`, `revision_matches_current`, and
`structure_path_matches_current`, so @mcp clients can distinguish a truly
current hot-load from a stale, unavailable, or visually suspect GUI artifact.
The report includes a stable spec fingerprint, rounded atom coordinates,
per-view projection bounding boxes, per-atom 2D/depth projections, and likely
overlap candidates for automated sanity checks.  For molecules it also records
bond-length rows, bond-angle rows, dihedral rows, per-atom connectivity, common
over-coordination errors, and non-bonded close-contact warnings.
For crystals it records periodic minimum-image nearest-neighbor statistics,
coordination rows, and slab vacuum diagnostics when surface metadata is present.
The tool response and persisted
audit report also include `modeling_health`, a consolidated verdict with checks,
errors, warnings, and the next action.
For semiconductor requests, inspect `modeling_health.checks` for promoted
domain-specific values such as formula, reduced formula, element count,
nominal valence-electron count, electron-count parity, carrier-type hint,
CASTEP calculation-preflight status/cutoff/k-point checks, reciprocal-lattice
lengths and estimated k-point grids,
lattice constants, cell volume, volume per non-passivant atom, non-passivant
atom density, nearest-neighbor pair count/type count and min/mean/max distance,
local-environment coordination outlier count, local angle min/mean/max,
tetrahedral angle deviation mean/max, interface segment count, interface
transition count, mixed-layer count, abrupt-interface flag, slab vacuum fraction, III-V and II-VI cation/anion counts, sublattice balance,
vacancy/interstitial/antisite fraction, finite-size/dilution warning, defect
missing-bond estimate, interstitial coordination outlier count, antisite
same-sublattice neighbor count, layer count and minimum interlayer spacing,
dopant coordination outlier count, superlattice period count,
dopant-fraction rounding warning, surface
dangling-bond estimate, passivation coverage, surface polarity/asymmetry, and heterostructure strain
warnings. These checks can turn an
otherwise successful preview or hot-load into `ready_with_warnings` or
`passed_with_warnings` so the client does not label an unrelaxed defect or
unpassivated slab as fully normal.

When no structured payload is supplied, the live-modeling tool first tries a
conservative local natural-language planner. It currently supports benzene,
water, methane, ammonia, carbon dioxide, graphene-vacancy, semiconductor
crystal starts for Si diamond cubic, Ge diamond cubic, 3C-SiC zinc blende, c-BN zinc blende,
ZnO wurtzite, AlN wurtzite, InN wurtzite, CdTe zinc blende, ZnS zinc blende, ZnSe zinc blende,
ZnTe zinc blende, CdS zinc blende, CdSe zinc blende, 2D MoS2 monolayer,
GaAs zinc blende, AlAs zinc blende, AlP zinc blende, AlSb zinc blende, GaP zinc blende, GaSb zinc blende,
InP zinc blende, InAs zinc blende, InSb zinc blende, GaN wurtzite, AlN wurtzite, and InN wurtzite, Si/Ge(001),
GaAs/AlAs(001), Al0.25Ga0.75N/GaN(0001), AlN/GaN(0001), and
In0.25Ga0.75N/GaN(0001) heterostructure starts,
semiconductor slab starts for Si(100), GaAs(001), GaN(0001), AlN(0001), InN(0001), and ZnO(0001),
and precise
atom-level patch commands such as delete/substitute/move-to-coordinate,
add-atom-at-coordinate, add/delete bond, set bond type, and conservative
functional-group replacements for nitro, hydroxyl, amino, and methyl. For
crystal current projects it also supports semiconductor-style patch commands
for explicit supercells, superlattice period repetition, explicit lattice parameters,
lateral layer/stacking-registry translations, lattice strain, dopant fractions,
alloy fractions, vacancies, interstitials, antisites, dopant substitutions,
auto-site vacancy/dopant selection, vacuum layers,
surface hydrogen passivation, explicit full dangling-bond hydrogen
passivation, adding atoms at fractional coordinates, and moving atoms to
fractional coordinates.
Inline semiconductor modifiers can be composed during new-structure planning.
For supercell starts, prefer deterministic auto-site requests such as `Build
silicon crystal as a 2x1x1 supercell and dope with P` or `Build silicon crystal
as a 2x1x1 supercell and create a Si vacancy`. If the request names an exact
site after the inline supercell, use the generated post-supercell ID such as
`Si1_000`; original IDs such as `Si1` are rejected in that same request because
they refer to multiple supercell images.
Natural-language vacancy, interstitial, and antisite patches record the removed,
added, or substituted lattice site in metadata so
`semiconductor_health.defect_summary` and `semiconductor_defects.csv` can report
defect concentration, nearest neighbors, under-coordinated neighbor counts, and
interstitial coordination outliers, while intentional antisite same-sublattice
neighbors are treated as review warnings rather than accidental model failures.
Natural-language strain patches record reference and strained lattice values in
`metadata.applied_strain`, expose `semiconductor_health.strain_summary`, and
export `semiconductor_strain.csv`.
Natural-language explicit lattice edits accept named `a`, `b`, `c`, `alpha`,
`beta`, and `gamma` parameters after an explicit lattice/cell-parameter phrase.
They preserve fractional coordinates, record `metadata.lattice_parameter_edits`,
and rely on `semiconductor_lattice.csv`, reciprocal-lattice diagnostics, neighbor
diagnostics, and `revision_delta.crystal.lattice_delta` for post-edit review.
Natural-language layer-registry edits require an explicit 1-based layer number
or `top`/`bottom` layer, an in-plane axis, a signed distance, and a length unit.
The planner resolves the layer using the same profile axis and tolerance as
`semiconductor_layer_profile.csv`, emits `translate_crystal_atoms` with exact
atom IDs, and exports `semiconductor_layer_translation.csv`. A profile-normal
request is rejected because interface-gap and layer-thickness tools own that
geometry change.
Natural-language layer rotation accepts an explicit 1-based layer number or
`top`/`bottom`, a signed angle in degrees, and an optional profile-axis name.
Examples include `twist the top layer by 3 degrees` and
`将第 2 层绕 c 轴旋转 5 度并热加载`. The planner emits
`rotate_crystal_atoms` with exact atom IDs and exports
`semiconductor_layer_rotation.csv`; request the `layer_registry_rotation`
diagnostic focus when a compact MCP client needs the corresponding profile,
rotation, neighbor, local-environment, view, and revision evidence. Arbitrary
twist angles are non-commensurate visual-review scaffolds. Same-window hot-load
is allowed after normal GUI preflight, but normality and calculation readiness
remain blocked until a commensurate supercell and geometry relaxation are
verified.
For pristine periodic TMD homobilayers, use the separate
`commensurate_tmd_twisted_bilayer` natural-language command or the structured
`make_commensurate_twisted_bilayer` patch. Supported local starts are MoS2,
WS2, MoSe2, and WSe2. Supply coprime `m > n >= 0`, or a target twist angle that
has a candidate within 0.1 degrees and the default 2000-atom bound. Request the
`commensurate_twisted_bilayer` diagnostic focus to require
`commensurate_twist_summary`, `semiconductor_commensurate_twist.csv`, layer,
neighbor, local-environment, and view evidence. Exact integer
commensurability permits same-window hot-loading but does not clear the
geometry-relaxation calculation blocker.
For distinct TMD materials, use `commensurate_tmd_heterobilayer` or the
structured `make_commensurate_tmd_heterobilayer` patch. The first named material
is the bottom layer and the second is the top layer. Supported pairs draw from
MoS2, WS2, MoSe2, and WSe2, and must contain two different materials. The patch
accepts coprime indices or a bounded target angle plus `balanced`,
`bottom_fixed`, or `top_fixed` biaxial-strain allocation. The default limits are
3% maximum absolute layer strain and 2000 atoms. Request the
`commensurate_tmd_heterobilayer` diagnostic focus to require
`commensurate_heterobilayer_summary`,
`semiconductor_commensurate_heterobilayer.csv`, composition, strain,
commensurability, neighbor, local-environment, and view evidence. Exact periodic
coincidence is established only after the explicit strain is recorded; the
result remains a pre-relaxation scaffold and is not calculation-ready.
The heterobilayer focus automatically adds
`two_dimensional_electrostatic_preflight`. Its required evidence is
`two_dimensional_electrostatic_summary`,
`semiconductor_2d_electrostatics.csv`, surface-model/polarity rows, slab vacuum,
and view diagnostics. Layer-level material and element-count differences prove
the expected composition asymmetry even when the two outer termination formulas
match. The summary explicitly reports `charge_density_available=false`,
`dipole_moment_calculated=false`, `dipole_correction_api_verified=false`, and
`dipole_correction_setting_verified=false`; do not translate this metadata-only
preflight into a quantitative electrostatic or calculation-ready claim. Review
the installed Materials Studio 20.1 Copy Script or documented CASTEP UI before
confirming a dipole-correction setting.
Semiconductor layer profiles are exported as `semiconductor_layer_profile.csv`
and summarize per-layer composition, axis coordinate, and interlayer spacing
along the interface axis, surface axis, or c axis.
Semiconductor composition summaries are exported as
`semiconductor_composition.csv` and summarize full/reduced formulas, element
counts, atomic fractions, and host/dopant/passivant roles.
Nominal charge-balance summaries are exported as
`semiconductor_charge_balance.csv` and summarize per-element nominal valence
electron counts, total valence-electron parity, dopant electron deltas, and
quick carrier-type hints. This is an element-count heuristic for preflight
review, not a DFT charge-density analysis.
Calculation-preflight summaries are exported as
`semiconductor_calculation_preflight.csv` and summarize CASTEP task,
functional, quality, cutoff energy, k-point mode, k-point separation or grid,
slab surface-normal k-point risks, and static warnings. This is a setup
preflight, not a convergence or accuracy proof.
Reciprocal-lattice summaries are exported as
`semiconductor_reciprocal_lattice.csv` and summarize real-space axis lengths,
reciprocal-vector lengths, estimated k-point grids from `kpoint_separation`,
and explicit-grid separations for quick axis-by-axis sampling review.
Band-path preflight summaries are exported as `semiconductor_band_path.csv`
for supported semiconductor families. Diamond-cubic and zinc-blende starts use
the fcc Gamma-X-W-K-Gamma-L-U-W-L-K path; wurtzite starts use the hexagonal
Gamma-M-K-Gamma-A-L-H-A-L-M-K-H path. Treat these as review aids before
BandStructure setup, not as proof that CASTEP band settings are complete.
Surface-polarity summaries are exported as
`semiconductor_surface_polarity.csv` and compare top and bottom slab
non-passivant formulas plus passivant-bond counts. They flag asymmetric or
polar-looking slabs, including one-sided passivation, for review before slab
DFT.
Finite-size summaries are exported as `semiconductor_finite_size.csv` when an
isolated dopant or defect is present. They report non-passivant cell size,
largest isolated dopant/defect fraction, and warnings for small cells or high
defect concentrations before quantitative DFT.
Semiconductor neighbor-distance summaries are exported as
`semiconductor_neighbor_pairs.csv` and summarize each nearest-neighbor pair type,
its expected/unchecked/unexpected/passivant role, and min/mean/max distance.
For large-radius tetrahedral binaries such as InN, long same-sublattice
candidates outside the heteropolar first shell are surfaced as
`same_sublattice_cutoff_artifact_pair_count` and unchecked neighbor-distance
rows instead of hard structure errors.
Semiconductor local-environment summaries are exported as
`semiconductor_local_environment.csv` and summarize each atom's neighbor shell,
coordination outlier flag, local angles, and tetrahedral-angle deviation.
Semiconductor interface-profile summaries are exported as
`semiconductor_interface_profile.csv` and summarize layer roles, material
segments, interface transitions, mixed layers, and abrupt-interface flags.
Semiconductor interface-quality summaries are exported as
`semiconductor_interface_quality.csv` and summarize expected versus actual
material sequence, period completeness, transition counts, missing declared
materials, mixed-layer count, and a compact `quality` field.
Sublattice balance summaries are exported as
`semiconductor_sublattice_balance.csv` and summarize III-V or II-VI
cation/anion counts and TMD metal/chalcogen counts, balance deltas, and
obvious stoichiometry warnings.
Superlattice period requests such as `Build a 3-period GaAs/AlAs
superlattice` repeat a heterostructure along its interface axis, record
`metadata.applied_superlattice_period`, expose
`semiconductor_health.superlattice_period_summary`, and verify layers per period
through the layer profile. III-nitride starts such as `Build a 3-period
AlGaN/GaN superlattice`, `Build a 3-period AlN/GaN superlattice`, and
`Build a 3-period InGaN/GaN MQW` additionally report material sequences through
`interface_profile_summary`, `interface_quality_summary`, and
`quantum_well_summary`; alloyed AlGaN/InGaN starts may report mixed cation
layers, while AlN/GaN reports complete GaN/AlN segment alternation. InGaN/GaN may also report
`semiconductor_alloy_same_sublattice_neighbor_pair_count` as a review warning
because In uses a larger preflight neighbor cutoff.
Explicit formulas such as `Build a 3-period In0.15Ga0.85N/GaN MQW` update the
template alloy metadata and choose a small in-plane supercell when needed;
`semiconductor_health.alloy_summary` reports requested versus actual finite-cell
composition. Layer and thickness requests may use compact aliases such as
`InGaN` or `AlGaN`.
The `quantum_well_summary` and `semiconductor_quantum_well.csv` also include
segment-level element counts, cation counts, and cation fractions, plus
well/barrier cation-fraction aggregates by material for quick alloy-composition
review.
Natural-language dopant-fraction patches record requested and actual
concentration in `metadata.applied_dopant_fraction`, expose
`semiconductor_health.dopant_fraction_summary`, and export
`semiconductor_dopant_fraction.csv`.
For III-nitride templates, Mg is treated as a cation-site acceptor dopant.
Requests such as `Build GaN wurtzite semiconductor crystal and dope with Mg`,
`dope Ga sublattice with Mg`, or `Build InN wurtzite semiconductor crystal and
dope with Mg` record `semiconductor_dopant_sites` and expose
`acceptor_like_p_type` through `dopant_site_summary` and site-adjusted
`charge_balance_summary`.
For TMD templates such as MoS2, dopant and defect follow-ups preserve
metal/chalcogen site roles. Requests such as `create S vacancy`,
`dope S sublattice with Cl`, or `dope with W` record `tmd_chalcogen` or
`tmd_metal` site families, inherit the substituted site's expected
coordination, and expose site-adjusted donor/acceptor hints through
`dopant_site_summary`.
Natural-language alloy patches record requested and actual composition in
`metadata.applied_alloy`, expose `semiconductor_health.alloy_summary`, and
export `semiconductor_alloy.csv`.
Formula-style alloy starts such as `Build SiGe alloy x=0.25 as a 2x1x1
supercell`, `Build Al0.25Ga0.75As as a 2x2x1 supercell`, and
`Build InGaAs alloy x=0.25 as a 2x2x1 supercell` use the same diagnostics path
and additionally record `metadata.formula_alloy_request`. III-nitride formula
starts such as `Build AlGaN alloy x=0.25 as a 2x2x1 supercell` and
`Build In0.25Ga0.75N as a 2x2x1 supercell` reuse the GaN wurtzite template; if
large-radius alloy atoms create same-sublattice preflight-neighbor hits, review
`semiconductor_alloy_same_sublattice_neighbor_pair_count` and
`neighbor_distance_summary`.
II-VI formula starts such as `Build Cd0.25Zn0.75Te alloy as a 2x2x1 supercell`,
`Build ZnS0.5Se0.5 alloy as a 2x1x1 supercell`, and
`Build ZnSe0.5Te0.5 alloy as a 2x1x1 supercell` use the zinc-blende II-VI
templates and keep the alloy on the cation or anion sublattice indicated by
the formula.
For new semiconductor crystal templates, it can also apply deterministic inline
modifiers in the same request, for example a supercell plus explicit-site or
auto-site dopant before hot-loading. If a vacancy or dopant site is omitted,
the selected site is recorded in `metadata.nl_auto_selected_sites`; explicit
site edits after an inline supercell require post-supercell IDs such as
`Si1_000`.
Treat
`nl_plan.kind="unsupported"` as a
request to provide a reviewed `ModelSpec` or `SemanticPatch`.

For semiconductor work, inspect
`material_studio_live_capabilities.domain_focus.semiconductor_template_ids`
before constructing a custom crystal. The built-in semiconductor templates are
preview-first `ModelSpec` examples with CASTEP energy defaults and full
view-bundle diagnostics.
CASTEP preview generation is pinned to the Materials Studio 20.1 scripting
contract. The compatibility tool `material_studio_castep_energy_script`
normalizes reviewed aliases to a canonical task, reports the resolved API
object in `castep_dispatch`, and never executes CASTEP itself. Custom cutoff and
primary SCF k-point settings use the documented `EnergyCutoff` and
`KPointDerivation` forms; separate property-grid sampling remains at the
Materials Studio default until a dedicated reviewed schema field is supplied.
The same contract exposes `dipole_correction` as the documented
`DipoleCorrection` setting with exact values `None`, `Non self-consistent`, and
`Self-consistent`. Non-self-consistent mode is Energy-only. For slab models,
the two-dimensional electrostatic audit additionally requires at least 8
angstrom of vacuum before treating an enabled setting as verified. No separate
dipole-direction MaterialsScript property is exposed by the verified 20.1 API,
so the MCP does not synthesize one. This is an input-setting receipt, not a
charge-density or dipole-moment result.
For structured crystal revisions, inspect the separate
`calculation_preview` receipt. Its `script_path` points to the persisted
`scripts/rNNN_castep_task.pl` companion, while `artifact_status`, generated and
persisted SHA-256 values, and `persisted_artifact_trusted` bind that script to
the current revision. Compact responses omit script source but retain this
binding and the dispatch summary.
`execution_policy="preview_only"` continues to describe the companion itself.
For Energy, BandStructure, DOS, PDOS, and GeometryOptimization, read the nested
`execution_handoff` for a separate dedicated-tool preview. That preview payload
is directly callable without confirmation and includes the exact `working_dir`,
`project_id`, and `expected_revision` that produced it. Do not remove those
bindings when following the action. A changed current revision returns a
revision-binding mismatch before runner invocation or run-directory creation.
The nested execute payload is only a template for an explicitly confirmed
second call; it is never authorized by the preview receipt alone. Optics,
Phonon, and ElasticConstants continue to report no dedicated execution tool.
For slab templates, read `modeling_report.inspection.surface` and
`modeling_report.inspection.slab_vacuum` to verify the surface orientation,
declared vacuum thickness, atom-center extent, inferred atom-center vacuum, and
termination metadata before trusting the model.
Plain surface hydrogen passivation is conservative and adds one H per detected
surface atom. Requests such as `fully hydrogen passivate both surfaces` or
`saturate all dangling bonds` add enough H atoms to satisfy the missing
tetrahedral coordination estimate when possible; confirm the result through
`modeling_report.inspection.semiconductor_health.surface_termination_summary`.
When the user explicitly requests hot-loading a crystal, execute mode writes a
CIF artifact and opens it in the GUI. This is reported as
`result.execution_backend="crystal_cif_materialize"`; it is not a CASTEP
calculation, it does not execute the CASTEP companion, and it does not require
guessing MaterialsScript lattice APIs.

For ongoing sessions, `material_studio_project_history` and
`material_studio_project_rollback` may omit `project_id`; the response includes
`project_resolution` when the latest current project is selected.

If live status reports `dopant_site_metadata_inconsistent`, follow its
`next_action_plan` and call
`material_studio_project_reconcile_dopant_metadata`. The tool creates a new
metadata-only revision when repair is needed, exports fresh diagnostics, and
returns `structure_unchanged` and `simulation_unchanged` invariants. When the
current metadata is already consistent it returns `already_consistent` without
adding an empty history entry. Keep the default in preview mode; execute and
same-window hot-load only after explicit user intent.

For a materialized crystal, inspect `structure_artifact_validation` before
trusting the visible model. `matched` proves the generated CIF agrees with the
current `CrystalSpec`; `not_materialized` is expected for preview-only state.
An existing `mismatch`, `missing`, or `parse_failed` artifact blocks model
normality. Follow the returned `next_action_plan` and, after explicit user
confirmation, call `material_studio_gui_apply_current_revision` with execute,
GUI open, snapshot, and view-audit export enabled. This rematerializes the same
revision and preserves revision history.

## CASTEP Relaxation Tool

The recommended config enables `material_studio_castep_relax_current` and sets
its approval mode to `prompt`. This is intentional: the same tool is
preview-safe by default, but `execution_mode="execute"` starts a real CASTEP
job through `RunMatScript.bat`.

Use a preview first:

```text
@mcp Run CASTEP geometry optimization on the current model, but keep execution_mode=preview.
```

The preview must report `execution_started=false`, `revision_created=false`,
and a missing planned optimized structure. After reviewing the exact script,
settings, cutoff, k-point sampling, license/queue readiness, and slab gates,
an explicit execute request may be made. Set `open_in_gui=false` when only the
calculation and revision promotion are wanted. When `open_in_gui=true`, the
tool requires one already-open Materials Studio process/window before CASTEP
starts and hot-loads only the converged promoted revision into that window.

The execute output should be treated as accepted only when these receipts are
present:

- `result_validation.ok=true` and `result_validation.converged=true`;
- `revision_created=true` with a new revision number;
- `relaxation_receipt.geometry_relaxation_verified=true`;
- `view_audit.health.semiconductor_health.castep_geometry_optimization_summary.transition_verified=true`;
- for fixed-cell slabs,
  `fixed_cell_transition_verified=true`.

## CASTEP Electronic Results Tool

The recommended config also enables `material_studio_castep_run_current` with
approval mode `prompt`. It handles only `Energy`, `BandStructure`,
`DensityOfStates`, and `ProjectedDensityOfStates` for the current crystal and is
preview-safe by default:

```text
@mcp Run CASTEP band structure on the current model, but keep execution_mode=preview.
```

Preview returns the exact script and all calculation gates without creating a
run directory, revision, structure, or GUI action. BandStructure, DOS, and PDOS
execute only after a verified geometry-optimization result is bound to the
current structure. Energy does not require that property-specific receipt, but
all other structural, semiconductor, cutoff, k-point, slab, and dipole gates
still apply.

An accepted execution has `status="castep_electronic_result_recorded"`,
`revision_created=true`, and
`electronic_receipt.receipt_binding_verified=true`. The new revision is
metadata-only: its CIF must remain bound to the unchanged source structure.
Runner-created native artifacts are accepted only from the isolated job
directory. With `open_in_gui=true`, preflight requires exactly one existing
Materials Studio window and the workflow never starts another GUI process.

Do not interpret backend completion as scientific convergence. The Materials
Studio 20.1 Energy Results API has no independent SCF convergence flag, so the
receipt intentionally reports `scientific_convergence_verified=false`. The
hash-bound native `.castep` output supplies a structured SCF audit, but a run
that completes below the configured maximum cycles is still review evidence,
not an independent convergence result.

Native Chart object names are retained for BandStructure, DOS, and PDOS, but a
Chart name is not numeric export. A valid native `.bands` file exports actual
k-point/eigenvalue rows for BandStructure. With explicit Smearing integration
and width, DOS also exports a deterministic, provenance-labeled Gaussian total
DOS. PDOS projection weights remain unsupported and fail-closed until the local
`.pdos_weights` layout is verified. Receipt v2 hash-binds the native audit and
all derived CSVs; an artifact mismatch makes the numeric export unverified.

For a hash-bound `.bands` file, read the compact sampled-band fields together:
`sampled_band_edge_status`, `sampled_band_gap_ev`,
`sampled_fermi_crossing_observed`,
`reported_band_gap_crosscheck_status`, and
`reported_band_gap_difference_ev`. The full receipt preserves per-spin VBM/CBM
states and native k-point coordinates. A Fermi crossing forces the sampled gap
to zero and requires metallic/semimetallic review. Even when the sampled gap and
Materials Studio `BandGap` agree, `scientific_band_gap_verified` remains false:
the result does not independently verify the analytic path, direct/indirect gap,
SCF convergence, or k-point convergence. Historical native-audit v1 receipts
remain readable but intentionally expose no trusted sampled-band summary.

For a recorded result, inspect
`inspection.semiconductor_health.castep_electronic_result_assessment` or
`semiconductor_review.electronic_result`. A verified artifact binding is not a
scientific convergence or band-gap conclusion. Result-review reasons are kept
separate in `live_readiness.calculation_result_review_reasons` and do not block
structure normality. The assessment's rerun payload always uses
`execution_mode="preview"`; execute requires a separate explicit request.

The natural-language request `Inspect the current CASTEP electronic result and
export native band edges` selects `castep_electronic_results` and runs the
read-only current-revision inspection path. A complete focus exposes
`semiconductor_castep_electronic_result.csv` and
`semiconductor_castep_band_edges.csv`; the latter contains aggregate, per-spin,
and Fermi-crossing provenance rows. This inspection does not rerun CASTEP or
create a revision.

For parameter studies, use requests such as `Inspect the current CASTEP cutoff
convergence series and export the CSV` or the explicit focus
`castep_convergence_series`. The server reloads every referenced immutable
revision, verifies its final matching electronic receipt and artifact hashes,
and compares cutoff energy, k-point separation, custom k-point grid, and
properties k-point separation only in separate series. The exported
`semiconductor_castep_convergence_series.csv` includes bound points, adjacent
deltas, and rejected-history reasons.

Two valid points establish pairwise sensitivity only; three are required for a
sequence. Defaults are 0.01 eV/atom for total-energy change and 0.05 eV for
reported `BandGap` change. Passing those thresholds never changes
`scientific_convergence_verified=false` or
`scientific_band_gap_verified=false`. Any suggested next point is returned as a
directly callable `material_studio_castep_run_current` preview with GUI loading
disabled. Execute remains a separately confirmed operation.

The active user config is not rewritten by the doctor or protocol smoke. After
merging the example snippet manually and restarting Codex, validate discovery
with:

```powershell
ms-mcp-config-doctor --cwd .
ms-mcp-protocol-smoke --cwd . --config .codex/config.toml.example
```

`material_studio_get_status` and `material_studio_live_session_preflight` also
publish a bounded runtime deployment receipt. `runtime_deployment` identifies
the loaded package root, source checkout, expected `run_server.py`, observed
entrypoint, process working directory, and the local Git HEAD/branch captured
when the MCP process started. This makes an old checkout visible even when its
source tree has not changed since startup. A direct `python -c` or pytest call
may report `entrypoint_binding=unobserved`; a Codex stdio server started through
the documented source entrypoint should report
`entrypoint_binding=matched_source_run_server`.

`codex_config_status` is advisory and read-only. It checks only the bounded
user-level Codex config and the loaded checkout's `.codex/config.toml`, selects
a matching registered entrypoint when present, and reports every candidate in
`config_candidates`. It never returns unrelated TOML contents, changes an
execution/hot-load gate, edits config, restarts Codex, launches Materials
Studio, or searches other drives/worktrees. Compare the reported Git HEAD with
the reviewed PR head outside the server; the local receipt deliberately does
not infer the newest remote branch. After an approved config or checkout
change, restart only the MCP session, keep the single Materials Studio window
open, and rerun `material_studio_live_session_preflight`.

The protocol smoke calls only the preview branch and asserts that CASTEP,
revision creation, structure materialization, and GUI input did not occur.

## Goal Watchdog

The optional local watchdog runs as one hidden user-level PowerShell process
and is restored by the Startup shortcut. It invokes
`scripts/codex_goal_watchdog.ps1` every 20 minutes. The runner skips a cycle
when a workspace `pytest` or `compileall` process is active, or when another
`codex exec` process is already handling the workspace. It also locates the
primary goal thread JSONL under `.codex/sessions` and skips unattended writes
until that thread has been quiet for at least 30 minutes. The 20-minute timer
therefore remains active without starting a second writer while an interactive
Codex turn or long validation is still progressing.
Each invocation owns a named iteration mutex and releases it on successful,
failed, and skipped paths. Two consecutive `-DryRun` calls must therefore both
produce `dry_run_ready`; a persistent `another watchdog iteration is already
active` entry indicates a stale daemon that should be restarted.

The default continuation is a fresh, ephemeral `codex exec` session with
`--model gpt-5.4-mini`, `--sandbox workspace-write`, and
`-c 'mcp_servers={}'`. It preserves the user configuration that marks this
workspace as trusted, while replacing the MCP server table for the unattended
child session so unrelated external MCP startup failures cannot block it. Do
not replace this with `--ignore-user-config`: on the current Windows CLI that
also removes project trust and silently reduces the child session to read-only.
The model is explicit because the locally installed Codex CLI may not support
the newer model selected by the desktop application. This avoids repeatedly
compacting an oversized prior task.
Transient model-refresh or transport startup failures are retried once after
15 seconds; the retry is skipped if validation or another workspace Codex
process starts meanwhile. The continuation prompt preserves the Materials
Studio single-window rule and forbids process enumeration because the parent
watchdog has already performed that check.

Every unattended fresh continuation is transactional. Before the child starts,
the parent makes a byte-preserving snapshot of source, tests, scripts,
configuration, examples, and documentation under
`workspace/codex_watchdog/transaction-*`. A successful child is committed only
after parent-owned validation passes. A failed child or failed validation is
rolled back to the snapshot only when the primary goal thread did not become
active during the transaction; the restored tree is validated again before the
snapshot is removed. If safe rollback cannot be proven, the snapshot is kept
and later write cycles stop for manual review instead of overwriting newer work.

The restricted Codex child does not run pytest directly because its Windows
sandbox can leave ACL-restricted pytest temp directories. After a successful
child turn, the parent watchdog uses the repository `.venv` to run
`tests/test_reports.py`, `tests/test_model_diagnostics.py`, and
`tests/test_live_smoke.py`, plus `tests/test_watchdog_script.py`, the short-path
GUI-wrapper regression, and the 4H-SiC contact smoke regression. Pytest uses a
short `C:\mpt_wd_*` base path with its cache disabled. The parent then runs
`python -m compileall -q src tests`, so source or test-file encoding damage is
detected before a transaction can commit. The
per-cycle result is stored in `run-*-validation.log` and summarized in
`watchdog-status.log`.

Use the following command to validate the watchdog gate without starting a
Codex continuation:

```powershell
.\scripts\codex_goal_watchdog.ps1 -Workspace (Get-Location).Path -DryRun
```

Use `-ValidationOnly` to run the parent-owned focused validation without
starting a Codex continuation.

Status and per-cycle JSONL logs are stored under
`workspace/codex_watchdog/`. Stop the daemon with the PID recorded in
`workspace/codex_watchdog/daemon.pid`; removing the Startup shortcut disables
automatic restoration at the next sign-in.
