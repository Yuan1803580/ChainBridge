[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

& (Join-Path $PSScriptRoot "run-local-v1.ps1")

$webPort = if ($env:LOCAL_WEB_PORT) { [int]$env:LOCAL_WEB_PORT } else { 3000 }
Write-Host "LOCAL_V1_FULL_STACK_READY http://127.0.0.1:$webPort"
