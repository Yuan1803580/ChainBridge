[CmdletBinding()]
param([switch]$DeleteChainData)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$compose = Join-Path $projectRoot "infra\experiment-v2\docker-compose.yml"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw "Docker is not installed." }
$arguments = @("compose", "-f", $compose, "--profile", "dynamic", "down")
if ($DeleteChainData) { $arguments += "--volumes" }
& docker @arguments
if ($LASTEXITCODE -ne 0) { throw "Could not stop Besu experiment" }

