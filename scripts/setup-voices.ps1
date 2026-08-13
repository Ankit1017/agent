param(
    [switch]$AcceptNonCommercialVoiceLicenses
)

$ErrorActionPreference = "Stop"

Write-Host "Piper engine: GPL-3.0. The engine may impose source-distribution obligations."
Write-Host "English Lessac: review its model card before redistribution."
Write-Host "Hindi Priyamvada: CC BY-NC-SA 4.0 dataset; noncommercial prototype use only."
Write-Host "Hindi Rohan: separate IITM IndicTTS dataset terms; prototype use only."
Write-Host "Commercial use or redistribution requires replacement/relicensing and legal review."

if (-not $AcceptNonCommercialVoiceLicenses) {
    throw "Re-run with -AcceptNonCommercialVoiceLicenses after reviewing these terms."
}

$workspaceRoot = [System.IO.Path]::GetFullPath((Split-Path $PSScriptRoot -Parent))
$modelDirectory = [System.IO.Path]::GetFullPath(
    (Join-Path $workspaceRoot ".harness\models\piper")
)
$requiredPrefix = $workspaceRoot.TrimEnd('\') + '\'
if (-not $modelDirectory.StartsWith($requiredPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to install voices outside the workspace."
}

$baseUrl = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
$files = @(
    @{ Relative = "en/en_US/lessac/medium/en_US-lessac-medium.onnx"; Name = "en_US-lessac-medium.onnx"; Md5 = "2fc642b535197b6305c7c8f92dc8b24f" },
    @{ Relative = "en/en_US/lessac/medium/en_US-lessac-medium.onnx.json"; Name = "en_US-lessac-medium.onnx.json"; Md5 = "c1f2b7bddefe113f3255ff9ef234cfd3" },
    @{ Relative = "en/en_US/lessac/medium/MODEL_CARD"; Name = "en_US-lessac-medium.MODEL_CARD"; Md5 = "42f2dd4a98149e12fc70b301d9579dfd" },
    @{ Relative = "hi/hi_IN/priyamvada/medium/hi_IN-priyamvada-medium.onnx"; Name = "hi_IN-priyamvada-medium.onnx"; Md5 = "7d5e20c2d1e72de8ed772f222e679626" },
    @{ Relative = "hi/hi_IN/priyamvada/medium/hi_IN-priyamvada-medium.onnx.json"; Name = "hi_IN-priyamvada-medium.onnx.json"; Md5 = "599ca4dc5d421a9c66692433f618e080" },
    @{ Relative = "hi/hi_IN/priyamvada/medium/MODEL_CARD"; Name = "hi_IN-priyamvada-medium.MODEL_CARD"; Md5 = "e2b745cf97087be0a97c7f10215bfa70" },
    @{ Relative = "hi/hi_IN/rohan/medium/hi_IN-rohan-medium.onnx"; Name = "hi_IN-rohan-medium.onnx"; Md5 = "d63d31559a4ccce62be938ab252a4804" },
    @{ Relative = "hi/hi_IN/rohan/medium/hi_IN-rohan-medium.onnx.json"; Name = "hi_IN-rohan-medium.onnx.json"; Md5 = "b4aeeef53e2c469def82769aa4ce19eb" },
    @{ Relative = "hi/hi_IN/rohan/medium/MODEL_CARD"; Name = "hi_IN-rohan-medium.MODEL_CARD"; Md5 = "03084fa6c2367cf7d6aaba2a0bd79b71" }
)

New-Item -ItemType Directory -Force -Path $modelDirectory | Out-Null
$stagingDirectory = Join-Path $modelDirectory (".install-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $stagingDirectory | Out-Null

try {
    foreach ($file in $files) {
        $staged = Join-Path $stagingDirectory $file.Name
        Write-Host "Downloading $($file.Name)"
        Invoke-WebRequest -Uri "$baseUrl/$($file.Relative)" -OutFile $staged
        $actual = (Get-FileHash -LiteralPath $staged -Algorithm MD5).Hash.ToLowerInvariant()
        if ($actual -ne $file.Md5) {
            throw "Checksum verification failed for $($file.Name)."
        }
    }
    foreach ($file in $files) {
        Move-Item -Force -LiteralPath (Join-Path $stagingDirectory $file.Name) `
            -Destination (Join-Path $modelDirectory $file.Name)
    }
}
finally {
    if (Test-Path -LiteralPath $stagingDirectory) {
        Remove-Item -Recurse -Force -LiteralPath $stagingDirectory
    }
}

Write-Host "Verified Piper voices installed under the protected .harness model directory."
Write-Host "Set HARNESS_TTS_ENABLED=true and restart the browser server."
