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
    $launcherPath = Join-Path $PluginRoot "scripts\Run-MS-MCP.ps1"
    $pluginManifest = Read-MSJson -Path $pluginManifestPath
    $mcpManifest = Read-MSJson -Path $mcpManifestPath
    if ([string]$pluginManifest.name -ne "materials-studio-mcp") { throw "Plugin name mismatch." }
    if ([string]$pluginManifest.version -ne $version) { throw "Plugin/package version mismatch." }
    if ([string]$pluginManifest.mcpServers -ne "./.mcp.json") { throw "Plugin mcpServers path must be ./.mcp.json." }
    if (-not (Test-Path -LiteralPath $launcherPath -PathType Leaf)) { throw "Plugin launcher implementation is missing." }
    $launcherText = Get-Content -LiteralPath $launcherPath -Raw -Encoding UTF8
    if ($launcherText -notmatch 'MATERIAL_STUDIO_WORKSPACE' -or $launcherText -notmatch 'MATERIAL_STUDIO_MCP_WORKSPACE') { throw "Plugin launcher must bind both workspace environment variables." }
    $server = $mcpManifest.'materials-studio'
    if ($null -eq $server -and $null -ne $mcpManifest.mcpServers) { $server = $mcpManifest.mcpServers.'materials-studio' }
    if ($null -eq $server) { throw ".mcp.json does not define materials-studio." }
    if ([string]$server.command -ne "cmd.exe") { throw ".mcp.json must use cmd.exe for the bundled Windows launcher." }
    if (-not (@($server.args) -contains "Run-MS-MCP.bat")) { throw ".mcp.json does not launch Run-MS-MCP.bat." }
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
    try { $toolPolicyText = (& $python -I -W ignore -c $toolPolicyBootstrap 2>&1 | Out-String).Trim(); $toolPolicyExit = $LASTEXITCODE }
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
    & $python -I -W ignore -c "import material_studio_mcp_server.server"
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
    try { $guardOutput = (& $python -I -W ignore -c $guardBootstrap 2>&1 | Out-String).Trim(); $guardExit = $LASTEXITCODE }
    finally { $ErrorActionPreference = $savedPreference }
    if ($guardExit -ne 0 -or $guardOutput -notmatch 'plugin_custom_script_disabled') { throw "material_studio_run_script plugin-mode guard failed: $guardOutput" }

    $launcherArgs = @($server.args) + @(
        "-LocalAppDataRoot", $paths.local_app_data_root,
        "-TestWorkspace", $isolatedWorkspace
    )
    $smokeArgs = @(
        "-X", "utf8", "-I", "-m", "material_studio_mcp_server.protocol_smoke",
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

    $stage = "launcher validate-only"
    & powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $launcherPath -LocalAppDataRoot $paths.local_app_data_root -ValidateOnly
    if ($LASTEXITCODE -ne 0) { throw "Plugin launcher validation failed." }

    $realStatus = "NOT_RUN"
    if ($RealMS) {
        if (-not $ConfirmRealMS) {
            if ($NonInteractive) { throw "--real-ms requires the separate -ConfirmRealMS acknowledgement." }
            $confirmation = Read-Host "Type REAL-MS-READ-ONLY to run a real read-only/preview Materials Studio preflight"
            if ($confirmation -cne "REAL-MS-READ-ONLY") { throw "Real Materials Studio preflight was not confirmed." }
        }
        $liveRelative = [string]$runtime.manifest.console_entrypoints.'ms-mcp-live-smoke'
        $liveSmoke = Join-Path $runtime.root $liveRelative
        if (-not (Test-Path -LiteralPath $liveSmoke -PathType Leaf)) { throw "ms-mcp-live-smoke entrypoint is missing." }
        $realOutput = Join-Path $paths.logs_root ("real-ms-read-only-{0}.json" -f [DateTime]::UtcNow.ToString("yyyyMMdd-HHmmss"))
        $env:MATERIAL_STUDIO_MCP_GUI_BACKEND = $oldGuiBackend
        $stage = "explicit real Materials Studio read-only preflight"
        & $liveSmoke --request "Check the local Materials Studio MCP and runner status without changing the model." --execution-mode preview --include-gui-status --working-dir $binding.workspace --output $realOutput
        if ($LASTEXITCODE -ne 0) { throw "Real Materials Studio read-only/preview preflight failed. See $realOutput" }
        $realStatus = "READ_ONLY_PREFLIGHT_ONLY"
    }

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
    if ($null -eq $oldPythonUtf8) { Remove-Item Env:PYTHONUTF8 -ErrorAction SilentlyContinue }
    else { $env:PYTHONUTF8 = $oldPythonUtf8 }
    if ($null -eq $oldPythonIoEncoding) { Remove-Item Env:PYTHONIOENCODING -ErrorAction SilentlyContinue }
    else { $env:PYTHONIOENCODING = $oldPythonIoEncoding }
    [Console]::OutputEncoding = $oldOutputEncoding
}
