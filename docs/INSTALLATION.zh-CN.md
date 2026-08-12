# Materials Studio MCP Windows 安装教程

本仓库采用 **MIT License**，Copyright (c) 2026 Xu kaidong。发行
manifest 会记录已声明的 MIT 许可证和已解除的仓库许可证发行阻断。

本项目是独立项目，不是 BIOVIA 或 Dassault Systèmes 官方产品。
本包不附带 Materials Studio、Materials Studio 商业许可证或未经授权的
BIOVIA/Dassault Systèmes 商标图标；真实运行必须另行获得本地 Materials
Studio 合法授权。

本教程面向一台安装了 BIOVIA Materials Studio 2020 或 20.1 的 Windows
计算机。推荐路径是“配置本机 → 安装稳定 runtime → 安全测试 → 从本地 Codex
Marketplace 安装插件”。手动 `config.toml` 注册仅作为兼容 fallback。

本文命令中的 `<...>` 是必须替换的占位符，不能原样粘贴到 `cmd.exe`；尖括号在
Windows shell 中可能被解释为重定向。

## 1. 系统要求

- Windows 10 或 Windows 11；建议安装当前系统安全更新。
- 64 位 Python 3.10 或更高版本，并能在命令提示符中运行 `py -3` 或
  `python`。安装 Python 时建议勾选 “Add Python to PATH”。
- 已获得合法许可的 Materials Studio 2020/20.1。
- 安装 Materials Studio 时包含 **Scripting** 组件；本项目需要该组件中的
  `RunMatScript.bat`，不使用 `MaterialsStudio.Application` COM 替代它。
- 若使用插件安装：支持 Plugins Directory 的 ChatGPT/Codex 桌面版本，或
  Codex CLI。Codex IDE Extension 当前不支持安装插件，但可以使用同一份本地
  STDIO MCP 配置。
- 安装、runtime、workspace 所在磁盘应有足够空间。计算产生的数据不会在卸载
  runtime 时自动删除。
- `Install` 时需要能够访问当前配置的 Python 包索引，或由管理员预先填充 pip
  缓存；发布包包含本项目 wheel，但不内置所有第三方依赖 wheel。

### 安装并验证 Python

如果尚未安装 Python 3.10+，从 Python 官方
[Windows 下载页](https://www.python.org/downloads/windows/)取得当前 64 位安装器。
按当前用户安装即可；保留 Windows `py` launcher，或在安装器中勾选
**Add python.exe to PATH**。安装完成后打开新的 `cmd.exe`，检查版本和实际解释器：

```bat
py -3 --version
py -3 -c "import sys; print(sys.executable); assert sys.version_info >= (3, 10)"
```

如果没有 `py`，把上面两条命令中的 `py -3` 换成 `python`。也可以向 Configure
传入经过核对的普通文件绝对路径，例如：

```bat
Configure-MS-MCP.bat -PythonCommand "C:\Python311\python.exe"
```

不要使用 WindowsApps execution alias，也不要让 Python 路径经过 symlink 或
junction；安装器会拒绝这些 reparse 路径，而不是绕过完整性门禁。

Codex CLI 不是启动 MCP Server 的硬依赖；只使用支持本地 STDIO MCP 的桌面
客户端时，也可以完成 Configure、Install 和 Test。

## 2. 下载并校验发行包

从仓库所有者授权的发行渠道取得以下文件；不要从未知镜像下载：

```text
materials-studio-mcp-plugin-<version>-windows.zip
SHA256SUMS.txt
release-manifest.json
```

发布清单必须包含：

```json
{
  "repository_license_status": "declared",
  "repository_license_spdx": "MIT",
  "repository_copyright": "Copyright (c) 2026 Xu kaidong",
  "public_distribution_ready": true,
  "release_blockers": []
}
```

`public_distribution_ready=true` 表示仓库许可证与封装门禁已通过，不代表该
版本已实际发布到 GitHub Release、PyPI 或公开 universal Marketplace。ZIP
和 wheel 都必须包含仓库 `LICENSE`。

在 PowerShell 中比较 ZIP 的 SHA-256 与 `SHA256SUMS.txt` 中的值：

```powershell
Get-FileHash -Algorithm SHA256 .\materials-studio-mcp-plugin-<version>-windows.zip
```

将 ZIP 解压到当前用户可写、不会自动同步的本地目录。路径可包含空格或中文；
如果遇到 Windows 长路径限制，请选择较短的解压目录。不要解压到 Materials
Studio 安装目录、Codex plugin cache 或现有 workspace 中。

## 3. Configure → Install → Test

打开普通的 `cmd.exe`，进入解压目录，依次运行：

```bat
Configure-MS-MCP.bat
Install-MS-MCP.bat
Test-MS-MCP.bat
```

不要使用 `pip install -e`。安装器从包内经过 SHA-256 校验的 wheel 创建独立
venv，并把稳定 runtime 发布到：

```text
%LOCALAPPDATA%\MaterialsStudioMCP\runtimes\<version>\
```

用户配置、日志和 workspace 分别位于稳定用户目录，而不位于源码 worktree 或
Codex plugin cache：

```text
%LOCALAPPDATA%\MaterialsStudioMCP\config\
%LOCALAPPDATA%\MaterialsStudioMCP\logs\
<你在 Configure 中明确选择的 workspace>
```

### 3.1 Configure-MS-MCP.bat

Configure 会：

1. 检测 Windows 和 Python 3.10+；
2. 在有边界的常见安装位置查找 Materials Studio 2020/20.1；
3. 查找并验证 `RunMatScript.bat`，也允许你明确输入 runner 路径；
4. 让你选择独立 workspace；
5. 检测 Codex CLI 是否存在，但不会把它当作 MCP Server 的运行依赖；
6. 只写 `%LOCALAPPDATA%\MaterialsStudioMCP\config\` 下的用户配置；
7. 不修改当前 Codex 配置、不安装依赖、不启动 Materials Studio。

如果自动发现失败，重新运行 Configure，并在提示时粘贴
`RunMatScript.bat` 的完整路径。路径必须指向文件本身，不能指向目录、快捷方式、
符号链接或 reparse point。workspace 不能位于 runtime、plugin cache 或
Materials Studio 安装目录内。

也可以显式传参（占位符需替换为本机真实路径）：

```bat
Configure-MS-MCP.bat -Runner "<full-path-to-RunMatScript.bat>" -Workspace "<workspace-path>" -MaterialsStudioVersion 20.1
```

`-PythonCommand` 可选择已审核的 Python 命令；自动化可使用
`-NonInteractive`。已有配置发生预期变化时，审核后才增加 `-Force`。测试只能用
`-LocalAppDataRoot` 指向隔离临时根，不能指向真实用户配置后再宣称隔离。

### 3.2 Install-MS-MCP.bat

Install 会验证 wheel SHA-256，创建全新的版本化 venv，从 wheel 安装包，运行
`pip check`，核对 console entrypoints、package/plugin version 和 runtime
完整性，然后原子发布 runtime manifest。它不会：

- 使用 editable install 或依赖解压目录；
- 覆盖旧 runtime；
- 修改 workspace、revision、模型或计算结果；
- 修改 Codex active config；
- 启动 Materials Studio；
- 运行 CASTEP、DMol3 或 Forcite。

同一版本已存在且完整时，安装器应安全复用或返回明确状态；存在但内容不同则
fail closed，不做破坏式覆盖。

正常 release 可从顶层或 `dist` 自动发现 wheel、checksum 和 manifest。显式调用
示例为：

```bat
Install-MS-MCP.bat -WheelPath ".\materials_studio_mcp-<version>-py3-none-any.whl" -WheelSha256 <64-hex-sha256>
```

隔离自动化可增加 `-NonInteractive`；不能省略或伪造 hash 来绕过校验。

### 3.3 Test-MS-MCP.bat

默认测试在隔离 workspace 与隔离配置上下文中完成，不向真实 GUI 发送输入，也
不运行任何 Materials Studio 计算。它检查配置、runner、import、`compileall`、
wheel/runtime 版本、console entrypoints、plugin manifest、`.mcp.json`、本地
Marketplace、工具发现、schema、annotations、protocol smoke、stdout 纯净度，
并确认 `material_studio_run_script` 默认禁用。

成功的 protocol smoke 不是“真实 Materials Studio 验收”。没有额外授权时：

```text
Real Materials Studio: NOT_RUN
Real CASTEP: NOT_RUN
```

`Test-MS-MCP.bat --real-ms` 是显式 opt-in，并会再次要求人工确认。只有在当前
任务已单独授权、用户已手工打开唯一一个 Materials Studio 窗口时才能使用；
详见 [真实 Materials Studio 验收](REAL_MS_ACCEPTANCE.zh-CN.md)。

## 4. 安装本地 Codex Plugin Marketplace

使用经审核的精确发行 tag 把仓库 Marketplace 添加到本机：

```bat
codex plugin marketplace add Wqeeeeeeee/ms_mcp --ref <tag>
```

命令只添加 Marketplace source。记下 Codex 返回的 `marketplaceName`，再安装：

```bat
codex plugin add materials-studio-mcp --marketplace <marketplaceName> --json
```

记录返回的 `installedPath`。它应位于 Codex plugin cache，而不是开发 worktree。
也可以在支持的 ChatGPT/Codex 桌面应用中打开 **Plugins Directory**，选择刚添加
的本地/仓库 Marketplace，再安装 **Materials Studio MCP**。

安装后完全退出并重新启动 ChatGPT/Codex，或在 Codex CLI 中开始一个新 session。
Codex CLI 可用 `/plugins` 打开插件浏览器。

### 验证从 cache 副本运行

1. 用 `codex plugin add ... --json` 保存 `installedPath`。
2. 结束当前 MCP session。
3. 临时重命名或移开原源码目录（不要删除）。
4. 开始新 Codex session，再检查 MCP server。
5. cache 中的 `Run-MS-MCP.bat` 应继续从
   `%LOCALAPPDATA%\MaterialsStudioMCP\config\` 找到已安装 runtime 并启动。
6. 测试后把源码目录恢复原名。

这项测试证明插件不依赖开发 worktree；它不会删除源码，也不会触碰 workspace。

## 5. 检查 MCP 连接

在 Codex CLI 中查看 `/mcp`；在桌面应用中打开 MCP Servers 页面。确认
`materials-studio` 已连接且工具可发现。若出现
`mcp_server_restart_required`，重启 MCP session 或应用，不要重装 Materials
Studio。

第一次调用必须只读：

```text
检查本地 Materials Studio MCP、runner、workspace、runtime manifest 和 GUI 状态；
只读执行 status 与 preflight，不创建 revision，不运行 runner，不发送 GUI 输入。
```

应优先调用 `material_studio_get_status`、
`material_studio_live_session_preflight`/`material_studio_gui_status`，随后才使用
`material_studio_live_modeling_request`。

## 6. 第一次 preview 建模

先预览，不执行：

```text
预览创建一个小型 silicon diamond 结构。使用 material_studio_live_modeling_request，
execution_mode=preview；不创建执行产物，不运行 runner，不打开或修改 GUI。
```

检查返回的结构 spec、project/revision 计划、验证结果、blocker 和
`next_action_plan`。不支持的材料或场景必须 fail closed；不要接受“相近模板”替代。

请始终区分：

- `structure valid`：结构/schema/几何验证通过；
- `model normal`：当前 revision 的领域诊断未发现阻塞；
- `live GUI normal`：绑定的唯一窗口、PID/HWND 与可见模型证据通过；
- `calculation ready`：runner、结构、参数与计算安全门禁通过；
- `scientifically verified`：有足够的收敛性和科学证据支持结论。

GUI 截图只能证明某个时刻可见内容，不能替代前四类结构化验证，也不能证明科学
正确性。

## 7. 第一次明确确认的热加载

1. 用户手工打开一个 Materials Studio 窗口，并保持只有一个顶层窗口。
2. 再次执行只读 GUI status/preflight，核对目标 PID、HWND、窗口标题、workspace
   provenance、project 和 revision。
3. 明确说“执行刚才已审核的计划并热加载到该唯一窗口”。
4. 只调用 preview 返回的、绑定同一 project/revision 的 execute payload。

不要把一次 preview 当作执行授权；不要自动重试失败的 runner 或 GUI 操作；不要
启动第二个 Materials Studio 进程。失败时保留结构化 blocker，并按返回的精确
下一步处理。

## 8. Fit-to-View 与多视角诊断

热加载成功且同一 revision/window 再验证通过后，先 preview
`material_studio_gui_fit_to_view`，用户明确确认后再 execute。随后可导出
`front`、`top`、`isometric` 等多视角诊断。任何 replay 都必须遵守当前 recipe、
窗口绑定和证据完整性门禁；不要使用盲坐标或以截图替代结构 SHA-256 验证。

## 9. Revision、修改与 rollback

修改当前模型时仍先预览：

```text
预览修改当前模型；绑定当前 project/revision，返回差异、验证和下一步，不执行。
```

用 `material_studio_project_history` 只读检查历史。rollback 也先 preview，确认目标
revision 和不变式后再明确 execute。旧 revision、计算结果与用户模型由 workspace
保存，更新或默认卸载不会删除它们。

## 10. CASTEP、Forcite 与 DMol3

所有计算都必须是独立的两步操作：

1. preview 并审核结构、参数、runner、许可证/队列、脚本和安全门禁；
2. 用户再次明确确认 execute。

示例：

```text
预览当前晶体的 CASTEP Energy，execution_mode=preview，不执行，不创建 run directory，
不修改 GUI。
```

Forcite 和 DMol3 同样不能因“热加载模型”而自动运行。后端完成也不等于科学收敛；
按结果 receipt 和收敛性证据单独判断。

## 11. 更新

1. 从仓库所有者授权的发行渠道获取新的 ZIP、`SHA256SUMS.txt` 和
   release manifest。
2. 验证新包 version、base/ref SHA、MIT 许可证元数据和校验和。
3. 在新的解压目录运行 Configure、Install、Test。
4. 安装器发布新版本 runtime 并保留旧 runtime；不要覆盖旧目录。
5. 更新/重新安装 Marketplace plugin，并重新启动 ChatGPT/Codex session。
6. 做一次只读 preflight，再开始新的 preview。

请以仓库公告的发行渠道为更新来源。本文不声称已存在 GitHub Release、
PyPI 发行或公开 universal Marketplace 条目。

## 12. 卸载

先预览将删除的准确路径：

```bat
Uninstall-MS-MCP.bat --dry-run
```

审核后再执行：

```bat
Uninstall-MS-MCP.bat
```

默认只移除安装 manifest 记录的 managed runtime 和插件本地配置，并保留
workspace、revision、计算结果和用户模型。它不会卸载 Materials Studio，也不会
修改无关 Codex 配置。插件本身通过 Plugins Directory 或以下命令单独移除：

```bat
codex plugin remove materials-studio-mcp --marketplace <marketplaceName> --json
```

需要恢复时，保留 workspace，重新运行 Configure → Install → Test，然后重新安装
插件并执行只读 preflight。

## 13. 手动 MCP 注册 fallback

插件是推荐路径。IDE Extension 当前不支持插件，或 Marketplace 不可用时，使用
共享的本地 STDIO MCP 配置。仅持有 release ZIP 的用户应把
`codex plugin add --json` 返回的 `installedPath` 作为 `PLUGIN_CACHE_PATH`，人工审核
并合并以下最小绑定（单引号是 TOML literal，替换占位符但保留引号）：

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

这仍从稳定 cache launcher 读取 `%LOCALAPPDATA%` runtime，不依赖开发 worktree。
不要整文件覆盖 active `config.toml`；保留其他 MCP server、认证和 trusted-project
配置。`enabled_tools` 的内容和顺序必须与 plugin cache 的 `.mcp.json` /
`SAFE_ENABLED_TOOLS` 完全一致，不得省略。重启 Codex 后执行只读 preflight。

现有 `register_codex.py`、`ms-mcp-config-register` 和
`.codex/config.toml.example` 是 **完整源码 checkout / managed-source runtime** 的
legacy fallback：registrar 会验证仓库根的 `run_server.py`，不能直接用于只有 wheel
venv 的 release runtime。需要它们时，从同一已审核 exact ref 取得完整 checkout，
先审核 fingerprint-bound preview、`registration_plan_id`、allowlist 和 denylist，再
显式 apply；不要从其他分支或 DrYe 参考仓库复制注册文件。

详细问题见 [故障排查](TROUBLESHOOTING.zh-CN.md) 和
[Codex 插件说明](CODEX_PLUGIN.zh-CN.md)。

## 14. 当前验收状态

本阶段只允许安全离线测试和 protocol smoke：

```text
Real Materials Studio: NOT_RUN
Real CASTEP: NOT_RUN
```

不得把 fake GUI、mock、import 测试或 protocol smoke 描述成真实验收。
