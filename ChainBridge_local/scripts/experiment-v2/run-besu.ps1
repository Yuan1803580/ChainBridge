[CmdletBinding()]
param([switch]$KeepChainData)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw "Docker is not installed." }
if (-not $KeepChainData) {
    & (Join-Path $PSScriptRoot "stop-besu.ps1") -DeleteChainData
}
& (Join-Path $PSScriptRoot "start-besu.ps1")
Push-Location $projectRoot
try {
    & powershell -NoProfile -ExecutionPolicy Bypass -File scripts\local\invoke-hardhat-isolated.ps1 -Command compile
    if ($LASTEXITCODE -ne 0) { throw "Contract compilation failed" }
}
finally { Pop-Location }
& (Join-Path $PSScriptRoot "deploy-besu.ps1")
& (Join-Path $PSScriptRoot "start-besu-chain-d.ps1")
& (Join-Path $PSScriptRoot "accept-besu.ps1")

Write-Host "EXPERIMENT_V2_BESU_READY results: .runtime\experiment-v2\results\latest.json"

