# Materials Studio MCP

面向 BIOVIA Materials Studio 2020/20.1 的本地 MCP 服务，支持：

- 自然语言半导体建模。
- `ModelSpec` / `SemanticPatch` 结构化 revision。
- MaterialsScript Perl 和 CIF 确定性生成。
- 单一 Materials Studio 窗口热加载与截图。
- 多视角、晶格、配位、缺陷、掺杂、界面和真空层诊断。
- Preview-first 的 CASTEP 与 Forcite 工作流。
- 非破坏历史、回滚和执行证据审计。

完整中文手册：
[docs/USER_GUIDE.zh-CN.md](docs/USER_GUIDE.zh-CN.md)

## 系统要求

- Windows 10/11。
- Materials Studio 2020/20.1，并安装 Scripting 组件。
- Python 3.10+。
- Codex Desktop 或其他支持 stdio MCP 的客户端。

本项目通过 `RunMatScript.bat` 执行 MaterialsScript，不使用
`MaterialsStudio.Application` COM。GUI 控制只复用唯一已打开的
Materials Studio 窗口，不依靠盲目坐标修改结构。

## 快速安装

```powershell
git clone https://github.com/Wqeeeeeeee/ms_mcp.git
cd ms_mcp
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
```

无法自动找到 runner 时：

```powershell
$env:MATERIAL_STUDIO_RUNNER = "D:\Program Files (x86)\BIOVIA\Materials Studio 20.1\etc\Scripting\bin\RunMatScript.bat"
```

## 注册到 Codex

先预览注册计划，不修改配置：

```powershell
.\.venv\Scripts\python.exe register_codex.py --cwd . --omit-snippet
```

检查路径、哈希和 `registration_plan_id` 后显式应用：

```powershell
.\.venv\Scripts\python.exe register_codex.py `
  --cwd . `
  --apply `
  --expected-plan-id <REVIEWED_REGISTRATION_PLAN_ID>
```

然后重启 Codex。也可以基于
[.codex/config.toml.example](.codex/config.toml.example)
手工合并配置。

主入口必须是：

```text
material_studio_mcp_server.server:main
```

`material_studio_run_script` 默认禁用。

## 首次验收

```powershell
.\.venv\Scripts\python.exe -m compileall -q src
.\.venv\Scripts\python.exe -m material_studio_mcp_server.protocol_smoke `
  --cwd . `
  --workspace workspace\mcp_protocol_acceptance `
  --config .codex\config.toml.example
```

在 Codex 中：

```text
@mcp 检查 Materials Studio MCP、runner、workspace 和 GUI 状态，只读，不执行。
```

开始建模前只保留一个 Materials Studio 顶层窗口。

## 使用示例

预览创建：

```text
@mcp 预览创建一个 2H-MoS2 单层，保留 20 埃真空，不执行，不改变 GUI。
```

确认并热加载：

```text
@mcp 确认执行刚才的计划，热加载到唯一已打开的 Materials Studio 窗口，
执行 Fit-to-View，并导出 front、top、isometric 和 (0001) 视角诊断。
```

修改当前模型：

```text
@mcp 预览把当前模型顶层沿 x 方向平移 0.5 埃，不执行。
```

历史与回滚：

```text
@mcp 列出当前项目 revision 历史。
@mcp 预览回滚到 revision 3，不执行。
```

CASTEP 计算仍需单独明确确认：

```text
@mcp 预览当前晶体的 CASTEP Energy，PBE，截断能 600 eV，
k 点间距 0.04，不执行。
```

## 安全策略

- 新建模和计算默认 `execution_mode="preview"`。
- 执行、GUI 输入和文件状态变化要求显式确认。
- 多窗口或目标窗口不明确时拒绝 GUI 操作。
- 回滚创建新 revision，不删除历史。
- 失败、未收敛或证据不完整的计算不会提升为成功 revision。
- GUI 可见不等于模型正常，模型正常也不等于已经可以计算。

## 开发验证

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q src
```

更多文档：

- [Codex 配置](docs/codex_setup.md)
- [自然语言工作流](docs/natural_language_workflow.md)
- [GUI 控制](docs/gui_control.md)
- [协议验收](docs/mcp_protocol_acceptance.md)
