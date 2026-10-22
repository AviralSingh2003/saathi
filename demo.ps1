# One-command offline demo for Windows (no AWS credentials needed).
# Usage:  powershell -ExecutionPolicy Bypass -File .\demo.ps1 [--keep-data] [--no-browser]
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Host "Creating virtual environment..."
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) { & py -3 -m venv .venv } else { & python -m venv .venv }
}

& $venvPy -c "import saathi, mcp, strands" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing dependencies..."
    & $venvPy -m pip install --quiet --upgrade pip
    & $venvPy -m pip install --quiet -e ".[dev]"
}

& $venvPy scripts\demo.py @args
