[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
# Some launchers inject both PATH and Path. Start-Process rejects duplicate keys.
$processPath = $env:PATH
Remove-Item Env:Path -ErrorAction SilentlyContinue
$env:Path = $processPath
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
. (Join-Path $PSScriptRoot "service-ownership.ps1")

$runtimeDir = if ($env:LOCAL_RUNTIME_DIR) {
    $runtimeCandidate = if ([System.IO.Path]::IsPathRooted($env:LOCAL_RUNTIME_DIR)) { $env:LOCAL_RUNTIME_DIR } else { Join-Path $projectRoot $env:LOCAL_RUNTIME_DIR }
    [System.IO.Path]::GetFullPath($runtimeCandidate)
} else {
    Join-Path $projectRoot ".runtime\local-v1"
}
$apiDir = Join-Path $projectRoot "apps\api"
$webDir = Join-Path $projectRoot "apps\web"
$runnerDir = Join-Path $projectRoot "services\agent-runner"
$signerDir = Join-Path $projectRoot "services\agent-signer"
$databasePath = if ($env:LOCAL_DATABASE_PATH) {
    $databaseCandidate = if ([System.IO.Path]::IsPathRooted($env:LOCAL_DATABASE_PATH)) { $env:LOCAL_DATABASE_PATH } else { Join-Path $projectRoot $env:LOCAL_DATABASE_PATH }
    [System.IO.Path]::GetFullPath($databaseCandidate)
} else {
    Join-Path $apiDir "data\agent_market.db"
}
$pythonCandidates = @(
    (Join-Path $apiDir ".venv\Scripts\python.exe"),
    (Join-Path $projectRoot ".venv\Scripts\python.exe"),
    (Join-Path $projectRoot ".venv-test\Scripts\python.exe")
)
$python = $pythonCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
$node = (Get-Command node.exe -ErrorAction SilentlyContinue).Source
$pnpm = (Get-Command pnpm.cmd -ErrorAction SilentlyContinue).Source
$hardhat = Join-Path $projectRoot "node_modules\.bin\hardhat.cmd"
$nextCli = Join-Path $webDir "node_modules\next\dist\bin\next"
$platformStateFile = Join-Path $runtimeDir "platform.json"
$skipWeb = $env:LOCAL_SKIP_WEB -in @("1", "true", "TRUE", "yes", "YES")
$apiPort = if ($env:LOCAL_API_PORT) { [int]$env:LOCAL_API_PORT } else { 8000 }
$webPort = if ($env:LOCAL_WEB_PORT) { [int]$env:LOCAL_WEB_PORT } else { 3000 }
$runnerPort = if ($env:LOCAL_AGENT_RUNNER_PORT) { [int]$env:LOCAL_AGENT_RUNNER_PORT } else { 8010 }
$apiUrl = "http://127.0.0.1:$apiPort"
$webUrl = "http://127.0.0.1:$webPort"
$runnerUrl = "http://127.0.0.1:$runnerPort"
$apiIdentityUrl = "$apiUrl/api/v1/health"
$webIdentityUrl = "$webUrl/api/runtime-health"
$runnerIdentityUrl = "$runnerUrl/health"
$previousState = if (Test-Path -LiteralPath $platformStateFile) {
    Get-Content -Raw -LiteralPath $platformStateFile | ConvertFrom-Json
} else { $null }

if (-not $python) { throw "Python environment is missing. Run scripts\setup-local.ps1 first." }
if (-not $node) { throw "Node.js is missing from PATH." }
if (-not $pnpm) { throw "pnpm.cmd is missing from PATH. Run scripts\setup-local.ps1 first." }
if (-not (Test-Path -LiteralPath $hardhat)) { throw "Hardhat dependencies are missing. Run scripts\setup-local.ps1 first." }
if (-not $skipWeb -and -not (Test-Path -LiteralPath $nextCli)) { throw "Web dependencies are missing. Run scripts\setup-local.ps1 first." }

& (Join-Path $PSScriptRoot "sync-python-dependencies.ps1") -ProjectRoot $projectRoot -PythonPath $python

function Test-SamePath([object]$Left, [string]$Right) {
    if ([string]::IsNullOrWhiteSpace([string]$Left)) { return $false }
    try {
        $leftPath = [System.IO.Path]::GetFullPath([string]$Left).TrimEnd('\')
        $rightPath = [System.IO.Path]::GetFullPath($Right).TrimEnd('\')
        return $leftPath.Equals($rightPath, [StringComparison]::OrdinalIgnoreCase)
    }
    catch { return $false }
}

$stateMatchesRuntime = (
    (Get-StateValue -State $previousState -Name "schemaVersion") -in @(2, 3) -and
    (Test-SamePath (Get-StateValue -State $previousState -Name "projectRoot") $projectRoot) -and
    (Test-SamePath (Get-StateValue -State $previousState -Name "runtimeDir") $runtimeDir) -and
    -not [string]::IsNullOrWhiteSpace([string](Get-StateValue -State $previousState -Name "runtimeId"))
)
$runtimeId = if ($stateMatchesRuntime) {
    [string](Get-StateValue -State $previousState -Name "runtimeId")
} else {
    [guid]::NewGuid().ToString()
}

New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path $databasePath) | Out-Null
$env:APP_ENV = "development"
$env:DATABASE_URL = "sqlite:///$($databasePath.Replace('\', '/'))"
$env:CORS_ORIGINS = "http://localhost:$webPort,$webUrl"
$env:NEXT_PUBLIC_API_URL = $apiUrl
$env:API_URL = $apiUrl
$env:LOCAL_PROTOCOL_RUNTIME_DIR = $runtimeDir
$env:LOCAL_PLATFORM_RUNTIME_ID = $runtimeId
$env:AGENT_RUNNER_BASE_URL = $runnerUrl
$env:AGENT_RUNNER_API_TOKEN = if ($env:AGENT_RUNNER_API_TOKEN) { $env:AGENT_RUNNER_API_TOKEN } else { "local-v1-agent-runner-token" }
$env:AGENT_RUNNER_ALLOW_INSECURE_LOCAL = "false"
$env:AGENT_WALLET_NONCE_STORE_PATH = Join-Path $runtimeDir "agent-wallet-nonces.json"
$env:AGENT_RUNNER_DATABASE_URL = if ($env:AGENT_RUNNER_DATABASE_URL) { $env:AGENT_RUNNER_DATABASE_URL } else { "sqlite:///$((Join-Path $runtimeDir 'agent-runner.db').Replace('\', '/'))" }
$env:AGENT_SIGNER_DATABASE_URL = if ($env:AGENT_SIGNER_DATABASE_URL) { $env:AGENT_SIGNER_DATABASE_URL } else { "sqlite:///$((Join-Path $runtimeDir 'agent-signer.db').Replace('\', '/'))" }
$env:DEV_IDENTITY_SEED = if ($env:DEV_IDENTITY_SEED) { $env:DEV_IDENTITY_SEED } else { "agent-market-demo" }
$env:AUTO_INVOKE_AGENTS = if ($env:AUTO_INVOKE_AGENTS) { $env:AUTO_INVOKE_AGENTS } else { "true" }
$env:REQUIRE_SIGNED_AGENT_RESULTS = "true"
$env:LLM_PROVIDER = if ($env:LLM_PROVIDER) { $env:LLM_PROVIDER } elseif ($env:DEEPSEEK_API_KEY) { "deepseek" } else { "mock" }
$env:NEXT_PUBLIC_ENABLE_MOCK_FALLBACK = "false"
$env:LOCAL_CHAIN_A_RPC_URL = if ($env:LOCAL_CHAIN_A_RPC_URL) { $env:LOCAL_CHAIN_A_RPC_URL } else { "http://127.0.0.1:8545" }
$env:LOCAL_CHAIN_B_RPC_URL = if ($env:LOCAL_CHAIN_B_RPC_URL) { $env:LOCAL_CHAIN_B_RPC_URL } else { "http://127.0.0.1:8546" }
$env:LOCAL_CHAIN_C_RPC_URL = if ($env:LOCAL_CHAIN_C_RPC_URL) { $env:LOCAL_CHAIN_C_RPC_URL } else { "http://127.0.0.1:8547" }
$env:LOCAL_CHAIN_D_RPC_URL = if ($env:LOCAL_CHAIN_D_RPC_URL) { $env:LOCAL_CHAIN_D_RPC_URL } else { "http://127.0.0.1:8548" }
$env:CHAIN_A_RPC_URL = $env:LOCAL_CHAIN_A_RPC_URL
$env:NEXT_PUBLIC_LOCAL_RPC_URL = $env:LOCAL_CHAIN_A_RPC_URL
$env:CHAIN_B_RPC_URL = $env:LOCAL_CHAIN_B_RPC_URL
$env:CHAIN_C_RPC_URL = $env:LOCAL_CHAIN_C_RPC_URL
$env:CHAIN_D_RPC_URL = $env:LOCAL_CHAIN_D_RPC_URL
$agentWalletKeys = if ($env:AGENT_HOME_WALLET_PRIVATE_KEYS_JSON) {
    $env:AGENT_HOME_WALLET_PRIVATE_KEYS_JSON
} else {
    (@{
        "workflow-research-b" = "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a"
        "workflow-verification-c" = "0x7c852118294e51e653712a81e05800f419141751be58f605c371e15141b007a6"
        "workflow-verification-d" = "0x47e179ec197488593b187f80a00eb0da91f1b9d0b13f8733639f19c30a34926a"
    } | ConvertTo-Json -Compress)
}
$agentWalletPolicies = if ($env:AGENT_WALLET_POLICIES_JSON) {
    $env:AGENT_WALLET_POLICIES_JSON
} else {
    (@{
        "workflow-research-b" = @{
            chainId = 31338; homeWallet = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"; rpcUrl = $env:CHAIN_B_RPC_URL
            allowedArtifacts = @{ RemoteWorkBoard = @("submitResult") }; maxValueWei = "0"; maxGas = 500000; maxGasPriceWei = "100000000000"
        }
        "workflow-verification-c" = @{
            chainId = 31339; homeWallet = "0x90F79bf6EB2c4f870365E785982E1f101E93b906"; rpcUrl = $env:CHAIN_C_RPC_URL
            allowedArtifacts = @{ RemoteWorkBoard = @("submitResult") }; maxValueWei = "0"; maxGas = 500000; maxGasPriceWei = "100000000000"
        }
        "workflow-verification-d" = @{
            chainId = 31340; homeWallet = "0x15d34AAf54267DB7D7c367839AAf71A00a2C6A65"; rpcUrl = $env:CHAIN_D_RPC_URL
            allowedArtifacts = @{ RemoteWorkBoard = @("submitResult") }; maxValueWei = "0"; maxGas = 500000; maxGasPriceWei = "100000000000"
        }
    } | ConvertTo-Json -Compress -Depth 8)
}

function Stop-MovedRecordedService {
    param(
        [string]$Name,
        [string]$ExpectedService,
        [object]$RecordedPid,
        [object]$RecordedStartTimeUtc,
        [object]$RecordedPort,
        [int]$RequestedPort,
        [string]$IdentityPath
    )

    if (-not $stateMatchesRuntime -or $null -eq $RecordedPort -or [int]$RecordedPort -eq $RequestedPort) {
        return
    }
    if (-not (Test-RecordedProcess -ProcessId $RecordedPid -StartTimeUtc $RecordedStartTimeUtc)) {
        return
    }

    $oldPort = [int]$RecordedPort
    $listenerPids = @(Get-ListeningProcessIds -Port $oldPort)
    if ($listenerPids -contains [int]$RecordedPid) {
        $identity = Get-HttpServiceIdentity -Url "http://127.0.0.1:$oldPort$IdentityPath"
        if ($null -eq $identity -or $identity.service -ne $ExpectedService -or $identity.runtimeId -ne $runtimeId) {
            throw "Refusing to move $Name from port $oldPort because the recorded listener no longer returns this runtime's service identity."
        }
    }

    $stopped = Stop-RecordedProcessTree -Name $Name -ProcessId $RecordedPid -StartTimeUtc $RecordedStartTimeUtc
    if ($stopped -and $listenerPids -contains [int]$RecordedPid) {
        Wait-ForPortRelease -Port $oldPort
    }
    Write-Host "Stopped owned $Name on old port $oldPort before moving to port $RequestedPort."
}

Stop-MovedRecordedService `
    -Name "Agent Runner" `
    -ExpectedService "agent-crosschain-platform-agent-runner" `
    -RecordedPid (Get-StateValue -State $previousState -Name "runnerPid") `
    -RecordedStartTimeUtc (Get-StateValue -State $previousState -Name "runnerStartTimeUtc") `
    -RecordedPort (Get-StateValue -State $previousState -Name "runnerPort") `
    -RequestedPort $runnerPort `
    -IdentityPath "/health"

Stop-MovedRecordedService `
    -Name "FastAPI" `
    -ExpectedService "agent-crosschain-platform-api" `
    -RecordedPid (Get-StateValue -State $previousState -Name "apiPid") `
    -RecordedStartTimeUtc (Get-StateValue -State $previousState -Name "apiStartTimeUtc") `
    -RecordedPort (Get-StateValue -State $previousState -Name "apiPort") `
    -RequestedPort $apiPort `
    -IdentityPath "/api/v1/health"

Stop-MovedRecordedService `
    -Name "Next.js" `
    -ExpectedService "agent-crosschain-platform-web" `
    -RecordedPid (Get-StateValue -State $previousState -Name "webPid") `
    -RecordedStartTimeUtc (Get-StateValue -State $previousState -Name "webStartTimeUtc") `
    -RecordedPort (Get-StateValue -State $previousState -Name "webPort") `
    -RequestedPort $webPort `
    -IdentityPath "/api/runtime-health"

# Reject unrelated listeners before migrations, chain startup, or contract deployment.
$runnerProcess = Assert-OwnedHttpService `
    -Name "Agent Runner" `
    -Port $runnerPort `
    -IdentityUrl $runnerIdentityUrl `
    -ExpectedService "agent-crosschain-platform-agent-runner" `
    -ExpectedRuntimeId $runtimeId `
    -RecordedPid (Get-StateValue -State $previousState -Name "runnerPid") `
    -RecordedStartTimeUtc (Get-StateValue -State $previousState -Name "runnerStartTimeUtc") `
    -RecordedPort (Get-StateValue -State $previousState -Name "runnerPort") `
    -StateMatchesRuntime $stateMatchesRuntime

$apiProcess = Assert-OwnedHttpService `
    -Name "FastAPI" `
    -Port $apiPort `
    -IdentityUrl $apiIdentityUrl `
    -ExpectedService "agent-crosschain-platform-api" `
    -ExpectedRuntimeId $runtimeId `
    -RecordedPid (Get-StateValue -State $previousState -Name "apiPid") `
    -RecordedStartTimeUtc (Get-StateValue -State $previousState -Name "apiStartTimeUtc") `
    -RecordedPort (Get-StateValue -State $previousState -Name "apiPort") `
    -StateMatchesRuntime $stateMatchesRuntime

$webProcess = $null
if (-not $skipWeb) {
    $webProcess = Assert-OwnedHttpService `
        -Name "Next.js" `
        -Port $webPort `
        -IdentityUrl $webIdentityUrl `
        -ExpectedService "agent-crosschain-platform-web" `
        -ExpectedRuntimeId $runtimeId `
        -RecordedPid (Get-StateValue -State $previousState -Name "webPid") `
        -RecordedStartTimeUtc (Get-StateValue -State $previousState -Name "webStartTimeUtc") `
        -RecordedPort (Get-StateValue -State $previousState -Name "webPort") `
        -StateMatchesRuntime $stateMatchesRuntime
} elseif ($stateMatchesRuntime -and (Test-RecordedProcess `
    -ProcessId (Get-StateValue -State $previousState -Name "webPid") `
    -StartTimeUtc (Get-StateValue -State $previousState -Name "webStartTimeUtc"))) {
    $recordedWebPid = [int](Get-StateValue -State $previousState -Name "webPid")
    $recordedWebPort = [int](Get-StateValue -State $previousState -Name "webPort")
    $listeners = @(Get-ListeningProcessIds -Port $recordedWebPort)
    $identity = Get-HttpServiceIdentity -Url "http://127.0.0.1:$recordedWebPort/api/runtime-health"
    if ($listeners -contains $recordedWebPid -and $identity.service -eq "agent-crosschain-platform-web" -and $identity.runtimeId -eq $runtimeId) {
        $webProcess = Get-Process -Id $recordedWebPid
    }
}

$runnerConfigMaterial = "$runnerUrl|$($env:AGENT_RUNNER_API_TOKEN)|$($env:DEV_IDENTITY_SEED)|$($env:LLM_PROVIDER)|$($env:DEEPSEEK_MODEL)|$([bool]$env:DEEPSEEK_API_KEY)|$agentWalletKeys|$agentWalletPolicies"
$sha256 = [Security.Cryptography.SHA256]::Create()
try {
    $runnerConfigHash = -join ($sha256.ComputeHash([Text.Encoding]::UTF8.GetBytes($runnerConfigMaterial)) | ForEach-Object { $_.ToString("x2") })
}
finally { $sha256.Dispose() }
$runnerConfigChanged = $null -ne $runnerProcess -and (Get-StateValue -State $previousState -Name "runnerConfigHash") -ne $runnerConfigHash
if ($runnerConfigChanged) {
    $stopped = Stop-RecordedProcessTree `
        -Name "Agent Runner" `
        -ProcessId (Get-StateValue -State $previousState -Name "runnerPid") `
        -StartTimeUtc (Get-StateValue -State $previousState -Name "runnerStartTimeUtc")
    if ($stopped) { Wait-ForPortRelease -Port $runnerPort }
    $runnerProcess = $null
}

$apiConfigChanged = $null -ne $apiProcess -and (
    -not (Test-SamePath (Get-StateValue -State $previousState -Name "databasePath") $databasePath) -or
    (Get-StateValue -State $previousState -Name "apiAllowedWebPort") -ne $webPort -or
    (Get-StateValue -State $previousState -Name "apiRunnerUrl") -ne $runnerUrl -or
    (Get-StateValue -State $previousState -Name "runnerConfigHash") -ne $runnerConfigHash
)
if ($apiConfigChanged) {
    $stopped = Stop-RecordedProcessTree `
        -Name "FastAPI" `
        -ProcessId (Get-StateValue -State $previousState -Name "apiPid") `
        -StartTimeUtc (Get-StateValue -State $previousState -Name "apiStartTimeUtc")
    if ($stopped) { Wait-ForPortRelease -Port $apiPort }
    $apiProcess = $null
}

$webConfigChanged = $null -ne $webProcess -and (Get-StateValue -State $previousState -Name "webApiUrl") -ne $apiUrl
if ($webConfigChanged) {
    $stopped = Stop-RecordedProcessTree `
        -Name "Next.js" `
        -ProcessId (Get-StateValue -State $previousState -Name "webPid") `
        -StartTimeUtc (Get-StateValue -State $previousState -Name "webStartTimeUtc")
    if ($stopped) { Wait-ForPortRelease -Port ([int](Get-StateValue -State $previousState -Name "webPort")) }
    $webProcess = $null
}

Push-Location $projectRoot
try {
    & $hardhat compile
    if ($LASTEXITCODE -ne 0) { throw "Smart contract compilation failed" }
    & $python -m alembic -c alembic.ini upgrade head
    if ($LASTEXITCODE -ne 0) { throw "Database migration failed" }
}
finally { Pop-Location }

Push-Location $runnerDir
try {
    & $python -m alembic -c alembic.ini upgrade head
    if ($LASTEXITCODE -ne 0) { throw "Agent Runner database migration failed" }
}
finally { Pop-Location }

Push-Location $signerDir
try {
    & $python -m alembic -c alembic.ini upgrade head
    if ($LASTEXITCODE -ne 0) { throw "Agent Signer database migration failed" }
}
finally { Pop-Location }

& (Join-Path $PSScriptRoot "start-four-chains.ps1") -Quiet

function Wait-ForOwnedHttpService {
    param(
        [string]$Name,
        [System.Diagnostics.Process]$Process,
        [int]$Port,
        [string]$IdentityUrl,
        [string]$ExpectedService
    )

    for ($attempt = 0; $attempt -lt 120; $attempt++) {
        $listenerPids = @(Get-ListeningProcessIds -Port $Port)
        $identity = if ($listenerPids.Count -gt 0) { Get-HttpServiceIdentity -Url $IdentityUrl } else { $null }
        if ($null -ne $identity -and $identity.service -eq $ExpectedService -and $identity.runtimeId -eq $runtimeId) {
            $ownerPid = if ($listenerPids -contains $Process.Id) { $Process.Id } else { $listenerPids[0] }
            return (Get-Process -Id ([int]$ownerPid))
        }
        Start-Sleep -Milliseconds 250
    }
    throw "$Name did not become ready with the expected runtime identity. Check $runtimeDir logs."
}

if ($null -eq $runnerProcess) {
    $env:AGENT_HOME_WALLET_PRIVATE_KEYS_JSON = $agentWalletKeys
    $env:AGENT_WALLET_POLICIES_JSON = $agentWalletPolicies
    $runnerLauncher = Start-Process -FilePath $python `
        -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$runnerPort") `
        -WorkingDirectory $runnerDir -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $runtimeDir "agent-runner.stdout.log") `
        -RedirectStandardError (Join-Path $runtimeDir "agent-runner.stderr.log")
    $runnerProcess = Wait-ForOwnedHttpService `
        -Name "Agent Runner" `
        -Process $runnerLauncher `
        -Port $runnerPort `
        -IdentityUrl $runnerIdentityUrl `
        -ExpectedService "agent-crosschain-platform-agent-runner"
}

# Agent wallet secrets belong only to the Runner child process. The API and
# Protocol Worker launched below must never inherit them.
Remove-Item Env:\AGENT_HOME_WALLET_PRIVATE_KEYS_JSON -ErrorAction SilentlyContinue
Remove-Item Env:\AGENT_WALLET_POLICIES_JSON -ErrorAction SilentlyContinue

if ($null -eq $apiProcess) {
    $apiLauncher = Start-Process -FilePath $python `
        -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$apiPort") `
        -WorkingDirectory $apiDir -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $runtimeDir "api.stdout.log") `
        -RedirectStandardError (Join-Path $runtimeDir "api.stderr.log")
    $apiProcess = Wait-ForOwnedHttpService `
        -Name "FastAPI" `
        -Process $apiLauncher `
        -Port $apiPort `
        -IdentityUrl $apiIdentityUrl `
        -ExpectedService "agent-crosschain-platform-api"
}

if (-not $skipWeb -and $null -eq $webProcess) {
    $webLauncher = Start-Process -FilePath $node `
        -ArgumentList @("`"$nextCli`"", "dev", "--hostname", "127.0.0.1", "--port", "$webPort") `
        -WorkingDirectory $webDir -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $runtimeDir "web.stdout.log") `
        -RedirectStandardError (Join-Path $runtimeDir "web.stderr.log")
    $webProcess = Wait-ForOwnedHttpService `
        -Name "Next.js" `
        -Process $webLauncher `
        -Port $webPort `
        -IdentityUrl $webIdentityUrl `
        -ExpectedService "agent-crosschain-platform-web"
}

$protocolProcess = $null
$recordedProtocolPid = Get-StateValue -State $previousState -Name "protocolPid"
$recordedProtocolStart = Get-StateValue -State $previousState -Name "protocolStartTimeUtc"
if ($stateMatchesRuntime -and (Test-RecordedProcess -ProcessId $recordedProtocolPid -StartTimeUtc $recordedProtocolStart)) {
    $protocolProcess = Get-Process -Id ([int]$recordedProtocolPid)
}
if ($null -eq $protocolProcess) {
    $protocolProcess = Start-Process -FilePath $node `
        -ArgumentList @("services\chain-worker\src\protocol-index.ts") `
        -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $runtimeDir "protocol-worker.stdout.log") `
        -RedirectStandardError (Join-Path $runtimeDir "protocol-worker.stderr.log")
    Start-Sleep -Seconds 2
    if ($protocolProcess.HasExited) {
        throw "Protocol Worker did not start. Check $runtimeDir\protocol-worker.stderr.log."
    }
}

$state = [ordered]@{
    schemaVersion = 3
    projectRoot = $projectRoot
    runtimeDir = $runtimeDir
    runtimeId = $runtimeId
    databasePath = $databasePath
    runnerPid = $runnerProcess.Id
    runnerPort = $runnerPort
    runnerStartTimeUtc = Get-ProcessStartTimeUtc -Process $runnerProcess
    runnerConfigHash = $runnerConfigHash
    apiPid = $apiProcess.Id
    apiPort = $apiPort
    apiStartTimeUtc = Get-ProcessStartTimeUtc -Process $apiProcess
    apiAllowedWebPort = $webPort
    apiRunnerUrl = $runnerUrl
    webPid = if ($null -ne $webProcess) { $webProcess.Id } else { $null }
    webPort = if ($null -ne $webProcess) { $webPort } else { $null }
    webStartTimeUtc = if ($null -ne $webProcess) { Get-ProcessStartTimeUtc -Process $webProcess } else { $null }
    webApiUrl = if ($null -ne $webProcess) { $apiUrl } else { $null }
    protocolPid = $protocolProcess.Id
    protocolStartTimeUtc = Get-ProcessStartTimeUtc -Process $protocolProcess
}
$state | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $platformStateFile -Encoding utf8

Write-Host "Local V1 platform is ready:"
if (-not $skipWeb) { Write-Host "  Web:      $webUrl" }
Write-Host "  API docs: $apiUrl/api/docs"
Write-Host "  Agents:   $runnerUrl/agents"
Write-Host "  Runtime:  $runtimeId"
Write-Host "  RPC A:    $env:LOCAL_CHAIN_A_RPC_URL"
Write-Host "  RPC B:    $env:LOCAL_CHAIN_B_RPC_URL"
Write-Host "  RPC C:    $env:LOCAL_CHAIN_C_RPC_URL"
Write-Host "  RPC D:    $env:LOCAL_CHAIN_D_RPC_URL"
Write-Host "  Worker:   protocol-worker PID $($protocolProcess.Id)"
