<#
.SYNOPSIS
Converts and installs one rights-confirmed Character Creator FBX archive.

.DESCRIPTION
The archive is never executed. One exact FBX entry is extracted into a
request-unique protected directory, converted by the pinned local Blender,
compacted, validated, installed under an exact avatar ID, and then cleaned up.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ArchivePath,
    [string]$FbxName = "Amber.Fbx",
    [ValidatePattern('^[a-z0-9][a-z0-9-]{0,31}$')]
    [string]$AvatarId = "amber",
    [ValidateLength(1, 80)]
    [string]$DisplayName = "Amber Presenter",
    [switch]$ConfirmAssetRights
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$blender = Join-Path $projectRoot ".harness\tools\studio\blender\blender.exe"
$unrar = "C:\Program Files\WinRAR\UnRAR.exe"
$converter = Join-Path $PSScriptRoot "convert-audio2face-cc3-fbx.py"
$compactor = Join-Path $PSScriptRoot "compact-glb-morphs.py"
$installer = Join-Path $PSScriptRoot "setup-audio2face-avatar.ps1"
$runtimeRoot = Join-Path $projectRoot ".harness\runtime\avatar-import"

Write-Host "Character Creator to Audio2Face avatar installation" -ForegroundColor Cyan
Write-Host "The Harness does not grant rights to the character, clothing, hair, or textures."
if (-not $ConfirmAssetRights) {
    throw "Review the asset terms, then rerun with -ConfirmAssetRights."
}
foreach ($required in @($python, $blender, $unrar, $converter, $compactor, $installer)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "A required local conversion component is missing. Complete Harness and Studio setup first."
    }
}
$archive = [IO.Path]::GetFullPath($ArchivePath)
if (-not (Test-Path -LiteralPath $archive -PathType Leaf)) {
    throw "The selected character archive does not exist."
}
if ([IO.Path]::GetFileName($FbxName) -ne $FbxName -or [IO.Path]::GetExtension($FbxName) -ine ".fbx") {
    throw "FbxName must be one plain FBX filename from the archive root."
}

New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
$requestRoot = Join-Path $runtimeRoot ([guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $requestRoot | Out-Null
$resolvedRuntime = [IO.Path]::GetFullPath($runtimeRoot) + [IO.Path]::DirectorySeparatorChar
$resolvedRequest = [IO.Path]::GetFullPath($requestRoot)
if (-not $resolvedRequest.StartsWith($resolvedRuntime, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Avatar conversion directory escaped the protected runtime root."
}

try {
    & $unrar e -o- -idq -p- -- $archive $FbxName ($resolvedRequest + "\")
    if ($LASTEXITCODE -ne 0) {
        throw "The exact FBX entry could not be extracted from the archive."
    }
    $source = Join-Path $resolvedRequest $FbxName
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "The requested FBX entry was not present in the archive."
    }
    $dense = Join-Path $resolvedRequest "converted-dense.glb"
    $compact = Join-Path $resolvedRequest "converted.glb"
    & $blender --background --factory-startup --disable-autoexec `
        --python $converter -- $source $dense
    if ($LASTEXITCODE -ne 0) {
        throw "Blender could not convert the Character Creator FBX."
    }
    & $python $compactor $dense $compact
    if ($LASTEXITCODE -ne 0) {
        throw "The converted facial morphs could not be compacted."
    }
    & $installer -AssetPath $compact -AvatarId $AvatarId `
        -DisplayName $DisplayName -ConfirmAssetRights
    if ($LASTEXITCODE -ne 0) {
        throw "The converted avatar did not pass protected installation."
    }
} finally {
    $checked = [IO.Path]::GetFullPath($requestRoot)
    if ($checked.StartsWith($resolvedRuntime, [StringComparison]::OrdinalIgnoreCase) -and
        (Test-Path -LiteralPath $checked)) {
        Remove-Item -LiteralPath $checked -Recurse -Force
    }
}
