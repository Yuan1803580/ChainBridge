[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
. (Join-Path $PSScriptRoot "service-ownership.ps1")

# Windows environment variables are case-insensitive while Start-Process builds a
# case-sensitive dictionary. Duplicated keys (PATH/Path, HTTPS_PROXY/https_proxy)
# make Start-Process fail before any service is started.
$processPath = $env:PATH
Remove-Item Env:Path -ErrorAction SilentlyContinue
$env:Path = $processPath
# Every readiness probe and service call in this stack targets 127.0.0.1.
# PowerShell 5.1 Invoke-RestMethod reads HTTP_PROXY but ignores NO_PROXY, so a
# machine-wide proxy sends those probes to the proxy instead of the service and
# every service looks dead even though it started fine. Local traffic never needs
# a proxy, so clear the variables for this run rather than relying on NO_PROXY.
foreach ($name in @(
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy"
)) {
    Remove-Item "Env:$name" -ErrorAction SilentlyContinue
}

$env:LOCAL_RUNTIME_DIR = if ($env:LOCAL_RUNTIME_DIR) { $env:LOCAL_RUNTIME_DIR } else { ".runtime\local-experiment" }
$runtimeDir = if ([System.IO.Path]::IsPathRooted($env:LOCAL_RUNTIME_DIR)) {
    [System.IO.Path]::GetFullPath($env:LOCAL_RUNTIME_DIR)
} else {
    [System.IO.Path]::GetFullPath((Join-Path $projectRoot $env:LOCAL_RUNTIME_DIR))
}
$experimentStateFile = Join-Path $runtimeDir "experiment.json"

function Import-CoordinatorLlmEnvironment([string]$Path) {
    if (-not $Path) { return }
    $resolvedPath = if ([System.IO.Path]::IsPathRooted($Path)) {
        [System.IO.Path]::GetFullPath($Path)
    } else {
        [System.IO.Path]::GetFullPath((Join-Path $projectRoot $Path))
    }
    if (-not (Test-Path -LiteralPath $resolvedPath)) {
        throw "Coordinator LLM environment file is missing: $resolvedPath"
    }
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
    foreach ($line in Get-Content -LiteralPath $resolvedPath) {
        if ($line -notmatch '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$') { continue }
        $sourceName = $matches[1]
        if (-not $mapping.ContainsKey($sourceName)) { continue }
        $targetName = [string]$mapping[$sourceName]
        if ([Environment]::GetEnvironmentVariable($targetName, "Process")) { continue }
        $value = $matches[2].Trim()
        if (
            ($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))
        ) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        [Environment]::SetEnvironmentVariable($targetName, $value, "Process")
    }
}

$defaultCoordinatorLlmEnv = Join-Path $runtimeDir "secrets\coordinator-openai.env"
$coordinatorLlmEnv = if ($env:COORDINATOR_LLM_ENV_FILE) {
    $env:COORDINATOR_LLM_ENV_FILE
} elseif (Test-Path -LiteralPath $defaultCoordinatorLlmEnv) {
    $defaultCoordinatorLlmEnv
} else {
    ""
}
Import-CoordinatorLlmEnvironment -Path $coordinatorLlmEnv

# A local experiment always starts from services owned by this runtime. This
# prevents a stale Runner from silently serving another Agent's wallet.
if ((Test-Path -LiteralPath $experimentStateFile) -or (Test-Path -LiteralPath (Join-Path $runtimeDir "platform.json"))) {
    & (Join-Path $PSScriptRoot "stop-local-experiment.ps1")
}

$apiPort = if ($env:LOCAL_API_PORT) { [int]$env:LOCAL_API_PORT } else { 8200 }
$webPort = if ($env:LOCAL_WEB_PORT) { [int]$env:LOCAL_WEB_PORT } else { 3100 }
$coordinatorPort = if ($env:LOCAL_COORDINATOR_PORT) { [int]$env:LOCAL_COORDINATOR_PORT } else { 8300 }
$requesterPort = if ($env:LOCAL_REQUESTER_PORT) { [int]$env:LOCAL_REQUESTER_PORT } else { 8400 }
$runnerBPort = if ($env:LOCAL_RUNNER_B_PORT) { [int]$env:LOCAL_RUNNER_B_PORT } else { 8111 }
$runnerCPort = if ($env:LOCAL_RUNNER_C_PORT) { [int]$env:LOCAL_RUNNER_C_PORT } else { 8112 }
$runnerDPort = if ($env:LOCAL_RUNNER_D_PORT) { [int]$env:LOCAL_RUNNER_D_PORT } else { 8113 }
$runnerToken = if ($env:AGENT_RUNNER_API_TOKEN) { $env:AGENT_RUNNER_API_TOKEN } else { "local-experiment-runner-token" }
$coordinatorToken = if ($env:COORDINATOR_API_TOKEN) { $env:COORDINATOR_API_TOKEN } else { "local-experiment-coordinator-token" }
$requesterToken = if ($env:LOCAL_REQUESTER_API_TOKEN) { $env:LOCAL_REQUESTER_API_TOKEN } else { "local-experiment-requester-token" }
$requesterDir = if ($env:LOCAL_REQUESTER_DIR) { [IO.Path]::GetFullPath($env:LOCAL_REQUESTER_DIR) } else { Join-Path $projectRoot "tools\local-requester-client" }
$requesterConfig = if ($env:REQUESTER_CONFIG) { [IO.Path]::GetFullPath($env:REQUESTER_CONFIG) } else { Join-Path $requesterDir "config.json" }
foreach ($name in @("KIMI_API_KEY", "KIMI_MODEL", "KIMI_BASE_URL", "KIMI_TIMEOUT_SECONDS", "KIMI_MAX_RETRIES", "KIMI_MAX_TOKENS")) {
    Remove-Item "Env:\$name" -ErrorAction SilentlyContinue
}
$standardRunnerProvider = if ($env:LLM_PROVIDER -in @("mock", "deepseek")) {
    $env:LLM_PROVIDER
} elseif ($env:DEEPSEEK_API_KEY) {
    "deepseek"
} else {
    "mock"
}

$pythonCandidates = @(
    (Join-Path $projectRoot ".venv\Scripts\python.exe"),
    (Join-Path $projectRoot ".venv\bin\python.exe"),
    (Join-Path $projectRoot "apps\api\.venv\Scripts\python.exe"),
    (Join-Path $projectRoot "apps\api\.venv\bin\python.exe")
)
$python = $pythonCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $python) { throw "Python environment is missing. Run scripts\setup-local.ps1 first." }
$node = (Get-Command node.exe -ErrorAction Stop).Source
if (-not (Test-Path -LiteralPath (Join-Path $requesterDir "src\server.js")) -or -not (Test-Path -LiteralPath $requesterConfig)) {
    throw "Local Requester Client or its existing wallet configuration is missing: $requesterDir"
}
if (-not (Test-Path -LiteralPath (Join-Path $requesterDir "node_modules\ethers"))) {
    throw "Local Requester Client dependencies are missing. Run pnpm requester:install first."
}

$env:LOCAL_API_PORT = "$apiPort"
$env:LOCAL_WEB_PORT = "$webPort"
$env:LOCAL_AGENT_RUNNER_PORT = "$runnerBPort"
$env:LOCAL_DATABASE_PATH = Join-Path $runtimeDir "agent-market.db"
$env:AGENT_RUNNER_API_TOKEN = $runnerToken
$env:COORDINATOR_API_TOKEN = $coordinatorToken
$env:API_URL = "http://127.0.0.1:$apiPort"
$env:COORDINATOR_URL = "http://127.0.0.1:$coordinatorPort"
$env:COORDINATOR_PLATFORM_URL = $env:API_URL
$env:COORDINATOR_STATE_PATH = Join-Path $runtimeDir "coordinator-conversations.json"
$env:COORDINATOR_PLATFORM_REGISTRATION_PATH = Join-Path $runtimeDir "coordinator-platform-registration.json"
$env:COORDINATOR_AGENT_KEY = if ($env:COORDINATOR_AGENT_KEY) { $env:COORDINATOR_AGENT_KEY } else { "workflow-coordinator-agent" }
$env:COORDINATOR_AGENT_DID = if ($env:COORDINATOR_AGENT_DID) { $env:COORDINATOR_AGENT_DID } else { "did:local:workflow-coordinator-agent" }
$env:DEV_IDENTITY_SEED = if ($env:DEV_IDENTITY_SEED) { $env:DEV_IDENTITY_SEED } else { "agent-market-demo" }
$env:COORDINATOR_ALLOW_INSECURE_LOCAL = "false"
$env:LOCAL_REQUESTER_API_TOKEN = $requesterToken
$env:LOCAL_REQUESTER_URL = "http://127.0.0.1:$requesterPort"
$env:REQUESTER_SERVICE_PORT = "$requesterPort"
$env:REQUESTER_CONFIG = $requesterConfig
$env:LOCAL_FAUCET_ALLOW_REPEAT = "true"
# Seeded local Runner profiles (Agent-B1 / C1 / D1) are the execution Agents of
# this experiment unless an independent external Agent is onboarded. Set this to
# "false" to require external Agents only.
$env:MATCH_ALLOW_SEEDED_LOCAL_AGENTS = if ($env:MATCH_ALLOW_SEEDED_LOCAL_AGENTS) {
    $env:MATCH_ALLOW_SEEDED_LOCAL_AGENTS
} else {
    "true"
}
# The Runner and the Coordinator historically resolved their provider from
# different variables, so setting only LLM_PROVIDER switched the Runners and
# silently left the Coordinator on the deterministic mock interpreter. Both now
# honour the same variables, which keeps the whole stack on one provider.
$coordinatorProvider = "mock"
if ($env:COORDINATOR_LLM_PROVIDER) {
    $coordinatorProvider = $env:COORDINATOR_LLM_PROVIDER
} elseif ($env:COORDINATOR_OPENAI_API_KEY) {
    $coordinatorProvider = "openai_compatible"
} elseif ($env:DEEPSEEK_API_KEY) {
    $coordinatorProvider = "deepseek"
} elseif ($env:KIMI_API_KEY -or $env:LLM_PROVIDER -eq "kimi") {
    # Moonshot speaks the OpenAI protocol and the Coordinator has no dedicated
    # kimi interpreter, so route it through the OpenAI-compatible one.
    $coordinatorProvider = "openai_compatible"
    if (-not $env:COORDINATOR_OPENAI_BASE_URL) { $env:COORDINATOR_OPENAI_BASE_URL = "https://api.moonshot.cn/v1" }
    if (-not $env:COORDINATOR_OPENAI_API_KEY) { $env:COORDINATOR_OPENAI_API_KEY = $env:KIMI_API_KEY }
    if (-not $env:COORDINATOR_OPENAI_MODEL) {
        $kimiModel = if ($env:KIMI_MODEL) { $env:KIMI_MODEL } else { "kimi-k2.6" }
        $env:COORDINATOR_OPENAI_MODEL = $kimiModel
    }
} elseif ($env:LLM_PROVIDER -in @("deepseek", "openai", "openai-compatible", "openai_compatible")) {
    $coordinatorProvider = $env:LLM_PROVIDER
}
$env:COORDINATOR_LLM_PROVIDER = $coordinatorProvider

$runnerEndpoints = [ordered]@{
    "workflow-research-b" = "http://127.0.0.1:$runnerBPort"
    "workflow-verification-c" = "http://127.0.0.1:$runnerCPort"
    "workflow-verification-d" = "http://127.0.0.1:$runnerDPort"
}
$env:AGENT_RUNNER_ENDPOINTS_JSON = $runnerEndpoints | ConvertTo-Json -Compress

$agentSpecs = @(
    [ordered]@{
        Id = "runner-b"; Slug = "workflow-research-b"; Port = $runnerBPort; ChainId = 31338
        Provider = $standardRunnerProvider
        RpcUrl = if ($env:LOCAL_CHAIN_B_RPC_URL) { $env:LOCAL_CHAIN_B_RPC_URL } else { "http://127.0.0.1:8546" }
        HomeWallet = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"
        PrivateKey = "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a"
    },
    [ordered]@{
        Id = "runner-c"; Slug = "workflow-verification-c"; Port = $runnerCPort; ChainId = 31339
        Provider = $standardRunnerProvider
        RpcUrl = if ($env:LOCAL_CHAIN_C_RPC_URL) { $env:LOCAL_CHAIN_C_RPC_URL } else { "http://127.0.0.1:8547" }
        HomeWallet = "0x90F79bf6EB2c4f870365E785982E1f101E93b906"
        PrivateKey = "0x7c852118294e51e653712a81e05800f419141751be58f605c371e15141b007a6"
    },
    [ordered]@{
        Id = "runner-d"; Slug = "workflow-verification-d"; Port = $runnerDPort; ChainId = 31340
        Provider = $standardRunnerProvider
        RpcUrl = if ($env:LOCAL_CHAIN_D_RPC_URL) { $env:LOCAL_CHAIN_D_RPC_URL } else { "http://127.0.0.1:8548" }
        HomeWallet = "0x15d34AAf54267DB7D7c367839AAf71A00a2C6A65"
        PrivateKey = "0x47e179ec197488593b187f80a00eb0da91f1b9d0b13f8733639f19c30a34926a"
    }
)

function Set-RunnerEnvironment([object]$Spec) {
    $env:AGENT_RUNNER_ID = [string]$Spec.Id
    $env:AGENT_RUNNER_SLUGS = [string]$Spec.Slug
    $env:LLM_PROVIDER = [string]$Spec.Provider
    $env:AGENT_CHAT_DB_PATH = Join-Path $runtimeDir "$($Spec.Id)-chat.db"
    $env:AGENT_PLATFORM_REGISTRATION_STORE_PATH = Join-Path $runtimeDir "$($Spec.Id)-platform-registrations.json"
    $env:AGENT_KNOWLEDGE_PATHS = Join-Path $projectRoot "docs"
    Remove-Item Env:\AGENT_PLATFORM_REGISTRATIONS_JSON -ErrorAction SilentlyContinue
    if ([string]$Spec.PrivateKey) {
        $env:AGENT_HOME_WALLET_PRIVATE_KEYS_JSON = (@{ ([string]$Spec.Slug) = [string]$Spec.PrivateKey } | ConvertTo-Json -Compress)
        $env:AGENT_WALLET_POLICIES_JSON = (@{
            ([string]$Spec.Slug) = @{
                chainId = [int]$Spec.ChainId
                homeWallet = [string]$Spec.HomeWallet
                rpcUrl = [string]$Spec.RpcUrl
                allowedArtifacts = @{ RemoteWorkBoard = @("submitResult") }
                maxValueWei = "0"
                maxGas = 500000
                maxGasPriceWei = "100000000000"
            }
        } | ConvertTo-Json -Compress -Depth 8)
        $env:AGENT_WALLET_NONCE_STORE_PATH = Join-Path $runtimeDir "$($Spec.Id)-wallet-nonces.json"
    } else {
        Remove-Item Env:\AGENT_HOME_WALLET_PRIVATE_KEYS_JSON -ErrorAction SilentlyContinue
        Remove-Item Env:\AGENT_WALLET_POLICIES_JSON -ErrorAction SilentlyContinue
        Remove-Item Env:\AGENT_WALLET_NONCE_STORE_PATH -ErrorAction SilentlyContinue
    }
}

# Startup is not atomic. If any step below fails, the services already launched
# stay behind as orphans and block the next attempt on their ports — and because
# experiment.json never gets written, stop-local-experiment.ps1 cannot find them
# either. Wrap the launch so a failure rolls back whatever this run started.
$extraProcesses = @()
$coordinatorProcess = $null
$requesterProcess = $null

try {

# The existing hardened launcher owns Runner B, API, Web, Worker and all chains.
Set-RunnerEnvironment $agentSpecs[0]
& (Join-Path $PSScriptRoot "start-platform-v1.ps1")

$platformState = Get-Content -Raw -LiteralPath (Join-Path $runtimeDir "platform.json") | ConvertFrom-Json
$runtimeId = [string]$platformState.runtimeId
$runnerDir = Join-Path $projectRoot "services\agent-runner"
$coordinatorDir = Join-Path $projectRoot "services\coordinator-agent"
$env:LOCAL_PLATFORM_RUNTIME_ID = $runtimeId

function Assert-PortFree([int]$Port, [string]$Name) {
    $listeners = @(Get-ListeningProcessIds -Port $Port)
    if ($listeners.Count -gt 0) {
        throw "$Name port $Port is already owned by PID(s) $($listeners -join ', '). Stop that process or choose another LOCAL_*_PORT."
    }
}

# Invoke-RestMethod resolves its proxy from the WinHTTP system proxy instead of
# HTTP_PROXY. Every readiness probe below targets 127.0.0.1, and a system proxy
# would swallow them, so local calls must bypass it explicitly.
try {
    [System.Net.WebRequest]::DefaultWebProxy = New-Object System.Net.WebProxy
}
catch {
    [System.Net.WebRequest]::DefaultWebProxy = $null
}

function Wait-ForService([string]$Url, [int]$Port, [string]$ExpectedService, [string]$ExpectedRuntime, [string]$ExpectedRunnerId = "") {
    for ($attempt = 0; $attempt -lt 120; $attempt++) {
        try {
            $identity = Invoke-RestMethod -Uri $Url -TimeoutSec 1
            if ($identity.service -eq $ExpectedService -and $identity.runtimeId -eq $ExpectedRuntime) {
                if (-not $ExpectedRunnerId -or $identity.runnerId -eq $ExpectedRunnerId) {
                    $listenerPids = @(Get-ListeningProcessIds -Port $Port)
                    if ($listenerPids.Count -eq 1) { return (Get-Process -Id ([int]$listenerPids[0])) }
                }
            }
        }
        catch {}
        Start-Sleep -Milliseconds 250
    }
    throw "Service $ExpectedService did not become ready at $Url."
}

$extraProcesses = @()
foreach ($spec in @($agentSpecs | Select-Object -Skip 1)) {
    Assert-PortFree -Port ([int]$spec.Port) -Name ([string]$spec.Id)
    Set-RunnerEnvironment $spec
    $process = Start-Process -FilePath $python `
        -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$($spec.Port)") `
        -WorkingDirectory $runnerDir -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $runtimeDir "$($spec.Id).stdout.log") `
        -RedirectStandardError (Join-Path $runtimeDir "$($spec.Id).stderr.log")
    $ownedProcess = Wait-ForService -Url "http://127.0.0.1:$($spec.Port)/health" -Port ([int]$spec.Port) `
        -ExpectedService "agent-crosschain-platform-agent-runner" `
        -ExpectedRuntime $runtimeId -ExpectedRunnerId ([string]$spec.Id)
    $extraProcesses += [ordered]@{
        name = [string]$spec.Id
        pid = $ownedProcess.Id
        port = [int]$spec.Port
        startTimeUtc = Get-ProcessStartTimeUtc -Process $ownedProcess
    }
}

Remove-Item Env:\AGENT_HOME_WALLET_PRIVATE_KEYS_JSON -ErrorAction SilentlyContinue
Remove-Item Env:\AGENT_WALLET_POLICIES_JSON -ErrorAction SilentlyContinue
Remove-Item Env:\AGENT_RUNNER_SLUGS -ErrorAction SilentlyContinue
Remove-Item Env:\AGENT_RUNNER_ID -ErrorAction SilentlyContinue
Remove-Item Env:\AGENT_CHAT_DB_PATH -ErrorAction SilentlyContinue
Remove-Item Env:\AGENT_KNOWLEDGE_PATHS -ErrorAction SilentlyContinue
Remove-Item Env:\AGENT_PLATFORM_REGISTRATION_STORE_PATH -ErrorAction SilentlyContinue
Remove-Item Env:\AGENT_PLATFORM_REGISTRATIONS_JSON -ErrorAction SilentlyContinue
$env:LLM_PROVIDER = $standardRunnerProvider

Assert-PortFree -Port $coordinatorPort -Name "Coordinator Agent"
$coordinatorProcess = Start-Process -FilePath $python `
    -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$coordinatorPort") `
    -WorkingDirectory $coordinatorDir -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $runtimeDir "coordinator.stdout.log") `
    -RedirectStandardError (Join-Path $runtimeDir "coordinator.stderr.log")
$ownedCoordinatorProcess = Wait-ForService -Url "http://127.0.0.1:$coordinatorPort/health" -Port $coordinatorPort `
    -ExpectedService "agent-crosschain-platform-coordinator-agent" `
    -ExpectedRuntime $runtimeId

Assert-PortFree -Port $requesterPort -Name "Local Requester"
$requesterProcess = Start-Process -FilePath $node `
    -ArgumentList @("src\server.js") `
    -WorkingDirectory $requesterDir -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $runtimeDir "requester.stdout.log") `
    -RedirectStandardError (Join-Path $runtimeDir "requester.stderr.log")
$ownedRequesterProcess = Wait-ForService -Url "http://127.0.0.1:$requesterPort/health" -Port $requesterPort `
    -ExpectedService "local-requester-wallet-service" `
    -ExpectedRuntime $runtimeId

$state = [ordered]@{
    schemaVersion = 1
    projectRoot = $projectRoot
    runtimeDir = $runtimeDir
    runtimeId = $runtimeId
    coordinatorPid = $ownedCoordinatorProcess.Id
    coordinatorPort = $coordinatorPort
    coordinatorStartTimeUtc = Get-ProcessStartTimeUtc -Process $ownedCoordinatorProcess
    requesterPid = $ownedRequesterProcess.Id
    requesterPort = $requesterPort
    requesterStartTimeUtc = Get-ProcessStartTimeUtc -Process $ownedRequesterProcess
    extraRunners = $extraProcesses
}
$state | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $experimentStateFile -Encoding utf8

Write-Host "Local experiment topology is ready:"
Write-Host "  Web:         http://127.0.0.1:$webPort"
Write-Host "  API docs:    http://127.0.0.1:$apiPort/api/docs"
Write-Host "  Coordinator: http://127.0.0.1:$coordinatorPort/docs"
Write-Host "  Requester:   http://127.0.0.1:$requesterPort/health"
Write-Host "  Runner B:    http://127.0.0.1:$runnerBPort/agents"
Write-Host "  Runner C:    http://127.0.0.1:$runnerCPort/agents"
Write-Host "  Runner D:    http://127.0.0.1:$runnerDPort/agents"
Write-Host "  External Agents are started separately and registered through /onboard."

} catch {
    Write-Host ""
    Write-Host "Startup failed: $($_.Exception.Message)" -ForegroundColor Yellow
    Write-Host "Rolling back everything this run started ..." -ForegroundColor Yellow

    # Only stop PIDs this run created. A pre-existing listener on the same port
    # belongs to an earlier run and is left alone.
    foreach ($item in @($extraProcesses)) {
        if ($item -and $item.pid) {
            Stop-Process -Id ([int]$item.pid) -Force -ErrorAction SilentlyContinue
        }
    }
    if ($coordinatorProcess) {
        Stop-Process -Id $coordinatorProcess.Id -Force -ErrorAction SilentlyContinue
    }
    if ($requesterProcess) {
        Stop-Process -Id $requesterProcess.Id -Force -ErrorAction SilentlyContinue
    }

    # The hardened launcher owns Runner B, API, Web, Worker and all chains.
    try {
        & (Join-Path $PSScriptRoot "stop-platform-v1.ps1") | Out-Null
    } catch {
        Write-Host "  Platform stop also reported: $($_.Exception.Message)" -ForegroundColor Yellow
    }

    Remove-Item -LiteralPath $experimentStateFile -ErrorAction SilentlyContinue

    Write-Host ""
    Write-Host "Rollback complete — no orphan processes were left behind."
    Write-Host "Fix the cause above, then run pnpm local:experiment:start again." -ForegroundColor Yellow
    Write-Host ""
    throw
}
