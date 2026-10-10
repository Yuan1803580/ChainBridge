[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$runtimeDir = if ($env:MANAGED_RUNTIME_DIR) {
    [IO.Path]::GetFullPath($(if ([IO.Path]::IsPathRooted($env:MANAGED_RUNTIME_DIR)) { $env:MANAGED_RUNTIME_DIR } else { Join-Path $projectRoot $env:MANAGED_RUNTIME_DIR }))
} else { Join-Path $projectRoot ".runtime\managed-testnet" }
$stateFile = Join-Path $runtimeDir "services.json"

function Require-Environment([string]$Name) {
    $value = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrWhiteSpace($value)) { throw "$Name is required for managed testnet" }
    return $value
}

function Assert-PortFree([int]$Port) {
    $listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    if ($listener) { throw "Port $Port is already in use. Stop the owning service or configure another managed-testnet port." }
}

function Wait-Health([string]$Name, [string]$Url, [Diagnostics.Process]$Process) {
    for ($attempt = 0; $attempt -lt 120; $attempt++) {
        if ($Process.HasExited) { throw "$Name exited before becoming healthy. Check $runtimeDir logs." }
        try {
            $health = Invoke-RestMethod -UseBasicParsing -Uri $Url -TimeoutSec 2
            if ($health.status -eq "ok") { return }
        } catch { }
        Start-Sleep -Milliseconds 250
    }
    throw "$Name did not become healthy. Check $runtimeDir logs."
}

if (Test-Path -LiteralPath $stateFile) {
    throw "Managed testnet state already exists at $stateFile. Run testnet:managed:stop before starting another instance."
}
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$node = (Get-Command node.exe -ErrorAction Stop).Source
if (-not (Test-Path -LiteralPath $python)) { throw "Python environment is missing. Run scripts\setup-local.ps1 first." }
& (Join-Path $projectRoot "scripts\local\sync-python-dependencies.ps1") -ProjectRoot $projectRoot -PythonPath $python

$apiPort = if ($env:MANAGED_API_PORT) { [int]$env:MANAGED_API_PORT } else { 8000 }
$runnerPort = if ($env:MANAGED_RUNNER_PORT) { [int]$env:MANAGED_RUNNER_PORT } else { 8010 }
$signerPort = if ($env:MANAGED_SIGNER_PORT) { [int]$env:MANAGED_SIGNER_PORT } else { 8020 }
$webPort = if ($env:MANAGED_WEB_PORT) { [int]$env:MANAGED_WEB_PORT } else { 3000 }
$skipWeb = $env:MANAGED_SKIP_WEB -in @("1", "true", "TRUE", "yes", "YES")
@($apiPort, $runnerPort, $signerPort) + $(if ($skipWeb) { @() } else { @($webPort) }) | ForEach-Object { Assert-PortFree $_ }

$appSecret = Require-Environment "APP_SECRET"
$platformAdminDid = Require-Environment "PLATFORM_ADMIN_DID"
$platformAdminPublicKey = Require-Environment "PLATFORM_ADMIN_PUBLIC_KEY"
$runnerApiToken = Require-Environment "AGENT_RUNNER_API_TOKEN"
$runnerControlSecret = Require-Environment "RUNNER_CONTROL_HMAC_SECRET"
$workerIntentSecret = Require-Environment "WORKER_INTENT_HMAC_SECRET"
$protocolWorkerToken = Require-Environment "PROTOCOL_WORKER_API_TOKEN"
$listenerKeys = Require-Environment "LISTENER_HMAC_KEYS"
$protocolListenerSecret = Require-Environment "PROTOCOL_WORKER_LISTENER_SECRET"
$protocolListenerId = $env:PROTOCOL_WORKER_LISTENER_ID
if ([string]::IsNullOrWhiteSpace($protocolListenerId)) {
    $firstListener = ($listenerKeys -split ",")[0].Trim()
    $separator = $firstListener.IndexOf(":")
    if ($separator -lt 1) { throw "LISTENER_HMAC_KEYS must contain worker-id:secret entries" }
    $protocolListenerId = $firstListener.Substring(0, $separator).Trim()
}
$signerConfig = Require-Environment "AGENT_SIGNER_CONFIG_FILE"
$sidecarStorageKey = Require-Environment "SIDECAR_STORAGE_KEY"
$selfServiceEnabled = $env:MANAGED_SELF_SERVICE_ONBOARDING -in @("1", "true", "TRUE", "yes", "YES")
$sidecarProvisioningToken = $null
$sidecarKeyDir = $null
if ($selfServiceEnabled) {
    $sidecarProvisioningToken = Require-Environment "SIDECAR_PROVISIONING_TOKEN"
    $sidecarKeyDir = Require-Environment "SIDECAR_KEY_DIR"
    $null = Require-Environment "MANAGED_SELF_SERVICE_CAPABILITIES"
}
$deepSeekKey = $env:DEEPSEEK_API_KEY
$protocolRelayerPrivateKey = $env:PROTOCOL_RELAYER_PRIVATE_KEY
$sourceRpc = Require-Environment "SOURCE_RPC_URL"
$destinationRpc = Require-Environment "DESTINATION_RPC_URL"
$sourceToken = Require-Environment "SOURCE_SETTLEMENT_TOKEN"
$null = Require-Environment "DESTINATION_SETTLEMENT_TOKEN"
$null = Require-Environment "SOURCE_CCIP_ROUTER"
$null = Require-Environment "DESTINATION_CCIP_ROUTER"
$null = Require-Environment "SOURCE_CHAIN_SELECTOR"
$null = Require-Environment "DESTINATION_CHAIN_SELECTOR"
if (-not $env:PROTOCOL_RELAYER_PRIVATE_KEY_FILE -and -not $env:PROTOCOL_RELAYER_PRIVATE_KEY) {
    throw "PROTOCOL_RELAYER_PRIVATE_KEY_FILE is required (PROTOCOL_RELAYER_PRIVATE_KEY remains a compatibility fallback)"
}
if ($env:PROTOCOL_RELAYER_PRIVATE_KEY_FILE -and $env:PROTOCOL_RELAYER_PRIVATE_KEY) {
    throw "Configure only one protocol relayer private-key source"
}

$env:SOURCE_CHAIN_ID = "11155111"
$env:DESTINATION_CHAIN_ID = "421614"
& $node (Join-Path $projectRoot "scripts\testnet\verify-managed-testnet-config.ts")
if ($LASTEXITCODE -ne 0) { throw "Managed testnet chain configuration verification failed" }

New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
$runtimeId = [guid]::NewGuid().ToString()
$apiDatabase = if ($env:DATABASE_URL) { $env:DATABASE_URL } else { "sqlite:///$((Join-Path $runtimeDir 'api.db').Replace('\', '/'))" }
$runnerDatabase = if ($env:AGENT_RUNNER_DATABASE_URL) { $env:AGENT_RUNNER_DATABASE_URL } else { "sqlite:///$((Join-Path $runtimeDir 'runner.db').Replace('\', '/'))" }
$signerDatabase = if ($env:AGENT_SIGNER_DATABASE_URL) { $env:AGENT_SIGNER_DATABASE_URL } else { "sqlite:///$((Join-Path $runtimeDir 'signer.db').Replace('\', '/'))" }

$env:APP_ENV = "production"
$env:PLATFORM_PROFILE = "managed-testnet"
$env:RUNNER_PROFILE = "managed"
$env:CHAIN_WORKER_PROFILE = "testnet-ccip"
$env:SETTLEMENT_MODE = "ccip_direct"
$env:DATABASE_URL = $apiDatabase
$env:AGENT_RUNNER_DATABASE_URL = $runnerDatabase
$env:AGENT_SIGNER_DATABASE_URL = $signerDatabase
$env:LOCAL_PLATFORM_RUNTIME_ID = $runtimeId
$env:LOCAL_PROTOCOL_RUNTIME_DIR = $runtimeDir
$env:RUNNER_CONTROL_URL = "http://127.0.0.1:$runnerPort"
$env:RUNNER_PUBLIC_BASE_URL = "http://127.0.0.1:$runnerPort"
$env:AGENT_RUNNER_BASE_URL = "http://127.0.0.1:$runnerPort"
$env:AGENT_SIGNER_BASE_URL = "http://127.0.0.1:$signerPort"
$env:MANAGED_SIDECAR_PUBLIC_URL = $env:AGENT_SIGNER_BASE_URL
$env:API_URL = "http://127.0.0.1:$apiPort"
$env:NEXT_PUBLIC_API_URL = if ($env:MANAGED_PUBLIC_API_ORIGIN) { $env:MANAGED_PUBLIC_API_ORIGIN.TrimEnd('/') } else { $env:API_URL }
$corsOrigins = @("http://127.0.0.1:$webPort", "http://localhost:$webPort")
if ($env:MANAGED_PUBLIC_WEB_ORIGIN) { $corsOrigins += $env:MANAGED_PUBLIC_WEB_ORIGIN.TrimEnd('/') }
$env:CORS_ORIGINS = ($corsOrigins | Select-Object -Unique) -join ","
$env:AGENT_RUNNER_CORS_ORIGINS = $env:CORS_ORIGINS
$env:DYNAMIC_SOURCE_CHAIN_ID = "11155111"
$env:DYNAMIC_SOURCE_RPC_URL = $sourceRpc
$env:DYNAMIC_SOURCE_SETTLEMENT_TOKEN = $sourceToken
$env:HOME_CHAIN_RPC_URLS = (@{ "421614" = $destinationRpc } | ConvertTo-Json -Compress)
$env:AUTO_INVOKE_AGENTS = "true"
$env:REQUIRE_SIGNED_AGENT_RESULTS = "true"
$env:PROTOCOL_ADMIN_DID = $platformAdminDid

$sha256 = [Security.Cryptography.SHA256]::Create()
try {
    $env:PROTOCOL_WORKER_API_TOKEN_SHA256 = -join ($sha256.ComputeHash([Text.Encoding]::UTF8.GetBytes($protocolWorkerToken)) | ForEach-Object { $_.ToString("x2") })
}
finally { $sha256.Dispose() }

Push-Location $projectRoot
try {
    & $python -m alembic -c alembic.ini upgrade head
    if ($LASTEXITCODE -ne 0) { throw "API migration failed" }
} finally { Pop-Location }
Push-Location (Join-Path $projectRoot "services\agent-runner")
try {
    & $python -m alembic -c alembic.ini upgrade head
    if ($LASTEXITCODE -ne 0) { throw "Runner migration failed" }
} finally { Pop-Location }
Push-Location (Join-Path $projectRoot "services\agent-signer")
try {
    & $python -m alembic -c alembic.ini upgrade head
    if ($LASTEXITCODE -ne 0) { throw "Sidecar migration failed" }
} finally { Pop-Location }

$processes = @()
try {
    $env:AGENT_SIGNER_CONFIG_FILE = $signerConfig
    $env:SIDECAR_STORAGE_KEY = $sidecarStorageKey
    if ($selfServiceEnabled) {
        $env:SIDECAR_PROVISIONING_TOKEN = $sidecarProvisioningToken
        $env:SIDECAR_KEY_DIR = $sidecarKeyDir
    }
    Remove-Item Env:\APP_SECRET, Env:\PLATFORM_ADMIN_PUBLIC_KEY, Env:\RUNNER_CONTROL_HMAC_SECRET, Env:\AGENT_RUNNER_API_TOKEN, `
        Env:\PROTOCOL_WORKER_API_TOKEN, Env:\PROTOCOL_RELAYER_PRIVATE_KEY, Env:\DEEPSEEK_API_KEY, Env:\LISTENER_HMAC_KEYS, `
        Env:\PROTOCOL_WORKER_LISTENER_SECRET -ErrorAction SilentlyContinue
    $signer = Start-Process -FilePath $python -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$signerPort") `
        -WorkingDirectory (Join-Path $projectRoot "services\agent-signer") -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $runtimeDir "signer.stdout.log") -RedirectStandardError (Join-Path $runtimeDir "signer.stderr.log")
    $processes += @{ Name = "signer"; Process = $signer }
    Wait-Health "Agent Signer" "http://127.0.0.1:$signerPort/health" $signer

    Remove-Item Env:\AGENT_SIGNER_CONFIG_FILE, Env:\SIDECAR_STORAGE_KEY, Env:\SIDECAR_PROVISIONING_TOKEN, Env:\SIDECAR_KEY_DIR -ErrorAction SilentlyContinue
    $env:AGENT_RUNNER_API_TOKEN = $runnerApiToken
    $env:RUNNER_CONTROL_HMAC_SECRET = $runnerControlSecret
    $env:WORKER_INTENT_HMAC_SECRET = $workerIntentSecret
    if ($deepSeekKey) { $env:DEEPSEEK_API_KEY = $deepSeekKey }
    $runner = Start-Process -FilePath $python -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$runnerPort") `
        -WorkingDirectory (Join-Path $projectRoot "services\agent-runner") -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $runtimeDir "runner.stdout.log") -RedirectStandardError (Join-Path $runtimeDir "runner.stderr.log")
    $processes += @{ Name = "runner"; Process = $runner }
    Wait-Health "Agent Runner" "http://127.0.0.1:$runnerPort/health" $runner

    $env:APP_SECRET = $appSecret
    $env:PLATFORM_ADMIN_DID = $platformAdminDid
    $env:PLATFORM_ADMIN_PUBLIC_KEY = $platformAdminPublicKey
    $env:RUNNER_CONTROL_HMAC_SECRET = $runnerControlSecret
    $env:RUNNER_CHECK_API_TOKEN = $runnerApiToken
    $env:LISTENER_HMAC_KEYS = $listenerKeys
    Remove-Item Env:\DEEPSEEK_API_KEY, Env:\WORKER_INTENT_HMAC_SECRET, Env:\PROTOCOL_WORKER_API_TOKEN, Env:\PROTOCOL_RELAYER_PRIVATE_KEY -ErrorAction SilentlyContinue
    $api = Start-Process -FilePath $python -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$apiPort") `
        -WorkingDirectory (Join-Path $projectRoot "apps\api") -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $runtimeDir "api.stdout.log") -RedirectStandardError (Join-Path $runtimeDir "api.stderr.log")
    $processes += @{ Name = "api"; Process = $api }
    Wait-Health "Platform API" "http://127.0.0.1:$apiPort/api/v1/health" $api

    if (-not $skipWeb) {
        Remove-Item Env:\APP_SECRET, Env:\RUNNER_CONTROL_HMAC_SECRET, Env:\RUNNER_CHECK_API_TOKEN, Env:\WORKER_INTENT_HMAC_SECRET, Env:\PROTOCOL_WORKER_API_TOKEN_SHA256, Env:\LISTENER_HMAC_KEYS -ErrorAction SilentlyContinue
        $nextCli = Join-Path $projectRoot "apps\web\node_modules\next\dist\bin\next"
        if (-not (Test-Path -LiteralPath $nextCli)) { throw "Web dependencies are missing. Run scripts\setup-local.ps1 first." }
        $web = Start-Process -FilePath $node -ArgumentList @("`"$nextCli`"", "dev", "--hostname", "127.0.0.1", "--port", "$webPort") `
            -WorkingDirectory (Join-Path $projectRoot "apps\web") -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $runtimeDir "web.stdout.log") -RedirectStandardError (Join-Path $runtimeDir "web.stderr.log")
        $processes += @{ Name = "web"; Process = $web }
        Wait-Health "Platform Web" "http://127.0.0.1:$webPort/api/runtime-health" $web
    }

    $env:PROTOCOL_WORKER_API_TOKEN = $protocolWorkerToken
    $env:PROTOCOL_WORKER_LISTENER_ID = $protocolListenerId
    $env:PROTOCOL_WORKER_LISTENER_SECRET = $protocolListenerSecret
    $env:AGENT_RUNNER_API_TOKEN = $runnerApiToken
    $env:WORKER_INTENT_HMAC_SECRET = $workerIntentSecret
    if ($protocolRelayerPrivateKey) { $env:PROTOCOL_RELAYER_PRIVATE_KEY = $protocolRelayerPrivateKey }
    Remove-Item Env:\APP_SECRET, Env:\PLATFORM_ADMIN_PUBLIC_KEY, Env:\RUNNER_CONTROL_HMAC_SECRET, Env:\RUNNER_CHECK_API_TOKEN, Env:\PROTOCOL_WORKER_API_TOKEN_SHA256, Env:\DEEPSEEK_API_KEY -ErrorAction SilentlyContinue
    $worker = Start-Process -FilePath $node -ArgumentList @("services\chain-worker\src\protocol-index.ts") `
        -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $runtimeDir "worker.stdout.log") -RedirectStandardError (Join-Path $runtimeDir "worker.stderr.log")
    $processes += @{ Name = "worker"; Process = $worker }
    Start-Sleep -Seconds 2
    if ($worker.HasExited) { throw "Protocol Worker exited during startup. Check worker.stderr.log." }

    $state = @{
        schemaVersion = 1
        runtimeId = $runtimeId
        runtimeDir = $runtimeDir
        startedAt = [DateTime]::UtcNow.ToString("o")
        apiUrl = "http://127.0.0.1:$apiPort"
        webUrl = if ($skipWeb) { $null } else { "http://127.0.0.1:$webPort" }
        publicApiUrl = $env:MANAGED_PUBLIC_API_ORIGIN
        publicWebUrl = $env:MANAGED_PUBLIC_WEB_ORIGIN
        services = @($processes | ForEach-Object { @{
            name = $_.Name
            pid = $_.Process.Id
            startTimeUtc = $_.Process.StartTime.ToUniversalTime().ToString("o")
        } })
    }
    $state | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $stateFile -Encoding UTF8
    Write-Host "Managed testnet services are running."
    Write-Host "API: http://127.0.0.1:$apiPort"
    if (-not $skipWeb) { Write-Host "Web: http://127.0.0.1:$webPort" }
}
catch {
    $toStop = @($processes)
    [array]::Reverse($toStop)
    foreach ($entry in $toStop) {
        if (-not $entry.Process.HasExited) { & taskkill.exe /PID $entry.Process.Id /T /F *> $null }
    }
    throw
}
