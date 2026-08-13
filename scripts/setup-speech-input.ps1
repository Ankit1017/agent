param(
    [switch]$AcceptModelLicenses
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$ModelRoot = Join-Path $ProjectRoot ".harness\models\speech-input"
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$SherpaCli = Join-Path $ProjectRoot ".venv\Scripts\sherpa-onnx-cli.exe"
$WhisperRevision = "536b0662742c02347bc0e980a01041f333bce120"

Write-Host "Local speech-input model terms:" -ForegroundColor Cyan
Write-Host "- sherpa-onnx runtime: Apache-2.0."
Write-Host "- GigaSpeech KWS model: review its bundled model card and GigaSpeech terms."
Write-Host "- Faster Whisper runtime: MIT; Whisper model weights: MIT."
Write-Host "- Models remain local and are not licensed by this project."
if (-not $AcceptModelLicenses) {
    throw "Review the terms, then rerun with -AcceptModelLicenses."
}
if (-not (Test-Path -LiteralPath $Python) -or -not (Test-Path -LiteralPath $SherpaCli)) {
    throw "Speech-input dependencies are missing. Run scripts/setup.ps1 first."
}

$staging = Join-Path $ModelRoot (".install-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $staging -Force | Out-Null

function Get-PinnedFile(
    [string]$Url,
    [string]$Destination,
    [string]$Sha256
) {
    Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $Destination
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Destination).Hash
    if ($actual -ne $Sha256) {
        throw "Downloaded speech-input artifact failed checksum verification."
    }
}

try {
    $archive = Join-Path $staging "sherpa-kws.tar.bz2"
    Get-PinnedFile `
        "https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01.tar.bz2" `
        $archive `
        "F170013B4716E41B62B9BFD809687C207CEF798EF9BC6534D524E17AF9B6561A"
    & tar.exe -xf $archive -C $staging
    if ($LASTEXITCODE -ne 0) { throw "Could not extract the verified wake-word model." }
    $extracted = Join-Path $staging "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01"
    $wake = Join-Path $staging "sherpa-kws-gigaspeech"
    Move-Item -LiteralPath $extracted -Destination $wake
    [IO.File]::WriteAllText(
        (Join-Path $wake "hey-buddy-raw.txt"),
        "HEY BUDDY :2.0 #0.25`n",
        [Text.UTF8Encoding]::new($false)
    )
    & $SherpaCli text2token `
        --tokens (Join-Path $wake "tokens.txt") `
        --tokens-type bpe `
        --bpe-model (Join-Path $wake "bpe.model") `
        (Join-Path $wake "hey-buddy-raw.txt") `
        (Join-Path $wake "hey-buddy.txt")
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath (Join-Path $wake "hey-buddy.txt"))) {
        throw "Could not create the pinned Hey Buddy token sequence."
    }

    $whisper = Join-Path $staging "whisper-small"
    New-Item -ItemType Directory -Path $whisper | Out-Null
    $whisperFiles = @(
        @{ Name = "model.bin"; Hash = "3E305921506D8872816023E4C273E75D2419FB89B24DA97B4FE7BCE14170D671" },
        @{ Name = "config.json"; Hash = "B55496AC7940A7AE47D2C01EAB40EDFD8701FEEC1229D9CCE3B40014383FB828" },
        @{ Name = "tokenizer.json"; Hash = "FB7B63191E9BB045082C79FD742A3106A12C99513AB30DF4A0D47FA6CB6FD0AB" },
        @{ Name = "vocabulary.txt"; Hash = "34CE3FE1C5041027B3F8D42912270993F986DBC4BB34CF27F951E34A1E453913" },
        @{ Name = "README.md"; Hash = "329373481008C7C38654AFF8ECDCF0163C211557CC7BA8E2EF6F2F84B4F75EC8" }
    )
    foreach ($file in $whisperFiles) {
        Get-PinnedFile `
            "https://huggingface.co/Systran/faster-whisper-small/resolve/$WhisperRevision/$($file.Name)" `
            (Join-Path $whisper $file.Name) `
            $file.Hash
    }

    New-Item -ItemType Directory -Path $ModelRoot -Force | Out-Null
    foreach ($name in @("sherpa-kws-gigaspeech", "whisper-small")) {
        $destination = Join-Path $ModelRoot $name
        if (Test-Path -LiteralPath $destination) {
            Remove-Item -LiteralPath $destination -Recurse -Force
        }
        Move-Item -LiteralPath (Join-Path $staging $name) -Destination $destination
    }
    Write-Host "Verified local speech-input models installed." -ForegroundColor Green
    Write-Host "Set HARNESS_STT_ENABLED=true and restart the browser server."
}
finally {
    if (Test-Path -LiteralPath $staging) {
        Remove-Item -LiteralPath $staging -Recurse -Force
    }
}
