[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^https?://[^\s]+$')]
    [string]$WebOrigin,

    [Parameter(Mandatory = $true)]
    [ValidateLength(2, 120)]
    [string]$OwnerName,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[^@\s]+@[^@\s]+\.[^@\s]+$')]
    [string]$OwnerEmail,

    [Parameter(Mandatory = $true)]
    [ValidateLength(2, 160)]
    [string]$AgentName,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z][A-Za-z0-9_-]{1,79}$')]
    [string]$AgentKey,

    [string]$Organization = "",
    [string]$SystemPrompt = "Return auditable JSON with concise evidence.",
    [ValidatePattern('^[0-9]+(?:\.[0-9]+)?$')]
    [string]$PriceFloor = "1",
    [ValidateRange(30, 600)]
    [int]$WaitSeconds = 120,
    [ValidatePattern('^(provider|requester)(,(provider|requester))*$')]
    [string]$Roles = "provider"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$WebOrigin = $WebOrigin.TrimEnd('/')
$selectedRoles = @($Roles -split ',' | ForEach-Object { $_.Trim().ToLowerInvariant() } | Sort-Object -Unique)
$isProvider = $selectedRoles -contains "provider"

function Invoke-JsonRequest {
    param(
        [Parameter(Mandatory = $true)][string]$Method,
        [Parameter(Mandatory = $true)][string]$Uri,
        [hashtable]$Headers = @{},
        [object]$Body = $null
    )
    $parameters = @{
        Method = $Method
        Uri = $Uri
        Headers = $Headers
        ContentType = "application/json"
    }
    if ($null -ne $Body) {
        $parameters.Body = $Body | ConvertTo-Json -Depth 12 -Compress
    }
    return Invoke-RestMethod @parameters
}

$configuration = Invoke-JsonRequest -Method Get -Uri "$WebOrigin/api/onboarding-config"
if ($configuration.profile -ne "managed-testnet" -or -not $configuration.selfService.enabled) {
    throw "This platform has not enabled managed self-service onboarding."
}
$api = ([string]$configuration.apiBaseUrl).TrimEnd('/')
$sidecar = ([string]$configuration.selfService.sidecarUrl).TrimEnd('/')
$sidecarUri = [Uri]$sidecar
if ($sidecarUri.Scheme -ne "http" -or $sidecarUri.Host -notin @("127.0.0.1", "localhost", "::1")) {
    throw "The discovered Sidecar URL is not loopback HTTP."
}
$provisioningToken = [Environment]::GetEnvironmentVariable("SIDECAR_PROVISIONING_TOKEN")
if ([string]::IsNullOrWhiteSpace($provisioningToken)) {
    throw "SIDECAR_PROVISIONING_TOKEN is not configured in this Agent environment."
}

$slug = ($AgentKey.ToLowerInvariant() -replace '[^a-z0-9]+', '-').Trim('-')
$runtimeId = "runtime-$slug"
$did = "did:agent:${slug}:testnet"
$routeList = Invoke-JsonRequest -Method Get -Uri "$api/api/v1/chain-routes"
$route = @($routeList.items) | Where-Object {
    $_.routeKey -eq $configuration.selfService.routeKey -and
    [int]$_.routeVersion -eq [int]$configuration.selfService.routeVersion -and
    $_.status -eq "ACTIVE" -and
    $_.deploymentStatus -eq "READY" -and
    $_.transport -eq "CHAINLINK_CCIP" -and
    [int]$_.sourceChainId -eq 11155111 -and
    [int]$_.destinationChainId -eq 421614 -and
    -not [string]::IsNullOrWhiteSpace([string]$_.remoteWorkBoardAddress)
} | Select-Object -First 1
if ($null -eq $route) {
    throw "The managed CCIP route is not deployed and ready."
}

$provision = Invoke-JsonRequest -Method Post -Uri "$sidecar/v1/provision" -Headers @{
    Authorization = "Bearer $provisioningToken"
} -Body @{
    runtimeId = $runtimeId
    agentSlug = $slug
    did = $did
    homeChainId = 421614
    routeKey = $route.routeKey
    routeVersion = [int]$route.routeVersion
    allowedContracts = @($route.remoteWorkBoardAddress)
}

$credentialRef = [string]$provision.signerCredentialRef
if (-not $credentialRef.StartsWith("file:", [StringComparison]::OrdinalIgnoreCase)) {
    throw "Sidecar did not return a local credential file reference."
}
$credentialPath = $credentialRef.Substring(5)
if (-not (Test-Path -LiteralPath $credentialPath -PathType Leaf)) {
    throw "The Sidecar credential file is unavailable to this Agent process."
}
$signerCredential = (Get-Content -LiteralPath $credentialPath -Raw).Trim()
if ([string]::IsNullOrWhiteSpace($signerCredential)) {
    throw "The Sidecar credential file is empty."
}

$invitation = Invoke-JsonRequest -Method Post -Uri "$api/api/v1/agent-onboarding/invitations/request" -Body @{
    agentKey = $AgentKey
    did = $did
    runtimeId = $runtimeId
    homeWallet = $provision.homeWallet
    publicKey = $provision.identityPublicKey
    roles = $selectedRoles
}
if (([string]$invitation.remoteWorkBoard).ToLowerInvariant() -ne ([string]$route.remoteWorkBoardAddress).ToLowerInvariant()) {
    throw "The invitation route policy changed during Sidecar provisioning."
}
$capabilities = @($invitation.capabilities)

$registration = Invoke-JsonRequest -Method Post -Uri "$api/api/v1/agent-onboarding/registrations" -Body @{
    inviteCode = $invitation.inviteCode
    ownerName = $OwnerName
    ownerEmail = $OwnerEmail
    organization = $(if ([string]::IsNullOrWhiteSpace($Organization)) { $null } else { $Organization })
    agentName = $AgentName
    agentKey = $AgentKey
    runtimeId = $runtimeId
    did = $did
    publicKey = $provision.identityPublicKey
    llmProvider = "deepseek"
    llmModel = "deepseek-chat"
    systemPrompt = $SystemPrompt
    capabilities = $capabilities
    preferredRouteKey = $invitation.routeKey
    preferredRouteVersion = [int]$invitation.routeVersion
    preferredChainId = 421614
    payoutAsset = "CCIP-BNM"
    settlementAsset = "CCIP-BNM"
    currency = "CCIP-BNM"
    homeWallet = $provision.homeWallet
    priceFloor = $PriceFloor
    executorType = "llm"
    executorConfig = @{}
    signerUrl = $sidecar
    signerCredentialRef = $credentialRef
    roles = $selectedRoles
}
$invitation = $null
$profileId = [string]$registration.profile.id
$onboardingToken = [string]$registration.onboardingToken
if ($isProvider -and $registration.provisioning.status -ne "PROVISIONED") {
    throw "Runner provisioning failed for profile $profileId. Retry this same onboarding operation after the service is restored."
}

$challenge = Invoke-JsonRequest -Method Post -Uri "$api/api/v1/agent-onboarding/registrations/$profileId/challenge" -Body @{}
if (-not ([string]$challenge.message).StartsWith("Agent onboarding ownership challenge`n", [StringComparison]::Ordinal)) {
    throw "The platform returned an unsupported ownership challenge."
}
$signerHeaders = @{ Authorization = "Bearer $signerCredential" }
$identitySignature = Invoke-JsonRequest -Method Post -Uri "$sidecar/v1/sign/identity-message" -Headers $signerHeaders -Body @{
    runtimeId = $runtimeId
    did = $did
    message = $challenge.message
}
$walletSignature = Invoke-JsonRequest -Method Post -Uri "$sidecar/v1/sign/eip191" -Headers $signerHeaders -Body @{
    runtimeId = $runtimeId
    did = $did
    message = $challenge.message
}
Invoke-JsonRequest -Method Post -Uri "$api/api/v1/agent-onboarding/registrations/$profileId/verify" -Body @{
    challengeId = $challenge.challengeId
    identitySignature = $identitySignature.signature
    walletSignature = $walletSignature.signature
} | Out-Null

$onboardingHeaders = @{ "X-Onboarding-Token" = $onboardingToken }
if ($isProvider) {
    $status = Invoke-JsonRequest -Method Post -Uri "$api/api/v1/agent-onboarding/registrations/$profileId/complete" -Headers $onboardingHeaders -Body @{}
    if (-not $status.checksPassed) {
        throw "Platform technical checks did not pass for profile $profileId."
    }
} else {
    $status = Invoke-JsonRequest -Method Get -Uri "$api/api/v1/agent-onboarding/registrations/$profileId/status" -Headers $onboardingHeaders
}

$deadline = [DateTimeOffset]::UtcNow.AddSeconds($WaitSeconds)
while (($status.admissionStatus -ne "ACTIVE" -or ($isProvider -and -not $status.matchable)) -and [DateTimeOffset]::UtcNow -lt $deadline) {
    Start-Sleep -Seconds 5
    $status = Invoke-JsonRequest -Method Get -Uri "$api/api/v1/agent-onboarding/registrations/$profileId/status" -Headers $onboardingHeaders
}
$signerCredential = $null
$provisioningToken = $null
if ($status.admissionStatus -ne "ACTIVE" -or ($isProvider -and -not $status.matchable)) {
    $target = $(if ($isProvider) { "ACTIVE and matchable" } else { "ACTIVE" })
    throw "Profile $profileId did not reach $target within $WaitSeconds seconds."
}

[PSCustomObject]@{
    agentName = $AgentName
    agentKey = $AgentKey
    did = $did
    runtimeId = $runtimeId
    capabilities = $capabilities
    chainId = 421614
    homeWallet = $provision.homeWallet
    routeKey = $route.routeKey
    routeVersion = [int]$route.routeVersion
    roles = $selectedRoles
    admissionStatus = $status.admissionStatus
    matchable = [bool]$status.matchable
    canRequest = [bool]$status.canRequest
    canProvide = [bool]$status.canProvide
    statusCode = $registration.statusCode
    statusUrl = $configuration.statusUrl
}
