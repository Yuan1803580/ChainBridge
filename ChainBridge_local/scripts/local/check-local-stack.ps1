[CmdletBinding()]
param(
    [int]$ApiPort = 8200,
    [int]$CoordinatorPort = 8300,
    [int]$RequesterPort = 8400,
    [int]$RunnerBPort = 8111,
    [int]$RunnerCPort = 8112,
    [int]$RunnerDPort = 8113,
    [int]$WebPort = 3100,
    [string]$CoordinatorToken = "",
    [switch]$Json
)

# Read-only health report for the whole local experiment stack.
# It never mutates platform state: onboarding is intentionally not performed here.
# Use ensure-local-agents.ps1 when a component is reported as not connected.

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$runtimeDir = if ($env:LOCAL_RUNTIME_DIR) {
    if ([IO.Path]::IsPathRooted($env:LOCAL_RUNTIME_DIR)) {
        [IO.Path]::GetFullPath($env:LOCAL_RUNTIME_DIR)
    } else {
        [IO.Path]::GetFullPath((Join-Path $projectRoot $env:LOCAL_RUNTIME_DIR))
    }
} else {
    Join-Path $projectRoot ".runtime\local-experiment"
}
$apiUrl = "http://127.0.0.1:$ApiPort"
$CoordinatorToken = if ($CoordinatorToken) { $CoordinatorToken } elseif ($env:COORDINATOR_API_TOKEN) { $env:COORDINATOR_API_TOKEN } else { "local-experiment-coordinator-token" }

# Every probe targets 127.0.0.1. PowerShell 5.1 Invoke-RestMethod reads
# HTTP_PROXY but ignores NO_PROXY, so a machine-wide proxy swallows the probes and
# every service looks down even when it is healthy. Clear the proxy variables
# instead of relying on NO_PROXY, and also neutralise the default web proxy.
foreach ($proxyName in @(
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy"
)) {
    Remove-Item "Env:$proxyName" -ErrorAction SilentlyContinue
}
try {
    [System.Net.WebRequest]::DefaultWebProxy = New-Object System.Net.WebProxy
}
catch {
    [System.Net.WebRequest]::DefaultWebProxy = $null
}

$platformFile = Join-Path $runtimeDir "platform.json"
$expectedRuntime = $null
if (Test-Path -LiteralPath $platformFile) {
    try {
        $expectedRuntime = [string](Get-Content -Raw -LiteralPath $platformFile | ConvertFrom-Json).runtimeId
    }
    catch {
        $expectedRuntime = $null
    }
}

$problems = @()
$report = [ordered]@{}

function Get-Json([string]$Url, [hashtable]$Headers = @{}, [int]$TimeoutSec = 8) {
    try {
        if ($Headers.Count -gt 0) {
            return Invoke-RestMethod -Uri $Url -Headers $Headers -TimeoutSec $TimeoutSec
        }
        return Invoke-RestMethod -Uri $Url -TimeoutSec $TimeoutSec
    }
    catch {
        return $null
    }
}

function Test-PortListening([int]$Port) {
    $listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    return $listeners.Count -gt 0
}

function Add-Service {
    param(
        [string]$Name,
        [int]$Port,
        [string]$ExpectedService = "",
        [string]$IdentityPath = "",
        [switch]$Optional
    )
    # Not every service exposes the same health route: the platform API serves
    # /api/v1/health, the web app has no health endpoint at all.
    if (-not $IdentityPath) {
        if (-not (Test-PortListening -Port $Port)) {
            $status = "DOWN"
            $detail = "port $Port is not listening"
            if (-not $Optional) { $script:problems += "$Name is DOWN ($detail)" }
            $script:report[$Name] = [ordered]@{
                port = $Port; status = $status; detail = $detail
                service = $null; provider = $null; model = $null
            }
            return
        }
        $script:report[$Name] = [ordered]@{
            port = $Port; status = "UP"; detail = "listening (no health route)"
            service = $null; provider = $null; model = $null
        }
        return
    }
    $health = Get-Json "http://127.0.0.1:$Port$IdentityPath"
    $status = "DOWN"
    $detail = "port $Port is not listening"
    if ($health) {
        $service = [string]$health.service
        $runtime = [string]$health.runtimeId
        if ($ExpectedService -and $service -ne $ExpectedService) {
            $status = "WRONG_SERVICE"
            $detail = "expected $ExpectedService but found $service"
        }
        elseif ($expectedRuntime -and $runtime -and $runtime -ne $expectedRuntime) {
            $status = "STALE"
            $detail = "runtimeId $runtime is not owned by this run ($expectedRuntime)"
        }
        else {
            $status = "UP"
            $detail = ""
        }
    }
    if ($status -ne "UP" -and -not $Optional) {
        $script:problems += "$Name is $status ($detail)"
    }
    # PowerShell 5.1 has no null-coalescing operator, so probe both field names.
    $provider = $null
    $model = $null
    if ($health) {
        if ($health.PSObject.Properties["provider"]) { $provider = [string]$health.provider }
        if (-not $provider -and $health.PSObject.Properties["llmProvider"]) { $provider = [string]$health.llmProvider }
        if ($health.PSObject.Properties["model"]) { $model = [string]$health.model }
    }
    $script:report[$Name] = [ordered]@{
        port = $Port
        status = $status
        detail = $detail
        service = if ($health) { [string]$health.service } else { $null }
        provider = $provider
        model = $model
    }
}

Add-Service "api" $ApiPort "agent-crosschain-platform-api" -IdentityPath "/api/v1/health"
Add-Service "web" $WebPort -Optional
Add-Service "coordinator" $CoordinatorPort "agent-crosschain-platform-coordinator-agent" -IdentityPath "/health"
Add-Service "requester" $RequesterPort "local-requester-wallet-service" -IdentityPath "/health" -Optional
Add-Service "runner-b" $RunnerBPort "agent-crosschain-platform-agent-runner" -IdentityPath "/health"
Add-Service "runner-c" $RunnerCPort "agent-crosschain-platform-agent-runner" -IdentityPath "/health"
Add-Service "runner-d" $RunnerDPort "agent-crosschain-platform-agent-runner" -IdentityPath "/health"

$agents = @()
$directory = Get-Json "$apiUrl/api/v1/workflow-agents"
if ($directory) {
    foreach ($item in @($directory.items)) {
        $heartbeat = $item.lastHeartbeatAt
        $freshSeconds = $null
        if ($heartbeat) {
            try {
                $freshSeconds = [int](([DateTime]::UtcNow - ([DateTime]$heartbeat).ToUniversalTime()).TotalSeconds)
            }
            catch {
                $freshSeconds = $null
            }
        }
        $agents += [ordered]@{
            agentKey = [string]$item.agentKey
            agentType = [string]$item.agentType
            admission = [string]$item.admissionStatus
            credential = [string]$item.credentialStatus
            availability = [string]$item.availability
            matchable = [bool]$item.matchable
            heartbeatAgeSeconds = $freshSeconds
            capabilities = @($item.capabilities)
        }
    }
}

$coordinatorProfile = @($agents | Where-Object { $_.agentType -eq "COORDINATOR" })
if ($coordinatorProfile.Count -eq 0) {
    $problems += "No COORDINATOR profile exists on the platform. Run ensure-local-agents.ps1."
}
else {
    $cp = $coordinatorProfile[0]
    if (-not $cp.matchable) {
        $problems += "Coordinator profile $($cp.agentKey) is not matchable (admission=$($cp.admission), heartbeatAge=$($cp.heartbeatAgeSeconds)s). Heartbeat must be younger than AGENT_HEARTBEAT_TTL_SECONDS."
    }
}

$executors = @($agents | Where-Object { $_.agentType -ne "COORDINATOR" })
$matchableExecutors = @($executors | Where-Object { $_.matchable })
if ($matchableExecutors.Count -eq 0) {
    $problems += "No matchable EXECUTOR Agent. The Coordinator cannot plan any node. Run ensure-local-agents.ps1."
}

$registration = $null
try {
    $registration = Invoke-RestMethod -Uri "http://127.0.0.1:$CoordinatorPort/platform-registration" `
        -Headers @{ Authorization = "Bearer $CoordinatorToken" } -TimeoutSec 8
}
catch {
    $registration = $null
}

$coordinatorRegistration = if ($registration) {
    [ordered]@{
        profileId = [string]$registration.profileId
        status = [string]$registration.status
        lastError = $registration.lastError
    }
} else {
    [ordered]@{ profileId = $null; status = "NOT_REGISTERED"; lastError = $null }
}

$providers = @(
    foreach ($key in $report.Keys) {
        $value = [string]$report[$key].provider
        if ($value) { $value }
    }
) | Sort-Object -Unique
$llmMode = if ($providers.Count -eq 0) { "unknown" }
elseif (@($providers | Where-Object { $_ -ne "mock" }).Count -eq 0) { "mock" }
elseif ($providers -contains "mock") { "mixed" }
else { "real" }

$result = [ordered]@{
    generatedAt = [DateTime]::UtcNow.ToString("o")
    runtimeId = $expectedRuntime
    llmMode = $llmMode
    providers = $providers
    services = $report
    coordinatorRegistration = $coordinatorRegistration
    agents = $agents
    problems = $problems
    healthy = ($problems.Count -eq 0)
}

if ($Json) {
    $result | ConvertTo-Json -Depth 8
    exit 0
}

Write-Host ""
Write-Host "Local stack report  (llmMode = $llmMode)"
Write-Host "--------------------------------------------------------------"
foreach ($key in $report.Keys) {
    $item = $report[$key]
    $marker = switch ($item.status) { "UP" { "[ok]  " } "DOWN" { "[down]" } default { "[warn]" } }
    Write-Host ("  {0} {1,-12} :{2,-5} {3}" -f $marker, $key, $item.port, $item.status)
    if ($item.provider) { Write-Host ("         provider={0} model={1}" -f $item.provider, $item.model) }
}
Write-Host ""
Write-Host "Platform Agent directory:"
foreach ($item in $agents) {
    Write-Host ("  {0,-32} {1,-11} adm={2,-9} cred={3,-12} matchable={4,-5} heartbeatAge={5}s" -f `
        $item.agentKey, $item.agentType, $item.admission, $item.credential, $item.matchable, $item.heartbeatAgeSeconds)
}
Write-Host ""
if ($problems.Count -eq 0) {
    Write-Host "RESULT: all local Agents are connected and matchable."
    exit 0
}
Write-Host "RESULT: $($problems.Count) problem(s) found:"
foreach ($problem in $problems) { Write-Host "  - $problem" }
Write-Host ""
Write-Host "Fix with: pnpm local:agents:connect"
exit 1
