@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ===================================================
echo 🔷 Starting Dynamics Suite
echo ===================================================

:: 1. Check if uv is installed in system or user space
where uv >nul 2>nul
if %errorlevel% neq 0 (
    if exist "%USERPROFILE%\.local\bin\uv.exe" (
        set "PATH=%USERPROFILE%\.local\bin;%PATH%"
    ) else if exist "%USERPROFILE%\.cargo\bin\uv.exe" (
        set "PATH=%USERPROFILE%\.cargo\bin;%PATH%"
    ) else (
        echo 📦 'uv' not found. Installing uv in user space (no admin required)...
        powershell -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex"
        set "PATH=%USERPROFILE%\.local\bin;%PATH%"
    )
)

:: 2. Ensure virtual environment exists (.venv seeded with pip)
if not exist ".venv" (
    echo 🔨 Creating virtual environment with uv (.venv)...
    uv venv --seed .venv
)

:: 3. Install core dependencies
echo ⚡ Checking and installing dependencies with uv...
uv pip install -r requirements.txt

:: 4. Install plugin dependencies if install_plugins.py exists
if exist "install_plugins.py" (
    .venv\Scripts\python.exe install_plugins.py
)

:: 5. Launch Streamlit app
echo 🚀 Launching Dynamics Suite...
.venv\Scripts\streamlit.exe run app.py

pause
