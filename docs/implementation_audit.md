# Implementation Audit

## Current Layout

The main MCP entry point is `material_studio_mcp_server.server:main`, reached through `ms-mcp` and `run_server.py`. The package contains runner detection, subprocess execution, MaterialsScript Perl templates, and MCP tool registration. The `ms_mcp` package is retained as a legacy/local extension package and is not the default entry point.

## Existing Tools

The existing public tools are preserved: status, custom script validation/execution, import/export, structure summary, Forcite geometry optimization, molecule/TNT builders, CASTEP energy script generation, and template listing. Existing input models already use Pydantic with `extra="forbid"`.

## Runner

`MaterialStudioRunner` resolves `RunMatScript.bat` or related launchers from environment variables and common BIOVIA installation paths. It writes Perl scripts into isolated job directories and invokes the local runner through `subprocess.run`.

## Script Generation

`scripts.py` generates Perl MaterialsScript through deterministic string templates and `perl_string` escaping. Existing templates continue to use Materials Studio APIs rather than hand-written `.xsd` XML.

## Risks And Gaps

`material_studio_run_script` remains intentionally powerful and risky because it executes arbitrary user-supplied Perl. Structured tools now default to preview and validate generated scripts before execution. Crystal lattice construction remains conservative because local Copy Script output should be trusted over guessed API calls.

## Migration

New structured workflow modules live under `material_studio_mcp_server/specs`, `state`, `validators`, `translators`, and `parsers`. They are additive and do not remove existing public tools.
