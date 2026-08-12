# 真实 Materials Studio 2020/20.1 验收

## 当前状态

```text
Real Materials Studio: NOT_RUN
Real CASTEP: NOT_RUN
```

当前任务没有授权真实 GUI 验收。本页是未来获得明确授权后的操作清单，不是已执行
结果。import、compileall、protocol smoke、mock/fake GUI 和离线截图都不能改变上面
的状态。

## 授权与范围

只有用户在当前任务中另行明确授权真实 Materials Studio 操作后，才能开始。
授权不自动包含计算。此最小验收：

- 只复用用户手工打开的唯一一个 Materials Studio 2020/20.1 窗口；
- 允许只读 status/preflight、预览一个小型 Si 或 3C-SiC、经第二次确认后物化并
  热加载、Fit-to-View 和导出视角证据；
- 不启动第二个 Materials Studio 进程；
- 不运行 CASTEP、DMol3 或 Forcite；
- 不使用任意 MaterialsScript，不放开 `material_studio_run_script`；
- 不使用盲坐标、未验证 toolbar 或任意 GUI script queue。

若用户只授权“检查状态”，不得推断其同时授权建模、runner、热加载或 GUI 输入。

## 前置条件

- Configure、Install 和默认 Test 已通过。
- package、plugin 和 runtime manifest version 一致。
- wheel/runtime SHA-256 验证通过。
- `material_studio_run_script` 默认禁用。
- workspace 是用户明确选择的稳定路径。
- 用户保存了窗口内所有未保存工作，并手工关闭其他 Materials Studio 顶层窗口。
- 用户手工打开一个 Materials Studio 窗口；Codex 不负责启动它。
- 不存在待运行 CASTEP、DMol3 或 Forcite 的隐式任务。

可以先运行：

```bat
Test-MS-MCP.bat --real-ms
```

该参数必须再次请求人工确认，并且仅执行安装器内经过审查的真实只读/preview
preflight；它本身不得热加载或运行计算。受控非交互验收必须同时显式提供
`-ConfirmRealMS -NonInteractive`，不能由普通 CI 默认启用。

## 最小验收步骤

### 1. 只读 status 与 preflight

调用 `material_studio_get_status`、`material_studio_live_session_preflight` 和
`material_studio_gui_status` 的只读路径，记录：

- project/workspace resolution；
- Materials Studio version；
- process count 和 top-level window count；
- 目标 PID、HWND、窗口标题、前台/可见/最小化状态；
- runner 路径和 runtime manifest identity；
- `single_window_policy_ok` 与 provenance 状态。

只要窗口不是唯一、目标不明确、workspace provenance mismatch、目标最小化/不可见
或 revision 绑定不确定，就停止。按返回的 blocker 处理，不自动启动或关闭进程。

### 2. 预览小型 Si 或 3C-SiC

通过 `material_studio_live_modeling_request` 提交清楚的 preview，例如：

```text
预览创建一个小型 silicon diamond 晶胞。execution_mode=preview；不运行 runner，
不打开或修改 GUI，不执行任何计算。
```

或使用已由当前 main 支持的 3C-SiC 模板。审核 exact spec、验证、计划的
project/revision、结构化 blocker、artifact 路径和 next action。不得添加新模板或
用相近材料替代。

preview 阶段应证明没有 runner、GUI input 或计算。若 preview 意外产生这些副作用，
验收失败并停止。

### 3. 第二次明确确认 execute

把 preview 内容展示给用户，要求针对“物化结构并热加载到当前已验证唯一窗口”做
第二次确认。确认必须发生在 status/preflight 之后，并引用同一 project/revision。

使用 preview 返回的 exact execute payload，不手工删除
`working_dir`、`expected_revision`、窗口或 provenance 绑定。执行前再次检查当前
revision 与 PID/HWND 未变化。

### 4. 物化与热加载

执行 runner/晶体物化，验证 immutable execution attempt、script/spec SHA-256、
structure artifact SHA-256 和 result metadata。只有同一 revision 仍为 current 且
唯一窗口门禁通过时，才把结构热加载到该窗口。

记录热加载前后 Materials Studio process count。必须保持一个进程；不得通过
`material_studio_gui_launch` 新建第二个进程来绕过状态问题。

### 5. Fit-to-View

结构成功热加载后，先调用 `material_studio_gui_fit_to_view` preview。确认其绑定
同一 project/revision/PID/HWND、当前 accessibility mapping 和 structure SHA-256。
用户明确确认后再 execute。要求：

- `post_hotload_fit_to_view.completed=true`；
- `structure_unchanged=true`；
- 最终 snapshot 与同一 revision 绑定。

若执行被阻塞，按返回的 exact Fit-to-View retry payload 重试；不得重新物化结构。

### 6. front、top、isometric 视角证据

导出或准备 `front`、`top`、`isometric` 诊断。每个视角先读取当前 recipe 与
`replay_continuation`，只在 `automation_ready=true` 时使用允许的自动路径。所有
证据必须绑定：

- project ID 与 revision；
- PID、HWND、窗口标题与 single-window 状态；
- structure artifact SHA-256；
- 新鲜 workspace screenshot/视角证据；
- 当前 recipe/schema 与记录事件的一致性。

截图只能作为 GUI 可见性/视角证据，不能替代结构验证、模型正常性、计算就绪或
科学验证。视角应用后调用对应 record 工具；未通过完整 evidence/journal gate 的
视角不能标记 accepted。

### 7. 结束检查

重新调用只读 status，确认：

- current project/revision 与预期一致；
- PID/HWND 未变；
- Materials Studio process count 仍为 1；
- structure SHA-256 与已物化 artifact 一致；
- 没有 CASTEP、DMol3 或 Forcite attempt/run directory；
- 没有额外 revision 或未解释的 GUI replay event；
- workspace 以外没有写入用户模型或配置。

## 证据记录表

| 项目 | 必须记录的实际值 |
|---|---|
| 授权文本与时间 | 用户在当前任务中的明确授权 |
| package/plugin version | 两者必须一致 |
| runtime manifest SHA-256 | 实际 digest |
| project/revision | preview、execute、GUI 证据一致 |
| PID/HWND/title | 唯一目标窗口 |
| 进程/窗口计数 | 热加载前后均为 1 |
| spec/script SHA-256 | runner attempt 所绑定的值 |
| structure SHA-256 | artifact、hot-load、视角证据一致 |
| Fit-to-View receipt | completed、structure unchanged、snapshot bound |
| front/top/isometric | recipe、截图、record/event integrity |
| CASTEP/DMol3/Forcite | 均为 NOT_RUN |

不要把含私人本机路径的截图放进发布 ZIP、PR 描述或公开日志。必要时只记录经过
脱敏的 workspace 相对路径与 SHA-256。

## 立即停止条件

- 多于一个 Materials Studio 进程或顶层窗口；
- 目标窗口非前台、不可见、最小化或身份不确定；
- project/revision、workspace provenance 或 structure SHA 不匹配；
- runner/runtime manifest 校验失败；
- current revision 在等待/执行期间前进；
- recipe 过期、accessibility evidence 不新鲜或 GUI postcheck 失败；
- 出现意外计算、第二进程、任意脚本或 workspace 外写入；
- 用户撤销授权。

停止后保留已有不可变证据，返回结构化 blocker 和 server 给出的 exact next
action。不得自动重试有副作用步骤，也不得降低门禁。

## 结果报告

未执行时保持：

```text
Real Materials Studio: NOT_RUN
Real CASTEP: NOT_RUN
```

实际执行后，只能按观察事实写 PASS/FAIL/PARTIAL，并列出 project/revision、
PID/HWND、structure SHA-256、进程计数和每一步 receipt。即使本页最小真实验收
通过，CASTEP 仍必须是 `NOT_RUN`；CASTEP 需要独立授权和独立科学验收。
