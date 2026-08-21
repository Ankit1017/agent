<#
.SYNOPSIS
Starts or restarts the complete local harness stack.

.DESCRIPTION
Delegates to the repository's canonical local-ai controller so Docker Desktop,
Ollama, LiteLLM, SearXNG, and the host browser server use the same guarded
startup path. The script can be launched from any working directory.

.PARAMETER Restart
Stops the managed services first and then starts them again.
#>
[CmdletBinding()]
param(
    [switch]$Restart
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot = Split-Path -Parent $PSScriptRoot
$controller = Join-Path $projectRoot "local-ai/local-ai.ps1"
if (-not (Test-Path -LiteralPath $controller -PathType Leaf)) {
    throw "The local service controller is missing. Run this script from a complete harness checkout."
}

$action = if ($Restart) { "Restart" } else { "Start" }
Write-Host "$action all local harness services..." -ForegroundColor Cyan
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $controller $action
if ($LASTEXITCODE -ne 0) {
    throw "The local harness service controller failed during $action."
}

Write-Host ""
Write-Host "Verifying service status..." -ForegroundColor Cyan
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $controller Status
if ($LASTEXITCODE -ne 0) {
    throw "The local harness service status check failed."
}

Write-Host ""
Write-Host "Ready: http://127.0.0.1:3000" -ForegroundColor Green
