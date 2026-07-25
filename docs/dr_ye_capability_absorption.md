# DrYe1109/MS-MCP capability absorption matrix

## Audit basis

- External repository: `DrYe1109/MS-MCP` at
  `991a1b3ab2ad985529fb645dc82f47528a2a1297`.
- Local baseline: `Wqeeeeeeee/ms_mcp` `origin/main` at
  `005d7594ee771769a0e39be4b29ce23400e0760b`.
- Scope: all 33 external MCP tools plus Dashboard, installation, and remote
  CASTEP support.
- Decision vocabulary:
  - `existing`: the local public surface already provides the capability.
  - `equivalent`: the local revision-oriented workflow provides the same
    outcome through a different contract.
  - `partial`: a safe subset exists, but a useful reviewed subset is missing.
  - `net-new`: no local public capability currently provides the outcome.
  - `reject-security-pattern`: do not copy the external mechanism; retain or
    build the outcome only through structured revisions and evidence.

The external tool count is derived from the 33 `server.tool(...)`
registrations in `src/index.js:935-3159`. The local public MCP surface is
defined in `src/material_studio_mcp_server/server.py:1677-56227`; the
absorption branch exposes 49 tools, including the eight public tools added by
this change.

## Exact 33-tool matrix

| # | External tool and evidence | Category | Local target or equivalent | Security decision | Executable acceptance criterion |
|---:|---|---|---|---|---|
| 1 | `ms_status` (`src/index.js:935-940`) | existing | `material_studio_get_status` (`server.py:1677-1707`) plus live/GUI preflight (`server.py:12608-12634`, `54752-54761`) | Keep the richer runtime provenance and read-only config diagnosis. | `pytest -q tests/test_server_tools.py -k status tests/test_runtime_provenance.py` passes and the call performs no runner or GUI action. |
| 2 | `ms_codex_config` (`src/index.js:942-960`) | equivalent | Status exposes config diagnosis (`server.py:1697-1706`); guarded snippet/diagnosis lives in `codex_config.py:110-248`. | Do not add a second unguarded config writer. | `pytest -q tests/test_codex_config_doctor.py tests/test_codex_config_registration.py` passes; preview leaves the active config byte-identical. |
| 3 | `ms_run_materialscript` (`src/index.js:962-985`) | existing | `material_studio_validate_script` and `material_studio_run_script` (`server.py:12637-12743`). | Preserve explicit destructive annotation, dry-run, validation, and current-runtime-source gate. | `pytest -q tests/test_server_tools.py -k 'validate_script or run_script'` passes; dry-run creates no job and execute is rejected for stale runtime source. |
| 4 | `ms_enqueue_materialscript` (`src/index.js:987-1010`) | reject-security-pattern | Structured preview/execute plus exact GUI hot-load (`server.py:44086-44295`, `56216-56227`). | Reject arbitrary `.pl` queue ingestion. The external loop executes pending files with `do` (`materialscript/mcp_loop_gui.pl:65-104`) without project/revision/window identity. | A security test must prove no public structured workflow creates `.mcp-queue/pending/*.pl`; arbitrary code remains confined to the separately destructive direct script tool. |
| 5 | `ms_queue_status` (`src/index.js:1012-1026`) | reject-security-pattern | Revision execution status and hash-linked attempts are exposed through `material_studio_live_project_status` (`server.py:44327-45903`) and `state/execution.py:589-998`. | Do not introduce a second queue state machine. | `pytest -q tests/test_execution_state.py tests/test_live_watchdog_status.py` passes and reports running/completed/interrupted from the authoritative attempt journal. |
| 6 | `ms_gui_state` (`src/index.js:1028-1033`) | equivalent | Current revision, project history, execution state, and exact GUI wrapper are separate receipts (`server.py:44297-44326`, `44327-45903`, `54752-54761`). | Do not collapse trusted revision state and observed GUI state into one mutable JSON file. | `pytest -q tests/test_structured_server_tools.py -k 'history or current' tests/test_gui_server_tools.py -k gui_status` passes with matching project/revision bindings. |
| 7 | `ms_gui_project_dir` (`src/index.js:1035-1049`) | equivalent | `ProjectStore.project_dir` and revision output directories (`state/store.py:295-311`, `838-866`). | Paths remain project-id sanitized and workspace confined. | `pytest -q tests/test_state_store_structured.py -k 'project_dir or outputs'` passes, including traversal rejection. |
| 8 | `ms_gui_start_project_session` (`src/index.js:1051-1094`) | equivalent | Creating a `ModelSpec` creates a serialized project/revision (`server.py:44086-44166`; `state/store.py:563-695`). | Do not create free-floating date folders or reset history outside the project transaction. | `pytest -q tests/test_structured_server_tools.py -k create_from_spec tests/test_state_store_structured.py -k create_project` passes and a second project cannot overwrite the first. |
| 9 | `ms_gui_set_current_document` (`src/index.js:1096-1119`) | reject-security-pattern | Exact current revision and GUI target are discovered, not asserted (`server.py:44327-45903`, `54752-54761`). | Reject caller-written "current document" state without live wrapper/artifact verification. | A negative test must show an arbitrary document name cannot change current project/revision or satisfy any GUI hot-load gate. |
| 10 | `ms_gui_import_current` (`src/index.js:1121-1164`) | equivalent | Known files use `ImportedStructureSpec` (`specs/project.py:27-38`), import/export (`server.py:12746-12809`), and exact-window open (`server.py:55403-55546`). | Import must bind an immutable source reference/hash before optional GUI open. | `pytest -q tests/test_server_tools.py -k import_export tests/test_gui_server_tools.py -k open_structure` passes; same-window preflight blocks ambiguous GUI sessions. |
| 11 | `ms_gui_download_cif_import_current` (`src/index.js:1166-1216`; downloader `src/index.js:175-219`) | net-new | Implemented secure fetch/provenance core (`cif_sources.py`) and public `material_studio_cif_source_ingest`, which feeds the structured revision workflow. | Absorb HTTPS allowlisting and byte limits, but add redirect revalidation, real CIF parsing, digest/provenance, and immutable storage; never queue GUI code as the download receipt. | `pytest -q tests/test_cif_sources.py tests/test_absorbed_public_tools.py::test_public_cif_search_and_ingest_previews_have_no_side_effects` covers core boundaries and preview purity. A no-GUI live acceptance fetched COD `1009001` as SHA-256 `9ee5b49616a5d5cbc03740e36d65b4cff9c8dc8df1bd4530c97aaf7603e88225`, bound that exact digest in `FileRef`, verified it with `Digest::SHA` before import, created `in.cif` exclusively, rehashed it after import, and produced a real MS 20.1 XSD successfully. |
| 12 | `ms_gui_find_cif_import_current` (`src/index.js:1218-1295`; scoring `src/index.js:245-330`) | net-new | Implemented bounded COD URL/search core and public read-only `material_studio_cif_source_search`; selection remains an explicit follow-up ingest call. | Preserve ambiguity refusal and credential-wall policy; never infer authority to access ICSD/CSD. | `pytest -q tests/test_cif_sources.py -k 'cod or search'` covers canonical URLs, side-effect-free preview, and bounded valid candidates; automatic tied-candidate selection remains deliberately absent. |
| 13 | `ms_gui_new_structure_current` (`src/index.js:1297-1536`) | partial | Structured create/natural-language routes plus explicit COD search/CIF ingest now cover reviewed source selection; automatic candidate choice and credentialed databases remain absent. | Source resolution must return a reviewed `ModelSpec`; do not reuse external queue/current-document state. | Public preview tests prove that search/ingest previews create no revision; the no-GUI COD `1009001` acceptance created a revision only after explicit source selection. Ambiguous automatic selection remains absent. |
| 14 | `ms_gui_create_current` (`src/index.js:1538-1593`) | reject-security-pattern | `material_studio_model_create_from_spec`, validation, and preview (`server.py:44086-44166`, `46514-46607`). | Reject caller-provided Perl bodies that create/replace the live document. | A protocol test must expose the structured create tool but no raw "current document body" tool; invalid specs create neither revision nor GUI input. |
| 15 | `ms_gui_create_crystal_current` (`src/index.js:1595-1707`) | equivalent | `CrystalSpec` (`specs/crystal.py:15-89`) through structured create and CIF materialization. | Keep lattice/atom validation, immutable revision, preview-first execution, and explicit hot-load. | `pytest -q tests/test_crystal_and_validation.py tests/test_translators_structured.py -k crystal` passes and execute materializes a hash-bound CIF without silently running CASTEP. |
| 16 | `ms_gui_set_lattice_current` (`src/index.js:1709-1764`) | equivalent | Semantic `set_lattice` (`specs/patch.py:49-61`, `564-568`) via revision-producing patch (`server.py:44167-44295`). | Never mutate only the GUI document. | `pytest -q tests/test_semantic_patch.py -k set_lattice` passes; old revision is unchanged, new revision reports the lattice delta, and rollback remains possible. |
| 17 | `ms_gui_make_supercell_current` (`src/index.js:1766-1813`) | equivalent | Semantic `make_supercell` (`specs/patch.py:465-488`). | Preserve atom IDs, metadata reconciliation, and atom-count limits rather than delegating state to a GUI queue. | `pytest -q tests/test_semantic_patch.py -k supercell` passes for molecule-independent crystal expansion, dopant metadata, and invalid dimensions. |
| 18 | `ms_gui_add_vacuum_current` (`src/index.js:1815-1886`) | equivalent | `add_vacuum`, `set_vacuum`, and `center_slab` (`specs/patch.py:489-497`). | Retain Cartesian-position preservation and slab metadata checks. | `pytest -q tests/test_semantic_patch.py -k vacuum` passes and proves Cartesian positions, lattice delta, vacuum metadata, and base-revision immutability. |
| 19 | `ms_gui_cleave_surface_vacuum_current` (`src/index.js:1888-1982`) | partial | Reviewed slab templates and vacuum patches exist, but `PatchOperationType` has no generic Miller cleave (`specs/patch.py:35-61`). | Absorb a deterministic `cleave_surface` revision operation only after validating plane, termination, thickness, atom mapping, and MS 20.1 API/offline geometry; reject direct in-place GUI mutation. | Proposed `pytest -q tests/test_surface_cleave.py`; reject `(0,0,0)`, prove requested normal/thickness/vacuum/termination, preserve source revision, support rollback, and gate live MS smoke behind an explicit marker. |
| 20 | `ms_gui_apply_current` (`src/index.js:1984-2032`) | reject-security-pattern | Semantic patches and `material_studio_live_update_with_patch` (`server.py:44167-44295`, `47722-49296`). | The external regex blacklist (`src/index.js:781-790`) is not a safe arbitrary-code boundary. Do not copy it. | Fuzz tests must show shell, network, file deletion, document creation/import/export, and obfuscated raw script inputs cannot enter the structured patch path. |
| 21 | `ms_gui_dmol3_optimize_current` (`src/index.js:2034-2162`) | net-new | Implemented strict DMol3 spec/translator/result promotion and public revision-bound `material_studio_dmol3_relax_current`. | Do not copy `extraSettings`: keys are emitted directly into Perl (`src/index.js:2046`, `2097-2127`). Use reviewed fields/enums only. | `pytest -q tests/test_dmol3_calculation.py tests/test_absorbed_public_tools.py::test_public_dmol3_preview_execute_and_revision_promotion` covers gates, concurrency, and fake-runner promotion. A licensed no-GUI MS 20.1 acceptance optimized water with Coarse/LDA and `charts=Yes`, converged from `r000` to `r001`, reported `-47602.3425319718` kcal/mol, matched all three XSD atom tokens with maximum coordinate delta `0`, verified the report, and returned `in Energies` plus `in Convergence` chart documents. |
| 22 | `ms_gui_forcite_optimize_current` (`src/index.js:2164-2299`) | existing | Compatibility tool (`server.py:12872-12970`) and structured `ForciteOptimizationSpec` (`specs/forcite.py:34-55`). | Keep strict fields; reject the external arbitrary `extraSettings` key interpolation (`src/index.js:2178`, `2238-2266`). | `pytest -q tests/test_server_tools.py -k forcite tests/test_translators_structured.py -k forcite` passes, including dry-run and malicious-field rejection. |
| 23 | `ms_gui_prepare_remote_castep_batch` (`src/index.js:2301-2650`) | net-new | Implemented immutable core `prepare_remote_castep_bundle` plus public `material_studio_remote_castep_prepare`. | No fixed 48-core/Gateway assumptions (`src/index.js:2350`, `2643-2647`), no script queue, no shell/SSH execution, and no arbitrary settings map. | `pytest -q tests/test_remote_handoff.py::test_prepare_preview_is_read_only tests/test_remote_handoff.py::test_execute_requires_the_exact_preview_manifest_and_rejects_input_drift tests/test_remote_handoff.py::test_prepare_execute_publishes_immutable_hash_bound_bundle tests/test_absorbed_public_tools.py::test_public_remote_handoff_lifecycle_is_revision_and_identity_bound` passes; execute must echo the exact preview manifest SHA-256, and manifest binds expected revision/spec/deterministic-script/input hashes. |
| 24 | `ms_remote_castep_record_submission` (`src/index.js:2652-2701`) | net-new | Implemented `record_remote_submission` and public `material_studio_remote_job_record` over a hash-linked event journal. | Submission recording is evidence, not proof that a scheduler accepted work; require manifest hash and structured scheduler/job ids. | `pytest -q tests/test_remote_handoff.py::test_record_submission_requires_exact_identity_and_manifest tests/test_remote_handoff.py::test_conflicting_submission_is_rejected` passes; the append-only event chain verifies. |
| 25 | `ms_remote_castep_batch_status` (`src/index.js:2703-2743`) | net-new | Implemented `record_remote_status`, local-only `read_remote_job_status`, and public record/status MCP wrappers. | No remote command is run; status cannot infer queued/running from filenames alone. | `pytest -q tests/test_remote_handoff.py::test_status_is_local_read_only_and_identity_bound tests/test_remote_handoff.py::test_status_rejects_tampered_event_chain` passes and before/after filesystem snapshots are identical. |
| 26 | `ms_gui_castep_current` (`src/index.js:2745-2871`) | partial | Reviewed mappings now include `Frequency`, `BandStructureAndDOS`, `ChargeDensity`, and `DensityDifference`; these four are deliberately preview-only while existing result-audited tasks retain their execution tools. | Add only MS 20.1-verified presets and result artifacts; never accept arbitrary setting keys. | `pytest -q tests/test_castep_extended_presets.py` proves deterministic property mappings, aliases, and preview-only gates; structured execute/result promotion remains deferred for these four presets. |
| 27 | `ms_gui_model_current` (`src/index.js:2873-2924`) | net-new | Target: revision-producing `Clean`, `AdjustHydrogen`, and combined cleanup transform with result round-trip. | Do not treat queued/in-place GUI mutation as a revision. `AdjustHydrogen` and `Clean` require output atom/geometry audit before promotion. | Proposed `pytest -q tests/test_structure_cleanup.py`; preview writes nothing, execute records an attempt, output promotion creates one new revision, failed/ambiguous atom mapping creates none, and opt-in licensed smoke checks a small molecule. |
| 28 | `ms_gui_edit_current` (`src/index.js:2926-3037`) | partial | Add/delete atoms/bonds, bond type, substitution, and position already exist (`specs/patch.py:35-40`, `358-425`); rename, calculated bonds, and cleanup are missing. | Add missing operations as typed patches/transforms with stable atom IDs; broad periodic bond guessing remains explicit and default-off. | Existing `pytest -q tests/test_semantic_patch.py -k 'atom or bond'` stays green; proposed tests cover ID-safe rename, periodic bond-guess refusal, cleanup result mapping, and rollback. |
| 29 | `ms_create_molecule` (`src/index.js:3039-3071`) | existing | `material_studio_build_molecule` and structured `MoleculeSpec` create (`server.py:12971-13052`, `specs/molecule.py:13-89`). | Prefer structured project creation for persistent work; retain isolated compatibility runner. | `pytest -q tests/test_server_tools.py -k build_molecule tests/test_structured_specs.py -k molecule` passes for duplicate IDs, missing bond targets, and dry-run. |
| 30 | `ms_forcite` (`src/index.js:3073-3091`) | partial | Geometry optimization and Dynamics exist (`specs/forcite.py:34-102`; `server.py:46641-46873`); typed Forcite Energy is absent. | Add a reviewed `ForciteEnergySpec`; do not expose `settings: record<string, scalar>`. | Proposed `pytest -q tests/test_forcite_energy.py`; exact deterministic settings, unknown-key rejection, preview-only default, attempt journal, and opt-in licensed single-point smoke. |
| 31 | `ms_castep` (`src/index.js:3093-3116`) | partial | The strict CASTEP enum/property renderer now includes the four extended presets in row 26, but their execution/result-audit contract remains intentionally unavailable. | Extend the strict enum/property/result contract only with documented API evidence. | `pytest -q tests/test_castep_extended_presets.py tests/test_castep_electronic.py` keeps deterministic preview and artifact-audit coverage separate; no preview-only preset may be advertised as executable. |
| 32 | `ms_list_workspace` (`src/index.js:3118-3145`) | net-new | Implemented bounded `WorkspaceSnapshotService` and public read-only `material_studio_workspace_snapshot`. | Do not expose unrestricted recursive workspace traversal; confine by project/revision, reject link escape, cap entries, return hashes/types. | `pytest -q tests/test_read_only_dashboard.py -k 'snapshot or index or link'` covers missing-root purity, bounded summaries, invalid metadata, traversal/link rejection, and no workspace write. |
| 33 | `ms_read_text` (`src/index.js:3147-3159`) | net-new | Implemented bounded `WorkspaceSnapshotService.read_artifact` and public `material_studio_workspace_artifact_read`. | Require project/revision and artifact-relative path; allow only reviewed text/JSON/CIF/raster types with strict byte caps, never arbitrary workspace files. | `pytest -q tests/test_read_only_dashboard.py -k 'artifact or traversal or symlink'` covers binary/type rejection, limits, traversal/link escape, and byte-for-byte read-only behavior. |

The mutually exclusive row totals are: `existing/equivalent = 13`,
`partial = 6`, `net-new = 9`, and `reject-security-pattern = 5`.

## Implementation status in this change

The categories above compare the external repository with the audited local
baseline; they are not completion labels. This absorption change implements:

- secure public COD search and CIF ingest backed by immutable source
  provenance (rows 11-12);
- strict DMol3 molecule geometry optimization with result-gated revision
  promotion (row 21);
- public immutable remote CASTEP prepare/record/status workflows backed by the
  revision-bound core and event journal (rows 23-25);
- four additional, deliberately preview-only CASTEP presets (rows 26 and 31);
  and
- a bounded public workspace snapshot/artifact reader plus loopback read-only
  dashboard (rows 32-33 and the Dashboard section).

Still deferred are automatic CIF candidate choice, credentialed ICSD/CSD
access, arbitrary scheduler transport, SSH, remote polling, Materials Studio
Job Control automation, executable/result promotion for the four new CASTEP
presets, the guided Windows installer/release bundle, and every remaining
`Target:` or `Proposed` item. The licensed DMol3 path was exercised through
the file-based runner without GUI input; no remote scheduler submission, SSH
operation, or calculation for the four new CASTEP presets was performed.

## Actual no-GUI acceptance evidence

- Protocol discovery enumerated 49 public MCP tools, including the eight added
  by this change.
- The COD `1009001` acceptance used the live network and the real Materials
  Studio 20.1 runner. The source SHA-256 was
  `9ee5b49616a5d5cbc03740e36d65b4cff9c8dc8df1bd4530c97aaf7603e88225`;
  the same value was carried by `FileRef`, checked inside MaterialsScript with
  `Digest::SHA`, and checked again after import. Exclusive staging prevented a
  pre-existing `in.cif` from being reused.
- The water DMol3 acceptance used Coarse quality, LDA, and `charts=Yes`. It
  converged and promoted exactly one revision (`r000` to `r001`) with total
  energy `-47602.3425319718` kcal/mol. All three XSD atom identity tokens
  matched, maximum coordinate delta was `0`, the report was verified, and the
  native chart documents were `in Energies` and `in Convergence`.
- `Frequency`, `BandStructureAndDOS`, `ChargeDensity`, and
  `DensityDifference` were exercised only through public preview calls with
  fail-fast runner/materializer/GUI sentinels. No CASTEP calculation ran.
- A real local remote-handoff lifecycle verified a manifest whose SHA-256
  begins `c7b493` and a consistent journal. It did not invoke SSH, a scheduler,
  or submit a job; those remain external actions.
- Dashboard source-process smoke covered the read-only loopback routes and
  rejection behavior. Workspace snapshots and artifact reads remained
  bounded, link-safe, and byte-for-byte read-only.

All of these acceptance runs left the Materials Studio GUI untouched.
Preview-only calls were checked for filesystem purity, and structured output
overrides were confined to their assigned output directories so absolute or
traversal paths could not escape the workspace transaction.

## Dashboard, installation, and remote capability decisions

### Dashboard

The external Dashboard is a loopback workspace viewer with current session,
queue heartbeat, calculation folders, XSD preview, and optional remote status
(`GUI-Dashboard/README.md:3-25`). Its server also has write endpoints that
create/switch sessions, overwrite state, and queue modeling/calculation scripts
(`GUI-Dashboard/server.js:762-805`). Those writes bypass the local project's
serialized current/history publication (`state/store.py:120-200`,
`930-970`).

The implemented first slice in `read_only_dashboard.py`:

1. Provides a loopback-literal-only HTTP server with `GET`/`HEAD` snapshot,
   artifact-index, and bounded artifact-read routes.
2. Uses the same `WorkspaceSnapshotService` as the public tools in rows 32-33.
3. Has no modeling, calculation, session-switch, config-write, queue, shell, or
   GUI-input endpoint.
4. Rejects unsafe bind/Host values, encoded traversal, links/reparse points,
   unsupported methods, extra query fields, oversized responses, and unsafe
   artifact types; it sets restrictive CSP and `no-store`.

`pytest -q tests/test_read_only_dashboard.py` covers these boundaries and
byte-for-byte read-only behavior. A source-tree subprocess smoke also covered
successful health/snapshot/artifact reads and rejection of write methods and
unsafe Host headers without changing the workspace. A richer projection of
live diagnostics, history, execution attempts, GUI snapshots, and remote
handoff summaries is still deferred; the implemented dashboard must not be
described as that full operational console yet.

### Installation and release

The external project offers a detection/configuration wizard
(`scripts/configure-ms-mcp.ps1:18-154`), `npm ci` plus smoke wrapper
(`Install-MS-MCP.bat:1-33`), SSH-key helper
(`Setup-Dashboard-SSH.bat:30-49`), and an allowlisted release manifest
(`RELEASE-FILES.md:1-19`). The local project already has safer fingerprinted
Codex registration and rollback (`codex_registration.py:46-258`) plus
immutable runtime deployment (`runtime_deployment.py:64-268`), but lacks one
guided Windows entry point and a comparable release bundle smoke.

Required absorption:

1. Add a Windows setup front end that only orchestrates the existing
   runtime-deployment and Codex-registration **preview/apply** contracts.
2. Detect Python and `RunMatScript.bat`, but require review of every resolved
   path and plan/hash before apply.
3. Add release-manifest generation, archive SHA-256, clean extraction, config
   preview, protocol smoke, and uninstall/rollback instructions.
4. Do not copy automatic `authorized_keys` mutation; remote credentials remain
   an administrator-managed adapter concern.

Acceptance: PowerShell tests with fake paths (spaces, CJK, long paths) prove
preview is byte-for-byte read-only, apply is idempotent and hash-bound,
config drift blocks apply, rollback restores the exact backup, and an extracted
release runs `ms-mcp-config-doctor` plus `ms-mcp-protocol-smoke`.

### Remote CASTEP

The external implementation prepares native or script-driver batches but still
requires manual Materials Studio submission (`src/index.js:2301-2650`), records
caller-reported Job Control identities (`src/index.js:2652-2701`), reads local
markers (`src/index.js:2703-2743`), and optionally executes an SSH shell probe
from the Dashboard (`GUI-Dashboard/server.js:536-695`). It hard-codes 48 cores
and workflow-specific process naming (`src/index.js:2350`, `2643-2647`;
`GUI-Dashboard/server.js:654-665`).

The implemented safe local increment comprises `remote_handoff.py`,
`specs/remote_job.py`, and the three public MCP wrappers listed in rows 23-25:

- preview/execute an immutable CASTEP bundle bound to the exact current
  revision, expected spec/script/input SHA-256 values, and the deterministic
  translator output for that saved script; execute must also echo the exact
  preview manifest SHA-256;
- append `prepared`, `submitted`, and `status` events to a per-bundle,
  hash-linked journal under a persistent advisory lock;
- require a structured scheduler identity and job id for submission and status;
- expose local read-only status without running shell, SSH, a scheduler client,
  Materials Studio, or a GUI action.

The no-GUI local acceptance verified a handoff manifest whose SHA-256 begins
`c7b493` and a consistent event journal. It did not contact a remote host,
submit a scheduler job, or claim that any recorded external identity had been
accepted by a scheduler.

Actual scheduler transport is a later adapter. It must consume this immutable
handoff, enforce host-key and remote-root policy, and append observed evidence
without rewriting the manifest or prior events.
