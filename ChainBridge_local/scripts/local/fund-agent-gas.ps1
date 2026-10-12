[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^0x[0-9a-fA-F]{40}$")]
    [string]$WalletAddress,

    [string]$RpcUrl = "http://127.0.0.1:8547",
    [int]$ExpectedChainId = 31339,
    [decimal]$TargetBalanceEth = 1,
    [string]$ProfileId,
    [int]$ApiPort = 8200
)

$ErrorActionPreference = "Stop"

function Invoke-Rpc([string]$Method, [object[]]$Params) {
    $body = @{
        jsonrpc = "2.0"
        id = 1
        method = $Method
        params = $Params
    } | ConvertTo-Json -Compress -Depth 8

    $response = Invoke-RestMethod -Method Post -Uri $RpcUrl `
        -ContentType "application/json" -Body $body -TimeoutSec 10
    if ($response.error) {
        throw "RPC $Method failed: $($response.error.message)"
    }
    return $response.result
}

function Convert-HexToBigInteger([string]$Value) {
    $hex = $Value -replace '^0x', ''
    if (-not $hex) { return [System.Numerics.BigInteger]::Zero }
    return [System.Numerics.BigInteger]::Parse(
        "0$hex",
        [System.Globalization.NumberStyles]::AllowHexSpecifier
    )
}

$rpcUri = [Uri]$RpcUrl
if (-not [System.Net.IPAddress]::IsLoopback(([System.Net.Dns]::GetHostAddresses($rpcUri.Host))[0])) {
    throw "This script only funds local test chains. RPC host must resolve to loopback."
}

$chainIdHex = [string](Invoke-Rpc "eth_chainId" @())
$chainId = [Convert]::ToInt32(($chainIdHex -replace '^0x', ''), 16)
if ($chainId -ne $ExpectedChainId) {
    throw "RPC reports chainId $chainId, expected $ExpectedChainId."
}

$accounts = @(Invoke-Rpc "eth_accounts" @())
if ($accounts.Count -eq 0) {
    throw "The local chain does not expose an unlocked test account."
}

$targetWeiText = ($TargetBalanceEth * [decimal]1000000000000000000).ToString(
    "0",
    [System.Globalization.CultureInfo]::InvariantCulture
)
$targetWei = [System.Numerics.BigInteger]::Parse($targetWeiText)
$balanceBefore = Convert-HexToBigInteger ([string](Invoke-Rpc "eth_getBalance" @($WalletAddress, "latest")))
$amountTransferred = [System.Numerics.BigInteger]::Zero
$transactionHash = $null

if ($balanceBefore -lt $targetWei) {
    $amountTransferred = $targetWei - $balanceBefore
    $transactionHash = [string](Invoke-Rpc "eth_sendTransaction" @(@{
        from = [string]$accounts[0]
        to = $WalletAddress
        value = "0x$($amountTransferred.ToString('x'))"
    }))

    $receipt = $null
    for ($attempt = 0; $attempt -lt 40 -and -not $receipt; $attempt++) {
        Start-Sleep -Milliseconds 500
        $receipt = Invoke-Rpc "eth_getTransactionReceipt" @($transactionHash)
    }
    if (-not $receipt) {
        throw "Timed out waiting for transaction $transactionHash."
    }
    if ([string]$receipt.status -ne "0x1") {
        throw "Funding transaction $transactionHash failed."
    }
}

$balanceAfter = Convert-HexToBigInteger ([string](Invoke-Rpc "eth_getBalance" @($WalletAddress, "latest")))
if ($balanceAfter -lt $targetWei) {
    throw "Wallet balance is still below the requested target."
}

$platformState = $null
if ($ProfileId) {
    $apiUrl = "http://127.0.0.1:$ApiPort"
    $login = Invoke-RestMethod -Method Post -Uri "$apiUrl/api/v1/auth/dev-login" `
        -ContentType "application/json" `
        -Body (@{ did = "did:local:platform-admin" } | ConvertTo-Json -Compress) `
        -TimeoutSec 10
    $platformState = Invoke-RestMethod -Method Post `
        -Uri "$apiUrl/api/v1/agent-onboarding/registrations/$ProfileId/gas-check" `
        -Headers @{ Authorization = "Bearer $($login.accessToken)" } `
        -ContentType "application/json" -Body "{}" -TimeoutSec 30
}

[pscustomobject]@{
    wallet = $WalletAddress
    chainId = $chainId
    targetBalanceEth = $TargetBalanceEth
    balanceWei = $balanceAfter.ToString()
    amountTransferredWei = $amountTransferred.ToString()
    transactionHash = $transactionHash
    gasReady = if ($platformState) { [bool]$platformState.gasReady } else { $null }
    chainStatus = if ($platformState) { [string]$platformState.chainStatus } else { $null }
    matchable = if ($platformState) { [bool]$platformState.matchable } else { $null }
} | Format-List
