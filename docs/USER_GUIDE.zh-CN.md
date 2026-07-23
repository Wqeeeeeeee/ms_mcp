# Materials Studio MCP 用户部署与使用手册

本手册面向在 Windows 上使用 Codex 和 BIOVIA Materials Studio 2020/20.1
的最终用户。项目通过 MaterialsScript、结构化模型 revision 和可选的本地
GUI 控制，实现可复现的半导体材料建模、同窗口热加载、结构诊断和计算预览。

## 1. 工作方式

Materials Studio MCP 包含两条相互配合的路径：

1. **结构路径**：自然语言请求转换为 `ModelSpec` 或 `SemanticPatch`，
   校验后生成 MaterialsScript/CIF，并保存不可变 revision。
2. **GUI 路径**：检测并复用唯一已打开的 Materials Studio 窗口，激活窗口、
   打开当前 revision、截图并辅助多视角验证。

MCP 不使用 COM 附着，也不会靠盲目坐标点击修改原子。结构实质变化仍由
结构化 spec/patch 和 MaterialsScript 完成。GUI 用于加载结果、检查模型和
执行经过验证的有限窗口操作。

默认行为是 `preview`。只有用户明确确认 `execution_mode="execute"`，
才会执行结构物化、计算或会改变 GUI 状态的操作。

## 2. 系统要求

- Windows 10 或 Windows 11。
- BIOVIA Materials Studio 2020/20.1。
- Materials Studio Scripting 组件和有效许可证。
- Python 3.10 或更高版本。
- Git。
- Codex Desktop 或支持 stdio MCP 的 Codex 环境。

确认 Materials Studio runner 存在：

```powershell
Test-Path "D:\Program Files (x86)\BIOVIA\Materials Studio 20.1\etc\Scripting\bin\RunMatScript.bat"
```

安装目录不同时，后续通过 `MATERIAL_STUDIO_RUNNER` 指定实际路径。

## 3. 下载和安装

```powershell
git clone https://github.com/Wqeeeeeeee/ms_mcp.git
cd ms_mcp
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
```

开发或运行测试时安装开发依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

如果 runner 未被自动发现：

```powershell
$env:MATERIAL_STUDIO_RUNNER = "D:\Program Files (x86)\BIOVIA\Materials Studio 20.1\etc\Scripting\bin\RunMatScript.bat"
```

需要永久保存时：

```powershell
[Environment]::SetEnvironmentVariable(
  "MATERIAL_STUDIO_RUNNER",
  "D:\Program Files (x86)\BIOVIA\Materials Studio 20.1\etc\Scripting\bin\RunMatScript.bat",
  "User"
)
```

## 4. 注册到 Codex

### 4.1 推荐：受保护的注册流程

先预览，不修改 Codex 配置：

```powershell
.\.venv\Scripts\python.exe register_codex.py --cwd . --omit-snippet
```

检查输出中的：

- Python 和仓库绝对路径。
- `config_sha256_before`。
- `proposed_config_sha256`。
- `registration_plan_id`。
- 安全工具 allowlist。

确认无误后，只应用刚才得到的计划 ID：

```powershell
.\.venv\Scripts\python.exe register_codex.py `
  --cwd . `
  --apply `
  --expected-plan-id <REVIEWED_REGISTRATION_PLAN_ID>
```

注册器只追加 `mcp_servers.materials_studio`，不会覆盖其他 Codex 配置。
它会创建原配置备份，并在成功回执中返回绑定哈希的回滚命令。

完成后：

1. 完全重启 Codex。
2. 不要启动第二个 Materials Studio 窗口。
3. 在 Codex 中调用 `@mcp` 进行连接检查。

### 4.2 手动注册

需要手工配置时，复制并修改 `.codex/config.toml.example` 中的绝对路径，
再合并到 `%USERPROFILE%\.codex\config.toml`。主入口必须是：

```text
material_studio_mcp_server.server:main
```

实际启动文件使用仓库根目录的 `run_server.py`。不要配置旧入口
`ms_mcp.server`，否则结构化工具和 GUI 工具可能不可见。

正常部署应继续禁用：

```text
material_studio_run_script
```

该工具允许任意自定义 Perl，仅适合经过人工审查的高级场景。

## 5. 部署验收

### 5.1 本地测试

```powershell
.\.venv\Scripts\python.exe -m compileall -q src
.\.venv\Scripts\python.exe -m pytest -q
```

完整测试可能需要较长时间。

### 5.2 MCP 协议验收

```powershell
.\.venv\Scripts\python.exe -m material_studio_mcp_server.protocol_smoke `
  --cwd . `
  --workspace workspace\mcp_protocol_acceptance `
  --config .codex\config.toml.example `
  --output workspace\mcp_protocol_acceptance\summary.json
```

该验收使用隔离 workspace，只做工具发现、schema、annotation 和 preview
检查，不启动计算，不修改 Materials Studio GUI。

### 5.3 Codex 内验收

在 Codex 中依次输入：

```text
@mcp 检查 Materials Studio MCP、runner 和 GUI 状态，只读，不执行建模。
```

```text
@mcp 对当前会话执行只读 preflight，确认工具入口、workspace、唯一窗口和当前 revision。
```

应确认：

- MCP server 来源是当前部署。
- runner 指向 `RunMatScript.bat`。
- `MatStudio.exe` 进程数和顶层窗口数均为 1。
- 没有 `mcp_server_restart_required`。
- 没有 workspace provenance 冲突。

## 6. 单窗口 Materials Studio 规则

1. 建模前只保留一个 Materials Studio 顶层窗口。
2. 多余窗口应由用户先保存需要的文件，再关闭。
3. MCP 热加载必须复用现有窗口，不允许为每个 revision 启动新进程。
4. 窗口最小化或失去焦点时，先执行 GUI activate，再截图或输入。
5. `model_ready_for_hotload=true` 只表示结构文件准备完成，不代表 GUI
   操作已经获准。

推荐启动方式：

1. 用户手工启动一个 Materials Studio。
2. 在 Codex 中执行只读 GUI status。
3. 状态确认单窗口后再执行热加载。

## 7. 自然语言建模

优先使用高层工具 `material_studio_live_modeling_request`。普通用户无需手写
JSON spec。

### 7.1 创建并热加载模型

先预览：

```text
@mcp 预览创建一个 2H-MoS2 单层，保留 20 埃真空层，不执行，不改变 GUI。
```

检查返回的：

- 模板或结构化 spec。
- 原子数、元素计数和晶格参数。
- validation。
- generated script/CIF 计划。
- calculation readiness 和 warnings。

确认后执行：

```text
@mcp 确认执行刚才的 MoS2 建模计划，热加载到唯一已打开的 Materials Studio 窗口，
执行 Fit-to-View，并导出 front、top、isometric 和 (0001) 视角诊断。
```

### 7.2 修改当前模型

```text
@mcp 预览把当前模型顶层沿 x 方向平移 0.5 埃，不执行。
```

```text
@mcp 确认执行该平移 patch，创建新 revision，并在同一个 Materials Studio 窗口中刷新。
```

结构修改不会覆盖旧 revision。

### 7.3 常用半导体请求

```text
@mcp 预览创建 4H-SiC 晶体。
```

```text
@mcp 预览创建 Si-face 3C-SiC(001)/SiO2 界面。
```

```text
@mcp 预览创建 2x2x2 金刚石 NV- 中心结构，并检查缺陷、有限尺寸和电荷自旋门禁。
```

```text
@mcp 检查当前模型是否正常，导出结构、配位、缺陷、界面、真空层和视角参数。
```

未被审查的材料组合、晶面、超胞或计算参数会 fail closed，不会偷偷替换成
相近模板。

## 8. Revision、历史和回滚

查看当前 revision：

```text
@mcp 读取当前项目和 revision，只读。
```

查看历史：

```text
@mcp 列出当前项目 revision 历史和每次 semantic diff。
```

回滚预览：

```text
@mcp 预览回滚到 revision 3，不执行。
```

确认回滚：

```text
@mcp 确认回滚到 revision 3，创建一个新的回滚 revision，并热加载到当前窗口。
```

回滚不会删除 revision，也不会覆写历史文件。

## 9. 结构诊断和多视角检查

建议对半导体模型至少检查：

- 化学式、元素计数和原子 ID。
- 晶格参数、体积和周期方向。
- 最近邻距离和配位异常。
- 缺陷、掺杂和界面元数据是否与实际原子表一致。
- 真空层、层厚、界面间距和表面方向。
- front、top、isometric 以及相关晶面/晶向视角。
- 当前 GUI 是否加载了同一 project/revision/structure SHA-256。

导出示例：

```text
@mcp 导出当前 revision 的完整 view bundle，包含 front、top、isometric 和推荐晶面，
并判断模型是否正常；只读，不运行计算。
```

诊断结论会区分：

- `can_claim_model_normal`：结构和持久化证据是否正常。
- `can_claim_live_gui_normal`：当前 GUI 是否有完整可信的视觉证据。
- `ready_for_calculation`：参数和科学审查是否足以开始计算。

模型正常不等于已经可以计算。

## 10. CASTEP 和 Forcite

### 10.1 CASTEP

先预览：

```text
@mcp 预览当前晶体的 CASTEP Energy，PBE，截断能 600 eV，
k 点间距 0.04，不执行。
```

执行前必须确认：

- cutoff 和 k 点采样明确。
- slab 的真空和偶极修正契约满足。
- 缺陷电荷/自旋设置已由结构化 schema 表示。
- 当前 revision 没有变化。
- 许可证和计算资源可用。

随后使用明确措辞：

```text
@mcp 我确认执行刚才绑定到当前 revision 的 CASTEP Energy 计划，
不启动新的 Materials Studio 窗口。
```

失败、未收敛或证据不完整的结果会保留，但不会伪装成成功 revision。

### 10.2 Forcite

```text
@mcp 预览对当前分子执行 Forcite 几何优化，使用 COMPASS 和 Fine 质量，不执行。
```

确认后再要求执行。Forcite 和 CASTEP 都不会因为普通自然语言中的模糊措辞
自动启动。

## 11. Workspace 和输出

默认 workspace：

```text
%LOCALAPPDATA%\materials_studio_mcp\workspace
```

可选自定义：

```powershell
[Environment]::SetEnvironmentVariable(
  "MATERIAL_STUDIO_MCP_WORKSPACE",
  "D:\MaterialsStudioMCPWorkspace",
  "User"
)
```

每个项目通常包含：

```text
<workspace>\
  projects\<project_id>\
    revisions\
    scripts\
    outputs\
    history.jsonl
    current.json
  screenshots\
```

不要手工修改 revision、hash journal 或 `current.json`。需要变更时使用 MCP
patch、rollback 或 restore 工具。

## 12. 更新部署

源码开发部署：

```powershell
git pull --ff-only
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m compileall -q src
```

之后必须重启 Codex MCP 进程。Materials Studio 窗口不需要关闭。

生产使用建议部署一个已推送、工作区干净的不可变 commit：

```powershell
.\.venv\Scripts\python.exe deploy_runtime.py --source .
```

检查部署计划后：

```powershell
.\.venv\Scripts\python.exe deploy_runtime.py `
  --source . `
  --apply `
  --expected-plan-id <REVIEWED_RUNTIME_DEPLOYMENT_PLAN_ID>
```

按照成功回执中的注册命令完成 Codex 注册。新 commit 需要新部署；旧 runtime
不会被自动覆盖或删除。

## 13. 常见故障

### Codex 看不到工具

- 确认配置入口是 `run_server.py`。
- 确认使用 `material_studio_mcp_server`，不是旧 `ms_mcp.server`。
- 运行 config doctor。
- 重启 Codex。

### 找不到 RunMatScript.bat

- 检查 Materials Studio Scripting 组件是否安装。
- 设置正确的 `MATERIAL_STUDIO_RUNNER`。
- 重启 Codex 使环境变量生效。

### 报告存在多个 Materials Studio 窗口

- 保存需要的项目。
- 关闭多余顶层窗口和异常残留进程。
- 只保留一个窗口后重新执行只读 preflight。
- 不要通过 MCP 强行选择一个不明确的窗口。

### 窗口已打开但不能截图或操作

- 检查窗口是否最小化、隐藏或被其他窗口遮挡。
- 先调用 GUI activate。
- 再执行 snapshot 或热加载，不要重复运行结构脚本。

### workspace 不匹配

使用状态返回的 `recommended_working_dir` 做只读 preflight，并显式指定
`project_id`。MCP 不会自动切换到另一个 workspace。

### 模型可见但 `ready_for_calculation=false`

这是科学门禁，不一定是软件故障。检查返回的 cutoff、k 点、真空、偶极修正、
缺陷电荷/自旋、结构弛豫和收敛原因。

## 14. 安全边界

- 默认 preview。
- 所有执行都要求明确确认。
- 自定义 `material_studio_run_script` 默认禁用。
- 不自动启动第二个 Materials Studio。
- 不删除历史 revision。
- 不自动重试失败或中断的计算。
- 不把 GUI 截图当作结构或计算成功的唯一证据。
- 不把 preview 结果描述成已经执行。

技术细节参见：

- [Codex 配置](codex_setup.md)
- [自然语言工作流](natural_language_workflow.md)
- [GUI 控制](gui_control.md)
- [MCP 协议验收](mcp_protocol_acceptance.md)
