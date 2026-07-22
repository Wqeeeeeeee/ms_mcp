# 3C-SiC CASTEP Energy Acceptance

This private acceptance harness validates one real CASTEP Energy execution for
the existing frozen 3C-SiC(001) Si-face surface candidate. It registers no MCP
tool and implements no runner. Execution delegates to
`material_studio_castep_run_current` and verifies its persisted state through
the existing project store, revision execution lock, attempt journal, native
SCF audit, and electronic receipt verifier.

## Frozen profile

The profile is closed to all optional scientific work:

| Setting | Required value |
| --- | --- |
| Structure | Existing 80-atom 2x2, four-bilayer, 15 angstrom vacuum, bottom-H-passivated 3C-SiC(001) Si-face slab |
| Task | Energy |
| Functional | PBE |
| Quality | Medium |
| Cutoff | 300 eV |
| K-point grid | 2x2x1 |
| Dipole correction | Self-consistent |
| GUI open | false |
| Snapshot | false |
| View audit | false |
| Response mode | full |

Geometry optimization, DOS, PDOS, band structure, surface energy,
convergence studies, retries, GUI input, hot-load, and scientific verification
are not part of this acceptance. A finite Energy result and a nonfatal native
SCF audit establish calculation evidence only. `scientifically_verified` and
`ms_roundtrip_valid` remain `NOT_RUN`.

## Preview and execution

Preview builds the candidate and exact public-tool payload in memory. It does
not create the workspace, resolve the server tool, inspect the GUI, or call a
runner. Execute requires both the matching preview SHA-256 and the literal
`--run-real-castep` authorization. The workspace must be absent, external to
the repository, and have an existing parent.

Execution performs one public-tool preview followed by one public-tool execute
call. The journal must prove exactly one backend attempt with `started` and
`completed` events. No automatic retry is available. The workspace is never
deleted by the harness, including on failure. Before writing the candidate, the
harness atomically reserves the fresh workspace and holds a hidden guard-file
handle for the entire ProjectStore, preview, execution, and verification
transaction; a rename or reparse replacement is therefore rejected while the
run is active.

The only permitted GUI interaction is read-only process/window inventory
before and after execution. Acceptance requires one existing Materials Studio
process and window with unchanged identity. The harness cannot launch,
activate, open, snapshot, hot-load, close, or send GUI input.

## Evidence

Real evidence is a strict coordinate-free projection. It excludes coordinates,
lattice vectors, atom mappings, displacement vectors, raw native output,
paths, commands, process IDs, window handles, host data, and environment
values. Hashed GUI inventory identities are retained under unambiguous digest
field names.

The evidence contract recomputes the canonical verification SHA-256 and binds
it to `benchmark_acceptance.calculation_evidence_sha256`. It also requires
`calculation_evidence_valid=PASS`, with round-trip and scientific states left
`NOT_RUN`. External publication rejects links and reparse points, uses an
atomic no-clobber write, and rereads the exact canonical bytes and SHA-256.

No real evidence or final Work Order result receipt is committed in the
offline implementation phase.

## Offline tests

The Work Order's offline commands are:

```powershell
python -m pytest -q tests/castep_acceptance/test_contracts.py tests/castep_acceptance/test_verification.py
python -m pytest -q tests/castep_acceptance/test_adapter.py tests/castep_acceptance/test_attempt_binding.py
python -m pytest -q tests/castep_acceptance/test_preview.py tests/test_runtime_public_compatibility.py tests/test_mcp_protocol_smoke.py
python -m pytest -q tests/castep_acceptance/test_benchmark.py -k offline
python -m pytest -q tests/castep_acceptance tests/domains/surface tests/benchmark_evaluation tests/test_castep_electronic.py tests/test_castep_native.py tests/test_execution_state.py
python -m pytest -q tests/castep_acceptance/test_no_reference_leak.py
python -m pytest -q tests/castep_acceptance/test_gui_prohibition.py
python -m compileall -q src/material_studio_mcp_server/castep_acceptance
python -m pytest -q
```

The ordinary suite skips the real test before backend resolution.

## Real-run gate

Do not run this command until the architect authorizes the single execution.
Before authorization is consumed, verify all of the following:

1. The reviewed commit and clean branch are checked out.
2. Windows has exactly one existing Materials Studio process with one window.
3. The detected runner is the unmodified Materials Studio 20.1
   `RunMatScript.bat`, runner arguments are empty, and
   `MATERIAL_STUDIO_COMMAND_TEMPLATE` is unset.
4. The external workspace path is absent, its parent exists, and its full path
   is at most 120 characters.
5. The external evidence file is absent and its real parent contains no link or
   reparse component.

The literal Work Order command is sufficient on a clean machine. With no path
options, the test atomically creates one fixed external parent under the OS
temporary directory, uses `workspace-001` and
`real-castep-evidence-001.json`, and refuses to reuse that parent on a later
attempt. This keeps the exact required command safe while preserving all
artifacts. A caller may instead provide both destinations explicitly; they are
validated as fresh external paths before execution.

Example authorized command using explicitly reviewed destinations:

```powershell
$workspace = 'C:\ms-castep-acceptance\workspace-001'
$evidence = 'C:\ms-castep-acceptance\real-castep-evidence-001.json'
python -m pytest -q tests/castep_acceptance/test_real_castep.py --run-real-castep --real-castep-workspace $workspace --real-castep-evidence-output $evidence
```

An explicit real command fails rather than skips when any prerequisite is
missing. It never retries. Preserve the workspace and external evidence for
independent review regardless of outcome.
