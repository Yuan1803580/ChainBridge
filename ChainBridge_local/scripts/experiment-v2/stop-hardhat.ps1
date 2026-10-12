[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$env:LOCAL_RUNTIME_DIR = ".runtime\experiment-v2\hardhat"
& (Join-Path $projectRoot "scripts\local\stop-four-chains.ps1")

