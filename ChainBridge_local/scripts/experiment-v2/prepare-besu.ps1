[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$runtime = Join-Path $projectRoot ".runtime\experiment-v2\besu"
$signer = "f39fd6e51aad88f6f4ce6ab8827279cfffb92266"
$extraData = "0x" + ("0" * 64) + $signer + ("0" * 130)
$nodePrivateKey = "ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
$fundedAddresses = @(
    "f39fd6e51aad88f6f4ce6ab8827279cfffb92266",
    "70997970c51812dc3a010c7d01b50e0d17dc79c8",
    "3c44cdddb6a900fa2b585dd299e03d12fa4293bc",
    "90f79bf6eb2c4f870365e785982e1f101e93b906",
    "15d34aaf54267db7d7c367839aaf71a00a2c6a65",
    "9965507d1a55bcc2695c58ba16fb37d819b0a4dc"
)

foreach ($chain in @(
    @{ Name = "a"; ChainId = 31337 },
    @{ Name = "b"; ChainId = 31338 },
    @{ Name = "c"; ChainId = 31339 },
    @{ Name = "d"; ChainId = 31340 }
)) {
    $directory = Join-Path $runtime "chain-$($chain.Name)"
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
    $alloc = [ordered]@{}
    foreach ($address in $fundedAddresses) {
        $alloc[$address] = [ordered]@{ balance = "0x3635c9adc5dea00000" }
    }
    $genesis = [ordered]@{
        config = [ordered]@{
            chainId = $chain.ChainId
            berlinBlock = 0
            londonBlock = 0
            zeroBaseFee = $true
            clique = [ordered]@{
                blockperiodseconds = 2
                epochlength = 30000
            }
        }
        nonce = "0x0"
        timestamp = "0x0"
        extraData = $extraData
        gasLimit = "0x1fffffffffffff"
        difficulty = "0x1"
        mixHash = "0x0000000000000000000000000000000000000000000000000000000000000000"
        coinbase = "0x0000000000000000000000000000000000000000"
        alloc = $alloc
    }
    $genesis | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $directory "genesis.json") -Encoding utf8
    Set-Content -LiteralPath (Join-Path $directory "node-private-key.txt") -Value $nodePrivateKey -Encoding ascii
}

Write-Host "Besu genesis files prepared under $runtime"
