<#
.SYNOPSIS
Validates and installs one user-supplied Audio2Face-compatible GLB avatar.

.DESCRIPTION
The script never downloads an avatar. The operator must confirm that they own the
asset or have permission to use it. The installed copy is validated, bounded, and
stored under the protected Harness model directory.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$AssetPath,
    [switch]$ConfirmAssetRights,
    [string]$DisplayName = "",
    [ValidatePattern('^[a-z0-9][a-z0-9-]{0,31}$')]
    [string]$AvatarId = "default"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$installer = Join-Path $PSScriptRoot "install-audio2face-avatar.py"
$avatarRoot = Join-Path $projectRoot ".harness\models\audio2face"
$destination = if ($AvatarId -eq "default") {
    Join-Path $avatarRoot "avatar"
} else {
    Join-Path (Join-Path $avatarRoot "avatars") $AvatarId
}
$maximum = 52428800

Write-Host "Local Audio2Face 3D avatar installation" -ForegroundColor Cyan
Write-Host "The Harness does not grant rights to the selected character or its textures."
Write-Host "Install only an asset you own or have permission to use and redistribute locally."
if (-not $ConfirmAssetRights) {
    throw "Review the asset terms, then rerun with -ConfirmAssetRights."
}
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Harness Python is missing. Run scripts/setup.ps1 first."
}
$resolvedAsset = [IO.Path]::GetFullPath($AssetPath)
if (-not (Test-Path -LiteralPath $resolvedAsset -PathType Leaf)) {
    throw "The selected avatar file does not exist."
}
if ([IO.Path]::GetExtension($resolvedAsset) -ine ".glb") {
    throw "The selected avatar must be a binary .glb file."
}
if (-not $DisplayName) {
    $DisplayName = [IO.Path]::GetFileNameWithoutExtension($resolvedAsset)
}

& $python $installer --asset $resolvedAsset --destination $destination `
    --max-bytes $maximum --name $DisplayName
if ($LASTEXITCODE -ne 0) {
    throw "The avatar did not pass the protected GLB validation."
}
Write-Host "Audio2Face 3D avatar installed and verified." -ForegroundColor Green
Write-Host "Avatar ID: $AvatarId"
Write-Host "Restart the browser server, then open /speech."
