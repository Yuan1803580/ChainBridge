[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$definitions = @(
    @{ Port = 8645; ChainId = 31337 },
    @{ Port = 8646; ChainId = 31338 },
    @{ Port = 8647; ChainId = 31339 },
    @{ Port = 8648; ChainId = 31340 }
)

foreach ($definition in $definitions) {
    $listener = Get-NetTCPConnection -LocalPort $definition.Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $listener) { continue }

    $body = @{ jsonrpc = "2.0"; id = 1; method = "eth_chainId"; params = @() } | ConvertTo-Json -Compress
    $rpc = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$($definition.Port)" `
        -ContentType "application/json" -Body $body -TimeoutSec 2
    $actualChainId = [Convert]::ToInt32($rpc.result, 16)
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
    $commandLine = [string]$process.CommandLine
    $expectedCli = Join-Path $projectRoot "node_modules"
    $isExpected = $actualChainId -eq $definition.ChainId `
        -and $process.Name -eq "node.exe" `
        -and $commandLine.IndexOf($expectedCli, [StringComparison]::OrdinalIgnoreCase) -ge 0 `
        -and $commandLine -match "hardhat.*node" `
        -and $commandLine -match "--port\s+$($definition.Port)" `
        -and $commandLine -match "--chain-id\s+$($definition.ChainId)"
    if (-not $isExpected) {
        throw "Refusing to stop PID $($listener.OwningProcess) on port $($definition.Port): it is not the expected project Hardhat chain."
    }

    Stop-Process -Id $listener.OwningProcess -Force
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        Start-Sleep -Milliseconds 100
        if (-not (Get-NetTCPConnection -LocalPort $definition.Port -State Listen -ErrorAction SilentlyContinue)) {
            break
        }
    }
    if (Get-NetTCPConnection -LocalPort $definition.Port -State Listen -ErrorAction SilentlyContinue) {
        throw "Hardhat chain on port $($definition.Port) did not stop."
    }
    Write-Host "Stopped verified orphan Hardhat chain on port $($definition.Port)."
}
