# Structured Modeling Coding Plan

1. Preserve the current Perl `RunMatScript.bat` path and all existing tool names.
2. Add structured Pydantic specs for molecules, crystals, imported structures, Forcite, CASTEP, and semantic patches.
3. Store project state as non-destructive revisions under a workspace root controlled by `MATERIAL_STUDIO_MCP_WORKSPACE`.
4. Generate MaterialsScript Perl previews from specs and validate generated scripts before execution.
5. Add preview-first MCP tools for create, modify, validate, preview, history, rollback, and current state.
6. Enhance runner metadata with `created_files`, `duration_seconds`, and new tagged JSON markers.
7. Keep expensive jobs and custom scripts behind explicit user confirmation.
