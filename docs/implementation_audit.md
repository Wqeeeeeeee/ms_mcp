# Implementation Audit

## Current Layout

The main MCP entry point is `material_studio_mcp_server.server:main`, reached through `ms-mcp` and `run_server.py`. The package contains runner detection, subprocess execution, MaterialsScript Perl templates, and MCP tool registration. The `ms_mcp` package is retained as a legacy/local extension package and is not the default entry point.

## Existing Tools

The existing public tools are preserved: status, custom script validation/execution, import/export, structure summary, Forcite geometry optimization, molecule/TNT builders, CASTEP energy script generation, and template listing. Existing input models already use Pydantic with `extra="forbid"`.

## Runner

`MaterialStudioRunner` resolves `RunMatScript.bat` or related launchers from environment variables and common BIOVIA installation paths. It writes Perl scripts into isolated job directories and invokes the local runner through `subprocess.run`.

Live status now separates the runner's process-wide `default_workspace_root` from the preflight request's `request_workspace_root`; downstream workspace-aware actions continue to receive the explicit request workspace. Long-lived servers also expose `material_studio_mcp_runtime_provenance_v1`, which captures a deterministic SHA-256 of all package Python sources when the server imports and compares it with the current tree. A changed or unavailable source snapshot makes live preflight fail closed with `mcp_server_restart_required`; no model, calculation, or GUI action is recommended until a fresh server process reports matching startup/current hashes. Side-effect-capable direct tools independently enforce `material_studio_mcp_direct_runtime_guard_v1` before entering their tool body, so bypassing preflight cannot write a revision or report, invoke the runner, launch or drive the GUI, or execute a calculation from stale code. Recovery status/capability/preflight tools remain available. Restart is intentionally external and never closes or launches Materials Studio.

## Script Generation

`scripts.py` generates Perl MaterialsScript through deterministic string templates and `perl_string` escaping. Existing templates continue to use Materials Studio APIs rather than hand-written `.xsd` XML.

## CASTEP 20.1 Contract

CASTEP generation is task-aware and shared by structured translators and the compatibility `material_studio_castep_energy_script` tool. The local Materials Studio 20.1 scripting reference confirms dedicated `GeometryOptimization` and `ElasticConstants` task objects, property flags on the `Energy` task, `UseCustomEnergyCutoff` plus `EnergyCutoff`, and the `KPointDerivation` forms for separation or custom grids. Unsupported task strings are rejected instead of being emitted as guessed `Task` settings. CASTEP and Forcite specs also constrain their `module` values so a minimal CASTEP simulation cannot be misclassified by the `ModelSpec` union.

The local Materials Studio 20.1 scripting and CASTEP UI help also confirms the read/write `DipoleCorrection` property with exact values `None`, `Non self-consistent`, and `Self-consistent`. The structured schema, semantic patch path, compatibility preview tool, and revision-bound companion renderer share that contract. Non-self-consistent mode is rejected outside the Energy task, and two-dimensional slab diagnostics require the documented 8 angstrom minimum vacuum before verifying an enabled setting. No separate dipole-direction property is emitted because the verified MaterialsScript API does not expose one. The resulting receipt verifies calculation input only; it does not claim a charge density or dipole moment was calculated.

The same local Energy and GeometryOptimization references document `Charge`, `SpinTreatment`, `UseFormalSpin`, `InitialSpin`, and `OptimizeTotalSpin`. These settings are now strict structured fields and render deterministically before cutoff and k-point entries. Spin-only controls require an explicit spin treatment, and incompatible non-polarized or formal-spin combinations are rejected. Diamond NV0 and NV- workflows bind reviewed collinear initial states to these exact fields and compare metadata, expected values, actual simulation values, and generated API settings before execution. This is an input-state contract only; it does not claim the backend converged to a particular charge density, multiplicity, or defect level.

Each crystal revision with a CASTEP simulation persists a deterministic `scripts/rNNN_castep_task.pl` companion that imports that revision's planned CIF and dispatches the reviewed task. Create, patch, rollback, validate, preview, live status, and GUI-apply responses expose a `calculation_preview` receipt with validation, file binding, SHA-256 comparison, and explicit non-execution fields. The companion remains preview-only: crystal execute mode materializes a CIF and does not run CASTEP. A missing or modified companion is reported as `planned_not_persisted`, `read_failed`, or `mismatch` and is never described as a trusted calculation preview. Supported tasks now also expose `execution_handoff`: Energy, BandStructure, DOS, and PDOS route to `material_studio_castep_run_current`, while GeometryOptimization routes to `material_studio_castep_relax_current`. The directly callable preview binds the active workspace and expected source revision; a stale handoff fails before creating a run directory. Optics, Phonon, and ElasticConstants remain companion-preview-only until a dedicated result workflow is implemented.

Explicit geometry optimization is now a separate, narrow workflow exposed as `material_studio_castep_relax_current`. It uses the locally documented `Modules->CASTEP->GeometryOptimization->Run` contract and exports the returned `Structure` and `Report`; tagged JSON carries `TotalEnergy`, `Enthalpy`, and `Converged`. The compatibility companion remains preview-only, while this dedicated tool defaults to preview and executes only when requested explicitly.

Execution is revision-bound and fail-closed. The workflow records an execution attempt, refuses to overwrite prior scripts, input structures, outputs, or reports, rechecks that the source revision is still current after CASTEP returns, and promotes only a converged CIF that preserves atom IDs/elements and passes round-trip validation. Promotion creates a fresh immutable revision. Failed, malformed, superseded, and unconverged results preserve evidence without advancing `current.json`.

The promoted revision records a source/output structure-hash transition. Diagnostics verify the receipt schema, history ordering, project/revision identity, task/backend, convergence, atom identity, script hash, model operation, simulation settings, source hash, and current output hash. Commensurate TMD diagnostics accept relaxed coordinate changes only through a verified fixed-cell transition. The view bundle exports `semiconductor_castep_geometry_optimization.csv`; a subsequent structural edit invalidates the output binding.

For slabs, execution requires `CellOptimization=None`. Asymmetric slabs additionally require self-consistent dipole correction and at least 8 angstrom vacuum. GUI hot-loading probes the one-window policy before starting CASTEP and never launches a new `MatStudio.exe`; only the promoted revision may be opened in the existing window.

Explicit electronic-property execution is exposed separately as `material_studio_castep_run_current`. Its reviewed scope is `Energy`, `BandStructure`, `DensityOfStates`, and `ProjectedDensityOfStates`, all dispatched through the locally documented `Modules->CASTEP->Energy->Run` API. The script exports the returned Structure and Report and records finite scalar values plus the native Chart document name when the selected task requires one. BandStructure, DOS, and PDOS execution additionally requires a current-bound verified geometry-optimization receipt; Energy retains the remaining structural, semiconductor, cutoff, k-point, slab, and dipole gates without that property-specific prerequisite.

Successful electronic execution creates a metadata-only immutable revision. The output CIF must match the source structure, while receipt v2 binds project/revision identity, task, simulation settings, generated script, tagged result, structure, report, safe runner-created native artifacts, the native-output audit JSON, and every derived CSV by SHA-256. Receipt v1 remains readable for existing revisions. Per-task run directories are append-only, concurrent current-revision changes prevent promotion, and artifact paths outside the runner job directory are rejected. Optional GUI loading reuses the one verified existing Materials Studio window and never launches another process.

The Materials Studio 20.1 Energy Results contract does not expose an independent SCF `Converged` boolean. The native `.castep` output is parsed into bounded SCF-cycle, energy, timing, warning, and fatal-marker evidence, but electronic receipts always keep `scientific_convergence_verified=false`. Completing below the configured maximum cycle count is not promoted into a scientific convergence claim.

Native-output audit v2 adds a conservative sampled band-edge summary when one hash-bound `.bands` artifact is available. Each spin component is evaluated against its own recorded Fermi energy. The audit records sampled VBM/CBM states, their native k-point and band indices, the minimum same-k-point Fermi separation, bands that reach or span Fermi, and a comparison against the scalar Materials Studio `BandGap` result. A sampled Fermi crossing forces `sampled_gap_ev=0`; disagreement beyond `max(0.05 eV, 5%)` is recorded as `review_difference`. These values are evidence at the native sampled points only. They do not establish a direct or indirect scientific gap, prove analytic-path equivalence, or clear SCF and k-point review, so `scientific_band_gap_verified` remains false throughout the audit, receipt, compact response, diagnostics, and health checks.

Receipt verification trusts sampled band-edge fields only from a current v2 native audit whose JSON and source artifacts pass immutable hash binding and contract checks. Existing v1 native audits remain verifiable for historical revisions but cannot supply sampled band-edge evidence; a v1 payload that carries such fields is rejected. This preserves prior results without allowing unvalidated new semantics to enter through the legacy schema.

Electronic-result diagnostics now derive a revision-bound `material_studio_castep_electronic_result_assessment_v1` object. It distinguishes artifact evidence from scientific claims, keeps `scientific_convergence_verified`, `scientific_band_gap_verified`, and `scientific_result_verified` false, and classifies every unresolved item as calculation-result review rather than a structure-normality blocker. Its follow-up tool is `material_studio_castep_run_current`, but the generated payload is always preview-safe and execute still requires explicit confirmation.

The `castep_electronic_results` diagnostic focus exposes the bound receipt, assessment, semiconductor review, and calculation-readiness summaries. View bundles promote both `semiconductor_castep_electronic_result.csv` and `semiconductor_castep_band_edges.csv` into the modeling report and compact change receipt. The band-edge table has aggregate, per-spin, and individual Fermi-crossing rows with native source path/SHA-256, k-point/spin counts, sampled states, crosscheck status, and assessment provenance. Missing or unbound native evidence leaves the focus incomplete instead of silently claiming a valid result.

`BandStructureChart`, `DOSChart`, and `PartialDOSChart` prove only that a native chart document was returned. Numeric provenance instead comes from the documented native `.bands` format. BandStructure exports the actual k-point coordinates and eigenvalues; DOS additionally exports a deterministic Gaussian total-DOS CSV only for an explicit Smearing method and width. These are not asserted to match an analytic preview path. PDOS remains `numeric_curve_data_exported=false` because the local `.pdos_weights` layout has not been verified. The view bundle exports these decisions, sampled VBM/CBM fields, Fermi-crossing evidence, reported-gap crosscheck, and native audit fields through `semiconductor_castep_electronic_result.csv` without changing structural-health conclusions.

CASTEP parameter-convergence diagnostics now derive `material_studio_castep_convergence_audit_v1` from the current revision's electronic-calculation history. Every point is reloaded from its exact immutable revision, must be that revision's last matching receipt, and must pass the full receipt verifier plus current-structure SHA-256 equality. Missing revisions, duplicate target revisions, receipt drift, changed reports, and artifact/hash failures are retained as binding errors and exclude the point from comparison. Cutoff energy, k-point separation, custom k-point grid, and properties k-point separation are grouped into separate series with all other settings held constant.

Two verified points yield pairwise sensitivity evidence; three are required for a sequence. The audit records adjacent total-energy deltas in eV/atom and reported `BandGap` deltas, using default review tolerances of 0.01 eV/atom and 0.05 eV. A sequence inside those tolerances reports parameter sensitivity only: `scientific_convergence_verified` and `scientific_band_gap_verified` remain false. Incomplete or above-tolerance series may return one deterministic finer-point payload, but it is always `execution_mode="preview"`, disables GUI opening, and requires separate explicit execute intent. The `castep_convergence_series` focus and `semiconductor_castep_convergence_series.csv` expose summary, verified-point, delta, and binding-error rows without converting calculation review into a structural-health failure.

CASTEP geometry-optimization and electronic-result hot-loads now share the structured post-hotload framing and replay-preparation contract. Both direct tools expose `fit_to_view_after_open` and `prepare_view_replay_after_open`; preview returns GUI-inert request receipts. Execute performs Fit-to-View only after the immutable promoted/recorded revision is loaded into the verified single existing window, then releases the GUI artifact/report transaction before preparing the exact result-revision replay manifest. Natural-language CASTEP orchestration injects the view and normality intent before report publication. Replay preparation remains non-mutating, does not create another revision, and does not rewrite the already published modeling report.

## Absorbed DrYe1109/MS-MCP Capabilities

The exact 33-tool comparison and disposition is recorded in
`docs/dr_ye_capability_absorption.md`. This change preserves the structured
revision, execution-attempt, and GUI-evidence architecture while adding eight
public tools, bringing protocol discovery to 49:

- secure preview-first COD search and content-addressed CIF ingestion;
- strict molecule/singlet DMol3 geometry optimization with convergence and
  atom-identity gates before revision promotion;
- immutable, preview/revision/hash-bound remote CASTEP bundle preparation plus a
  hash-linked local journal for externally observed submission/status evidence;
  and
- bounded read-only workspace snapshots/artifact reads shared with a
  loopback-only GET/HEAD dashboard.

The arbitrary shared GUI script queue, caller-written "current document"
state, lockless JSON updates, raw settings maps, SSH shell probes, and
in-place GUI mutations from the compared project were deliberately rejected.
`Frequency`, `BandStructureAndDOS`, `ChargeDensity`, and `DensityDifference`
have reviewed Materials Studio 20.1 property mappings but remain
companion-preview-only until dedicated result validation and promotion
contracts exist.

## No-GUI Acceptance Evidence

- A live fetch of COD `1009001` produced source SHA-256
  `9ee5b49616a5d5cbc03740e36d65b4cff9c8dc8df1bd4530c97aaf7603e88225`.
  The import plan carried that exact `FileRef` digest; generated
  MaterialsScript used `Digest::SHA`, staged `in.cif` with exclusive-create
  semantics, and rehashed the source after import. The real Materials Studio
  20.1 runner produced an XSD successfully.
- A real MS 20.1 DMol3 water optimization used Coarse quality, LDA, and
  `charts=Yes`. It converged and promoted `r000` to `r001` with total energy
  `-47602.3425319718` kcal/mol. The three XSD atom tokens matched with maximum
  coordinate delta `0`, the report passed verification, and the native chart
  documents were `in Energies` and `in Convergence`.
- Public calls for `Frequency`, `BandStructureAndDOS`, `ChargeDensity`, and
  `DensityDifference` were tested only as previews behind fail-fast
  runner/materializer/GUI sentinels. No CASTEP calculation was performed.
- The local remote-handoff lifecycle verified a manifest whose SHA-256 begins
  `c7b493` and a consistent journal. It performed no SSH, scheduler invocation,
  remote polling, or job submission.
- The dashboard was exercised as a source subprocess over loopback read-only
  routes. Bounded workspace reads, method/Host rejection, preview filesystem
  purity, and unchanged workspace state were checked conservatively; packaging
  and wheel results are reported by the release validation rather than inferred
  here.

No acceptance step drove or modified the Materials Studio GUI. New structured
output paths are confined to their assigned calculation/revision directories:
absolute and traversal overrides are rejected before runner execution. The
workspace readers remain bounded and reject links/reparse-point escapes.

## Risks And Gaps

`material_studio_run_script` remains intentionally powerful and risky because it executes arbitrary user-supplied Perl. Structured tools now default to preview and validate generated scripts before execution. Crystal lattice construction remains conservative because local Copy Script output should be trusted over guessed API calls.

CASTEP remains an external licensed calculation backend. Unit and protocol tests use fake runners, strict native-file fixtures, and preview calls; they prove transaction, parsing, result recording, derived-file binding, diagnostics, and single-window gating without proving a particular local pseudopotential, queue, license, production band/DOS result, or scientific convergence setup. A read-only installed `.castep` example validates the parser shape, but this increment does not run CASTEP. The licensed execution evidence above is DMol3 only and must not be generalized into a CASTEP result claim.

## Migration

New structured workflow modules live under `material_studio_mcp_server/specs`, `state`, `validators`, `translators`, and `parsers`. They are additive and do not remove existing public tools.
