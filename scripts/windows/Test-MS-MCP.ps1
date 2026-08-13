[CmdletBinding()]
param(
    [string]$LocalAppDataRoot,
    [string]$PluginRoot,
    [string]$ReleaseRoot,
    [Alias("real-ms")][switch]$RealMS,
    [switch]$ConfirmRealMS,
    [switch]$NonInteractive
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "WindowsInstaller.Common.ps1")

$oldGuiBackend = $env:MATERIAL_STUDIO_MCP_GUI_BACKEND
$oldComtypesCache = $env:MATERIAL_STUDIO_MCP_COMTYPES_CACHE
$oldPythonUtf8 = $env:PYTHONUTF8
$oldPythonIoEncoding = $env:PYTHONIOENCODING
$oldOutputEncoding = [Console]::OutputEncoding
$stage = "initialization"
try {
    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw "Test-MS-MCP is supported only on Windows."
    }
    if ([string]::IsNullOrWhiteSpace($ReleaseRoot)) { $ReleaseRoot = Get-MSReleaseRoot }
    $ReleaseRoot = Resolve-MSFullPath -Path $ReleaseRoot
    $version = Get-MSPackageVersion -ReleaseRoot $ReleaseRoot
    $paths = Get-MSProductPaths -LocalAppDataRoot $LocalAppDataRoot
    $settings = Read-MSJson -Path $paths.settings_path
    $binding = Assert-MSSettings -Settings $settings -Paths $paths
    $active = Read-MSJson -Path $paths.active_runtime_path
    if ([string]$active.schema -ne $script:MSActiveRuntimeSchema) { throw "Active runtime pointer schema mismatch." }
    if ([string]$active.version -ne $version) { throw "Active runtime version does not match plugin version." }
    $runtime = Test-MSRuntime -RuntimeRoot ([string]$active.runtime_root) -Version $version -ExpectedManifestSha256 ([string]$active.runtime_manifest_sha256
    )
    $python = [string]$runtime.python

    if ([string]::IsNullOrWhiteSpace($PluginRoot)) {
        $PluginRoot = Join-Path $ReleaseRoot "plugins\materials-studio-mcp"
    }
    $PluginRoot = Assert-MSNoReparsePath -Path $PluginRoot
    if (-not (Test-Path -LiteralPath $PluginRoot -PathType Container)) { throw "Plugin root not found: $PluginRoot" }
    $pluginManifestPath = Join-Path $PluginRoot ".codex-plugin\plugin.json"
    $mcpManifestPath = Join-Path $PluginRoot ".mcp.json"
    $batchLauncherPath = Join-Path $PluginRoot "Run-MS-MCP.bat"
    $launcherPath = Join-Path $PluginRoot "scripts\Run-MS-MCP.ps1"
    $pluginManifest = Read-MSJson -Path $pluginManifestPath
    $mcpManifest = Read-MSJson -Path $mcpManifestPath
    if ([string]$pluginManifest.name -ne "materials-studio-mcp") { throw "Plugin name mismatch." }
    if ([string]$pluginManifest.version -ne $version) { throw "Plugin/package version mismatch." }
    if ([string]$pluginManifest.mcpServers -ne "./.mcp.json") { throw "Plugin mcpServers path must be ./.mcp.json." }
    if (-not (Test-Path -LiteralPath $launcherPath -PathType Leaf)) { throw "Plugin launcher implementation is missing." }
    if (-not (Test-Path -LiteralPath $batchLauncherPath -PathType Leaf)) { throw "Plugin batch launcher is missing." }
    $batchLauncherText = Get-Content -LiteralPath $batchLauncherPath -Raw -Encoding UTF8
    if ($batchLauncherText -notmatch '%LOCALAPPDATA%\\MaterialsStudioMCP') { throw "Plugin batch launcher must leave the versioned Codex cache before starting the server." }
    $externalCwdIndex = $batchLauncherText.IndexOf('cd /d "%MS_MCP_EXTERNAL_CWD%"', [StringComparison]::OrdinalIgnoreCase)
    $powershellIndex = $batchLauncherText.IndexOf('powershell.exe', [StringComparison]::OrdinalIgnoreCase)
    if ($externalCwdIndex -lt 0 -or $powershellIndex -lt 0 -or $externalCwdIndex -gt $powershellIndex) { throw "Plugin batch launcher changes working directory too late." }
    $launcherText = Get-Content -LiteralPath $launcherPath -Raw -Encoding UTF8
    if ($launcherText -notmatch 'MATERIAL_STUDIO_WORKSPACE' -or $launcherText -notmatch 'MATERIAL_STUDIO_MCP_WORKSPACE') { throw "Plugin launcher must bind both workspace environment variables." }
    foreach ($guiLoopVariable in @('MATERIAL_STUDIO_GUI_HOTLOAD_TRANSPORT', 'MATERIAL_STUDIO_GUI_LOOP_TIMEOUT_SECONDS', 'MATERIAL_STUDIO_GUI_LOOP_HEARTBEAT_TTL_SECONDS')) {
        if ($launcherText -notmatch $guiLoopVariable) { throw "Plugin launcher is missing GUI-loop environment default: $guiLoopVariable" }
    }
    $server = $mcpManifest.'materials-studio'
    if ($null -eq $server -and $null -ne $mcpManifest.mcpServers) { $server = $mcpManifest.mcpServers.'materials-studio' }
    if ($null -eq $server) { throw ".mcp.json does not define materials-studio." }
    if ([string]$server.command -ne "powershell.exe") { throw ".mcp.json must use direct PowerShell for the bundled Windows launcher." }
    $expectedServerArgs = @("-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", "scripts\Run-MS-MCP.ps1")
    if (@($server.args).Count -ne $expectedServerArgs.Count) { throw ".mcp.json PowerShell launcher arguments drifted." }
    for ($index = 0; $index -lt $expectedServerArgs.Count; $index++) {
        if ([string]$server.args[$index] -cne $expectedServerArgs[$index]) { throw ".mcp.json PowerShell launcher arguments drifted." }
    }
    if ([string]$server.cwd -ne ".") { throw ".mcp.json cwd must be cache-relative '.'." }
    if ([string]$server.env.MATERIAL_STUDIO_MCP_PLUGIN_MODE -ne "1") { throw ".mcp.json must enable fail-closed plugin mode." }
    if ([string]$server.default_tools_approval_mode -ne "prompt") { throw ".mcp.json must require prompt approval by default for every bundled MCP tool." }
    if (-not (@($server.disabled_tools) -contains "material_studio_run_script")) { throw ".mcp.json must disable material_studio_run_script by default." }
    $toolPolicyCode = @'
import json
from material_studio_mcp_server.codex_config import DISABLED_TOOLS, SAFE_ENABLED_TOOLS
print(json.dumps({"enabled_tools": SAFE_ENABLED_TOOLS, "disabled_tools": DISABLED_TOOLS}))
'@
    $toolPolicyBase64 = [Convert]::ToBase64String((New-Object System.Text.UTF8Encoding($false)).GetBytes($toolPolicyCode))
    $toolPolicyBootstrap = "import base64;exec(base64.b64decode('$toolPolicyBase64'))"
    $savedPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { $toolPolicyText = (& $python -B -I -W ignore -c $toolPolicyBootstrap 2>&1 | Out-String).Trim(); $toolPolicyExit = $LASTEXITCODE }
    finally { $ErrorActionPreference = $savedPreference }
    if ($toolPolicyExit -ne 0) { throw "Could not load installed Codex tool policy: $toolPolicyText" }
    $toolPolicy = $toolPolicyText | ConvertFrom-Json
    $manifestEnabled = @($server.enabled_tools)
    $expectedEnabled = @($toolPolicy.enabled_tools)
    $manifestDisabled = @($server.disabled_tools)
    $expectedDisabled = @($toolPolicy.disabled_tools)
    if ($manifestEnabled.Count -ne $expectedEnabled.Count -or $manifestDisabled.Count -ne $expectedDisabled.Count) {
        throw ".mcp.json tool allowlist/denylist count drifted from the installed Codex policy."
    }
    for ($index = 0; $index -lt $expectedEnabled.Count; $index++) {
        if ([string]$manifestEnabled[$index] -ne [string]$expectedEnabled[$index]) { throw ".mcp.json enabled_tools drifted from SAFE_ENABLED_TOOLS." }
    }
    for ($index = 0; $index -lt $expectedDisabled.Count; $index++) {
        if ([string]$manifestDisabled[$index] -ne [string]$expectedDisabled[$index]) { throw ".mcp.json disabled_tools drifted from DISABLED_TOOLS." }
    }

    $marketplacePath = Join-Path $ReleaseRoot ".agents\plugins\marketplace.json"
    $marketplace = Read-MSJson -Path $marketplacePath
    $marketplaceText = Get-Content -LiteralPath $marketplacePath -Raw -Encoding UTF8
    if ($marketplaceText -notmatch [regex]::Escape("./plugins/materials-studio-mcp")) { throw "Local marketplace does not reference ./plugins/materials-studio-mcp." }

    $testRoot = Join-Path $paths.product_root "test-workspaces"
    New-Item -ItemType Directory -Path $testRoot -Force | Out-Null
    Assert-MSNoReparsePath -Path $testRoot | Out-Null
    $isolatedWorkspace = Join-Path $testRoot ([Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $isolatedWorkspace | Out-Null
    $smokeOutputPath = Join-Path $isolatedWorkspace "protocol-smoke.json"

    $stage = "installed package import"
    & $python -B -I -W ignore -c "import material_studio_mcp_server.server"
    if ($LASTEXITCODE -ne 0) { throw "Installed package import failed." }
    $packageRoot = [string]$runtime.package_root
    if (-not (Test-Path -LiteralPath $packageRoot -PathType Container)) { throw "Could not locate the installed package." }
    $stage = "compileall"
    $compileCache = Join-Path $isolatedWorkspace "compile-pycache"
    try {
        & $python -X "pycache_prefix=$compileCache" -I -m compileall -f -q --invalidation-mode checked-hash $packageRoot
        if ($LASTEXITCODE -ne 0) { throw "compileall failed for the installed package." }
    }
    finally {
        if (Test-Path -LiteralPath $compileCache) { Remove-Item -LiteralPath $compileCache -Recurse -Force }
    }

    $guardCode = @'
import json
import os
os.environ["MATERIAL_STUDIO_MCP_PLUGIN_MODE"] = "1"
import material_studio_mcp_server.server as server
server.runtime_provenance_status = lambda: {"source_current": True, "restart_required": False}
result = server.material_studio_run_script(script="print 'must not run';")
assert result.get("status") == "plugin_custom_script_disabled", result
assert result.get("execution_started") is False, result
assert result.get("runner_invoked") is False, result
print(json.dumps({"ok": True, "status": result["status"]}))
'@
    $guardBytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes($guardCode)
    $guardBase64 = [Convert]::ToBase64String($guardBytes)
    $guardBootstrap = "import base64;exec(base64.b64decode('$guardBase64'))"
    $stage = "plugin-mode custom-script guard"
    $savedPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { $guardOutput = (& $python -B -I -W ignore -c $guardBootstrap 2>&1 | Out-String).Trim(); $guardExit = $LASTEXITCODE }
    finally { $ErrorActionPreference = $savedPreference }
    if ($guardExit -ne 0 -or $guardOutput -notmatch 'plugin_custom_script_disabled') { throw "material_studio_run_script plugin-mode guard failed: $guardOutput" }

    $launcherArgs = @($server.args) + @(
        "-LocalAppDataRoot", $paths.local_app_data_root,
        "-TestWorkspace", $isolatedWorkspace
    )
    $smokeArgs = @(
        "-B", "-X", "utf8", "-I", "-m", "material_studio_mcp_server.protocol_smoke",
        "--command", ([string]$server.command)
    )
    foreach ($argument in $launcherArgs) { $smokeArgs += "--server-arg=$argument" }
    $smokeArgs += @(
        "--cwd", $PluginRoot,
        "--workspace", $isolatedWorkspace,
        "--list-only",
        "--timeout-seconds", "90",
        "--output", $smokeOutputPath
    )

    $codexConfigPath = if (-not [string]::IsNullOrWhiteSpace($env:USERPROFILE)) { Join-Path $env:USERPROFILE ".codex\config.toml" } else { $null }
    $codexConfigHashBefore = if ($null -ne $codexConfigPath -and (Test-Path -LiteralPath $codexConfigPath -PathType Leaf)) { Get-MSFileSha256 -Path $codexConfigPath } else { $null }
    $env:MATERIAL_STUDIO_MCP_GUI_BACKEND = "null"
    $stage = "cache BAT MCP stdio protocol smoke"
    $savedPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { $smokeConsole = (& $python @smokeArgs 2>&1 | Out-String).Trim(); $smokeExit = $LASTEXITCODE }
    finally { $ErrorActionPreference = $savedPreference }
    if ($smokeExit -ne 0) { throw "MCP stdio protocol smoke failed: $smokeConsole" }
    $smoke = Read-MSJson -Path $smokeOutputPath
    if ($smoke.ok -ne $true -or $smoke.transport -ne "stdio") { throw "Protocol smoke did not report a successful stdio session." }
    if ($smoke.discovery.ok -ne $true -or @($smoke.discovery.schema_errors).Count -ne 0 -or @($smoke.discovery.annotation_errors).Count -ne 0) {
        throw "Tool discovery, schema, or annotation validation failed."
    }
    if ([int]$smoke.tool_count -le 0) { throw "Protocol smoke discovered no tools." }
    $codexConfigHashAfter = if ($null -ne $codexConfigPath -and (Test-Path -LiteralPath $codexConfigPath -PathType Leaf)) { Get-MSFileSha256 -Path $codexConfigPath } else { $null }
    if ($codexConfigHashBefore -ne $codexConfigHashAfter) { throw "Test modified the active Codex configuration." }

    $runtimeTreeBeforeGuiStatus = Get-MSTreeSha256 -Root ([string]$runtime.root)
    if ($runtimeTreeBeforeGuiStatus -ne [string]$runtime.manifest.runtime_tree_sha256) {
        throw "Runtime tree drifted before the GUI status immutable-runtime smoke."
    }
    $guiStatusProbeConfig = [ordered]@{
        command = [string]$server.command
        args = @($launcherArgs)
        cwd = $PluginRoot
        workspace = $isolatedWorkspace
    } | ConvertTo-Json -Depth 8 -Compress
    $guiStatusProbeConfigBase64 = [Convert]::ToBase64String(
        (New-Object System.Text.UTF8Encoding($false)).GetBytes($guiStatusProbeConfig)
    )
    $guiStatusProbeCode = @'
import asyncio
import base64
import json
from datetime import timedelta

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

config = json.loads(base64.b64decode("__CONFIG_BASE64__"))

async def main():
    server = StdioServerParameters(
        command=config["command"],
        args=config["args"],
        cwd=config["cwd"],
        encoding="utf-8",
        encoding_error_handler="replace",
    )
    timeout = timedelta(seconds=90)
    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(
            read_stream,
            write_stream,
            read_timeout_seconds=timeout,
        ) as session:
            await session.initialize()
            result = await session.call_tool(
                "material_studio_gui_status",
                arguments={"working_dir": config["workspace"]},
                read_timeout_seconds=timeout,
            )
            if result.isError:
                raise RuntimeError("material_studio_gui_status returned an MCP error")
            structured = result.structuredContent
            payload = None
            if isinstance(structured, dict):
                payload = structured.get("result") if set(structured) == {"result"} else structured
            if not isinstance(payload, dict):
                for item in result.content:
                    text = getattr(item, "text", None)
                    if not isinstance(text, str):
                        continue
                    try:
                        candidate = json.loads(text)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(candidate, dict):
                        payload = candidate
                        break
            if not isinstance(payload, dict):
                raise RuntimeError("material_studio_gui_status returned no JSON object payload")
            if payload.get("ok") is not True:
                raise RuntimeError(
                    "material_studio_gui_status payload was not successful: "
                    + json.dumps(payload, ensure_ascii=False, sort_keys=True)
                )
    print(json.dumps(
        {"gui_status_payload_ok": True, "tool": "material_studio_gui_status"},
        separators=(",", ":"),
    ))

asyncio.run(main())
'@.Replace("__CONFIG_BASE64__", $guiStatusProbeConfigBase64)
    $guiStatusProbeBase64 = [Convert]::ToBase64String(
        (New-Object System.Text.UTF8Encoding($false)).GetBytes($guiStatusProbeCode)
    )
    $guiStatusProbeBootstrap = "import base64;exec(base64.b64decode('$guiStatusProbeBase64'))"
    $stage = "GUI status immutable-runtime smoke"
    $savedPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    Remove-Item Env:MATERIAL_STUDIO_MCP_GUI_BACKEND -ErrorAction SilentlyContinue
    try { $guiStatusProbeOutput = (& $python -B -I -W ignore -c $guiStatusProbeBootstrap 2>&1 | Out-String).Trim(); $guiStatusProbeExit = $LASTEXITCODE }
    finally {
        $env:MATERIAL_STUDIO_MCP_GUI_BACKEND = "null"
        $ErrorActionPreference = $savedPreference
    }
    if ($guiStatusProbeExit -ne 0 -or $guiStatusProbeOutput -notmatch '"gui_status_payload_ok":true') {
        throw "GUI status immutable-runtime smoke failed: $guiStatusProbeOutput"
    }
    $runtimeTreeAfterGuiStatus = Get-MSTreeSha256 -Root ([string]$runtime.root)
    if ($runtimeTreeAfterGuiStatus -ne $runtimeTreeBeforeGuiStatus -or
        $runtimeTreeAfterGuiStatus -ne [string]$runtime.manifest.runtime_tree_sha256) {
        throw "GUI status changed the immutable runtime tree."
    }

    $realStatus = "NOT_RUN"
    if ($RealMS) {
        if (-not $ConfirmRealMS) {
            if ($NonInteractive) { throw "--real-ms requires the separate -ConfirmRealMS acknowledgement." }
            $confirmation = Read-Host "Type REAL-MS-READ-ONLY to run a real read-only/preview Materials Studio preflight"
            if ($confirmation -cne "REAL-MS-READ-ONLY") { throw "Real Materials Studio preflight was not confirmed." }
        }
        $realOutput = Join-Path $paths.logs_root ("real-ms-read-only-{0}.json" -f [DateTime]::UtcNow.ToString("yyyyMMdd-HHmmss"))
        $comtypesCacheRoot = Join-Path $paths.logs_root "comtypes-cache"
        $realMsComtypesCache = New-MSComtypesCache -Root $comtypesCacheRoot
        Remove-Item Env:MATERIAL_STUDIO_MCP_GUI_BACKEND -ErrorAction SilentlyContinue
        $env:MATERIAL_STUDIO_MCP_COMTYPES_CACHE = $realMsComtypesCache
        $stage = "explicit real Materials Studio read-only preflight"
        try {
            & $python -B -X utf8 -I -m material_studio_mcp_server.live_smoke --request "Check the local Materials Studio MCP and runner status without changing the model." --execution-mode preview --include-gui-status --working-dir $binding.workspace --output $realOutput
            $realMsExit = $LASTEXITCODE
        }
        finally {
            $env:MATERIAL_STUDIO_MCP_GUI_BACKEND = "null"
            if ($null -eq $oldComtypesCache) { Remove-Item Env:MATERIAL_STUDIO_MCP_COMTYPES_CACHE -ErrorAction SilentlyContinue }
            else { $env:MATERIAL_STUDIO_MCP_COMTYPES_CACHE = $oldComtypesCache }
            Remove-MSComtypesCache -Path $realMsComtypesCache -Root $comtypesCacheRoot
        }
        if ($realMsExit -ne 0) { throw "Real Materials Studio read-only/preview preflight failed. See $realOutput" }
        $realStatus = "READ_ONLY_PREFLIGHT_ONLY"
    }

    $stage = "final launcher validate-only"
    $runtimeTreeAfterAllProbes = Get-MSTreeSha256 -Root ([string]$runtime.root)
    if ($runtimeTreeAfterAllProbes -ne $runtimeTreeBeforeGuiStatus -or
        $runtimeTreeAfterAllProbes -ne [string]$runtime.manifest.runtime_tree_sha256) {
        throw "A GUI or real-MS status probe changed the immutable runtime tree."
    }
    & powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $launcherPath -LocalAppDataRoot $paths.local_app_data_root -ValidateOnly
    if ($LASTEXITCODE -ne 0) { throw "Plugin launcher validation failed." }
    Write-Host "PASS: GUI status preserved the immutable runtime tree and launcher validation"

    Write-Host "PASS: configuration, runner, package import, compileall, runtime integrity, and manifests"
    Write-Host "PASS: plugin cache-compatible launcher completed stdio discovery with $($smoke.tool_count) tools"
    Write-Host "PASS: tool schemas and annotations; material_studio_run_script is disabled in plugin mode"
    Write-Host "PASS: isolated workspace used; no GUI input, calculation, or active Codex config change"
    Write-Host "Real Materials Studio: $realStatus"
    Write-Host "Real CASTEP: NOT_RUN"
    exit 0
}
catch {
    [Console]::Error.WriteLine("Test-MS-MCP failed during $stage`: $($_.Exception.Message)")
    exit 1
}
finally {
    if ($null -eq $oldGuiBackend) { Remove-Item Env:MATERIAL_STUDIO_MCP_GUI_BACKEND -ErrorAction SilentlyContinue }
    else { $env:MATERIAL_STUDIO_MCP_GUI_BACKEND = $oldGuiBackend }
    if ($null -eq $oldComtypesCache) { Remove-Item Env:MATERIAL_STUDIO_MCP_COMTYPES_CACHE -ErrorAction SilentlyContinue }
    else { $env:MATERIAL_STUDIO_MCP_COMTYPES_CACHE = $oldComtypesCache }
    if ($null -eq $oldPythonUtf8) { Remove-Item Env:PYTHONUTF8 -ErrorAction SilentlyContinue }
    else { $env:PYTHONUTF8 = $oldPythonUtf8 }
    if ($null -eq $oldPythonIoEncoding) { Remove-Item Env:PYTHONIOENCODING -ErrorAction SilentlyContinue }
    else { $env:PYTHONIOENCODING = $oldPythonIoEncoding }
    [Console]::OutputEncoding = $oldOutputEncoding
}
