[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$env:LOCAL_RUNTIME_DIR = if ($env:LOCAL_RUNTIME_DIR) { $env:LOCAL_RUNTIME_DIR } else { ".runtime\local-experiment" }
$env:LOCAL_API_PORT = if ($env:LOCAL_API_PORT) { $env:LOCAL_API_PORT } else { "8200" }
$env:LOCAL_WEB_PORT = if ($env:LOCAL_WEB_PORT) { $env:LOCAL_WEB_PORT } else { "3100" }
$env:LOCAL_COORDINATOR_PORT = if ($env:LOCAL_COORDINATOR_PORT) { $env:LOCAL_COORDINATOR_PORT } else { "8300" }
$env:COORDINATOR_API_TOKEN = if ($env:COORDINATOR_API_TOKEN) { $env:COORDINATOR_API_TOKEN } else { "local-experiment-coordinator-token" }
$env:COORDINATOR_URL = "http://127.0.0.1:$($env:LOCAL_COORDINATOR_PORT)"
$env:API_URL = "http://127.0.0.1:$($env:LOCAL_API_PORT)"

& (Join-Path $PSScriptRoot "start-local-experiment.ps1")

Push-Location $projectRoot
try {
    & node scripts\local-protocol-worker-acceptance.ts
    if ($LASTEXITCODE -ne 0) { throw "Local experiment acceptance failed" }
}
finally {
    Pop-Location
}

Write-Host "LOCAL_EXPERIMENT_READY http://127.0.0.1:$($env:LOCAL_WEB_PORT)"

