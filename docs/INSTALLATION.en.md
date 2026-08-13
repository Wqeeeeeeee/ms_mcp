# Materials Studio MCP Windows Installation

The repository is licensed under the **MIT License**, Copyright (c) 2026
Xu kaidong. The release manifest records the declared MIT license and a cleared
repository-license release gate.

This is an independent project, not an official BIOVIA or Dassault Systèmes
product. It does not bundle Materials Studio, a Materials Studio commercial
license, or unauthorized BIOVIA/Dassault Systèmes trademark icons. Real use
requires a separately licensed local Materials Studio installation.

This guide installs the existing Python Materials Studio MCP server as a
stable, versioned local runtime and installs its Codex plugin from a repository
marketplace source. Manual STDIO MCP registration remains a compatibility
fallback.

Every `<...>` token below is a placeholder that must be replaced before a
command is run. Do not paste angle brackets literally into `cmd.exe`, where
they may be interpreted as redirection.

## 1. Requirements

- Windows 10 or Windows 11.
- 64-bit Python 3.10 or newer, available through `py -3` or `python`.
- A separately licensed BIOVIA Materials Studio 2020 or 20.1 installation.
- The Materials Studio **Scripting** component, including
  `RunMatScript.bat`.
- For plugin installation, a ChatGPT/Codex desktop build with the Plugins
  Directory or Codex CLI. The Codex IDE Extension currently does not install
  plugins; use the shared local STDIO MCP configuration fallback for the IDE.
- Enough local disk space for a versioned virtual environment and the chosen
  workspace.
- Access to the configured Python package index during `Install` (or an
  administrator-preloaded pip cache); the bundle includes the project wheel,
  not every third-party dependency wheel.

### Install and verify Python

If Python 3.10+ is not already installed, download a current 64-bit installer
from the official [Python releases for Windows](https://www.python.org/downloads/windows/).
A per-user installation is sufficient. Keep the Windows `py` launcher, or
select **Add python.exe to PATH** in the installer. Open a new Command Prompt
after installation and verify both the version and resolved executable:

```bat
py -3 --version
py -3 -c "import sys; print(sys.executable); assert sys.version_info >= (3, 10)"
```

If `py` is unavailable, run the same checks with `python`. Configure also
accepts an explicit ordinary-file path such as
`Configure-MS-MCP.bat -PythonCommand "C:\Python311\python.exe"`. WindowsApps
execution aliases and Python paths reached through symlinks or junctions are
rejected; install Python to a normal local directory instead of bypassing that
gate.

Codex CLI is useful for marketplace installation but is not a hard dependency
of the MCP server itself.

## 2. Download and verify the release ZIP

Obtain these files through a repository-owner-authorized release channel:

```text
materials-studio-mcp-plugin-<version>-windows.zip
SHA256SUMS.txt
release-manifest.json
```

The manifest must declare the repository license and cleared release gate:

```json
{
  "repository_license_status": "declared",
  "repository_license_spdx": "MIT",
  "repository_copyright": "Copyright (c) 2026 Xu kaidong",
  "public_distribution_ready": true,
  "release_blockers": []
}
```

`public_distribution_ready=true` records the repository-license and packaging
gate; it is not evidence that this version has already been published to a
GitHub Release, PyPI, or a public universal marketplace. The ZIP and wheel must
contain the repository `LICENSE`.

Compare the ZIP digest with `SHA256SUMS.txt`:

```powershell
Get-FileHash -Algorithm SHA256 .\materials-studio-mcp-plugin-<version>-windows.zip
```

Extract the ZIP to a user-writable local directory. Spaces and non-ASCII path
characters are supported. If Windows long-path policy causes a failure, choose
a shorter extraction path. Do not extract into the Materials Studio program
directory, a Codex plugin cache directory, or an existing model workspace.

## 3. Configure, install, and test

Open a normal Command Prompt in the extracted directory and run:

```bat
Configure-MS-MCP.bat
Install-MS-MCP.bat
Test-MS-MCP.bat
```

Do not use an editable install. The installer creates an independent venv and
publishes a stable runtime under:

```text
%LOCALAPPDATA%\MaterialsStudioMCP\runtimes\<version>\
```

Configuration, logs, and user model data stay outside the plugin cache and
outside any development worktree:

```text
%LOCALAPPDATA%\MaterialsStudioMCP\config\
%LOCALAPPDATA%\MaterialsStudioMCP\logs\
<the workspace explicitly selected during Configure>
```

### Configure-MS-MCP.bat

Configure checks Windows, Python 3.10+, Materials Studio 2020/20.1, the
Scripting runner, Codex CLI availability, and the workspace. It writes only
user-local configuration. It does not change active Codex configuration,
install packages, or start Materials Studio.

Automatic runner discovery is bounded to reviewed common install locations.
If discovery fails, provide the exact runner and workspace explicitly:

```bat
Configure-MS-MCP.bat -Runner "<full-path-to-RunMatScript.bat>" -Workspace "<workspace-path>" -MaterialsStudioVersion 20.1
```

`-PythonCommand` may select a reviewed Python command. Automation can use
`-NonInteractive`; use `-Force` only after reviewing an intentional
configuration change. Do not pass a directory, shortcut, symlink, or reparse
point as the runner. Keep the workspace outside runtime, cache, and application
directories.

### Install-MS-MCP.bat

Install verifies the wheel SHA-256, creates a new versioned venv, installs from
the wheel (never `pip install -e`), runs `pip check`, validates console
entrypoints and version agreement, and publishes a runtime manifest. It keeps
older runtimes and does not modify workspace data, active Codex configuration,
Materials Studio, CASTEP, DMol3, or Forcite.

The release bundle is auto-discovered. An explicit audited wheel invocation is:

```bat
Install-MS-MCP.bat -WheelPath ".\materials_studio_mcp-<version>-py3-none-any.whl" -WheelSha256 <64-hex-sha256>
```

`-NonInteractive` is available for isolated automation. An existing runtime is
reused only if its complete manifest and content still match; it is never
destructively overwritten.

### Test-MS-MCP.bat

The default test is safe and uses an isolated workspace. It validates config,
runner path, imports, `compileall`, package and entrypoint identity, plugin and
marketplace JSON, tool discovery, schemas, annotations, protocol transport,
stdout purity, and the default denial of `material_studio_run_script`. It does
not run a Materials Studio calculation and does not send GUI input.

Protocol smoke, mocks, and fake GUI evidence are not real acceptance:

```text
Real Materials Studio: NOT_RUN
Real CASTEP: NOT_RUN
```

`Test-MS-MCP.bat --real-ms` is a separate opt-in. It asks for confirmation
again and is allowed only after the current task grants real-MS authorization
and the user manually opens exactly one Materials Studio window. In
noninteractive controlled acceptance, both `-ConfirmRealMS` and
`-NonInteractive` are required. See the
[real acceptance checklist](REAL_MS_ACCEPTANCE.zh-CN.md).

## 4. Install from the local Codex marketplace

Add the repository marketplace at the exact reviewed release tag:

```bat
codex plugin marketplace add Wqeeeeeeee/ms_mcp --ref <tag>
```

This command adds the marketplace source; it does not install the plugin.
Record the marketplace name reported by Codex, then run:

```bat
codex plugin add materials-studio-mcp --marketplace <marketplaceName> --json
```

Record `installedPath` from the JSON result. It must point into the Codex
plugin cache, not a development checkout. On a supported ChatGPT/Codex desktop
surface, open **Plugins Directory**, choose the local/repository
marketplace, and install **Materials Studio MCP** there instead.

Fully restart ChatGPT/Codex, or start a new Codex CLI session. `/plugins` opens
the CLI plugin browser.

### Prove cache-copy independence

1. Save `installedPath` from `codex plugin add --json`.
2. End the current MCP session.
3. Temporarily rename or move the original source checkout; do not delete it.
4. Start a new Codex session and reconnect `materials-studio`.
5. The cached `Run-MS-MCP.bat` must still resolve the installed runtime through
   `%LOCALAPPDATA%\MaterialsStudioMCP\config\`.
6. Restore the source checkout after the test.

This proves the installed plugin does not execute from the development
worktree. It does not change or remove the workspace.

## 5. Check MCP discovery

Use `/mcp` in Codex CLI or the MCP Servers page in the desktop application.
Confirm that `materials-studio` connects and exposes the reviewed tools. If
`mcp_server_restart_required` appears, restart the MCP session or application;
do not reinstall Materials Studio.

The first request must be read-only:

```text
Check the local Materials Studio MCP runtime manifest, runner, workspace, and GUI status.
Run status and preflight only; do not create a revision, invoke the runner, or send GUI input.
```

Status and preflight tools precede modeling. Prefer
`material_studio_live_modeling_request` for structured orchestration.

## 6. First modeling preview

```text
Preview a small silicon diamond structure with material_studio_live_modeling_request.
Keep execution_mode=preview; do not invoke the runner and do not open or modify the GUI.
```

Review the exact spec, planned project/revision, validation, blockers, and
`next_action_plan`. Unsupported materials and scenarios must fail closed; do
not accept substitution with a nearby template.

Keep these claims separate:

- **structure valid**: schema, geometry, and structure checks passed;
- **model normal**: domain diagnostics for the bound revision passed;
- **live GUI normal**: the one bound PID/HWND and visible-model evidence passed;
- **calculation ready**: runner, structure, settings, and calculation gates passed;
- **scientifically verified**: sufficient convergence and scientific evidence
  support the conclusion.

A screenshot alone proves none of the structural or scientific claims.

## 7. First explicitly confirmed hot-load

1. Manually open one Materials Studio top-level window and leave only that one
   window open.
2. Run read-only GUI status/preflight again and verify PID, HWND, title,
   workspace provenance, project, and revision.
3. Explicitly authorize execution of the reviewed plan and hot-loading into
   that exact window.
4. Use the preview-returned execute payload without removing its bindings.

A preview is never execution approval. Do not auto-retry a failed runner or GUI
action, and do not launch a second Materials Studio process. Return the
structured blocker and exact next action.

## 8. Fit-to-View and multi-view diagnostics

After a successful same-revision hot-load, preview
`material_studio_gui_fit_to_view`; execute it only after explicit confirmation.
Then export reviewed `front`, `top`, and `isometric` diagnostics as appropriate.
View replay must use the current recipe and exact window/revision evidence.
Never use blind coordinates, and never replace structure-SHA verification with
a screenshot.

## 9. Revisions, edits, and rollback

Preview modifications against the exact current project/revision before
execution. Inspect history read-only with `material_studio_project_history`.
Preview rollback, review the target and invariants, then explicitly execute.
Workspace revisions, results, and user models survive updates and the default
uninstall.

## 10. CASTEP, Forcite, and DMol3

Every calculation is a separate two-step operation:

1. preview and review structure, settings, runner, license/queue, script, and
   safety gates;
2. explicitly confirm execution in a second request.

Example:

```text
Preview CASTEP Energy for the current crystal with execution_mode=preview.
Do not execute, create a run directory, or change the GUI.
```

Hot-loading a structure never authorizes CASTEP, Forcite, or DMol3. Backend
completion is not scientific convergence; inspect the result receipt and
convergence evidence independently.

## 11. Update

1. Obtain a new ZIP, checksum file, and manifest through a
   repository-owner-authorized release channel.
2. Verify version, base/reference SHAs, MIT license metadata, and hashes.
3. Extract to a new directory and run Configure, Install, and Test.
4. Keep the older immutable runtime; do not overwrite it.
5. upgrade/reinstall the marketplace plugin and start a new session.
6. Perform a fresh read-only preflight before any preview.

Follow the repository's announced release channel for updates. This guide does
not claim that a GitHub Release, PyPI distribution, or public universal
marketplace listing already exists.

## 12. Uninstall and recovery

Preview the exact managed paths first:

```bat
Uninstall-MS-MCP.bat --dry-run
```

After review, run:

```bat
Uninstall-MS-MCP.bat
```

The default uninstall removes only the managed runtime and plugin configuration
recorded by the install manifest. It preserves workspaces, revisions,
calculation results, and user models. It does not remove Materials Studio or
edit unrelated Codex configuration. Noninteractive uninstall requires both
`-Confirm` and `-NonInteractive`.

Remove the plugin separately through Plugins Directory or:

```bat
codex plugin remove materials-studio-mcp --marketplace <marketplaceName> --json
```

To recover, keep the workspace, rerun Configure → Install → Test, reinstall the
plugin, restart Codex, and perform a read-only preflight.

## 13. Manual local STDIO MCP fallback

Plugin installation is recommended. For the IDE Extension, or when the
marketplace is unavailable, use shared local MCP configuration. Bundle-only
users should take `installedPath` from `codex plugin add --json`, substitute it
for `PLUGIN_CACHE_PATH`, and manually review and merge this minimal binding
(the single-quoted values are TOML literal strings):

```toml
[mcp_servers.materials_studio]
command = "cmd.exe"
args = ["/d", "/c", 'PLUGIN_CACHE_PATH\Run-MS-MCP.bat']
cwd = 'PLUGIN_CACHE_PATH'
env = { MATERIAL_STUDIO_MCP_PLUGIN_MODE = "1" }
default_tools_approval_mode = "prompt"
enabled_tools = [
  "material_studio_get_status",
  "material_studio_live_capabilities",
  "material_studio_live_session_preflight",
  "material_studio_model_validate",
  "material_studio_model_create_from_spec",
  "material_studio_model_modify_with_patch",
  "material_studio_model_preview_script",
  "material_studio_model_get_current",
  "material_studio_live_modeling_request",
  "material_studio_live_project_status",
  "material_studio_live_watchdog_status",
  "material_studio_model_export_view_audit",
  "material_studio_model_export_view_bundle",
  "material_studio_live_update_with_patch",
  "material_studio_project_history",
  "material_studio_project_rollback",
  "material_studio_project_reconcile_dopant_metadata",
  "material_studio_gui_status",
  "material_studio_gui_loop_status",
  "material_studio_gui_loop_prepare",
  "material_studio_gui_loop_stop",
  "material_studio_gui_launch",
  "material_studio_gui_activate",
  "material_studio_gui_snapshot",
  "material_studio_gui_open_structure",
  "material_studio_gui_apply_current_revision",
  "material_studio_gui_fit_to_view",
  "material_studio_gui_record_visual_confirmation",
  "material_studio_gui_copy_script_assist",
  "material_studio_gui_prepare_view_replay",
  "material_studio_gui_execute_view_replay",
  "material_studio_gui_record_view_replay",
  "material_studio_structure_summary",
  "material_studio_import_export",
  "material_studio_forcite_geometry_optimization",
  "material_studio_castep_energy_script",
  "material_studio_castep_relax_current",
  "material_studio_castep_run_current",
  "material_studio_dmol3_relax_current",
  "material_studio_cif_source_search",
  "material_studio_cif_source_ingest",
  "material_studio_remote_castep_prepare",
  "material_studio_remote_job_record",
  "material_studio_remote_job_status",
  "material_studio_workspace_snapshot",
  "material_studio_workspace_artifact_read",
  "material_studio_list_script_templates",
]
disabled_tools = ["material_studio_run_script"]
```

This keeps the stable cache launcher and `%LOCALAPPDATA%` runtime independent
of a development worktree. Merge the table without replacing the whole active
`config.toml` or removing unrelated servers, authentication, or trusted-project
settings. The `enabled_tools` array must remain equivalent in content and
order to the plugin cache's `.mcp.json`/`SAFE_ENABLED_TOOLS` policy; do not
omit it. Restart Codex and repeat read-only preflight.

The existing `register_codex.py`, `ms-mcp-config-register`, and
`.codex/config.toml.example` are legacy fallbacks for a **complete source
checkout or managed-source runtime**. The registrar verifies repository-root
`run_server.py`, so it cannot register the wheel-only release runtime. Obtain
the same reviewed exact source ref, review the fingerprint-bound preview,
`registration_plan_id`, allowlist, and denylist, then explicitly apply it. Do
not copy registration files from another branch or the DrYe reference.

## 14. Troubleshooting checklist

### `RunMatScript.bat` is not found

Modify the Materials Studio 2020/20.1 installation and add the **Scripting**
component. Then rerun Configure. If discovery still fails, locate the exact
reviewed `RunMatScript.bat` and pass it with `-Runner`; do not substitute a
different batch file or an unreviewed release.

### Python is missing or below 3.10

Open a new Command Prompt and repeat the version/executable checks in section
1. Pass the verified absolute executable with `-PythonCommand` if needed. Do
not point Configure at a WindowsApps alias, symlink, junction, or command string
containing shell operators.

### The plugin cache path is unexpected

Use `installedPath` from `codex plugin add ... --json` or `/plugins`. A cache
path is versioned and may contain spaces. Do not edit it or store user config in
it. Remove/reinstall the plugin from the reviewed ref if the cache copy is
stale; the stable runtime and workspace remain under `%LOCALAPPDATA%`.

### Multiple Materials Studio windows or a background/minimized target

Close unrelated Materials Studio windows until the intended wrapper is unique.
Use read-only GUI status, then activation with a snapshot if the exact target is
hidden, minimized, or not foreground. Never let the workflow launch a second
process merely to satisfy preflight, and never send input while window identity
is ambiguous.

### `mcp_server_restart_required`

The loaded MCP process no longer matches its installed source snapshot. Restart
the MCP session or ChatGPT/Codex application, then repeat read-only status and
preflight. Do not reinstall Materials Studio and do not retry a side-effecting
operation from stale state.

### `workspace provenance mismatch`

Stop before any revision write. Inspect the returned visible workspace,
project, revision, and `recommended_working_dir`; rerun read-only preflight in
that directory and then provide an explicit project ID. Never auto-adopt or
rewrite the other workspace.

### Tool allowlist drift

The plugin cache `.mcp.json` `enabled_tools` must exactly match the installed
`SAFE_ENABLED_TOOLS` policy, and `material_studio_run_script` must remain in
`disabled_tools`. Stop side-effecting work on any mismatch, reinstall the
reviewed plugin ref, and rerun Test. Do not replace the whole active Codex
configuration.

### MCP stdout contains banners, warnings, or logs

STDIO stdout is reserved for JSON-RPC framing. Launcher diagnostics belong on
stderr or in `%LOCALAPPDATA%\MaterialsStudioMCP\logs`. Run Test again and do not
add `echo`, profile output, or debug prints to the launcher. A non-protocol byte
on stdout is a failure.

### Paths contain spaces or Chinese characters

These paths are supported. Pass each path as one quoted argument and never add
`&`, a pipe, or redirection. If a third-party Python dependency still mishandles
the path, reproduce with Test and use a shorter ordinary local path; do not
weaken traversal, reparse, or injection checks.

### Windows long paths

Install fails closed before publishing a runtime when the projected executable
or package path approaches the conservative Windows process-path limit. Choose
a shorter extraction/local-data root and rerun Configure → Install → Test.
Enabling the OS long-path policy is an administrator-reviewed option, not an
installer side effect.

The Chinese deep-dive pages remain available in
[Codex plugin details](CODEX_PLUGIN.zh-CN.md) and
[troubleshooting](TROUBLESHOOTING.zh-CN.md), but the checklist above is complete
for a first English-language installation.

## 15. Acceptance status

This phase authorizes offline safety tests and protocol smoke only:

```text
Real Materials Studio: NOT_RUN
Real CASTEP: NOT_RUN
```

Mocks, fake GUI evidence, imports, and protocol smoke must not be reported as
real acceptance.
