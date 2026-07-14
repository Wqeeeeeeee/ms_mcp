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
- `material_studio_castep_energy_script`：生成 CASTEP Energy 脚本
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
`%USERPROFILE%\.codex\config.toml`. Review and merge the snippet manually,
preserving unrelated config, then restart Codex before calling
`material_studio_live_session_preflight`.

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

Compact schema v2 enforces a 48 KB protocol acceptance budget for capabilities,
create, status, and view-bundle replies. It keeps camera parameters, current GUI
binding, normality and next-action gates, and the complete `view_bundle_files`
index in normal compact projection. Repeated evidence trees and capability catalogs remain available through
`response_mode="full"` and the persisted report artifacts.
If a complex all-view response still reaches the hard budget, the receipt adds
`response_compaction.hard_budget_applied=true` and lists each omitted duplicate
field. Camera parameters and artifact indexes are retained first; the manifest
paths remain the authoritative fallback.

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
left-down, B right-down, C up. Submit that live observation as
`runtime_accessibility_evidence`; it is persisted in
`gui_view_replay_accessibility_preflight.json` only after revision, wrapper
handle/title, and single-window binding pass. Static registry/help evidence is
not an automation grant. When MS exposes only unnamed toolbar children, the
recipe stays review-gated and unnamed element indexes or coordinates are
prohibited. `crystal_plane_*` views can also become
automatic-ready when the installed Miller Plane/Properties/View Onto evidence
is complete, a supported semantic selection profile is verified, and a
current-window `runtime_ui_evidence` probe is bound to the exact revision and
wrapper handle/title. Static registry/help evidence alone is insufficient. The
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
The recipe records strict `miller_plane_evidence` proving exact cleanup, Reset
View restoration, and an unchanged structure hash. The
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

Conversation-style requests such as `continue the next GUI view replay` or
`继续验证下一个 GUI 视角` route through the high-level live modeling tool. The
`continue_view_replay` workflow prepares or upgrades the current manifest when
needed and returns its continuation receipt without issuing GUI input or
creating a structural revision.

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
- `material_studio_gui_record_view_replay`

这不是 COM 自动化，也不手写 `.xsd` XML。如果 Computer Use 不可用，本地回退仍然支持进程检测、
激活、打开结构、BMP 快照和 GUI 操作日志。视角回放先生成 revision 级清单，并且只执行
`automation_ready=true` 的 recipe。晶向与有界整数 Miller 面法向严格共线时可复用临时平面
View Onto 路径，但必须记录映射和平面/晶向双重证据；其他晶向仍需 Computer Use 或本机
Copy Script 审核。MCP 不猜测 MaterialsScript 相机 API，也不把同指标 `[uvw]` 与 `(hkl)` 等同。
半导体诊断还会把具体掺杂位点记录与当前原子表逐项核对；若记录元素与实际元素不一致，
即使当前 revision 已热加载到 GUI，也会阻断“模型正常”和“可计算”的结论，直到元数据调和并重新审计。
晶体 execute/hot-load 还会重新解析生成的 CIF，逐项核对原子 ID、元素、分数坐标和六个晶格参数。
已有 CIF 与当前 `CrystalSpec` 不一致时，即使路径和窗口 revision 正确也会阻断正常性结论；重新物化需要显式确认且不会创建空 revision。
