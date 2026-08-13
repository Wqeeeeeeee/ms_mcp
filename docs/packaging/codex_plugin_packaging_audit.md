# Codex plugin packaging audit

Goal ID: `CODEX-MS-PLUGIN-GUI-LOOP-V1`

Audit date: 2026-08-13 (Asia/Shanghai)

This is the authoritative incremental audit for the `0.5.0` signed GUI-loop
release after the `0.4.0` Codex plugin packaging work merged as PR #157. It
supersedes the packaging-only safety decision wherever that older decision
rejected every persistent GUI loop. The source and release review itself was
completed without changing active Codex configuration, starting a calculation,
or weakening any existing modeling, revision, window, or evidence gate.

## Decision

Packaging may proceed from the exact current `origin/main` baseline:

- base SHA: `3b2502a479f39b52aa7358ab20ef34985911eed3`;
- base package version: `0.4.0`;
- repository license: SPDX `MIT`;
- copyright: `Copyright (c) 2026 Xu kaidong`;
- PR #157 Codex plugin packaging: merged and verified;
- DrYe reference: read-only only, with no commit-history import and no detected
  copied source or documentation requiring a third-party notice;
- Real Materials Studio: `NOT_RUN`;
- Real CASTEP: `NOT_RUN`.

The increment uses new package/plugin/runtime version `0.5.0` because it adds a
new same-window GUI hot-load capability and because each managed runtime is
addressed immutably by package version. Reusing `0.4.0` for a different wheel
would make an installed runtime correctly fail its integrity gate rather than
upgrade. This increment does not add a material template or authorize CASTEP,
Forcite, DMol3, or any other calculation.

## Git and source baseline

Read-only commands and observations:

1. `git fetch origin --prune` resolved `origin/main` to
   `3b2502a479f39b52aa7358ab20ef34985911eed3`.
2. `3b2502a4` is `feat: package Materials Studio MCP as a Codex plugin (#157)`
   and its tree is the reviewed `0.4.0` package/plugin/runtime baseline.
3. The GUI-loop increment is isolated on `codex/gui-loop-hotload-v1`; its final
   change must be based on `3b2502a4` so PR review does not replay the already
   merged packaging commit under a different SHA.
4. The original development worktree and unrelated user changes remain outside
   this dedicated sibling worktree.
5. The `0.5.0` wheel, ZIP, checksums, and release manifest must be rebuilt only
   after the final source and audit commit; artifacts built from an intermediate
   dirty tree are not release candidates.

The initially supplied development worktree and any unrelated user changes
were left untouched. Work continued in the dedicated sibling worktree.

## License and distribution status

`origin/main:LICENSE` begins with `MIT License` and contains
`Copyright (c) 2026 Xu kaidong`. This is the Wqeeeeeeee/ms_mcp repository
license, not a BIOVIA Materials Studio commercial entitlement and not the
DrYe1109/MS-MCP license.

The release branch must consistently publish:

```json
{
  "repository_license_status": "declared",
  "repository_license_spdx": "MIT",
  "public_distribution_ready": true,
  "release_blockers": []
}
```

The plugin manifest, Python core metadata, wheel license file, plugin copy of
the license, ZIP root license, and release manifest must all agree. The release
builder must fail closed on a mismatch.

This project is independent and is not an official BIOVIA or Dassault Systèmes
product. The release does not bundle Materials Studio, a Materials Studio
commercial license, or unauthorized trademark artwork. A separately licensed
local Materials Studio 2020/20.1 installation is required for real execution.

## Existing console entry points

The base `pyproject.toml` already publishes every requested Python CLI. The
installer must verify these from clean wheel metadata and executable shims
rather than reimplement them.

| Command | Entry point | Packaging use |
|---|---|---|
| `ms-mcp` | `material_studio_mcp_server.server:main` | Formal server entry point. |
| `ms-mcp-config-doctor` | `material_studio_mcp_server.codex_config:main` | Read-only configuration/runtime diagnosis. |
| `ms-mcp-config-register` | `material_studio_mcp_server.codex_registration:main` | Explicit manual fallback only. |
| `ms-mcp-runtime-deploy` | `material_studio_mcp_server.runtime_deployment:main` | Reuse reviewed integrity primitives. |
| `ms-mcp-protocol-smoke` | `material_studio_mcp_server.protocol_smoke:main` | Safe STDIO discovery/schema/annotation smoke. |
| `ms-mcp-live-smoke` | `material_studio_mcp_server.live_smoke:main` | Opt-in real acceptance only; never default test. |
| `ms-mcp-dashboard` | `material_studio_mcp_server.read_only_dashboard:main` | Preserve existing loopback read-only dashboard. |

`ms-mcp-legacy = ms_mcp.server:main` remains a compatibility entry point, but
the plugin launcher must never use it.

## Public MCP tool baseline

The exact `0.4.0` base exposed 49 public tools. The `0.5.0` increment adds only
`material_studio_gui_loop_status`, `material_studio_gui_loop_prepare`, and
`material_studio_gui_loop_stop`. The current server was imported as
`material_studio_mcp_server.server.mcp`, `mcp.list_tools()` was awaited, and an
independent AST scan of top-level `@mcp.tool(...)` registrations produced the
same 52-tool set. Tests compare the source checkout and clean wheel dynamically.

- registry-order newline-list SHA-256:
  `4cae241538584e08838556f5235330da9ffe7becc87f218eec77737fbb403f30`
- sorted newline-list SHA-256:
  `ed34ee359c3e264fcbc0b487337e56d54773aff866e8419599f919dcdc8a0ea4`

```text
material_studio_get_status
material_studio_list_script_templates
material_studio_live_capabilities
material_studio_live_session_preflight
material_studio_validate_script
material_studio_run_script
material_studio_import_export
material_studio_structure_summary
material_studio_forcite_geometry_optimization
material_studio_build_molecule
material_studio_build_tnt
material_studio_castep_energy_script
material_studio_model_create_from_spec
material_studio_model_modify_with_patch
material_studio_project_history
material_studio_live_project_status
material_studio_live_watchdog_status
material_studio_project_rollback
material_studio_model_validate
material_studio_model_preview_script
material_studio_model_get_current
material_studio_forcite_dynamics_from_spec
material_studio_model_export_view_audit
material_studio_model_export_view_bundle
material_studio_project_reconcile_dopant_metadata
material_studio_live_update_with_patch
material_studio_castep_run_current
material_studio_castep_relax_current
material_studio_live_modeling_request
material_studio_gui_status
material_studio_gui_loop_status
material_studio_gui_loop_prepare
material_studio_gui_loop_stop
material_studio_gui_launch
material_studio_gui_activate
material_studio_gui_snapshot
material_studio_gui_record_visual_confirmation
material_studio_gui_open_structure
material_studio_gui_copy_script_assist
material_studio_gui_prepare_view_replay
material_studio_gui_execute_view_replay
material_studio_gui_record_view_replay
material_studio_gui_fit_to_view
material_studio_gui_apply_current_revision
material_studio_cif_source_search
material_studio_cif_source_ingest
material_studio_workspace_snapshot
material_studio_workspace_artifact_read
material_studio_remote_castep_prepare
material_studio_remote_job_record
material_studio_remote_job_status
material_studio_dmol3_relax_current
```

`src/material_studio_mcp_server/codex_config.py` defines the current 47-tool
safe allowlist and the explicit denylist
`["material_studio_run_script"]`. Four other compatibility tools are outside
the recommended allowlist. Plugin mode adds a server-side fail-closed guard for
the arbitrary-script tool without changing the public wheel tool set.

## Signed GUI-loop protocol decision

The `0.5.0` GUI loop is a bounded transport for structures that have already
passed the existing explicit execute, immutable revision, materialization, and
single-window gates. It is not a modeling engine and is not a general
MaterialsScript runner.

- The generated GUI-context script has one fixed operation:
  `import_structure`. Queue jobs contain data envelopes, never Perl source or a
  path to executable script content.
- Manager key material is local and at least 32 bytes. Configuration, current
  state, heartbeat, jobs, and terminal results are HMAC-SHA256 authenticated;
  invalid or mismatched signatures fail closed.
- Every session is bound to an exact Materials Studio PID, top-level HWND,
  workspace project, and base/current revision. Every import is a
  compare-and-swap from the signed `expected_revision` to `target_revision` and
  also binds the document name, absolute structure path, and structure SHA-256.
- Queue publication and state transitions use exclusive producer ownership and
  atomic `staging -> pending -> running -> done|failed` moves. A terminal success
  is accepted only when its signed receipt and committed state match the exact
  job, revision, document, and structure digest.
- `auto` may use the existing verified File/Open dialog only when the exact loop
  is not ready **before** enqueue. Once a job is enqueued, timeout, failure, or
  uncertain completion reports `side_effect_may_have_occurred=true` and
  `automatic_dialog_fallback_allowed=false`; it must never retry through the
  dialog or enqueue the same import automatically.
- Preparation writes the fixed loop but sends no GUI input and does not start it
  through `RunMatScript.bat`. The operator starts it once inside the already
  verified Materials Studio GUI context. Stop is explicit session shutdown, not
  routine cleanup.

These constraints preserve the existing same-window policy and allow Codex
modeling requests to hot-load successive revisions without opening a second
Materials Studio process. They do not authorize Fit-to-View, screenshots,
visual acceptance, or report publication outside their existing exact-revision
GUI artifact transaction.

## Already merged DrYe capability absorption

Main already contains the separately reviewed capability absorption work:

- merge commit: `6d9149dcf35e73cb44fe9d393677f4e3a7593ac2`;
- integration parent: `6d593a5e3ac40c368bfa8cfb15886584dfa70b2e`;
- feature commit: `e27176e70ad429b300cbac6dada149f8ef535cbf`;
- detailed matrix: `docs/dr_ye_capability_absorption.md`;
- fixed DrYe audit reference:
  `991a1b3ab2ad985529fb645dc82f47528a2a1297`.

That work already reviewed external CIF, DMol3, CASTEP handoff, workspace read,
dashboard, GUI, modeling, and calculation ideas. This increment adds only the
independently implemented fixed-operation GUI transport described above. It
does not add a semiconductor template, CASTEP scientific feature, GUI modeling
algorithm, or unmerged PR feature.

## Read-only DrYe packaging comparison

Reference repository: <https://github.com/DrYe1109/MS-MCP>

Reference commit: `991a1b3ab2ad985529fb645dc82f47528a2a1297`

| Reference item | Observation | Decision |
|---|---|---|
| `.codex-plugin/plugin.json` | Old `ms-mcp` v0.2.0 metadata points to a different author/upstream and declares that repository's MIT license. | Rebuild from Wqeeeeeeee/ms_mcp metadata and its own LICENSE; never copy old author or license claims. |
| `.mcp.json` | Wrapped MCP map launches a repository-local batch file. | Use the current officially supported direct map and a cache-relative launcher bound to a stable user runtime. |
| `Configure-MS-MCP.bat` | `%~dp0` wrapper and Configure → Install → Test sequence are understandable to Windows users. | Preserve the UX while using Python 3.10+, explicit runner/workspace selection, safe noninteractive parameters, and no active Codex config mutation. |
| `Install-MS-MCP.bat` | Guided dependency setup. | Replace Node/npm and repo-local dependencies with a verified wheel, isolated venv, versioned runtime, staging publication, `pip check`, and runtime manifest. |
| `Test-MS-MCP.bat` | Layered installation/security/protocol checks. | Adapt to isolated `LOCALAPPDATA`/workspace, dynamic tool parity, schema/annotation checks, stdout purity, cache-copy execution, and no GUI input. |
| `Run-MS-MCP.bat` | Relative self-location and readable errors. | Validate the fixed user runtime, runner, workspace, package version, and hashes; launch only `material_studio_mcp_server.server:main`. |
| Configuration generation | Writes executable repository-local settings. | Write data-only JSON under `%LOCALAPPDATA%\MaterialsStudioMCP\config`; never write active Codex config. |
| GUI loop / `mcp_loop_gui.pl` | Polls a mutable queue and can execute arbitrary pending Perl scripts without immutable project/revision/window bindings. | Reject its arbitrary-script protocol and implementation. Independently adapt only the GUI-context polling pattern into the fixed HMAC-authenticated `import_structure` protocol bound to PID/HWND/project/revision CAS. |

## Adopt / Adapt / Reject

| Adopt | Adapt | Reject |
|---|---|---|
| Configure → Install → Test guided order | Implement with Python wheel, independent venv, versioned `%LOCALAPPDATA%` runtime, staging and integrity manifests | Node/npm rewrite or `node_modules` runtime |
| `%~dp0` self-location | Resolve only plugin/bundle-owned scripts, then load stable user configuration | Developer absolute paths or dependency on a source worktree |
| Actionable Windows errors and next steps | Keep MCP stdout JSON-RPC-only; send diagnostics to stderr/logs | Status banners or logging on MCP stdout |
| Relative bundled MCP launch | Use direct `.mcp.json`, exact allowlist, explicit `material_studio_run_script` denylist | Automatic edits to active Codex config |
| Layered smoke-test experience | Add wheel, entrypoint, protocol, schema, cache, reparse, Unicode, long-path and uninstall tests | Calling fake/protocol smoke a real Materials Studio acceptance |
| Narrow release allowlist | Build deterministic wheel/ZIP/SHA/release manifest from reviewed files | Copying DrYe metadata, license, Git history, or wholesale source |
| Human-readable configuration flow | Explicit runner/workspace choices; add a fixed signed GUI-context import loop with exact binding and no post-enqueue fallback | Arbitrary `.pl` queue, queued code execution, hardcoded drive paths, dashboard autostart or SSH config writes |

## THIRD_PARTY_NOTICES audit

No `THIRD_PARTY_NOTICES.md` is required for this increment, and one must not be
created merely because the DrYe installation experience was reviewed.

Evidence from the fixed reference SHA:

- the repositories share no commit;
- 287 current HEAD blobs and 53 reference blobs had zero identical blob hashes;
- a working-tree comparison likewise found zero identical non-build files;
- normalized exact-line and targeted semantic comparisons found only generic
  shell/PowerShell/manifest/MaterialsScript constructs and task-mandated fields;
- the current manifest, MCP map, scripts, English guide, Skill, Uninstall flow,
  wheel/venv runtime, and deterministic release builder are independent;
- the current tree copies no DrYe `.pl` file; it independently generates one
  fixed-operation loop whose queue contains authenticated data rather than
  executable script text;
- the DrYe MIT copyright is `shengh_he`, and neither that license nor its
  NOTICE/source was incorporated.

The DrYe repository was used only as a read-only installation-experience and
capability reference. If a future change copies or substantively adapts a
specific third-party file, that change must add the precise original copyright
and license notice at that time.

## Current official OpenAI documentation

Checked on 2026-08-12 using the current locally fetched official Codex manual
and installed `codex-cli 0.142.5`:

- [Package your plugin](https://developers.openai.com/plugins/build/plugins)
- [Plugin architecture](https://developers.openai.com/plugins/concepts/plugins)
- [Codex MCP configuration](https://learn.chatgpt.com/docs/mcp)

Current conclusions:

1. A plugin has `.codex-plugin/plugin.json`; `skills/` and `.mcp.json` live at
   the plugin root, not inside `.codex-plugin/`.
2. `skills` and `mcpServers` paths start with `./`, resolve relative to the
   plugin root, and must remain inside it.
3. Bundled `.mcp.json` supports either a direct server map or a wrapped
   `mcp_servers` object. This plugin deliberately uses the direct map.
4. A local marketplace is `.agents/plugins/marketplace.json`; its local source
   is `./plugins/materials-studio-mcp` with explicit installation,
   authentication, and category policy.
5. `codex plugin marketplace add Wqeeeeeeee/ms_mcp --ref <tag>` is the supported
   Git marketplace command form.
6. Installed local plugins run from
   `~/.codex/plugins/cache/<marketplace>/<plugin>/<version-or-local>/`, not the
   marketplace development directory.
7. ChatGPT Desktop Work/Codex and Codex CLI support plugin browsing. The Codex
   IDE Extension currently does not support plugin installation; it can use the
   shared local STDIO MCP configuration fallback.
8. ChatGPT Desktop, Codex CLI, and the IDE Extension share local MCP config.
   STDIO supports `command`, `args`, `cwd`, environment and tool policy.
9. Plugin install state may touch Codex configuration when a user explicitly
   installs/enables a plugin, but Configure/Install/Test scripts must not edit
   active Codex configuration themselves.
10. `license` is optional in the general manifest schema, but this licensed
    repository supplies the accurate value `MIT`.

The bundled MCP definition is therefore:

```json
{
  "materials-studio": {
    "command": "cmd.exe",
    "args": ["/d", "/c", "Run-MS-MCP.bat"],
    "cwd": ".",
    "env": {"MATERIAL_STUDIO_MCP_PLUGIN_MODE": "1"},
    "enabled_tools": ["<derived existing safe allowlist>"],
    "disabled_tools": ["material_studio_run_script"]
  }
}
```

The bundled plugin-creator validator bundled with this host still expects an
older wrapped key and rejects the direct map. The current official manual and
the actual Codex host accept the direct map. Final acceptance therefore records
both the stale-validator mismatch and an isolated real-host load/cache test.

## Old artifact audit and disposal requirement

The merged `0.4.0` artifacts were inspected before replacement:

| File | SHA-256 |
|---|---|
| `materials_studio_mcp-0.4.0-py3-none-any.whl` | `4a22e64f4cb5b4600dcf2b35234883e6eb4ae73939b26f17400557164bcc1604` |
| `materials-studio-mcp-plugin-0.4.0-windows.zip` | `10ccb94f81fc379e83b948a67666e33e4854dc93255d0b84ec365bcd8e5e59ea` |
| `release-manifest.json` | `974ecd486a42e52cc5134cba8aee0f1fcbb7e17192fc1ae5003580e60368f39d` |
| `SHA256SUMS.txt` | `931bfe0be6bc27ad796e1ab091e7da5b5eb2172de88ceba204b28d63a2dd8e7f` |

Those files bind the immutable `0.4.0` runtime and do not contain the signed
GUI-loop implementation. They must not be renamed or reused as `0.5.0`.
Intermediate `0.5.0` artifacts built while source was still changing are also
not release candidates. Final artifacts must be rebuilt from the clean final
commit and their source/wheel/protocol parity rechecked before publication.

## Non-negotiable safety boundaries

- Do not rewrite the Python MCP server as Node.
- Do not add an arbitrary GUI script queue. A GUI polling loop is acceptable
  only when it implements the reviewed fixed HMAC-authenticated
  `import_structure` protocol and exact PID/HWND/project/revision CAS contract.
- Do not enable arbitrary MaterialsScript by default.
- Do not weaken preview-first, revision, execution-attempt, runner, exact-window
  GUI, calculation, provenance, lock, or evidence-integrity gates.
- Prefer read-only status and preflight; prefer
  `material_studio_live_modeling_request`; default to preview.
- Revision creation, runner execution, GUI input and calculations require
  explicit confirmation through existing gates.
- A screenshot cannot establish structural or scientific validity.
- Unsupported material/scenario requests fail closed without substituting a
  nearby template.
- Configure, Install and Test do not modify active Codex config, launch
  Materials Studio, run CASTEP/Forcite/DMol3, or send GUI input.
- Uninstall preserves workspace, revisions, models and calculation results by
  default and removes only manifest-recorded managed runtime/config paths.

## Phase 0 outcome

Proceed with the isolated `0.5.0` GUI-loop increment. After the final source and
audit commit, rebuild every artifact from that clean tree, run the complete
source/wheel/protocol/installer/cache acceptance matrix, verify active Codex
config and workspace preservation, perform an independent review, and report
real Materials Studio and real CASTEP evidence exactly as observed rather than
inferring it from protocol or unit tests.
