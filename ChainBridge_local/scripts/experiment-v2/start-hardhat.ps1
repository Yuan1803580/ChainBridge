[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$env:LOCAL_RUNTIME_DIR = ".runtime\experiment-v2\hardhat"
$env:LOCAL_CHAIN_A_RPC_URL = "http://127.0.0.1:8645"
$env:LOCAL_CHAIN_B_RPC_URL = "http://127.0.0.1:8646"
$env:LOCAL_CHAIN_C_RPC_URL = "http://127.0.0.1:8647"
$env:LOCAL_CHAIN_D_RPC_URL = "http://127.0.0.1:8648"
& (Join-Path $projectRoot "scripts\local\start-four-chains.ps1") -Chains A,B,C
$env:EXPERIMENT_V2_CHAIN_A_RPC = $env:LOCAL_CHAIN_A_RPC_URL
$env:EXPERIMENT_V2_CHAIN_B_RPC = $env:LOCAL_CHAIN_B_RPC_URL
$env:EXPERIMENT_V2_CHAIN_C_RPC = $env:LOCAL_CHAIN_C_RPC_URL
Write-Host "Hardhat A/B/C functional RPCs started. These chains use on-demand mining and are not valid for performance measurements."

