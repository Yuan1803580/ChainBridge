[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
function Require-Environment([string]$Name) {
    $value = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrWhiteSpace($value)) { throw "$Name is required" }
    return $value
}

$apiUrl = (Require-Environment "API_URL").TrimEnd('/')
$token = Require-Environment "PLATFORM_ADMIN_API_TOKEN"
$sourceRouter = Require-Environment "SOURCE_CCIP_ROUTER"
$destinationRouter = Require-Environment "DESTINATION_CCIP_ROUTER"
$sourceToken = Require-Environment "SOURCE_SETTLEMENT_TOKEN"
$destinationToken = Require-Environment "DESTINATION_SETTLEMENT_TOKEN"
$decimals = [int](Require-Environment "CCIP_BNM_DECIMALS")
$payload = @{
    routeKey = "SEPOLIA_TO_ARBITRUM_SEPOLIA_CCIP_BNM_V1"
    sourceChainId = 11155111
    destinationChainId = 421614
    rpcUrl = "secretref:DESTINATION_RPC_URL"
    transport = "CHAINLINK_CCIP"
    sourceChainSelector = "16015286601757825753"
    destinationChainSelector = "3478487238524512106"
    sourceRouterAddress = $sourceRouter
    destinationRouterAddress = $destinationRouter
    sourceTokenAddress = $sourceToken
    destinationTokenAddress = $destinationToken
    tokenAddress = $destinationToken
    tokenDecimals = $decimals
    settlementMode = "ccip_direct"
    settlementAsset = "CCIP-BNM"
    payoutAsset = "CCIP-BNM"
    payoutDecimals = $decimals
    payoutRate = "1"
    payoutSlippageBps = 0
    confirmationBlocks = 5
} | ConvertTo-Json

$headers = @{
    Authorization = "Bearer $token"
    "Idempotency-Key" = "managed-ccip-bnm-route-v1"
}
$result = Invoke-RestMethod -UseBasicParsing -Method Post -Uri "$apiUrl/api/v1/chain-routes" `
    -ContentType "application/json" -Headers $headers -Body $payload
Write-Host "Created route $($result.routeKey) version $($result.routeVersion)."
$deploymentHeaders = @{
    Authorization = "Bearer $token"
}
$queued = Invoke-RestMethod -UseBasicParsing -Method Post -Uri "$apiUrl/api/v1/chain-routes/$($result.id)/deploy" `
    -Headers $deploymentHeaders
Write-Host "Queued route deployment $($queued.deployment.id). Wait for status READY before updating the Sidecar allowedContracts list."
