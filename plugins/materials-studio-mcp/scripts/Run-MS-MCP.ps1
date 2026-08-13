[CmdletBinding()]
param(
    [string]$LocalAppDataRoot,
    [string]$TestWorkspace,
    [switch]$ValidateOnly
)

$ErrorActionPreference = "Stop"

function Fail-Launcher {
    param([string]$Message)
    [Console]::Error.WriteLine("Materials Studio MCP launcher: $Message")
    exit 1
}

function Full-LauncherPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path) -or $Path.Contains("`r") -or $Path.Contains("`n") -or $Path.IndexOf([char]0) -ge 0) {
        throw "invalid path value"
    }
    $expanded = [Environment]::ExpandEnvironmentVariables($Path.Trim())
    if (-not [System.IO.Path]::IsPathRooted($expanded)) { throw "managed paths must be absolute" }
    return [System.IO.Path]::GetFullPath($expanded)
}

function Path-IsWithin {
    param([string]$Path, [string]$Root)
    $candidate = (Full-LauncherPath $Path).TrimEnd('\', '/')
    $boundary = (Full-LauncherPath $Root).TrimEnd('\', '/')
    return $candidate.StartsWith($boundary + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)
}

function Reject-ReparsePath {
    param([string]$Path)
    $resolved = Full-LauncherPath $Path
    $cursor = $resolved
    while (-not (Test-Path -LiteralPath $cursor)) {
        $parent = [System.IO.Path]::GetDirectoryName($cursor)
        if ([string]::IsNullOrEmpty($parent) -or $parent -eq $cursor) { break }
        $cursor = $parent
    }
    while (-not [string]::IsNullOrEmpty($cursor)) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "reparse point is not allowed: $cursor"
            }
        }
        $parent = [System.IO.Path]::GetDirectoryName($cursor)
        if ([string]::IsNullOrEmpty($parent) -or $parent -eq $cursor) { break }
        $cursor = $parent
    }
    return $resolved
}

function Read-LauncherJson {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "required file is missing: $Path" }
    try { return (Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json) }
    catch { throw "invalid JSON: $Path" }
}

function Launcher-FileHash {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "required file is missing: $Path" }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Launcher-TreeHash {
    param([string]$Root)
    $resolvedRoot = Reject-ReparsePath $Root
    if (-not (Test-Path -LiteralPath $resolvedRoot -PathType Container)) { throw "runtime directory is missing: $resolvedRoot" }
    $lines = New-Object System.Collections.Generic.List[string]
    foreach ($file in (Get-ChildItem -LiteralPath $resolvedRoot -File -Recurse -Force | Sort-Object FullName)) {
        if (($file.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { throw "runtime contains a reparse point" }
        $relative = $file.FullName.Substring($resolvedRoot.TrimEnd('\').Length).TrimStart('\').Replace('\', '/')
        if ($relative -eq "runtime-manifest.json") { continue }
        $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        $lines.Add("$relative`t$($file.Length)`t$hash")
    }
    foreach ($directory in (Get-ChildItem -LiteralPath $resolvedRoot -Directory -Recurse -Force)) {
        if (($directory.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { throw "runtime contains a reparse point" }
    }
    $bytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes((($lines -join "`n") + "`n"))
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant() }
    finally { $sha.Dispose() }
}

try {
    $base = $LocalAppDataRoot
    if ([string]::IsNullOrWhiteSpace($base)) { $base = $env:LOCALAPPDATA }
    if ([string]::IsNullOrWhiteSpace($base)) { throw "LOCALAPPDATA is not set; run Configure-MS-MCP.bat" }
    $productRoot = Reject-ReparsePath (Join-Path (Full-LauncherPath $base) "MaterialsStudioMCP")
    $settingsPath = Join-Path $productRoot "config\settings.json"
    $activePath = Join-Path $productRoot "config\active-runtime.json"
    $settings = Read-LauncherJson $settingsPath
    $active = Read-LauncherJson $activePath
    if ([string]$settings.schema -ne "materials_studio_mcp_windows_config_v1") { throw "configuration schema is stale; rerun Configure-MS-MCP.bat" }
    if ([string]$active.schema -ne "materials_studio_mcp_active_runtime_v1") { throw "active runtime pointer is stale; rerun Install-MS-MCP.bat" }
    $version = [string]$settings.package_version
    if ([string]::IsNullOrWhiteSpace($version) -or [string]$active.version -ne $version) { throw "configuration and active runtime versions differ; rerun Configure and Install" }
    $pluginRoot = Reject-ReparsePath (Join-Path $PSScriptRoot "..")
    $pluginManifestPath = Join-Path $pluginRoot ".codex-plugin\plugin.json"
    $pluginManifest = Read-LauncherJson $pluginManifestPath
    if ([string]$pluginManifest.name -ne "materials-studio-mcp") { throw "the cache-local plugin manifest has an unexpected name" }
    if ([string]$pluginManifest.version -ne $version) { throw "cache-local plugin version does not match the configured runtime; rerun Install-MS-MCP.bat" }

    $runtimesRoot = Join-Path $productRoot "runtimes"
    $runtimeRoot = Reject-ReparsePath ([string]$active.runtime_root)
    $expectedRuntimeRoot = Full-LauncherPath (Join-Path $runtimesRoot $version)
    if (-not (Path-IsWithin -Path $runtimeRoot -Root $runtimesRoot) -or -not $runtimeRoot.Equals($expectedRuntimeRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "active runtime path is outside the versioned managed runtime root"
    }
    $manifestPath = Full-LauncherPath ([string]$active.runtime_manifest_path)
    if (-not $manifestPath.Equals((Join-Path $runtimeRoot "runtime-manifest.json"), [System.StringComparison]::OrdinalIgnoreCase)) { throw "runtime manifest path binding mismatch" }
    $manifestHash = Launcher-FileHash $manifestPath
    if ($manifestHash -ne [string]$active.runtime_manifest_sha256) { throw "runtime manifest SHA-256 mismatch; reinstall this version" }
    $manifest = Read-LauncherJson $manifestPath
    if ([string]$manifest.schema -ne "materials_studio_mcp_windows_runtime_v1") { throw "runtime manifest schema mismatch" }
    if ([string]$manifest.version -ne $version -or -not (Full-LauncherPath ([string]$manifest.runtime_root)).Equals($runtimeRoot, [System.StringComparison]::OrdinalIgnoreCase)) { throw "runtime identity binding mismatch" }
    $treeHash = Launcher-TreeHash $runtimeRoot
    if ($treeHash -ne [string]$manifest.runtime_tree_sha256) { throw "runtime tree SHA-256 mismatch; reinstall this version" }

    if ([string]$manifest.python_relative_path -ne ".venv/Scripts/python.exe") { throw "runtime Python relative path is invalid" }
    $python = Full-LauncherPath (Join-Path $runtimeRoot ([string]$manifest.python_relative_path))
    if (-not (Path-IsWithin -Path $python -Root $runtimeRoot)) { throw "runtime Python path escaped the runtime root" }
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw "runtime Python executable is missing; reinstall this version" }
    Reject-ReparsePath $python | Out-Null
    if ([string]$manifest.package_relative_path -ne ".venv/Lib/site-packages/material_studio_mcp_server") { throw "runtime package relative path is invalid" }
    $packageRoot = Full-LauncherPath (Join-Path $runtimeRoot ([string]$manifest.package_relative_path))
    if (-not (Path-IsWithin -Path $packageRoot -Root $runtimeRoot) -or -not (Test-Path -LiteralPath $packageRoot -PathType Container)) { throw "runtime package path is missing or escaped" }
    Reject-ReparsePath $packageRoot | Out-Null
    $versionCode = "from importlib.metadata import version; print(version('materials-studio-mcp'))"
    $packageVersion = (& $python -I -c $versionCode 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $packageVersion -ne $version) { throw "installed package version does not match plugin version" }
    try { $declaredMcpVersion = [Version]([string]$manifest.dependency_versions.mcp) }
    catch { throw "runtime manifest MCP SDK version is missing or invalid; reinstall this version" }
    if ($declaredMcpVersion -lt [Version]"1.12.4" -or $declaredMcpVersion.Major -ge 2) { throw "runtime manifest MCP SDK version is outside the reviewed >=1.12.4,<2 range; reinstall this version" }
    $mcpCode = "from importlib.metadata import version; import mcp.server.fastmcp; print(version('mcp'))"
    $savedPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { $observedMcpText = (& $python -X utf8 -I -c $mcpCode 2>&1 | Out-String).Trim(); $observedMcpExit = $LASTEXITCODE }
    finally { $ErrorActionPreference = $savedPreference }
    if ($observedMcpExit -ne 0) { throw "installed MCP SDK cannot import mcp.server.fastmcp; reinstall this version" }
    try { $observedMcpVersion = [Version]$observedMcpText }
    catch { throw "installed MCP SDK version is invalid; reinstall this version" }
    if ($observedMcpVersion -lt [Version]"1.12.4" -or $observedMcpVersion.Major -ge 2) { throw "installed MCP SDK version is outside the reviewed >=1.12.4,<2 range; reinstall this version" }
    if ($observedMcpVersion -ne $declaredMcpVersion) { throw "runtime manifest/installed MCP SDK version mismatch; reinstall this version" }

    $runner = Reject-ReparsePath ([string]$settings.materials_studio.runner)
    if (-not (Test-Path -LiteralPath $runner -PathType Leaf) -or -not [System.IO.Path]::GetFileName($runner).Equals("RunMatScript.bat", [System.StringComparison]::OrdinalIgnoreCase)) { throw "configured RunMatScript.bat is unavailable; rerun Configure-MS-MCP.bat" }
    $workspace = Reject-ReparsePath ([string]$settings.workspace)
    if (-not (Test-Path -LiteralPath $workspace -PathType Container)) { throw "configured workspace is unavailable; rerun Configure-MS-MCP.bat" }
    foreach ($managedRoot in @((Join-Path $productRoot "config"), (Join-Path $productRoot "logs"), (Join-Path $productRoot "runtimes"), $pluginRoot)) {
        if ((Path-IsWithin -Path $workspace -Root $managedRoot) -or (Path-IsWithin -Path $managedRoot -Root $workspace) -or (Full-LauncherPath $workspace).Equals((Full-LauncherPath $managedRoot), [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "configured workspace overlaps managed runtime/config/log or plugin cache paths; rerun Configure-MS-MCP.bat"
        }
    }
    if (-not [string]::IsNullOrWhiteSpace($TestWorkspace)) {
        $testRoot = Join-Path $productRoot "test-workspaces"
        $workspace = Reject-ReparsePath $TestWorkspace
        if (-not (Path-IsWithin -Path $workspace -Root $testRoot) -or -not (Test-Path -LiteralPath $workspace -PathType Container)) {
            throw "test workspace must be an existing child of the managed test-workspaces directory"
        }
    }

    $env:MATERIAL_STUDIO_RUNNER = $runner
    $env:MATERIAL_STUDIO_WORKSPACE = $workspace
    $env:MATERIAL_STUDIO_MCP_WORKSPACE = $workspace
    if ([string]::IsNullOrWhiteSpace($env:MATERIAL_STUDIO_GUI_HOTLOAD_TRANSPORT)) { $env:MATERIAL_STUDIO_GUI_HOTLOAD_TRANSPORT = "auto" }
    if ([string]::IsNullOrWhiteSpace($env:MATERIAL_STUDIO_GUI_LOOP_TIMEOUT_SECONDS)) { $env:MATERIAL_STUDIO_GUI_LOOP_TIMEOUT_SECONDS = "45" }
    if ([string]::IsNullOrWhiteSpace($env:MATERIAL_STUDIO_GUI_LOOP_HEARTBEAT_TTL_SECONDS)) { $env:MATERIAL_STUDIO_GUI_LOOP_HEARTBEAT_TTL_SECONDS = "10" }
    $env:MATERIAL_STUDIO_MCP_PLUGIN_MODE = "1"
    $env:MATERIAL_STUDIO_MCP_LOG_DIR = (Join-Path $productRoot "logs")
    $env:PYTHONUNBUFFERED = "1"
    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    if ($ValidateOnly) { exit 0 }

    & $python -X utf8 -I -c "from material_studio_mcp_server.server import main; main()"
    exit $LASTEXITCODE
}
catch {
    Fail-Launcher "$($_.Exception.Message). Run Configure-MS-MCP.bat, Install-MS-MCP.bat, then Test-MS-MCP.bat."
}
