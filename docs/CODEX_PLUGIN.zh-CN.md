# Codex 插件、本地 Marketplace 与稳定 Runtime

本仓库采用 **MIT License**，Copyright (c) 2026 Xu kaidong。插件、
wheel 与发行包必须保留该 `LICENSE`。

本项目是独立项目，不是 BIOVIA 或 Dassault Systèmes 官方产品。
本插件不附带 Materials Studio、Materials Studio 商业许可证或未经授权的
BIOVIA/Dassault Systèmes 商标图标；真实运行需要另行获得本地 Materials
Studio 合法授权。

## 1. 这个插件包含什么

`materials-studio-mcp` 插件只封装当前主仓库已有能力，不引入新的半导体模板、
CASTEP 科学功能、GUI 建模算法或其他未合并 Draft PR 的代码。插件根目录包含：

```text
plugins/materials-studio-mcp/
  .codex-plugin/plugin.json
  .mcp.json
  Run-MS-MCP.bat
  scripts/Run-MS-MCP.ps1
  skills/materials-studio-modeling/SKILL.md
```

- `plugin.json` 提供稳定插件身份、界面信息、Skill 和 MCP 入口。
- `.mcp.json` 使用相对于插件 cache 副本的 STDIO server 配置；不包含开发机绝对
  Python 或仓库路径。
- `Run-MS-MCP.bat`/PowerShell launcher 只读取固定用户配置，验证 runtime
  manifest、Python、package version、runtime SHA-256、runner 和 workspace，随后
  启动 `material_studio_mcp_server.server:main`。
- Skill 指导 Codex 先 status/preflight、默认 preview、明确确认副作用，并区分
  结构、模型、GUI、计算就绪与科学验证。

launcher 不安装依赖、不修改 Codex 配置、不启动 Materials Studio，也不调用
legacy `ms_mcp.server`。MCP JSON-RPC 独占 stdout；诊断只能进入 stderr 或日志。

## 2. 插件 cache 与稳定 runtime 为什么分离

插件被安装后，Codex 使用自己的 cache 副本。cache 路径可能随 Marketplace、
插件版本或客户端变化，不应存放用户配置、venv 或模型数据。稳定状态位于：

```text
%LOCALAPPDATA%\MaterialsStudioMCP\
  runtimes\<version>\
  config\
  logs\
```

workspace 是 Configure 明确选择的独立路径。这样即使插件 cache 被刷新，launcher
仍能验证并使用同一个版本化 runtime；即使开发 worktree 被改名或移走，cache
副本也能启动。

若 `codex plugin add ... --json` 返回 `installedPath`，应把它当作本次安装的
权威 cache 路径，而不是猜测目录名。不要手工把配置写进 cache。

## 3. 当前支持的 Codex/ChatGPT surface

根据任务执行日核对的 OpenAI 官方文档：

- ChatGPT 桌面应用中支持 Plugins Directory 的 Chat/Codex surface 可以安装
  插件；
- Codex CLI 可以通过 `/plugins` 或 `codex plugin` 安装插件；
- Codex IDE Extension **当前不支持插件**；
- IDE Extension 可以使用 Codex host 的本地 STDIO MCP 配置，因此采用手动
  MCP fallback；
- ChatGPT web 不读取本机 Codex 配置，不能借此直接控制本地 Materials Studio。

官方参考：

- [Package your plugin](https://developers.openai.com/plugins/build/plugins.md)
- [Plugins in ChatGPT and Codex](https://learn.chatgpt.com/docs/plugins.md)
- [Codex plugin CLI](https://learn.chatgpt.com/docs/developer-commands?surface=cli#cli-codex-plugin)
- [Codex marketplace CLI](https://learn.chatgpt.com/docs/developer-commands?surface=cli#cli-codex-plugin-marketplace)
- [Local MCP configuration](https://learn.chatgpt.com/docs/extend/mcp.md)

客户端能力可能更新；公开发布前必须再次以当日官方文档验证 schema、surface 和
Marketplace 行为。

## 4. 本地/仓库 Marketplace 安装

仓库 Marketplace 清单位于：

```text
.agents/plugins/marketplace.json
```

其 source 是相对于 Marketplace 根目录的：

```text
./plugins/materials-studio-mcp
```

以下命令的 `<...>` 都是必须先替换的占位符，不得在 `cmd.exe`/PowerShell 中原样
粘贴。使用经审核的精确发行 tag：

```bat
codex plugin marketplace add Wqeeeeeeee/ms_mcp --ref <tag>
```

记下 `marketplaceName`，然后安装：

```bat
codex plugin add materials-studio-mcp --marketplace <marketplaceName> --json
```

Marketplace add 与 plugin add 是两个独立步骤。安装完成后开始新 session；旧
session 不应被当作已加载新 manifest。桌面端可在 Plugins Directory 中从相同
Marketplace 选择安装。

上述命令只添加仓库 Marketplace source，不表示本插件已上架公开
universal Marketplace，也不证明 GitHub Release 或 PyPI 已发布对应产物。

## 5. cache 副本独立性验收

1. 完成 Configure → Install → Test。
2. 用 `codex plugin add ... --json` 安装并保存 `installedPath`。
3. 结束所有使用该 MCP server 的 Codex session。
4. 把原源码目录临时改名或移动到同一磁盘的安全位置；不得删除。
5. 从新 session 连接 `materials-studio` 并执行只读 status/preflight。
6. 检查 launcher 实际位于 `installedPath`，runtime 位于
   `%LOCALAPPDATA%\MaterialsStudioMCP\runtimes\<version>`。
7. 检查 stdout 只有 MCP protocol；日志未进入 stdout。
8. 恢复源码目录。

若 cache 副本尝试加载源码目录、editable `.pth`、开发 `.venv` 或仓库根 Python，
验收失败。不要通过创建软链接或复制开发环境来“修复”。

## 6. Skill 的调用纪律

建模请求优先经过：

```text
read-only status/preflight
        ↓
material_studio_live_modeling_request (preview)
        ↓
用户审核 project/revision/spec/blocker/next action
        ↓
用户明确确认某个副作用
        ↓
绑定同一 project/revision/window 的 execute
```

以下每一种都需要独立、明确的确认：

- 创建或推进 revision；
- 调用 `RunMatScript.bat` runner；
- 打开、激活、热加载或改变 Materials Studio GUI；
- Fit-to-View 或视角 replay 中实际发送 GUI 输入；
- 启动 CASTEP、Forcite 或 DMol3。

`material_studio_run_script` 默认禁用。只有用户明确要求、脚本已人工审查且 active
配置也明确允许时，才可考虑它。Skill 文本不是安全执行器；runtime、server 和
Codex tool policy 都必须继续 fail closed。

失败的副作用调用不得自动重试。返回结构化 blocker、保持 revision/evidence，按
server 返回的 exact next action 继续。不支持的材料不能换成相近模板。

## 7. 手动 MCP fallback

插件不可用或使用 IDE Extension 时，保留三种兼容入口：

1. bundle-only 用户人工把 cache 中的 `Run-MS-MCP.bat` 合并到共享 STDIO MCP
   配置，并同时设置 `MATERIAL_STUDIO_MCP_PLUGIN_MODE=1`、prompt 审批、与
   `SAFE_ENABLED_TOOLS` 完全一致的 `enabled_tools`，以及
   `disabled_tools=["material_studio_run_script"]`；
2. 完整源码 checkout 中的 `register_codex.py`；
3. 完整源码/managed-source runtime 的 `ms-mcp-config-register` 与
   `.codex/config.toml.example`。

现有 registrar 硬绑定仓库根 `run_server.py`，不能直接注册 wheel-only runtime；
不要把 console entrypoint 存在误报为该能力已实现。源码注册必须先 preview，审核
runtime/entrypoint、配置 SHA、`registration_plan_id`、tool allowlist 和审批模式，
再显式 apply。两种 fallback 都必须保证：

```toml
# REQUIRED: copy the complete enabled_tools array from the cache .mcp.json.
disabled_tools = ["material_studio_run_script"]
```

可直接审核并复制的完整 TOML 数组见
[中文安装教程](INSTALLATION.zh-CN.md#13-手动-mcp-注册兼容路径)；不得用省略号、历史
清单或只含单个工具的列表替代。

不要让 Configure 或 plugin install 自动修改 active config。不要覆盖已有
`config.toml`，也不要删除无关 MCP server、认证或 trusted-project 条目。变更后
重启 Codex，执行只读 doctor/status/preflight。

## 8. 更新与移除

Marketplace ref、plugin cache 和稳定 runtime 是三个不同状态。更新时：

1. 校验新的内部 ZIP/manifest/hash；
2. 安装新的不可变 runtime，保留旧版本；
3. upgrade/reinstall Marketplace plugin；
4. 开始新 session；
5. 做只读 preflight。

移除插件使用 Plugins Directory 或 `codex plugin remove`。移除 managed runtime
前先运行 `Uninstall-MS-MCP.bat --dry-run`。默认保留 workspace、revision、计算
结果和模型；不要删除 Materials Studio 安装或修改无关 Codex 配置。

## 9. 发行构建

先在干净环境构建 wheel，然后把审计记录的精确 base SHA 与 DrYe 只读参考 commit
传给确定性构建器：

```powershell
python -m build
python .\scripts\build_plugin_release.py `
  --wheel .\dist\materials_studio_mcp-<version>-py3-none-any.whl `
  --base-sha <exact-40-character-origin-main-sha> `
  --reference-sha <exact-40-character-DrYe-reference-sha>
```

构建器只收录固定 allowlist：插件目录、本地 Marketplace、四个根 BAT、五个
`scripts/windows` PowerShell 实现、中英文文档、仓库 `LICENSE`、必要时的
`THIRD_PARTY_NOTICES.md` 和 wheel。它拒绝
路径穿越、symlink/reparse point、开发绝对路径、常见 secret、workspace、`.venv`、
Git 元数据、临时文件和不一致的许可证元数据。

输出为：

```text
dist/materials_studio_mcp-<version>-py3-none-any.whl
dist/materials-studio-mcp-plugin-<version>-windows.zip
dist/SHA256SUMS.txt
dist/release-manifest.json
```

ZIP 内部也包含仓库 `LICENSE`、payload `SHA256SUMS.txt` 与相同 release
manifest；当审计确认需要第三方声明时，同时包含
`THIRD_PARTY_NOTICES.md`。相同输入应产生字节一致的 ZIP。

## 10. 发行门禁

发行构建器必须输出：

```json
{
  "repository_license_status": "declared",
  "repository_license_spdx": "MIT",
  "repository_copyright": "Copyright (c) 2026 Xu kaidong",
  "public_distribution_ready": true,
  "release_blockers": []
}
```

`public_distribution_ready=true` 仅表示许可证与封装门禁已通过，不是已发布
GitHub Release、PyPI 包或公开 universal Marketplace 条目的证据。发行前仍要核对
当日官方 plugin schema，并从干净树重新构建/测试。

只有审计确认实际复制或改写了第三方 MIT 代码时，才应在
`THIRD_PARTY_NOTICES.md` 中保留原版权和许可；不得虚构复制关系或把参考仓库
的 MIT 许可证宣称为本仓库许可证。

- Real Materials Studio: **NOT_RUN**
- Real CASTEP: **NOT_RUN**
