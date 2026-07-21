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

## Live smoke acceptance

`ms-mcp-live-smoke` exposes the audit through two opt-in flags. Neither flag is
sent to the server during default smoke runs, and default compact summaries do
not contain round-trip fields.

Use a preview to verify request routing and planning without calling the
runner or touching the GUI:

```powershell
ms-mcp-live-smoke --scenario silicon --execution-mode preview `
  --verify-ms-roundtrip --no-include-gui-status --no-take-snapshot `
  --working-dir workspace/live_smoke_roundtrip_preview
```

The smoke result requires a revision-bound plan with no runner call, no file or
GUI side effect, and no audit run directory. When a follow-up request is
present, the base and follow-up revisions are checked separately. The final
`material_studio_live_project_status` audit must match the final live receipt.

For a real Materials Studio acceptance run, first verify that exactly one
existing Materials Studio window is selected, then explicitly authorize
execution:

```powershell
ms-mcp-live-smoke --scenario silicon --hotload --execution-mode execute `
  --require-real-ms-roundtrip `
  --working-dir workspace/live_smoke_roundtrip_real
```

`--require-real-ms-roundtrip` implies `--verify-ms-roundtrip` and is rejected
with `auto` or `preview`. Execute acceptance checks the source hashes before
and after import/export, deterministic script identity, output confinement,
tagged JSON, CIF comparison, runner identity, and unchanged one-process,
one-window GUI inventory. A structurally valid fake-runner receipt remains
`NOT_RUN`: it can test the acceptance code but cannot pass the real-MS flag.

Continuation flags do not synthesize `verify_ms_roundtrip`. They use the exact
workspace-bound retry payload returned by the server, so deferred execution or
GUI synchronization cannot silently change the original audit request.

## MCP protocol acceptance

The default `ms-mcp-protocol-smoke` run sends the silicon preview through MCP
stdio with `verify_ms_roundtrip=true`, `open_in_gui=false`, and compact
responses. It verifies that tool discovery exposes the request field, then
binds the create and live-status plans to the same project, revision, spec
SHA-256, and workspace-confined output paths. The accepted plan must remain
`deferred_until_materialized`, with no runner call, GUI probe, file side
effect, receipt, comparison, or round-trip directory.

This protocol check proves that an @mcp client can discover and request the
preview audit. It does not prove real Materials Studio execution; that remains
the explicit `--require-real-ms-roundtrip` live-smoke gate described above.
