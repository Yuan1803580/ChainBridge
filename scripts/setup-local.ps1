$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VenvPath = Join-Path $RepoRoot ".venv"
$PythonPath = Join-Path $VenvPath "Scripts\python.exe"

if (Test-Path -LiteralPath $PythonPath) {
    $venvUsable = $false
    try {
        & $PythonPath -c "import sys; print(sys.version)" *> $null
        $venvUsable = $LASTEXITCODE -eq 0
    }
    catch { $venvUsable = $false }
    if (-not $venvUsable) {
        $backup = "$VenvPath.invalid-$([DateTime]::UtcNow.ToString('yyyyMMddHHmmss'))"
        Move-Item -LiteralPath $VenvPath -Destination $backup
        Write-Warning "The existing Python environment was unusable and was moved to $backup"
    }
}

if (-not (Test-Path -LiteralPath $PythonPath)) {
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        & $launcher.Source -3 -m venv $VenvPath
    } else {
        $windowsPython = Get-Command python.exe -ErrorAction SilentlyContinue
        if (-not $windowsPython -or $windowsPython.Source -match '(?i)msys|mingw') {
            throw "A native Windows Python 3 installation is required. Install Python from python.org and enable the py launcher."
        }
        & $windowsPython.Source -m venv $VenvPath
    }
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $PythonPath)) {
        throw "Failed to create the local Python environment."
    }
}

& $PythonPath -m pip install --upgrade pip
& $PythonPath -m pip install -r (Join-Path $RepoRoot "apps\api\requirements.txt")
& $PythonPath -m pip install -r (Join-Path $RepoRoot "services\agent-runner\requirements.txt")
& $PythonPath -m pip install -r (Join-Path $RepoRoot "services\agent-signer\requirements.txt")
& $PythonPath -m pip install -e (Join-Path $RepoRoot "packages\sdk\python")

$Pnpm = Get-Command pnpm.cmd -ErrorAction SilentlyContinue
if (-not $Pnpm) {
    throw "pnpm.cmd was not found. Install pnpm or enable it with Corepack."
}

Push-Location $RepoRoot
try {
    & $Pnpm.Source install
    if ($LASTEXITCODE -ne 0) { throw "pnpm dependency installation failed" }
    & $Pnpm.Source exec hardhat compile
    if ($LASTEXITCODE -ne 0) { throw "Smart contract compilation failed" }
}
finally {
    Pop-Location
}

Write-Host "Local dependencies are ready. Run pnpm local:v1:start next."
