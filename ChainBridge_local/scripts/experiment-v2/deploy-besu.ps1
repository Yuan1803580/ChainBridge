[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$env:EXPERIMENT_V2_CHAIN_A_RPC = "http://127.0.0.1:9545"
$env:EXPERIMENT_V2_CHAIN_B_RPC = "http://127.0.0.1:9546"
$env:EXPERIMENT_V2_CHAIN_C_RPC = "http://127.0.0.1:9547"
Push-Location $projectRoot
try {
    & node scripts\experiment-v2-deploy.ts
    if ($LASTEXITCODE -ne 0) { throw "Besu experiment deployment failed" }
}
finally { Pop-Location }

