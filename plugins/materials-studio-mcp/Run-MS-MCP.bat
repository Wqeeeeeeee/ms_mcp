@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "MS_MCP_LAUNCHER=%~dp0scripts\Run-MS-MCP.ps1"
if not exist "%MS_MCP_LAUNCHER%" (
  1>&2 echo [materials-studio-mcp] ERROR: Missing runtime launcher: "%MS_MCP_LAUNCHER%"
  1>&2 echo [materials-studio-mcp] Run Install-MS-MCP.bat from the Windows release bundle, then retry.
  exit /b 2
)

set "MS_MCP_EXTERNAL_CWD=%LOCALAPPDATA%\MaterialsStudioMCP"
if not exist "%MS_MCP_EXTERNAL_CWD%\" (
  1>&2 echo [materials-studio-mcp] ERROR: Missing managed install root: "%MS_MCP_EXTERNAL_CWD%"
  1>&2 echo [materials-studio-mcp] Run Install-MS-MCP.bat from the Windows release bundle, then retry.
  exit /b 2
)

rem Release the versioned Codex plugin cache as the process working directory.
rem Codex may atomically refresh that cache while this long-lived STDIO server runs.
cd /d "%MS_MCP_EXTERNAL_CWD%" || exit /b 2

powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%MS_MCP_LAUNCHER%" %*
exit /b %ERRORLEVEL%
