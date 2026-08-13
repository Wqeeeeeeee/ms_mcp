[CmdletBinding()]
param(
    [string]$WheelPath,
    [string]$WheelSha256,
    [string]$Sha256SumsPath,
    [string]$ReleaseManifestPath,
    [string]$PythonCommand,
    [string]$LocalAppDataRoot,
    [switch]$NonInteractive,
    [switch]$PlanOnly
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "WindowsInstaller.Common.ps1")

function Invoke-CheckedNative {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Label
    )
    $captured = Invoke-MSCapturedNative -Executable $Executable -Arguments $Arguments
    if (-not [string]::IsNullOrEmpty([string]$captured.stdout)) {
        [Console]::Out.Write([string]$captured.stdout)
    }
    if (-not [string]::IsNullOrEmpty([string]$captured.stderr)) {
        [Console]::Error.Write([string]$captured.stderr)
    }
    if ($captured.exit_code -ne 0) {
        throw "$Label failed with exit code $($captured.exit_code)."
    }
}

function ConvertTo-MSNativeArgument {
    param([AllowEmptyString()][Parameter(Mandatory = $true)][string]$Argument)

    # ProcessStartInfo.ArgumentList is unavailable in Windows PowerShell 5.1.
    # Quote according to the Windows CommandLineToArgvW/CRT rules. The process is
    # still started directly (never through cmd.exe), so shell metacharacters are
    # inert and paths containing spaces, CJK text, quotes, or trailing slashes are
    # passed as one exact argv element.
    if ($Argument.Length -gt 0 -and $Argument -notmatch '[\s"]') { return $Argument }
    $builder = New-Object System.Text.StringBuilder
    [void]$builder.Append([char]34)
    $backslashes = 0
    foreach ($character in $Argument.ToCharArray()) {
        if ($character -eq [char]92) {
            $backslashes++
            continue
        }
        if ($character -eq [char]34) {
            [void]$builder.Append(([string][char]92) * (($backslashes * 2) + 1))
            [void]$builder.Append([char]34)
            $backslashes = 0
            continue
        }
        if ($backslashes -gt 0) {
            [void]$builder.Append(([string][char]92) * $backslashes)
            $backslashes = 0
        }
        [void]$builder.Append($character)
    }
    if ($backslashes -gt 0) {
        [void]$builder.Append(([string][char]92) * ($backslashes * 2))
    }
    [void]$builder.Append([char]34)
    return $builder.ToString()
}

function Invoke-MSCapturedNative {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [int]$TimeoutMilliseconds = 300000
    )

    # Windows PowerShell converts native stderr pipeline records into terminating
    # errors when ErrorActionPreference is Stop. Read the two OS pipes directly so
    # a warning cannot corrupt JSON stdout and a failing Python probe retains its
    # complete traceback for the installer diagnostic.
    $start = New-Object System.Diagnostics.ProcessStartInfo
    $start.FileName = $Executable
    $start.Arguments = (($Arguments | ForEach-Object { ConvertTo-MSNativeArgument -Argument ([string]$_) }) -join " ")
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    $start.StandardOutputEncoding = $utf8NoBom
    $start.StandardErrorEncoding = $utf8NoBom
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $start
    try {
        if (-not $process.Start()) { throw "Native process did not start: $Executable" }
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutMilliseconds)) {
            try { $process.Kill() } catch {}
            $process.WaitForExit()
            throw "Native process timed out after $TimeoutMilliseconds ms: $Executable"
        }
        # WaitForExit() without a timeout also drains asynchronous stream events on
        # older .NET Framework releases used by Windows PowerShell 5.1.
        $process.WaitForExit()
        return [pscustomobject]@{
            exit_code = $process.ExitCode
            stdout = $stdoutTask.Result
            stderr = $stderrTask.Result
        }
    }
    finally {
        $process.Dispose()
    }
}

function Get-MSInstalledMcpVersion {
    param([Parameter(Mandatory = $true)][string]$PythonExecutable)

    # Override the common helper in this installer so warnings emitted while MCP
    # imports are kept on stderr instead of being merged into the version value.
    $code = "from importlib.metadata import version; import mcp.server.fastmcp; print(version('mcp'))"
    $captured = Invoke-MSCapturedNative -Executable $PythonExecutable -Arguments @("-B", "-X", "utf8", "-I", "-c", $code)
    $observedText = ([string]$captured.stdout).Trim()
    if ($captured.exit_code -ne 0) {
        $detail = (([string]$captured.stderr + " " + [string]$captured.stdout).Trim() -replace '\s+', ' ')
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

function Test-MSConsoleEntrypointHelp {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string]$Name
    )
    $start = New-Object System.Diagnostics.ProcessStartInfo
    $start.FileName = $Executable
    $start.Arguments = "--help"
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $start
    if (-not $process.Start()) { throw "Console entrypoint did not start: $Name" }
    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    if (-not $process.WaitForExit(30000)) {
        try { $process.Kill() } catch {}
        throw "Console entrypoint --help timed out: $Name"
    }
    if ($process.ExitCode -ne 0) {
        $detail = (($stderr + " " + $stdout).Trim() -replace '\s+', ' ')
        if ($detail.Length -gt 2000) { $detail = $detail.Substring($detail.Length - 2000) }
        throw "Console entrypoint --help failed: $Name ($detail)"
    }
}

$target = $null
$stagingRuntime = $null
$stagingRuntimeForVerification = $null
$stagingRuntimeAliases = @()
$stagingOwnedByThisProcess = $false
$version = $null
$paths = $null
try {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw "Install-MS-MCP is supported only on Windows."
    }
    $releaseRoot = Get-MSReleaseRoot
    $version = Get-MSPackageVersion -ReleaseRoot $releaseRoot
    $paths = Get-MSProductPaths -LocalAppDataRoot $LocalAppDataRoot
    Assert-MSNoReparsePath -Path $paths.product_root | Out-Null
    $settings = Read-MSJson -Path $paths.settings_path
    $settingsBinding = Assert-MSSettings -Settings $settings -Paths $paths
    if ([string]$settings.package_version -ne $version) {
        throw "Configuration version $($settings.package_version) does not match installer version $version. Rerun Configure-MS-MCP.bat."
    }

    if ([string]::IsNullOrWhiteSpace($WheelPath)) {
        $wheelCandidates = @(
            (Join-Path $releaseRoot ("dist\materials_studio_mcp-{0}-py3-none-any.whl" -f $version)),
            (Join-Path $releaseRoot ("materials_studio_mcp-{0}-py3-none-any.whl" -f $version))
        )
        $WheelPath = $wheelCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    }
    if ([string]::IsNullOrWhiteSpace($WheelPath)) {
        throw "Built wheel not found. Pass -WheelPath or place the versioned wheel in dist."
    }
    $wheel = Resolve-MSFullPath -Path $WheelPath -BasePath $releaseRoot
    Assert-MSNoReparsePath -Path $wheel | Out-Null
    if (-not (Test-Path -LiteralPath $wheel -PathType Leaf)) { throw "Wheel not found: $wheel" }
    $expectedWheelName = "materials_studio_mcp-$version-py3-none-any.whl"
    if (-not [System.IO.Path]::GetFileName($wheel).Equals($expectedWheelName, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Wheel filename must be $expectedWheelName."
    }

    $autoDiscoverHashSources = [string]::IsNullOrWhiteSpace($WheelSha256) -and [string]::IsNullOrWhiteSpace($Sha256SumsPath) -and [string]::IsNullOrWhiteSpace($ReleaseManifestPath)
    if ($autoDiscoverHashSources) {
        foreach ($candidate in @((Join-Path $releaseRoot "dist\SHA256SUMS.txt"), (Join-Path $releaseRoot "SHA256SUMS.txt"))) {
            if (Test-Path -LiteralPath $candidate -PathType Leaf) { $Sha256SumsPath = $candidate; break }
        }
    }
    if ($autoDiscoverHashSources) {
        foreach ($candidate in @((Join-Path $releaseRoot "dist\release-manifest.json"), (Join-Path $releaseRoot "release-manifest.json"))) {
            if (Test-Path -LiteralPath $candidate -PathType Leaf) { $ReleaseManifestPath = $candidate; break }
        }
    }
    $expectedWheelHash = Get-MSExpectedWheelHash -WheelPath $wheel -ExplicitSha256 $WheelSha256 -Sha256SumsPath $Sha256SumsPath -ReleaseManifestPath $ReleaseManifestPath
    $observedWheelHash = Get-MSFileSha256 -Path $wheel
    if ($observedWheelHash -ne $expectedWheelHash) {
        throw "Wheel SHA-256 mismatch. Expected $expectedWheelHash, observed $observedWheelHash."
    }

    $bootstrapPython = if (-not [string]::IsNullOrWhiteSpace($PythonCommand)) {
        Resolve-MSPython -PythonCommand $PythonCommand
    }
    else {
        Resolve-MSPython -PythonCommand ([string]$settings.python.executable)
    }
    $pythonProbe = Get-MSPythonProbe -PythonExecutable $bootstrapPython
    foreach ($directory in @($paths.product_root, $paths.config_root, $paths.logs_root, $paths.runtimes_root)) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
        Assert-MSNoReparsePath -Path $directory | Out-Null
    }
    $runtimesRoot = Resolve-MSFullPath -Path $paths.runtimes_root
    Assert-MSNoReparsePath -Path $runtimesRoot | Out-Null
    $target = Assert-MSRuntimePathLength -RuntimeRoot (Join-Path $runtimesRoot $version)
    if ((-not (Test-MSPathWithin -Path $target -Root $runtimesRoot)) -or
        (-not [System.IO.Path]::GetDirectoryName($target).Equals($runtimesRoot, [System.StringComparison]::OrdinalIgnoreCase))) {
        throw "Runtime target escaped the managed runtimes root."
    }
    Assert-MSNoReparsePath -Path $target | Out-Null

    if ($PlanOnly) {
        [ordered]@{
            schema = "materials_studio_mcp_windows_install_plan_v1"
            ok = $true
            read_only = $true
            version = $version
            wheel = $wheel
            wheel_sha256 = $observedWheelHash
            bootstrap_python = $bootstrapPython
            runtime_target = $target
            runtime_exists = (Test-Path -LiteralPath $target)
            active_codex_config_modified = $false
            materials_studio_started = $false
            calculation_started = $false
        } | ConvertTo-Json -Depth 8
        exit 0
    }

    $runtimeStatus = $null
    $runtimeReused = $false
    if (Test-Path -LiteralPath $target) {
        $runtimeStatus = Test-MSRuntime -RuntimeRoot $target -Version $version -ExpectedWheelSha256 $observedWheelHash
        $runtimeReused = $true
    }
    else {
        # Build in a unique sibling. A process crash can leave this directory behind,
        # but no incomplete byte is ever visible at the version-addressed target.
        # Other attempts' staging directories are deliberately never discovered or removed.
        for ($attempt = 0; $attempt -lt 8 -and $null -eq $stagingRuntime; $attempt++) {
            # Keep the random sibling name short so staging does not exceed the
            # already-enforced Windows venv path budget of the final version name.
            $candidateName = ".i" + [Guid]::NewGuid().ToString("N").Substring(0, 7)
            $candidate = Resolve-MSFullPath -Path (Join-Path $runtimesRoot $candidateName)
            if (-not (Test-Path -LiteralPath $candidate)) { $stagingRuntime = $candidate }
        }
        if ($null -eq $stagingRuntime) { throw "Could not allocate a unique runtime staging directory." }
        $stagingRuntime = Assert-MSRuntimePathLength -RuntimeRoot $stagingRuntime
        if ((-not (Test-MSPathWithin -Path $stagingRuntime -Root $runtimesRoot)) -or
            (-not [System.IO.Path]::GetDirectoryName($stagingRuntime).Equals($runtimesRoot, [System.StringComparison]::OrdinalIgnoreCase))) {
            throw "Runtime staging directory escaped the managed runtimes root."
        }
        Assert-MSNoReparsePath -Path $stagingRuntime | Out-Null
        New-Item -ItemType Directory -Path $stagingRuntime | Out-Null
        $stagingOwnedByThisProcess = $true
        $stagingRuntimeForVerification = $stagingRuntime
        $stagingRuntimeAliases = @(
            $stagingRuntime,
            (Get-Item -LiteralPath $stagingRuntime -Force).FullName
        ) | Select-Object -Unique
        Assert-MSNoReparsePath -Path $stagingRuntime | Out-Null

        $venv = Join-Path $stagingRuntime ".venv"
        Invoke-CheckedNative -Executable $bootstrapPython -Arguments @("-I", "-m", "venv", $venv) -Label "venv creation"
        if ($env:MATERIAL_STUDIO_MCP_TEST_INTERRUPT_AFTER_VENV -eq "1") {
            throw "Simulated interrupted installation."
        }
        $runtimePython = Join-Path $venv "Scripts\python.exe"
        if (-not (Test-Path -LiteralPath $runtimePython -PathType Leaf)) { throw "venv did not create Scripts\python.exe." }

        $artifactRoot = Join-Path $stagingRuntime "artifacts"
        New-Item -ItemType Directory -Path $artifactRoot | Out-Null
        $installedWheel = Join-Path $artifactRoot $expectedWheelName
        Copy-Item -LiteralPath $wheel -Destination $installedWheel
        if ((Get-MSFileSha256 -Path $installedWheel) -ne $observedWheelHash) { throw "Copied wheel failed SHA-256 verification." }

        Invoke-CheckedNative -Executable $runtimePython -Arguments @("-I", "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--no-warn-script-location", $installedWheel) -Label "wheel installation"
        Invoke-CheckedNative -Executable $runtimePython -Arguments @("-I", "-m", "pip", "check") -Label "pip check"
        $mcpProbeCode = @'
import json
from importlib.metadata import version
import mcp
import mcp.server.fastmcp
try:
    mcp_version = version("mcp")
except Exception:
    mcp_version = getattr(mcp, "__version__", "unknown")
print(json.dumps({"mcp_version": mcp_version, "fastmcp_import": True}))
'@
        $mcpProbeBase64 = [Convert]::ToBase64String((New-Object System.Text.UTF8Encoding($false)).GetBytes($mcpProbeCode))
        $mcpProbeBootstrap = "import base64;exec(base64.b64decode('$mcpProbeBase64'))"
        $mcpProbeCapture = Invoke-MSCapturedNative -Executable $runtimePython -Arguments @("-B", "-X", "utf8", "-I", "-c", $mcpProbeBootstrap)
        $mcpProbeText = ([string]$mcpProbeCapture.stdout).Trim()
        if ($mcpProbeCapture.exit_code -ne 0) {
            $mcpProbeDetail = (([string]$mcpProbeCapture.stderr + " " + [string]$mcpProbeCapture.stdout).Trim() -replace '\s+', ' ')
            if ($mcpProbeDetail.Length -gt 800) { $mcpProbeDetail = $mcpProbeDetail.Substring($mcpProbeDetail.Length - 800) }
            throw "Installed MCP SDK is incompatible with mcp.server.fastmcp; use the wheel's reviewed mcp<2 dependency contract. $mcpProbeDetail"
        }
        try { $mcpProbe = $mcpProbeText | ConvertFrom-Json; $mcpVersion = [Version]([string]$mcpProbe.mcp_version) }
        catch { throw "Installed MCP SDK version could not be verified: $mcpProbeText" }
        if ($mcpVersion.Major -ge 2 -or $mcpVersion -lt [Version]"1.12.4") { throw "Installed MCP SDK version is outside the reviewed >=1.12.4,<2 range: $mcpVersion" }
        $windowsUiaVersions = Get-MSInstalledWindowsUiaVersions -PythonExecutable $runtimePython

        $expectedEntrypoints = [ordered]@{
            "ms-mcp" = "material_studio_mcp_server.server:main"
            "ms-mcp-config-doctor" = "material_studio_mcp_server.codex_config:main"
            "ms-mcp-config-register" = "material_studio_mcp_server.codex_registration:main"
            "ms-mcp-runtime-deploy" = "material_studio_mcp_server.runtime_deployment:main"
            "ms-mcp-protocol-smoke" = "material_studio_mcp_server.protocol_smoke:main"
            "ms-mcp-live-smoke" = "material_studio_mcp_server.live_smoke:main"
            "ms-mcp-dashboard" = "material_studio_mcp_server.read_only_dashboard:main"
        }
        $entrypointNames = @($expectedEntrypoints.Keys)
        $entrypoints = [ordered]@{}
        foreach ($name in $entrypointNames) {
            $candidate = Join-Path $venv ("Scripts\{0}.exe" -f $name)
            if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
                throw "Required console entrypoint missing: $name"
            }
            $entrypoints[$name] = (".venv/Scripts/{0}.exe" -f $name)
        }
        $entrypointProbeCode = @'
import json
from importlib.metadata import distribution
dist = distribution("materials-studio-mcp")
print(json.dumps({ep.name: ep.value for ep in dist.entry_points if ep.group == "console_scripts"}, sort_keys=True))
'@
        $entrypointProbeBase64 = [Convert]::ToBase64String((New-Object System.Text.UTF8Encoding($false)).GetBytes($entrypointProbeCode))
        $entrypointProbeBootstrap = "import base64;exec(base64.b64decode('$entrypointProbeBase64'))"
        $entrypointMetadataCapture = Invoke-MSCapturedNative -Executable $runtimePython -Arguments @("-B", "-X", "utf8", "-I", "-c", $entrypointProbeBootstrap)
        $entrypointMetadataText = ([string]$entrypointMetadataCapture.stdout).Trim()
        if ($entrypointMetadataCapture.exit_code -ne 0) {
            $entrypointMetadataDetail = (([string]$entrypointMetadataCapture.stderr + " " + [string]$entrypointMetadataCapture.stdout).Trim())
            throw "Console entrypoint metadata probe failed: $entrypointMetadataDetail"
        }
        $entrypointMetadata = $entrypointMetadataText | ConvertFrom-Json
        foreach ($name in $entrypointNames) {
            if ([string]$entrypointMetadata.$name -ne [string]$expectedEntrypoints[$name]) { throw "Console entrypoint metadata mismatch: $name" }
        }
        foreach ($name in ($entrypointNames | Where-Object { $_ -ne "ms-mcp" })) {
            $candidate = Join-Path $venv ("Scripts\{0}.exe" -f $name)
            Test-MSConsoleEntrypointHelp -Executable $candidate -Name $name
        }
        $versionCode = "from importlib.metadata import version; print(version('materials-studio-mcp'))"
        $installedVersionCapture = Invoke-MSCapturedNative -Executable $runtimePython -Arguments @("-B", "-X", "utf8", "-I", "-c", $versionCode)
        $installedVersion = ([string]$installedVersionCapture.stdout).Trim()
        if ($installedVersionCapture.exit_code -ne 0 -or $installedVersion -ne $version) {
            throw "Installed package version $installedVersion does not match plugin version $version."
        }
        # A successful import may emit third-party warnings whose source location
        # still names the private staging directory used before publication. Keep
        # successful probe stderr private; surface it only when the import fails.
        $packageImportCapture = Invoke-MSCapturedNative -Executable $runtimePython -Arguments @(
            "-B", "-X", "utf8", "-I", "-c", "import material_studio_mcp_server.server"
        )
        if ($packageImportCapture.exit_code -ne 0) {
            $packageImportDetail = (([string]$packageImportCapture.stderr + " " + [string]$packageImportCapture.stdout).Trim() -replace '\s+', ' ')
            if ($packageImportDetail.Length -gt 2000) { $packageImportDetail = $packageImportDetail.Substring($packageImportDetail.Length - 2000) }
            throw "Package import failed: $packageImportDetail"
        }

        $directUrlFiles = @(Get-ChildItem -LiteralPath (Join-Path $venv "Lib\site-packages") -Filter "direct_url.json" -File -Recurse -ErrorAction SilentlyContinue)
        foreach ($directUrlFile in $directUrlFiles) {
            $directUrl = Read-MSJson -Path $directUrlFile.FullName
            try { $directUri = [Uri]([string]$directUrl.url) }
            catch { throw "Installed metadata contains an invalid direct URL." }
            if (-not $directUri.IsFile) { throw "Installed metadata contains a non-file direct URL." }
            $directPath = Resolve-MSFullPath -Path $directUri.LocalPath
            if (-not (Test-MSPathWithin -Path $directPath -Root $stagingRuntime)) { throw "Installed metadata contains a non-runtime direct URL." }
            if (Test-MSPathsOverlap -Left $directPath -Right $releaseRoot) { throw "Installed metadata contains a development/release source path." }
            $directRelativePath = $directPath.Substring($stagingRuntime.TrimEnd('\').Length).TrimStart('\')
            $publishedDirectPath = Resolve-MSFullPath -Path (Join-Path $target $directRelativePath)
            if (-not (Test-MSPathWithin -Path $publishedDirectPath -Root $target)) { throw "Installed metadata relocation escaped the published runtime." }
            $directUrl.url = [System.Uri]::new($publishedDirectPath).AbsoluteUri
            Write-MSJsonAtomic -Path $directUrlFile.FullName -Value $directUrl
        }

        # Venv activation metadata and distlib launchers otherwise retain the temporary
        # staging path. Relocate text metadata and regenerate the managed console
        # launchers for the final, version-addressed Python executable before publish.
        $publishedRuntimePython = Join-Path $target ".venv\Scripts\python.exe"
        $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
        $relocatableTextFiles = @(
            (Join-Path $venv "pyvenv.cfg"),
            (Join-Path $venv "Scripts\activate"),
            (Join-Path $venv "Scripts\activate.bat"),
            (Join-Path $venv "Scripts\Activate.ps1"),
            (Join-Path $venv "Scripts\activate.fish")
        )
        foreach ($relocatableTextFile in $relocatableTextFiles) {
            if (-not (Test-Path -LiteralPath $relocatableTextFile -PathType Leaf)) { continue }
            $relocatableText = [System.IO.File]::ReadAllText($relocatableTextFile)
            $relocatedText = $relocatableText
            foreach ($stagingAlias in $stagingRuntimeAliases) {
                $relocatedText = $relocatedText.Replace([string]$stagingAlias, $target)
            }
            if ($relocatedText -ne $relocatableText) {
                [System.IO.File]::WriteAllText($relocatableTextFile, $relocatedText, $utf8NoBom)
            }
        }

        $launcherPayload = [ordered]@{
            scripts_dir = (Join-Path $venv "Scripts")
            published_python = $publishedRuntimePython
            staging_roots = @($stagingRuntimeAliases)
        } | ConvertTo-Json -Depth 6 -Compress
        $launcherPayloadBase64 = [Convert]::ToBase64String($utf8NoBom.GetBytes($launcherPayload))
        $launcherRebindCode = @'
import base64
import json
from importlib.metadata import distributions
from pathlib import Path
from pip._vendor.distlib.scripts import ScriptMaker

payload = json.loads(base64.b64decode("__PAYLOAD__").decode("utf-8"))
scripts_dir = Path(payload["scripts_dir"])
staging_paths = set()
for raw_root in payload["staging_roots"]:
    raw_path = Path(raw_root)
    for root_text in {str(raw_path), str(raw_path.resolve(strict=True))}:
        staging_paths.add(root_text.encode("utf-8"))
removed = []
for candidate in scripts_dir.glob("*.exe"):
    if any(staging_path in candidate.read_bytes() for staging_path in staging_paths):
        removed.append(str(candidate.resolve()))
        candidate.unlink()

entrypoints = {}
for dist in distributions():
    for entrypoint in dist.entry_points:
        if entrypoint.group != "console_scripts":
            continue
        previous = entrypoints.get(entrypoint.name)
        if previous is not None and previous != entrypoint.value:
            raise RuntimeError(f"Duplicate console entrypoint: {entrypoint.name}")
        entrypoints[entrypoint.name] = entrypoint.value

maker = ScriptMaker(None, payload["scripts_dir"])
maker.clobber = True
maker.executable = payload["published_python"]
maker.set_mode = True
maker.variants = {""}
created = {}
for name, value in entrypoints.items():
    created[name] = maker.make(f"{name} = {value}")
print(json.dumps({"created": created, "removed": removed}, sort_keys=True))
'@.Replace("__PAYLOAD__", $launcherPayloadBase64)
        $launcherRebindBase64 = [Convert]::ToBase64String($utf8NoBom.GetBytes($launcherRebindCode))
        $launcherRebindBootstrap = "import base64;exec(base64.b64decode('$launcherRebindBase64'))"
        $launcherRebindCapture = Invoke-MSCapturedNative -Executable $runtimePython -Arguments @("-B", "-X", "utf8", "-I", "-c", $launcherRebindBootstrap)
        $launcherRebindText = ([string]$launcherRebindCapture.stdout).Trim()
        if ($launcherRebindCapture.exit_code -ne 0) {
            $launcherRebindDetail = (([string]$launcherRebindCapture.stderr + " " + [string]$launcherRebindCapture.stdout).Trim())
            throw "Console entrypoint relocation failed: $launcherRebindDetail"
        }
        try { $launcherRebindResult = $launcherRebindText | ConvertFrom-Json }
        catch { throw "Console entrypoint relocation returned invalid output: $launcherRebindText" }
        foreach ($name in $entrypointNames) {
            if ($null -eq $launcherRebindResult.created.$name) { throw "Installed console entrypoint metadata was not relocated: $name" }
            $candidate = Join-Path $venv ("Scripts\{0}.exe" -f $name)
            if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { throw "Relocated console entrypoint missing: $name" }
            $launcherBinaryText = [System.Text.Encoding]::UTF8.GetString([System.IO.File]::ReadAllBytes($candidate))
            if (-not $launcherBinaryText.Contains($publishedRuntimePython)) { throw "Console entrypoint does not bind the published runtime Python: $name" }
            if ($launcherBinaryText.Contains($stagingRuntime)) { throw "Console entrypoint retained the staging runtime path: $name" }
        }

        # Freeze every installed bytecode cache before publishing the runtime hash.
        # The hash includes .pyc files, so later bytecode additions or edits fail closed.
        # Strip the staging prefix and record the final package path in code objects.
        $stagedSitePackages = Join-Path $venv "Lib\site-packages"
        $publishedSitePackages = Join-Path $target ".venv\Lib\site-packages"
        Invoke-CheckedNative -Executable $runtimePython -Arguments @(
            "-B", "-I", "-m", "compileall", "-f", "-q", "--invalidation-mode", "checked-hash",
            "-s", $stagedSitePackages, "-p", $publishedSitePackages, $stagedSitePackages
        ) -Label "runtime bytecode compilation"
        $stagedScripts = Join-Path $venv "Scripts"
        $publishedScripts = Join-Path $target ".venv\Scripts"
        Invoke-CheckedNative -Executable $runtimePython -Arguments @(
            "-B", "-I", "-m", "compileall", "-f", "-q", "--invalidation-mode", "checked-hash",
            "-s", $stagedScripts, "-p", $publishedScripts, $stagedScripts
        ) -Label "runtime Scripts bytecode compilation"

        # Relocation changed direct_url.json and every generated console launcher;
        # compileall may also replace recorded bytecode. Rebind every installed
        # RECORD entry (except RECORD itself, whose digest is intentionally empty)
        # and then independently verify every path, digest, and size.
        $recordPayload = [ordered]@{
            runtime_root = $stagingRuntime
            site_root = $stagedSitePackages
            removed_launchers = @($launcherRebindResult.removed)
        } | ConvertTo-Json -Compress
        $recordPayloadBase64 = [Convert]::ToBase64String($utf8NoBom.GetBytes($recordPayload))
        $recordRepairCode = @'
import base64
import csv
import hashlib
import json
import os
from pathlib import Path

payload = json.loads(base64.b64decode("__PAYLOAD__").decode("utf-8"))
runtime_root = Path(payload["runtime_root"]).resolve(strict=True)
site_root = Path(payload["site_root"]).resolve(strict=True)
allowed_removed = {Path(path).resolve(strict=False) for path in payload["removed_launchers"]}

def resolve_record_path(relative):
    candidate = (site_root / Path(relative.replace("/", os.sep))).resolve(strict=False)
    if os.path.commonpath((str(runtime_root), str(candidate))) != str(runtime_root):
        raise RuntimeError(f"RECORD path escaped the staged runtime: {relative}")
    return candidate

record_count = 0
verified_entry_count = 0
for record in sorted(site_root.glob("*.dist-info/RECORD")):
    with record.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream))
    updated = []
    record_relative = record.relative_to(site_root).as_posix()
    for row in rows:
        if len(row) != 3 or not row[0]:
            raise RuntimeError(f"Installed RECORD contains a malformed row: {record}")
        relative = row[0].replace("\\", "/")
        if relative == record_relative:
            updated.append([relative, "", ""])
            continue
        candidate = resolve_record_path(relative)
        if not candidate.exists():
            # A staging-bound launcher variant was intentionally removed before
            # all declared console entrypoints were regenerated for the target.
            if candidate in allowed_removed:
                continue
            raise RuntimeError(f"Installed RECORD file is unexpectedly missing: {relative}")
        content = candidate.read_bytes()
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode("ascii")
        updated.append([relative, f"sha256={digest}", str(len(content))])
    with record.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream, lineterminator="\n").writerows(updated)
    with record.open("r", encoding="utf-8", newline="") as stream:
        verified = list(csv.reader(stream))
    for relative, digest_text, size_text in verified:
        relative = relative.replace("\\", "/")
        if relative == record_relative:
            if digest_text or size_text:
                raise RuntimeError("RECORD self-entry must not carry a digest or size")
            continue
        candidate = resolve_record_path(relative)
        content = candidate.read_bytes()
        expected = "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode("ascii")
        if digest_text != expected or size_text != str(len(content)):
            raise RuntimeError(f"Installed RECORD integrity mismatch: {relative}")
        verified_entry_count += 1
    record_count += 1
print(json.dumps({"records": record_count, "verified_entries": verified_entry_count}))
'@.Replace("__PAYLOAD__", $recordPayloadBase64)
        $recordRepairBase64 = [Convert]::ToBase64String($utf8NoBom.GetBytes($recordRepairCode))
        $recordRepairBootstrap = "import base64;exec(base64.b64decode('$recordRepairBase64'))"
        $recordRepairCapture = Invoke-MSCapturedNative -Executable $runtimePython -Arguments @("-B", "-X", "utf8", "-I", "-c", $recordRepairBootstrap)
        $recordRepairText = ([string]$recordRepairCapture.stdout).Trim()
        if ($recordRepairCapture.exit_code -ne 0) {
            $recordRepairDetail = (([string]$recordRepairCapture.stderr + " " + [string]$recordRepairCapture.stdout).Trim())
            throw "Installed RECORD relocation/integrity verification failed: $recordRepairDetail"
        }
        try { $recordRepairResult = $recordRepairText | ConvertFrom-Json }
        catch { throw "Installed RECORD integrity probe returned invalid output: $recordRepairText" }
        if ([int]$recordRepairResult.records -le 0 -or [int]$recordRepairResult.verified_entries -le 0) {
            throw "Installed RECORD integrity probe verified no distributions or files."
        }

        Assert-MSNoReparsePath -Path $stagingRuntime | Out-Null
        $treeHash = Get-MSTreeSha256 -Root $stagingRuntime
        $runtimeManifest = [ordered]@{
            schema = $script:MSRuntimeSchema
            version = $version
            runtime_root = $target
            installed_utc = [DateTime]::UtcNow.ToString("o")
            python_relative_path = ".venv/Scripts/python.exe"
            package_relative_path = ".venv/Lib/site-packages/material_studio_mcp_server"
            bootstrap_python = [ordered]@{
                executable = $bootstrapPython
                version = ($pythonProbe.version -join ".")
            }
            wheel = [ordered]@{
                file_name = $expectedWheelName
                relative_path = "artifacts/$expectedWheelName"
                sha256 = $observedWheelHash
            }
            entrypoint = "material_studio_mcp_server.server:main"
            console_entrypoints = $entrypoints
            dependency_versions = [ordered]@{
                mcp = $mcpVersion.ToString()
                comtypes = [string]$windowsUiaVersions.comtypes
                pywinauto = [string]$windowsUiaVersions.pywinauto
            }
            runtime_tree_sha256 = $treeHash
            runtime_tree_excludes = @("runtime-manifest.json")
            immutability = [ordered]@{
                version_addressed = $true
                existing_runtime_never_overwritten = $true
                old_runtimes_never_deleted_by_install = $true
            }
            distribution = [ordered]@{
                channel = "windows_local_marketplace"
                repository_license_status = "declared"
                repository_license_spdx = "MIT"
                public_distribution_ready = $true
            }
        }
        $stagedManifestPath = Join-Path $stagingRuntime "runtime-manifest.json"
        Write-MSJsonAtomic -Path $stagedManifestPath -Value $runtimeManifest

        # Validate the final manifest contract and every immutable byte while the
        # version-addressed destination is still absent.
        $stagedManifest = Read-MSJson -Path $stagedManifestPath
        if ([string]$stagedManifest.schema -ne $script:MSRuntimeSchema -or [string]$stagedManifest.version -ne $version) {
            throw "Staged runtime manifest schema or version mismatch."
        }
        if (-not (Resolve-MSFullPath -Path ([string]$stagedManifest.runtime_root)).Equals($target, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Staged runtime manifest does not bind the final runtime target."
        }
        if ([string]$stagedManifest.wheel.sha256 -ne $observedWheelHash) { throw "Staged runtime manifest wheel SHA-256 mismatch." }
        if ((Get-MSTreeSha256 -Root $stagingRuntime) -ne [string]$stagedManifest.runtime_tree_sha256) {
            throw "Staged runtime tree SHA-256 mismatch."
        }
        $stagedVersionCapture = Invoke-MSCapturedNative -Executable $runtimePython -Arguments @("-B", "-X", "utf8", "-I", "-c", $versionCode)
        $stagedVersion = ([string]$stagedVersionCapture.stdout).Trim()
        if ($stagedVersionCapture.exit_code -ne 0 -or $stagedVersion -ne $version) { throw "Staged package version verification failed: $stagedVersion" }
        $stagedMcpVersion = Get-MSInstalledMcpVersion -PythonExecutable $runtimePython
        if ($stagedMcpVersion -ne $mcpVersion) { throw "Staged MCP SDK version verification failed." }
        $stagingScanPayload = [ordered]@{
            runtime_root = $stagingRuntime
            runtime_aliases = @($stagingRuntimeAliases)
        } | ConvertTo-Json -Compress
        $stagingScanPayloadBase64 = [Convert]::ToBase64String($utf8NoBom.GetBytes($stagingScanPayload))
        $stagingScanCode = @'
import base64
import json
from pathlib import Path
from urllib.parse import quote

payload = json.loads(base64.b64decode("__PAYLOAD__").decode("utf-8"))
root = Path(payload["runtime_root"]).resolve(strict=True)
aliases = {str(root)}
for raw_alias in payload["runtime_aliases"]:
    alias_path = Path(raw_alias)
    aliases.add(str(alias_path))
    aliases.add(str(alias_path.resolve(strict=True)))
markers = set()
for alias_text in aliases:
    slash_text = alias_text.replace("\\", "/")
    markers.update({
        alias_text.encode("utf-8"),
        alias_text.encode("utf-16le"),
        alias_text.encode("utf-16be"),
        alias_text.replace("\\", "\\\\").encode("utf-8"),
        slash_text.encode("utf-8"),
        quote(slash_text, safe="/:").encode("ascii"),
    })
offenders = []
files_scanned = 0
for candidate in root.rglob("*"):
    if not candidate.is_file():
        continue
    files_scanned += 1
    content = candidate.read_bytes()
    if any(marker in content for marker in markers):
        offenders.append(candidate.relative_to(root).as_posix())
if offenders:
    raise RuntimeError("staging absolute path remains in: " + ", ".join(offenders[:10]))
print(json.dumps({"files_scanned": files_scanned}))
'@.Replace("__PAYLOAD__", $stagingScanPayloadBase64)
        $stagingScanBase64 = [Convert]::ToBase64String($utf8NoBom.GetBytes($stagingScanCode))
        $stagingScanBootstrap = "import base64;exec(base64.b64decode('$stagingScanBase64'))"
        $stagingScanCapture = Invoke-MSCapturedNative -Executable $runtimePython -Arguments @("-B", "-X", "utf8", "-I", "-c", $stagingScanBootstrap)
        $stagingScanText = ([string]$stagingScanCapture.stdout).Trim()
        if ($stagingScanCapture.exit_code -ne 0) {
            $stagingScanDetail = (([string]$stagingScanCapture.stderr + " " + [string]$stagingScanCapture.stdout).Trim())
            throw "Staged runtime retained its temporary absolute path: $stagingScanDetail"
        }
        try { $stagingScanResult = $stagingScanText | ConvertFrom-Json }
        catch { throw "Staged runtime path scan returned invalid output: $stagingScanText" }
        if ([int]$stagingScanResult.files_scanned -le 0) { throw "Staged runtime path scan inspected no files." }
        Assert-MSNoReparsePath -Path $runtimesRoot | Out-Null
        Assert-MSNoReparsePath -Path $stagingRuntime | Out-Null
        Assert-MSNoReparsePath -Path $target | Out-Null

        if (Test-Path -LiteralPath $target) {
            # A concurrent installer won the publish race. Reuse only its fully
            # verified runtime and let finally remove this process's staging tree.
            $runtimeStatus = Test-MSRuntime -RuntimeRoot $target -Version $version -ExpectedWheelSha256 $observedWheelHash
            $runtimeReused = $true
        }
        else {
            # Directory.Move is an atomic same-parent rename and, unlike Move-Item
            # to an existing directory, fails instead of nesting/overwriting on a race.
            $publishedByThisAttempt = $false
            try {
                [System.IO.Directory]::Move($stagingRuntime, $target)
                $publishedByThisAttempt = $true
            }
            catch [System.IO.IOException] {
                if (-not (Test-Path -LiteralPath $target -PathType Container)) { throw }
                # A concurrent process may have atomically published after our
                # absence check. Reuse only an independently verified identical
                # runtime; never overwrite or remove the winner's directory.
                $runtimeStatus = Test-MSRuntime -RuntimeRoot $target -Version $version -ExpectedWheelSha256 $observedWheelHash
                $runtimeReused = $true
            }
            if ($publishedByThisAttempt) {
                $stagingRuntime = $null
                $stagingOwnedByThisProcess = $false
                Assert-MSNoReparsePath -Path $target | Out-Null
                $runtimeStatus = Test-MSRuntime -RuntimeRoot $target -Version $version -ExpectedWheelSha256 $observedWheelHash
                foreach ($name in ($entrypointNames | Where-Object { $_ -ne "ms-mcp" })) {
                    $publishedEntrypoint = Join-Path $target (".venv\Scripts\{0}.exe" -f $name)
                    Test-MSConsoleEntrypointHelp -Executable $publishedEntrypoint -Name $name
                }
            }
        }
    }

    $activeRuntime = [ordered]@{
        schema = $script:MSActiveRuntimeSchema
        version = $version
        runtime_root = $target
        runtime_manifest_path = $runtimeStatus.manifest_path
        runtime_manifest_sha256 = $runtimeStatus.manifest_sha256
        activated_utc = [DateTime]::UtcNow.ToString("o")
    }
    Write-MSJsonAtomic -Path $paths.active_runtime_path -Value $activeRuntime

    $managedRuntimes = @($target)
    if (Test-Path -LiteralPath $paths.install_manifest_path -PathType Leaf) {
        $previous = Read-MSJson -Path $paths.install_manifest_path
        if ([string]$previous.schema -ne $script:MSInstallManifestSchema) { throw "Existing install manifest schema mismatch." }
        $managedRuntimes = @($previous.managed_runtime_roots) + @($target)
    }
    $managedRuntimes = @($managedRuntimes | ForEach-Object { Resolve-MSFullPath -Path ([string]$_) } | Select-Object -Unique)
    foreach ($managedRuntime in $managedRuntimes) {
        if (-not (Test-MSPathWithin -Path $managedRuntime -Root $paths.runtimes_root)) { throw "Install manifest runtime escaped the managed root." }
    }
    $installManifest = [ordered]@{
        schema = $script:MSInstallManifestSchema
        updated_utc = [DateTime]::UtcNow.ToString("o")
        product_root = $paths.product_root
        managed_runtime_roots = $managedRuntimes
        managed_config_files = @($paths.settings_path, $paths.active_runtime_path, $paths.install_manifest_path)
        preserved_paths = @($settingsBinding.workspace, $paths.logs_root)
        materials_studio_installation_managed = $false
        codex_active_config_managed = $false
    }
    Write-MSJsonAtomic -Path $paths.install_manifest_path -Value $installManifest
    Write-Host "Materials Studio MCP runtime ready: $target"
    Write-Host "Runtime manifest SHA-256: $($runtimeStatus.manifest_sha256)"
    Write-Host "Runtime reused: $runtimeReused"
    Write-Host "Old runtimes and workspace data were preserved."
    Write-Host "No active Codex configuration was modified and Materials Studio was not started."
    Write-Host "Next: Test-MS-MCP.bat"
    exit 0
}
catch {
    [Console]::Error.WriteLine("Install-MS-MCP failed: $($_.Exception.Message)")
    exit 1
}
finally {
    if ($stagingOwnedByThisProcess -and $null -ne $stagingRuntime -and (Test-Path -LiteralPath $stagingRuntime)) {
        try {
            $pathsForCleanup = Get-MSProductPaths -LocalAppDataRoot $LocalAppDataRoot
            $cleanupRoot = Resolve-MSFullPath -Path $pathsForCleanup.runtimes_root
            if ((-not (Test-MSPathWithin -Path $stagingRuntime -Root $cleanupRoot)) -or
                (-not [System.IO.Path]::GetDirectoryName($stagingRuntime).Equals($cleanupRoot, [System.StringComparison]::OrdinalIgnoreCase))) {
                throw "This attempt's staging path escaped the runtimes root."
            }
            Assert-MSNoReparsePath -Path $cleanupRoot | Out-Null
            Assert-MSNoReparsePath -Path $stagingRuntime | Out-Null
            Remove-Item -LiteralPath $stagingRuntime -Recurse -Force
        }
        catch {
            [Console]::Error.WriteLine("Warning: failed to remove this attempt's incomplete runtime: $($_.Exception.Message)")
        }
    }
}
