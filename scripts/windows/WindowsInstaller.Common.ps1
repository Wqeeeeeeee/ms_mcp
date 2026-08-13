$script:MSConfigSchema = "materials_studio_mcp_windows_config_v1"
$script:MSRuntimeSchema = "materials_studio_mcp_windows_runtime_v1"
$script:MSActiveRuntimeSchema = "materials_studio_mcp_active_runtime_v1"
$script:MSInstallManifestSchema = "materials_studio_mcp_windows_install_manifest_v1"

function Get-MSReleaseRoot {
    return [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
}

function Resolve-MSFullPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [string]$BasePath = (Get-Location).Path
    )

    if ([string]::IsNullOrWhiteSpace($Path)) {
        throw "Path must not be empty."
    }
    if ($Path.IndexOf([char]0) -ge 0 -or $Path.Contains("`r") -or $Path.Contains("`n")) {
        throw "Path contains a forbidden control character."
    }
    $expanded = [Environment]::ExpandEnvironmentVariables($Path.Trim())
    if (-not [System.IO.Path]::IsPathRooted($expanded)) {
        $expanded = Join-Path $BasePath $expanded
    }
    return [System.IO.Path]::GetFullPath($expanded)
}

function Get-MSProductPaths {
    param([string]$LocalAppDataRoot)

    $base = $LocalAppDataRoot
    if ([string]::IsNullOrWhiteSpace($base)) {
        $base = $env:LOCALAPPDATA
    }
    if ([string]::IsNullOrWhiteSpace($base)) {
        throw "LOCALAPPDATA is not set. Pass -LocalAppDataRoot explicitly."
    }
    $resolvedBase = Resolve-MSFullPath -Path $base
    $productRoot = [System.IO.Path]::GetFullPath((Join-Path $resolvedBase "MaterialsStudioMCP"))
    return [ordered]@{
        local_app_data_root = $resolvedBase
        product_root = $productRoot
        config_root = (Join-Path $productRoot "config")
        settings_path = (Join-Path $productRoot "config\settings.json")
        active_runtime_path = (Join-Path $productRoot "config\active-runtime.json")
        install_manifest_path = (Join-Path $productRoot "config\install-manifest.json")
        logs_root = (Join-Path $productRoot "logs")
        workspace_root = (Join-Path $productRoot "workspace")
        runtimes_root = (Join-Path $productRoot "runtimes")
    }
}

function Test-MSPathWithin {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Root,
        [switch]$AllowRoot
    )

    $candidate = (Resolve-MSFullPath -Path $Path).TrimEnd('\', '/')
    $boundary = (Resolve-MSFullPath -Path $Root).TrimEnd('\', '/')
    if ($AllowRoot -and $candidate.Equals($boundary, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $true
    }
    return $candidate.StartsWith(
        $boundary + [System.IO.Path]::DirectorySeparatorChar,
        [System.StringComparison]::OrdinalIgnoreCase
    )
}

function Test-MSPathsOverlap {
    param(
        [Parameter(Mandatory = $true)][string]$Left,
        [Parameter(Mandatory = $true)][string]$Right
    )
    return (Test-MSPathWithin -Path $Left -Root $Right -AllowRoot) -or
        (Test-MSPathWithin -Path $Right -Root $Left -AllowRoot)
}

function Assert-MSNoReparsePath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [switch]$IncludeLeafIfMissing
    )

    $resolved = Resolve-MSFullPath -Path $Path
    $cursor = $resolved
    while (-not (Test-Path -LiteralPath $cursor)) {
        $parent = [System.IO.Path]::GetDirectoryName($cursor)
        if ([string]::IsNullOrEmpty($parent) -or $parent -eq $cursor) {
            break
        }
        $cursor = $parent
    }
    while (-not [string]::IsNullOrEmpty($cursor)) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Reparse points are not allowed in managed paths: $cursor"
            }
        }
        $parent = [System.IO.Path]::GetDirectoryName($cursor)
        if ([string]::IsNullOrEmpty($parent) -or $parent -eq $cursor) {
            break
        }
        $cursor = $parent
    }
    return $resolved
}

function Get-MSFileSha256 {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "File not found: $Path"
    }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-MSTreeSha256 {
    param([Parameter(Mandatory = $true)][string]$Root)

    $resolvedRoot = Assert-MSNoReparsePath -Path $Root
    if (-not (Test-Path -LiteralPath $resolvedRoot -PathType Container)) {
        throw "Runtime root not found: $resolvedRoot"
    }
    $lines = New-Object System.Collections.Generic.List[string]
    $files = Get-ChildItem -LiteralPath $resolvedRoot -File -Recurse -Force | Sort-Object FullName
    foreach ($file in $files) {
        $relative = $file.FullName.Substring($resolvedRoot.TrimEnd('\').Length).TrimStart('\').Replace('\', '/')
        if ($relative -eq "runtime-manifest.json") {
            continue
        }
        if (($file.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Runtime contains a reparse point: $relative"
        }
        $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        $lines.Add("$relative`t$($file.Length)`t$hash")
    }
    $directories = Get-ChildItem -LiteralPath $resolvedRoot -Directory -Recurse -Force
    foreach ($directory in $directories) {
        if (($directory.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            $relative = $directory.FullName.Substring($resolvedRoot.TrimEnd('\').Length).TrimStart('\')
            throw "Runtime contains a reparse point: $relative"
        }
    }
    $text = ($lines -join "`n") + "`n"
    $bytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes($text)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
    }
    finally {
        $sha.Dispose()
    }
}

function New-MSComtypesCache {
    param([Parameter(Mandatory = $true)][string]$Root)

    $resolvedRoot = Resolve-MSFullPath -Path $Root
    New-Item -ItemType Directory -Path $resolvedRoot -Force | Out-Null
    Assert-MSNoReparsePath -Path $resolvedRoot | Out-Null
    for ($attempt = 0; $attempt -lt 8; $attempt++) {
        $leaf = "run-" + [Guid]::NewGuid().ToString("N")
        $candidate = Resolve-MSFullPath -Path (Join-Path $resolvedRoot $leaf)
        if (-not [System.IO.Path]::GetDirectoryName($candidate).Equals($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Comtypes cache allocation escaped its managed root."
        }
        try {
            New-Item -ItemType Directory -Path $candidate -ErrorAction Stop | Out-Null
            return $candidate
        }
        catch {
            if (Test-Path -LiteralPath $candidate) { continue }
            throw
        }
    }
    throw "Could not allocate a unique comtypes cache directory."
}

function Remove-MSComtypesCache {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Root
    )

    $resolved = Resolve-MSFullPath -Path $Path
    $resolvedRoot = Resolve-MSFullPath -Path $Root
    $leaf = [System.IO.Path]::GetFileName($resolved)
    if (-not [System.IO.Path]::GetDirectoryName($resolved).Equals($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
        $leaf -notmatch '^run-[0-9a-f]{32}$') {
        throw "Refusing to remove a comtypes cache outside the exact managed run directory."
    }
    if (-not (Test-Path -LiteralPath $resolved)) { return }
    Assert-MSNoReparsePath -Path $resolved | Out-Null
    foreach ($item in (Get-ChildItem -LiteralPath $resolved -Force)) {
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing to remove a comtypes cache containing a reparse point."
        }
        if ($item.PSIsContainer) {
            throw "Refusing to remove a comtypes cache containing a child directory."
        }
        if (-not [System.IO.Path]::GetDirectoryName($item.FullName).Equals($resolved, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove a comtypes cache file outside its exact run directory."
        }
        Remove-Item -LiteralPath $item.FullName -Force
    }
    Remove-Item -LiteralPath $resolved -Force
}

function Read-MSJson {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required JSON file not found: $Path"
    }
    try {
        return (Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json)
    }
    catch {
        throw "Invalid JSON in $Path`: $($_.Exception.Message)"
    }
}

function Write-MSJsonAtomic {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Value
    )

    $resolved = Resolve-MSFullPath -Path $Path
    $parent = [System.IO.Path]::GetDirectoryName($resolved)
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    Assert-MSNoReparsePath -Path $parent | Out-Null
    $temporary = Join-Path $parent (".{0}.{1}.tmp" -f [System.IO.Path]::GetFileName($resolved), [Guid]::NewGuid().ToString("N"))
    $encoded = ($Value | ConvertTo-Json -Depth 16) + "`n"
    [System.IO.File]::WriteAllText($temporary, $encoded, (New-Object System.Text.UTF8Encoding($false)))
    try {
        Move-Item -LiteralPath $temporary -Destination $resolved -Force
    }
    finally {
        if (Test-Path -LiteralPath $temporary) {
            Remove-Item -LiteralPath $temporary -Force
        }
    }
}

function Resolve-MSPython {
    param([string]$PythonCommand)

    $candidate = $PythonCommand
    $wasExplicit = -not [string]::IsNullOrWhiteSpace($candidate)
    if (-not $wasExplicit) { $candidate = "python" }
    if ($candidate.Contains("`r") -or $candidate.Contains("`n") -or $candidate.IndexOf([char]0) -ge 0) {
        throw "Python command contains a forbidden control character."
    }
    if ($candidate.Trim().Equals("py -3", [System.StringComparison]::OrdinalIgnoreCase) -or
        $candidate.Trim().Equals("py", [System.StringComparison]::OrdinalIgnoreCase)) {
        $launcher = Get-Command -Name "py.exe" -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($null -eq $launcher) { throw "The Windows py launcher was not found." }
        $launcherPath = Resolve-MSFullPath -Path $launcher.Source
        Assert-MSNoReparsePath -Path $launcherPath | Out-Null
        $resolvedOutput = (& $launcherPath -3 -I -c 'import sys; print(sys.executable)' 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($resolvedOutput)) { throw "The Windows py launcher could not resolve Python 3." }
        $resolved = Resolve-MSFullPath -Path $resolvedOutput
        if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) { throw "The Python selected by py -3 was not found: $resolved" }
        Assert-MSNoReparsePath -Path $resolved | Out-Null
        return $resolved
    }
    if ([System.IO.Path]::IsPathRooted($candidate) -or $candidate.Contains('\') -or $candidate.Contains('/')) {
        $resolved = Resolve-MSFullPath -Path $candidate
        if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
            throw "Python executable not found: $resolved"
        }
        Assert-MSNoReparsePath -Path $resolved | Out-Null
        return $resolved
    }
    if ($candidate.Trim() -match '\s') {
        throw "PythonCommand must be an executable path, python, py, or the exact value 'py -3'."
    }
    $command = Get-Command -Name $candidate -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $command) {
        if (-not $wasExplicit) { return (Resolve-MSPython -PythonCommand "py -3") }
        throw "Python 3.10 or newer was not found. Install Python and rerun Configure-MS-MCP.bat."
    }
    $resolvedCommand = Resolve-MSFullPath -Path $command.Source
    if (-not $wasExplicit) {
        try {
            Assert-MSNoReparsePath -Path $resolvedCommand | Out-Null
            Get-MSPythonProbe -PythonExecutable $resolvedCommand | Out-Null
        }
        catch { return (Resolve-MSPython -PythonCommand "py -3") }
    }
    else {
        Assert-MSNoReparsePath -Path $resolvedCommand | Out-Null
    }
    return $resolvedCommand
}

function Get-MSPythonProbe {
    param([Parameter(Mandatory = $true)][string]$PythonExecutable)

    $code = 'import json,sys; print(json.dumps(dict(version=list(sys.version_info[:3]), executable=sys.executable)))'
    $output = & $PythonExecutable -I -c $code 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Python probe failed: $($output -join ' ')"
    }
    try {
        $probe = (($output -join "`n") | ConvertFrom-Json)
    }
    catch {
        throw "Python probe returned invalid output."
    }
    if ($probe.version.Count -lt 2 -or [int]$probe.version[0] -lt 3 -or ([int]$probe.version[0] -eq 3 -and [int]$probe.version[1] -lt 10)) {
        throw "Python 3.10 or newer is required; detected $($probe.version -join '.')."
    }
    return $probe
}

function Get-MSPackageVersion {
    param([string]$ReleaseRoot = (Get-MSReleaseRoot))

    $pluginManifest = Join-Path $ReleaseRoot "plugins\materials-studio-mcp\.codex-plugin\plugin.json"
    if (Test-Path -LiteralPath $pluginManifest -PathType Leaf) {
        $manifest = Read-MSJson -Path $pluginManifest
        if (-not [string]::IsNullOrWhiteSpace([string]$manifest.version)) {
            return [string]$manifest.version
        }
    }
    $pyproject = Join-Path $ReleaseRoot "pyproject.toml"
    if (Test-Path -LiteralPath $pyproject -PathType Leaf) {
        $match = Select-String -LiteralPath $pyproject -Pattern '^version\s*=\s*"([^"]+)"\s*$' | Select-Object -First 1
        if ($null -ne $match) {
            return $match.Matches[0].Groups[1].Value
        }
    }
    $releaseManifest = Join-Path $ReleaseRoot "release-manifest.json"
    if (Test-Path -LiteralPath $releaseManifest -PathType Leaf) {
        $manifest = Read-MSJson -Path $releaseManifest
        foreach ($name in @("version", "package_version")) {
            if (-not [string]::IsNullOrWhiteSpace([string]$manifest.$name)) {
                return [string]$manifest.$name
            }
        }
    }
    throw "Unable to determine the Materials Studio MCP package version."
}

function Get-MSRunnerCandidates {
    param([string]$ExplicitRunner)

    $candidates = New-Object System.Collections.Generic.List[string]
    foreach ($value in @($ExplicitRunner, $env:MATERIAL_STUDIO_RUNNER, $env:MS_RUNNER, $env:BIOVIA_MATERIALS_STUDIO_RUNNER)) {
        if (-not [string]::IsNullOrWhiteSpace($value)) {
            $candidates.Add($value)
        }
    }
    $installRoots = New-Object System.Collections.Generic.List[string]
    if (-not [string]::IsNullOrWhiteSpace($env:ProgramFiles)) {
        foreach ($vendor in @("BIOVIA", "Dassault Systemes", "Accelrys")) {
            $installRoots.Add((Join-Path $env:ProgramFiles $vendor))
        }
    }
    if (-not [string]::IsNullOrWhiteSpace(${env:ProgramFiles(x86)})) {
        foreach ($vendor in @("BIOVIA", "Dassault Systemes", "Accelrys")) {
            $installRoots.Add((Join-Path ${env:ProgramFiles(x86)} $vendor))
        }
    }
    foreach ($root in $installRoots) {
        if ([string]::IsNullOrWhiteSpace($root) -or -not (Test-Path -LiteralPath $root -PathType Container)) {
            continue
        }
        Get-ChildItem -LiteralPath $root -Filter "RunMatScript.bat" -File -Recurse -ErrorAction SilentlyContinue |
            ForEach-Object { $candidates.Add($_.FullName) }
    }
    return @($candidates | Select-Object -Unique)
}

function Resolve-MSRunner {
    param([string]$ExplicitRunner)

    foreach ($candidate in (Get-MSRunnerCandidates -ExplicitRunner $ExplicitRunner)) {
        try {
            $resolved = Resolve-MSFullPath -Path $candidate
            if ((Test-Path -LiteralPath $resolved -PathType Leaf) -and [System.IO.Path]::GetFileName($resolved).Equals("RunMatScript.bat", [System.StringComparison]::OrdinalIgnoreCase)) {
                Assert-MSNoReparsePath -Path $resolved | Out-Null
                return $resolved
            }
        }
        catch {
            continue
        }
    }
    return $null
}

function Get-MSMaterialsStudioVersion {
    param(
        [string]$Runner,
        [string]$ExplicitVersion
    )

    if (-not [string]::IsNullOrWhiteSpace($ExplicitVersion)) {
        if ($ExplicitVersion -notin @("2020", "20.1")) {
            throw "Materials Studio version must be 2020 or 20.1."
        }
        return $ExplicitVersion
    }
    if ($Runner -match '(?i)(?:materials studio[^\\/]*)(20\.1|2020)' -or $Runner -match '(?i)(20\.1|2020)') {
        return $Matches[1]
    }
    return $null
}

function Assert-MSSettings {
    param(
        [Parameter(Mandatory = $true)]$Settings,
        [Parameter(Mandatory = $true)]$Paths
    )

    if ([string]$Settings.schema -ne $script:MSConfigSchema) {
        throw "Unsupported configuration schema. Rerun Configure-MS-MCP.bat."
    }
    $runner = Resolve-MSFullPath -Path ([string]$Settings.materials_studio.runner)
    if (-not (Test-Path -LiteralPath $runner -PathType Leaf)) {
        throw "Configured MATERIAL_STUDIO_RUNNER was not found: $runner"
    }
    if (-not [System.IO.Path]::GetFileName($runner).Equals("RunMatScript.bat", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Configured runner must be RunMatScript.bat."
    }
    Assert-MSNoReparsePath -Path $runner | Out-Null
    $workspace = Resolve-MSFullPath -Path ([string]$Settings.workspace)
    if (-not (Test-Path -LiteralPath $workspace -PathType Container)) {
        throw "Configured workspace was not found: $workspace"
    }
    Assert-MSNoReparsePath -Path $workspace | Out-Null
    foreach ($managedRoot in @($Paths.config_root, $Paths.logs_root, $Paths.runtimes_root)) {
        if (Test-MSPathsOverlap -Left $workspace -Right $managedRoot) {
            throw "Configured workspace overlaps a managed config, logs, or runtimes path. Rerun Configure-MS-MCP.bat."
        }
    }
    return [ordered]@{ runner = $runner; workspace = $workspace }
}

function Get-MSExpectedWheelHash {
    param(
        [Parameter(Mandatory = $true)][string]$WheelPath,
        [string]$ExplicitSha256,
        [string]$Sha256SumsPath,
        [string]$ReleaseManifestPath
    )

    $values = New-Object System.Collections.Generic.List[string]
    if (-not [string]::IsNullOrWhiteSpace($ExplicitSha256)) {
        $normalizedExplicit = $ExplicitSha256.Trim().ToLowerInvariant()
        if ($normalizedExplicit -notmatch '^[0-9a-f]{64}$') {
            throw "Explicit WheelSha256 must be exactly 64 hexadecimal characters."
        }
        $values.Add($normalizedExplicit)
    }
    if (-not [string]::IsNullOrWhiteSpace($Sha256SumsPath) -and (Test-Path -LiteralPath $Sha256SumsPath -PathType Leaf)) {
        $wheelName = [System.IO.Path]::GetFileName($WheelPath)
        foreach ($line in (Get-Content -LiteralPath $Sha256SumsPath -Encoding UTF8)) {
            if ($line -match '^([0-9A-Fa-f]{64})\s+[* ]?(.+?)\s*$') {
                $listedName = [System.IO.Path]::GetFileName($Matches[2].Replace('/', '\'))
                if ($listedName.Equals($wheelName, [System.StringComparison]::OrdinalIgnoreCase)) {
                    $values.Add($Matches[1].ToLowerInvariant())
                }
            }
        }
    }
    if (-not [string]::IsNullOrWhiteSpace($ReleaseManifestPath) -and (Test-Path -LiteralPath $ReleaseManifestPath -PathType Leaf)) {
        $manifest = Read-MSJson -Path $ReleaseManifestPath
        $wheelName = [System.IO.Path]::GetFileName($WheelPath)
        if ($null -ne $manifest.wheel -and [string]$manifest.wheel.sha256 -match '^[0-9A-Fa-f]{64}$') {
            $values.Add(([string]$manifest.wheel.sha256).ToLowerInvariant())
        }
        foreach ($artifact in @($manifest.artifacts) + @($manifest.files)) {
            $artifactPath = [string]$artifact.path
            if ([System.IO.Path]::GetFileName($artifactPath).Equals($wheelName, [System.StringComparison]::OrdinalIgnoreCase) -and [string]$artifact.sha256 -match '^[0-9A-Fa-f]{64}$') {
                $values.Add(([string]$artifact.sha256).ToLowerInvariant())
            }
        }
    }
    $unique = @($values | Where-Object { $_ -match '^[0-9a-f]{64}$' } | Select-Object -Unique)
    if ($unique.Count -eq 0) {
        throw "No trusted SHA-256 was found for the wheel. Provide -WheelSha256 or a release checksum file."
    }
    if ($unique.Count -ne 1) {
        throw "Wheel SHA-256 sources disagree."
    }
    return $unique[0]
}

function Assert-MSRuntimePathLength {
    param([Parameter(Mandatory = $true)][string]$RuntimeRoot)

    $root = Resolve-MSFullPath -Path $RuntimeRoot
    $processProbe = Join-Path $root ".venv\Lib\site-packages\material_studio_mcp_server\gui_view_replay_executor.py"
    if ($processProbe.Length -ge 240) {
        throw "Versioned runtime path is too long for reliable Windows venv/process startup. Choose a shorter -LocalAppDataRoot; projected path length is $($processProbe.Length) (must be below 240)."
    }
    return $root
}

function Get-MSInstalledMcpVersion {
    param([Parameter(Mandatory = $true)][string]$PythonExecutable)

    $code = "from importlib.metadata import version; import mcp.server.fastmcp; print(version('mcp'))"
    $savedPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $observedText = (& $PythonExecutable -B -X utf8 -I -c $code 2>&1 | Out-String).Trim()
        $observedExit = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $savedPreference
    }
    if ($observedExit -ne 0) {
        $detail = ($observedText -replace '\s+', ' ')
        if ($detail.Length -gt 800) { $detail = $detail.Substring($detail.Length - 800) }
        throw "Installed MCP SDK cannot import mcp.server.fastmcp: $detail"
    }
    try { $observed = [Version]$observedText }
    catch { throw "Installed MCP SDK version is invalid: $observedText" }
    if ($observed -lt [Version]"1.12.4" -or $observed.Major -ge 2) {
        throw "Installed MCP SDK version is outside the reviewed >=1.12.4,<2 range: $observed"
    }
    return $observed
}

function Get-MSInstalledWindowsUiaVersions {
    param([Parameter(Mandatory = $true)][string]$PythonExecutable)

    $code = "import json; from importlib.metadata import version; print(json.dumps(dict(comtypes=version('comtypes'), pywinauto=version('pywinauto')), sort_keys=True))"
    $savedPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $observedText = (& $PythonExecutable -B -X utf8 -I -c $code 2>&1 | Out-String).Trim()
        $observedExit = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $savedPreference
    }
    if ($observedExit -ne 0) {
        $detail = ($observedText -replace '\s+', ' ')
        if ($detail.Length -gt 800) { $detail = $detail.Substring($detail.Length - 800) }
        throw "Installed Windows UI dependency versions could not be read: $detail"
    }
    try { $observed = $observedText | ConvertFrom-Json }
    catch { throw "Installed Windows UI dependency versions are invalid: $observedText" }
    if ([string]$observed.comtypes -ne "1.4.16" -or [string]$observed.pywinauto -ne "0.6.9") {
        throw "Installed Windows UI dependencies must be comtypes 1.4.16 and pywinauto 0.6.9."
    }
    return [ordered]@{
        comtypes = [string]$observed.comtypes
        pywinauto = [string]$observed.pywinauto
    }
}

function Test-MSRuntime {
    param(
        [Parameter(Mandatory = $true)][string]$RuntimeRoot,
        [Parameter(Mandatory = $true)][string]$Version,
        [string]$ExpectedWheelSha256,
        [string]$ExpectedManifestSha256
    )

    $root = Assert-MSNoReparsePath -Path $RuntimeRoot
    $manifestPath = Join-Path $root "runtime-manifest.json"
    $manifest = Read-MSJson -Path $manifestPath
    if ([string]$manifest.schema -ne $script:MSRuntimeSchema) { throw "Runtime manifest schema mismatch." }
    if ([string]$manifest.version -ne $Version) { throw "Runtime version mismatch." }
    if (-not (Resolve-MSFullPath -Path ([string]$manifest.runtime_root)).Equals($root, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Runtime root binding mismatch." }
    if (-not [string]::IsNullOrWhiteSpace($ExpectedWheelSha256) -and [string]$manifest.wheel.sha256 -ne $ExpectedWheelSha256) { throw "Runtime wheel SHA-256 mismatch." }
    $manifestHash = Get-MSFileSha256 -Path $manifestPath
    if (-not [string]::IsNullOrWhiteSpace($ExpectedManifestSha256) -and $manifestHash -ne $ExpectedManifestSha256) { throw "Runtime manifest SHA-256 mismatch." }
    $treeHash = Get-MSTreeSha256 -Root $root
    if ($treeHash -ne [string]$manifest.runtime_tree_sha256) { throw "Runtime tree SHA-256 mismatch." }
    if ([string]$manifest.python_relative_path -ne ".venv/Scripts/python.exe") { throw "Runtime Python relative path is invalid." }
    $python = Resolve-MSFullPath -Path (Join-Path $root ([string]$manifest.python_relative_path))
    if (-not (Test-MSPathWithin -Path $python -Root $root)) { throw "Runtime Python path escaped the runtime root." }
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw "Runtime Python executable is missing." }
    if ([string]$manifest.package_relative_path -ne ".venv/Lib/site-packages/material_studio_mcp_server") { throw "Runtime package relative path is invalid." }
    $packageRoot = Resolve-MSFullPath -Path (Join-Path $root ([string]$manifest.package_relative_path))
    if (-not (Test-MSPathWithin -Path $packageRoot -Root $root) -or -not (Test-Path -LiteralPath $packageRoot -PathType Container)) { throw "Runtime package path is missing or escaped the runtime root." }
    $versionCode = "from importlib.metadata import version; print(version('materials-studio-mcp'))"
    $observedVersion = (& $python -B -I -c $versionCode 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $observedVersion -ne $Version) { throw "Installed package version mismatch: $observedVersion" }
    try { $declaredMcpVersion = [Version]([string]$manifest.dependency_versions.mcp) }
    catch { throw "Runtime manifest MCP SDK version is missing or invalid." }
    if ($declaredMcpVersion -lt [Version]"1.12.4" -or $declaredMcpVersion.Major -ge 2) {
        throw "Runtime manifest MCP SDK version is outside the reviewed >=1.12.4,<2 range: $declaredMcpVersion"
    }
    $observedMcpVersion = Get-MSInstalledMcpVersion -PythonExecutable $python
    if ($observedMcpVersion -ne $declaredMcpVersion) {
        throw "Runtime manifest/installed MCP SDK version mismatch: declared $declaredMcpVersion, observed $observedMcpVersion"
    }
    if ([string]$manifest.dependency_versions.comtypes -ne "1.4.16" -or
        [string]$manifest.dependency_versions.pywinauto -ne "0.6.9") {
        throw "Runtime manifest Windows UI dependency versions are missing or unsupported."
    }
    $observedWindowsUiaVersions = Get-MSInstalledWindowsUiaVersions -PythonExecutable $python
    if ([string]$observedWindowsUiaVersions.comtypes -ne [string]$manifest.dependency_versions.comtypes -or
        [string]$observedWindowsUiaVersions.pywinauto -ne [string]$manifest.dependency_versions.pywinauto) {
        throw "Runtime manifest/installed Windows UI dependency version mismatch."
    }
    return [ordered]@{ root = $root; manifest_path = $manifestPath; manifest_sha256 = $manifestHash; python = $python; package_root = $packageRoot; mcp_version = $observedMcpVersion.ToString(); windows_uia_versions = $observedWindowsUiaVersions; manifest = $manifest }
}
