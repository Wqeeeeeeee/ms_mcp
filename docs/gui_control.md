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
- `material_studio_gui_copy_script_assist`: returns a checklist for extracting exact Materials Studio Copy Script output, with status scoped to the latest current project when no project context is supplied.
- `material_studio_gui_prepare_view_replay`: resolves the requested/current revision, computes deterministic Cartesian, crystal-direction, reciprocal-plane-normal, or surface/interface-frame camera parameters, and writes `gui_view_replay_manifest.json` under that revision. Optional `runtime_ui_evidence` records a current-window Miller-plane UI probe in `gui_view_replay_runtime_preflight.json`; the evidence is written only after exact revision, wrapper handle/title, and single-window binding succeeds. The tool never activates the window or changes the GUI.
- `material_studio_gui_record_view_replay`: records Computer Use, reviewed Copy Script, or human evidence for one prepared view in append-only `gui_view_replay_events.jsonl`. Evidence is accepted only when the wrapper identifies the exact project/revision, the current revision is loaded, and the single-window policy passes. Optional exact handle/title binding and a reviewed `native_command_id` make the event machine-auditable.
- `material_studio_gui_record_visual_confirmation`: persists Computer Use or manual viewport evidence after verifying the current project/revision, exact wrapper title and handle, wrapper metadata, and single-window state. The same path is available through `material_studio_live_modeling_request.visual_confirmation` for restricted MCP allowlists.

Accepted visual and view-replay evidence recomputes the current revision's
diagnostic report, but that automatic re-audit is not itself a user-requested
normality check. The `gui_evidence_reaudit` receipt records the trigger, prior
and current-request diagnostic intent, effective intent, and proves that no
revision, structure, or simulation state was changed. Existing explicit intent
is preserved; a plain evidence-recording request cannot silently set
`normality_check_requested=true`.
- `material_studio_live_session_preflight`: read-only session check that combines runner status, GUI status, latest current project, readiness flags, and next recommended tool.
- `material_studio_live_capabilities`: lists the high-level live-modeling entry point, deterministic natural-language templates, supported patch commands, schema paths, GUI tools, and diagnostic fields.
- `material_studio_live_update_with_patch`: applies a semantic patch, creates a new revision, and can execute/open it in the live GUI when explicitly requested.
- `material_studio_live_modeling_request`: high-level entry point for a natural-language request, with optional local template inference or explicit `ModelSpec`/`SemanticPatch` payloads.
- `material_studio_live_project_status`: summarizes the current revision, saved script, planned outputs, latest change, persisted `view_audit.json`/`report.json` receipt, computed audit, `modeling_health`, optional GUI status, and next action. If the audit JSON is missing a GUI-open artifact but `report.json` still has `gui_open`, status uses that fallback to preserve current-revision GUI checks.
  It also returns `gui_view_replay` with the current revision's replay manifest/event paths, replay status, preflight, confirmed-view counts, last event, and next action so resumed sessions and watchdog checks can continue without scanning the workspace.
- `material_studio_model_export_view_audit`: exports `modeling_health`, model-health checks, semiconductor health checks, stable spec fingerprints, rounded atom coordinates, and per-view projection parameters for front/back/right/left/top/bottom/isometric-style inspection.
- `material_studio_model_export_view_bundle`: writes `view_audit.json` plus CSV tables for atoms, bonds, bond angles, dihedrals, connectivity, close contacts, crystal nearest neighbors, crystal coordination, semiconductor lattice volume/density, semiconductor neighbor-pair distances, semiconductor local environments, semiconductor interface profiles, semiconductor interface quality, MOS/gate-stack diagnostics, metal/semiconductor contact diagnostics, semiconductor composition, nominal charge-balance/valence-electron summaries, semiconductor calculation-preflight summaries, reciprocal-lattice/k-point summaries, band-path preflight summaries, band-alignment metadata preflight summaries, semiconductor sublattice balance, semiconductor layer profiles, semiconductor dopants, p-n junctions, dopant fractions, alloy fractions, finite-size/dilution preflight, vacancy/defect summaries, heterostructure strain, surface termination, surface polarity/asymmetry, view summaries, per-view atom projections, projection overlaps, a health summary, and a compact modeling-report summary.

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
direction/up vector. Therefore view replay is deliberately split into two
auditable phases:

1. `material_studio_gui_prepare_view_replay` persists exact camera, framing,
   crystallographic metadata, expected projection bounds, target-window
   identity, and single-window preflight state without touching the GUI.
2. Computer Use or locally reviewed Copy Script output activates and verifies
   the exact wrapper window, applies the view, captures fresh visual evidence,
   and calls `material_studio_gui_record_view_replay` for that view.

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
automatic-ready when both the local command registry and installed keyboard
help are verified. `front` targets the named Reset View control; `back` uses
Reset + `Left x4`; `right` uses Reset + `Up x2, Left x2`; `left` uses Reset +
`Up x2, Right x2`; `top` uses Reset + `Up x2`; and `bottom` uses Reset +
`Left x4, Down x2`. The installed help defines each arrow rotation as 45
degrees and
states that Shift+arrow rotates selected objects, so Shift is prohibited and
the camera axis layout plus projection/overlap counts require a fresh visual
postcheck. Isometric is also automatic-ready through the verified staged
recipe Reset, `45 degrees: Up x2, Left x3`, then `35.26438968 degrees: Down`.
It must show A left-down, B right-down, C up, restore Angle to 45 degrees,
preserve Screen factor 2.0, and close Movement.

`crystal_plane_*` views have a separate documented MS 20.1 recipe. Installed
Miller Plane, Properties Explorer, and View Onto registry/help evidence is
necessary but not sufficient. Automatic replay also requires a current
`gui_view_replay_runtime_preflight.json` observation whose revision, wrapper
handle/title, and single-window binding still match. The observation must prove
that Reset View, Tools > Miller Planes, the `Miller Planes` dialog,
`MillerPlanesCtl`, `TxtHKL`, `CmdCreate`, Properties Explorer, View Onto, and
one supported semantic plane-selection profile are present at runtime. Missing,
incomplete, or stale evidence returns `runtime_ui_preflight_required` and keeps
`automation_ready=false`.
In that state, `replay_continuation.payload_hint_is_directly_callable=false`;
the hint identifies the evidence schema and window binding but deliberately
omits example `miller_plane_evidence` values.

Open Tools > Miller Planes only through the verified keyboard menu path
`Alt+T`, then `M`. Do not invoke that menu item with a pointer or accessibility
click: in MS 20.1 the release can click through into the modeless dialog and
activate Create. If an unexpected default plane is created, invoke only the
exact named `Undo Create Miller Plane`, verify a clean document, no temporary
node, and an unchanged structure hash, then abort the replay attempt and run
the preflight again.

After that gate passes, the recipe resets the view and creates exactly one
temporary plane with the requested three-index dialog values. On an installation
that exposes Object Tree, it may isolate the exact new
`<Miller Family>/<Miller Parallel Planes>/<Miller Plane>` leaf by before/after
diff and select its semantic item rectangle. The local MS 20.1 installation
instead verifies that Object Tree is hidden and that Project Explorer contains
project documents, so Project Explorer must not be used as a substitute. Its
supported `viewport_unique_plane_properties_verified` profile captures fresh
screenshots before and after creation, derives one unique newly rendered plane
region, selects only that fresh region with no modifiers, and verifies
`Filter=Miller Plane` plus the expected Miller label in Properties Explorer.
Only then may it invoke the named 3D Viewer Recenter > View Onto item. It
captures the aligned view before cleanup, then accepts only observed whitelisted
View Onto/Create Miller Plane/Reset View undo labels (plus Recenter when present)
and requires the document to be clean, no temporary plane to remain, the reset
view baseline to be restored, and the wrapper source structure SHA-256 to be
unchanged. These observations are submitted in `miller_plane_evidence`.

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
title/wrapper identity, and only then inspect or send input. Unnamed toolbar
controls and blind coordinates are not an accepted replay backend.
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
Al/SiO2/Si MOS capacitor, TiN/HfO2/Si high-k MOS capacitor, and Cu/SiO2 use the
same interface review fields, but are treated as one-shot interface starts
rather than periodic quantum-well stacks or passivated semiconductor slabs. MOS
capacitor templates also emit a dedicated `gate_stack_summary` and
`semiconductor_gate_stack.csv` table that check the expected `Si -> SiO2 -> Al`
or `Si -> HfO2 -> TiN` material sequence, gate/oxide/channel presence, declared
oxide/gate/channel thicknesses, and per-segment layer spans. Si/SiO2,
Al/SiO2/Si, and TiN/HfO2/Si mark mixed oxide or compound gate layers as
expected, so those layers remain visible in `interface_profile_summary` without
becoming a mixed-interface risk flag.
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
neighbors. For heterostructure templates,
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
5. For real-time GUI updates, call `material_studio_live_modeling_request` with the original user text. If the request matches a conservative local template, the tool can infer the payload; otherwise provide a `ModelSpec` for new projects or a `SemanticPatch` for modifications. For precise follow-up patches, including MOS/gate-stack layer-thickness edits, the high-level entry can use the latest workspace `current.json` when `project_id` is omitted and returns `project_resolution` to make that choice visible. If `execution_mode` is omitted, the entry point stays in preview unless the text explicitly asks for hot-loading/real-time GUI execution, in which case it returns `execution_mode_source="explicit_live_intent"`.
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
