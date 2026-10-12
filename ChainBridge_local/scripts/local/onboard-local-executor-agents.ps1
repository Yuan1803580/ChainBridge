[CmdletBinding()]
param(
    [int]$ApiPort = 8200,
    [int]$RunnerBPort = 8111,
    [int]$RunnerCPort = 8112,
    [int]$RunnerDPort = 8113,
    [string]$RuntimeToken = "",
    [switch]$SkipCapabilitySync
)

# Onboards the three local Agent Runner processes (B / C / D) so the platform
# capability market exposes their real output contracts and the Coordinator can
# build a Core-validated DAG.
#
# For every Runner this script:
#   1. reads the Runner manifest (capabilityId + outputSchema),
#   2. resolves the matching WorkflowAgentProfile (by Agent slug),
#   3. asks the platform to sync/publish the market capability contract,
#   4. registers the Runner's signed heartbeat loop against that profile.
#
# Step 4 is what makes the profile `matchable`. Without a fresh signed heartbeat
# the Coordinator sees an empty capability market and rejects every proposal.

$ErrorActionPreference = "Stop"
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
$RuntimeToken = if ($RuntimeToken) { $RuntimeToken } elseif ($env:AGENT_RUNNER_API_TOKEN) { $env:AGENT_RUNNER_API_TOKEN } else { "local-experiment-runner-token" }
$runnerHeaders = @{ Authorization = "Bearer $RuntimeToken" }
$statePath = Join-Path $runtimeDir "executor-onboarding.json"

function Invoke-JsonPost([string]$Uri, [object]$Body, [hashtable]$Headers = @{}) {
    return Invoke-RestMethod -Method Post -Uri $Uri -Headers $Headers `
        -ContentType "application/json; charset=utf-8" `
        -Body ($Body | ConvertTo-Json -Compress -Depth 20) -TimeoutSec 240
}

$specs = @(
    [ordered]@{ Id = "runner-b"; Slug = "workflow-research-b"; Port = $RunnerBPort },
    [ordered]@{ Id = "runner-c"; Slug = "workflow-verification-c"; Port = $RunnerCPort },
    [ordered]@{ Id = "runner-d"; Slug = "workflow-verification-d"; Port = $RunnerDPort }
)

$adminLogin = Invoke-JsonPost "$apiUrl/api/v1/auth/dev-login" @{ did = "did:local:platform-admin" }
$adminHeaders = @{ Authorization = "Bearer $($adminLogin.accessToken)" }

$directory = Invoke-RestMethod -Uri "$apiUrl/api/v1/workflow-agents" -TimeoutSec 20
$results = @()

foreach ($spec in $specs) {
    $runnerUrl = "http://127.0.0.1:$($spec.Port)"
    $agentUrl = "$runnerUrl/agents/$($spec.Slug)"
    $manifest = Invoke-RestMethod -Uri "$agentUrl/manifest" -TimeoutSec 20

    $profile = @($directory.items | Where-Object { $_.agent.slug -eq [string]$spec.Slug }) | Select-Object -First 1
    if (-not $profile) {
        throw "No WorkflowAgentProfile is bound to Agent slug '$($spec.Slug)'. Start the local platform stack first."
    }

    $capabilityIds = @($manifest.capabilities | ForEach-Object { [string]$_.capabilityId })
    if (-not $SkipCapabilitySync) {
        $sync = Invoke-JsonPost "$apiUrl/api/v1/workflow-agents/$($profile.id)/capabilities/sync" @{} $adminHeaders
        $synced = @($sync.profileCapabilities)
    } else {
        $synced = @($profile.capabilities)
    }

    $registration = Invoke-JsonPost "$agentUrl/platform-registration" @{
        platformUrl = $apiUrl
        profileId = [string]$profile.id
        status = "ONLINE"
        capacityAvailable = $true
        endpointLatencyMs = 100
        heartbeatIntervalSeconds = 30
    } $runnerHeaders

    $results += [ordered]@{
        runnerId = [string]$spec.Id
        slug = [string]$spec.Slug
        profileId = [string]$profile.id
        agentKey = [string]$profile.agentKey
        endpoint = [string]$profile.agent.endpoint
        declaredCapabilities = $capabilityIds
        publishedCapabilities = $synced
        matchable = [bool]$registration.heartbeat.matchable
        heartbeatAccepted = [bool]$registration.heartbeat.accepted
    }
}

$marketContext = @()
foreach ($item in $results) {
    if (-not $item.matchable) {
        throw "Runner $($item.runnerId) is still not matchable after heartbeat registration."
    }
}

$state = [ordered]@{
    schemaVersion = 1
    apiUrl = $apiUrl
    updatedAt = [DateTime]::UtcNow.ToString("o")
    agents = $results
}
New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
$state | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $statePath -Encoding utf8

Write-Host "Local Executor Agent onboarding completed:"
foreach ($item in $results) {
    Write-Host ("  {0,-10} {1,-26} profile={2} capabilities={3} matchable={4}" -f `
        $item.runnerId, $item.slug, $item.profileId, ($item.publishedCapabilities -join ","), $item.matchable)
}
Write-Host "  State: $statePath"
