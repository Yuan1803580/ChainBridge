[CmdletBinding()]
param(
    [int]$RunnerPort = 8122
)

$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$runtimeDir = if ($env:LOCAL_RUNTIME_DIR) {
    [System.IO.Path]::GetFullPath((Join-Path $projectRoot $env:LOCAL_RUNTIME_DIR))
} else {
    Join-Path $projectRoot ".runtime\local-experiment"
}
$platformStatePath = Join-Path $runtimeDir "platform.json"
$experimentStatePath = Join-Path $runtimeDir "experiment.json"
if (-not (Test-Path -LiteralPath $platformStatePath) -or -not (Test-Path -LiteralPath $experimentStatePath)) {
    throw "Local experiment is not running. Start it before running the Kimi acceptance."
}

$platform = Get-Content -Raw -LiteralPath $platformStatePath | ConvertFrom-Json
$experiment = Get-Content -Raw -LiteralPath $experimentStatePath | ConvertFrom-Json
$kimiHealth = Invoke-RestMethod -Uri "http://127.0.0.1:$RunnerPort/health" -TimeoutSec 10
if ($kimiHealth.status -ne "ok" -or $kimiHealth.runnerId -ne "independent-kimi-runner-v2") {
    throw "The independent Kimi Runner did not pass its identity health check on port $RunnerPort."
}

$env:API_URL = "http://127.0.0.1:$($platform.apiPort)"
$env:COORDINATOR_URL = "http://127.0.0.1:$($experiment.coordinatorPort)"
$env:COORDINATOR_API_TOKEN = if ($env:COORDINATOR_API_TOKEN) { $env:COORDINATOR_API_TOKEN } else { "local-experiment-coordinator-token" }
$env:AGENT_RUNNER_API_TOKEN = if ($env:AGENT_RUNNER_API_TOKEN) { $env:AGENT_RUNNER_API_TOKEN } else { "local-experiment-runner-token" }
$env:AUTO_INVOKE_AGENTS = "true"
$env:LOCAL_PROTOCOL_RUNTIME_DIR = $runtimeDir

Push-Location $projectRoot
try {
    & node scripts\kimi-e2e-acceptance.ts
    if ($LASTEXITCODE -ne 0) { throw "Kimi end-to-end acceptance failed." }
} finally {
    Pop-Location
}
