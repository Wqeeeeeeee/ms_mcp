# 3C-SiC Materials Studio 20.1 CIF Round Trip

## Scope

`material_studio_mcp_server.ms_roundtrip` is a private, preview-first adapter
for one fixed acceptance profile:

- material: zinc-blende 3C-SiC;
- surface: ideal `(001)` Si face;
- cell: `2x2` conventional in-plane repeat and four Si-C bilayers;
- termination: Si top, C bottom, with bottom H passivation;
- composition: `C32 H16 Si32`, exactly 80 atoms;
- full-extent vacuum: `15.0 angstrom`; and
- candidate identity: revision zero from
  `sic_3c_001_si_face_surface` contract and implementation `1.0.0`.

The package imports the candidate CIF into Materials Studio and exports it to a
new CIF. It then checks whether that bounded transformation preserved the
structure. It does not perform optimization, dynamics, CASTEP, or any other
calculation.

The round-trip contract and implementation versions are both `1.0.0`. All
package contracts are strict, frozen Pydantic v2 models with unknown fields
forbidden and non-finite numbers rejected. This package registers no public MCP
tool and does not alter the existing MCP inventory or
`material_studio_live_modeling_request` schema.

## Request Contract

`RoundtripRequest` binds all execution-relevant inputs:

| Field | Contract |
| --- | --- |
| `request_id` | Stable identifier using letters, digits, `.`, `_`, or `-` |
| `run_id` | Unique identifier; also names the fresh run directory |
| `candidate` | Fixed `CandidateBinding` for the revision-zero profile |
| `candidate.structure_path` | Existing regular `.cif` file |
| `candidate.expected_structure_sha256` | Exact lowercase SHA-256 of the input bytes |
| `output_root` | Existing regular directory |
| `execution_mode` | Exact literal `preview` or `execute`; default `preview` |
| `timeout_seconds` | Integer from 1 through 3600; default 300 |

The request cannot encode path traversal in `run_id`. A caller must provide a
new run ID for every execution because a pre-existing run root is rejected and
never overwritten.

## Preview Semantics

`plan_roundtrip(request)` is the pure planning boundary. Calling
`MaterialsStudioRoundtripAdapter.run()` with the default
`execution_mode="preview"` returns the same `RoundtripPlan`.

Preview performs these checks in memory:

1. Read the input through a stable file-identity and SHA-256 check.
2. Require CIF input and match it to the exact fixed surface candidate.
3. Resolve the existing output root and prove that `output_root/run_id` is
   absent and confined.
4. Generate the shared deterministic `import_export_script` for the exact
   source and output paths.
5. Require the generated script to equal the reviewed template, pass
   `validate_materialscript`, bind each path exactly once, and contain none of
   the adapter's forbidden shell, delete, network, runner, document-creation,
   Forcite, or CASTEP markers.
6. Return the candidate validation, script text, script digests, request
   digest, planned paths, and explicit no-side-effect flags.

Preview does not create a directory or file, call `RunMatScript.bat`, enumerate
Materials Studio processes or windows, or send GUI input. This remains true
when `plan_roundtrip()` receives a request whose mode is `execute`; planning
alone never executes it.

## Execute Semantics

Execution occurs only through `MaterialsStudioRoundtripAdapter.run()` or
`run_roundtrip()` with the exact lowercase literal
`execution_mode="execute"`. There is no automatic retry.

The adapter executes the following fail-closed transaction:

1. Rebuild the deterministic plan.
2. Inspect and hash the runner executable.
3. Enumerate the existing `MatStudio.exe` process and window inventory without
   changing it.
4. Require one process and one visible, titled window belonging to that
   process. A minimized, non-foreground window is acceptable when Windows
   still reports it as visible; the adapter does not restore or activate it.
5. Re-read the candidate and require the preview-bound digest to be unchanged.
6. Create the previously absent `output_root/run_id` directory.
7. Invoke the runner once with the deterministic script, confined working
   directory, timeout, unique job prefix, and saved script name
   `roundtrip.pl`.
8. Enumerate the process/window inventory again and require the same process
   IDs and window PID/handle pairs.
9. Re-read the input and runner executable and require both snapshots to be
   unchanged.
10. Verify every reported runner artifact is under the run root and verify the
    saved script bytes against the preview script artifact digest.
11. Require a fresh confined `roundtrip_output.cif` and a tagged JSON summary
    containing exactly `source`, `output`, and nonempty `document_name`, with
    source and output equal to the bound paths.
12. Recompute the canonical input/output comparison from the frozen CIF bytes.
13. Atomically publish `result_receipt.json`, including a failure receipt when
    execution reached the receipt stage but one or more acceptance checks
    failed.

Runner identity, GUI, input, or output-root precondition failures can occur
before the run root exists and therefore need not produce a result receipt.
Receipt publication failure is also an exception rather than a successful
result.

## Artifact Confinement and Hash Binding

Input, runner, script, output, and receipt evidence is content-addressed:

- the immutable input CIF binds byte count, SHA-256, and a SHA-256 of its
  absolute location rather than exposing that location in the receipt;
- the runner executable binds the same external-artifact fields and is
  snapshotted before and after execution;
- the script binds both UTF-8 source bytes and native-newline artifact bytes;
- the persisted script must match the native artifact digest exactly;
- every runner-created file is represented by a run-root-relative path, byte
  count, SHA-256, and role;
- the exported CIF binds its relative path, byte count, and SHA-256;
- command, stdout, stderr, Materials Studio output, and Materials Studio log
  are represented by hashes in `RunnerExecutionReceipt`; and
- the atomically written receipt is returned with its own relative path, byte
  count, and SHA-256 in `RoundtripExecutionResult`.

Receipt publication uses an atomic no-clobber primitive in the run directory.
If another writer creates the destination between preflight and publication,
the adapter fails without replacing either file; a complete receipt is never
made visible by a replace operation that can overwrite an existing target.

The tagged MaterialsScript JSON is separately bound by hashes of the source
path, output path, and document name. Runner success without the exact tagged
summary and a fresh bound output is a failure.

The reviewed `import_export_script` currently emits
`__MATERIAL_STUDIO_MCP_JSON_BEGIN__` and
`__MATERIAL_STUDIO_MCP_JSON_END__`. The shared runner parser accepts both that
pair and the newer `__MS_MCP_JSON_START__` and `__MS_MCP_JSON_END__` pair. The
round-trip adapter trusts neither marker form by itself; it validates the
parsed object's exact keys and path values.

The path layer rejects escape from the run root, pre-existing run roots,
symlink or Windows reparse-point components, Windows alternate data streams,
unsupported node types, unstable reads, and input hard-link ambiguity. CIF
reads are capped at 16 MiB; runner artifacts are capped at 64 MiB. The input
must be outside the fresh run root. The adapter never overwrites or deletes the
input, output, prior run, or receipt.

## Read-Only Materials Studio Inventory

The GUI backend is used only through `list_processes()` and `list_windows()`.
The compact receipt records counts, visibility/minimized/foreground flags, and
SHA-256 identities. It deliberately contains no raw PID, window handle, or
window title.

Real execution requires:

- exactly one existing `MatStudio.exe` process before execution;
- exactly one usable window tied to that process before execution;
- exactly one process and one usable window afterward;
- identical process ID inventory before and after;
- identical window PID/handle inventory before and after; and
- no newly observed `MatStudio.exe` process.

`RunMatScript.bat` and its headless MatServer child are allowed. Another
`MatStudio.exe` process or another GUI window fails the invariant. The adapter
does not close extra processes or windows.

## No GUI Activation or Hot-Load

This round trip never calls GUI launch, activation, restore, screenshot,
file-open, same-window open, hot-load, UIA, keyboard, mouse, COM, or file
association paths. It does not display `roundtrip_output.cif` in the open
Materials Studio window. The existing window is only a read-only before/after
identity invariant.

This policy is intentional: a real round-trip PASS must preserve the user's
already-open Materials Studio process and window without interacting with
them. Any separate visual review or hot-load workflow is outside this Work
Order and must not be mixed into the real round-trip acceptance run.

## Real and Fake Backend Claims

The adapter supports dependency-injected fake runners and GUI inventories for
offline protocol tests. Backend claims are intentionally asymmetric:

| Execution evidence | Adapter result | `real_materials_studio_status` | Benchmark `ms_roundtrip_valid` |
| --- | --- | --- | --- |
| Offline fake runner succeeds | May be `PASS` | `NOT_RUN` | `NOT_RUN` |
| Offline fake runner fails | `FAIL` | `NOT_RUN` | `NOT_RUN` |
| Bound real MS 20.1 runner succeeds and all gates pass | `PASS` | `PASS` | `PASS` |
| Bound real MS 20.1 runner or any real gate fails | `FAIL` | `FAIL` | `FAIL` |

A real claim requires a `MaterialStudioRunner` whose executable is exactly
named `RunMatScript.bat` under a path component named `Materials Studio 20.1`
or `Materials Studio 20.1 x64 Server`. It also requires no
`MATERIAL_STUDIO_COMMAND_TEMPLATE` override and no extra runner arguments. The
executable is hashed before execution and must remain unchanged afterward.

An injected callable fake runner is labeled `offline_fake_runner` even when it
copies the CIF perfectly and produces an adapter `PASS`. Fake evidence can
test contracts, confinement, failure handling, and comparison, but it cannot
claim that Materials Studio ran.

## CIF Compatibility Adaptation

The surface translator's deterministic CIF dialect differs narrowly from the
shared CIF canonicalizer. When direct parsing fails, the round-trip comparator
builds a compatibility copy in memory only:

- require ASCII input;
- require exactly one unambiguous fractional atom loop;
- require label, type symbol, and fractional `x`, `y`, and `z` columns;
- accept only `C`, `H`, and `Si` rows with a consistent column count;
- relabel rows deterministically as element-prefixed counters such as `C1`,
  `H1`, and `Si1`;
- append occupancy `1.0` when `_atom_site_occupancy` is absent; and
- append the single identity operation `x,y,z` when neither supported
  symmetry-operation tag is present.

The original bytes are SHA-256 checked before any parse attempt. The adapted
bytes receive a separate in-memory digest for parser input. Neither the input
CIF nor the Materials Studio output CIF is rewritten by this compatibility
step. This is a closed adapter for the known surface-translator dialect, not a
general CIF repair or conversion facility.

The explicit real acceptance first gives the exact byte-for-byte surface
translator CIF to Materials Studio. Only after that raw-input run passes does a
second isolated benchmark run use the narrow normalization required by the
shared evaluator's candidate dialect. Both inputs are immutable and
SHA-256-bound before their respective runs. The normalized benchmark staging
therefore cannot substitute for or claim coverage of the first raw-input run.
No general-purpose CIF repair is enabled by this test-only staging step.

The expanded parsed CIF is used for exact atom count, composition, and vacuum
checks. The shared canonicalizer may represent the periodic structure in a
smaller conventional form; that does not relax the requirement that each raw
expanded candidate contain all 80 atoms with exact composition.

## Candidate Identity and Canonical Comparison

Before preview succeeds, the candidate must match the fixed surface plugin
output with all of these bounds:

- atom count exactly 80;
- composition exactly `C:32`, `H:16`, `Si:32`;
- mapping coverage exactly `1.0`;
- maximum displacement no greater than `1e-7 angstrom`;
- maximum relative lattice error no greater than `1e-10`; and
- full-extent vacuum within `1e-6 angstrom` of `15.0 angstrom`.

After export, the input and output are independently parsed and canonicalized,
then compared with the shared periodic comparator. Same-species atom
permutation and reviewed conventional-cell re-expression are handled by that
canonicalizer. The output must still contain exactly 80 expanded sites and the
exact composition.

The frozen round-trip thresholds are inclusive:

| Metric | Required result |
| --- | ---: |
| Mapping coverage | `== 1.0` |
| RMS displacement | `<= 0.05 angstrom` |
| Maximum displacement | `<= 0.15 angstrom` |
| Maximum relative lattice error | `<= 0.001` |
| Full-extent vacuum absolute error | `<= 0.10 angstrom` |

Full-extent vacuum is the largest translation-invariant empty fractional gap
converted to a Cartesian height over the three cell axes. Mapping degeneracy
is recorded, while the five frozen metrics determine the round-trip decision.
Malformed, ambiguous, unsupported, or uncomparable structures fail closed.

## Benchmark and Reference Isolation

The development descriptor is
`benchmarks/cases/sic_3c_ms_roundtrip/benchmark_case.json`. The shared evaluator
keeps `ms_roundtrip_valid` disabled and `NOT_RUN`; the PR-7
`evaluate_roundtrip_benchmark()` wrapper derives that state without modifying
the shared report.

The wrapper requires one submitted `structure` and one
`ms_roundtrip_structure`, verifies the round-trip receipt digest, and requires
every trusted domain observation to bind the submitted round-trip output
SHA-256. It does not trust the caller's numeric observation: after reading the
frozen CIF bytes it derives the allowlisted metric again and requires strict
equality before evaluator entry. Unknown, non-finite, duplicate, or mismatched
metrics fail closed. It then:

1. freezes the complete candidate tree with `CandidateTreeGuard`;
2. reads the input and output only from the explicit candidate root;
3. recomputes the round-trip comparison from those immutable bytes;
4. requires the receipt's input, output, and comparison to match;
5. runs the shared evaluator against explicit isolated roots;
6. proves the shared evaluator report was not changed; and
7. proves the candidate tree remained byte- and identity-stable.

The adapter receives only the candidate CIF, its expected digest, output root,
and execution controls. It does not enumerate or read
`benchmarks/references`, validation data, hidden holdouts, final reference
coordinates, atom mappings, or displacement vectors. The fixed candidate is
reconstructed through the surface plugin under `task_only` reference access,
with raw structures, final coordinates, and hidden holdouts denied.

The development benchmark's coordinate-bearing analytical oracle exists only
inside the isolated evaluator harness after candidate artifacts are frozen.
The coordinate-free acceptance projection is checked to contain no
coordinates, lattice vectors, atom mappings, displacement vectors, raw
artifact bytes, absolute paths, PID, or window handle. Real-run CIFs and other
machine-local artifacts stay outside Git.

The repository retains only the coordinate-free projection
`benchmarks/cases/sic_3c_ms_roundtrip/real_ms_20_1_evidence.json`. It binds the
raw candidate, real exported CIF, runner, persisted run receipt, exact numeric
comparison, compact one-window invariant, and five validity states by digest
or scalar value. It contains no coordinates, lattice vectors, atom mappings,
raw bytes, absolute paths, PID, handle, or title. Ordinary regression rebuilds
the raw surface candidate and checks its SHA-256 against this projection; the
explicit real test additionally requires all stable projection fields to match
the current backend run.

For an offline fake run, the shared structural and semiconductor gates may
pass, but the MS state and overall state remain `NOT_RUN`. A real acceptance
requires all three independent gates to pass:

- `structure_valid=PASS`;
- `semiconductor_domain_valid=PASS`; and
- `ms_roundtrip_valid=PASS`.

`calculation_evidence_valid` and `scientifically_verified` remain `NOT_RUN`.
Weighted scoring is unavailable and hard failures are not compensable.

## Output Layout and Receipt

Execute writes beneath one fresh local run root:

```text
<output_root>/
  <run_id>/
    roundtrip_output.cif
    result_receipt.json
    .material-studio-mcp/
      jobs/
        <runner-job>/
          roundtrip.pl
          <runner output and log artifacts>
```

The exact runner-job name and optional output/log files are runner-owned. All
reported files must remain under `<output_root>/<run_id>`.

`result_receipt.json` is canonical ASCII JSON with a trailing newline. It
records request and plan digests, timestamps, candidate validation, script
safety, runner identity and execution hashes, input and runner immutability,
the relative output artifact, tagged-summary binding, compact GUI invariant,
canonical comparison, stable failure codes, and the real/fake environment
classification. It contains no absolute paths or raw GUI identity.

The receipt deliberately reports both structural success and evidence scope.
`calculation_evidence_status` and `scientific_status` are always `NOT_RUN` for
this profile.

## Acceptance Commands

Run all commands from the PR-7 worktree with the repository's configured Python
environment:

```powershell
python -m pytest -q tests/ms_roundtrip/test_contracts.py tests/ms_roundtrip/test_plan.py tests/ms_roundtrip/test_comparison.py
python -m pytest -q tests/ms_roundtrip/test_adapter.py tests/ms_roundtrip/test_artifact_binding.py
python -m pytest -q tests/test_runtime_public_compatibility.py tests/test_mcp_protocol_smoke.py
python -m pytest -q tests/ms_roundtrip/test_benchmark.py -k offline
python -m pytest -q tests/ms_roundtrip tests/domains/surface tests/benchmark_evaluation tests/test_semiconductor_architecture_schemas.py
python -m pytest -q tests/ms_roundtrip/test_no_reference_leak.py
python -m pytest -q tests/ms_roundtrip/test_gui_invariants.py
python -m compileall -q src/material_studio_mcp_server/ms_roundtrip
git diff --check 228251e2597d5945172d5e40272edf2f73dfe5e4...HEAD
python -m pytest -q
```

Every command above is merge-blocking under `WO-MS-ROUNDTRIP-001`; the explicit
real-MS command below is additionally required in the real environment.

### Explicit Real Materials Studio 20.1 Acceptance

The real test is opt-in and must be invoked exactly with `--run-real-ms`:

```powershell
python -m pytest -q tests/ms_roundtrip/test_real_ms_20_1.py --run-real-ms
```

An architect recording a new real-environment projection may additionally
provide a new path outside the repository. The command refuses an existing
target and never updates the committed evidence automatically:

```powershell
python -m pytest -q tests/ms_roundtrip/test_real_ms_20_1.py --run-real-ms --real-ms-evidence-output <new-external-json>
```

Before this command, leave exactly one existing Materials Studio window open.
Do not launch a second instance, activate or restore the existing window for
the test, load a structure into it, or use GUI automation. The selected runner
must satisfy the MS 20.1 identity rules above, and the local output location
must be outside the repository so coordinate-bearing artifacts and
machine-local paths are not committed.

In ordinary regression mode, the real test may skip. With the explicit
`--run-real-ms` option, missing runner or single-window prerequisites are an
acceptance failure, not a skip. A passing invocation must report real backend
evidence, unchanged input and runner bytes, unchanged single-process/window
identity, a bound exported CIF and tagged summary, and all frozen comparison
thresholds passing.

## Known Limits

- Only the exact revision-zero 3C-SiC(001) Si-face candidate is supported.
- Only CIF import/export is covered; arbitrary structures and other formats
  fail closed.
- The compatibility adapter supports only the documented narrow ASCII atom
  loop dialect.
- The process/window check is inventory-only. It neither proves viewport
  content nor provides visual confirmation.
- A minimized window may satisfy inventory if Windows reports it visible; the
  adapter does not change its state.
- No GUI output loading, activation, screenshot, Copy Script, or replay is part
  of this transaction.
- No retry, overwrite, cleanup, or recovery of a used run ID is automatic.
- A successful fake execution is protocol evidence only, never real Materials
  Studio evidence.
- A real PASS proves bounded import/export structural preservation only. It
  does not prove relaxation, reconstruction energetics, electronic properties,
  calculation convergence, experimental agreement, or scientific correctness.
