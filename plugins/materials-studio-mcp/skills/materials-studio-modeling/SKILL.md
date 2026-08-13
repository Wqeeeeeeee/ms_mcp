---
name: materials-studio-modeling
description: Safely inspect, preview, create, modify, validate, hot-load, diagnose, revise, roll back, or prepare calculations for BIOVIA Materials Studio models through the bundled structured MCP tools. Use when a user asks about Materials Studio status or preflight, semiconductor or molecular modeling, current project/revision changes, GUI synchronization or diagnostics, or CASTEP, Forcite, or DMol3 previews or execution.
---

# Materials Studio Modeling

Use the structured server as a guarded orchestration layer. Preserve its preview,
revision, runner, GUI, calculation, and evidence gates; never bypass them with an
unstructured shortcut.

## Safe workflow

1. Establish scope. Identify the requested material, operation, project, and
   revision. If the requested material or scenario is unsupported, fail closed;
   never substitute a nearby material, structure, template, or calculation.
2. Start read-only. Call appropriate status tools such as
   `material_studio_get_status`, `material_studio_live_project_status`, and
   `material_studio_gui_status`, then call
   `material_studio_live_session_preflight` before modeling. Do not launch or
   modify Materials Studio just to satisfy preflight.
3. Prefer `material_studio_live_modeling_request` over lower-level tools for
   supported modeling, inspection, rollback, diagnostic, GUI, and calculation
   workflows. Preserve the tool's returned project/revision bindings and next
   action payloads exactly.
4. Default to `execution_mode=preview`. A preview must not be described as an
   executed revision, a runner result, a GUI change, or a calculation result.
5. Before any call that creates a revision, executes the runner, changes or
   controls the GUI, hot-loads a model, or starts CASTEP, Forcite, or DMol3,
   obtain explicit user confirmation for that exact action and payload. A broad
   request to inspect or preview is not execution confirmation.
6. Before GUI input, recheck that exactly the intended Materials Studio window
   is bound to the correct project/revision and satisfies the returned
   single-window, PID/HWND, foreground, visibility, and provenance gates. Never
   target a merely selected, similarly titled, stale, or unbound window.
7. After execution, validate the returned revision and artifacts. Report which
   claims are supported, which remain pending, and any exact next action.

## Same-window GUI loop

- For repeated live visualization, inspect `material_studio_gui_loop_status`
  for the exact project/revision and verified PID/HWND. If no healthy loop is
  bound, call `material_studio_gui_loop_prepare`, then have the user start the
  returned fixed Materials Studio User Menu script in that already-open
  window. Preparation alone does not run GUI code.
- Leave `MATERIAL_STUDIO_GUI_HOTLOAD_TRANSPORT=auto` unless the user explicitly
  requests `loop` or `dialog`. Auto uses the signed loop only while its fresh
  heartbeat, active document, current revision, window identity, and workspace
  binding all match; it may fall back to the verified File/Open transaction
  only before a loop job is enqueued.
- Once a job is enqueued, never fall back or retry an import automatically. A
  timeout or failure may already have changed the GUI; preserve its `job_id`,
  `side_effect_may_have_occurred`, and revision receipt, then poll only
  `material_studio_gui_loop_status` for that job.
- Do not call `material_studio_gui_loop_stop` as routine cleanup. It is an
  explicit same-session shutdown action, and a stopped PID/HWND/project binding
  must be reviewed before preparing another loop.

## Tool boundaries

- Do not call `material_studio_run_script` unless the user explicitly requests
  that exact escape hatch and the complete script has been manually reviewed.
  The plugin disables this tool and also makes it fail closed in plugin mode;
  never alter active Codex configuration to weaken that boundary. If it is
  unavailable, return the blocker instead of finding another arbitrary-script
  path.
- Do not invent MaterialsScript APIs, create an arbitrary GUI script queue, send
  blind GUI input, launch a second Materials Studio process, or bypass a
  revision/runner/GUI/calculation safety receipt.
- Treat a side-effecting failure as final for that attempt. Do not automatically
  retry revision creation, runner execution, GUI input, hot-load, rollback, or a
  calculation. Return the structured blocker and the server-provided next tool
  and payload for explicit review.

## Evidence vocabulary

Keep these conclusions separate:

- **Structure valid:** structural schema, geometry, chemistry, and artifact
  checks passed for the bound revision.
- **Model normal:** domain-specific model diagnostics passed; this is stronger
  than file validity but weaker than scientific verification.
- **Live GUI normal:** the expected revision is visible in the verified live
  window and the relevant GUI checks passed.
- **Calculation ready:** required structure, runner, inputs, and safety gates are
  ready; no calculation result is implied.
- **Scientifically verified:** methodology, convergence, provenance, and needed
  comparison or validation evidence support the scientific claim.

A GUI screenshot can support visual review only. It cannot replace structure
validation, prove model normality or calculation readiness, or establish
scientific verification. State uncertainty explicitly and keep unsupported
claims unresolved.

## Blocked outcomes

When an operation cannot proceed, report the failed gate, project/revision,
observed state, whether any side effect occurred, and the exact safe next step.
Never manufacture success, silently change scope, or replace an unsupported
request with the nearest available template.
