[CmdletBinding()]
param(
    [string]$PythonCommand,
    [string]$Runner,
    [string]$Workspace,
    [ValidateSet("2020", "20.1")][string]$MaterialsStudioVersion,
    [string]$LocalAppDataRoot,
    [switch]$NonInteractive,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "WindowsInstaller.Common.ps1")

try {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw "Configure-MS-MCP is supported only on Windows."
    }
    $releaseRoot = Get-MSReleaseRoot
    $version = Get-MSPackageVersion -ReleaseRoot $releaseRoot
    $paths = Get-MSProductPaths -LocalAppDataRoot $LocalAppDataRoot
    Assert-MSNoReparsePath -Path $paths.local_app_data_root | Out-Null

    $python = Resolve-MSPython -PythonCommand $PythonCommand
    $pythonProbe = Get-MSPythonProbe -PythonExecutable $python

    $resolvedRunner = Resolve-MSRunner -ExplicitRunner $Runner
    if ($null -eq $resolvedRunner -and -not $NonInteractive) {
        $answer = Read-Host "RunMatScript.bat was not detected. Enter its full path"
        $resolvedRunner = Resolve-MSRunner -ExplicitRunner $answer
    }
    if ($null -eq $resolvedRunner) {
        throw "RunMatScript.bat for Materials Studio 2020/20.1 was not found. Rerun with -Runner <full-path>."
    }
    $detectedVersion = Get-MSMaterialsStudioVersion -Runner $resolvedRunner -ExplicitVersion $MaterialsStudioVersion
    if ($null -eq $detectedVersion -and -not $NonInteractive) {
        $detectedVersion = Read-Host "Confirm installed Materials Studio version (2020 or 20.1)"
        $detectedVersion = Get-MSMaterialsStudioVersion -Runner $resolvedRunner -ExplicitVersion $detectedVersion
    }
    if ($null -eq $detectedVersion) {
        throw "The runner path did not identify Materials Studio 2020/20.1. Pass -MaterialsStudioVersion 2020 or 20.1."
    }

    $workspacePath = $Workspace
    if ([string]::IsNullOrWhiteSpace($workspacePath)) {
        $workspacePath = $paths.workspace_root
        if (-not $NonInteractive) {
            $answer = Read-Host "Workspace path [$workspacePath]"
            if (-not [string]::IsNullOrWhiteSpace($answer)) { $workspacePath = $answer }
        }
    }
    $workspacePath = Resolve-MSFullPath -Path $workspacePath
    Assert-MSNoReparsePath -Path $workspacePath | Out-Null
    if ((Test-MSPathsOverlap -Left $workspacePath -Right $paths.runtimes_root) -or
        (Test-MSPathsOverlap -Left $workspacePath -Right $paths.config_root) -or
        (Test-MSPathsOverlap -Left $workspacePath -Right $paths.logs_root) -or
        (Test-MSPathsOverlap -Left $workspacePath -Right $releaseRoot)) {
        throw "Workspace must not overlap managed runtimes, config, or logs."
    }

    if ((Test-Path -LiteralPath $paths.settings_path -PathType Leaf) -and -not $Force -and $NonInteractive) {
        $existing = Read-MSJson -Path $paths.settings_path
        if ([string]$existing.schema -ne $script:MSConfigSchema -or [string]$existing.package_version -ne $version -or
            -not ([string]$existing.materials_studio.runner).Equals($resolvedRunner, [System.StringComparison]::OrdinalIgnoreCase) -or
            -not ([string]$existing.workspace).Equals($workspacePath, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Configuration already exists with different values. Review it and rerun with -Force to replace only the plugin settings file."
        }
    }

    foreach ($directory in @($paths.product_root, $paths.config_root, $paths.logs_root, $workspacePath, $paths.runtimes_root)) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
        Assert-MSNoReparsePath -Path $directory | Out-Null
    }

    $codex = Get-Command -Name "codex" -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    $settings = [ordered]@{
        schema = $script:MSConfigSchema
        package_version = $version
        distribution = [ordered]@{
            channel = "windows_local_marketplace"
            public_distribution_ready = $true
            repository_license_status = "declared"
            repository_license_spdx = "MIT"
        }
        configured_utc = [DateTime]::UtcNow.ToString("o")
        windows = [ordered]@{
            version = [Environment]::OSVersion.Version.ToString()
            platform = [Environment]::OSVersion.Platform.ToString()
        }
        python = [ordered]@{
            executable = $python
            version = ($pythonProbe.version -join ".")
        }
        materials_studio = [ordered]@{
            version = $detectedVersion
            runner = $resolvedRunner
        }
        workspace = $workspacePath
        logs = $paths.logs_root
        codex_cli = [ordered]@{
            detected = ($null -ne $codex)
            executable = $(if ($null -ne $codex) { $codex.Source } else { $null })
            required_to_start_server = $false
        }
        safety = [ordered]@{
            active_codex_config_modified = $false
            materials_studio_started = $false
            calculation_started = $false
        }
    }
    Write-MSJsonAtomic -Path $paths.settings_path -Value $settings

    Write-Host "Materials Studio MCP configuration written: $($paths.settings_path)"
    Write-Host "Runner: $resolvedRunner"
    Write-Host "Workspace: $workspacePath"
    Write-Host "Codex CLI detected: $($null -ne $codex) (not required to start the MCP server)"
    Write-Host "No active Codex configuration was modified."
    Write-Host "Next: Install-MS-MCP.bat"
    exit 0
}
catch {
    [Console]::Error.WriteLine("Configure-MS-MCP failed: $($_.Exception.Message)")
    exit 1
}
