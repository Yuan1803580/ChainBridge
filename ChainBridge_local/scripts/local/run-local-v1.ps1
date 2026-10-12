[CmdletBinding()]
param(
    [switch]$StopAfter
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

& (Join-Path $PSScriptRoot "start-platform-v1.ps1")
Push-Location $projectRoot
try {
    node scripts\local-protocol-worker-acceptance.ts
    if ($LASTEXITCODE -ne 0) { throw "Integrated local protocol V1 acceptance failed" }
}
finally {
    Pop-Location
    if ($StopAfter) {
        & (Join-Path $PSScriptRoot "stop-platform-v1.ps1")
    }
}
