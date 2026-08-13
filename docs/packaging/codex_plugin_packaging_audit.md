# Codex plugin packaging audit

Goal ID: `CODEX-MS-PLUGIN-PACKAGING-V1`

Audit date: 2026-08-12 (Asia/Shanghai)

This is the authoritative Phase 0 re-audit after PR #156 merged. It supersedes
the pre-license audit recorded on the original branch base. The audit was
completed read-only before rebuilding any release artifact. No real Codex
configuration, Materials Studio process, GUI, calculation, workspace model, or
user data was changed.

## Decision

Packaging may proceed from the exact current `origin/main` baseline:

- base SHA: `c04e6dc66cae52ab64a0af40b19596249e976b6b`;
- base package version: `0.3.0`;
- repository license: SPDX `MIT`;
- copyright: `Copyright (c) 2026 Xu kaidong`;
- PR #156 Windows child-process stdin fix: merged and verified;
- DrYe reference: read-only only, with no commit-history import and no detected
  copied source or documentation requiring a third-party notice;
- Real Materials Studio: `NOT_RUN`;
- Real CASTEP: `NOT_RUN`.

The packaging branch uses new package/plugin/runtime version `0.4.0` because an immutable runtime is
addressed by package version. Reusing `0.3.0` for a different wheel would make
an already installed internal-preview runtime correctly fail the integrity
gate rather than upgrade. This is a packaging/runtime version change, not a
new material, CASTEP, GUI-modeling, or calculation capability.

## Git and source baseline

Read-only commands and observations:

1. `git fetch origin --prune` advanced `origin/main` from
   `8920ae5c7e44ec947e08be9ba52ff9b2279735ae` to
   `c04e6dc66cae52ab64a0af40b19596249e976b6b`.
2. `c04e6dc6` is `Merge pull request #156 from
   msm123MSM/codex/fix-windows-mcp-subprocess-stdin`.
3. Its first parent, `8920ae5c`, adds the repository MIT license.
4. Its second-parent feature commit is
   `c26d98fa255402e5952ac3258e0faa7309353927`.
5. `origin/main:src/material_studio_mcp_server/python_runtime.py` passes
   `stdin=subprocess.DEVNULL` to the isolated `subprocess.run` runtime probe.
6. `origin/main:tests/test_python_runtime_contract.py` asserts that exact
   `subprocess.DEVNULL` binding.
7. The packaging work is isolated on
   `codex/codex-plugin-windows-installer-v1`; it was not pushed to `main`.
8. No open Draft PR, including the former #156 branch, was cherry-picked or
   stacked. The packaging checkpoint was rebased normally after #156 merged.

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

The exact base was imported as
`material_studio_mcp_server.server.mcp`, and `mcp.list_tools()` was awaited.
An independent AST scan of top-level `@mcp.tool(...)` registrations produced
the same set. Both methods returned 49 tools; tests compare the source checkout
and clean wheel dynamically and never hardcode the historical count.

- registry-order newline-list SHA-256:
  `9a1a88c0b344fd50b6e5587bea186de0f731c98ed0a8bdbd147313262535d366`
- sorted newline-list SHA-256:
  `35dbc16d44f01b295bcddaaef53803781e2eeaa4bf65bcf1a67b2f4bf9f5d2f5`

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

`src/material_studio_mcp_server/codex_config.py` defines the existing 44-tool
safe allowlist and the explicit denylist
`["material_studio_run_script"]`. Four other compatibility tools are outside
the recommended allowlist. Plugin mode adds a server-side fail-closed guard for
the arbitrary-script tool without changing the public wheel tool set.

## Already merged DrYe capability absorption

Main already contains the separately reviewed capability absorption work:

- merge commit: `6d9149dcf35e73cb44fe9d393677f4e3a7593ac2`;
- integration parent: `6d593a5e3ac40c368bfa8cfb15886584dfa70b2e`;
- feature commit: `e27176e70ad429b300cbac6dada149f8ef535cbf`;
- detailed matrix: `docs/dr_ye_capability_absorption.md`;
- fixed DrYe audit reference:
  `991a1b3ab2ad985529fb645dc82f47528a2a1297`.

That work already reviewed external CIF, DMol3, CASTEP handoff, workspace read,
dashboard, GUI, modeling, and calculation ideas. This increment packages the
current main implementation only. It does not add a semiconductor template,
CASTEP scientific feature, GUI modeling algorithm, or unmerged PR feature.

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
| GUI loop / `mcp_loop_gui.pl` | Polls a mutable queue and can execute arbitrary pending Perl scripts without immutable project/revision/window bindings. | Reject completely. No arbitrary GUI script queue or persistent mutation loop is shipped. |

## Adopt / Adapt / Reject

| Adopt | Adapt | Reject |
|---|---|---|
| Configure → Install → Test guided order | Implement with Python wheel, independent venv, versioned `%LOCALAPPDATA%` runtime, staging and integrity manifests | Node/npm rewrite or `node_modules` runtime |
| `%~dp0` self-location | Resolve only plugin/bundle-owned scripts, then load stable user configuration | Developer absolute paths or dependency on a source worktree |
| Actionable Windows errors and next steps | Keep MCP stdout JSON-RPC-only; send diagnostics to stderr/logs | Status banners or logging on MCP stdout |
| Relative bundled MCP launch | Use direct `.mcp.json`, exact allowlist, explicit `material_studio_run_script` denylist | Automatic edits to active Codex config |
| Layered smoke-test experience | Add wheel, entrypoint, protocol, schema, cache, reparse, Unicode, long-path and uninstall tests | Calling fake/protocol smoke a real Materials Studio acceptance |
| Narrow release allowlist | Build deterministic wheel/ZIP/SHA/release manifest from reviewed files | Copying DrYe metadata, license, Git history, or wholesale source |
| Human-readable configuration flow | Explicit runner/workspace choices and safe noninteractive arguments | Arbitrary `.pl` queue, GUI loop, hardcoded drive paths, dashboard autostart or SSH config writes |

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
- the current tree ships no `.pl` file or GUI queue;
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

Before rebase, the existing internal-preview artifacts were inspected without
deleting them:

| File | SHA-256 |
|---|---|
| `materials_studio_mcp-0.3.0-py3-none-any.whl` | `df24e5784ed7b6dc1f2772d18bf9fb4001d879a3818e4dc08ede890c4607b7f2` |
| `materials-studio-mcp-plugin-0.3.0-windows.zip` | `8e92d55c752acae26e35542e7787e3f6499eff81d8ae04016849ce3131a8ff3e` |
| `release-manifest.json` | `e633bd92939420b864e48edc69ccde085cc947df430ec3f94ea6505f90ed2133` |
| `SHA256SUMS.txt` | `a4ce43c1b03d26861de282a61287d772237e5837d8e32c4245e2496c5459509a` |

Those files predate PR #156, omit the new repository license metadata, bind
the old base SHA, and are not release candidates. They must be removed and
must never have their hashes reused. Final artifacts are rebuilt from a clean
committed tree after the version/license/staging migration.

## Non-negotiable safety boundaries

- Do not rewrite the Python MCP server as Node.
- Do not add an arbitrary GUI script queue or GUI polling loop.
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

Proceed with the isolated packaging increment. Before push, rebuild every
artifact from a clean tree, run the complete source/wheel/protocol/installer/
cache acceptance matrix, verify active Codex config and workspace preservation,
perform an independent review, and report real Materials Studio and real CASTEP
as `NOT_RUN`.
