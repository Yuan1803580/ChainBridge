[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("status", "stop", "start", "restart")]
    [string]$Action,

    [ValidateSet("B", "C", "D", "all")]
    [string]$Agent = "all",

    [string]$RuntimeDir = ".runtime\local-experiment"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
. (Join-Path $PSScriptRoot "service-ownership.ps1")

$runtimeDir = if ([IO.Path]::IsPathRooted($RuntimeDir)) {
    [IO.Path]::GetFullPath($RuntimeDir)
} else {
    [IO.Path]::GetFullPath((Join-Path $projectRoot $RuntimeDir))
}
$experimentStateFile = Join-Path $runtimeDir "experiment.json"
$platformStateFile = Join-Path $runtimeDir "platform.json"
if (-not (Test-Path -LiteralPath $experimentStateFile) -or -not (Test-Path -LiteralPath $platformStateFile)) {
    throw "Local experiment is not running. Start it with pnpm local:experiment:start first."
}

$experimentState = Get-Content -Raw -LiteralPath $experimentStateFile | ConvertFrom-Json
$platformState = Get-Content -Raw -LiteralPath $platformStateFile | ConvertFrom-Json
if ($experimentState.runtimeId -ne $platformState.runtimeId) {
    throw "Experiment and platform runtime identities do not match. Restart the local experiment."
}
$runtimeId = [string]$experimentState.runtimeId

$specs = [ordered]@{
    B = [ordered]@{
        Id = "runner-b"; Slug = "workflow-research-b"; Port = 8111; ChainId = 31338
        RpcUrl = "http://127.0.0.1:8546"
        HomeWallet = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"
        PrivateKey = "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a"
    }
    C = [ordered]@{
        Id = "runner-c"; Slug = "workflow-verification-c"; Port = 8112; ChainId = 31339
        RpcUrl = "http://127.0.0.1:8547"
        HomeWallet = "0x90F79bf6EB2c4f870365E785982E1f101E93b906"
        PrivateKey = "0x7c852118294e51e653712a81e05800f419141751be58f605c371e15141b007a6"
    }
    D = [ordered]@{
        Id = "runner-d"; Slug = "workflow-verification-d"; Port = 8113; ChainId = 31340
        RpcUrl = "http://127.0.0.1:8548"
        HomeWallet = "0x15d34AAf54267DB7D7c367839AAf71A00a2C6A65"
        PrivateKey = "0x47e179ec197488593b187f80a00eb0da91f1b9d0b13f8733639f19c30a34926a"
    }
}

if ($env:LOCAL_RUNNER_B_PORT) { $specs.B.Port = [int]$env:LOCAL_RUNNER_B_PORT }
if ($env:LOCAL_RUNNER_C_PORT) { $specs.C.Port = [int]$env:LOCAL_RUNNER_C_PORT }
if ($env:LOCAL_RUNNER_D_PORT) { $specs.D.Port = [int]$env:LOCAL_RUNNER_D_PORT }
if ($env:LOCAL_CHAIN_B_RPC_URL) { $specs.B.RpcUrl = $env:LOCAL_CHAIN_B_RPC_URL }
if ($env:LOCAL_CHAIN_C_RPC_URL) { $specs.C.RpcUrl = $env:LOCAL_CHAIN_C_RPC_URL }
if ($env:LOCAL_CHAIN_D_RPC_URL) { $specs.D.RpcUrl = $env:LOCAL_CHAIN_D_RPC_URL }

if ($platformState.runnerPort) { $specs.B.Port = [int]$platformState.runnerPort }
foreach ($record in @($experimentState.extraRunners)) {
    if ($record.name -eq "runner-c" -and $record.port) { $specs.C.Port = [int]$record.port }
    if ($record.name -eq "runner-d" -and $record.port) { $specs.D.Port = [int]$record.port }
}

function Assert-PlatformOnline {
    $apiPort = [int]$platformState.apiPort
    $identity = Get-HttpServiceIdentity -Url "http://127.0.0.1:$apiPort/api/v1/health"
    if (
        $null -eq $identity -or
        $identity.service -ne "agent-crosschain-platform-api" -or
        $identity.runtimeId -ne $runtimeId
    ) {
        throw "The platform API for runtime $runtimeId is offline. Start the complete platform before starting an individual Runner."
    }
}

function Get-RunnerRecord([string]$Key) {
    if ($Key -eq "B") {
        return [pscustomobject]@{
            name = "runner-b"
            pid = $platformState.runnerPid
            port = $platformState.runnerPort
            startTimeUtc = $platformState.runnerStartTimeUtc
        }
    }
    return @($experimentState.extraRunners) |
        Where-Object { $_.name -eq $specs[$Key].Id } |
        Select-Object -First 1
}

function Set-RunnerRecord([string]$Key, [System.Diagnostics.Process]$Process) {
    $spec = $specs[$Key]
    $startTime = Get-ProcessStartTimeUtc -Process $Process
    if ($Key -eq "B") {
        $platformState.runnerPid = $Process.Id
        $platformState.runnerPort = [int]$spec.Port
        $platformState.runnerStartTimeUtc = $startTime
        $platformState | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $platformStateFile -Encoding utf8
        return
    }
    $records = [Collections.Generic.List[object]]::new()
    foreach ($item in @($experimentState.extraRunners)) {
        if ($item.name -ne $spec.Id) { $records.Add($item) }
    }
    $records.Add([pscustomobject]@{
        name = $spec.Id
        pid = $Process.Id
        port = [int]$spec.Port
        startTimeUtc = $startTime
    })
    $experimentState.extraRunners = @($records)
    $experimentState | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $experimentStateFile -Encoding utf8
}

function Clear-RunnerRecord([string]$Key) {
    $spec = $specs[$Key]
    if ($Key -eq "B") {
        $platformState.runnerPid = $null
        $platformState.runnerStartTimeUtc = $null
        $platformState | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $platformStateFile -Encoding utf8
        return
    }
    foreach ($item in @($experimentState.extraRunners)) {
        if ($item.name -eq $spec.Id) {
            $item.pid = $null
            $item.startTimeUtc = $null
        }
    }
    $experimentState | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $experimentStateFile -Encoding utf8
}

function Get-RunnerStatus([string]$Key) {
    $spec = $specs[$Key]
    $identity = Get-HttpServiceIdentity -Url "http://127.0.0.1:$($spec.Port)/health"
    $listeners = @(Get-ListeningProcessIds -Port ([int]$spec.Port))
    $owned = $null -ne $identity -and
        $identity.service -eq "agent-crosschain-platform-agent-runner" -and
        $identity.runtimeId -eq $runtimeId -and
        $identity.runnerId -eq $spec.Id -and
        @($identity.agentSlugs) -contains $spec.Slug
    return [pscustomobject]@{
        Agent = $Key
        Runner = $spec.Id
        Slug = $spec.Slug
        Port = [int]$spec.Port
        Status = if ($owned) { "ONLINE" } elseif ($listeners.Count -gt 0) { "FOREIGN_PROCESS" } else { "OFFLINE" }
        Pid = if ($listeners.Count -eq 1) { $listeners[0] } else { $null }
    }
}

function Stop-Runner([string]$Key) {
    $status = Get-RunnerStatus -Key $Key
    if ($status.Status -eq "OFFLINE") { return $status }
    if ($status.Status -ne "ONLINE") {
        throw "Refusing to stop Runner $Key because port $($status.Port) is not owned by this experiment."
    }
    $record = Get-RunnerRecord -Key $Key
    if ($null -eq $record) { throw "Runner $Key has no process ownership record." }
    $process = Get-Process -Id ([int]$status.Pid) -ErrorAction Stop
    Stop-RecordedProcessTree -Name "Mock Agent Runner $Key" -ProcessId $process.Id `
        -StartTimeUtc (Get-ProcessStartTimeUtc -Process $process) | Out-Null
    Wait-ForPortRelease -Port ([int]$status.Port)
    Clear-RunnerRecord -Key $Key
    return Get-RunnerStatus -Key $Key
}

function Start-Runner([string]$Key) {
    Assert-PlatformOnline
    $status = Get-RunnerStatus -Key $Key
    if ($status.Status -eq "ONLINE") { return $status }
    if ($status.Status -eq "FOREIGN_PROCESS") {
        throw "Refusing to start Runner $Key because port $($status.Port) is occupied by another process."
    }

    $spec = $specs[$Key]
    $pythonCandidates = @(
        (Join-Path $projectRoot ".venv\Scripts\python.exe"),
        (Join-Path $projectRoot "apps\api\.venv\Scripts\python.exe")
    )
    $python = $pythonCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $python) { throw "Python environment is missing." }

    $runnerToken = if ($env:AGENT_RUNNER_API_TOKEN) { $env:AGENT_RUNNER_API_TOKEN } else { "local-experiment-runner-token" }
    $provider = if ($env:LLM_PROVIDER -in @("mock", "deepseek")) { $env:LLM_PROVIDER } else { "mock" }
    $env:LOCAL_PLATFORM_RUNTIME_ID = $runtimeId
    $env:DEV_IDENTITY_SEED = if ($env:DEV_IDENTITY_SEED) { $env:DEV_IDENTITY_SEED } else { "agent-market-demo" }
    $env:AGENT_RUNNER_API_TOKEN = $runnerToken
    $env:AGENT_RUNNER_ALLOW_INSECURE_LOCAL = "false"
    $env:AGENT_RUNNER_ID = $spec.Id
    $env:AGENT_RUNNER_SLUGS = $spec.Slug
    $env:LLM_PROVIDER = $provider
    $env:AGENT_CHAT_DB_PATH = Join-Path $runtimeDir "$($spec.Id)-chat.db"
    $env:AGENT_PLATFORM_REGISTRATION_STORE_PATH = Join-Path $runtimeDir "$($spec.Id)-platform-registrations.json"
    $env:AGENT_KNOWLEDGE_PATHS = Join-Path $projectRoot "docs"
    $env:AGENT_HOME_WALLET_PRIVATE_KEYS_JSON = (@{ ([string]$spec.Slug) = [string]$spec.PrivateKey } | ConvertTo-Json -Compress)
    $env:AGENT_WALLET_POLICIES_JSON = (@{
        ([string]$spec.Slug) = @{
            chainId = [int]$spec.ChainId
            homeWallet = $spec.HomeWallet
            rpcUrl = $spec.RpcUrl
            allowedArtifacts = @{ RemoteWorkBoard = @("submitResult") }
            maxValueWei = "0"
            maxGas = 500000
            maxGasPriceWei = "100000000000"
        }
    } | ConvertTo-Json -Compress -Depth 8)
    $env:AGENT_WALLET_NONCE_STORE_PATH = Join-Path $runtimeDir "$($spec.Id)-wallet-nonces.json"

    # Some Windows hosts expose both Path and PATH to PowerShell. Start-Process
    # rejects that duplicate environment block, so normalize it for this
    # control process and its Runner child only.
    $processPath = [Environment]::GetEnvironmentVariable("Path", "Process")
    if (-not $processPath) {
        $processPath = [Environment]::GetEnvironmentVariable("PATH", "Process")
    }
    [Environment]::SetEnvironmentVariable("PATH", $null, "Process")
    [Environment]::SetEnvironmentVariable("Path", $null, "Process")
    [Environment]::SetEnvironmentVariable("Path", $processPath, "Process")

    $stdoutLog = Join-Path $runtimeDir "$($spec.Id).control.stdout.log"
    $stderrLog = Join-Path $runtimeDir "$($spec.Id).control.stderr.log"
    $process = Start-Process -FilePath $python `
        -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$($spec.Port)") `
        -WorkingDirectory (Join-Path $projectRoot "services\agent-runner") -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog

    for ($attempt = 0; $attempt -lt 120; $attempt++) {
        Start-Sleep -Milliseconds 250
        $ready = Get-RunnerStatus -Key $Key
        if ($ready.Status -eq "ONLINE") {
            $listener = Get-Process -Id ([int]$ready.Pid) -ErrorAction Stop
            Set-RunnerRecord -Key $Key -Process $listener
            return Get-RunnerStatus -Key $Key
        }
        if ($process.HasExited) {
            throw "Runner $Key exited during startup with code $($process.ExitCode). Check $stderrLog."
        }
    }
    throw "Runner $Key did not become ready."
}

$targets = if ($Agent -eq "all") { @("B", "C", "D") } else { @($Agent) }
$results = foreach ($target in $targets) {
    switch ($Action) {
        "status" { Get-RunnerStatus -Key $target }
        "stop" { Stop-Runner -Key $target }
        "start" { Start-Runner -Key $target }
        "restart" {
            Stop-Runner -Key $target | Out-Null
            Start-Runner -Key $target
        }
    }
}

$results | Format-Table Agent, Runner, Slug, Port, Status, Pid -AutoSize
