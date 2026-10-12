[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$env:LOCAL_RUNTIME_DIR = ".runtime\experiment-v2\hardhat"
$env:LOCAL_CHAIN_D_RPC_URL = "http://127.0.0.1:8648"
& (Join-Path $projectRoot "scripts\local\start-four-chains.ps1") -Chains D
$env:EXPERIMENT_V2_CHAIN_D_RPC = $env:LOCAL_CHAIN_D_RPC_URL
Write-Host "Hardhat chain D functional RPC started. Run experiment:v2:add-chain-d next."

