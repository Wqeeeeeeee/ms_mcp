# 3C-SiC Surface Plugin

## Scope

WO-SURFACE-001 covers one deterministic create-only profile:

- material: zinc-blende 3C-SiC;
- orientation and face: `(001)` Si face;
- cell: `2x2` conventional in-plane repeat;
- thickness: four Si-C bilayers, represented by eight alternating atomic planes;
- termination: ideal Si top and C bottom;
- passivation: two H atoms per bottom C atom with `1.09 angstrom` C-H bonds;
- vacuum: `15.0 angstrom` over the full atomic extent, with that extent centered; and
- composition: `Si32 C32 H16`, 80 atoms total.

The plugin returns revision-zero `ModelSpec` data only. Construction is ideal,
unreconstructed, unrelaxed, deterministic, and side-effect free.

## Source Pin

The lattice source is Crystallography Open Database record `1010995`, revision
`278158`, licensed `CC0-1.0`. The reviewed development metadata pins:

- source URL: `https://www.crystallography.net/cod/1010995.html`;
- provider revision: `278158`;
- raw SHA-256: `7bf61ff721dae3b8fa263506aa85e0de5a83bca822744d58e9d30670200eafbb`; and
- license: `CC0-1.0`.

These are provenance pins, not candidate coordinate inputs. The plugin's
reference policy is `task_only`; final reference coordinates and hidden
holdout data are denied.

## Preview Boundary

`match`, `plan`, `build`, and `validate` are passive internal plugin stages.
They do not register a public MCP tool, read files, use the network, create a
revision, run MaterialsScript, control the GUI, launch Materials Studio, or run
CASTEP. A successful validation is `PASS_WITH_WARNINGS` because the structure
is still an ideal pre-relaxation preview.

The plugin fails closed when the project ID is missing or invalid, a fixed
value conflicts, a boolean is substituted for a numeric value, an unknown
parameter is present, a current model is supplied, or the request asks for a
different material, face, facet, polytype, output kind, or operation. A missing
project ID produces a typed question and no build-eligible step.

## Unsupported Features

The profile does not support C-face slabs, other Miller faces, 4H-SiC or
6H-SiC, arbitrary repeats or layer counts, reconstructions, relaxation,
adsorbates, defects, interfaces, contacts, oxides, calculations, patching,
backend execution, or GUI control. Unsupported requests must not fall back to
this fixed structure.

## Development Benchmark

The coordinate-free descriptor is
`benchmarks/cases/sic_3c_surface/benchmark_case.json`. The parent auditor
constructs a reviewed `CoordinateFreeStructureProjection`, then calls the
public `compile_coordinate_free_blind_task` and
`project_coordinate_free_contract` boundaries. A real subprocess receives
only that projected task and confined candidate-output instructions. Its PID
must differ from the auditor PID. The subprocess routes and plans through
`CapabilityRegistry` and `RuntimeRouter`, then builds, validates, and writes the
candidate. It receives no oracle path, artifact, or bytes.

The candidate files and submission hashes are frozen after the subprocess
exits. Only then does the parent create and read a temporary analytical oracle.
That oracle independently repeats the public zinc-blende construction rules in
test code; it does not call plugin geometry helpers, reuse candidate
coordinates, or access the repository reference store. This ordering is the
evidence for the descriptor's `process_isolation_required=true` declaration.

The descriptor binds the temporary oracle recipe output by SHA-256
`a798c4b6e4af7b5fdd299392d6fbd181a4f8e7e3c8e4d8667ebfb4f056d405a8`.
Only the digest is checked in; the coordinate-bearing oracle is generated
inside the isolated temporary reference root after candidate submission.

The isolated evaluator compares the frozen candidate with the temporary
oracle and receives exactly one trusted surface observation:
`surface.vacuum_absolute_error_angstrom`, recomputed independently from the
frozen candidate CIF against `15.0 angstrom` and bound to the submitted
candidate structure hash. This is the evaluator's only independent
surface-domain metric. It does not
independently certify reconstruction energetics, relaxation, electronic
properties, convergence, or experimental agreement.

The SHA-256 binding is enforced by the development harness immediately before
evaluation: the observation evidence digest must equal
`CandidateSubmission.structure_sha256`. A negative regression proves a
mismatch is rejected before the evaluator is called. The shared evaluator does
not natively enforce this binding; adding that common-contract behavior is
outside WO-SURFACE-001.

The development harness adds the explicit P1 identity operation and unit
occupancy column, then normalizes atom labels to unambiguous element-prefixed
counters in the public translator's deterministic CIF text before candidate
freeze. The canonicalizer requires these forms. This adapter does not change
the translated lattice or coordinates.

The benchmark descriptor retains the schema-required
`public_entry_tool=material_studio_live_modeling_request` literal. Migration is
off for this Work Order: tests exercise the internal `RuntimeRouter` path in
the modeler subprocess, and public MCP dispatch is not activated or tested.

Expected development states are:

| Gate | State | Evidence boundary |
| --- | --- | --- |
| `structure_valid` | `PASS` | Offline periodic comparison with the independent oracle |
| `semiconductor_domain_valid` | `PASS` | Independently recomputed full-extent vacuum error |
| `ms_roundtrip_valid` | `NOT_RUN` | No real Materials Studio round trip |
| `calculation_evidence_valid` | `NOT_RUN` | No real calculation |
| `scientifically_verified` | `NOT_RUN` | Offline construction is not scientific verification |

Candidate bytes, submitted hashes, and the complete candidate-tree identity
must remain unchanged through evaluation.

## Verification

Run the focused suite with the repository virtual environment:

```powershell
C:\Users\Administrator\Documents\ms_MCP\.venv\Scripts\python.exe -B -m pytest tests/domains/surface -q
```

The manifest validates against `schemas/domain_plugin.schema.json`, registers
in `CapabilityRegistry`, and routes end to end when the strict canonical
`atom_count=80` parameter is present. Boolean `True` and any other integer atom
count fail closed before build.
