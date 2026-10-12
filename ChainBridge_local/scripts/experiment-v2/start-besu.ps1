[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$compose = Join-Path $projectRoot "infra\experiment-v2\docker-compose.yml"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker is not installed. Install Docker Desktop or use experiment:v2:hardhat:start for functional validation."
}
& (Join-Path $PSScriptRoot "prepare-besu.ps1")
& docker compose -f $compose up -d chain-a chain-b chain-c
if ($LASTEXITCODE -ne 0) { throw "Could not start Besu chains A/B/C" }

function Wait-Rpc([int]$Port, [int]$ExpectedChainId) {
    for ($attempt = 0; $attempt -lt 90; $attempt++) {
        try {
            $request = @{ jsonrpc = "2.0"; id = 1; method = "eth_chainId"; params = @() } | ConvertTo-Json -Compress
            $response = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$Port" -ContentType "application/json" -Body $request -TimeoutSec 1
            if ([Convert]::ToInt32($response.result, 16) -eq $ExpectedChainId) { return }
        }
        catch {}
        Start-Sleep -Seconds 1
    }
    throw "Besu RPC on port $Port did not report chainId $ExpectedChainId"
}
Wait-Rpc 9545 31337
Wait-Rpc 9546 31338
Wait-Rpc 9547 31339
Write-Host "Besu A/B/C started on RPC ports 9545, 9546, and 9547. Chain D remains offline."
