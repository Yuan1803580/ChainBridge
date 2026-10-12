[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$processPath = $env:PATH
Remove-Item Env:\Path -ErrorAction SilentlyContinue
$env:Path = $processPath

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
. (Join-Path $PSScriptRoot "service-ownership.ps1")

$runtimeValue = if ($env:LOCAL_RUNTIME_DIR) { $env:LOCAL_RUNTIME_DIR } else { ".runtime\local-experiment" }
$runtimeDir = if ([IO.Path]::IsPathRooted($runtimeValue)) {
    [IO.Path]::GetFullPath($runtimeValue)
} else {
    [IO.Path]::GetFullPath((Join-Path $projectRoot $runtimeValue))
}
$platformStateFile = Join-Path $runtimeDir "platform.json"
$experimentStateFile = Join-Path $runtimeDir "experiment.json"
if (-not (Test-Path -LiteralPath $platformStateFile) -or -not (Test-Path -LiteralPath $experimentStateFile)) {
    throw "The local experiment state is missing. Use pnpm local:experiment:start first."
}

$platformState = Get-Content -Raw -LiteralPath $platformStateFile | ConvertFrom-Json
$experimentState = Get-Content -Raw -LiteralPath $experimentStateFile | ConvertFrom-Json
$runtimeId = [string]$platformState.runtimeId
$apiPort = [int]$platformState.apiPort
$webPort = [int]$platformState.webPort
$runnerPort = [int]$platformState.runnerPort
$coordinatorPort = [int]$experimentState.coordinatorPort
$requesterPort = if ($experimentState.requesterPort) { [int]$experimentState.requesterPort } elseif ($env:LOCAL_REQUESTER_PORT) { [int]$env:LOCAL_REQUESTER_PORT } else { 8400 }
$requesterToken = if ($env:LOCAL_REQUESTER_API_TOKEN) { $env:LOCAL_REQUESTER_API_TOKEN } else { "local-experiment-requester-token" }
$requesterDir = if ($env:LOCAL_REQUESTER_DIR) { [IO.Path]::GetFullPath($env:LOCAL_REQUESTER_DIR) } else { Join-Path $projectRoot "tools\local-requester-client" }
$requesterConfig = if ($env:REQUESTER_CONFIG) { [IO.Path]::GetFullPath($env:REQUESTER_CONFIG) } else { Join-Path $requesterDir "config.json" }
if (-not (Test-Path -LiteralPath (Join-Path $requesterDir "src\server.js")) -or -not (Test-Path -LiteralPath $requesterConfig)) {
    throw "Local Requester Client or its configuration is missing: $requesterDir"
}
if (-not (Test-Path -LiteralPath (Join-Path $requesterDir "node_modules\ethers"))) {
    throw "Local Requester Client dependencies are missing. Run pnpm requester:install first."
}

function Stop-OwnedHttpService {
    param(
        [string]$Name,
        [object]$ProcessId,
        [object]$StartTimeUtc,
        [int]$Port,
        [string]$IdentityUrl,
        [string]$ExpectedService
    )
    $listeners = @(Get-ListeningProcessIds -Port $Port)
    $identity = if ($listeners.Count -gt 0) { Get-HttpServiceIdentity -Url $IdentityUrl } else { $null }
    if ($listeners.Count -eq 0 -and $null -eq $identity) {
        return
    }
    if ($null -eq $identity -or $identity.service -ne $ExpectedService -or $identity.runtimeId -ne $runtimeId) {
        throw "Refusing to restart $Name because its runtime identity does not match."
    }
    $effectivePid = $ProcessId
    $effectiveStart = $StartTimeUtc
    if (-not (Test-RecordedProcess -ProcessId $effectivePid -StartTimeUtc $effectiveStart)) {
        if ($listeners.Count -ne 1) {
            throw "Refusing to recover $Name because its runtime has $($listeners.Count) listening processes."
        }
        $effectivePid = [int]$listeners[0]
        $effectiveStart = Get-ProcessStartTimeUtc -Process (Get-Process -Id $effectivePid)
    }
    Stop-RecordedProcessTree -Name $Name -ProcessId $effectivePid -StartTimeUtc $effectiveStart | Out-Null
    Wait-ForPortRelease -Port $Port
}

function Wait-ForService {
    param(
        [string]$Name,
        [Diagnostics.Process]$Process,
        [int]$Port,
        [string]$IdentityUrl,
        [string]$ExpectedService
    )
    for ($attempt = 0; $attempt -lt 120; $attempt++) {
        $identity = Get-HttpServiceIdentity -Url $IdentityUrl
        if ($null -ne $identity -and $identity.service -eq $ExpectedService -and $identity.runtimeId -eq $runtimeId) {
            $listeners = @(Get-ListeningProcessIds -Port $Port)
            $ownerPid = if ($listeners -contains $Process.Id) { $Process.Id } else { $listeners[0] }
            return Get-Process -Id ([int]$ownerPid)
        }
        Start-Sleep -Milliseconds 250
    }
    throw "$Name did not become ready. Check logs in $runtimeDir."
}

function Import-CoordinatorEnvironment([string]$Path) {
    $mapping = @{
        "LLM_PROVIDER" = "COORDINATOR_LLM_PROVIDER"
        "LLM_API_KEY" = "COORDINATOR_OPENAI_API_KEY"
        "LLM_BASE_URL" = "COORDINATOR_OPENAI_BASE_URL"
        "LLM_MODEL" = "COORDINATOR_OPENAI_MODEL"
        "COORDINATOR_LLM_PROVIDER" = "COORDINATOR_LLM_PROVIDER"
        "COORDINATOR_OPENAI_API_KEY" = "COORDINATOR_OPENAI_API_KEY"
        "COORDINATOR_OPENAI_BASE_URL" = "COORDINATOR_OPENAI_BASE_URL"
        "COORDINATOR_OPENAI_MODEL" = "COORDINATOR_OPENAI_MODEL"
        "COORDINATOR_OPENAI_API_MODE" = "COORDINATOR_OPENAI_API_MODE"
        "COORDINATOR_OPENAI_TIMEOUT_SECONDS" = "COORDINATOR_OPENAI_TIMEOUT_SECONDS"
        "COORDINATOR_OPENAI_MAX_RETRIES" = "COORDINATOR_OPENAI_MAX_RETRIES"
        "COORDINATOR_OPENAI_MAX_OUTPUT_TOKENS" = "COORDINATOR_OPENAI_MAX_OUTPUT_TOKENS"
    }
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -notmatch '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$') { continue }
        $sourceName = $matches[1]
        if (-not $mapping.ContainsKey($sourceName)) { continue }
        $value = $matches[2].Trim()
        if ((($value.StartsWith('"')) -and $value.EndsWith('"')) -or (($value.StartsWith("'")) -and $value.EndsWith("'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        [Environment]::SetEnvironmentVariable([string]$mapping[$sourceName], $value, "Process")
    }
}

$python = @(
    (Join-Path $projectRoot ".venv\Scripts\python.exe"),
    (Join-Path $projectRoot "apps\api\.venv\Scripts\python.exe")
) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
$node = (Get-Command node.exe -ErrorAction Stop).Source
$apiDir = Join-Path $projectRoot "apps\api"
$webDir = Join-Path $projectRoot "apps\web"
$coordinatorDir = Join-Path $projectRoot "services\coordinator-agent"
$nextCli = Join-Path $webDir "node_modules\next\dist\bin\next"
if (-not $python -or -not (Test-Path -LiteralPath $nextCli) -or -not (Test-Path -LiteralPath (Join-Path $requesterDir "src\server.js")) -or -not (Test-Path -LiteralPath $requesterConfig)) {
    throw "The Python or Web runtime is missing."
}

if ($experimentState.requesterPid -and $experimentState.requesterStartTimeUtc) {
    Stop-OwnedHttpService -Name "Local Requester" -ProcessId $experimentState.requesterPid `
        -StartTimeUtc $experimentState.requesterStartTimeUtc -Port $requesterPort `
        -IdentityUrl "http://127.0.0.1:$requesterPort/health" `
        -ExpectedService "local-requester-wallet-service"
}
Stop-OwnedHttpService -Name "Coordinator Agent" -ProcessId $experimentState.coordinatorPid `
    -StartTimeUtc $experimentState.coordinatorStartTimeUtc -Port $coordinatorPort `
    -IdentityUrl "http://127.0.0.1:$coordinatorPort/health" `
    -ExpectedService "agent-crosschain-platform-coordinator-agent"
Stop-OwnedHttpService -Name "Next.js" -ProcessId $platformState.webPid `
    -StartTimeUtc $platformState.webStartTimeUtc -Port $webPort `
    -IdentityUrl "http://127.0.0.1:$webPort/api/runtime-health" `
    -ExpectedService "agent-crosschain-platform-web"
Stop-OwnedHttpService -Name "FastAPI" -ProcessId $platformState.apiPid `
    -StartTimeUtc $platformState.apiStartTimeUtc -Port $apiPort `
    -IdentityUrl "http://127.0.0.1:$apiPort/api/v1/health" `
    -ExpectedService "agent-crosschain-platform-api"
if (Test-RecordedProcess -ProcessId $platformState.protocolPid -StartTimeUtc $platformState.protocolStartTimeUtc) {
    Stop-RecordedProcessTree -Name "Protocol Worker" -ProcessId $platformState.protocolPid `
        -StartTimeUtc $platformState.protocolStartTimeUtc | Out-Null
}

$nextCache = [IO.Path]::GetFullPath((Join-Path $webDir ".next"))
if (-not $nextCache.StartsWith($webDir + "\", [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to remove a Web cache outside the Web directory."
}
if (Test-Path -LiteralPath $nextCache) {
    Remove-Item -LiteralPath $nextCache -Recurse -Force
}

$env:APP_ENV = "development"
$env:DATABASE_URL = "sqlite:///$((Join-Path $runtimeDir 'agent-market.db').Replace('\', '/'))"
$env:CORS_ORIGINS = "http://localhost:$webPort,http://127.0.0.1:$webPort"
$env:LOCAL_PROTOCOL_RUNTIME_DIR = $runtimeDir
$env:LOCAL_PLATFORM_RUNTIME_ID = $runtimeId
$env:AGENT_RUNNER_BASE_URL = "http://127.0.0.1:$runnerPort"
$env:AGENT_RUNNER_API_TOKEN = if ($env:AGENT_RUNNER_API_TOKEN) { $env:AGENT_RUNNER_API_TOKEN } else { "local-experiment-runner-token" }
$env:AGENT_RUNNER_ENDPOINTS_JSON = (@{
    "workflow-research-b" = "http://127.0.0.1:8111"
    "workflow-verification-c" = "http://127.0.0.1:8112"
    "workflow-verification-d" = "http://127.0.0.1:8113"
} | ConvertTo-Json -Compress)
$env:AUTO_INVOKE_AGENTS = "true"
$env:REQUIRE_SIGNED_AGENT_RESULTS = "true"
$env:AGENT_INVOCATION_TIMEOUT_SECONDS = if ($env:AGENT_INVOCATION_TIMEOUT_SECONDS) { $env:AGENT_INVOCATION_TIMEOUT_SECONDS } else { "2100" }
$env:AGENT_TASK_DEADLINE_SECONDS = if ($env:AGENT_TASK_DEADLINE_SECONDS) { $env:AGENT_TASK_DEADLINE_SECONDS } else { "2400" }
$env:AGENT_TIMEOUT_MAX_ATTEMPTS_PER_AGENT = if ($env:AGENT_TIMEOUT_MAX_ATTEMPTS_PER_AGENT) { $env:AGENT_TIMEOUT_MAX_ATTEMPTS_PER_AGENT } else { "2" }
$env:LOCAL_CHAIN_A_RPC_URL = "http://127.0.0.1:8545"
$env:LOCAL_CHAIN_B_RPC_URL = "http://127.0.0.1:8546"
$env:LOCAL_CHAIN_C_RPC_URL = "http://127.0.0.1:8547"
$env:LOCAL_CHAIN_D_RPC_URL = "http://127.0.0.1:8548"
$env:CHAIN_A_RPC_URL = $env:LOCAL_CHAIN_A_RPC_URL
$env:CHAIN_B_RPC_URL = $env:LOCAL_CHAIN_B_RPC_URL
$env:CHAIN_C_RPC_URL = $env:LOCAL_CHAIN_C_RPC_URL
$env:CHAIN_D_RPC_URL = $env:LOCAL_CHAIN_D_RPC_URL
$env:API_URL = "http://127.0.0.1:$apiPort"
$env:LOCAL_FAUCET_ALLOW_REPEAT = "true"
$env:MATCH_ALLOW_SEEDED_LOCAL_AGENTS = "false"
$env:AGENT_MIN_GAS_BALANCE_WEI = if ($env:AGENT_MIN_GAS_BALANCE_WEI) { $env:AGENT_MIN_GAS_BALANCE_WEI } else { "10000000000000000" }
$env:LOCAL_AGENT_GAS_AUTO_TOP_UP = if ($env:LOCAL_AGENT_GAS_AUTO_TOP_UP) { $env:LOCAL_AGENT_GAS_AUTO_TOP_UP } else { "true" }
$env:LOCAL_AGENT_GAS_TOP_UP_WEI = if ($env:LOCAL_AGENT_GAS_TOP_UP_WEI) { $env:LOCAL_AGENT_GAS_TOP_UP_WEI } else { "1000000000000000000" }
$env:LOCAL_AGENT_GAS_REFRESH_ON_HEARTBEAT = if ($env:LOCAL_AGENT_GAS_REFRESH_ON_HEARTBEAT) { $env:LOCAL_AGENT_GAS_REFRESH_ON_HEARTBEAT } else { "true" }

$apiLauncher = Start-Process -FilePath $python `
    -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$apiPort") `
    -WorkingDirectory $apiDir -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $runtimeDir "api.stdout.log") `
    -RedirectStandardError (Join-Path $runtimeDir "api.stderr.log")
$apiProcess = Wait-ForService -Name "FastAPI" -Process $apiLauncher -Port $apiPort `
    -IdentityUrl "http://127.0.0.1:$apiPort/api/v1/health" `
    -ExpectedService "agent-crosschain-platform-api"

if ($env:LOCAL_PROTOCOL_USE_CONFIGURED_RELAYER -notin @("1", "true", "TRUE", "yes", "YES")) {
    Remove-Item Env:\PROTOCOL_RELAYER_PRIVATE_KEY -ErrorAction SilentlyContinue
}
$protocolProcess = Start-Process -FilePath $node `
    -ArgumentList @("services\chain-worker\src\protocol-index.ts") `
    -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $runtimeDir "protocol-worker.stdout.log") `
    -RedirectStandardError (Join-Path $runtimeDir "protocol-worker.stderr.log")
Start-Sleep -Seconds 2
if ($protocolProcess.HasExited) {
    throw "Protocol Worker did not restart. Check $runtimeDir\protocol-worker.stderr.log."
}

$env:NEXT_PUBLIC_API_URL = "http://127.0.0.1:$apiPort"
$env:API_URL = $env:NEXT_PUBLIC_API_URL
$env:COORDINATOR_URL = "http://127.0.0.1:$coordinatorPort"
$env:COORDINATOR_API_TOKEN = if ($env:COORDINATOR_API_TOKEN) { $env:COORDINATOR_API_TOKEN } else { "local-experiment-coordinator-token" }
$env:LOCAL_REQUESTER_API_TOKEN = $requesterToken
$env:LOCAL_REQUESTER_URL = "http://127.0.0.1:$requesterPort"
$env:NEXT_PUBLIC_ENABLE_MOCK_FALLBACK = "false"
$webLauncher = Start-Process -FilePath $node `
    -ArgumentList @("`"$nextCli`"", "dev", "--hostname", "127.0.0.1", "--port", "$webPort") `
    -WorkingDirectory $webDir -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $runtimeDir "web.stdout.log") `
    -RedirectStandardError (Join-Path $runtimeDir "web.stderr.log")
$webProcess = Wait-ForService -Name "Next.js" -Process $webLauncher -Port $webPort `
    -IdentityUrl "http://127.0.0.1:$webPort/api/runtime-health" `
    -ExpectedService "agent-crosschain-platform-web"

$coordinatorEnv = Join-Path $runtimeDir "secrets\coordinator-openai.env"
if (Test-Path -LiteralPath $coordinatorEnv) { Import-CoordinatorEnvironment -Path $coordinatorEnv }
$env:COORDINATOR_API_TOKEN = if ($env:COORDINATOR_API_TOKEN) { $env:COORDINATOR_API_TOKEN } else { "local-experiment-coordinator-token" }
$env:COORDINATOR_PLATFORM_URL = "http://127.0.0.1:$apiPort"
$env:COORDINATOR_STATE_PATH = Join-Path $runtimeDir "coordinator-conversations.json"
$env:COORDINATOR_PLATFORM_REGISTRATION_PATH = Join-Path $runtimeDir "coordinator-platform-registration.json"
$env:COORDINATOR_AGENT_KEY = if ($env:COORDINATOR_AGENT_KEY) { $env:COORDINATOR_AGENT_KEY } else { "workflow-coordinator-agent" }
$env:COORDINATOR_AGENT_DID = if ($env:COORDINATOR_AGENT_DID) { $env:COORDINATOR_AGENT_DID } else { "did:local:workflow-coordinator-agent" }
$env:DEV_IDENTITY_SEED = if ($env:DEV_IDENTITY_SEED) { $env:DEV_IDENTITY_SEED } else { "agent-market-demo" }
$env:COORDINATOR_ALLOW_INSECURE_LOCAL = "false"
$env:COORDINATOR_CORS_ORIGINS = "http://127.0.0.1:$webPort,http://localhost:$webPort"
$coordinatorLauncher = Start-Process -FilePath $python `
    -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$coordinatorPort") `
    -WorkingDirectory $coordinatorDir -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $runtimeDir "coordinator.stdout.log") `
    -RedirectStandardError (Join-Path $runtimeDir "coordinator.stderr.log")
$coordinatorProcess = Wait-ForService -Name "Coordinator Agent" -Process $coordinatorLauncher -Port $coordinatorPort `
    -IdentityUrl "http://127.0.0.1:$coordinatorPort/health" `
    -ExpectedService "agent-crosschain-platform-coordinator-agent"

$requesterListeners = @(Get-ListeningProcessIds -Port $requesterPort)
if ($requesterListeners.Count -gt 0) {
    throw "Local Requester port $requesterPort is already in use."
}
$env:REQUESTER_SERVICE_PORT = "$requesterPort"
$env:REQUESTER_CONFIG = $requesterConfig
$requesterLauncher = Start-Process -FilePath $node `
    -ArgumentList @("src\server.js") `
    -WorkingDirectory $requesterDir -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $runtimeDir "requester.stdout.log") `
    -RedirectStandardError (Join-Path $runtimeDir "requester.stderr.log")
$requesterProcess = Wait-ForService -Name "Local Requester" -Process $requesterLauncher -Port $requesterPort `
    -IdentityUrl "http://127.0.0.1:$requesterPort/health" `
    -ExpectedService "local-requester-wallet-service"

$platformState.apiPid = $apiProcess.Id
$platformState.apiStartTimeUtc = Get-ProcessStartTimeUtc -Process $apiProcess
$platformState.webPid = $webProcess.Id
$platformState.webStartTimeUtc = Get-ProcessStartTimeUtc -Process $webProcess
$platformState.protocolPid = $protocolProcess.Id
$platformState.protocolStartTimeUtc = Get-ProcessStartTimeUtc -Process $protocolProcess
$experimentState.coordinatorPid = $coordinatorProcess.Id
$experimentState.coordinatorStartTimeUtc = Get-ProcessStartTimeUtc -Process $coordinatorProcess
$experimentState | Add-Member -NotePropertyName requesterPid -NotePropertyValue $requesterProcess.Id -Force
$experimentState | Add-Member -NotePropertyName requesterPort -NotePropertyValue $requesterPort -Force
$experimentState | Add-Member -NotePropertyName requesterStartTimeUtc -NotePropertyValue (Get-ProcessStartTimeUtc -Process $requesterProcess) -Force
$platformState | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $platformStateFile -Encoding utf8
$experimentState | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $experimentStateFile -Encoding utf8

Write-Host "Coordinator stack restarted without resetting chains, Agents, or the database."
Write-Host "Web: http://127.0.0.1:$webPort"
Write-Host "API: http://127.0.0.1:$apiPort/api/docs"
Write-Host "Coordinator: http://127.0.0.1:$coordinatorPort/docs"
Write-Host "Requester: http://127.0.0.1:$requesterPort/health"
