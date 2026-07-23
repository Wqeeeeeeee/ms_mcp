# Revision-bound execution handoffs

Structured preview and status responses may recommend
`material_studio_gui_apply_current_revision` after the user explicitly confirms
execution. The returned `payload_hint` is a directly callable handoff, not a
generic example.

## Required bindings

An apply handoff binds:

- `project_id` and `expected_revision`;
- the exact `working_dir` that owns the project;
- `execution_mode="execute"` and the GUI/snapshot/export choices;
- `verify_ms_roundtrip=true` when the preview requested the Materials Studio
  CIF import/export audit;
- explicit `fit_to_view_after_open` and
  `prepare_view_replay_after_open` choices;
- the selected diagnostic `views` when they were explicitly requested or are
  needed for post-hot-load replay.

The action continues to report `needs_user_confirmation=true` and
`safe_to_call_without_confirmation=false`. A preview receipt never authorizes
execution on its own.

When live status first requires a GUI preflight, the same apply action is kept
under `next_action_plan.deferred_hotload_action`. Its payload retains the same
workspace, revision, and safety options as the create/patch preview.

## Stale handoffs

`material_studio_gui_apply_current_revision` accepts optional
`expected_revision`. If the project current revision changed after the handoff
was generated, the tool returns `status=current_revision_execution_block` with
`execution_started=false`. This check runs before:

- runner or CIF materialization;
- execution-attempt journal creation;
- Materials Studio GUI status probing or input;
- output or report mutation.

Refresh `material_studio_live_project_status` and review the newly current
revision before requesting another explicit execution. Do not remove or rewrite
`expected_revision` to force an old handoff through.

## Protocol acceptance

The default `ms-mcp-protocol-smoke` silicon preview requests
`verify_ms_roundtrip=true` and validates both the immediate create handoff and
the status/preflight-deferred handoff. Acceptance requires:

- matching project, revision, workspace, views, and payloads;
- `expected_revision` equal to the preview revision;
- `verify_ms_roundtrip=true` retained;
- an explicit confirmation gate;
- preview-only round-trip planning with no runner, GUI input, or round-trip run
  directory.

This is still protocol-level, zero-side-effect evidence. It does not claim that
real Materials Studio 20.1 execution or GUI hot-loading was performed.
