# MCP Protocol Acceptance

`material_studio_mcp_server.protocol_smoke` proves that a real MCP client can
start the stdio server, negotiate the protocol, discover the expected tools,
and invoke the preview-safe live workflow. It complements direct Python unit
tests; it does not replace them.

## Run

```powershell
.\.venv\Scripts\python.exe -m material_studio_mcp_server.protocol_smoke `
  --cwd . `
  --workspace workspace\mcp_protocol_acceptance `
  --config .codex\config.toml.example `
  --output workspace\mcp_protocol_acceptance\summary.json
```

The command verifies:

- MCP initialization and server metadata.
- Complete paginated `tools/list` discovery.
- Required live, GUI, view replay, and repair tools.
- Selected input schema fields and safety annotations.
- Preview-only silicon creation, project status, history, and three-view export.
- Isolated view-replay manifest preparation followed by a resumed-session
  preflight with a revision-bound `visual_diagnostics` action track.
- One coordinated sequence reference, with the callable visual payload kept in
  `visual_diagnostics_next_action_plan` and no extra model revision.
- No materialized structure and no GUI open during the preview path.
- Optional Codex TOML drift without writing the config.
- Compact responses for capabilities, create, status, replay preparation,
  resumed preflight, and view-bundle calls, each below the 48 KB protocol
  acceptance limit.
- The resumed preflight's own compact receipt: exact serialized byte count,
  45 KB target, 48 KB budget, positive headroom, and no target overflow.

## Safety

The acceptance workspace is supplied through
`MATERIAL_STUDIO_MCP_WORKSPACE`. Preview calls do not invoke
`RunMatScript.bat`, do not launch `MatStudio.exe`, and do not modify the active
Materials Studio project. `material_studio_run_script` must remain explicitly
disabled in the example Codex config.
Replay preparation writes only its manifest inside the isolated acceptance
workspace; it does not issue GUI input, mutate the structure, or create a
second revision.

## Config Drift

Run the dedicated read-only doctor against the active config before protocol
acceptance:

```powershell
ms-mcp-config-doctor --cwd . `
  --output-snippet workspace\codex_config\materials_studio.toml
```

It distinguishes missing registration, legacy `ms_mcp.server` entrypoints,
disabled servers, path drift, and tool-allowlist drift. The receipt includes
before/after SHA-256 values proving the active config was not changed. The
generated snippet uses absolute paths for the current checkout, keeps custom
script execution disabled, and can only be written to a separate file.

Without `--strict-config`, protocol acceptance can pass while the summary
reports an incomplete active Codex allowlist. This is useful when validating a
server before the user decides to update their local config. With
`--strict-config`, any missing required tool, enabled custom-script tool, or
missing explicit custom-script disablement makes the command fail.

The audit is intentionally read-only. Update the active `.codex/config.toml`
only with the user's explicit approval, then restart the Codex MCP session.

## Response Modes

The live capabilities, modeling, update, status, view-bundle, and GUI-apply
tools accept `response_mode="compact"` or `response_mode="full"`. Compact mode
is intended for interactive @mcp work and returns the decision receipt plus
camera parameters and artifact entry points. Full mode preserves the previous
in-band response shape. Neither mode changes persisted diagnostics or execution
behavior.

Compact schema v2 removes repeated evidence trees and full capability catalogs
from the in-band receipt. It targets 45 KB below the hard 48 KB limit, keeps one
authoritative next-action payload, and preserves normality, visual, and camera
decision fields before artifact indexes. It uses `report_json_path`,
`view_audit_report_path`, and `view_bundle_manifest_path` as stable detail entry
points.
Complex responses that require hard-budget fallback return
`response_compaction.hard_budget_applied=true` and an explicit `omitted_fields`
list. The same receipt reports `semantic_core_preserved`, exact
`response_bytes`, and remaining `headroom_bytes`. This fallback changes only the
in-band receipt, not persisted diagnostics.

Protocol discovery also requires
`material_studio_gui_record_visual_confirmation`. The high-level modeling tool
exposes the same persistence path through its `visual_confirmation` payload so
clients with a restricted allowlist can record Computer Use evidence. Evidence
is accepted only when its revision, handle, exact wrapper title, project
metadata, and single-window state match the current GUI.

Discovery also validates the direct view-replay recorder's exact-window,
reviewed-command, and compact-response fields. Restricted clients can submit
the same evidence through
`material_studio_live_modeling_request.view_replay_confirmation`; the schema is
strict, and a stale revision or window mismatch is rejected before any replay
event is appended.

Compact capabilities also expose `view_replay_automation_policy`, while compact
project status preserves `gui_view_replay.replay_continuation`. Clients must
honor `automation_ready`; the current MS 20.1 policy defines named Reset View
for `front`, installed-help-backed unmodified arrow-key recipes for `back`,
`right`, `left`, `top`, `bottom`, and staged `isometric`. Static eligibility is
not automatic readiness. `runtime_accessibility_evidence` must bind the current
revision and wrapper handle/title, prove the exact named controls are invocable,
and persist as `gui_view_replay_accessibility_preflight.json`. Protocol
acceptance verifies that this nested schema is discoverable and strict
(`extra="forbid"`). Unnamed toolbar children remain blocked unless the server
derives an allowlisted command from the installed registry SHA-256 plus an
exact live toolbar child sequence. In that narrow path, protocol discovery must
expose `anonymous_toolbars` and the record tool's
`accessibility_command_uses`; mismatched mapping receipts are rejected. The
receipt must also prove the exact key sequence or stages, Reset precondition,
angle, no modifiers, setting restoration, and visual axis/projection match.
Protocol acceptance also verifies that a complete, integrity-verified command
receipt followed by a failed visual postcheck suppresses automatic retry. A
failed `front` Reset semantic hash must block pending keyboard/isometric
recipes that depend on the same Reset baseline, persist across manifest
re-prepare, and return `automatic_recipe_postcheck_failed` with a reviewed
Copy Script/manual continuation. Re-preparing a different view subset must
preserve the immutable revision's complete replay-event history, and only a
later integrity-verified success for the same semantic mapping may clear the
gate.
The record tool's nested `reviewed_copy_script_evidence` schema is also
discoverable and strict. Selecting `source="reviewed_copy_script"` requires the
exact inert script, completed review attestations, exact current wrapper
handle/title, and a workspace screenshot. The server must report
`execution_allowed=false`; unsafe external-effect, calculation, or structural
mutation findings block acceptance and raw script persistence.
Accepted reviewed evidence exposes a SHA-256 integrity receipt for the bound
screenshot, inert script, metadata, and structure artifact. Live status must
reverify those artifacts. A mismatch preserves append-only history but removes
the view from trusted acceptance and invalidates the replay-derived visual
confirmation until new evidence is recorded.
Every new replay event also exposes a stable record SHA-256. The manifest copy
and durable JSONL copy must reconcile one-to-one on live status. Missing,
duplicate, malformed, or digest-mismatched journal records block trusted view
acceptance and replay-derived visual confirmation without rewriting either
source.
Protocol acceptance also requires project/revision-scoped serialization for
prepare and record writes. Two concurrent records must both survive in the
manifest and JSONL journal, prepare must preserve an event committed ahead of
it, and lock timeout must occur before a journal append.
Accepted visual confirmations must also serialize their report read-modify-write
transaction per project/revision. Protocol tests require two concurrent notes to
remain in `gui_artifacts`, a lock timeout to preserve the committed report, and
an injected atomic-replace failure to leave valid prior `report.json` bytes with
no temporary file residue.
`crystal_plane_*` recipes may also be automatic-ready when installed Miller
Plane/Properties/View Onto evidence, one supported semantic selection profile,
and an exact current-window accessibility binding are verified. External replay
also requires a bound `runtime_ui_evidence` probe. The local transactional
executor may generate that UI evidence during explicit execution; it must not
invoke Reset.
Protocol acceptance checks that the prepare-tool field and its nested viewport
probe are discoverable and strict (`extra="forbid"`). Runtime evidence must bind
the exact revision and wrapper handle/title, verify the required live controls,
and use the `Alt+T`, then `M` dialog path. Static registry/help files alone never
satisfy this gate. The MS 20.1 viewport profile additionally requires fresh
before/after screenshot hit testing, unique transient-plane selection,
Properties filter/label verification, live numeric View Onto mapping, exactly
the View Onto/Create Plane undo labels, and equal before/after/current structure
hashes. Project Explorer is not accepted as Object Tree. The record-tool schema
also exposes strict `miller_plane_evidence`; runtime acceptance additionally
requires exact plane selection, a pre-action viewport baseline, pre-cleanup
aligned screenshot, exact two-step cleanup, restored document/temporary-plane
state, pixel-identical viewport restoration, and an unchanged wrapper-source
SHA-256. The camera scope is plane-normal plus native MS roll rather than exact
analytic up/right. Protocol tests also require the Miller recipe to declare
`reset_view_allowed=false`, no Reset accessibility target, final camera from
`cmdViewer3DViewOnto`, and independence from failed generic front Reset
orientation. The execute tool must expose preview-by-default semantics and
must leave visual acceptance and replay-event creation for the record tool.
Live MS 20.1 acceptance also covers the internal viewer-document dirty suffix,
the exact owner-drawn Properties Explorer command ID `33439`, the virtualized
`MillerIndex Record 0` DataItem under a visible Properties grid, and clipped
viewport capture on negative-coordinate monitors. The clipping receipt must
show an unbroken visible chain from `CViewer3DCtrl` through the internal
document and `MDIClient` to the exact target-window handle. Protocol tests keep
zero-pixel viewport restoration while proving that mutable status-bar text
outside that chain is excluded. These runtime-specific allowances remain
fail-closed for any other command ID, duplicate/mismatched property row, hidden
pane/grid/ancestor, broken target binding, or undersized visible intersection.
The same schema supports crystallographic directions only when the prepared
recipe reports an exact direct-direction/reciprocal-normal collinearity
mapping; it additionally requires the direction-match boolean. Directions
without that exact mapping remain review-gated.
