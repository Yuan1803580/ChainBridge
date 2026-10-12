[CmdletBinding()]
param(
    [int]$ApiPort = 8200,
    [int]$RunnerPort = 8122,
    [int]$ChainBPort = 8546
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$runtimeDir = if ($env:LOCAL_RUNTIME_DIR) {
    if ([System.IO.Path]::IsPathRooted($env:LOCAL_RUNTIME_DIR)) {
        [System.IO.Path]::GetFullPath($env:LOCAL_RUNTIME_DIR)
    } else {
        [System.IO.Path]::GetFullPath((Join-Path $projectRoot $env:LOCAL_RUNTIME_DIR))
    }
} else {
    Join-Path $projectRoot ".runtime\local-experiment"
}
$apiUrl = "http://127.0.0.1:$ApiPort"
$runnerUrl = "http://127.0.0.1:$RunnerPort"
$chainBRpc = if ($env:LOCAL_CHAIN_B_RPC_URL) { $env:LOCAL_CHAIN_B_RPC_URL } else { "http://127.0.0.1:$ChainBPort" }
$onboardingPath = Join-Path $runtimeDir "runner-kimi-onboarding.json"
$evidencePath = Join-Path $runtimeDir "evidence\kimi-gas-funding.json"

function Assert-Service([string]$Url, [string]$Name) {
    try {
        $null = Invoke-RestMethod -Uri $Url -TimeoutSec 3
    } catch {
        throw "$Name is not available at $Url. Start the local experiment first."
    }
}

function Invoke-JsonPost([string]$Uri, [object]$Body, [hashtable]$Headers = @{}) {
    return Invoke-RestMethod -Method Post -Uri $Uri -Headers $Headers `
        -ContentType "application/json; charset=utf-8" `
        -Body ($Body | ConvertTo-Json -Compress -Depth 8) -TimeoutSec 30
}

if (-not (Test-Path -LiteralPath $onboardingPath)) {
    throw "Kimi onboarding state is missing. Run pnpm local:kimi:onboard first."
}
Assert-Service "$apiUrl/api/v1/health" "Platform API"
Assert-Service "$runnerUrl/health" "Kimi Agent Runner"
try {
    $chainState = Invoke-RestMethod -Method Post -Uri $chainBRpc `
        -ContentType "application/json" `
        -Body '{"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]}' -TimeoutSec 3
    if ([Convert]::ToInt64([string]$chainState.result, 16) -ne 31338) {
        throw "Chain B RPC returned the wrong chainId."
    }
} catch {
    throw "Chain B RPC is not available at $chainBRpc or does not report chainId 31338."
}

$env:LOCAL_RUNTIME_DIR = $runtimeDir
$env:LOCAL_CHAIN_B_RPC_URL = $chainBRpc
Push-Location $projectRoot
try {
    & node "scripts/fund-kimi-gas.ts" | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "The chain B Gas funding transaction failed." }
} finally {
    Pop-Location
}

$state = Get-Content -Raw -LiteralPath $onboardingPath | ConvertFrom-Json
$adminLogin = Invoke-JsonPost "$apiUrl/api/v1/auth/dev-login" @{ did = "did:local:platform-admin" }
$adminHeaders = @{ Authorization = "Bearer $($adminLogin.accessToken)" }
$gasCheck = Invoke-JsonPost `
    "$apiUrl/api/v1/agent-onboarding/registrations/$($state.profileId)/gas-check" @{} $adminHeaders
$evidence = Get-Content -Raw -LiteralPath $evidencePath | ConvertFrom-Json
$state.gasReady = [bool]$gasCheck.gasReady
$state.chainStatus = [string]$gasCheck.chainStatus
if ($state.PSObject.Properties.Name -contains "matchable") {
    $state.matchable = [bool]$gasCheck.matchable
} else {
    $state | Add-Member -NotePropertyName matchable -NotePropertyValue ([bool]$gasCheck.matchable)
}
$state.updatedAt = [DateTime]::UtcNow.ToString("o")
$state | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $onboardingPath -Encoding utf8

Write-Host "Kimi chain B Gas funding completed:"
Write-Host "  Profile:      $($state.profileId)"
Write-Host "  Wallet:       $($evidence.recipient)"
Write-Host "  Chain:        $($evidence.chainId)"
Write-Host "  Transaction:  $($evidence.transactionHash)"
Write-Host "  Balance wei:  $($gasCheck.gasBalanceWei)"
Write-Host "  Gas ready:    $($gasCheck.gasReady)"
Write-Host "  Matchable:    $($gasCheck.matchable)"
Write-Host "  Evidence:     $evidencePath"
