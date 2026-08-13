# Materials Studio GUI Loop 实时热加载

GUI loop 是可选的同窗口热加载传输层。结构仍由 `ModelSpec`、
`SemanticPatch`、不可变 revision、确定性 MaterialsScript/CIF 物化和审计报告
定义；loop 只负责把已经生成并校验的结构导入当前 Materials Studio GUI。
它不会替代建模、验证或显式 `execution_mode="execute"` 确认。

## 目标效果

完成一次 GUI 绑定后，可以在 Codex 对话中直接提出例如：

```text
创建 2×2×2 Si 超胞，execution_mode=execute，热加载到当前 Materials Studio
窗口，Fit-to-View，截图并更新报告。
```

若 `material_studio_gui_loop_status` 返回 `loop_ready=true`，建模完成后的结构
会通过当前窗口内正在运行的 loop 导入；不会为每个 revision 重新走 File/Open，
也不会启动第二个 `MatStudio.exe`。Fit-to-View、截图和报告仍执行原有的精确
revision 与 GUI artifact transaction 门禁。

## 一次性启动步骤

1. 只保留目标 Materials Studio 实例，并先把某个 MCP 生成的 revision wrapper
   加载到该窗口，使 `material_studio_gui_status` 能验证 project/revision。
2. 在 Codex 中调用：

   ```text
   使用 material_studio_gui_loop_prepare，为当前 project/revision 准备 GUI loop。
   ```

3. 查看返回的 `loop_script_path`。在返回值绑定的同一个 Materials Studio 窗口
   中，通过 Script Library/User Menu 运行该 `materials_studio_gui_loop.pl` 一次。
   不要用 `RunMatScript.bat` 代替：runner 是独立脚本进程，不能证明脚本处于
   当前 GUI 上下文。
4. Materials Studio 20.1 首次运行本地 User Menu 脚本时可能显示
   **Choose Workspace Folder**。确认合适的本机临时目录后继续。
5. 在 Codex 中调用：

   ```text
   使用 material_studio_gui_loop_status 检查当前 project/revision，直到
   loop_ready=true。
   ```

6. 之后正常提交显式 execute/hot-load 建模请求。默认 `auto` 会优先使用健康
   loop；要单独验证 loop，可以在 `material_studio_gui_open_structure` 中显式
   指定 `hotload_transport="loop"`。

每个新的 PID/HWND/project GUI 绑定都需要重新执行一次上述启动步骤。User Menu
关闭或脚本菜单项被点击不等于 loop 已就绪；必须以签名状态中的
`loop_ready=true` 为准。

## MCP 工具

### `material_studio_gui_loop_prepare`

为当前精确 wrapper 生成固定 loop 和签名队列，不发送 GUI 输入，也不启动脚本。
关键返回字段包括：

- `status="prepared"`
- `binding.pid`、`binding.window_handle`、`binding.project_id`、
  `binding.base_revision`
- `loop_script_path`、`queue_root`
- `operation_allowlist=["import_structure"]`
- `arbitrary_script_supported=false`、`secret_exposed=false`
- `gui_input_performed=false`、`loop_started=false`
- `required_next_step`

### `material_studio_gui_loop_status`

这是只读检查，可选传入 `job_id` 查看单个任务。重点字段包括：

- `status`: `not_prepared`、`prepared`、`not_ready`、`stale` 或 `running`
- `loop_ready`
- `heartbeat_signature_valid`、`heartbeat_fresh`、
  `heartbeat_age_seconds`
- `loop_lock_matches_heartbeat`
- `current_state_signature_valid`
- `heartbeat_revision_matches_state`
- `heartbeat_document_matches_state`
- `current_revision`、`current_document_name`、`last_job_id`
- `queue.staging|pending|running|done|failed` 和可选 `job`

### `material_studio_gui_loop_stop`

为精确绑定发布签名 stop marker。它不终止 Materials Studio、不关闭窗口，也不
删除文档。stop 是当前队列绑定的会话终点；不要删除 marker 或 lock 来强行重启。
需要继续时，应重新建立并验证新的 GUI 绑定。

### `material_studio_gui_open_structure`

新增 `hotload_transport`：

| 值 | 行为 |
| --- | --- |
| `auto` | 默认。入队前 loop 健康则使用 loop；否则在入队前回退同窗口 File/Open。 |
| `loop` | 强制 loop；未准备、心跳不健康或绑定不符时失败关闭。 |
| `dialog` | 强制使用已有同窗口 File/Open，不检查或使用 loop。 |

成功使用 loop 时应检查：

- `hotload_transport_used="loop"`
- `gui_loop_used=true`
- `same_window_open_used=true`
- `open_result.method="verified_gui_loop_import"`
- `open_result.spawned_process_ids=[]`
- `open_result.loop_receipt.status="done"`
- terminal receipt 的 job、target revision、document name 与 structure SHA-256
  均和排队任务一致
- `gui_loop_live_binding_publication.status="published"`
- `post_open_single_window_policy_ok=true`

## 安全与一致性

实现独立借鉴了
[DrYe1109/MS-MCP 固定版本中的 GUI 轮询模式](https://github.com/DrYe1109/MS-MCP/blob/991a1b3ab2ad985529fb645dc82f47528a2a1297/materialscript/mcp_loop_gui.pl#L4-L100)：
在 GUI 上下文运行、轮询文件队列、使用重命名声明任务并持续写入心跳。本项目
没有复制开放式脚本队列，也没有实现任意 `do $script`。

本实现只允许 `import_structure`。队列内容是 HMAC-SHA256 签名的数据 envelope，
不是可执行代码。任务经过 `staging -> pending -> running -> done|failed` 原子状态
转换，并绑定：

- Materials Studio PID 与顶层窗口 HWND
- MCP workspace 与 project
- expected revision 与 target revision
- 当前及目标文档名
- 结构绝对路径和 SHA-256
- job ID、创建时间与到期时间

GUI loop 在每轮轮询持续刷新签名 heartbeat；提交新 current state 后会先刷新
heartbeat，再发布 terminal receipt，保证紧接着的 `rN -> rN+1` 请求仍能通过
revision compare-and-swap。MCP 端只接受 job ID、target/current revision、document
name 和 structure SHA-256 全部匹配的签名成功回执。

loop 只调用固定的 `Documents->Import`，不会执行计算、删除文件、运行任意 Perl、
切换 project 或绕过现有单窗口门禁。它也不会自动关闭旧文档，以免丢失未保存的
人工编辑；长会话中可能积累多个文档，需要用户确认保存状态后自行整理。

## 回退和故障处理

`auto` 回退只允许发生在任务发布到 `pending` **之前**。一旦任务入队，超时、
`failed`、窗口身份变化、revision 冲突或 terminal receipt 不匹配都禁止自动改走
dialog，因为导入可能已经产生副作用。相关错误会保留：

- `job_id`
- `side_effect_may_have_occurred=true`
- `automatic_dialog_fallback_allowed=false`

此时先调用 `material_studio_gui_loop_status(job_id=...)` 和
`material_studio_live_project_status` 核实实际状态，不要重复执行同一热加载。

常见状态：

- `loop_start_required` / `loop_not_ready`：在精确窗口中启动生成脚本并等待新鲜心跳。
- `queue_busy`：已有 pending/running 任务；先检查对应 job ID。
- `revision_conflict`：调用方 revision 与签名 current state 不一致；刷新项目状态。
- `terminal_receipt_binding_mismatch`：成功回执与排队任务身份不一致；保留证据并人工复核。
- `timeout`：导入可能已经发生；禁止 dialog 重试。
- `loop_completion_window_identity_changed`：完成期间 PID/HWND 改变；人工核对窗口。

## 环境变量

```text
MATERIAL_STUDIO_GUI_HOTLOAD_TRANSPORT=auto
MATERIAL_STUDIO_GUI_LOOP_TIMEOUT_SECONDS=45
MATERIAL_STUDIO_GUI_LOOP_HEARTBEAT_TTL_SECONDS=10
```

- `MATERIAL_STUDIO_GUI_HOTLOAD_TRANSPORT` 接受 `auto`、`loop`、`dialog`。
- `MATERIAL_STUDIO_GUI_LOOP_TIMEOUT_SECONDS` 是等待签名 terminal receipt 的正整数秒数。
- `MATERIAL_STUDIO_GUI_LOOP_HEARTBEAT_TTL_SECONDS` 是 heartbeat 仍被视为新鲜的正整数秒数。

修改 MCP 进程环境变量后需要重启 Codex/MCP 服务。单次
`material_studio_gui_open_structure(hotload_transport=...)` 可覆盖默认传输选择。
