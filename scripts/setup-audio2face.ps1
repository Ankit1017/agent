<#
.SYNOPSIS
Validates, builds, and installs the local NVIDIA Audio2Face bridge.

.DESCRIPTION
Uses the repository-owned Audio2Face SDK checkout at the pinned commit. Downloads and
model conversion happen only after explicit SDK and model-license acceptance. CUDA and
TensorRT must be installed separately because their installers and account terms are
owned by NVIDIA. No setup work occurs during Harness startup or a browser request.
#>
[CmdletBinding()]
param(
    [switch]$AcceptNvidiaSdkLicense,
    [switch]$AcceptModelLicenses,
    [switch]$ValidateOnly,
    [string]$SdkRoot = "",
    [string]$CudaRoot = $env:CUDA_PATH,
    [string]$TensorRtRoot = $env:TENSORRT_ROOT_DIR
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot = (Split-Path -Parent $PSScriptRoot)
$expectedCommit = "1ca0f02535ed774f5dbcd724a31cd486368dc783"

function Resolve-ConfiguredRoot {
    param([string]$Value, [string]$Name)
    if ($Value) { return $Value }
    $userValue = [Environment]::GetEnvironmentVariable($Name, "User")
    if ($userValue) { return $userValue }
    return [Environment]::GetEnvironmentVariable($Name, "Machine")
}

$CudaRoot = Resolve-ConfiguredRoot -Value $CudaRoot -Name "CUDA_PATH"
$TensorRtRoot = Resolve-ConfiguredRoot -Value $TensorRtRoot -Name "TENSORRT_ROOT_DIR"
$sdkCandidate = if ($SdkRoot) { $SdkRoot } else { Join-Path $projectRoot "Audio2Face-3D-SDK" }
$sdk = [System.IO.Path]::GetFullPath($sdkCandidate)
$projectPrefix = [System.IO.Path]::GetFullPath($projectRoot).TrimEnd('\') + '\'
if (-not $sdk.StartsWith($projectPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Audio2Face SDK must be located inside the Harness control workspace."
}

Write-Host "NVIDIA Audio2Face local integration" -ForegroundColor Cyan
Write-Host "SDK source: MIT license (Copyright NVIDIA Corporation)."
Write-Host "Audio2Face model artifacts have separate NVIDIA/Hugging Face terms."
Write-Host "CUDA and TensorRT are governed by NVIDIA's respective license terms."

$checks = [ordered]@{}
$checks["SDK checkout"] = Test-Path -LiteralPath (Join-Path $sdk "CMakeLists.txt") -PathType Leaf
$actualCommit = if ($checks["SDK checkout"]) {
    (& git -C $sdk rev-parse HEAD 2>$null).Trim()
} else { "" }
$checks["Pinned SDK commit"] = $actualCommit -eq $expectedCommit
$checks["Visual C++ tools"] = $false
$vswhere = "C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"
if (Test-Path -LiteralPath $vswhere -PathType Leaf) {
    $vsPath = (& $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath).Trim()
    $checks["Visual C++ tools"] = [bool]$vsPath -and (
        Test-Path -LiteralPath (
            Join-Path $vsPath "Common7\Tools\Microsoft.VisualStudio.DevShell.dll"
        ) -PathType Leaf
    )
}
$checks["Python 3.10"] = $false
try {
    $python310 = (& py -3.10 -c "import sys; print(sys.executable)" 2>$null).Trim()
    $checks["Python 3.10"] = [bool]$python310
} catch { $python310 = "" }
$cudaVersion = "unknown"
$cudaReady = $false
if ($CudaRoot) {
    $nvcc = Join-Path $CudaRoot "bin\nvcc.exe"
    if (Test-Path -LiteralPath $nvcc -PathType Leaf) {
        $nvccOutput = (& $nvcc --version 2>$null) -join "`n"
        $cudaMatch = [regex]::Match($nvccOutput, "release\s+(\d+)\.(\d+)")
        if ($cudaMatch.Success) {
            $cudaMajor = [int]$cudaMatch.Groups[1].Value
            $cudaMinor = [int]$cudaMatch.Groups[2].Value
            $cudaVersion = "$cudaMajor.$cudaMinor"
            $cudaReady = $cudaMajor -eq 12 -and $cudaMinor -ge 8
        }
    }
}
$checks["CUDA Toolkit $cudaVersion"] = $cudaReady

$tensorRtVersion = "unknown"
$tensorRtReady = $false
if ($TensorRtRoot) {
    $tensorRtHeader = Join-Path $TensorRtRoot "include\NvInferVersion.h"
    $tensorRtLibraries = @(
        (Join-Path $TensorRtRoot "lib\nvinfer.lib"),
        (Join-Path $TensorRtRoot "lib\nvinfer_10.lib")
    )
    $tensorRtExecutableReady = Test-Path -LiteralPath (
        Join-Path $TensorRtRoot "bin\trtexec.exe"
    ) -PathType Leaf
    $tensorRtLibraryReady = [bool]($tensorRtLibraries | Where-Object {
        Test-Path -LiteralPath $_ -PathType Leaf
    } | Select-Object -First 1)
    if (Test-Path -LiteralPath $tensorRtHeader -PathType Leaf) {
        $tensorRtHeaderText = Get-Content -LiteralPath $tensorRtHeader -Raw
        $majorMatch = [regex]::Match(
            $tensorRtHeaderText,
            "#define\s+(?:NV_TENSORRT_MAJOR|TRT_MAJOR_ENTERPRISE)\s+(\d+)"
        )
        $minorMatch = [regex]::Match(
            $tensorRtHeaderText,
            "#define\s+(?:NV_TENSORRT_MINOR|TRT_MINOR_ENTERPRISE)\s+(\d+)"
        )
        $patchMatch = [regex]::Match(
            $tensorRtHeaderText,
            "#define\s+(?:NV_TENSORRT_PATCH|TRT_PATCH_ENTERPRISE)\s+(\d+)"
        )
        if ($majorMatch.Success -and $minorMatch.Success) {
            $tensorRtMajor = [int]$majorMatch.Groups[1].Value
            $tensorRtMinor = [int]$minorMatch.Groups[1].Value
            $tensorRtPatch = if ($patchMatch.Success) { [int]$patchMatch.Groups[1].Value } else { 0 }
            $tensorRtVersion = "$tensorRtMajor.$tensorRtMinor.$tensorRtPatch"
            $tensorRtReady = (
                $tensorRtLibraryReady -and
                $tensorRtExecutableReady -and
                $tensorRtMajor -eq 10 -and
                $tensorRtMinor -ge 13
            )
        }
    }
}
$checks["TensorRT $tensorRtVersion"] = $tensorRtReady
$checks["Git LFS"] = $false
try { $checks["Git LFS"] = (& git lfs version 2>$null) -match "git-lfs" } catch {}

$checks.GetEnumerator() | ForEach-Object {
    $mark = if ($_.Value) { "OK" } else { "MISSING" }
    $color = if ($_.Value) { "Green" } else { "Yellow" }
    Write-Host ("{0,-28} {1}" -f $_.Key, $mark) -ForegroundColor $color
}

if ($ValidateOnly) {
    if ($checks.Values -contains $false) { exit 2 }
    Write-Host "Native prerequisites are ready." -ForegroundColor Green
    exit 0
}
if (-not $AcceptNvidiaSdkLicense -or -not $AcceptModelLicenses) {
    throw "Re-run with -AcceptNvidiaSdkLicense and -AcceptModelLicenses after reviewing the printed terms."
}
if ($checks.Values -contains $false) {
    throw "Install the missing prerequisites, set CUDA_PATH and TENSORRT_ROOT_DIR, then re-run this script."
}

$toolRoot = Join-Path $projectRoot ".harness\tools\audio2face"
$modelRoot = Join-Path $projectRoot ".harness\models\audio2face"
$buildRoot = Join-Path $projectRoot ".harness\build\audio2face"
$venv = Join-Path $toolRoot "python310"
$python = Join-Path $venv "Scripts\python.exe"
$hf = Join-Path $venv "Scripts\hf.exe"

Write-Host "Pulling pinned SDK dependencies and LFS sample data..." -ForegroundColor Cyan
& git -C $sdk lfs pull
if ($LASTEXITCODE -ne 0) { throw "Git LFS pull failed." }
& cmd.exe /d /c (Join-Path $sdk "fetch_deps.bat") release
if ($LASTEXITCODE -ne 0) { throw "Audio2Face dependency fetch failed." }

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    & py -3.10 -m venv $venv
}
& $python -m pip install --disable-pip-version-check -r (Join-Path $sdk "deps\requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Audio2Face Python dependency installation failed." }
& $hf auth whoami *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Hugging Face is not authenticated. Run '$hf auth login' after accepting NVIDIA model access, then re-run setup."
}

Write-Host "Downloading the license-protected Audio2Face Mark model..." -ForegroundColor Cyan
$oldPath = $env:PATH
$oldCuda = $env:CUDA_PATH
$oldTensorRt = $env:TENSORRT_ROOT_DIR
try {
    $conversionPaths = @(
        (Join-Path $venv "Scripts"),
        (Join-Path $TensorRtRoot "bin"),
        (Join-Path $TensorRtRoot "lib"),
        (Join-Path $CudaRoot "bin"),
        $oldPath
    )
    $env:PATH = ($conversionPaths | Where-Object { $_ }) -join ";"
    $env:CUDA_PATH = $CudaRoot
    $env:TENSORRT_ROOT_DIR = $TensorRtRoot
    $downloadedMark = Join-Path $sdk "_data\audio2face-models\audio2face-3d-v2.3-mark"
    & $hf download nvidia/Audio2Face-3D-v2.3-Mark --local-dir $downloadedMark
    if ($LASTEXITCODE -ne 0) { throw "Audio2Face Mark model download failed." }
    $generatedModel = Join-Path $sdk "_data\generated\audio2face-sdk\samples\data\mark"
    $generatedEngine = Join-Path $generatedModel "network.trt"
    if (
        (Test-Path -LiteralPath (Join-Path $generatedModel "model.json") -PathType Leaf) -and
        (Test-Path -LiteralPath $generatedEngine -PathType Leaf)
    ) {
        Write-Host "Reusing the existing verified Mark TensorRT model." -ForegroundColor Green
    } else {
        $oldPythonPath = $env:PYTHONPATH
        try {
            $env:PYTHONPATH = (Join-Path $sdk "audio2x-common\scripts") + ";" + (Join-Path $sdk "audio2face-sdk\scripts")
            Push-Location $sdk
            & $python -c "from gen_sample_data import regression_gen_data; regression_gen_data(source_model='audio2face-3d-v2.3-mark', custom_folder_name='mark')"
            if ($LASTEXITCODE -ne 0) { throw "Audio2Face Mark model conversion failed." }
        } finally {
            Pop-Location
            $env:PYTHONPATH = $oldPythonPath
        }
    }

    $cmake = Join-Path $sdk "_deps\build-deps\cmake\bin\cmake.exe"
    $ninja = Join-Path $sdk "_deps\build-deps\ninja\ninja.exe"
    if (-not (Test-Path -LiteralPath $cmake) -or -not (Test-Path -LiteralPath $ninja)) {
        throw "The pinned CMake/Ninja dependency bundle is incomplete."
    }
    $developerShell = Join-Path $vsPath "Common7\Tools\Microsoft.VisualStudio.DevShell.dll"
    Import-Module $developerShell
    Enter-VsDevShell -VsInstallPath $vsPath -SkipAutomaticLocation `
        -DevCmdArguments "-arch=x64 -host_arch=x64" | Out-Null
    & $cmake --fresh -S (Join-Path $projectRoot "native\audio2face_bridge") -B $buildRoot -G Ninja `
        "-DAUDIO2FACE_SDK_ROOT=$sdk" "-DCMAKE_BUILD_TYPE=Release" "-DCMAKE_MAKE_PROGRAM=$ninja" `
        "-DTENSORRT_ROOT_DIR=$TensorRtRoot" "-DCUDAToolkit_ROOT=$CudaRoot"
    if ($LASTEXITCODE -ne 0) { throw "Audio2Face bridge configuration failed." }
    & $cmake --build $buildRoot --target audio2face-bridge --config Release --parallel
    if ($LASTEXITCODE -ne 0) { throw "Audio2Face bridge build failed." }
} finally {
    $env:PATH = $oldPath
    $env:CUDA_PATH = $oldCuda
    $env:TENSORRT_ROOT_DIR = $oldTensorRt
}

$bridge = Join-Path $buildRoot "bin\audio2face-bridge.exe"
$audio2x = Get-ChildItem -LiteralPath $buildRoot -Filter "audio2x.dll" -File -Recurse | Select-Object -First 1
if (-not (Test-Path -LiteralPath $bridge -PathType Leaf) -or -not $audio2x -or -not (Test-Path -LiteralPath (Join-Path $generatedModel "model.json"))) {
    throw "Built bridge or generated Mark model is missing."
}

$installBin = Join-Path $toolRoot "bin"
$installModel = Join-Path $modelRoot "mark"
New-Item -ItemType Directory -Force -Path $installBin, $installModel | Out-Null
Copy-Item -LiteralPath $bridge -Destination (Join-Path $installBin "audio2face-bridge.exe") -Force
Copy-Item -LiteralPath $audio2x.FullName -Destination (Join-Path $installBin "audio2x.dll") -Force
Copy-Item -Path (Join-Path $generatedModel "*") -Destination $installModel -Recurse -Force

Write-Host "Audio2Face bridge and Mark model installed." -ForegroundColor Green
Write-Host "Set HARNESS_AUDIO2FACE_ENABLED=true and restart the browser server."
