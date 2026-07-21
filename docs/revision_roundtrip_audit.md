# Revision-Bound CIF Round-Trip Audit

`verify_ms_roundtrip=true` enables an optional audit for crystal revisions. The
audit runs after the revision's deterministic CIF materialization and before a
GUI hot-load. It imports the revision CIF and exports a fresh CIF through the
configured `RunMatScript.bat` runner, then compares lattice parameters,
composition, atom coordinates, and periodic atom identity.

The audit is deliberately bounded:

- Preview only creates a plan. It does not call Materials Studio, inspect the
  GUI, or write an audit run directory.
- Execute uses a unique revision execution attempt directory and refuses path
  traversal, pre-existing run roots, unsafe generated scripts, and runner
  artifacts outside that directory.
- Source, generated script, runner, output, and normalized receipt identities
  are rechecked at their execution boundaries. Oversized inputs are rejected
  before CIF parsing, and the persisted receipt is the exact validated payload
  returned to the caller.
- When GUI hot-loading is requested, the process/window inventory is captured
  before and after the import/export. A new process, changed window identity,
  or a violation of the single-window policy fails the audit.
- The source CIF is hash-checked before and after execution. The generated
  import/export script and tagged JSON summary are bound to the exact source
  and output paths.

The receipt is stored in `outputs/rNNN/result_metadata.json` and the audit run
directory. It is surfaced in `modeling_report`, `modeling_health`, compact
responses, and live project status. Important fields include
`real_materials_studio_status`, `source_unchanged`, `gui_invariant`, and
`comparison`.

This is an artifact and execution-integrity check. It does not run Energy,
CASTEP, Forcite, geometry optimization, or any other calculation, and
`scientific_correctness_established` is always `false`. A fake or unverified
runner may produce structural comparison evidence for tests, but its
`real_materials_studio_status` remains `NOT_RUN`.

For non-crystal revisions, an explicit request returns `not_applicable` and
does not call the round-trip runner or probe the GUI.

Example:

```json
{
  "project_id": "silicon_demo",
  "execution_mode": "preview",
  "verify_ms_roundtrip": true,
  "open_in_gui": true
}
```

For a real Materials Studio smoke test, first run the read-only GUI status
tool and confirm that exactly one existing Materials Studio window is the
target. Then submit the same revision with explicit `execution_mode="execute"`
and `verify_ms_roundtrip=true`. The audit never launches another
`MatStudio.exe` process and never sends GUI input itself.
