# Natural-Language Workflow

## Benzene Preview

Before a live GUI session, `material_studio_live_session_preflight` can be used
as the safe first call. It reports runner readiness, GUI window availability,
latest-project context for follow-up edits, and the recommended next tool
without executing scripts or changing the open GUI.
For a resumed project, `next_action_plan` remains the backward-compatible
immediate session action. Read `coordinated_next_action_plan.recommended_sequence`
for the complete three-track order. `session_next_action_plan` handles
activation/reload/preflight, `visual_diagnostics_next_action_plan` handles the
current revision's view-replay or visual-diagnostic continuation, and
`modeling_next_action_plan` carries structural or calculation remediation.
Require `latest_project_visual_diagnostics.binding_verified=true` or
`latest_project_modeling.binding_verified=true` as appropriate, follow each
`plan_ref`, and rerun preflight after every step. GUI session work does not
clear visual/modeling actions, and visual-diagnostic preparation does not
satisfy a later modeling confirmation gate.
Preflight compacts duplicated runner/window metadata only after deriving this
sequence. Check its `response_compaction` receipt, follow the returned
`latest_project_gui.*_ref` paths for compact context, and use the receipt's
full-detail tools when the complete GUI inventory or runner search is needed.
When it recommends `material_studio_gui_launch`, use that tool only when
starting a new GUI session is intentional; otherwise activate an already-open
Materials Studio window first. Explicit hot-load/open paths do not implicitly
launch `MatStudio.exe`. On Windows, the local fallback first tries to load the
workspace `.stp` wrapper through the already-running window's File/Open dialog.
If that same-window dialog path is unavailable or fails, the request keeps the
generated structure and report artifacts and returns `gui_open_warning` instead
of opening another GUI window.
When the only running Materials Studio process is still on its welcome page,
the Windows fallback cancels an empty `New Project` dialog if present and uses
the welcome page's `Browse...` picker to choose the exact generated `.stp`.
Directly replacing the welcome-page text is not sufficient because a selected
Recent Projects entry can otherwise win and open a stale project.

User asks to create benzene and optimize it with Forcite. Codex should produce a `ModelSpec`, call `material_studio_model_validate`, then call `material_studio_model_create_from_spec` with `execution_mode=preview`.

For the high-level `material_studio_live_modeling_request` entry point, omitting `execution_mode` still resolves to preview by default. It resolves to execute only when the user text contains explicit live-loading intent such as "hot-load it in Materials Studio", "real-time GUI", "push it to MS", "let me see it in MS", "实时热加载", "推送到当前窗口", or "让我在 MS 里看到变化"; the response records this in `execution_mode_source`.

For ongoing sessions, display-only requests should stay on the GUI hot-load path
instead of becoming diagnostics-only. Chinese requests such as `把这个模型推到当前
MS 窗口，同时导出视角参数` should resolve to `workflow=show_current`,
`execution_mode=execute`, `execution_mode_source=explicit_live_intent`, and
`diagnostic_export_requested=true`. Short reload requests such as `重新热加载当前
revision` also resolve to `show_current` and execute against the latest current
revision without creating a new revision.

Requests that contain an actual structural edit still take precedence over
display-only routing. For example, `把 Si1_000 从 P 换回 Si并热加载到当前窗口` and
`replace Si1_000 from P back to Si and hot-load it` create a new semantic-patch
revision before opening it in the same Materials Studio window.

Diagnostic export requests can choose the view subset in natural language. For
example, `导出正视、俯视和等轴测视角参数` exports front/top/isometric rows,
`导出六视图参数` exports front/back/right/left/top/bottom, and `导出标准视角参数`
exports the default front/back/right/left/top/bottom/isometric set.
`导出三视图模型参数` exports front/right/top rows. Broader Chinese phrases such
as `导出各个视角模型参数`, `导出不同视角模型参数`, `导出多角度模型参数`, and
`导出全视角模型参数` select the default seven-view set.

When no view phrase or explicit `views` list is supplied, semiconductor crystal
diagnostics use a domain-aware default set. Every set starts with
front/top/isometric. Interface models then add interface-normal and two in-plane
views, surface or slab models add surface-normal and two in-plane views, and
bulk models add three lattice-family plane-normal views. Cubic bulk defaults to
(100)/(110)/(111), while hexagonal bulk defaults to
(0001)/(10-10)/(11-20). Explicit views are never expanded by this policy. The
same `view_selection` receipt is available in the audit, modeling report, view
parameter summary, and live summary.

Crystal diagnostics also accept lattice-aware direction views. For example,
`export [001], [110], and [111] crystallographic view parameters` selects
`crystal_001`, `crystal_110`, and `crystal_111`; `沿[0001]晶向导出视图参数`
selects `crystal_0001`; and crystal directions can be combined with screen
views. These camera vectors are computed from the actual lattice vectors, so a
hexagonal `[010]` direction is not treated as Cartesian Y. `view_summary.csv`
records `coordinate_system`, `crystal_direction_indices`,
`crystal_direction_label`, and `crystal_direction_cartesian` for external
normality and orientation checks.

Crystal-plane requests use reciprocal-space normals and remain distinct from
lattice directions. For example, `view normal to the (100), (110), and (111)
crystal planes` selects `crystal_plane_100`, `crystal_plane_110`, and
`crystal_plane_111`; `沿(0001)晶面法向导出视图参数` selects
`crystal_plane_0001`. Hexagonal `(10-10)` and `(11-20)` plane views are also
available. `view_summary.csv` records the Miller or Miller-Bravais indices,
plane label, normalized Cartesian normal, reciprocal vector in inverse
angstroms, reciprocal convention, and `crystal_plane_spacing_angstrom`. The
reciprocal vector uses the `dual_basis_without_2pi` convention, so
`d_hkl_angstrom = 1 / magnitude(g_hkl_per_angstrom)`. These values are computed
from the actual reciprocal lattice, so a monoclinic `(001)` plane normal is
not silently treated as the direct-lattice `[001]` direction.
Plane labels in an explicit view/normal/projection clause do not select a slab
template. For example, `build beta-Ga2O3 crystal and export (001) and (010)
plane-normal views` keeps the monoclinic bulk template. A surface model still
requires explicit modeling intent such as `build beta-Ga2O3(010) surface slab`.

Surface normality diagnostics distinguish source-plane provenance from the
transformed slab cell. Local slab templates use
`parent_bulk_mapped_to_cell_axis`: the declared parent `(hkl)` is mapped to the
current cell's `surface_axis`, which is also the vacuum/stacking axis. Reviewed
custom specs can set `metadata.surface_orientation_basis=current_cell`; the
diagnostic then computes the reciprocal-lattice normal and its angle to the
surface axis. More than 1 degree produces
`current_cell_plane_axis_mismatch`, the `surface_orientation_mismatch` risk
flag, `normality=failed`, and `ready_for_calculation=false`. The complete
receipt is available at
`inspection.semiconductor_health.surface_orientation_summary` and in
`semiconductor_surface_model.csv`.

Surface and interface models also expose a cell-aware orthogonal camera frame.
`export surface-normal and two surface in-plane view parameters` selects
`surface_normal`, `surface_in_plane_1`, and `surface_in_plane_2` from
`metadata.surface_axis`. The interface form selects `interface_normal`,
`interface_in_plane_1`, and `interface_in_plane_2` from
`metadata.interface_axis`. Chinese requests such as
`导出表面法向和两个面内视图参数` and `导出界面法向和两个面内视图参数`
use the same path. `view_summary.csv` records the frame kind, role, source
metadata field, axis, reference cell axis, orthonormal direction, and both
in-plane basis vectors. The parameters are independent of screen coordinates
and remain reproducible after same-window hot-loading; actual GUI viewport
rotation still requires Computer Use or reviewed Materials Studio Copy Script
output.

For requests that ask whether the current model is normal, client code should
read `modeling_report.normality_gate` before answering. A preview can be
`preview_ready` while `normality_gate.can_claim_model_normal=false`; live GUI
answers should require `can_claim_live_gui_normal=true` and otherwise report
the gate reasons and next action.

### Dopant Metadata Reconciliation

Stale concrete dopant-site metadata is a structural consistency blocker, but
repairing it is still a write: the repair creates a new immutable metadata-only
revision even when `execution_mode="preview"`. Diagnostics therefore return
`needs_user_confirmation=true` and a payload containing
`confirm_metadata_reconciliation=true`. The client must set that field only
after the user explicitly approves the repair.

The confirmation gate is enforced by
`material_studio_project_reconcile_dopant_metadata`,
`material_studio_live_modeling_request`,
`material_studio_live_update_with_patch`, and the lower-level structured patch
tool. A missing confirmation returns
`status=dopant_metadata_reconcile_confirmation_required` without changing
`current.json` or history. The `reconcile_dopant_metadata` operation must be the
only operation in its patch; a structural repair, such as restoring the
declared dopant atom, belongs in a separate normal `SemanticPatch` revision.
When metadata is already consistent, the tools return `already_consistent`
without confirmation and without creating an empty revision.

`material_studio_live_capabilities.diagnostics.diagnostic_focus_profiles`
lists the supported semiconductor diagnostic profiles, their expected summary
paths, CSV artifact keys, and example prompts. Use it to decide whether a
request needs surface, dopant, defect, alloy, electronic-structure, HEMT/2DEG,
quantum-well, contact, gate-stack, band-alignment, or view-quality diagnostics.
When a new-structure request also asks whether a band-gap, band-structure, DOS,
DFT, or CASTEP calculation is ready, keep the request on the create workflow and
attach `electronic_structure_preflight` plus `view_quality` diagnostics. For
example, the Chinese request equivalent to "build silicon crystal, export view
parameters, and check whether band-gap calculation is possible" should create
the `silicon_diamond` template, keep execution in preview unless live loading is
explicit, set the CASTEP task intent to `BandStructure`, and write the view
bundle with calculation-preflight, reciprocal-lattice, band-path, and
view-quality CSV files.
Live create/status responses also echo the selected semiconductor template in
`semiconductor_template_profile` and expose
`recommended_diagnostic_focuses` plus
`unrequested_recommended_diagnostic_focuses`. Use those returned fields when
replying about a built model, because they combine the user's requested
diagnostics with the template's default semiconductor checks.

Supported deterministic new-structure templates include benzene, water, methane, ammonia, carbon dioxide, graphene vacancy, and common monosubstituted benzenes: nitrobenzene, phenol, aniline, and toluene. Substituted benzene templates are generated from the benzene template plus the same functional-group patch logic used for follow-up edits.
The same live entry supports common Chinese requests such as `构建硝基苯并准备预览`, `构建水分子并热加载到 Materials Studio`, `构建硅晶体并热加载到 Materials Studio`, `构建硅晶体并实时热加载到当前界面，导出视角参数并检查模型是否正常`, `构建砷化镓晶体并热加载到 Materials Studio`, `构建二硫化钼单层并热加载到 Materials Studio`, `构建硒化锌晶体并热加载到 Materials Studio`, `构建 2x1x1 硅超胞并掺杂 P，然后热加载到 Materials Studio`, and follow-ups like `把它变成硝基苯`.

## Semiconductor Crystal Templates

For semiconductor materials, `material_studio_live_capabilities` exposes
`domain_focus.semiconductor_template_profiles`. Each profile includes the
template id, example file, natural-language terms, structure family,
material/interface/surface metadata, the execute backend, and default diagnostic
focuses to request when exporting model checks. Current local crystal templates
include Si diamond cubic, Ge diamond cubic, GaAs zinc blende, AlAs zinc
blende, AlP zinc blende, AlSb zinc blende, GaP zinc blende, GaSb zinc blende,
InP zinc blende, InAs zinc blende, InSb zinc blende, 3C-SiC zinc blende,
4H-SiC and 6H-SiC hexagonal bulk, c-BN zinc blende,
ZnO wurtzite, AlN wurtzite, InN wurtzite, CdTe zinc blende, ZnS zinc blende, ZnSe zinc blende, ZnTe zinc blende, CdS zinc blende, CdSe zinc
blende, 2D MoS2 monolayer, GaN wurtzite, a deterministic Si p-n junction start,
and the graphene-vacancy example. Coherent Si/Ge(001) diamond-cubic,
Si/SiO2(100) semiconductor-oxide, Al/SiO2/Si MOS capacitor gate-stack,
TiN/HfO2/Si high-k MOS capacitor gate-stack, Cu/SiO2(100) metal-oxide,
Al/Si(100), metal/ZnO(0001), metal/beta-Ga2O3(010), metal/4H-SiC(0001), and
metal/6H-SiC(0001) Si-face Schottky metal-semiconductor contacts,
GaAs/AlAs(001) zinc-blende, Al0.25Ga0.75N/GaN(0001), AlN/GaN(0001), and
In0.25Ga0.75N/GaN(0001) wurtzite heterostructure templates are available for
interface starts; the III-V and group-IV heterostructures also support
superlattice, quantum-well, and MQW starts.
Current slab starting points include Si(100), GaAs(001), GaN(0001), AlN(0001),
InN(0001), ZnO(0001), and a hydrogen-backed 6H-SiC(0001) Si-face slab. Requests such as
"build silicon crystal", "build a silicon p-n junction", "build GaAs zinc blende", "build AlAs zinc blende", "build AlP zinc blende", "build AlSb zinc blende", "build GaP zinc blende", "build GaSb zinc blende", "build InP zinc blende", "build InAs zinc blende", "build InSb zinc blende", "build GaN wurtzite",
"build AlN wurtzite", "build InN wurtzite", "build 3C-SiC zinc blende",
"build 6H-SiC crystal", "build cubic BN zinc blende", "build silicon carbide", "build ZnO wurtzite", "build CdTe zinc blende",
"build ZnS zinc blende", "build ZnSe zinc blende", "build ZnTe zinc blende", "build CdS zinc blende", "build CdSe zinc blende", "build MoS2 monolayer",
"build Si/Ge heterostructure", "build Si/Ge MQW",
"build a Si/SiO2 MOS interface",
"build an Al/SiO2/Si MOS capacitor",
"build a TiN/HfO2/Si high-k MOS capacitor",
"build a HfO2 high-k gate stack",
"build a MOS capacitor gate stack",
"build a silicon gate oxide interface",
"build a Cu/SiO2 interface",
"build an Al/Si Schottky contact", "build an Au/ZnO Schottky contact",
"build an Au/beta-Ga2O3(010) Schottky contact",
"build an Au/4H-SiC(0001) Si-face Schottky contact",
"build an Au/6H-SiC(0001) Si-face Schottky contact",
"build a metal-semiconductor contact",
"build GaAs/AlAs superlattice", "build GaAs/AlAs quantum well",
"build AlGaN/GaN HEMT heterostructure", "build AlN/GaN HEMT heterostructure", "build InGaN/GaN quantum well",
"build a 3-period AlGaN/GaN superlattice",
"build a 3-period AlN/GaN superlattice",
"build a 3-period GaAs/AlAs MQW",
"build Si(100) surface slab",
"build AlN(0001) surface slab",
"build InN(0001) surface slab",
"build GaAs(001) surface", or
"build a 6H-SiC(0001) Si-face slab", or
"build ZnO(0001) surface slab" can be routed
directly through `material_studio_live_modeling_request`.

The `silicon_carbide_6h_hexagonal` template is a 12-atom P63mc bulk cell with
the ABCACB stacking sequence. Its lattice and special-position coordinates come
from the single-crystal X-ray refinement by Capitani et al.,
[DOI 10.2138/am.2007.2346](https://rruff.geo.arizona.edu/doclib/am/vol92/AM92_403.pdf).
The example metadata records the citation, symmetry expansion, formula units,
reference lattice, and reference average Si-C bond length. Explicit execution
materializes the reviewed bulk spec as CIF and may hot-load it into the current
Materials Studio window.

Two reviewed derived starts are also available. The
`silicon_carbide_6h_0001_si_face_slab` virtual template reorders the cited bulk
cell into a centered `2x2` slab with six C-Si bilayers, a Si-terminated `(0001)`
top face, and four H atoms saturating the C-terminated back face. The
`metal_silicon_carbide_6h_0001_schottky_contact` virtual template adds a top-site
metal layer plus a shifted second visualization layer so contact thickness can
be diagnosed. The six-bilayer, `2x2`, back-H model is grounded in
[Tanaka et al., DOI 10.2320/matertrans.47.2690](https://www.jstage.jst.go.jp/article/matertrans/47/11/47_11_2690/_pdf),
whose reference calculation used one metal monolayer. The generated contact
records that difference and remains an unrelaxed, unreconstructed preflight
scaffold. The 3.85 eV electron affinity and 3.0 eV band gap come from the TCAD
inputs reported by
[Li et al., DOI 10.3390/ma10060583](https://www.mdpi.com/1996-1944/10/6/583);
they are metadata-only screening values, not CASTEP or surface-reconstruction
results. Explicit C-face `(000-1)`, ambiguous unoriented surfaces, 6H-SiC MOS,
and other 6H-SiC interface/device requests remain unsupported and never fall
back to 3C-SiC, 4H-SiC, or silicon.

The metal/beta-Ga2O3(010) scaffold is centered in a vacuum cell and remains an
unrelaxed visualization and diagnostic starting point. Its 4.0 eV electron
affinity and 4.8 eV band gap are metadata-only device-model screening values,
not CASTEP results; the parameter source is the table in
[Chinese Physics B 30 (2021) 027301](https://cpb.iphy.ac.cn/EN/article/downloadArticleFile.do?attachType=PDF&id=123252).
The normality report must keep interface relaxation, k-point review, and any
actual projection overlaps separate from false dopant or surface-passivation
warnings caused by the metal electrode.

The metal/4H-SiC scaffold uses a deterministic `2x2x1` 4H-SiC slab cut so the
contacted `(0001)` surface is Si-terminated and the opposite surface is
C-terminated. It is centered in a vacuum cell and remains an unrelaxed polar
interface scaffold. The 3.60 eV electron affinity and 3.26 eV band gap are
metadata-only screening values from
[Xin et al., Demonstration of the First 4H-SiC EUV Detector with Large Detection Area](https://ntrs.nasa.gov/api/citations/20090022809/downloads/20090022809.pdf),
not CASTEP results. Normality checks must therefore retain interface-relaxation,
surface-polarity, and k-point review warnings while avoiding false dopant flags
for the metal electrode.

Chinese semiconductor aliases cover the same local route for common names such
as `砷化镓`, `磷化镓`, `锑化镓`, `砷化铝`, `磷化铝`, `锑化铝`, `磷化铟`,
`砷化铟`, `锑化铟`, `硫化锌`, `硒化锌`, `碲化锌`, `硫化镉`, `硒化镉`,
`碲化镉`, `锗晶体`, `硅锗`, and 2D TMD names such as `二硫化钼`.
Discovery clients can read these aliases from
`material_studio_live_capabilities.domain_focus.cjk_semiconductor_aliases`.
Surface-capable aliases such as `砷化镓` include a `surface_template_id`, so
phrases with `表面`, `001`, or `slab` can be routed to the matching slab template.
Chinese device and polytype starts such as `构建4H碳化硅晶体并热加载到 Materials Studio`
and `构建硅 MOS 电容并热加载到 Materials Studio` are routed to the 4H-SiC
hexagonal template and the Al/SiO2/Si MOS capacitor gate-stack template,
respectively.
Chinese metal/semiconductor contact starts such as
`构建金属-半导体接触并热加载到 Materials Studio` and `构建金半接触` route to
the Al/Si Schottky contact template; `接触诊断` selects the
`metal_semiconductor_contact` diagnostic focus.

These templates are deterministic `ModelSpec` starting points with CASTEP energy
settings and context-aware multi-view diagnostics. MaterialsScript lattice construction remains
preview-only until local Materials Studio Copy Script output confirms the exact
API, but explicit `execution_mode="execute"` can materialize a CIF artifact and
hot-load that CIF into the Materials Studio GUI.
For bulk semiconductor templates, `modeling_report.inspection.semiconductor_health`
reports composition/formula summaries, nominal valence-electron/charge-balance summaries, calculation-preflight summaries, expected tetrahedral coordination or TMD metal/chalcogen 6/3 coordination, per-element coordination statistics,
neighbor pair counts, unexpected III-V, II-VI, or TMD near-neighbor pair types, layer profiles
along the interface/surface axis, dopant summaries with concentration and donor/acceptor role hints, and vacancy/defect
summaries with concentration, nearest neighbors, under-coordinated neighbor
counts, interstitial coordination outliers, and antisite same-sublattice
neighbors when defect metadata is present.
For 2D TMD templates such as MoS2, follow-up defect and dopant edits can be
routed through the same live patch path. Examples include `create S vacancy`,
`dope S sublattice with Cl`, and `dope with W`; the diagnostics preserve
`tmd_chalcogen` versus `tmd_metal` site families, inherit the substituted
site's expected coordination, and report site-adjusted carrier hints in
`dopant_site_summary`.
Heterostructure
templates also include an epitaxial strain summary derived from the template's
reference-lattice metadata. The Si/SiO2, Al/SiO2/Si MOS capacitor,
TiN/HfO2/Si high-k MOS capacitor, and Cu/SiO2 templates are marked as single
oxide-interface or gate-stack starts, so diagnostics report material sequence
and layer profile without forcing quantum-well or surface-passivation warnings.
The MOS capacitor templates check `Si -> SiO2 -> Al` or `Si -> HfO2 -> TiN`
layer sequences and emit `gate_stack_summary` plus
`semiconductor_gate_stack.csv` with gate/oxide/channel presence, declared
thicknesses, and per-segment layer spans. The Si/SiO2, Al/SiO2/Si, and
TiN/HfO2/Si templates mark mixed oxide or compound gate layers as expected, so
`mixed_interface_layers` is not raised for these idealized setup layers.
MOS/gate-stack follow-ups can adjust layer-center thicknesses through a
structured `set_gate_stack_thickness` patch, for example `set HfO2 thickness to
6 angstrom`, `make SiO2 gate oxide thickness 5 angstrom`, `make TiN gate
thickness 2 angstrom`, `set channel thickness to 8 angstrom`, `把 HfO2 厚度改为
6 埃`, `栅氧厚度设为 5 Å`, `金属栅厚度 2 埃`, or `沟道厚度设为 8 埃`. The patch
rescales the target gate/oxide/channel segment along the interface axis, shifts
upper stack segments to preserve ordering, records `gate_stack_thickness_edits`,
and updates `gate_stack_summary` plus `semiconductor_gate_stack.csv`; GUI tools
should then hot-load or snapshot the resulting revision rather than editing the
stack by blind viewport clicks.
The Al/Si Schottky contact template is treated as a metal/semiconductor contact,
not as a quantum-well stack or an unpassivated slab. It emits
`metal_semiconductor_contact_summary` plus `semiconductor_contact.csv` with
metal/semiconductor roles, contact type, declared gap, metal thickness, channel
thickness, sequence checks, and metadata-only Schottky-Mott barrier preflight
fields. The barrier values are screening metadata, not DFT band-alignment
results.
Heterostructure and MQW starts with reference electronic metadata also emit
`band_alignment_summary` plus `semiconductor_band_alignment.csv`. These fields
estimate conduction/valence offsets from electron affinity and band-gap
metadata so clients can spot likely type-I confinement or review inverted
well/barrier assignments before launching CASTEP. They are quick screening
checks, not quantitative DFT band offsets.
III-nitride wurtzite HEMT-style starts such as AlGaN/GaN and AlN/GaN also emit
`polarization_2deg_summary` plus `semiconductor_polarization_2deg.csv`. This is
a metadata-only spontaneous/piezoelectric polarization and sheet-density
preflight for spotting plausible Al-containing 2DEG barrier candidates; it is
not a self-consistent electrostatic, DFT, or device simulation result.
For formula-alloy follow-ups such as `In0.15Ga0.85N/GaN`, the preflight can
linearly interpolate endpoint reference values for common Si/Ge, III-V,
III-nitride, and II-VI alloys when explicit metadata for that exact composition
is absent.
Slab templates include surface termination
diagnostics with dangling-bond estimates, passivation coverage, and top/bottom
surface polarity/asymmetry checks.
For slab templates, `view_audit.metadata` and
`modeling_report.inspection.surface` expose the surface orientation, surface
axis, slab thickness, vacuum thickness, and termination label for quick checks.

## Follow-Up Modification

When the user asks for a precise atom-level change, Codex should load the current project, build a `SemanticPatch`, and call `material_studio_live_modeling_request` or `material_studio_live_update_with_patch` with `execution_mode=preview` unless live hot-loading was explicitly requested.

For conversation-style follow-ups such as "turn it into nitrobenzene", `material_studio_live_modeling_request` can infer the latest current project in the workspace when `project_id` is omitted. The response includes `project_resolution` so clients can show whether the project was explicit or resolved from the latest `current.json`. If the only visible Materials Studio wrapper belongs to another trusted workspace, this implicit resolution is blocked before any revision write. The response returns `workspace_context_mismatch=true`, the visible wrapper identity, and a `recommended_working_dir`; rerun preflight there and provide the visible `project_id` explicitly rather than silently changing workspace context.

Explicit execution of a persisted revision is serialized. Inspect
`execution_transaction` to confirm the immutable revision, backend, and whether
that revision remained current through the run. A
`status=revision_execution_busy` response means another request owns that exact
revision's execution lock and this request did not start a runner. A
`status=current_revision_execution_block` response means current advanced while
the request waited; refresh through the returned status retry payload and apply
the user's intent to the new current revision. Never bypass either response by
launching an untracked second MaterialsScript job.

For long-running execution monitoring, read
`material_studio_live_project_status.execution_runtime`; do not infer activity
from the presence of `revision_execution.lock`. Continue polling when status is
`running`, `running_unrecorded`, or `transitioning`. Treat
`running_identity_mismatch`, `failed`, `interrupted`, `history_invalid`,
`identity_mismatch`, and `result_missing` as review gates. The returned
continuation always keeps `automatic_retry_allowed=false`; a retry must preserve
the journal and come from explicit execution intent after logs and artifacts
have been reviewed.

Explicit GUI-view continuation phrases such as `continue the next GUI view
replay`, `resume view replay`, `继续视角回放`, and `继续验证下一个 GUI 视角` route to
`workflow=continue_view_replay`. This path reads or prepares the current
revision's replay manifest, returns `replay_continuation`, and creates no model
revision. It never performs GUI input itself: clients must first require
`automatic_replay_ready=true` and inspect the selected view's
`execution_recipe`; review-gated views must remain unexecuted until an
authoritative camera backend is available. On MS 20.1, all six face-aligned
orthographic views have verified Reset/45-degree unmodified arrow recipes, and
isometric has a verified staged 45/35.26438968-degree recipe with mandatory
45-degree restoration and Screen-factor verification. The manifest is the
source of truth for each exact sequence and signed axis layout. Keyboard
events must persist `modifier_keys=[]`; Shift+arrow is a structure-editing
operation and is never a camera replay action.

When the returned isometric recipe is automation-ready, the optional local
`material_studio_gui_execute_view_replay` path may execute it after explicit
confirmation. It uses exact UIA Reset/Movement invocation, ValuePattern-only
angle changes, screen-factor and disabled-nudge readback, closes Movement before
viewport input, and restores 45 degrees before returning. This mechanical
success still does not establish model visibility or camera correctness; record
those only after reviewing the fresh workspace screenshot.

An automation-ready continuation is still pre-action state, not accepted
evidence. Its `execution_action` is the only GUI-input instruction, while its
`post_action_record_payload_template` is non-callable and contains null values
for observations that do not yet exist. After Computer Use completes the exact
recipe, re-query the bound window, capture the viewport, populate the required
observation fields, call the record tool, and re-query status again. A client
must never prefill invocation success or camera-match evidence from the recipe.

For a pending `front`, `back`, `right`, `left`, `top`, or `bottom` view, call
`material_studio_gui_execute_view_replay` with its default preview mode first.
After inspecting the exact window/UIA gate, an explicit
`execution_mode="execute"` performs one deterministic Reset-plus-arrow action
in the already open wrapper. The result remains
`awaiting_visual_confirmation`: inspect the returned BMP with Computer Use or a
human reviewer, fill the null visibility/camera/native-roll observations, and
only then record the replay. A Miller plane/direction recipe that matches the
verified local screenshot-difference/Properties profile follows the same
preview-first tool path and performs a bounded create/View Onto/undo transaction
only after explicit execute intent. Unsupported Miller selection profiles and
other reviewed camera backends remain on their Computer Use paths.

For crystal standard views, do not compare the screenshot against the audit's
analytic in-plane basis as an exact equality test. Confirm the requested view
direction, observe the Materials Studio native in-plane roll, capture a fresh
workspace screenshot, and submit `crystal_camera_evidence`. Both
`view_direction_matches_manifest` and `native_in_plane_roll_observed` must be
true; `analytic_in_plane_basis_matches_manifest` is required but may be false or
null. After a recipe-schema upgrade, an old accepted event stays in history but
the view remains pending while
`current_camera_evidence_reverification_view_names` contains it.

After all prepared views have been reviewed, read
`trusted_clean_view_replay` from live status before reporting the current GUI
model as visually normal. `ok=true` means the current revision has a complete,
integrity-verified, journal-consistent replay for the exact diagnostic view set,
including the recommended clean view and every manual-review view. The normality
gate then moves only the allowlisted nonblocking visual reasons into
`resolved_visual_review_reasons`; it preserves them as visual notes and leaves
unknown visual reasons unresolved. Structural and semiconductor trust gates and
`ready_for_calculation` are unchanged. For example, a TMD model can become
`can_claim_live_gui_normal=true` while an unconfirmed reciprocal-lattice k-point
recommendation still keeps `ready_for_calculation=false`.
The exported `modeling_report_summary.csv` carries the same replay status,
binding/view-set checks, integrity and journal states, trusted clean view names,
and resolved versus unresolved visual-review reasons so the decision remains
auditable outside the in-band MCP response.
When a later view-bundle export omits `views`, it preserves the current
revision's bound replay view set. If no bound replay exists, it preserves the
current valid persisted audit selection before using domain defaults. Read
`diagnostic_export_view_resolution` for this source; supplying an explicit
`views` list intentionally replaces the set and may require fresh replay
evidence.

When the next automatic view is `crystal_plane_*`, the recipe instead uses a
temporary Miller Plane, a verified semantic selection profile, Properties
Explorer verification, and the named View Onto command. Object Tree before/after
leaf selection remains valid only when that explorer is exposed. On the verified
MS 20.1 path, fresh before/after screenshots must isolate one unique new plane
region; a no-modifier selection must then make Properties Explorer report
`Filter=Miller Plane` and the exact label. Project Explorer is not accepted as
Object Tree. A successful record requires `miller_plane_evidence` showing one
created/selected plane, fresh screenshot-derived selection, a pre-cleanup
screenshot, unchanged structure SHA-256, a captured pre-action viewport with
no Reset, live numeric View Onto mapping, exactly `Undo View Onto Miller Plane`
then `Undo Create Miller Plane`, no remaining temporary plane, a clean document,
and pixel-identical restoration of the pre-action view. The local transactional
executor can produce this mechanical evidence only after explicit execute
intent; visual acceptance remains separate. Its camera contract verifies the reciprocal-plane
normal and the MS native smallest-acute-angle roll separately; it does not assert exact analytic
up/right agreement. A `crystal_*` lattice-direction view is automatic-ready
only when its direct-space vector has an exact bounded integer reciprocal-plane
normal mapping. Use the recipe's mapped Miller indices and require
`direct_lattice_direction_matches_manifest=true`; never infer `(hkl)` directly
from `[uvw]`. Non-collinear directions remain review-gated.
Patch and live status responses include `revision_delta` and
`modeling_report.revision_delta`. Use it to show the user what changed before
or after a GUI hot-load: atom and element count deltas, added/deleted/moved or
substituted atoms, bond changes, lattice changes, simulation setting changes,
and metadata changes. When `modeling_report.change_validation` is available,
check it before claiming the current model reflects the requested edit; it
compares the delta with the current view audit and sets `ok=false` when an
added atom is missing, a deleted atom is still present, a substituted or moved
atom does not match, or final atom/element/bond/lattice counts disagree.
Use `modeling_report.change_receipt` as the short response receipt after a
natural-language create or follow-up edit. It includes the request, base/new
revision, compact delta, GUI current-revision state, formula, dopants, strain,
readiness, and review reasons.
If `current.json` is missing or malformed, latest-project resolution falls
back read-only to the newest valid immutable revision. Inspect
`project_resolution.current_pointer` and the top-level `current` receipt:
`recovery_used=true` means the damaged pointer was preserved for audit and no
revision was created. A later explicitly requested successful revision write
uses an unused revision number above every existing revision file and
atomically replaces the pointer; it never overwrites an orphan revision.
Patch and rollback writes also recheck the current revision and the exact
prepared new revision under the project state lock. If another request advanced
the project, `project_revision_conflict` requires rebuilding against current
state. If an orphan file changes the next safe revision number,
`project_revision_allocation_conflict` requires regenerating the revision-bound
script and outputs. Neither conflict executes Materials Studio or mutates the
current pointer/history.

When resuming a session, `material_studio_live_project_status` preserves the
latest `persisted_change_receipt` and `latest_change` summary from history and
report files.
Read `modeling_report.next_action_plan` first for the structured next tool
call, payload hints, confirmation requirement, and key artifact paths. Then use
`modeling_report.live_readiness` to understand whether the current revision can
be hot-loaded, edited further, used for calculation, or needs blocking/review
reasons inspected.
Read `modeling_report.acceptance_review` when the spec includes acceptance
criteria. It checks maximum accepted health warnings and required convergence
evidence. Failed criteria appear in `live_readiness.review_reasons` and
`live_readiness.calculation_blocking_reasons`; they block calculation/trust
claims, but do not block live editing when the structure and GUI state are
otherwise usable.
For semiconductor follow-ups, also read
`modeling_report.semiconductor_review`. It is the compact summary for formula,
lattice, CASTEP/k-point/band-path readiness, dopant/alloy/defect/interface or
surface state, risk flags, and the recommended next action.
For live visual checks, read `modeling_report.view_review` before opening the
projection CSVs. It summarizes supported views, projection atom-count
consistency, overlap or warning views, best view candidates, GUI visual
validation, critical flags, and the recommended next action.

Supported local natural-language patch patterns include deleting an atom, substituting an atom with an element, moving an atom to explicit Cartesian coordinates, adding an atom at explicit Cartesian coordinates, adding/deleting a bond between explicit atom IDs, changing an existing bond type, and replacing a bonded site with nitro, hydroxyl, amino, or methyl groups. More complex chemistry should be translated into a reviewed multi-operation `SemanticPatch`.
Chinese follow-up commands are supported for the same precise cases, for example `删除 H1`, `把 H1 换成 N`, `将 H1 移动到 2.6, 0, 0`, `在 0 0 1.5 添加 H 并连接到 C1`, `删除 C1-C2 键`, and `把 C1-C2 改成双键`.

For crystal current projects, the same live entry can infer semiconductor-style
patches: `make 2x2x1 supercell`, `make a 3-period superlattice`, `build a 3-period GaAs/AlAs MQW`, `create vacancy at Si1`, `create a Si vacancy`, `dope Si2 with P`, `dope with P`,
`apply 2% tensile strain in-plane`, `apply 1% strain along c`,
`dope 6.25% P`, `replace 25% Si with P dopants`,
`make 25% Ge alloy`, `replace 25% Si with Ge`,
`set HfO2 thickness to 6 angstrom`, `make TiN gate thickness 2 angstrom`,
`build an Al/Si Schottky contact`,
`add 10 angstrom vacuum along z`, `hydrogen passivate the top surface`,
`hydrogen passivate both surfaces`, `fully hydrogen passivate both surfaces`,
`add Si interstitial at fractional 0.5 0.5 0.5`,
`create As antisite at Ga1`,
`add Htop1 H at fractional 0.5 0.5 0.24`, and `move Htop1 to fractional 0.5 0.5 0.28`.
Chinese supercell follow-ups accept common matrix separators, for example
`做 2x2x1 超胞并热加载到 Materials Studio` and
`做 2×2×1 超胞并热加载到 Materials Studio`.
For new semiconductor starts, these inline modifiers can be composed in a
single request. After an inline supercell, omitting the exact site allows
deterministic auto-site selection, for example `Build silicon crystal as a
2x1x1 supercell and dope with P` or `Build silicon crystal as a 2x1x1
supercell and create a Si vacancy`. If a site is given after the inline
supercell, use the post-supercell ID such as `Si1_000`; original IDs such as
`Si1` are rejected to avoid silently targeting the wrong image.
Formula-style semiconductor alloy starts are also supported for common Group-IV,
III-V, and II-VI alloys, for example `Build SiGe alloy x=0.25 as a 2x1x1
supercell`, `Build Si0.75Ge0.25 alloy as a 2x1x1 supercell`, and
`Build Al0.25Ga0.75As as a 2x2x1 supercell`, or
`Build InGaAs alloy x=0.25 as a 2x2x1 supercell`. III-nitride starts such as
`Build AlGaN alloy x=0.25 as a 2x2x1 supercell` and
`Build In0.25Ga0.75N as a 2x2x1 supercell` use the GaN wurtzite template.
II-VI formula starts such as `Build Cd0.25Zn0.75Te alloy as a 2x2x1 supercell`,
`Build ZnS0.5Se0.5 alloy as a 2x1x1 supercell`, and
`Build ZnSe0.5Te0.5 alloy as a 2x1x1 supercell` use zinc-blende II-VI
templates and keep the alloy on the cation or anion sublattice indicated by
the formula. Hybrid halide perovskite formula starts such as
`Build MAPb(I0.67Br0.33)3 alloy and export alloy diagnostics` use the MAPbI3
template and replace the requested fraction of I sites with Br/Cl/F. Follow-up
composition edits such as `Replace 33% I with Br in MAPbI3 and export alloy
diagnostics` use the same structured alloy patch path without double-counting
the request as a dopant fraction unless dopant/doping or p/n-type wording is
explicit.
Chinese equivalents such as `将 MAPbI3 中 33% 碘替换为溴并导出合金诊断`
and `把 MAPbI3 中 33% I 换成 Br 并导出合金诊断` are routed through the same
halide-alloy path.
These requests reuse the
structured alloy patch path, record `formula_alloy_request`, and still export
`semiconductor_alloy.csv` plus composition and local-environment diagnostics.
Chinese semiconductor follow-ups use the same structured patch path, for example
`创建硅空位`, `沿 z 添加 10 埃真空层`,
`在分数坐标 0.5 0.5 0.24 添加 Htop1 H`,
`将 Htop1 移动到分数坐标 0.5 0.5 0.28 并热加载到 Materials Studio`,
and `添加 Si 间隙原子到分数坐标 0.5 0.5 0.5`.
Explicit atom-id vacancy follow-ups such as `创建 Ga1 空位并热加载到 Materials Studio`,
`在当前模型中创建 Ga1 空位并热加载到 Materials Studio`, and
`把当前模型的 Ga1 变成空位并热加载到 Materials Studio` resolve against the latest
current project when `project_id` is omitted, create a new revision, and can
hot-load that revision when the request contains live GUI wording.
Explicit atom-id dopant follow-ups such as `用 P 掺杂 Si1_000 并热加载到 Materials Studio`,
`将 Si1_000 掺杂为 P 并热加载到 Materials Studio`, and
`在 Si1_000 位点掺杂 P 并热加载到 Materials Studio` use the semiconductor dopant
patch path, record `semiconductor_dopant_sites`, and preserve site-dependent
carrier-role diagnostics.
Chinese dopant-concentration follow-ups are routed to the dopant-fraction
patch path rather than single-site auto doping. Supported examples include
`掺杂浓度为 6.25% P`, `掺杂浓度为 6.25％ P`,
`P 掺杂浓度 6.25%`, and `在硅中掺杂 6.25％ P`; both ASCII `%` and
full-width `％` are accepted.
Explicit atom-id antisite follow-ups such as `在 Ga1 位点创建 As 反位并热加载到 Materials Studio`,
`创建 As 反位于 Ga1 并热加载到 Materials Studio`, and
`把 Ga1 变成 As 反位并热加载到 Materials Studio` use the semiconductor defect
path, record antisite entries in `defect_summary`, and export
`semiconductor_defects.csv`.
Single-host Group-IV carrier shorthand is also supported: `n-type silicon` or
`n型硅` maps to P substitution, and `p-type silicon` or `p型硅` maps to B
substitution. The generated metadata records
`last_semiconductor_carrier_intent`; `carrier_intent_summary` then compares the
requested n/p-type intent with the actual dopant and charge-balance diagnostics.
The view bundle also exports `semiconductor_carrier_intents.csv` for review.
Concrete dopant-site metadata is checked against the current atom table on every
audit. If a recorded dopant atom is missing or its actual element differs from
the recorded dopant element, `dopant_site_summary.metadata_consistent` is false,
the stale record is excluded from carrier-type inference, and model normality is
blocked until the metadata is reconciled and diagnostics are exported again.
Semantic patches reconcile these records without mutating the base revision;
supercell patches expand valid records to the generated atom IDs, while restore
or substitution patches remove records that no longer describe a dopant.
For legacy revisions that are already inconsistent, call
`material_studio_project_reconcile_dopant_metadata`, or use a natural-language
request such as `repair current dopant metadata` or
`修复当前掺杂位点元数据并重新审计`. The tool creates a metadata-only revision,
exports fresh diagnostics, and reports `structure_unchanged` plus
`simulation_unchanged`. A repeated call returns `already_consistent` without
creating an empty revision. Explicit hot-load wording is still required before
the repaired revision is materialized and opened in the existing GUI window.
For crystal execute/hot-load workflows, the generated CIF is parsed back and
compared with the current `CrystalSpec`. The receipt
`structure_artifact_validation` checks atom count, element counts, atom IDs,
per-site elements, periodic fractional coordinates, and all six lattice
parameters. `not_materialized` is non-blocking in preview. An existing
`mismatch`, `missing`, or `parse_failed` artifact blocks normality and
calculation readiness; the recommended remediation is an explicitly confirmed
`material_studio_gui_apply_current_revision` execute call, which rewrites and
hot-loads the same revision rather than creating another revision.
Silicon p-n junction starts are supported as a separate deterministic template
or follow-up patch. Requests such as `build a silicon p-n junction`,
`build a silicon p-n junction and hot-load it in Materials Studio`, or
`make it a p-n junction` create a Si supercell when needed, place B in the
p-side region and P in the n-side region along the a axis, and record
`semiconductor_junctions` plus `last_semiconductor_junction`. The full audit
exports `junction_summary` and `semiconductor_junctions.csv`; the compact report
surfaces this as `semiconductor_review.junction` and
`live_summary.semiconductor_pn_junction_count`.
Chinese live and follow-up requests such as `构建硅 PN 结并热加载到 Materials Studio`
or `把当前模型变成 PN 结，导出视角参数` use the same p/n region dopant path,
not a plain silicon template fallback. When the request also asks for model
normality or view parameters, the response should keep
`diagnostic_export_requested=true`, persist `view_quality.csv`, and include the
PN junction count in the live summary.
For III-V or mixed-host systems, provide the explicit dopant element and target
site or host sublattice. For III-V site-dependent dopants, natural-language
dopant patches understand shorthand such as `Si_Ga`, `Si on Ga site`,
`dope Ga sublattice with Si`, and `dope GaN with Mg`. They record
`semiconductor_dopant_sites`; `dopant_site_summary` distinguishes roles such
as Si on a Ga cation site as donor-like n-type, Si on an As anion site as
acceptor-like p-type, and Mg on Ga/In as acceptor-like p-type for III-nitrides.
For II-VI templates such as ZnO, ZnS, ZnSe, CdS, CdSe, and CdTe, the same site-aware path supports
explicit requests such as `dope O sublattice with N` and `dope Te sublattice
with Cl`, and conservative auto-site selection can choose Zn/Cd cation sites
for lower-valence dopants such as Al or O/S/Se/Te anion sites for higher-valence
dopants such as Cl. `charge_balance_summary` keeps the old average-host
heuristic in `average_host_*` fields but reports `carrier_type_hint` from
`dopant_site_summary` when `carrier_type_hint_source` is `dopant_site_summary`.
The view bundle exports `semiconductor_dopant_sites.csv`.
Vacancy patches record the removed site element and fractional coordinate in
metadata so the audit can report vacancy concentration and the surrounding
under-coordinated atoms. Interstitial patches record the added atom and
fractional coordinate so the audit can report interstitial concentration,
nearest neighbors, and coordination outliers. Antisite patches record the
original site element and substituted element so intentional same-sublattice
neighbors are reported as review warnings instead of accidental structure
failures.
When a vacancy or dopant request omits the exact crystal atom ID, the planner
can choose the first deterministic matching semiconductor site and records that
choice in `metadata.nl_auto_selected_sites`. If the user supplies a site-like
ID, that ID must still resolve exactly; invalid explicit IDs are rejected rather
than silently replaced.
Lattice strain patches update the crystal lattice with `set_lattice`, record
the reference and strained lattice in `metadata.applied_strain`, and export a
`semiconductor_strain.csv` table. Strain above the configured health threshold
is reported as `ready_with_warnings` or `passed_with_warnings` instead of being
treated as fully normal.
Explicit lattice-parameter edits use the same immutable patch path without
pretending the change is strain. Requests such as `set lattice constant a to
5.43 angstrom`, `set lattice parameters a=b=3.189 and c=5.185 angstrom`, or
`把晶格参数 a 和 b 设为 3.189 埃，c 设为 5.185 埃` preserve fractional
coordinates, record the before/after receipt in
`metadata.lattice_parameter_edits`, and re-export the existing semiconductor
lattice, reciprocal-lattice, neighbor, and revision-delta diagnostics. Lengths
default to Angstrom and may be supplied in `nm` when the unit is attached to
the corresponding parameter or axis group; `alpha`, `beta`, and `gamma`
default to degrees. The request must name `lattice constant`, `lattice
parameter`, `cell parameter`, `晶格常数`, `晶格参数`, or `晶胞参数`, so
ordinary layer-thickness, vacuum, and strain text is not reinterpreted.
Chinese strain follow-ups use the same patch path. Supported examples include
`面内拉伸 2％ 应变`, `对 c 轴加 -3% 应变`, `对c轴加 -3％ 应变`, and
`c轴压缩 6％ 应变`; both ASCII `%` and full-width `％` are accepted.
Layer profiles are exported for semiconductor crystals as
`semiconductor_layer_profile.csv`; they group atoms by fractional position along
the interface axis, surface axis, or c axis and report per-layer composition,
axis coordinate, and interlayer spacing.
Explicit lateral layer translations use those same 1-based layer indices.
Requests such as `shift layer 3 by 0.5 angstrom along x`, `shift the top layer
by -0.25 angstrom along y`, or `将第 3 层沿 x 方向平移 0.5 埃` resolve the
target layer to an exact `atom_ids` list before creating the immutable patch.
The `translate_crystal_atoms` operation moves that atom set rigidly along a
lattice vector and wraps fractional coordinates periodically. It records
`metadata.crystal_layer_translations`, exposes
`semiconductor_health.layer_translation_summary`, and exports
`semiconductor_layer_translation.csv` so the target binding, displacement,
and wrapped atom IDs can be audited after hot-loading. Natural-language layer
translation is limited to axes in the interface/surface plane; use the
interface-gap or layer-thickness commands for movement along the profile axis.
Explicit layer rotation and twist requests use the same profile and exact atom
binding. Examples include `twist the top layer by 3 degrees`, `rotate layer 2
by -5 degrees around c`, and `将第 2 层绕 c 轴旋转 5 度并热加载`. The planner
emits one `rotate_crystal_atoms` operation, uses the periodic centroid as the
pivot, performs a Cartesian rigid-body rotation around the profile axis, and
wraps the resulting fractional coordinates. Automatic natural-language
rotation is accepted only when the profile axis is orthogonal to both in-plane
lattice vectors; an axis that would tilt the layer is rejected.

Each accepted rotation records `metadata.crystal_layer_rotations`, exposes
`semiconductor_health.layer_rotation_summary`, and exports
`semiconductor_layer_rotation.csv`. The receipt binds the selected layer and
atom IDs to a post-rotation coordinate SHA-256 so later edits cannot reuse stale
rotation evidence. An arbitrary twist angle is deliberately classified as a
non-commensurate, visual-review-only scaffold. It may be hot-loaded into the
verified single Materials Studio window, but normality and calculation readiness
remain blocked until a commensurate supercell is constructed and geometry
relaxation is completed and re-audited.
Explicit commensurate TMD requests use a separate structured operation,
`make_commensurate_twisted_bilayer`. It accepts a pristine periodic MoS2,
WS2, MoSe2, or WSe2 monolayer and either coprime integer indices
`m > n >= 0` or a target angle. Angle selection is bounded by a 0.1 degree
tolerance and a default 2000-atom cap. For example,
`Build a commensurate twisted MoS2 bilayer with m=2, n=1 and 6.15 angstrom
interlayer distance` produces the exact 21.786789298 degree, 42-atom periodic
cell. The Chinese equivalent `构建 m=2,n=1 的共格扭转双层二硫化钼，层间距
6.15 埃并热加载到 Materials Studio` uses the same path.

The immutable receipt records both integer supercell matrices, theoretical and
signed twist angles, common lattice, per-layer atom-ID hashes, metal-plane
separation, opposing-chalcogen gap, vacuum, and full structure SHA-256.
`semiconductor_health.commensurate_twist_summary` recomputes those invariants
from the current revision, while `semiconductor_commensurate_twist.csv` exports
the compact evidence. The model is periodic and commensurability-verified, so
it does not inherit the arbitrary-rotation `requires_commensurate_supercell`
blocker. It remains a pre-relaxation structure: same-window visual hot-loading
is allowed after normal GUI preflight, but calculation readiness remains false
until a trusted geometry-relaxation result is bound and re-audited.
Lattice summaries are exported as `semiconductor_lattice.csv` and report cell
volume, atom density, volume per non-passivant atom, and slab vacuum fractions
when surface metadata is present.
Neighbor-distance summaries are exported as `semiconductor_neighbor_pairs.csv`
and report expected, unchecked, unexpected, and passivant nearest-neighbor pair
types with min/mean/max distances.
For large-radius tetrahedral binaries such as InN, long same-sublattice
candidates outside the heteropolar first shell are surfaced as
`same_sublattice_cutoff_artifact_pair_count` and unchecked neighbor-distance
rows instead of hard structure errors.
Local-environment summaries are exported as
`semiconductor_local_environment.csv` and report each atom's neighbor shell,
coordination outlier flag, local angle statistics, and tetrahedral-angle
deviation from 109.471221 degrees.
Interface-profile summaries are exported as
`semiconductor_interface_profile.csv` and report layer roles, material segments,
interface transitions, mixed layers, and abrupt-interface flags for
heterostructures and superlattices.
Interface-quality summaries are exported as
`semiconductor_interface_quality.csv` and condense the heterostructure or MQW
layer sequence into expected versus actual material order, period completeness,
linear and periodic interface-transition counts, mixed-layer count, and a
`quality` value such as `complete` or `complete_with_mixed_layers`.
Quantum-well/MQW summaries are exported as
`semiconductor_quantum_well.csv` and report each material segment's period
index, well/barrier role, layer span, and estimated thickness along the
interface axis. The summary also exposes mean well, barrier, and period
thicknesses in `semiconductor_health.quantum_well_summary`.
For alloy wells or barriers, each segment also reports `element_counts`,
`cation_counts`, and `cation_fractions`, plus material-level aggregates such as
`barrier_cation_fractions_by_material`, so the actual finite-cell In/Al/Ga
composition can be checked without opening the full atom table.
Composition summaries are exported as `semiconductor_composition.csv` and report
full/reduced formulas, element counts, atomic fractions, and host/dopant/passivant
roles. Nominal charge-balance summaries are exported as
`semiconductor_charge_balance.csv` and report per-element nominal valence
electron counts, total valence-electron parity, dopant electron deltas, and
carrier-type hints for quick donor/acceptor review. This is a preflight
heuristic and not a substitute for DFT charge-density or Bader analysis.
Calculation-preflight summaries are exported as
`semiconductor_calculation_preflight.csv` and report CASTEP task family/intent,
functional, quality, cutoff energy, k-point mode, k-point separation or grid,
slab surface-normal k-point risks, whether the task can change structure,
whether a prior relaxed structure is expected, execution risk, and static
warnings before expensive runs.
This is not a convergence or accuracy proof.
Reciprocal-lattice preflight summaries are exported as
`semiconductor_reciprocal_lattice.csv` and report real-space axis lengths,
reciprocal-vector lengths, estimated k-point grids from `kpoint_separation`,
actual separations for explicit grids, slab surface-normal sampling warnings,
and a conservative explicit-grid recommendation when the warning is
deterministically repairable. For slabs, the recommendation preserves or
increases the in-plane sampling density and sets the surface-normal count to
one. The corresponding
`semiconductor_calculation_readiness.action_id=apply_recommended_semiconductor_kpoint_grid`
returns a directly callable `material_studio_live_update_with_patch` payload.
Because that payload creates an immutable simulation-only revision, it must be
shown to the user and explicitly confirmed even though execution remains in
preview. Applying it does not change geometry or automatically hot-load the
unchanged structure; re-export the electronic diagnostics and require
`reciprocal_status=ok` before clearing the blocker. This is a structural setup
check, not a replacement for k-point convergence testing.

Natural-language follow-ups such as `Apply the recommended k-point grid`,
`Use the suggested slab-aware k-point settings`, or `应用推荐的 k 点网格` use the
same action through `material_studio_live_modeling_request`. The first call is
read-only with respect to revision state: it returns the exact current
revision, grid, `SemanticPatch`, and a high-level confirmation payload without
creating a revision. A second call with
`confirm_recommended_calculation_settings=true` may create the simulation-only
revision only when the base revision and patch still match the current
diagnostic recommendation. Stale or modified confirmation payloads are
rejected and refreshed. Repeating the request after `reciprocal_status=ok`
returns an idempotent no-op and creates no empty revision. The persisted
`report.json` records the confirmation receipt, geometry invariant,
simulation-setting change, diagnostic re-audit, and postcondition result.

After a Codex or MCP restart, `material_studio_live_project_status` recovers
that receipt only after validating it against the current report envelope and
embedded modeling report, latest history event, base/current immutable specs,
current-revision view-audit fingerprint, and freshly recomputed reciprocal
diagnostics. A successful recovery returns
`recommended_calculation_settings_receipt_recovery.status=validated_and_restored`
and restores the historical confirmation plus current `reciprocal_status` and
postcondition. Corrupt, stale, cross-revision, or diagnostically inconsistent
receipts return `invalid_persisted_remediation_receipt`; they do not restore
confirmation claims, create a revision, or rewrite any persisted artifact.
Band-path preflight summaries are exported as `semiconductor_band_path.csv`
for supported semiconductor families. Diamond-cubic and zinc-blende starts use
a conservative fcc path, and wurtzite starts use a conservative hexagonal path.
These rows are fractional reciprocal coordinates for review before
BandStructure setup; they do not replace Materials Studio/CASTEP setting
inspection or convergence tests.
Finite-size summaries are exported as `semiconductor_finite_size.csv` for
isolated dopant or defect models. They flag small cells and high effective
dopant/defect concentrations so a larger supercell can be considered before
quantitative DFT.
Surface-polarity summaries are exported as
`semiconductor_surface_polarity.csv` and compare top and bottom slab formulas
and passivant-bond counts. They flag polar-looking or asymmetrically passivated
slabs for review before DFT.
Sublattice balance summaries are exported as
`semiconductor_sublattice_balance.csv` and report III-V or II-VI cation/anion counts,
balance deltas, and warnings for obvious stoichiometry breaks.
Superlattice period requests repeat a heterostructure along its interface axis,
record `metadata.applied_superlattice_period`, expose
`semiconductor_health.superlattice_period_summary`, and use the layer profile to
report estimated layers per period.
Dopant fraction patches replace a deterministic subset of a host sublattice,
record requested and actual concentration in `metadata.applied_dopant_fraction`,
and export `semiconductor_dopant_fraction.csv`. The regular dopant summary
still reports total dopant concentration, donor/acceptor role hints, and
coordination statistics.
Alloy fraction patches replace a deterministic subset of a host sublattice,
record requested and actual composition in `metadata.applied_alloy`, and export
`semiconductor_alloy.csv`. Small cells can force composition rounding; a large
rounding gap is surfaced through `modeling_health.checks`.
Chinese alloy-fraction follow-ups are also routed to this path, including
`锗合金比例为 25％`, `Ge 合金比例 25%`,
`在硅中合金化 25％ 锗`, and `硅中加入 25％ 锗形成合金`; both
ASCII `%` and full-width `％` are accepted.
Plain hydrogen passivation is conservative and adds one H per detected surface
atom. Requests that explicitly say `fully`, `complete`, `saturate`, or `all
dangling bonds` estimate each surface atom's missing tetrahedral coordination
and add enough H atoms to drive the slab passivation coverage diagnostic to
100% when the local geometry supports it. Hydrogen passivation adds H atoms by
deterministic fractional offsets along the slab `surface_axis` and updates
surface termination metadata. These remain `SemanticPatch`
operations against the current revision and can omit `project_id` when the
latest project is the intended target. If the request also asks to hot-load the
change, execute mode regenerates the CIF and opens it in the GUI.
Chinese slab follow-ups use the same path, including `氢钝化上下表面`,
`完全氢钝化上下表面`, `氢化上下表面`, and
`用氢饱和所有悬挂键` or `钝化所有悬挂键`. Requests with `所有悬挂键`
default to both slab surfaces unless a specific top or bottom surface is named.

For one-shot new semiconductor crystal requests, the local planner can apply
deterministic inline modifiers after the selected template. For example,
`Build silicon crystal as a 2x1x1 supercell and dope Si1_000 with P, then hot-load it in Materials Studio`
creates the template, expands the supercell, substitutes the explicit
post-supercell site, materializes a CIF, and opens it.
Requests can also combine template creation with strain, for example
`Build silicon crystal and apply 2% tensile strain in-plane`.
For superlattice or MQW starts, the period count can be included inline, for
example `Build a 3-period GaAs/AlAs superlattice` or
`Build a 3-period GaAs/AlAs MQW`; III-nitride period starts such as
`Build a 3-period AlGaN/GaN superlattice` or `Build a 3-period InGaN/GaN MQW`
are also supported.
Explicit III-nitride barrier compositions such as
`Build a 3-period In0.15Ga0.85N/GaN MQW` are mapped to the matching wurtzite
heterostructure template, with `alloy_summary` recording both requested and
actual finite-cell alloy fractions. The material aliases `AlGaN` and `InGaN`
can be used in layer or thickness requests, for example
`Build a 2-period InGaN/GaN MQW with 4 InGaN layers and 8 GaN layers`.
Quantum-well layer counts can also be included for supported Si/Ge and
GaAs/AlAs starts, for example
`Build a 3-period GaAs/AlAs MQW with 8 well layers and 4 barrier layers` or
`Build a Si/Ge quantum well with 8 Si layers and 4 Ge layers`. Compact
semiconductor notation such as `GaAs(8 ML)/AlAs(4 ML) MQW` and
`3-period 8ML GaAs / 4ML AlAs MQW` is also accepted. Counts must be even and
the total well+barrier layer count must preserve the 4-layer periodic motif;
incompatible requests such as 6/4 layers are rejected instead of generating a
visually plausible but structurally wrong periodic model.
Thickness requests are accepted for the same templates, for example
`Build a 2-period GaAs(3 nm)/AlAs(1.5 nm) MQW`. The planner converts requested
well/barrier thicknesses to the closest motif-compatible layer counts, records
the requested and actual thicknesses in `quantum_well_summary`, and surfaces
the thickness error in `modeling_health.checks`.
For doped starts, use an explicit supercell when the requested concentration
needs enough sites, for example
`Build silicon crystal as a 2x1x1 supercell and dope 25% P`.
For alloy starts, use an explicit supercell when the requested composition
needs enough sites, for example
`Build silicon crystal as a 2x1x1 supercell and make 25% Ge alloy`.
When a request creates a supercell and then modifies a site, use post-supercell
IDs such as `Si1_000`; old pre-supercell IDs such as `Si1` are rejected instead
of being guessed. Site-free requests such as `dope with P` or `create a Si
vacancy` are allowed because the auto-selected site is explicitly recorded.

When a natural-language request is unsupported, use the JSON schemas in
`src/material_studio_mcp_server/schemas/` as the contract for a reviewed
structured payload. The unsupported response includes `capabilities_hint` with
the capabilities discovery tool name, supported template IDs, supported patch
command IDs, and schema paths.

## Graphene Vacancy

For a graphene vacancy model, Codex should create a crystal `ModelSpec`, preview the generated script, and export view diagnostics. Explicit execute mode can materialize a CIF artifact for GUI hot-loading; direct MaterialsScript lattice construction remains disabled until local Copy Script output confirms the exact API.

## CASTEP Settings

CASTEP task, cutoff, or k-point changes should be semantic patches that update
simulation settings without rebuilding geometry. Natural-language follow-ups
such as `set CASTEP cutoff to 600 eV and kpoint separation 0.03 for band
structure` are routed to `set_castep_energy`. CASTEP execution must remain
preview-only unless explicitly confirmed; property tasks such as band structure,
band gap setup, DOS, or PDOS require reviewed settings and usually a prior
relaxed structure. Chinese follow-ups such as `计算带隙，设置 k 点间距 0.04`
should route through the same semantic patch path.

For a crystal `ModelSpec`, the primary structured script remains the
preview-only lattice/CIF path. A separate revision-bound
`scripts/rNNN_castep_task.pl` companion imports the planned CIF and contains the
reviewed CASTEP dispatch. Read `calculation_preview` to inspect the task,
settings, validation, script path, and SHA-256 binding. Only
`artifact_status=matched` with `persisted_artifact_trusted=true` proves that the
persisted preview matches the current revision. `execution_mode=execute` on the
crystal workflow still only materializes and optionally hot-loads the CIF;
`structure_materialization_executes_calculation=false` and
`calculation_executed=false` remain authoritative.

The CASTEP renderer follows the locally installed Materials Studio 20.1
MaterialsScript reference. Canonical tasks are `Energy`,
`GeometryOptimization`, `BandStructure`, `DensityOfStates`,
`ProjectedDensityOfStates`, `Optics`, `Phonon`, and `ElasticConstants`.
Band structure, DOS, PDOS, optics, and phonons use the documented property
flags on `Modules->CASTEP->Energy`; geometry optimization and elastic constants
use their dedicated task objects. A custom cutoff emits
`UseCustomEnergyCutoff` plus `EnergyCutoff`. The primary SCF k-point grid uses
either `KPointDerivation=Separation` with `KPointSeparation` or
`KPointDerivation=CustomGrid` with `ParameterA/B/C`. The renderer deliberately
does not treat `kpoint_separation` as a property-grid override.
Common Chinese CASTEP setting phrases are also supported, including
`设置 CASTEP 截断能为 600 eV`,
`计算带隙，平面波截断 520 eV，k点网格 6×6×6`, and
`计算光学性质，截断能 500 eV`. These still create preview-first
`set_castep_energy` patches unless execution is explicitly confirmed.

## Rollback

Use `material_studio_project_rollback` to create a new revision copied from a previous revision. Rollback must not delete historical revisions.
For ongoing live sessions, `material_studio_project_history` and
`material_studio_project_rollback` may omit `project_id`; the response includes
`project_resolution` when the latest current project was selected.
The high-level live entry also accepts session rollback commands. Chinese
requests such as `回退到上一个 revision 并热加载到 Materials Studio，导出视角参数并检查模型是否正常`
should resolve to `workflow=rollback`, create a new revision copied from the
previous revision, hot-load that new revision into Materials Studio, and persist
the requested view bundle. `重做刚才撤销的修改并热加载到 Materials Studio` resolves
to `workflow=redo` when the latest history entry is a rollback. Explicit
revision restores such as `恢复 r000 并热加载到 MS` use the same non-destructive
rollback machinery with `nl_plan.template_id=restore_revision`.
