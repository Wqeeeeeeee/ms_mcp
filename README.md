# Materials Studio MCP

Local, preview-first MCP workflows for BIOVIA Materials Studio 2020/20.1 on
Windows. The Python server provides structured modeling, immutable revisions,
runner execution, exact-window GUI synchronization, diagnostics, and guarded
CASTEP/Forcite/DMol3 workflows.

This repository is licensed under the **MIT License**, Copyright (c) 2026
Xu kaidong. See [LICENSE](LICENSE).

This is an independent project and is not an official BIOVIA or Dassault
Systèmes product. It does not bundle Materials Studio, a Materials Studio
commercial license, or unauthorized BIOVIA/Dassault Systèmes trademark icons.
Real use requires a separately licensed local Materials Studio installation.

## Install the Windows package

Obtain the versioned Windows ZIP and matching `SHA256SUMS.txt` from a
repository-owner-authorized release channel, verify it, extract it, and run
from a normal Command Prompt:

```bat
Configure-MS-MCP.bat
Install-MS-MCP.bat
Test-MS-MCP.bat
```

The installer creates a versioned runtime under
`%LOCALAPPDATA%\MaterialsStudioMCP\runtimes\<version>\`; it does not use an
editable install or depend on the source checkout. Configuration and workspace
data remain outside the Codex plugin cache.

- [中文安装教程](docs/INSTALLATION.zh-CN.md)
- [English installation guide](docs/INSTALLATION.en.md)
- [Codex 插件与本地 Marketplace](docs/CODEX_PLUGIN.zh-CN.md)
- [真实 Materials Studio 验收](docs/REAL_MS_ACCEPTANCE.zh-CN.md)
- [中文故障排查](docs/TROUBLESHOOTING.zh-CN.md)
- [现有建模与安全工作流手册](https://github.com/Wqeeeeeeee/ms_mcp/blob/main/docs/USER_GUIDE.zh-CN.md)

Plugin installation is supported from the Plugins Directory in ChatGPT
Desktop/Codex and from Codex CLI. Add the repository marketplace with:

```bat
codex plugin marketplace add Wqeeeeeeee/ms_mcp --ref <tag>
```

The Codex IDE Extension currently does not support plugin installation; use the
documented shared local STDIO MCP configuration fallback there. This README
does not claim that artifacts are already published to GitHub Releases, PyPI,
or a public universal marketplace.

## Safety boundary

- Read-only status and preflight come first; modeling defaults to preview.
- Creating a revision, invoking the runner, controlling the GUI, or starting a
  calculation requires explicit confirmation through the existing tool gates.
- `material_studio_run_script` is disabled by default.
- A screenshot is not structural or scientific validation.
- Unsupported materials and workflows fail closed; no nearby template is
  silently substituted.

- Real Materials Studio: **NOT_RUN**
- Real CASTEP: **NOT_RUN**

## Optional GUI-loop hot-load

The same-window GUI path supports a fixed, signed hot-load loop through:

- `material_studio_gui_loop_prepare`
- `material_studio_gui_loop_status`
- `material_studio_gui_loop_stop`
- `material_studio_gui_open_structure(hotload_transport="auto|loop|dialog")`

`auto` uses the loop only when its signed heartbeat, PID, window handle,
project, current revision, and active document all match. If the loop is not
ready, fallback to the existing File/Open path is permitted only before a job
is enqueued. A queued job is never retried through the dialog because the GUI
import may already have occurred.

Preparation writes a fixed `import_structure` MaterialsScript loop; it does
not start it or send GUI input. Run the returned script once from Script
Library/User Menu in the exact verified Materials Studio window, then wait for
`loop_ready=true`. The queue accepts signed data envelopes only, never arbitrary
Perl. See [the GUI-loop guide](docs/gui_loop.md) and
[the GUI-control contract](docs/gui_control.md).

```text
MATERIAL_STUDIO_GUI_HOTLOAD_TRANSPORT=auto
MATERIAL_STUDIO_GUI_LOOP_TIMEOUT_SECONDS=45
MATERIAL_STUDIO_GUI_LOOP_HEARTBEAT_TTL_SECONDS=10
```

For source-development registration compatibility, see
[the Codex setup guide](https://github.com/Wqeeeeeeee/ms_mcp/blob/main/docs/codex_setup.md),
`register_codex.py`, `ms-mcp-config-register`, and
`.codex/config.toml.example`. Those registrars require a complete
source/managed-source runtime; bundle-only IDE users should manually bind the
cached `Run-MS-MCP.bat` as documented. Plugin installation is the recommended
end-user path.
