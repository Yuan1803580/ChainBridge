[CmdletBinding()]
param(
    [int]$ApiPort = 8200,
    [int]$CoordinatorPort = 8300,
    [int]$RunnerBPort = 8111,
    [int]$RunnerCPort = 8112,
    [int]$RunnerDPort = 8113,
    [string]$RuntimeToken = "",
    [string]$CoordinatorToken = "",
    [switch]$SkipSmokeTest
)

# One command that connects every local Agent to the running platform:
#   * the three Executor Runners (research / verification) are published into the
#     capability market and start sending signed heartbeats,
#   * the user-facing Coordinator Agent is registered, ownership-verified,
#     activated and starts its own heartbeat loop,
#   * a dry Coordinator chat proves the DAG proposal is Core-validated.
#
# Run this after `pnpm local:experiment:start`.

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
$coordinatorUrl = "http://127.0.0.1:$CoordinatorPort"
$RuntimeToken = if ($RuntimeToken) { $RuntimeToken } elseif ($env:AGENT_RUNNER_API_TOKEN) { $env:AGENT_RUNNER_API_TOKEN } else { "local-experiment-runner-token" }
$CoordinatorToken = if ($CoordinatorToken) { $CoordinatorToken } elseif ($env:COORDINATOR_API_TOKEN) { $env:COORDINATOR_API_TOKEN } else { "local-experiment-coordinator-token" }

function Invoke-Step([string]$Name, [scriptblock]$Action) {
    Write-Host ""
    Write-Host "== $Name =="
    & $Action
}

# Invoke-RestMethod resolves its proxy from the WinHTTP system proxy instead of
# HTTP_PROXY. On a machine with a system proxy every 127.0.0.1 readiness probe
# would be sent to the proxy and hang, so local calls must bypass it explicitly.
try {
    [System.Net.WebRequest]::DefaultWebProxy = New-Object System.Net.WebProxy
}
catch {
    [System.Net.WebRequest]::DefaultWebProxy = $null
}

function Assert-Healthy([string]$Url, [string]$Label) {
    try {
        $null = Invoke-RestMethod -Uri $Url -TimeoutSec 5
    }
    catch {
        throw "$Label is not reachable at $Url. Start the local stack with 'pnpm local:experiment:start' first."
    }
}

Assert-Healthy "$apiUrl/api/v1/health" "Platform API"
Assert-Healthy "$coordinatorUrl/health" "Coordinator Agent"
foreach ($port in @($RunnerBPort, $RunnerCPort, $RunnerDPort)) {
    Assert-Healthy "http://127.0.0.1:$port/health" "Agent Runner on port $port"
}

Invoke-Step "Executor Agent onboarding" {
    & (Join-Path $PSScriptRoot "onboard-local-executor-agents.ps1") `
        -ApiPort $ApiPort -RunnerBPort $RunnerBPort -RunnerCPort $RunnerCPort `
        -RunnerDPort $RunnerDPort -RuntimeToken $RuntimeToken
}

Invoke-Step "Coordinator Agent onboarding" {
    & (Join-Path $PSScriptRoot "onboard-coordinator-agent.ps1") `
        -ApiPort $ApiPort -CoordinatorPort $CoordinatorPort -RuntimeToken $CoordinatorToken
}

if (-not $SkipSmokeTest) {
    Invoke-Step "Coordinator dry-run smoke test" {
        $headers = @{ Authorization = "Bearer $CoordinatorToken" }
        $chat = Invoke-RestMethod -Method Post -Uri "$coordinatorUrl/chat" -Headers $headers `
            -ContentType "application/json; charset=utf-8" -TimeoutSec 240 `
            -Body (@{
                message = "Produce a two-step research and verification workflow over the local cross-chain settlement flow."
                budget = "20.00"
                platformFee = "1.10"
                currency = "USDC"
            } | ConvertTo-Json -Compress)
        if ([string]$chat.status -ne "DRAFT") {
            throw "Coordinator chat did not return a DRAFT plan: $($chat.status)"
        }
        $nodeCount = @($chat.plan.nodes).Count
        if ($nodeCount -lt 1) {
            throw "Coordinator Core returned an empty plan."
        }
        Write-Host "  conversationId: $($chat.conversationId)"
        Write-Host "  provider:       $($chat.provider)"
        Write-Host "  core nodes:     $nodeCount -> $((@($chat.plan.nodes) | ForEach-Object { $_.nodeKey }) -join ',')"
    }
}

Write-Host ""
Write-Host "All local Agents are connected to $apiUrl."
Write-Host "  Web console:  http://127.0.0.1:3100/workflows/new"
Write-Host "  Coordinator:  $coordinatorUrl/docs"
Write-Host "  State:        $runtimeDir"
