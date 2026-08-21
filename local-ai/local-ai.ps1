[CmdletBinding()]
param(
    [ValidateSet("Setup", "Start", "Stop", "DeepStop", "Restart", "Status", "Monitor", "UI", "LegacyUI", "Observability", "Credentials", "Test", "Logs", "HarnessLogs")]
    [string]$Action = "Status"
)

$ErrorActionPreference = "Stop"
$StackRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $StackRoot
$ComposeFile = Join-Path $StackRoot "compose.yaml"
$EnvFile = Join-Path $StackRoot ".env"
$RuntimeDir = Join-Path $StackRoot "runtime"
$OllamaRoot = Join-Path $env:LOCALAPPDATA "Programs\Ollama"
$OllamaExe = Join-Path $OllamaRoot "ollama.exe"
$ModelName = "gpt-oss:20b"
$EmbeddingModelName = "embeddinggemma"
$UiUrl = "http://127.0.0.1:3000"
$LegacyUiUrl = "http://localhost:3001"
$OllamaUrl = "http://127.0.0.1:11434"
$GatewayUrl = "http://localhost:4000"
$SearxngUrl = "http://127.0.0.1:8080"
$ObservabilityUrl = "$GatewayUrl/ui"
$HarnessPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$HarnessPidFile = Join-Path $RuntimeDir "harness-web.json"
$HarnessStdout = Join-Path $RuntimeDir "harness-web.stdout.log"
$HarnessStderr = Join-Path $RuntimeDir "harness-web.stderr.log"
$HarnessBrowserProfile = Join-Path $RuntimeDir "chrome-harness-react-v2"

function Open-LocalUrl([string]$Url) {
    $chromeCandidates = @(
        (Join-Path $env:ProgramFiles "Google\Chrome\Application\chrome.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Google\Chrome\Application\chrome.exe"),
        (Join-Path $env:LOCALAPPDATA "Google\Chrome\Application\chrome.exe")
    )
    $chrome = $chromeCandidates | Where-Object { Test-Path -LiteralPath $_ } |
        Select-Object -First 1
    if ($chrome) {
        Start-Process -FilePath $chrome -ArgumentList "--new-window", $Url
        return
    }
    Start-Process $Url
}

function Open-HarnessBrowser {
    $chromeCandidates = @(
        (Join-Path $env:ProgramFiles "Google\Chrome\Application\chrome.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Google\Chrome\Application\chrome.exe"),
        (Join-Path $env:LOCALAPPDATA "Google\Chrome\Application\chrome.exe")
    )
    $chrome = $chromeCandidates | Where-Object { Test-Path -LiteralPath $_ } |
        Select-Object -First 1
    if (-not $chrome) {
        Open-LocalUrl $UiUrl
        return
    }
    # Keep the React GUI isolated from service workers previously registered by Open WebUI
    # when it occupied the same localhost origin. The versioned profile is regenerable UI state.
    New-Item -ItemType Directory -Path $HarnessBrowserProfile -Force | Out-Null
    Start-Process -FilePath $chrome -ArgumentList @(
        "--new-window",
        "--user-data-dir=$HarnessBrowserProfile",
        "--disable-extensions",
        "--no-first-run",
        "--no-default-browser-check",
        "$UiUrl/?ui=harness-react-v2"
    )
}

function Get-HarnessProcess {
    if (-not (Test-Path -LiteralPath $HarnessPidFile)) { return $null }
    try {
        $identity = Get-Content -LiteralPath $HarnessPidFile -Raw | ConvertFrom-Json
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($identity.pid)" -ErrorAction Stop
        if (-not $process -or -not $process.CommandLine) { return $null }
        $isHarnessServer = $process.CommandLine.Contains("harness-web") -or
            $process.CommandLine.Contains("local_harness.interfaces.web.server")
        if (-not $isHarnessServer -or -not $process.CommandLine.Contains($ProjectRoot)) {
            return $null
        }
        return $process
    }
    catch { return $null }
}

function Start-HarnessGui {
    if (Test-LocalHttp "$UiUrl/health") { return }
    if (Get-HarnessProcess) { throw "The recorded harness GUI process is running but unhealthy." }
    if (-not (Test-Path -LiteralPath $HarnessPython)) {
        throw "The harness Python environment is missing. Run Setup Local AI.cmd once."
    }
    $staticDir = Join-Path $ProjectRoot "web\dist"
    if (-not (Test-Path -LiteralPath (Join-Path $staticDir "index.html"))) {
        throw "The browser UI is not built. Run Setup Local AI.cmd once."
    }
    New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
    $arguments = @(
        "-m", "local_harness.interfaces.web.server",
        "--control-workspace", $ProjectRoot,
        "--catalog", (Join-Path $RuntimeDir "harness-workspaces.json"),
        "--static-dir", $staticDir,
        "--host", "127.0.0.1",
        "--port", "3000"
    )
    $process = Start-Process -FilePath $HarnessPython -ArgumentList $arguments -WindowStyle Hidden -PassThru `
        -WorkingDirectory $ProjectRoot -RedirectStandardOutput $HarnessStdout `
        -RedirectStandardError $HarnessStderr
    @{ pid = $process.Id; started_at = (Get-Date).ToUniversalTime().ToString("o") } |
        ConvertTo-Json | Set-Content -LiteralPath $HarnessPidFile -Encoding UTF8
    Wait-Until { Test-LocalHttp "$UiUrl/health" } 60 "Harness browser GUI"
}

function Stop-HarnessGui {
    $process = Get-HarnessProcess
    if ($process) {
        Stop-Process -Id $process.ProcessId -ErrorAction SilentlyContinue
        Wait-Until { -not (Test-LocalHttp "$UiUrl/health") } 15 "Harness browser GUI to stop"
    }
    Remove-Item -LiteralPath $HarnessPidFile -Force -ErrorAction SilentlyContinue
}

function Write-Section([string]$Title) {
    Write-Host "`n=== $Title ===" -ForegroundColor Cyan
}

function New-LocalSecret([string]$Prefix = "") {
    $bytes = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    }
    finally {
        $rng.Dispose()
    }
    $secret = [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
    return "$Prefix$secret"
}

function Get-LocalSettings {
    $settings = [ordered]@{}
    if (Test-Path -LiteralPath $EnvFile) {
        foreach ($line in Get-Content -LiteralPath $EnvFile) {
            if ($line -and -not $line.TrimStart().StartsWith("#") -and $line.Contains("=")) {
                $name, $value = $line.Split("=", 2)
                $settings[$name.Trim()] = $value.Trim()
            }
        }
    }
    return $settings
}

function Ensure-LocalSecrets {
    $settings = Get-LocalSettings
    if (-not $settings.Contains("WEBUI_SECRET_KEY")) {
        $settings["WEBUI_SECRET_KEY"] = New-LocalSecret
    }
    if (-not $settings.Contains("LITELLM_MASTER_KEY")) {
        $settings["LITELLM_MASTER_KEY"] = New-LocalSecret "sk-local-"
    }
    if (-not $settings.Contains("LITELLM_SALT_KEY")) {
        $settings["LITELLM_SALT_KEY"] = New-LocalSecret "sk-salt-"
    }
    if (-not $settings.Contains("LITELLM_UI_PASSWORD")) {
        $settings["LITELLM_UI_PASSWORD"] = New-LocalSecret "local-"
    }
    if (-not $settings.Contains("POSTGRES_PASSWORD")) {
        $settings["POSTGRES_PASSWORD"] = New-LocalSecret
    }
    if (-not $settings.Contains("SEARXNG_SECRET")) {
        $settings["SEARXNG_SECRET"] = New-LocalSecret
    }

    $lines = @($settings.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" })
    [IO.File]::WriteAllText($EnvFile, ($lines -join "`r`n") + "`r`n", [Text.UTF8Encoding]::new($false))
}

function Test-LocalHttp([string]$Url) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 3
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 500
    }
    catch {
        return $false
    }
}

function Wait-Until([scriptblock]$Condition, [int]$TimeoutSeconds, [string]$Description) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (& $Condition) {
            return
        }
        Start-Sleep -Seconds 2
    }
    throw "Timed out waiting for $Description."
}

function Invoke-Compose([string[]]$Arguments) {
    & docker compose --project-directory $StackRoot --env-file $EnvFile -f $ComposeFile @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose failed with exit code $LASTEXITCODE"
    }
}

function Test-DockerReady {
    try {
        & docker info --format "{{.ServerVersion}}" *> $null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

function Ensure-Docker {
    if (Test-DockerReady) {
        return
    }
    Write-Host "Starting Docker Desktop..."
    & docker desktop start | Out-Host
    Wait-Until { Test-DockerReady } 180 "Docker Desktop"
}

function Get-OllamaProcesses {
    $resolvedRoot = [IO.Path]::GetFullPath($OllamaRoot)
    Get-CimInstance Win32_Process | Where-Object {
        $_.ExecutablePath -and
        [IO.Path]::GetFullPath($_.ExecutablePath).StartsWith($resolvedRoot, [StringComparison]::OrdinalIgnoreCase)
    }
}

function Test-OllamaModelAvailable([string]$Name = $ModelName) {
    try {
        $tags = Invoke-RestMethod -Uri "$OllamaUrl/api/tags" -TimeoutSec 3
        $names = @($tags.models | ForEach-Object { $_.name })
        return ($names -contains $Name) -or ($names -contains "$Name`:latest")
    }
    catch {
        return $false
    }
}

function Ensure-Ollama {
    if (-not (Test-Path -LiteralPath $OllamaExe)) {
        throw "Ollama is not installed at $OllamaExe"
    }

    # Keep the desktop app and controller pointed at the already-downloaded
    # D-drive model. This does not move or download model data.
    if ([Environment]::GetEnvironmentVariable("OLLAMA_MODELS", "User") -ne "D:\ollama-models") {
        [Environment]::SetEnvironmentVariable("OLLAMA_MODELS", "D:\ollama-models", "User")
    }

    # The Ollama desktop app can auto-start a server using its old/default
    # model directory. Replace that server when it cannot see our local model.
    if ((Test-LocalHttp "$OllamaUrl/api/version") -and -not (Test-OllamaModelAvailable)) {
        Write-Host "Restarting Ollama with the existing D:\ollama-models directory..."
        foreach ($process in @(Get-OllamaProcesses)) {
            Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
        }
        Wait-Until { -not (Test-LocalHttp "$OllamaUrl/api/version") } 15 "the old Ollama server to stop"
    }

    if (-not (Test-LocalHttp "$OllamaUrl/api/version")) {
        New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
        $env:OLLAMA_MODELS = "D:\ollama-models"
        $env:OLLAMA_NO_CLOUD = "true"
        $env:OLLAMA_CONTEXT_LENGTH = "8192"
        $env:OLLAMA_LLM_LIBRARY = "vulkan"
        $env:OLLAMA_VULKAN = "1"
        $env:OLLAMA_KEEP_ALIVE = "2m"
        Start-Process -FilePath $OllamaExe -ArgumentList "serve" -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $RuntimeDir "ollama.stdout.log") `
            -RedirectStandardError (Join-Path $RuntimeDir "ollama.stderr.log")
        Wait-Until { Test-LocalHttp "$OllamaUrl/api/version" } 60 "Ollama"
    }

    if (-not (Test-OllamaModelAvailable)) {
        throw "$ModelName is not present in D:\ollama-models. Setup will not download it automatically."
    }
}

function Start-LocalAI {
    Ensure-LocalSecrets
    Ensure-Docker
    Ensure-Ollama

    Write-Host "Starting SearXNG, LiteLLM, and the host Harness GUI without pulling images or models..."
    Invoke-Compose @("up", "-d", "--pull", "never")
    Wait-Until { Test-LocalHttp "$GatewayUrl/health/liveliness" } 180 "LiteLLM gateway"
    Wait-Until { Test-LocalHttp "$SearxngUrl/search?q=health&format=json" } 180 "SearXNG"
    Start-HarnessGui
    Write-Host "Harness GUI   : $UiUrl" -ForegroundColor Green
    Write-Host "Observability : $ObservabilityUrl" -ForegroundColor Green
    Write-Host "Web search    : $SearxngUrl" -ForegroundColor Green
    Write-Host "The model remains unloaded until the first request, then unloads after 2 minutes idle."
}

function Stop-LocalAI {
    Ensure-LocalSecrets
    Stop-HarnessGui
    if (Test-DockerReady) {
        Invoke-Compose @("stop", "--timeout", "20")
    }
    # Stopping the Ollama server releases every loaded model. Avoid `ollama stop`
    # here because its Windows progress renderer writes control bytes to stderr,
    # which Windows PowerShell can incorrectly promote to a terminating error.
    foreach ($process in @(Get-OllamaProcesses)) {
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Write-Host "Harness GUI, SearXNG, LiteLLM, its database, and Ollama are stopped; model RAM/VRAM has been released." -ForegroundColor Green
}

function Get-ContainerStatus([string]$Name) {
    $existing = docker ps -a --filter "name=^/$Name$" --format "{{.Names}}" 2>$null
    if ($existing -ne $Name) { return "not created" }
    $status = docker inspect $Name --format "{{.State.Status}} | health={{if .State.Health}}{{.State.Health.Status}}{{else}}n/a{{end}} | RAM limit={{.HostConfig.Memory}} bytes | CPUs={{.HostConfig.NanoCpus}}" 2>$null
    if ($status) { return $status }
    return "not created"
}

function Show-Status {
    Write-Section "Local AI"
    $dockerReady = Test-DockerReady
    Write-Host ("Docker engine : " + $(if ($dockerReady) { "running" } else { "stopped" }))
    if ($dockerReady) {
        Write-Host ("LiteLLM DB    : " + (Get-ContainerStatus "local-ai-litellm-db"))
        Write-Host ("LiteLLM       : " + (Get-ContainerStatus "local-ai-gateway"))
        Write-Host ("Open WebUI legacy: " + (Get-ContainerStatus "local-ai-webui"))
        Write-Host ("SearXNG       : " + (Get-ContainerStatus "local-ai-searxng"))
    }
    Write-Host ("Harness GUI    : " + $(if (Test-LocalHttp "$UiUrl/health") { "$UiUrl (healthy)" } else { "stopped" }))
    Write-Host ("Gateway API    : " + $(if (Test-LocalHttp "$GatewayUrl/health/liveliness") { "$GatewayUrl (healthy)" } else { "stopped" }))
    Write-Host ("Ollama API    : " + $(if (Test-LocalHttp "$OllamaUrl/api/version") { "$OllamaUrl (healthy)" } else { "stopped" }))
    Write-Host ("SearXNG API   : " + $(if (Test-LocalHttp "$SearxngUrl/search?q=health&format=json") { "$SearxngUrl (healthy)" } else { "stopped" }))
    Write-Host "Model storage : D:\ollama-models"
    Write-Host "Model          : $ModelName"
    Write-Host ("Embedding model: " + $(if (Test-OllamaModelAvailable $EmbeddingModelName) { "$EmbeddingModelName (installed)" } else { "$EmbeddingModelName (missing; lexical fallback active)" }))
    Write-Host "Runtime context: 8192 tokens (native model maximum: 131072)"
    Write-Host "GPU backend    : Vulkan"
    Write-Host "Idle unload    : 2 minutes"
    Write-Host "Model cloud/downloads: disabled; SearXNG web access: enabled"

    if (Test-LocalHttp "$OllamaUrl/api/version") {
        Write-Section "Loaded models"
        & $OllamaExe ps
    }

    $nvidia = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if ($nvidia) {
        Write-Section "GPU"
        & nvidia-smi --query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw `
            --format=csv,noheader
    }

    Write-Section "Control"
    Write-Host "Harness GUI     : $UiUrl"
    Write-Host "Legacy Open WebUI: $LegacyUiUrl (manual profile)"
    Write-Host "Observability   : $ObservabilityUrl"
    Write-Host "Web search      : $SearxngUrl"
    Write-Host "Observed API    : $GatewayUrl/v1/chat/completions"
    Write-Host "Direct Ollama   : $OllamaUrl (bypasses observability)"
    Write-Host "Logs           : .\local-ai.ps1 Logs"
    Write-Host "Live monitor   : .\local-ai.ps1 Monitor"
}

function Show-Credentials {
    Ensure-LocalSecrets
    $settings = Get-LocalSettings
    Write-Section "LiteLLM observability login"
    Write-Host "URL      : $ObservabilityUrl"
    Write-Host "Username : admin"
    Write-Host "Password : $($settings['LITELLM_UI_PASSWORD'])"
    Write-Section "Observed OpenAI-compatible API"
    Write-Host "Base URL : $GatewayUrl/v1"
    Write-Host "API key  : $($settings['LITELLM_MASTER_KEY'])"
    Write-Warning "Keep these local credentials private and never commit local-ai/.env."
}

function Test-Gateway {
    Start-LocalAI
    $settings = Get-LocalSettings
    $body = @{
        model = $ModelName
        messages = @(
            @{ role = "system"; content = "You are a concise local observability test assistant." }
            @{ role = "user"; content = "Reply with exactly: local gateway healthy" }
        )
        user = $env:USERNAME
        metadata = @{
            generation_name = "local-gateway-smoke-test"
            session_id = "local-ai-controller"
            source = "Test Observed Call.cmd"
            environment = "local"
            tags = @("local-ai", "controller-test", "ollama")
        }
        max_tokens = 128
        stream = $false
    } | ConvertTo-Json -Depth 6
    $response = Invoke-RestMethod -Uri "$GatewayUrl/v1/chat/completions" -Method Post `
        -Headers @{ Authorization = "Bearer $($settings['LITELLM_MASTER_KEY'])" } `
        -ContentType "application/json" -Body $body -TimeoutSec 600
    Write-Section "Gateway response"
    $message = $response.choices[0].message
    if ([string]::IsNullOrWhiteSpace($message.content) -and $message.reasoning_content) {
        Write-Host "[The short test ended during reasoning]"
        Write-Host $message.reasoning_content
    }
    else {
        Write-Host $message.content
    }
    Write-Host ("Usage: prompt={0}, completion={1}, total={2}" -f `
        $response.usage.prompt_tokens, $response.usage.completion_tokens, $response.usage.total_tokens)
    Write-Host "This request is now recorded in $ObservabilityUrl" -ForegroundColor Green
}

switch ($Action) {
    "Setup" {
        Ensure-LocalSecrets
        Ensure-Docker
        Ensure-Ollama
        Write-Host "Downloading pinned application images and the local embedding model once."
        Invoke-Compose @("pull")
        if (-not (Test-OllamaModelAvailable $EmbeddingModelName)) {
            Write-Host "Downloading $EmbeddingModelName for local project-memory retrieval..."
            & $OllamaExe pull $EmbeddingModelName
            if ($LASTEXITCODE -ne 0) { throw "Embedding model download failed." }
        }
        Write-Host "Installing the host harness and building the pinned browser client..."
        & (Join-Path $ProjectRoot ".venv\Scripts\python.exe") -m pip install -e $ProjectRoot
        if ($LASTEXITCODE -ne 0) { throw "Harness Python installation failed." }
        & npm ci --prefix (Join-Path $ProjectRoot "web")
        if ($LASTEXITCODE -ne 0) { throw "Browser dependency installation failed." }
        & npm run build --prefix (Join-Path $ProjectRoot "web")
        if ($LASTEXITCODE -ne 0) { throw "Browser production build failed." }
        Start-LocalAI
    }
    "Start" {
        Start-LocalAI
    }
    "Stop" {
        Stop-LocalAI
    }
    "DeepStop" {
        Stop-LocalAI
        Write-Warning "Stopping Docker Desktop also stops every other Docker workload on this laptop."
        & docker desktop stop | Out-Host
        Write-Host "Docker Desktop is stopped for maximum RAM savings." -ForegroundColor Green
    }
    "Restart" {
        Stop-LocalAI
        Start-LocalAI
    }
    "Status" {
        Show-Status
    }
    "Monitor" {
        while ($true) {
            Clear-Host
            Show-Status
            Write-Host "`nRefreshing every 3 seconds. Press Ctrl+C to exit." -ForegroundColor DarkGray
            Start-Sleep -Seconds 3
        }
    }
    "UI" {
        Start-LocalAI
        Open-HarnessBrowser
    }
    "LegacyUI" {
        Ensure-LocalSecrets
        Ensure-Docker
        Invoke-Compose @("--profile", "legacy-ui", "up", "-d", "open-webui")
        Wait-Until { Test-LocalHttp "$LegacyUiUrl/health" } 180 "legacy Open WebUI"
        Open-LocalUrl $LegacyUiUrl
    }
    "Observability" {
        Start-LocalAI
        Open-LocalUrl $ObservabilityUrl
    }
    "Credentials" {
        Show-Credentials
    }
    "Test" {
        Test-Gateway
    }
    "Logs" {
        Ensure-Docker
        Invoke-Compose @("logs", "--tail", "150", "-f")
    }
    "HarnessLogs" {
        if (Test-Path -LiteralPath $HarnessStdout) { Get-Content -LiteralPath $HarnessStdout -Tail 150 }
        if (Test-Path -LiteralPath $HarnessStderr) { Get-Content -LiteralPath $HarnessStderr -Tail 150 }
    }
}
