# PowerShell Starter for Dynamics Suite
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "===================================================" -ForegroundColor Cyan
Write-Host "Starting Dynamics Suite" -ForegroundColor Cyan
Write-Host "===================================================" -ForegroundColor Cyan

$env:UV_LINK_MODE = "copy"

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

if ($PSScriptRoot -like "*AppData\Local\Temp\Temp*") {
    Write-Error "You are running start.ps1 directly from inside the ZIP file! Please right-click DynamicsSuite-latest.zip -> Extract All."
    Read-Host "Press Enter to exit..."
    exit 1
}

$recreateVenv = $false
if (Test-Path ".venv\Scripts\python.exe") {
    & ".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[venv] Existing .venv uses Python < 3.10. Recreating with Python 3.11..." -ForegroundColor Yellow
        $recreateVenv = $true
    }
} else {
    $recreateVenv = $true
}

if ($recreateVenv) {
    Write-Host "[venv] Preparing virtual environment with Python 3.11 via uv..." -ForegroundColor Green
    Write-Host "[venv] (If Python 3.11 is not on this machine, uv will download a portable runtime automatically)" -ForegroundColor Gray
    if (Test-Path ".venv") {
        Remove-Item -Recurse -Force ".venv" -ErrorAction SilentlyContinue
    }
    uv venv --python 3.11 .venv
}

$doInstall = $recreateVenv -or (-not (Test-Path ".venv\Scripts\streamlit.exe")) -or ($args -contains "--update") -or ($args -contains "-u")

if ($doInstall) {
    Write-Host "[deps] Installing dependencies with uv..." -ForegroundColor Green
    uv pip install pip -r requirements.txt

    if (Test-Path "install_plugins.py") {
        if (Test-Path ".venv\Scripts\python.exe") {
            & ".venv\Scripts\python.exe" install_plugins.py
        }
    }
} else {
    Write-Host "[deps] Virtual environment ready. Launching immediately..." -ForegroundColor Green
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
