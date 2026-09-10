# PowerShell Starter for Dynamics Suite
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "===================================================" -ForegroundColor Cyan
Write-Host "Starting Dynamics Suite" -ForegroundColor Cyan
Write-Host "===================================================" -ForegroundColor Cyan

$uvPaths = @(
    "$env:USERPROFILE\.cargo\bin",
    "$env:USERPROFILE\.local\bin",
    "$env:LOCALAPPDATA\uv"
)
foreach ($p in $uvPaths) {
    if ((Test-Path $p) -and ($env:Path -notlike "*$p*")) {
        $env:Path = "$p;$env:Path"
    }
}

if (-not (Get-Command "uv" -ErrorAction SilentlyContinue)) {
    Write-Host "[uv] uv not found. Installing uv in user space..." -ForegroundColor Yellow
    irm https://astral.sh/uv/install.ps1 | iex
    foreach ($p in $uvPaths) {
        if ((Test-Path $p) -and ($env:Path -notlike "*$p*")) {
            $env:Path = "$p;$env:Path"
        }
    }
}

if (-not (Get-Command "uv" -ErrorAction SilentlyContinue)) {
    Write-Error "Could not find or install uv. Please install manually from https://astral.sh/uv"
    Read-Host "Press Enter to exit..."
    exit 1
}

if (-not (Test-Path ".venv")) {
    Write-Host "[venv] Creating virtual environment with uv in .venv..." -ForegroundColor Green
    uv venv --seed .venv
}

Write-Host "[deps] Checking and installing dependencies with uv..." -ForegroundColor Green
uv pip install -r requirements.txt

if (Test-Path "install_plugins.py") {
    if (Test-Path ".venv\Scripts\python.exe") {
        & ".venv\Scripts\python.exe" install_plugins.py
    }
}

Write-Host "[launch] Launching Dynamics Suite..." -ForegroundColor Cyan
if (Test-Path ".venv\Scripts\streamlit.exe") {
    & ".venv\Scripts\streamlit.exe" run app.py
} elseif (Test-Path ".venv\Scripts\python.exe") {
    & ".venv\Scripts\python.exe" -m streamlit run app.py
} else {
    Write-Error "Streamlit executable not found in .venv\Scripts."
    Read-Host "Press Enter to exit..."
    exit 1
}
