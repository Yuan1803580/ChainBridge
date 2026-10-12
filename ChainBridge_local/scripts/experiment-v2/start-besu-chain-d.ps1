[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$compose = Join-Path $projectRoot "infra\experiment-v2\docker-compose.yml"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw "Docker is not installed." }
& (Join-Path $PSScriptRoot "prepare-besu.ps1")
& docker compose -f $compose --profile dynamic up -d chain-d
if ($LASTEXITCODE -ne 0) { throw "Could not start Besu chain D" }
for ($attempt = 0; $attempt -lt 90; $attempt++) {
    try {
        $request = @{ jsonrpc = "2.0"; id = 1; method = "eth_chainId"; params = @() } | ConvertTo-Json -Compress
        $response = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:9548" -ContentType "application/json" -Body $request -TimeoutSec 1
        if ([Convert]::ToInt32($response.result, 16) -eq 31340) { break }
    }
    catch {}
    if ($attempt -eq 89) { throw "Besu chain D RPC did not become ready" }
    Start-Sleep -Seconds 1
}
Write-Host "Besu chain D started on RPC port 9548. Run experiment:v2:add-chain-d to deploy and activate its route."
