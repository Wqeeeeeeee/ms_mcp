# Implementation Audit

## Current Layout

The main MCP entry point is `material_studio_mcp_server.server:main`, reached through `ms-mcp` and `run_server.py`. The package contains runner detection, subprocess execution, MaterialsScript Perl templates, and MCP tool registration. The `ms_mcp` package is retained as a legacy/local extension package and is not the default entry point.

## Existing Tools

The existing public tools are preserved: status, custom script validation/execution, import/export, structure summary, Forcite geometry optimization, molecule/TNT builders, CASTEP energy script generation, and template listing. Existing input models already use Pydantic with `extra="forbid"`.

## Runner

`MaterialStudioRunner` resolves `RunMatScript.bat` or related launchers from environment variables and common BIOVIA installation paths. It writes Perl scripts into isolated job directories and invokes the local runner through `subprocess.run`.

## Script Generation

`scripts.py` generates Perl MaterialsScript through deterministic string templates and `perl_string` escaping. Existing templates continue to use Materials Studio APIs rather than hand-written `.xsd` XML.

## CASTEP 20.1 Contract

CASTEP generation is task-aware and shared by structured translators and the compatibility `material_studio_castep_energy_script` tool. The local Materials Studio 20.1 scripting reference confirms dedicated `GeometryOptimization` and `ElasticConstants` task objects, property flags on the `Energy` task, `UseCustomEnergyCutoff` plus `EnergyCutoff`, and the `KPointDerivation` forms for separation or custom grids. Unsupported task strings are rejected instead of being emitted as guessed `Task` settings. CASTEP and Forcite specs also constrain their `module` values so a minimal CASTEP simulation cannot be misclassified by the `ModelSpec` union.

The local Materials Studio 20.1 scripting and CASTEP UI help also confirms the read/write `DipoleCorrection` property with exact values `None`, `Non self-consistent`, and `Self-consistent`. The structured schema, semantic patch path, compatibility preview tool, and revision-bound companion renderer share that contract. Non-self-consistent mode is rejected outside the Energy task, and two-dimensional slab diagnostics require the documented 8 angstrom minimum vacuum before verifying an enabled setting. No separate dipole-direction property is emitted because the verified MaterialsScript API does not expose one. The resulting receipt verifies calculation input only; it does not claim a charge density or dipole moment was calculated.

Each crystal revision with a CASTEP simulation persists a deterministic `scripts/rNNN_castep_task.pl` companion that imports that revision's planned CIF and dispatches the reviewed task. Create, patch, rollback, validate, preview, live status, and GUI-apply responses expose a `calculation_preview` receipt with validation, file binding, SHA-256 comparison, and explicit non-execution fields. The companion remains preview-only: crystal execute mode materializes a CIF and does not run CASTEP. A missing or modified companion is reported as `planned_not_persisted`, `read_failed`, or `mismatch` and is never described as a trusted calculation preview.

Explicit geometry optimization is now a separate, narrow workflow exposed as `material_studio_castep_relax_current`. It uses the locally documented `Modules->CASTEP->GeometryOptimization->Run` contract and exports the returned `Structure` and `Report`; tagged JSON carries `TotalEnergy`, `Enthalpy`, and `Converged`. The compatibility companion remains preview-only, while this dedicated tool defaults to preview and executes only when requested explicitly.

Execution is revision-bound and fail-closed. The workflow records an execution attempt, refuses to overwrite prior scripts, input structures, outputs, or reports, rechecks that the source revision is still current after CASTEP returns, and promotes only a converged CIF that preserves atom IDs/elements and passes round-trip validation. Promotion creates a fresh immutable revision. Failed, malformed, superseded, and unconverged results preserve evidence without advancing `current.json`.

The promoted revision records a source/output structure-hash transition. Diagnostics verify the receipt schema, history ordering, project/revision identity, task/backend, convergence, atom identity, script hash, model operation, simulation settings, source hash, and current output hash. Commensurate TMD diagnostics accept relaxed coordinate changes only through a verified fixed-cell transition. The view bundle exports `semiconductor_castep_geometry_optimization.csv`; a subsequent structural edit invalidates the output binding.

For slabs, execution requires `CellOptimization=None`. Asymmetric slabs additionally require self-consistent dipole correction and at least 8 angstrom vacuum. GUI hot-loading probes the one-window policy before starting CASTEP and never launches a new `MatStudio.exe`; only the promoted revision may be opened in the existing window.

Explicit electronic-property execution is exposed separately as `material_studio_castep_run_current`. Its reviewed scope is `Energy`, `BandStructure`, `DensityOfStates`, and `ProjectedDensityOfStates`, all dispatched through the locally documented `Modules->CASTEP->Energy->Run` API. The script exports the returned Structure and Report and records finite scalar values plus the native Chart document name when the selected task requires one. BandStructure, DOS, and PDOS execution additionally requires a current-bound verified geometry-optimization receipt; Energy retains the remaining structural, semiconductor, cutoff, k-point, slab, and dipole gates without that property-specific prerequisite.

Successful electronic execution creates a metadata-only immutable revision. The output CIF must match the source structure, while receipt v2 binds project/revision identity, task, simulation settings, generated script, tagged result, structure, report, safe runner-created native artifacts, the native-output audit JSON, and every derived CSV by SHA-256. Receipt v1 remains readable for existing revisions. Per-task run directories are append-only, concurrent current-revision changes prevent promotion, and artifact paths outside the runner job directory are rejected. Optional GUI loading reuses the one verified existing Materials Studio window and never launches another process.

The Materials Studio 20.1 Energy Results contract does not expose an independent SCF `Converged` boolean. The native `.castep` output is parsed into bounded SCF-cycle, energy, timing, warning, and fatal-marker evidence, but electronic receipts always keep `scientific_convergence_verified=false`. Completing below the configured maximum cycle count is not promoted into a scientific convergence claim.

Native-output audit v2 adds a conservative sampled band-edge summary when one hash-bound `.bands` artifact is available. Each spin component is evaluated against its own recorded Fermi energy. The audit records sampled VBM/CBM states, their native k-point and band indices, the minimum same-k-point Fermi separation, bands that reach or span Fermi, and a comparison against the scalar Materials Studio `BandGap` result. A sampled Fermi crossing forces `sampled_gap_ev=0`; disagreement beyond `max(0.05 eV, 5%)` is recorded as `review_difference`. These values are evidence at the native sampled points only. They do not establish a direct or indirect scientific gap, prove analytic-path equivalence, or clear SCF and k-point review, so `scientific_band_gap_verified` remains false throughout the audit, receipt, compact response, diagnostics, and health checks.

Receipt verification trusts sampled band-edge fields only from a current v2 native audit whose JSON and source artifacts pass immutable hash binding and contract checks. Existing v1 native audits remain verifiable for historical revisions but cannot supply sampled band-edge evidence; a v1 payload that carries such fields is rejected. This preserves prior results without allowing unvalidated new semantics to enter through the legacy schema.

`BandStructureChart`, `DOSChart`, and `PartialDOSChart` prove only that a native chart document was returned. Numeric provenance instead comes from the documented native `.bands` format. BandStructure exports the actual k-point coordinates and eigenvalues; DOS additionally exports a deterministic Gaussian total-DOS CSV only for an explicit Smearing method and width. These are not asserted to match an analytic preview path. PDOS remains `numeric_curve_data_exported=false` because the local `.pdos_weights` layout has not been verified. The view bundle exports these decisions, sampled VBM/CBM fields, Fermi-crossing evidence, reported-gap crosscheck, and native audit fields through `semiconductor_castep_electronic_result.csv` without changing structural-health conclusions.

## Risks And Gaps

`material_studio_run_script` remains intentionally powerful and risky because it executes arbitrary user-supplied Perl. Structured tools now default to preview and validate generated scripts before execution. Crystal lattice construction remains conservative because local Copy Script output should be trusted over guessed API calls.

CASTEP remains an external licensed calculation backend. Unit and protocol tests use fake runners, strict native-file fixtures, and preview calls; they prove transaction, parsing, result recording, derived-file binding, diagnostics, and single-window gating without proving a particular local pseudopotential, queue, license, production band/DOS result, or scientific convergence setup. A read-only installed `.castep` example validates the parser shape, but this increment does not run CASTEP.

## Migration

New structured workflow modules live under `material_studio_mcp_server/specs`, `state`, `validators`, `translators`, and `parsers`. They are additive and do not remove existing public tools.
