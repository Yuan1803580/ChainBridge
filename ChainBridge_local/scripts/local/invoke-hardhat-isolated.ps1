[CmdletBinding()]
param(
    [ValidateSet("compile", "test")]
    [string]$Command = "test"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$stageRoot = Join-Path $projectRoot ".runtime\hardhat-stage"
$stageContracts = Join-Path $stageRoot "contracts"
$stageTests = Join-Path $stageRoot "test"
$stageModules = Join-Path $stageRoot "node_modules"

New-Item -ItemType Directory -Force -Path $stageContracts, $stageTests | Out-Null
Copy-Item -Path (Join-Path $projectRoot "contracts\*") -Destination $stageContracts -Recurse -Force
Copy-Item -Path (Join-Path $projectRoot "test\*") -Destination $stageTests -Recurse -Force
Copy-Item -Path (Join-Path $projectRoot "hardhat.config.ts"), (Join-Path $projectRoot "package.json") -Destination $stageRoot -Force
if (-not (Test-Path -LiteralPath $stageModules)) {
    New-Item -ItemType Junction -Path $stageModules -Target (Join-Path $projectRoot "node_modules") | Out-Null
}

Push-Location $stageRoot
try {
    $hardhat = Join-Path $stageModules ".bin\hardhat.cmd"
    if ($Command -eq "compile") {
        & $hardhat compile
    }
    else {
        & $hardhat test nodejs
    }
    if ($LASTEXITCODE -ne 0) { throw "Hardhat $Command failed" }
}
finally { Pop-Location }

$compiledArtifacts = Join-Path $stageRoot "hardhat-artifacts\contracts\src"
if (Test-Path -LiteralPath $compiledArtifacts) {
    $projectArtifacts = Join-Path $projectRoot "hardhat-artifacts\contracts\src"
    New-Item -ItemType Directory -Force -Path $projectArtifacts | Out-Null
    Copy-Item -Path (Join-Path $compiledArtifacts "*") -Destination $projectArtifacts -Recurse -Force
}
