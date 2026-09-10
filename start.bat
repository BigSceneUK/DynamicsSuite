@echo off
setlocal
cd /d "%~dp0"

echo ===================================================
echo Starting Dynamics Suite
echo ===================================================

set "PATH=%USERPROFILE%\.cargo\bin;%USERPROFILE%\.local\bin;%LOCALAPPDATA%\uv;%PATH%"

REM 1. Check if uv is installed in system or user space
where uv >nul 2>nul
if %errorlevel% neq 0 (
    echo [uv] 'uv' not found. Installing uv in user space...
    powershell -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    set "PATH=%USERPROFILE%\.cargo\bin;%USERPROFILE%\.local\bin;%LOCALAPPDATA%\uv;%PATH%"
)

where uv >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Could not find or install 'uv'.
    echo Please install uv manually from https://astral.sh/uv and run start.bat again.
    pause
    exit /b 1
)

REM 2. Ensure virtual environment exists (.venv seeded with pip)
if not exist ".venv" (
    echo [venv] Creating virtual environment with uv in .venv...
    uv venv --seed .venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment with uv.
        pause
        exit /b %errorlevel%
    )
)

REM 3. Install core dependencies
echo [deps] Checking and installing dependencies with uv...
uv pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b %errorlevel%
)

REM 4. Install plugin dependencies if install_plugins.py exists
if exist "install_plugins.py" (
    if exist ".venv\Scripts\python.exe" (
        .venv\Scripts\python.exe install_plugins.py
    )
)

REM 5. Launch Streamlit app
echo [launch] Launching Dynamics Suite...
if exist ".venv\Scripts\streamlit.exe" (
    .venv\Scripts\streamlit.exe run app.py
) else if exist ".venv\Scripts\python.exe" (
    .venv\Scripts\python.exe -m streamlit run app.py
) else (
    echo [ERROR] Streamlit executable not found in .venv\Scripts.
    pause
    exit /b 1
)

pause

