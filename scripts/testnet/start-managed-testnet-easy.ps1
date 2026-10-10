[CmdletBinding()]
param(
    [string]$PublicWebOrigin = "",
    [string]$PublicApiOrigin = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$stateFile = Join-Path $projectRoot ".runtime\managed-testnet\services.json"

& (Join-Path $PSScriptRoot "load-managed-testnet-env.ps1") `
    -PublicWebOrigin $PublicWebOrigin `
    -PublicApiOrigin $PublicApiOrigin

if (Test-Path -LiteralPath $stateFile -PathType Leaf) {
    $alreadyHealthy = $false
    try {
        $apiHealth = Invoke-RestMethod -UseBasicParsing -Uri "http://127.0.0.1:8000/api/v1/health" -TimeoutSec 3
        $alreadyHealthy = $apiHealth.status -eq "ok"
    }
    catch { }
    if ($alreadyHealthy) {
        & (Join-Path $PSScriptRoot "status-managed-testnet.ps1")
        Write-Host "Managed testnet is already running."
        Write-Host "Web: http://127.0.0.1:3000"
        return
    }
    & (Join-Path $PSScriptRoot "stop-managed-testnet.ps1")
}

& (Join-Path $PSScriptRoot "start-managed-testnet.ps1")
& (Join-Path $PSScriptRoot "status-managed-testnet.ps1")
if ($LASTEXITCODE -ne 0) { throw "Managed testnet started but one or more health checks failed" }
Write-Host "Open http://127.0.0.1:3000"
