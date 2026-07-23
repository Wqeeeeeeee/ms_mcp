# Materials Studio MCP 服务

通过 BIOVIA MaterialsScript 自动化 Materials Studio 的本地 MCP 服务。

本项目面向 Materials Studio 2020/20.1，正确入口是 `RunMatScript.bat`，不是
`MaterialsStudio.Application` COM。服务会自动探测本机 Materials Studio runner。

## 安装

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

## 启动

```powershell
uv run ms-mcp
# 或
.\.venv\Scripts\python.exe run_server.py
# 或使用启动脚本
.\start-material-studio-mcp.cmd
```

## MCP 配置

```json
{
  "type": "stdio",
  "command": "<仓库目录>\\.venv\\Scripts\\python.exe",
  "args": ["<仓库目录>\\run_server.py"]
}
```

Claude Code 配置：

```powershell
$repo = (Resolve-Path .).Path
claude mcp add material_studio_mcp_server "$repo\.venv\Scripts\python.exe" "$repo\run_server.py" -s user
```

## Materials Studio 自动探测

服务启动后自动查找 `RunMatScript.bat`。自动探测失败时设置：

```powershell
$env:MATERIAL_STUDIO_RUNNER = "<MS安装目录>\etc\Scripting\bin\RunMatScript.bat"
```

## 可用工具

### 核心工具（来自 noc228076/materials-studio-mcp）

- `material_studio_get_status`：检查服务和 runner 状态
- `material_studio_build_molecule`：用 CreateAtom/CreateBond 生成分子 XSD
- `material_studio_build_tnt`：直接生成 TNT XSD，可选 Forcite 优化
- `material_studio_run_script`：执行自定义 MaterialsScript Perl
- `material_studio_validate_script`：检查 MaterialsScript 基本结构
- `material_studio_import_export`：导入结构并导出为另一格式
- `material_studio_structure_summary`：读取结构基础信息
- `material_studio_forcite_geometry_optimization`：Forcite 几何优化
- `material_studio_castep_energy_script`：按 Materials Studio 20.1 API 生成任务感知的 CASTEP 预览脚本（保留兼容工具名）
- `material_studio_list_script_templates`：列出内置脚本模板

### 结构化建模工具

这些工具把自然语言建模请求拆成 `ModelSpec`、`SemanticPatch`、校验、脚本预览、可选执行和版本历史。默认只预览，不调用 Materials Studio runner。

- `material_studio_model_create_from_spec`：从结构化规格创建项目和 revision
- `material_studio_model_modify_with_patch`：用语义 patch 修改当前项目
- `material_studio_model_validate`：校验规格或当前项目
- `material_studio_model_preview_script`：预览生成的 MaterialsScript Perl
- `material_studio_model_get_current`：读取当前规格
- `material_studio_project_history`：读取 revision 历史
- `material_studio_project_rollback`：非破坏式回滚
- `material_studio_project_reconcile_dopant_metadata`：在新 revision 中调和失效掺杂位点元数据，并验证结构与 simulation 未变化
- `material_studio_forcite_dynamics_from_spec`：结构化 Forcite Dynamics 预览/显式执行
- `material_studio_castep_relax_current`：预览或显式执行当前晶体的 CASTEP 几何优化，仅提升已验证收敛结果
- `material_studio_castep_run_current`：预览或显式执行 Energy、BandStructure、DOS、PDOS，以结构不变的 metadata revision 记录结果，并审计原生 `.castep`/`.bands` 数值证据

结构化晶体响应中的 `calculation_preview` 仍把 companion 标记为
`execution_policy="preview_only"`。当任务有专用执行路径时，
`execution_handoff` 会提供绑定 `working_dir`、`project_id` 和
`expected_revision` 的可直接调用 preview；任何 execute payload 仍要求用户另行明确确认。
- CASTEP 原生带边审计按自旋通道使用各自 Fermi 能级，导出采样 VBM/CBM、采样间隔、Fermi 穿越和 `BandGap` 交叉核对；这些字段始终保持 `scientific_band_gap_verified=false`，不能替代完整能带路径、收敛性或科学带隙验证
- `castep_electronic_result_assessment` 将 revision/hash 绑定的产物证据与科学收敛、科学带隙结论分开；结果复核不会被误报为结构异常，建议重算始终先返回 `execution_mode="preview"`
- `castep_electronic_results` 诊断焦点支持“检查当前 CASTEP 结果”等只读请求，并导出结果摘要及 aggregate/per-spin/crossing-band 的 `semiconductor_castep_band_edges.csv`
- `castep_convergence_audit` 从多个不可变、哈希绑定的 CASTEP 结果 revision 独立比较截断能、K 点间距、自定义 K 点网格或性质 K 点间距；两点只提供成对敏感性证据，至少三点才形成序列
- `castep_convergence_series` 诊断焦点可只读检查收敛序列并导出 `semiconductor_castep_convergence_series.csv`；默认阈值为总能量变化 0.01 eV/atom、报告 `BandGap` 变化 0.05 eV，阈值内也不会宣称已科学收敛，后续计算只先给出 preview
- 显式 `periodic_maximin` 合金/掺杂位点会导出逐壳层 AA/AB/BB 配对和有限组成修正的 Warren-Cowley 型描述性审计 `semiconductor_site_short_range_order.csv`；它只覆盖有限超胞唯一位点对，不等同于标准周期壳层 Warren-Cowley、SQS 质量、统计显著性或热力学短程有序

### 扩展工具（本地 crystal/interface builders）

- `build_crystal`：从晶格参数生成 CIF 结构文件
- `build_cu_sio2_interface`：构建 Cu(100)/SiO₂(100) 界面模型
- `validate_structure`：轻量级结构验证
- `parse_results`：解析计算结果
- `diagnose_failure`：分类失败原因
- `request_gui_check`：生成 Computer Use 检查清单

## 测试

```powershell
python -m pip install pytest
python -m pytest -q
```

## MCP Protocol Acceptance

Before relying on `@mcp`, audit the active Codex registration without changing
it:

```powershell
.\.venv\Scripts\python.exe -m material_studio_mcp_server.codex_config `
  --cwd . `
  --output-snippet workspace\codex_config\materials_studio.toml
```

The doctor reports missing or legacy registration, entrypoint and allowlist
drift, and the exact absolute paths for this checkout. It writes only the
separate snippet path supplied above and refuses to overwrite the active
`%USERPROFILE%\.codex\config.toml`.

For a missing registration, preview a guarded append without changing the
active config:

```powershell
ms-mcp-config-register --cwd . --omit-snippet
```

After reviewing the returned paths, hashes, and `registration_plan_id`, apply
only that exact plan with
`ms-mcp-config-register --cwd . --apply --expected-plan-id <REVIEWED_ID>`.
The command preserves existing bytes, creates an exact backup, refuses stale or
conflicting registrations, and returns a hash-bound rollback command. It never
restarts Codex or touches Materials Studio. Restart Codex after an approved
change, then call `material_studio_live_session_preflight`. If automatic append
is blocked, review and merge the generated snippet manually.

For a long-lived `@mcp` registration, deploy a clean pushed commit instead of
pointing Codex at a temporary PR worktree:

```powershell
.\.venv\Scripts\python.exe deploy_runtime.py --source .
.\.venv\Scripts\python.exe deploy_runtime.py --source . --apply `
  --expected-plan-id <REVIEWED_RUNTIME_DEPLOYMENT_PLAN_ID>
```

The first command is read-only. The second publishes the exact Git archive
under `%LOCALAPPDATA%\materials_studio_mcp\runtimes\<commit>`, validates the
stdio tool contract, and returns a separate fingerprint-bound registration
plan and command. It does not edit the active Codex config, restart Codex, or
touch Materials Studio. Review and explicitly apply the returned registration
plan, then restart Codex while leaving the single Materials Studio window open.
Managed runtimes are never overwritten or automatically deleted; their
manifest and complete file snapshot are reverified at startup and during
preflight.

For resumed projects, the preflight keeps the legacy `next_action_plan` as the
immediate session-control action and coordinates three revision-bound tracks:
session control, visual diagnostics, and modeling. Follow
`coordinated_next_action_plan.recommended_sequence` and resolve each `plan_ref`
to its top-level action plan. Activating/reloading the GUI does not clear a
pending replay or modeling step, and preparing visual diagnostics does not
satisfy a later modeling action's explicit confirmation gate.
The preflight response is independently bounded to a 45 KB target and 48 KB
hard budget. Its `response_compaction` receipt reports the exact serialized
size and points to the full runner, GUI, and project-status tools when a compact
`*_ref` replaces duplicated probe internals.

Long-lived MCP processes also return `runtime_provenance`. It binds one process
instance to a deterministic SHA-256 snapshot of every Python source under
`material_studio_mcp_server` at import time and compares it with the current
source tree. If `source_current=false`, preflight returns
`state="mcp_server_restart_required"` and blocks preview, execution, and GUI
input until the MCP server is restarted and both source hashes match. Restarting
the MCP server does not require closing or launching Materials Studio. The
runner receipt separately reports `default_workspace_root` and
`request_workspace_root`; explicit tool `working_dir` values remain authoritative.

Use the real MCP stdio client to verify initialization, tool discovery, input
schemas, safety annotations, and preview-only live modeling calls:

```powershell
.\.venv\Scripts\python.exe -m material_studio_mcp_server.protocol_smoke `
  --cwd . `
  --workspace workspace\mcp_protocol_acceptance `
  --config .codex\config.toml.example `
  --output workspace\mcp_protocol_acceptance\summary.json
```

The smoke test uses an isolated workspace, does not execute Materials Studio,
and does not open or change the GUI. Add `--strict-config` only when config
drift should make the command fail. The config audit never edits the file.

Interactive MCP clients should pass `response_mode="compact"` to the live
capabilities, create/update, status, view-bundle, and GUI-apply tools. This
keeps the protocol response bounded while preserving full diagnostics in
`report.json`, `view_audit.json`, and the view-bundle manifest. The default
remains `full` for backward compatibility.

Compact schema v2 targets 45 KB and enforces a 48 KB protocol acceptance budget
for capabilities, create, status, and view-bundle replies. It keeps camera
parameters, current GUI binding, normality explanations, visual conclusions,
and the authoritative next-action payload. Repeated action payloads, successful
diagnostic-focus detail, evidence trees, and capability catalogs remain
available through `response_mode="full"` and the persisted report artifacts.
If a complex all-view response still reaches the hard budget, the receipt adds
`response_compaction.hard_budget_applied=true` and lists each omitted duplicate
field. Check `semantic_core_preserved`, `response_bytes`, and `headroom_bytes`
before summarizing the result; manifest paths remain the authoritative fallback.

When GUI view replay is already complete and has no callable follow-up action,
compact mode replaces inert null action templates and repeated replay evidence
with a terminal receipt plus explicit `response_mode="full"` detail references.
Pending, blocked, review-required, and automation-ready replay states still
retain their callable payloads, execution recipes, observation requirements,
and safety gates in-band.

Live GUI status performs a bounded provenance lookup across the active
workspace, `MATERIAL_STUDIO_MCP_WORKSPACE`, and the platform default user
workspace. If the visible MCP wrapper belongs to a different workspace,
preflight reports `preview_ready_gui_workspace_context_mismatch` and returns the
exact `recommended_working_dir`, project, and revision. Omitted-project
follow-up edits stop before writing a revision; the server never switches to or
writes the visible wrapper workspace automatically.

Computer Use or manual viewport evidence can be recorded through the enabled
high-level `material_studio_live_modeling_request` tool by supplying a
`visual_confirmation` object. The payload must contain the observed revision,
window handle, and exact MCP wrapper title. The server rejects stale or
unmatched windows and does not create a revision. For an ongoing session,
`project_id` may be omitted; the server resolves the latest current project and
accepts the evidence only when its revision matches the supplied observed
revision. Visual confirmation proves what is visible; it cannot clear
structural or semiconductor health failures. GUI open, snapshot, and visual
confirmation re-audits preserve a current revision's custom view selection only
when the persisted project, revision, and spec fingerprint still match. Their
`view_selection_resolution` receipt reports whether explicit, persisted, or
default views were used.

Prepared camera/view replay can use the same restricted high-level entry via a
`view_replay_confirmation` object. It requires the prepared view name, current
revision, exact wrapper handle/title, and camera-manifest review result. A
reviewed Materials Studio 20.1 command such as `cmdViewer3DResetView` can be
recorded as `native_command_id`. Failed window binding writes no replay event;
accepted evidence updates the revision-scoped replay manifest without creating
a model revision.

Each prepared view now carries an `execution_recipe`, and the manifest exposes
`replay_continuation` with pending views and the next safe action. Materials
Studio 20.1 supplies deterministic six-face orthographic recipes plus a staged
isometric recipe. They become automatic-ready only after exact-window runtime
accessibility evidence verifies the required named controls.
`front` uses the named `cmdViewer3DResetView` accessibility command; `back`
uses Reset + `Left x4`; `right` uses Reset + `Up x2, Left x2`; `left` uses
Reset + `Up x2, Right x2`; `top` uses Reset + `Up x2`; and `bottom` uses Reset
followed by `Left x4, Down x2`. The installed help documents 45-degree arrow
rotation and separately documents Shift+arrow as rotating selected objects, so
the replay receipt must record `modifier_keys=[]` and visual axis/projection
checks. Isometric uses a verified staged recipe: Reset, `45 degrees: Up x2,
Left x3`, then `35.26438968 degrees: Down x1`; it must restore the Movement
angle to 45 degrees, preserve Screen factor 2.0, close Movement, and verify A
left-down, B right-down, C up. `material_studio_gui_execute_view_replay` can
execute that exact staged recipe locally after binding the single current
wrapper, exact Movement window/control tree, disabled `cmdNudge*` inventory,
ValuePattern readbacks, and unique semantic viewport; preview never opens the
dialog or sends input, and execution still requires separate screenshot review
before acceptance. Submit externally collected live observations as
`runtime_accessibility_evidence`; it is persisted in
`gui_view_replay_accessibility_preflight.json` only after revision, wrapper
handle/title, and single-window binding pass. Static registry/help evidence is
not an automation grant. When MS exposes only unnamed toolbar children, their
mapping must come from the server's exact live toolbar/registry verification;
client-guessed indexes or coordinates are prohibited. `crystal_plane_*` views
can also become automatic-ready when the installed Miller
Plane/Properties/View Onto evidence is complete and the local transactional
executor can verify one supported semantic selection profile. External replay
still requires a current-window `runtime_ui_evidence` probe bound to the exact
revision and wrapper handle/title. Static registry/help evidence alone is
insufficient. The
dialog must be opened with `Alt+T`, then `M`; pointer/accessibility menu clicks
are prohibited because they can click through and create a default plane. Any
dialog control must then be targeted from a fresh modeless child-window state:
use an in-bounds accessibility element or a coordinate derived from the fresh
child screenshot, never a parent-window coordinate or an out-of-bounds duplicate
element. `TxtHKL` replacement is not assumed to succeed from `Ctrl+A`: read the
value back from a new child accessibility state, correct it with exact `set_value`
or a verified clear-and-type fallback, and permit Create only when the trimmed
text exactly matches `dialog_miller_indices_text`. The replay event must persist
that readback in `dialog_miller_indices_text_before_create`, its source, and the
verification boolean. Any unexpected plane must be removed with the exact named undo and the
replay attempt must stop. On versions that expose Object Tree, the recipe may select
its exact new leaf. The verified MS 20.1 fallback instead derives one unique
transient-plane region from fresh before/after screenshots, selects it without
modifiers, and requires Properties Explorer to show `Filter=Miller Plane` and
the exact Miller label before the named View Onto action. Project Explorer is
not an Object Tree substitute, and old viewport coordinates are never reused.
The recipe records strict `miller_plane_evidence` proving a pre-action viewport
baseline, no Reset, live `View Onto=33297` mapping, exactly the View Onto/Create
Plane undo sequence, pixel-identical viewport restoration, and an unchanged
structure hash. `material_studio_gui_execute_view_replay` can perform this
bounded transaction after explicit execute intent; its aligned screenshot and
mechanical receipt still require visual review before acceptance. The
camera receipt verifies the reciprocal-plane normal plus MS native in-plane
roll; it does not misreport exact analytic up/right agreement. A lattice
direction `crystal_*` view can use the same transient-plane workflow only when
its direct-space vector is numerically collinear with an exact bounded
integer reciprocal-plane normal. The manifest supplies the mapped Miller
indices and requires explicit direction-match evidence; non-collinear
directions remain reviewed-backend gated. The implementation never assumes
that `[uvw]` and `(hkl)` with the same indices are equivalent. Continuous
Spin/Roll/Rock and object nudge/align commands are never accepted as camera
replay.

The reviewed Copy Script fallback is evidence-only. A replay recorded with
`source="reviewed_copy_script"` must include the exact script in
`reviewed_copy_script_evidence`, the exact current wrapper handle/title, and a
workspace screenshot. The script is never executed. Safe inert text is archived
under `gui_copy_script_evidence/` with its SHA-256 and JSON review metadata;
scripts containing shell, network, file import/export/delete, calculation, or
structure-mutation signals retain only their hash and static rejection analysis.
Accepted reviewed evidence is also revalidated on every live status or manifest
refresh. The screenshot, inert script, metadata, and structure artifact have
separate SHA-256 records. If any artifact is missing or changed, the append-only
event remains in history but no longer counts as an accepted view or valid
external visual confirmation until fresh evidence is recorded.
Each new replay event also has a stable SHA-256 record digest in both the
manifest and the durable `gui_view_replay_events.jsonl` journal. Status compares
the two independent copies. Missing, duplicate, or changed journal records keep
their historical files but no longer satisfy trusted view acceptance. Status is
read-only and never repairs one copy from the other.
Prepare and record writes for the same project/revision are serialized with an
OS-managed advisory lock. Concurrent MCP calls therefore read the latest
manifest in order, and a lock timeout fails before any event is appended.
Visual-confirmation report updates use a separate project/revision lock so
concurrent manual or replay-derived confirmations retain both GUI artifacts.
The stable `report.json` entry point is flushed to a temporary file and
atomically replaced; interrupted publication leaves the prior report intact.

Conversation-style requests such as `continue the next GUI view replay` or
`继续验证下一个 GUI 视角` route through the high-level live modeling tool. The
`continue_view_replay` workflow prepares or upgrades the current manifest when
needed and returns its continuation receipt without issuing GUI input or
creating a structural revision.

An automation-ready receipt separates GUI execution from evidence recording.
`execution_action` describes the exact Computer Use operation, while
`post_action_record_payload_template` remains non-callable with null observation
fields until the action, fresh screenshot, and current-window postcheck have
completed. Prepared values never count as accepted replay evidence.

## GUI 控制层

Open-GUI 工作流是可选的。结构化的 `ModelSpec`/`SemanticPatch` 工作流仍然是事实来源；
GUI 工具激活已打开的 Materials Studio 窗口、打开生成的结构、捕获快照，并帮助提取 Copy Script。

新的 GUI 工具：

- `material_studio_gui_status`
- `material_studio_gui_activate`
- `material_studio_gui_snapshot`
- `material_studio_gui_open_structure`
- `material_studio_gui_apply_current_revision`
- `material_studio_gui_copy_script_assist`
- `material_studio_gui_prepare_view_replay`
- `material_studio_gui_execute_view_replay`
- `material_studio_gui_record_view_replay`

这不是 COM 自动化，也不手写 `.xsd` XML。如果 Computer Use 不可用，本地回退仍然支持进程检测、
激活、打开结构、BMP 快照和 GUI 操作日志。视角回放先生成 revision 级清单，并且只执行
`automation_ready=true` 的 recipe。晶向与有界整数 Miller 面法向严格共线时可复用临时平面
View Onto 路径，但必须记录映射和平面/晶向双重证据；其他晶向仍需 Computer Use 或本机
Copy Script 审核。MCP 不猜测 MaterialsScript 相机 API，也不把同指标 `[uvw]` 与 `(hkl)` 等同。
半导体诊断还会把具体掺杂位点记录与当前原子表逐项核对；若记录元素与实际元素不一致，
即使当前 revision 已热加载到 GUI，也会阻断“模型正常”和“可计算”的结论，直到元数据调和并重新审计。
半导体层状结构支持可审计的横向堆垛配准调整，例如 `shift layer 3 by 0.5 angstrom along x`
或 `将顶层沿 y 方向平移 -0.25 埃并热加载`。层号先解析为明确的原子 ID，随后通过
`translate_crystal_atoms` 刚性平移并周期回卷；`layer_translation_summary` 和
`semiconductor_layer_translation.csv` 会记录目标绑定、位移及回卷原子，法向位移则继续使用
界面间距或层厚工具。
层旋转/扭转也使用同一层剖面进行精确绑定，例如 `twist the top layer by 3 degrees`
或 `将第 2 层绕 c 轴旋转 5 度并热加载`。`rotate_crystal_atoms` 会围绕周期质心对目标原子组
执行笛卡尔刚体旋转，并通过 `layer_rotation_summary` 与
`semiconductor_layer_rotation.csv` 保存原子 ID、旋转轴、角度、枢轴和坐标摘要。任意扭角默认是
非共格、仅供可视审阅的结构脚手架：它可以在已验证的单一 MS 窗口中热加载，但在构建共格超胞并完成
几何弛豫前，MCP 会阻断“模型正常”和“可计算”的结论。
对于明确的 TMD 共格请求，`make_commensurate_twisted_bilayer` 会从干净、周期性的
MoS2/WS2/MoSe2/WSe2 单层模板构造精确整数共格同质双层。可直接指定互素整数
`m > n >= 0`，例如 `构建 m=2,n=1 的共格扭转双层二硫化钼，层间距 6.15 埃`；也可给出目标角度，
规划器会在 0.1 度容差和 2000 原子默认上限内选择角度误差最小、原子数次优的共格候选。`commensurate_twist_summary`
和 `semiconductor_commensurate_twist.csv` 会重新验证整数矩阵、理论扭角、公共晶格、层原子绑定、
层间距和完整结构 SHA-256。该结果是精确周期共格的预弛豫结构，可在已验证的单一 MS 窗口中热加载，
但 `requires_geometry_relaxation=true`，完成并绑定可信弛豫结果前仍不会标记为可计算。
不同 TMD 材料组成的共格异质双层使用 `make_commensurate_tmd_heterobilayer`，例如
`构建 MoS2/WSe2 共格扭转异质双层，m=2,n=1，并热加载到 Materials Studio`。材料书写顺序固定为
底层/顶层；支持 `balanced`、`bottom_fixed`、`top_fixed` 三种面内双轴应变分配策略，默认最大绝对应变
为 3%，原子数上限仍为 2000。只有在明确记录应变后才形成精确整数重合晶格，超出阈值会被拒绝，
不会偷偷改变材料、角度或应变策略。`commensurate_heterobilayer_summary` 与
`semiconductor_commensurate_heterobilayer.csv` 会复核两层组成、应变分配、整数矩阵、扭角、层间距、
真空和结构 SHA-256。该模型可在预检通过后热加载到同一个 MS 窗口，但始终标记为预弛豫结构，
完成可信几何弛豫并重新审计前不能声称模型正常或可计算。
该路径会自动补充 `two_dimensional_electrostatic_preflight`。它按完整上下层材料和元素计数识别预期的
二维组成非对称，因此 MoS2/WS2 即使两个最外表面都是 S，也不会被误判为表面几何异常。
`two_dimensional_electrostatic_summary` 和 `semiconductor_2d_electrostatics.csv` 会记录结构 SHA-256、
真空/居中状态、外表面是否同构以及层组成绑定。该预检不含电荷密度，不会计算面外偶极，也不会声称
已验证或启用 CASTEP 偶极修正；在经审阅的 Materials Studio Copy Script 或已记录的 CASTEP UI 设置
确认前，定量静电计算仍保持阻断。
晶体 execute/hot-load 还会重新解析生成的 CIF，逐项核对原子 ID、元素、分数坐标和六个晶格参数。
已有 CIF 与当前 `CrystalSpec` 不一致时，即使路径和窗口 revision 正确也会阻断正常性结论；重新物化需要显式确认且不会创建空 revision。
