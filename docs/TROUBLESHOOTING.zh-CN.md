# Materials Studio MCP Windows 故障排查

本仓库采用 **MIT License**，Copyright (c) 2026 Xu kaidong。故障
排查不能通过改写 `LICENSE`、校验和或 release manifest，也不能通过从
未知镜像下载“修复包”来绕过完整性检查。

本项目是独立项目，不是 BIOVIA 或 Dassault Systèmes 官方产品；不
附带 Materials Studio、其商业许可证或未经授权的商标图标。真实运行需要
另行获得本地 Materials Studio 合法授权。

先运行安全默认测试：

```bat
Test-MS-MCP.bat
```

默认 Test 不运行 Materials Studio 计算、不发送 GUI 输入。保留完整错误、退出码、
runtime manifest digest 和相对 artifact 路径；不要把 API key、私人绝对路径或含
隐私的截图提交到 PR。

本文 `<...>` 表示需要替换的占位符；不要把尖括号原样粘贴进 Windows shell。

## 找不到 RunMatScript.bat

确认安装 Materials Studio 2020/20.1 时选择了 **Scripting** 组件。Windows 搜索或
Materials Studio 安装维护程序可以确认组件，但不要对整个磁盘做无边界递归扫描。

重新配置并明确传入文件：

```bat
Configure-MS-MCP.bat -Runner "<full-path-to-RunMatScript.bat>" -Workspace "<workspace-path>" -MaterialsStudioVersion 20.1
```

runner 必须是存在的普通 `.bat` 文件，不是目录、快捷方式、symlink/reparse point、
网络下载占位文件或 shell 片段。不要把引号、`&`、`|`、重定向符等拼入路径以外的
参数。Configure 只写用户配置，不会启动 Materials Studio。

## Python 版本错误或找不到 Python

检查：

```bat
py -3 --version
python --version
```

需要 Python 3.10+。不要让 Windows Store 的占位 alias 冒充可执行 Python。安装或
修复 64 位 Python 后重新运行 Configure；必要时使用经过审核的
`-PythonCommand`。Install 必须创建独立 venv，不能用系统 site-packages、开发
`.venv` 或 `pip install -e` 代替。

## wheel hash 不匹配

停止安装。比较外部 `SHA256SUMS.txt`、`release-manifest.json` 和实际文件：

```powershell
Get-FileHash -Algorithm SHA256 .\materials_studio_mcp-<version>-py3-none-any.whl
```

不要改写 checksum 或用 `--force` 绕过。重新从仓库所有者授权的发行渠道取得
完整包。若 manifest 不是 `repository_license_status=declared`、
`repository_license_spdx=MIT`、`public_distribution_ready=true` 和空
`release_blockers`，也应停止使用并重新获取发行包。这些字段不代表该版本已在
GitHub Release、PyPI 或公开 universal Marketplace 发布。

## runtime 已存在、配置过期或安装中断

runtime 是版本化且不可破坏覆盖的。已存在目录只有在完整 manifest/content 校验
一致时才能复用；内容不一致时安装器应 fail closed。不要手工删除 lock、manifest
或旧 runtime 来促使安装继续。

配置引用旧版本、runner/workspace 已变时，先重新运行 Configure，审核变化后再用
`-Force`。安装中断后保留旧 runtime，重新运行 Install；它应忽略未发布 staging，
而不是覆盖已发布目录。workspace 不参与 runtime 修复。

## plugin cache 路径不符合预期

不要猜测 cache 目录。运行：

```bat
codex plugin list --json
```

或重新执行安装并读取返回的 `installedPath`：

```bat
codex plugin add materials-studio-mcp --marketplace <marketplaceName> --json
```

路径应位于 Codex 管理的 plugin cache，而非源码 worktree。用户配置不能写进
cache；launcher 应从 `%LOCALAPPDATA%\MaterialsStudioMCP\config\` 解析 runtime。
若删除/刷新 cache 后 runtime 消失，说明错误地把 venv 放进了插件目录，应重新按
Configure → Install → Test 安装。

## 插件已安装但 MCP 未出现

Marketplace add 不等于 plugin add。确认已完成两步，并在桌面端完全重启应用或在
Codex CLI 开始新 session。使用 `/plugins` 检查插件，使用 `/mcp` 或 MCP Servers
页检查 `materials-studio`。

Codex IDE Extension 当前不支持插件。IDE 中请使用手动共享 STDIO MCP fallback，
不要在 IDE 的插件 UI 中反复寻找本插件。

## mcp_server_restart_required

这通常表示 plugin/runtime/config 已改变，但现有 MCP process 仍加载旧状态。保存
当前工作，停止对应 MCP session，重启 ChatGPT/Codex 或开始新 CLI session，再做
只读 status/preflight。保持用户手工打开的 Materials Studio 窗口，不要为“重启
MCP”而启动第二个 Materials Studio 进程。

## stdout 被日志污染

症状包括初始化 JSON 解析失败、第一帧前出现普通文本、随机断连。STDIO MCP 的
stdout 只能包含 JSON-RPC framing；banner、Python traceback、PowerShell verbose、
runner 检查和状态日志必须写 stderr 或日志文件。

执行默认 Test 的 stdout-purity 检查。直接从 cache 副本运行 launcher 时，把
stdout 与 stderr 分开捕获；任何非协议 stdout 都是失败。不要用 `echo` 在
`Run-MS-MCP.bat` 成功路径打印提示。配置无效时应只向 stderr 给出可执行错误并以
非零码退出。

## tool allowlist drift

运行 config doctor 和 protocol smoke，比较当前 source baseline、clean wheel 和
实际 server discovery；不要把历史工具数量硬编码成判据。确认
plugin `enabled_tools` 与已安装包的 `SAFE_ENABLED_TOOLS` 顺序和内容完全一致，
`material_studio_run_script` 仍在 `disabled_tools`，所有副作用工具仍使用 prompt
审批。兼容型旧 builder 不得因 allowlist 漂移而重新暴露。

若 plugin cache、手动 config 和 runtime 的 allowlist 不一致，先停止有副作用
操作。完整源码/managed-source 用户审核 `register_codex.py` 或
`ms-mcp-config-register` 生成的 preview 和 `registration_plan_id`；bundle-only
用户审核手工 cache-launcher 表。不要自动整文件覆盖 active config。

## workspace provenance mismatch

可见 Materials Studio wrapper 可能属于另一个受信 workspace/project/revision。
读取 status 返回的 `workspace_context_mismatch`、可见 wrapper metadata 和
`recommended_working_dir`。不要自动采用或写入那个 workspace，也不要在省略
project 的 follow-up 中创建 revision。

先用返回的 working directory 做只读 preflight，再由用户明确选择 project。只有
匹配 project/revision/window 的请求才能热加载。

## 多个 Materials Studio 窗口

关闭或保存其他窗口，使顶层 Materials Studio 窗口唯一；由用户手工完成。不要让
Codex 猜目标、关闭窗口或启动新进程。重新调用 `material_studio_gui_status`，检查
`process_count`、`top_level_window_count`、`single_window_policy_ok`、selected 与
target identity。

只要歧义存在，GUI open、snapshot、Fit-to-View、replay 和计算热加载都应阻塞。

## 目标窗口不在前台、不可见或最小化

这是 activation gate。调用 server 返回的、绑定 project/revision 的
`material_studio_gui_activate` preview/next action；用户确认后只激活同一 target，
再用新鲜 status 验证 foreground/visible/non-minimized。不要用盲坐标或通用窗口
搜索替代 exact HWND。

激活成功但截图前失焦时，按 `snapshot_retry_payload` 重试捕获；不要重新打开结构
或启动第二进程。

## 模型可见性差或没有 Fit-to-View

只有 status 报告明确的 low-contrast/not-fit-to-view，并且唯一窗口、前台、可见、
非最小化门禁通过时，才跟随只读 Fit-to-View preview。执行仍需明确确认。capture
limitation、stale revision、外部视觉确认或目标不活跃时，不应自动 Fit-to-View。

截图不是结构验证。始终核对 project/revision 和 structure SHA-256。

## 路径包含空格或中文

使用完整参数边界和成对引号，例如：

```bat
Configure-MS-MCP.bat -Runner "<runner path with spaces>" -Workspace "<workspace path with non-ASCII characters>"
```

不要把整条命令作为一个字符串再二次执行，不要用 `Invoke-Expression`，不要在路径
末尾手工添加 `&`、管道或重定向。默认 Test 会在含空格/中文的隔离路径中覆盖这一
场景；失败时保留原始 Unicode 路径和退出码，但公开报告应脱敏私人目录。

## Windows 长路径

深层 plugin cache、长用户名和长 workspace 叠加可能超过传统路径限制。优先选择
较短的 workspace/解压目录；不要缩短或重命名 runtime 内部已由 manifest 绑定的
文件。安装器/runtime 可能使用 `\\?\` 形式做文件 I/O，但传给外部程序的 cwd 与
参数仍必须遵循被验证的 Windows/Materials Studio 能力。

若组织允许，可由管理员审核并启用 Windows long path policy；这不是安装器自动
修改注册表的理由。重新 Configure 后验证 manifest 和 protocol smoke。

## symlink、junction 或 reparse point 被拒绝

这是安全行为。runtime、plugin payload、runner 和 managed config 路径不接受链接
逃逸或 junction 穿越。把文件复制到真实、普通、用户可写的本地目录，重新校验
SHA-256，再 Configure/Install。不要修改脚本以跳过检测。

## shell argument injection 或路径穿越被拒绝

参数中的控制字符、命令分隔符、`..` 越界、绝对 archive member、驱动器切换或
UNC/reparse 越界都应 fail closed。使用单独参数传值并让 wrapper 保持原边界。若
合法文件名确实包含危险 shell 字符，移动到简单本地路径，不要禁用验证。

## 卸载后 workspace 是否还在

这是预期行为。先运行：

```bat
Uninstall-MS-MCP.bat --dry-run
```

默认卸载只删除 install manifest 记录的 managed runtime 和插件本地配置，保留
workspace、revision、计算结果和用户模型。不要手工把 workspace 加入删除清单。
恢复时重新 Configure → Install → Test 并安装插件；原 workspace 仍可供审核使用。

## 手动 MCP 注册 fallback

IDE 的 bundle-only 场景应把 `codex plugin add --json` 返回的 cache
`Run-MS-MCP.bat` 人工绑定到 STDIO MCP，并保留 plugin-mode env、prompt 审批与
`disabled_tools=["material_studio_run_script"]`。现有 `register_codex.py`、
`ms-mcp-config-register` 和 `.codex/config.toml.example` 只适用于完整源码 checkout
或 managed-source runtime，因为 registrar 要求仓库根 `run_server.py`。源码路径
先 preview、审核配置 SHA 和 plan ID，再显式 apply。Configure 不修改 active
config；任何 fallback 都不得覆盖无关 server、认证或 trusted-project 设置。

## 仍无法解决时应收集什么

- package/plugin version；
- `release-manifest.json` 中 base/reference SHA、MIT 许可证元数据和 release gate；
- wheel、runtime manifest 与 launcher 的 SHA-256；
- Test 的退出码和脱敏 stderr/log；
- Codex surface/version、`marketplaceName` 与脱敏 `installedPath`；
- runner 是否存在及 Materials Studio version（不要附商业许可证内容）；
- workspace/project/revision 的非敏感 identity；
- GUI 问题的 PID/HWND/count 和 blocker，不上传含私人路径的截图。

当前真实验收状态必须保持准确：

```text
Real Materials Studio: NOT_RUN
Real CASTEP: NOT_RUN
```
