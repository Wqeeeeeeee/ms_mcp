[CmdletBinding()]
param(
    [string]$LocalAppDataRoot,
    [Alias("dry-run")][switch]$DryRun,
    [switch]$Confirm,
    [switch]$NonInteractive
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "WindowsInstaller.Common.ps1")

try {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw "Uninstall-MS-MCP is supported only on Windows."
    }
    $paths = Get-MSProductPaths -LocalAppDataRoot $LocalAppDataRoot
    Assert-MSNoReparsePath -Path $paths.product_root | Out-Null
    $manifest = Read-MSJson -Path $paths.install_manifest_path
    if ([string]$manifest.schema -ne $script:MSInstallManifestSchema) { throw "Install manifest schema mismatch; refusing unmanaged deletion." }
    if (-not (Resolve-MSFullPath -Path ([string]$manifest.product_root)).Equals($paths.product_root, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Install manifest product-root binding mismatch."
    }

    $activeRuntime = $null
    $activeRuntimeRoot = $null
    if (Test-Path -LiteralPath $paths.active_runtime_path -PathType Leaf) {
        $activeRuntime = Read-MSJson -Path $paths.active_runtime_path
        if ([string]$activeRuntime.schema -ne $script:MSActiveRuntimeSchema) {
            throw "Active runtime pointer schema mismatch; refusing managed deletion."
        }
        $activeRuntimeRoot = Resolve-MSFullPath -Path ([string]$activeRuntime.runtime_root)
        if ((-not (Test-MSPathWithin -Path $activeRuntimeRoot -Root $paths.runtimes_root)) -or
            (-not [System.IO.Path]::GetDirectoryName($activeRuntimeRoot).Equals($paths.runtimes_root, [System.StringComparison]::OrdinalIgnoreCase))) {
            throw "Active runtime pointer escaped the versioned runtimes root."
        }
    }

    $allowedConfig = @($paths.settings_path, $paths.active_runtime_path, $paths.install_manifest_path)
    $managedConfig = @($manifest.managed_config_files | ForEach-Object { Resolve-MSFullPath -Path ([string]$_) } | Select-Object -Unique)
    foreach ($configPath in $managedConfig) {
        if (-not ($allowedConfig | Where-Object { $_.Equals($configPath, [System.StringComparison]::OrdinalIgnoreCase) })) {
            throw "Install manifest contains an unrecognized config path: $configPath"
        }
        Assert-MSNoReparsePath -Path $configPath | Out-Null
    }

    $managedRuntimes = @($manifest.managed_runtime_roots | ForEach-Object { Resolve-MSFullPath -Path ([string]$_) } | Select-Object -Unique)
    $validatedRuntimeIdentities = @{}
    foreach ($runtime in $managedRuntimes) {
        if ((-not (Test-MSPathWithin -Path $runtime -Root $paths.runtimes_root)) -or
            (-not [System.IO.Path]::GetDirectoryName($runtime).Equals($paths.runtimes_root, [System.StringComparison]::OrdinalIgnoreCase))) {
            throw "Managed runtime escaped the runtimes root or is not a direct version child: $runtime"
        }
        Assert-MSNoReparsePath -Path $runtime | Out-Null
        if (Test-Path -LiteralPath $runtime -PathType Container) {
            $runtimeManifestPath = Join-Path $runtime "runtime-manifest.json"
            $runtimeManifest = Read-MSJson -Path $runtimeManifestPath
            if ([string]$runtimeManifest.schema -ne $script:MSRuntimeSchema) { throw "Refusing to delete a runtime with an unknown manifest: $runtime" }
            if (-not (Resolve-MSFullPath -Path ([string]$runtimeManifest.runtime_root)).Equals($runtime, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Runtime manifest path binding mismatch: $runtime" }
            $runtimeVersion = [string]$runtimeManifest.version
            if ([string]::IsNullOrWhiteSpace($runtimeVersion)) { throw "Runtime manifest version is missing: $runtime" }

            $expectedManifestSha256 = $null
            if ($null -ne $activeRuntimeRoot -and $runtime.Equals($activeRuntimeRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
                if ([string]$activeRuntime.version -ne $runtimeVersion) { throw "Active runtime version binding mismatch: $runtime" }
                $activeManifestPath = Resolve-MSFullPath -Path ([string]$activeRuntime.runtime_manifest_path)
                if (-not $activeManifestPath.Equals($runtimeManifestPath, [System.StringComparison]::OrdinalIgnoreCase)) {
                    throw "Active runtime manifest path binding mismatch: $runtime"
                }
                $expectedManifestSha256 = ([string]$activeRuntime.runtime_manifest_sha256).ToLowerInvariant()
                if ($expectedManifestSha256 -notmatch '^[0-9a-f]{64}$') {
                    throw "Active runtime manifest SHA-256 is missing or invalid: $runtime"
                }
            }

            # Deletion authorization requires the same complete runtime validation
            # used by install/launch. A manifest-shaped directory is not sufficient.
            $runtimeStatus = Test-MSRuntime -RuntimeRoot $runtime -Version $runtimeVersion -ExpectedManifestSha256 $expectedManifestSha256
            $validatedRuntimeIdentities[$runtime] = [ordered]@{
                version = $runtimeVersion
                manifest_sha256 = $runtimeStatus.manifest_sha256
            }
        }
    }
    $preserved = @($manifest.preserved_paths | ForEach-Object { Resolve-MSFullPath -Path ([string]$_) } | Select-Object -Unique)
    foreach ($preservedPath in $preserved) {
        foreach ($runtime in $managedRuntimes) {
            if ((Test-MSPathWithin -Path $preservedPath -Root $runtime -AllowRoot) -or (Test-MSPathWithin -Path $runtime -Root $preservedPath -AllowRoot)) {
                throw "A preserved workspace/log path overlaps a managed runtime; refusing deletion."
            }
        }
    }

    Write-Host "Managed runtime directories to remove:"
    foreach ($runtime in $managedRuntimes) { Write-Host "  $runtime" }
    Write-Host "Managed plugin configuration files to remove:"
    foreach ($configPath in $managedConfig) { Write-Host "  $configPath" }
    Write-Host "Paths explicitly preserved:"
    foreach ($preservedPath in $preserved) { Write-Host "  $preservedPath" }
    Write-Host "Materials Studio and unrelated Codex configuration are never removed."

    if ($DryRun) {
        Write-Host "DRY RUN: nothing was removed."
        exit 0
    }
    if (-not $Confirm) {
        if ($NonInteractive) { throw "Noninteractive uninstall requires -Confirm. Run with --dry-run first." }
        $answer = Read-Host "Type REMOVE-MANAGED-RUNTIMES to continue"
        if ($answer -cne "REMOVE-MANAGED-RUNTIMES") { throw "Uninstall was not confirmed." }
    }

    foreach ($runtime in $managedRuntimes) {
        if (Test-Path -LiteralPath $runtime -PathType Container) {
            if ((-not (Test-MSPathWithin -Path $runtime -Root $paths.runtimes_root)) -or
                (-not [System.IO.Path]::GetDirectoryName($runtime).Equals($paths.runtimes_root, [System.StringComparison]::OrdinalIgnoreCase))) {
                throw "Runtime deletion scope changed unexpectedly."
            }
            Assert-MSNoReparsePath -Path $runtime | Out-Null
            $validatedIdentity = $validatedRuntimeIdentities[$runtime]
            if ($null -eq $validatedIdentity) { throw "Runtime was not validated for managed deletion: $runtime" }
            # Revalidate after the interactive confirmation window so a swapped or
            # modified runtime cannot cross the earlier authorization boundary.
            Test-MSRuntime -RuntimeRoot $runtime -Version ([string]$validatedIdentity.version) -ExpectedManifestSha256 ([string]$validatedIdentity.manifest_sha256) | Out-Null
            Remove-Item -LiteralPath $runtime -Recurse -Force
        }
    }
    foreach ($configPath in ($managedConfig | Where-Object { -not $_.Equals($paths.install_manifest_path, [System.StringComparison]::OrdinalIgnoreCase) })) {
        if (Test-Path -LiteralPath $configPath -PathType Leaf) { Remove-Item -LiteralPath $configPath -Force }
    }
    if (Test-Path -LiteralPath $paths.install_manifest_path -PathType Leaf) { Remove-Item -LiteralPath $paths.install_manifest_path -Force }

    Write-Host "Managed runtime and plugin configuration removed."
    Write-Host "Workspace, revisions, models, calculation results, and logs were preserved."
    Write-Host "To restore: run Configure-MS-MCP.bat, Install-MS-MCP.bat, and Test-MS-MCP.bat."
    exit 0
}
catch {
    [Console]::Error.WriteLine("Uninstall-MS-MCP failed: $($_.Exception.Message)")
    exit 1
}
