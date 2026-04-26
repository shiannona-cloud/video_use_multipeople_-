param(
    [string]$OutputDir = "dist",
    [string]$PackagePrefix = "video-use-distributable"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$stagingDir = Join-Path $root "$OutputDir\_staging-$timestamp"
$zipName = "$PackagePrefix-$timestamp.zip"
$zipPath = Join-Path $root "$OutputDir\$zipName"

$excludePatterns = @(
    ".env",
    "dist",
    "video_use.egg-info",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".git",
    ".DS_Store"
)

function Should-Exclude([string]$relativePath) {
    $normalized = $relativePath -replace "\\", "/"
    foreach ($pattern in $excludePatterns) {
        if ($normalized -eq $pattern) { return $true }
        if ($normalized.StartsWith("$pattern/")) { return $true }
        if ($normalized -like "*/$pattern/*") { return $true }
        if ($normalized -like "*/$pattern") { return $true }
    }
    return $false
}

New-Item -ItemType Directory -Force -Path (Join-Path $root $OutputDir) | Out-Null
if (Test-Path $stagingDir) {
    Remove-Item -Recurse -Force $stagingDir
}
New-Item -ItemType Directory -Path $stagingDir | Out-Null

Get-ChildItem -Path $root -Recurse -Force | ForEach-Object {
    $full = $_.FullName
    if ($full -eq $stagingDir) { return }
    $relative = Resolve-Path -Relative -Path $full
    $relative = $relative.TrimStart(".\")

    if ([string]::IsNullOrWhiteSpace($relative)) { return }
    if (Should-Exclude $relative) { return }

    $target = Join-Path $stagingDir $relative
    if ($_.PSIsContainer) {
        New-Item -ItemType Directory -Force -Path $target | Out-Null
    } else {
        $parent = Split-Path -Parent $target
        if (-not (Test-Path $parent)) {
            New-Item -ItemType Directory -Force -Path $parent | Out-Null
        }
        Copy-Item -Path $full -Destination $target -Force
    }
}

if (Test-Path $zipPath) {
    Remove-Item -Force $zipPath
}
Compress-Archive -Path (Join-Path $stagingDir "*") -DestinationPath $zipPath -CompressionLevel Optimal
Remove-Item -Recurse -Force $stagingDir

Write-Host "Created distributable package:"
Write-Host $zipPath
