[CmdletBinding()]
param([switch]$KeepExisting)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$runtimeState = Join-Path $projectRoot ".runtime\experiment-v2\hardhat\chains.json"
if ((Test-Path -LiteralPath $runtimeState) -and -not $KeepExisting) {
    & (Join-Path $PSScriptRoot "stop-hardhat.ps1")
}
Push-Location $projectRoot
try {
    & powershell -NoProfile -ExecutionPolicy Bypass -File scripts\local\invoke-hardhat-isolated.ps1 -Command compile
    if ($LASTEXITCODE -ne 0) { throw "Contract compilation failed" }
}
finally { Pop-Location }
& (Join-Path $PSScriptRoot "start-hardhat.ps1")
Push-Location $projectRoot
try {
    & node scripts\experiment-v2-deploy.ts
    if ($LASTEXITCODE -ne 0) { throw "Initial V2 deployment failed" }
}
finally { Pop-Location }
& (Join-Path $PSScriptRoot "start-hardhat-chain-d.ps1")
Push-Location $projectRoot
try {
    & node scripts\experiment-v2-acceptance.ts
    if ($LASTEXITCODE -ne 0) { throw "V2 acceptance failed" }
}
finally { Pop-Location }

Write-Host "EXPERIMENT_V2_READY results: .runtime\experiment-v2\results\latest.json"
