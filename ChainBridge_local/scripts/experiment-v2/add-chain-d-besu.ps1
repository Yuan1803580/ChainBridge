[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$env:EXPERIMENT_V2_CHAIN_D_RPC = "http://127.0.0.1:9548"
Push-Location $projectRoot
try {
    & node scripts\experiment-v2-add-chain-d.ts
    if ($LASTEXITCODE -ne 0) { throw "Besu chain D route deployment failed" }
}
finally { Pop-Location }

