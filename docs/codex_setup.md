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

Use `response_mode="compact"` for normal interactive calls to the live
capabilities, modeling, update, status, view-bundle, and GUI-apply tools. The
compact receipt includes multi-view camera parameters, normality and
semiconductor decisions, current GUI revision identity, next-action payloads,
and stable artifact paths. Use `response_mode="full"` only when the complete
in-band report is needed; both modes persist the same full report files.

Compact schema v2 is protocol-tested below 48 KB for capabilities, create,
status, and view-bundle replies. `view_bundle_files` normally remains the complete
diagnostic artifact index; top-level `artifacts` is intentionally limited to
frequent entry points. Full diagnostic-focus profiles and repeated evidence
trees are retrieved with `response_mode="full"` or from `report_json_path`.
For unusually large all-view/focus requests, inspect
`response_compaction.hard_budget_applied` and `omitted_fields`; use the returned
detail paths instead of assuming an omitted duplicate field was unavailable.

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
For isometric, use the returned `keyboard_stages`: Reset, then `45 degrees: Up
x2, Left x3`, followed by `35.26438968 degrees: Down x1`. Also submit
`rotation_increment_restored_degrees: 45`, the returned Movement command and
control IDs, `movement_screen_factor: 2.0`, and
`movement_dialog_closed: true`.
Before any `crystal_plane_*` or exact-collinear `crystal_*` replay can become
automatic-ready, observe the live controls on the exact current wrapper and
submit them back to the prepare tool. A complete payload has this shape:

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
    "tree_explorer_menu_observed": true,
    "properties_explorer_menu_observed": true,
    "view_onto_control_observed": true,
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
    "selection_modifier_keys": []
  }
}
```

Report observed values only. Static XML/help evidence cannot replace this
probe. Never click Tools > Miller Planes with the pointer or an accessibility
menu action; use `Alt+T`, then `M`. If that invocation creates an unexpected
plane, use only the exact named `Undo Create Miller Plane`, verify cleanup, set
the cleanup evidence truthfully, and abort the replay attempt.
While this gate is blocked, the continuation receipt sets
`payload_hint_is_directly_callable=false` and does not supply record-evidence
examples. Fill the strict runtime evidence schema from the current observation.
For `crystal_plane_*`, submit the returned `miller_plane_evidence` only after
observing every value. It binds the requested/dialog Miller indices, exact
Object Tree leaf selection and Properties label, plane-normal/native-roll
camera scope, screenshot timing, temporary-plane counts, whitelisted undo
labels, clean document/tree/view restoration, and equal before/after SHA-256
for the wrapper source structure. Do not reuse the payload hint's example
counts without inspecting the current Object Tree. Exact analytic in-plane
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

Call `material_studio_live_session_preflight` when starting a live @mcp
session or when runner/GUI/latest-project readiness is uncertain. It returns
runner status, GUI status, latest current project metadata, readiness flags,
safe smoke-test prompts, and the next recommended tool.
If `latest_project.current_pointer_recovery_used=true`, continue read-only
inspection from the returned immutable revision and report the recovery. Do
not rewrite `current.json` merely to hide the warning; only an explicit
successful revision-changing request may atomically repair it.
For a local command-line acceptance pass, run
`ms-mcp-live-smoke --scenario sic_mos --working-dir workspace/live_smoke`.

For preview-only 6H-SiC surface and contact acceptance, run
`ms-mcp-live-smoke --scenario sic_6h_slab --execution-mode preview --no-include-gui-status --no-take-snapshot --working-dir workspace/live_smoke_6h_slab`
or
`ms-mcp-live-smoke --scenario sic_6h_contact --execution-mode preview --no-include-gui-status --no-take-snapshot --working-dir workspace/live_smoke_6h_contact`.

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
`set_bond_type` and `set_metadata`.

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
reasons, so clients do not need to parse the prose `next_action`. Use
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
for explicit supercells, superlattice period repetition, lattice strain, dopant fractions, alloy fractions, vacancies, interstitials, antisites, dopant substitutions, auto-site vacancy/dopant selection, vacuum layers,
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
calculation and it does not require guessing MaterialsScript lattice APIs.

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
